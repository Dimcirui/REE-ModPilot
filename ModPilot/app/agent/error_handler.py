"""
Structured PhaseError → user-facing message (design decision B7).

format()            — single LLM call; translates technical error into plain
                      language and appends [Retry] / [Skip] / [Ask] options.
parse_user_choice() — keyword match on the user's reply; no LLM call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.llm.client import LLMClient
    from app.phases.base import PhaseError


_ERROR_SYSTEM = (
    "You are a helpful assistant explaining a Blender automation error to a modder. "
    "Be concise (2-3 sentences max). Use plain language — no Python tracebacks, "
    "no bpy.ops syntax in the explanation. "
    "Always end your reply with exactly this line:\n"
    "[Retry] — run this phase again  |  [Skip] — skip and continue  |  "
    "[Ask] — explain what went wrong"
)


class ErrorHandler:
    def format(self, error: "PhaseError", llm: "LLMClient") -> str:
        """
        Translate PhaseError into a user-facing error message via one LLM call.
        Returns the formatted string ready to display to the user.
        """
        from app.agent.prompts import build_error_prompt

        prompt = build_error_prompt(
            operator=error.operator,
            message=error.message,
            suggestion=error.suggestion,
        )
        response = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system=_ERROR_SYSTEM,
            max_tokens=300,
        )
        return response.content

    def parse_user_choice(
        self, reply: str
    ) -> Literal["retry", "skip", "ask", "unknown"]:
        """
        Parse the user's response to the [Retry] / [Skip] / [Ask] prompt.
        Uses keyword matching — intentionally no LLM call for reliability.
        """
        lower = reply.lower()
        if any(kw in lower for kw in ("retry", "重试", "再试", "try again")):
            return "retry"
        if any(kw in lower for kw in ("skip", "跳过", "略过")):
            return "skip"
        if any(kw in lower for kw in ("ask", "explain", "why", "为什么", "问", "help")):
            return "ask"
        return "unknown"
