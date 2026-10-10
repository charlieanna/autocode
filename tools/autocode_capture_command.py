"""Execute fresh proof, retaining exact output before applying a display filter."""

import argparse
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

try:
    from . import autocode_output_filter as filters
    from . import autocode_output_store as store
    from .autocode_output import representation
    from .autocode_util import atomic_json
except ImportError:
    import autocode_output_filter as filters
    import autocode_output_store as store
    from autocode_output import representation
    from autocode_util import atomic_json


def cli(argv=None, *, formatter=filters.compact_output):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-compress", action="store_true", help="Always display the complete original")
    parser.add_argument(
        "--mode", choices=("raw", "conservative"), default=os.environ.get("AUTOCODE_OUTPUT_MODE", "conservative")
    )
    parser.add_argument(
        "--known-output-sha256",
        help="Omit an identical display only after verifying exact retrieval; still execute fresh proof",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required")
    capture_context = None
    if os.environ.get("AUTOCODE_CAPTURE_CONTEXT"):
        try:
            capture_context = json.loads(os.environ["AUTOCODE_CAPTURE_CONTEXT"])
        except ValueError:
            parser.error("invalid runner capture context")
        if not isinstance(capture_context, dict) or any(
            not isinstance(capture_context.get(key), str) or not capture_context[key]
            for key in ("attempt", "nonce", "source_revision")
        ):
            parser.error("invalid runner capture context")
    path = args.output.resolve()
    if not path.is_relative_to(Path.cwd().resolve() / ".autocode"):
        parser.error("evidence output must be under this project's .autocode directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(".log")
    if raw == path:
        parser.error("receipt filename must differ from the retained .log filename")
    if path.exists() or raw.exists():
        parser.error("use a unique evidence filename; existing evidence is immutable")
    execution_cwd, verification_context = None, None
    if os.environ.get("AUTOCODE_VERIFICATION_COPY"):
        try:
            try:
                from . import autocode_verification_copy as copies
            except ImportError:
                import autocode_verification_copy as copies
            execution_cwd, verification_context = copies.execution(
                os.environ["AUTOCODE_VERIFICATION_COPY"],
                os.environ.get("AUTOCODE_VERIFICATION_COPY_SHA256", ""),
                Path.cwd(),
            )
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
    started = time.monotonic()
    with raw.open("xb") as handle:
        result = subprocess.run(command, cwd=execution_cwd, stdout=handle, stderr=subprocess.STDOUT)
    data = raw.read_bytes()
    original = representation(data)
    compact = {"format": "text", **original, "filter": "raw"}
    mode = "raw" if args.no_compress else args.mode
    retained = None
    root = None
    try:
        root = store.store_root(os.environ.get("AUTOCODE_OUTPUT_STORE"))
        retained = store.retain(root, data)
        if original["encoding"] == "utf-8":
            compact = formatter(
                original["content"], enabled=mode != "raw", command=command, exit_code=result.returncode
            )
        if mode != "raw" and args.known_output_sha256 == retained["sha256"]:
            compact = {
                "format": "text",
                "content": "",
                "filter": "verified-identical",
                "omitted_sections": [{"start_byte": 0, "end_byte": len(data)}],
                "notice": "Identical display omitted; command executed again and this receipt is fresh.",
            }
    except Exception as error:
        compact = {
            "format": "text",
            **original,
            "compression_error": type(error).__name__,
            "fallback": "complete_original",
        }
    receipt = {
        "command": command,
        "command_text": shlex.join(command),
        "exit_code": result.returncode,
        "duration_seconds": time.monotonic() - started,
        "full_output": str(raw),
        "full_output_sha256": store.digest(data),
        "summary": compact,
    }
    if verification_context:
        receipt["verification_copy"] = verification_context
    if retained:
        receipt["exact_output"] = retained
    if capture_context:
        receipt["capture_context"] = capture_context
    atomic_json(path, receipt)
    display = json.dumps(receipt) + "\n"
    print(display, end="")
    if root:
        store.record(
            root,
            operation="capture",
            raw_bytes=len(data),
            displayed_bytes=len(display.encode()),
            mode=mode,
            omitted=len(compact.get("omitted_sections", [])),
        )
    return result.returncode
