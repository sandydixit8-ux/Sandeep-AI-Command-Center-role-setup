"""LLM client: Anthropic Messages API and OpenAI-compatible chat completions.

Kept dependency-light (httpx only) and mirrors the ResumeIQ backend's pattern of
calling Anthropic directly without an SDK.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import get_settings


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient:
    """Stateless-ish client. The agent owns message history; we format it per provider."""

    def __init__(self) -> None:
        self.cfg = get_settings()
        self._http = httpx.Client(timeout=self.cfg.timeout)

    # ------------------------------------------------------------------ api

    def complete(self, history: list[dict[str, Any]], tools_schemas: list[dict[str, Any]], max_tokens: int = 2500) -> ChatResult:
        """history = list of normalized message dicts (see format_messages)."""
        if self.cfg.provider == "anthropic":
            return self._anthropic(history, tools_schemas, max_tokens)
        if self.cfg.provider == "openai":
            return self._openai(history, tools_schemas, max_tokens)
        return _mock_complete(history)

    # --------------------------------------------------------------- providers

    def _anthropic(self, history: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> ChatResult:
        system = "".join(m["content"] for m in history if m["role"] == "system")
        messages = _to_anthropic(history)
        payload: dict[str, Any] = {
            "model": self.cfg.anthropic_model,
            "max_tokens": max_tokens,
            "system": system or "You are a helpful assistant.",
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        try:
            resp = self._http.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self.cfg.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"anthropic error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        result = ChatResult(raw=data)
        for block in data.get("content", []):
            if block.get("type") == "text":
                result.text = (result.text or "") + block.get("text", "")
            elif block.get("type") == "tool_use":
                result.tool_calls.append(
                    ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input") or {})
                )
        if result.text:
            result.text = result.text.strip()
        return result

    def _openai(self, history: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.cfg.openai_model,
            "messages": _to_openai(history),
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        url = self.cfg.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.openai_api_key}",
            "Content-Type": "application/json",
        }
        resp = None
        for attempt in range(3):
            try:
                resp = self._http.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise LLMError(f"openai request failed: {exc}") from exc
            if resp.status_code != 429:
                break
            if attempt < 2:
                time.sleep(_retry_after(resp.text))
        if resp is None:
            raise LLMError("openai request failed: no response")
        if resp.status_code != 200:
            # Some models (e.g. llama.cpp derivatives on Groq) occasionally emit a tool
            # call in <function=...> text form instead of structured JSON. Groq rejects
            # it with a 400 but includes the raw generation — rescue it and run the tool.
            fallback = _parse_failed_generation(resp.text)
            if fallback is not None:
                return fallback
            raise LLMError(f"openai error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        result = ChatResult(raw=data)
        message = (data.get("choices") or [{}])[0].get("message") or {}
        result.text = (message.get("content") or "").strip() or None
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            result.tool_calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=_parse_tool_arguments(fn.get("arguments")))
            )
        return result

    def close(self) -> None:
        self._http.close()


# ------------------------------------------------------------------ formatting


def _to_anthropic(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert normalized history to Anthropic Messages format.

    Normalized roles: system, user, assistant, tool.
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if m["role"] == "system":
            continue
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            content: list[dict[str, Any]] = []
            if m.get("tool_calls"):
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    content.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    )
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": "assistant", "content": m.get("content") or ""})
        elif m["role"] == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}],
                }
            )
    return _merge_anthropic(out)


def _merge_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic requires alternating roles; merge consecutive same-role messages."""
    merged: list[dict[str, Any]] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            prev = merged[-1]
            if isinstance(prev["content"], str) and isinstance(m["content"], str):
                prev["content"] = prev["content"] + "\n" + m["content"]
            elif isinstance(prev["content"], list) and isinstance(m["content"], list):
                prev["content"].extend(m["content"])
            else:
                merged.append(m)
        else:
            merged.append(m)
    return merged


def _to_openai(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in history:
        if m["role"] == "system":
            out.append({"role": "system", "content": m["content"]})
        elif m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content")}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return out


# ------------------------------------------------------------------ mock


def _mock_complete(history: list[dict[str, Any]]) -> ChatResult:
    """Deterministic fake LLM used for offline testing of the tool loop.

    Reads the last user message: if it contains '@tool <name> <json>' it returns a
    tool call, otherwise it echoes the conversation summary as text. Once a tool
    result exists in the history it always returns final text (no infinite loops).
    """
    if any(m["role"] == "tool" for m in history):
        return ChatResult(text="[mock] task complete (tools executed).")
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    import re

    marker = re.search(r"@tool\s+(\w+)\s*(\{.*\})?", last_user, re.S)
    if marker:
        name = marker.group(1)
        try:
            args = json.loads(marker.group(2)) if marker.group(2) else {}
        except json.JSONDecodeError:
            args = {}
        return ChatResult(tool_calls=[ToolCall(id="mock_tool_call", name=name, arguments=args)])
    text = f"[mock] received: {last_user[:200] or '(no user message)'}"
    return ChatResult(text=text)


# ------------------------------------------------------------------ fallback


def _retry_after(error_body: str, default: float = 8.0) -> float:
    """Parse Groq's 'Please try again in 8.26s' from a 429 body; clamp to 60s."""
    import re

    m = re.search(r"try again in\s+([\d.]+)\s*s", error_body)
    if m:
        try:
            return min(max(float(m.group(1)), 0.5), 60.0)
        except ValueError:
            pass
    return default


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """Parse a tool-call arguments JSON string into a dict.

    Providers (especially llama-3.x on Groq) sometimes emit `"arguments": "null"`,
    an empty string, or malformed JSON for tools with no required parameters.
    All of those must become `{}` so the tool still executes.
    """
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_failed_generation(error_body: str) -> ChatResult | None:
    """Rescue a tool call that a provider rejected as text.

    Some providers return 400 for the llama.cpp-style format
    ``<function=name={"arg": "val"}</function>`` but include the raw generation in the
    error body. Parse it so the agent loop can execute the tool anyway. Tolerated
    separators between the name and JSON: ``=``, ``:``, or nothing.
    """
    try:
        data = json.loads(error_body)
        gen = (data.get("error") or {}).get("failed_generation") or ""
        if not gen:
            return None
    except (json.JSONDecodeError, AttributeError):
        gen = error_body
    import re

    m = re.search(r"<function=(\w+)(?:[=:]\s*)?(\{.*?\}|[^\n<]+)(?:</function>|>|$)", gen, re.S)
    if not m:
        return None
    args_str = m.group(2).strip()
    try:
        arguments = json.loads(args_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(arguments, dict):
        return None
    return ChatResult(tool_calls=[ToolCall(id="rescued_tool_call", name=m.group(1), arguments=arguments)])
