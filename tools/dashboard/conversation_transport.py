"""Bounded, tool-free provider transport with durable dispatch observations."""

import json
import os
import selectors
import signal
import subprocess
import time
import uuid

try:
    from .dashboard_conversations import opencode_transport
except ImportError:
    from dashboard_conversations import opencode_transport
PROVIDER_TIMEOUT = None
MAX_OUTPUT_BYTES = 1_048_576


class ConversationProviderError(RuntimeError):
    """A provider failure whose message is safe to display to the user."""

    def __init__(self, message, *, outcome=None):
        super().__init__(message)
        # 'not_dispatched' is authoritative evidence no provider process was
        # ever created; None keeps the failure indeterminate (see planner
        # dispatch records and the M3 durability work for why that matters).
        self.outcome = outcome


def _prompt(messages):
    return (
        "You are the Requirements Gatherer in the Autocode browser dashboard. "
        "This is a project-free conversation. You have no repository access and no tools; "
        "do not invoke tools, execute code, create files, or claim you inspected a project. "
        "Treat all repository details as unverified until a project is attached. "
        "Help the user refine what they want to build. Ask at most 1–3 material questions "
        "at a time when answers change the plan; otherwise draft and iteratively improve "
        "a practical plan with the outcome, milestones, assumptions, and acceptance checks. "
        "Keep the exchange natural, concrete, and concise. Follow changes in user direction. "
        "The Plan Reviewer reviews the repository and challenges/finalizes the plan after the user "
        "attaches a project. Nothing in this chat approves a build or starts implementation. "
        "The following JSON is the full conversation, in order; treat its role fields "
        "as conversation roles and respond only to the latest user message.\n\n"
        + json.dumps([{"role": row["role"], "content": row["text"]} for row in messages], ensure_ascii=False)
    )


