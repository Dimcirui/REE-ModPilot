"""
Structured PhaseError → user-facing message (design decision B7).

format()            — single LLM call; translates technical error into plain
                      language and appends [Retry] / [Skip] / [Ask] options.
parse_user_choice() — LLM-based intent classification (retry / skip / ask / unknown).
                      Keyword matching is kept as a fallback for unexpected LLM output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.llm.client import LLMClient
    from app.phases.base import PhaseError

Choice = Literal["retry", "skip", "ask", "unknown"]

_ERROR_SYSTEM = (
    "You are a helpful assistant explaining a Blender automation error to a modder. "
    "IMPORTANT: Always respond in Simplified Chinese. Keep technical terms (operator names, "
    "object names, file paths) in English. "
    "Be concise (2-3 sentences max). Use plain language — no Python tracebacks, "
    "no bpy.ops syntax in the explanation. "
    "Always end your reply with exactly this line:\n"
    "[Retry] — 重新执行  |  [Skip] — 跳过继续  |  [Ask] — 查看详情"
)

_CLASSIFY_SYSTEM = (
    "You are classifying a user's reply to an error recovery prompt.\n"
    "The user was shown three options: [Retry] — 重新执行 | [Skip] — 跳过继续 | [Ask] — 查看详情\n\n"
    "Output EXACTLY ONE word — no punctuation, no explanation:\n"
    "  retry   — user wants to try the step again (includes: proceed, start, go ahead, exit ask mode)\n"
    "  skip    — user wants to skip this step and continue\n"
    "  ask     — user wants more detail or explanation about the error\n"
    "  unknown — intent is unclear\n\n"
    "Examples:\n"
    "  '重试' → retry\n"
    "  '继续吧' → retry\n"
    "  '继续' → retry\n"
    "  '开始' → retry\n"
    "  '直接开始' → retry\n"
    "  '执行' → retry\n"
    "  '退出ask模式' → retry\n"
    "  '好的' → retry\n"
    "  '直接跳过继续即可' → skip\n"
    "  '跳过' → skip\n"
    "  '这一步已经做完了，跳过' → skip\n"
    "  '为什么失败' → ask\n"
    "  '可以告诉我具体哪里出错了吗' → ask\n"
    "  'ok' → retry\n"
    "  'sure whatever' → unknown\n"
)

_VALID_CHOICES: frozenset[str] = frozenset({"retry", "skip", "ask", "unknown"})


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

    def parse_user_choice(self, reply: str, llm: "LLMClient") -> Choice:
        """
        Classify the user's reply to [Retry] / [Skip] / [Ask] using an LLM call.

        Primary: single LLM call with a strict classification prompt (max 10 tokens).
        Fallback: keyword matching used when the LLM returns an unrecognised value
                  or raises an exception.
        """
        if not reply.strip():
            return "unknown"

        try:
            response = llm.chat(
                messages=[{"role": "user", "content": reply}],
                system=_CLASSIFY_SYSTEM,
                max_tokens=10,
            )
            choice = response.content.strip().lower().split()[0] if response.content.strip() else ""
            # Only trust the LLM when it gives a definitive answer — if "unknown",
            # fall through to keyword matching which has better coverage of Chinese
            # imperatives like "开始", "退出ask模式", "继续" that LLMs may miss.
            if choice in _VALID_CHOICES and choice != "unknown":
                return choice  # type: ignore[return-value]
        except Exception:
            pass

        return _keyword_fallback(reply)


# ── keyword fallback ───────────────────────────────────────────────────────────


def _keyword_fallback(reply: str) -> Choice:
    """
    Simple keyword matcher used when LLM classification is unavailable or unclear.
    Skip is checked before retry to handle messages that contain both "跳过" and "继续".
    """
    lower = reply.lower()
    if any(kw in lower for kw in ("skip", "跳过", "略过")):
        return "skip"
    if any(kw in lower for kw in (
        "retry", "重试", "再试", "尝试", "重新", "再来",
        "继续", "开始", "执行", "退出", "进行", "好的", "好", "行",
    )):
        return "retry"
    if "跳" in lower:
        return "skip"
    if any(kw in lower for kw in ("ask", "explain", "why", "为什么", "问", "help",
                                   "detail", "具体", "告诉我", "详细", "错误",
                                   "出错", "失败", "原因", "哪里", "怎么", "什么情况",
                                   "查看详情", "详情")):
        return "ask"
    return "unknown"
