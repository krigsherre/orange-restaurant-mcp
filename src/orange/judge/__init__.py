"""Offer adjudication interfaces and factory."""

from __future__ import annotations

import os
from typing import Protocol

from ..models import Offer, QueryContext


class JudgeUnavailable(RuntimeError):
    """Raised when the requested judge cannot run (missing key, bad response)."""


class OfferJudge(Protocol):
    name: str

    def score(self, offers: list[Offer], ctx: QueryContext) -> None:
        """Mutate each offer in place: score, effective_pct, flags, rationale, judged_by."""


def build_judge(mode: str = "auto", *, verdicts_path: str | None = None) -> OfferJudge:
    from .rubric import RubricJudge

    mode = (mode or "auto").lower()
    if mode == "rubric":
        return RubricJudge()
    if mode == "replay":
        from .llm import ReplayJudge

        if not verdicts_path:
            raise JudgeUnavailable("--verdicts is required when --judge replay")
        return ReplayJudge(verdicts_path)
    if mode == "llm":
        from .llm import DeepSeekJudge

        return DeepSeekJudge()
    # auto: LLM when a key is configured, otherwise the transparent rubric.
    if os.environ.get("DEEPSEEK_API_KEY"):
        from .llm import DeepSeekJudge

        return DeepSeekJudge()
    return RubricJudge()
