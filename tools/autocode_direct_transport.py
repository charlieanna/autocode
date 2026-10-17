"""Direct inference transports. No tools, workflow state, automatic retries or fallback."""

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
        # Never forward a credential to a different destination, even on 307/308.
        return None


def compatible(config, key, messages, tools, model, effort):
    body = {"model": model, "messages": messages, "tools": tools, "max_completion_tokens": 8192}
    if effort:
        body["reasoning_effort"] = effort
    request = urllib.request.Request(
        config["endpoint"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise TransportError("Direct API response exceeded the response-size bound")
        data = json.loads(raw)
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("not an assistant response")
        return message, choice.get("finish_reason"), data.get("usage", {})
    except urllib.error.HTTPError as error:
        # Raw errors can echo keys or request bodies. Keep only the HTTP code.
        code = error.code
        error.close()
        raise TransportError(f"Direct API returned HTTP {code}; no automatic retry or route fallback", code) from None
    except (OSError, urllib.error.URLError):
        raise TransportError("Direct API request did not finish; reconcile the attempt before retrying") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise TransportError("Direct API returned an invalid response") from None


def aws_session(config):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise TransportError(
            "Bedrock requires AutoCode's bedrock extra: pip install 'autocode-supervisor[bedrock]'"
        ) from None
    try:
        session = boto3.Session(profile_name=config.get("profile") or None, region_name=config["region"])
        return session.client(
            "bedrock-runtime", config=Config(connect_timeout=10, read_timeout=60, retries={"total_max_attempts": 1})
        )
    except Exception:
        raise TransportError(
            "AWS session could not be created. Check the configured region/profile and corporate sign-in"
        ) from None


def bedrock_messages(messages):
    system, result = [], []
    for message in messages:
        if message["role"] == "system":
            system.append({"text": message["content"]})
            continue
        if message["role"] == "tool":
            role, blocks = (
                "user",
                [{"toolResult": {"toolUseId": message["tool_call_id"], "content": [{"text": message["content"]}]}}],
            )
        else:
            role = message["role"]
            blocks = [{"text": message["content"]}] if message.get("content") else []
            for call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": call["id"],
                            "name": call["function"]["name"],
                            "input": json.loads(call["function"]["arguments"]),
                        }
                    }
                )
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": blocks})
    return system, result


def bedrock(config, messages, tools, model, effort, *, client=None):
    if effort:
        raise TransportError(
            "Bedrock reasoning-effort overrides are model-specific and not supported by this transport; use its model default"
        )
    system, conversation = bedrock_messages(messages)
    definitions = [
        {
            "toolSpec": {
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
                "inputSchema": {"json": tool["function"]["parameters"]},
            }
        }
        for tool in tools
    ]
    try:
        response = (client or aws_session(config)).converse(
            modelId=model,
            messages=conversation,
            system=system,
            inferenceConfig={"maxTokens": 8192},
            toolConfig={"tools": definitions},
        )
    except TransportError:
        raise
    except Exception as error:
        code = getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        raise TransportError(
            "Bedrock request failed; check model access, region and AWS sign-in. No automatic retry or fallback", code
        ) from None
    try:
        blocks = response["output"]["message"]["content"]
        text = "\n".join(block["text"] for block in blocks if "text" in block)
        calls = [
            {
                "id": block["toolUse"]["toolUseId"],
                "type": "function",
                "function": {"name": block["toolUse"]["name"], "arguments": json.dumps(block["toolUse"]["input"])},
            }
            for block in blocks
            if "toolUse" in block
        ]
        # Model-specific reasoning/signature blocks must be replayed verbatim.
        # This initial text/tool transport refuses them rather than discarding them.
        if any(set(block) - {"text", "toolUse"} for block in blocks):
            raise TransportError("Bedrock returned content unsupported by the text/tool transport")
        stop = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(
            response["stopReason"], "refused"
        )
        usage = response.get("usage", {})
        return (
            {"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})},
            stop,
            {"prompt_tokens": usage.get("inputTokens"), "completion_tokens": usage.get("outputTokens")},
        )
    except (KeyError, TypeError, ValueError):
        raise TransportError("Bedrock returned an invalid response") from None
