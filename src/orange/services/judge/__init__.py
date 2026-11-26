"""Offer adjudication interfaces and factory."""

from __future__ import annotations

import os
from typing import Protocol

from ...domain.models import Offer, QueryContext


LLM_ENV = "ORANGE_LLM"
_TRUE = {"on", "true", "1", "yes", "enable", "enabled"}
_FALSE = {"off", "false", "0", "no", "disable", "disabled"}


class JudgeUnavailable(RuntimeError):
    """Raised when the requested judge cannot run (missing key, bad response)."""


class OfferJudge(Protocol):
    name: str

    def score(self, offers: list[Offer], ctx: QueryContext) -> None:
        """Mutate each offer in place: score, effective_pct, flags, rationale, judged_by."""


def normalize_llm(value: str | None) -> str:
    """Map a CLI/env value to one of auto, on, off. Empty means auto."""
    raw = (value or "").strip().lower()
    if not raw:
        return "auto"
    if raw in _TRUE:
        return "on"
    if raw in _FALSE:
        return "off"
    if raw == "auto":
        return "auto"
    raise ValueError(f"invalid LLM switch {value!r}: use auto, on or off")


def resolve_judge_mode(
    judge_mode: str = "auto",
    llm: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve --judge/--llm (and ORANGE_LLM) into rubric, llm or replay.

    --llm is the coarse on/off switch; --judge keeps the exact engines. Contradictions
    are errors instead of silent upgrades or downgrades.
    """
    env = os.environ if environ is None else environ
    mode = (judge_mode or "auto").strip().lower()
    if mode not in ("auto", "llm", "rubric", "replay"):
        raise ValueError(f"unknown judge mode {judge_mode!r}")
    switch = normalize_llm(llm if llm is not None else env.get(LLM_ENV))

    if mode == "replay":
        if switch != "auto":
            raise ValueError("--judge replay is already offline; drop --llm with it")
        return "replay"
    if mode == "llm":
        if switch == "off":
            raise ValueError("--judge llm conflicts with --llm off")
        return "llm"
    if mode == "rubric":
        if switch == "on":
            raise ValueError("--judge rubric conflicts with --llm on")
        return "rubric"
    # auto: an explicit switch wins over the presence of a key.
    if switch == "on":
        return "llm"
    if switch == "off":
        return "rubric"
    return "llm" if env.get("DEEPSEEK_API_KEY") else "rubric"


def llm_demanded(judge_mode: str = "auto", llm: str | None = None, *, environ: dict[str, str] | None = None) -> bool:
    """True when the caller explicitly asked for the LLM (flag, env or --judge llm)."""
    env = os.environ if environ is None else environ
    switch = normalize_llm(llm if llm is not None else env.get(LLM_ENV))
    return switch == "on" or (judge_mode or "").strip().lower() == "llm"


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
    # auto: honour ORANGE_LLM, then the presence of a key, otherwise the rubric.
    if resolve_judge_mode("auto") == "llm":
        from .llm import DeepSeekJudge

        return DeepSeekJudge()
    return RubricJudge()
