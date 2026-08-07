"""Base Agent: owns the tool-calling loop, message history and tool execution."""
from __future__ import annotations

from typing import Any

from .config import get_settings
from .llm import LLMClient, LLMError
from .tools import Tool, build_tools, execute_tool


class Agent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[Tool] | None = None,
        max_tokens: int = 2500,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt.strip()
        self.tools = tools or build_tools(name)
        self.max_tokens = max_tokens
        self.client = LLMClient()
        self.history: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

    # ------------------------------------------------------------------ public

    def run(self, task: str) -> str:
        """Run one task (fresh user turn). Returns the final assistant text."""
        self.history.append({"role": "user", "content": task})
        return self._loop()

    def chat(self, task: str) -> str:
        """Conversational turn that keeps history (for REPL use)."""
        return self.run(task)

    # ------------------------------------------------------------------ internals

    def _tool_schemas(self) -> list[dict[str, Any]]:
        if self.client.cfg.provider == "anthropic":
            return [t.anthropic_schema() for t in self.tools]
        return [t.openai_schema() for t in self.tools]

    def _loop(self) -> str:
        max_steps = get_settings().max_tool_steps
        try:
            for _ in range(max_steps):
                result = self.client.complete(self.history, self._tool_schemas(), self.max_tokens)
                if not result.wants_tools:
                    self.history.append({"role": "assistant", "content": result.text or ""})
                    return result.text or "(no response)"
                self.history.append(
                    {
                        "role": "assistant",
                        "content": result.text,
                        "tool_calls": result.tool_calls,
                    }
                )
                for tc in result.tool_calls:
                    output = execute_tool(self.tools, tc.name, tc.arguments, self.name)
                    self.history.append(
                        {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": output}
                    )
            return "(reached the maximum tool steps — task may be incomplete)"
        except LLMError as exc:
            self.history.append({"role": "assistant", "content": f"[error: {exc}]"})
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001
            self.history.append({"role": "assistant", "content": f"[unexpected error: {exc}]"})
            return f"unexpected error: {exc}"

    def reset(self) -> None:
        self.history = [{"role": "system", "content": self.system_prompt}]

    def close(self) -> None:
        self.client.close()
