"""Direct API protocols. No tools, workflow state, automatic retries or fallback."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TransportError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward credentials on a redirect, including 307/308.
        return None


def _request(config, path, headers, body):
    request = urllib.request.Request(
        config["endpoint"] + path,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise TransportError("Direct API response exceeded the response-size bound")
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise TransportError(f"Direct API returned HTTP {code}; no automatic retry or route fallback", code) from None
    except (OSError, urllib.error.URLError):
        raise TransportError("Direct API request did not finish; reconcile the attempt before retrying") from None
    except ValueError:
        raise TransportError("Direct API returned invalid JSON") from None


def compatible(config, key, messages, tools, model, effort):
    provider = config.get("api_provider", "compatible")
    limit = "max_tokens" if provider == "zai" else "max_completion_tokens"
    body = {"model": model, "messages": messages, "tools": tools, limit: 8192}
    if effort:
        body["reasoning_effort"] = effort
    headers = {"api-key": key} if provider == "mimo" else {"Authorization": "Bearer " + key}
    data = _request(config, "/chat/completions", headers, body)
    try:
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("not an assistant response")
        # Keep the original message, including reasoning_content and tool IDs,
        # for MiMo/Z.ai's required replay on the next tool turn.
        return message, choice.get("finish_reason"), data.get("usage", {})
    except (ValueError, KeyError, IndexError, TypeError):
        raise TransportError("Direct API returned an invalid response") from None


def anthropic_messages(messages):
    system, result = [], []
    for message in messages:
        if message["role"] == "system":
            system.append({"type": "text", "text": message["content"]})
            continue
        if message["role"] == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}]
        else:
            role = message["role"]
            if "_anthropic_content" in message:
                # Replay signed thinking/redacted thinking verbatim, in order.
                blocks = message["_anthropic_content"]
            else:
                blocks = [{"type": "text", "text": message["content"]}] if message.get("content") else []
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": list(blocks)})
    return system, result


def anthropic(config, key, messages, tools, model, effort):
    if effort:
        raise TransportError("Claude API reasoning overrides are not supported yet; use the model default")
    system, conversation = anthropic_messages(messages)
    body = {
        "model": model,
        "max_tokens": 8192,
        "system": system,
        "messages": conversation,
        "tools": [
            {
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
                "input_schema": tool["function"]["parameters"],
            }
            for tool in tools
        ],
    }
    data = _request(config, "/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
    try:
        if data["role"] != "assistant" or data["type"] != "message":
            raise ValueError("not an assistant message")
        blocks = data["content"]
        if not isinstance(blocks, list) or any(
            block["type"] not in ("text", "tool_use", "thinking", "redacted_thinking") for block in blocks
        ):
            raise ValueError("unsupported message content")
        text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
        calls = [
            {
                "id": block["id"],
                "type": "function",
                "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
            }
            for block in blocks
            if block["type"] == "tool_use"
        ]
        stop = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(
            data["stop_reason"], "refused"
        )
        raw_usage = data.get("usage", {})
        usage = {"completion_tokens": raw_usage.get("output_tokens")}
        # Anthropic input_tokens excludes cache reads and cache creation tokens.
        # Do not undercount total input when caching is used.
        input_fields = [
            raw_usage.get("input_tokens"),
            raw_usage.get("cache_read_input_tokens", 0),
            raw_usage.get("cache_creation_input_tokens", 0),
        ]
        if all(type(count) is int and count >= 0 for count in input_fields):
            usage["prompt_tokens"] = sum(input_fields)
        if "cache_read_input_tokens" in raw_usage:
            usage["prompt_tokens_details"] = {"cached_tokens": raw_usage["cache_read_input_tokens"]}
        return (
            {
                "role": "assistant",
                "content": text,
                "_anthropic_content": blocks,
                **({"tool_calls": calls} if calls else {}),
            },
            stop,
            usage,
        )
    except (KeyError, TypeError, ValueError):
        raise TransportError("Claude API returned an invalid or unsupported response") from None