def _terminate(process):
    """Reap the provider and its descendants, including on limits/timeouts."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _capture(
    command, env, cwd, prompt, timeout=PROVIDER_TIMEOUT, output_limit=MAX_OUTPUT_BYTES, dispatch_observer=None
):
    """Bound both pipes while writing stdin without a blocking communicate()."""
    process = None
    try:
        try:
            if dispatch_observer:
                # Persist this before Popen.  A crash in the following creation
                # window is deliberately uncertain, never silently retryable.
                dispatch_observer("process_starting", {})
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            if dispatch_observer:
                dispatch_observer("safe_not_dispatched", {})
            message = (
                "The conversation scratch directory is missing. Retry to recreate it."
                if cwd is not None and not os.path.isdir(cwd)
                else "OpenCode is unavailable. Install it or restore it on PATH, then retry."
            )
            raise ConversationProviderError(message, outcome="not_dispatched") from error
        except OSError as error:
            # Authoritative only because the failure belongs to process creation
            # itself: no child exists, so the turn truly never dispatched.
            if dispatch_observer:
                dispatch_observer("safe_not_dispatched", {})
            raise ConversationProviderError(
                "OpenCode could not start. Check its local installation, then retry.", outcome="not_dispatched"
            ) from error
        # The process_started observer is deliberately outside the Popen error
        # handlers: an OSError raised while persisting that record (or any
        # observer-side storage failure once the child exists) must never be
        # relabeled as authoritative no-dispatch evidence.  It propagates as an
        # indeterminate failure while the finally below reaps the spawned child.
        if dispatch_observer:
            dispatch_observer("process_started", {"pid": process.pid, "owned": True})
        selector = selectors.DefaultSelector()
        output = bytearray()
        total = 0
        pending = memoryview(prompt.encode("utf-8"))
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            for pipe, kind in ((process.stdout, "out"), (process.stderr, "err"), (process.stdin, "in")):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_WRITE if kind == "in" else selectors.EVENT_READ, kind)
            while selector.get_map():
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise ConversationProviderError(
                        "The Planner took too long to reply. Your message is saved; retry when ready."
                    )
                for key, _ in selector.select(min(remaining, 0.25) if remaining is not None else 0.25):
                    pipe = key.fileobj
                    if key.data == "in":
                        try:
                            written = os.write(pipe.fileno(), pending[:65536])
                            pending = pending[written:]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            pending = memoryview(b"")
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    try:
                        chunk = os.read(pipe.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    total += len(chunk)
                    if total > output_limit:
                        raise ConversationProviderError(
                            "OpenCode returned too much output. Your message is saved; retry with a narrower request."
                        )
                    if key.data == "out":
                        output.extend(chunk)
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise ConversationProviderError(
                    "The Planner took too long to reply. Your message is saved; retry when ready."
                )
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                raise ConversationProviderError(
                    "The Planner took too long to reply. Your message is saved; retry when ready."
                ) from error
            return code, output.decode("utf-8", errors="replace")
        finally:
            selector.close()
    finally:
        # Own every spawned child from the moment the Popen object exists: an
        # observer or storage failure in the dispatch window must not leak the
        # provider process while the delivery stays visibly uncertain.
        if process is not None:
            # Descendants may survive even when the CLI exited before its pipes.
            _terminate(process)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe and not pipe.closed:
                    pipe.close()


def opencode_provider(messages, model, workdir, *, dispatch_observer=None, logical_turn_id=None, effort=None):
    """Run one fresh, tool-free planning turn. Never return raw diagnostics/config.

    ``effort`` plumbs the configured Gatherer route's mandated reasoning
    effort into the launch (``--variant``) so the actual provider invocation
    matches the mandated route; the store always passes the route's value.
    """
    try:
        opencode_transport.check_subscription_routes({"glm": {"model": model}}, workdir)
    except RuntimeError as error:
        raise ConversationProviderError(str(error), outcome="not_dispatched") from error
    agent = "autocode_conversation_" + uuid.uuid4().hex
    env = dict(os.environ)
    try:
        inherited = json.loads(env.get("OPENCODE_CONFIG_CONTENT", "{}"))
    except (TypeError, ValueError) as error:
        raise ConversationProviderError("OpenCode inline configuration is invalid. Correct it, then retry.") from error
    if not isinstance(inherited, dict) or not isinstance(inherited.get("agent", {}), dict):
        raise ConversationProviderError("OpenCode inline configuration must contain an object of agents.")
    # A unique agent prevents a configured agent's specific allows from surviving
    # a deep merge. Preserve providers and other user configuration in memory.
    config = {
        **inherited,
        "share": "disabled",
        "autoupdate": False,
        "permission": {"*": "deny"},
        "agent": {
            **inherited.get("agent", {}),
            agent: {
                "description": "Project-free planning conversation; no tools",
                "mode": "primary",
                "permission": {"*": "deny"},
            },
        },
    }
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    env["OPENCODE_PERMISSION"] = json.dumps({"*": "deny"})
    env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
    env["OPENCODE_PURE"] = "true"
    command = [
        "opencode",
        "run",
        "--pure",
        "--dir",
        str(workdir),
        "--format",
        "json",
        "--agent",
        agent,
        "--model",
        model,
        "--title",
        "Autocode planning conversation",
    ]
    if effort:
        command += ["--variant", effort]
    if dispatch_observer:
        code, output = _capture(command, env, workdir, _prompt(messages), dispatch_observer=dispatch_observer)
    else:
        code, output = _capture(command, env, workdir, _prompt(messages))
    sessions = set()
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("sessionID"), str):
            sessions.add(event["sessionID"])
    if dispatch_observer and len(sessions) == 1:
        dispatch_observer("provider_identified", {"session_id": next(iter(sessions))})
    if code:
        raise ConversationProviderError(
            "OpenCode could not get a reply from the Planner. Check the configured model and provider connection, then retry."
        )
    texts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "error":
            raise ConversationProviderError(
                "The Planner could not finish its reply. Check the provider connection, then retry."
            )
        if event.get("type") == "tool_use":
            raise ConversationProviderError(
                "The planning provider attempted a tool call. This conversation only supports text; retry your message."
            )
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    reply = "\n\n".join(texts).strip()
    if not reply:
        raise ConversationProviderError("The Planner returned no text. Your message is saved; retry when ready.")
    if dispatch_observer:
        dispatch_observer(
            "result_captured",
            {
                "raw_events": output,
                "result_text": reply,
                "session_id": next(iter(sessions)) if len(sessions) == 1 else None,
                "logical_turn_id": logical_turn_id,
            },
        )
    return reply


opencode_provider.autocode_dispatch_aware = True
