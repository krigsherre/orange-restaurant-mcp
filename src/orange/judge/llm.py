"""LLM offer judge (DeepSeek chat completions) + a replay judge for saved verdicts."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..models import Offer, QueryContext
from . import JudgeUnavailable

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0
CACHE_DIR = Path(".cache")

SYSTEM_PROMPT = """You are the offer adjudicator for a restaurant table-booking agent in Bengaluru, India.

Your job: decide how much a dine-in offer is REALLY worth for one specific booking, reading every
term and condition the way a careful human would. Advertised headlines lie; the fine print decides.

Judge each offer on:
1. Cash value for this party: percent vs flat rupee vs non-cash perk (free dessert, 1+1, welcome drink).
2. Minimum spend versus the party's budget. A ₹500-off coupon needing ₹3,000 spend is worthless here.
3. Validity against the requested date/time/meal. Lunch-only offers are useless for a dinner booking.
   Weekend exclusions and weekday-only windows matter for the requested date.
4. Access conditions: specific bank card, dine-in only, advance booking, one coupon per table,
   cannot be clubbed, alcohol excluded.
5. Caps and qualifiers: "up to 40%" is a ceiling, not a promise — discount it accordingly.
6. Stackability: assume offers do NOT stack unless stated, and say so in the rationale.

Scoring:
- 0   = unusable for this booking (expired window, impossible spend, wrong card you may not hold).
- 1-20 = marginal or heavily conditional.
- 21-50 = decent but with real strings attached.
- 51-80 = strong, broadly usable offer.
- 81-100 = exceptional, near-unconditional value.
Never reward a bigger headline number that is harder to actually use.

Return STRICT JSON only, no markdown, matching:
{"verdicts":[{"index":0,"score":0-100,"effective_pct":0-100,"flags":["short-kebab-tag"],"rationale":"max 20 words"}]}

One verdict per input index, same order. `effective_pct` is your estimate of the true cash-equivalent
discount percentage for this party. Use short kebab-case flags such as: capped-up-to, min-spend-tight,
min-spend-above-party-budget, weekend-excluded, weekday-only, lunch-only, not-valid-at-requested-time,
card-required, food-only, non-stackable, advance-booking, one-per-table, unparsed-offer."""


def build_user_prompt(offers: list[Offer], ctx: QueryContext) -> str:
    payload = {
        "booking": ctx.to_judge_dict(),
        "offers": [
            {
                "index": index,
                "restaurant_provider": offer.source,
                "headline": offer.title,
                "terms": offer.description,
                "parsed_type": offer.offer_type,
                "parsed_value": offer.value,
                "parsed_min_spend_inr": offer.min_spend,
                "restaurant_per_person_inr": offer.per_person_ref,
                "detected_conditions": offer.conditions,
            }
            for index, offer in enumerate(offers)
        ],
    }
    return (
        "Adjudicate every offer for this booking. Compare them against each other so the scores are "
        "relative and consistent across providers. Return JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _apply(offer: Offer, verdict: dict[str, Any], judged_by: str) -> None:
    score = verdict.get("score", 0)
    effective = verdict.get("effective_pct", score)
    try:
        offer.score = round(max(0.0, min(100.0, float(score))), 1)
        offer.effective_pct = round(max(0.0, min(100.0, float(effective))), 1)
    except (TypeError, ValueError):
        offer.score = 0.0
        offer.effective_pct = 0.0
    flags = verdict.get("flags") or []
    offer.flags = [str(f) for f in flags][:8] if isinstance(flags, list) else []
    offer.rationale = str(verdict.get("rationale", ""))[:200]
    offer.judged_by = judged_by


class DeepSeekJudge:
    name = "llm"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        cache: bool = True,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise JudgeUnavailable(
                "DEEPSEEK_API_KEY is not set (checked the environment and .env); "
                "add it to .env or export it, or pass --llm off to score with the deterministic rubric"
            )
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        default_timeout = os.environ.get("DEEPSEEK_TIMEOUT") or DEFAULT_TIMEOUT
        try:
            self.timeout = float(timeout if timeout is not None else default_timeout)
        except ValueError as exc:
            raise JudgeUnavailable(f"invalid DEEPSEEK_TIMEOUT: {exc}") from exc
        self.cache = cache

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    # ------------------------------------------------------------------ cache
    def _cache_key(self, offers: list[Offer], ctx: QueryContext) -> str:
        blob = json.dumps(
            {
                "model": self.model,
                "base_url": self.base_url,
                "ctx": ctx.to_judge_dict(),
                "offers": [[o.title, o.description, o.offer_type, o.value, o.min_spend] for o in offers],
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def _read_cache(self, key: str) -> list[dict[str, Any]] | None:
        if not self.cache:
            return None
        path = CACHE_DIR / "offer_judge.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get(key)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, key: str, verdicts: list[dict[str, Any]]) -> None:
        if not self.cache:
            return
        CACHE_DIR.mkdir(exist_ok=True)
        path = CACHE_DIR / "offer_judge.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (json.JSONDecodeError, OSError):
            data = {}
        data[key] = verdicts
        path.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------ call
    def _request(self, offers: list[Offer], ctx: QueryContext) -> list[dict[str, Any]]:
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(offers, ctx)},
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise JudgeUnavailable(f"DeepSeek HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network path
            raise JudgeUnavailable(f"DeepSeek unreachable: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise JudgeUnavailable(f"DeepSeek returned an unexpected payload: {exc}") from exc
        verdicts = parsed.get("verdicts") if isinstance(parsed, dict) else None
        if not isinstance(verdicts, list) or len(verdicts) != len(offers):
            raise JudgeUnavailable("DeepSeek returned a malformed verdict list")
        if not all(isinstance(verdict, dict) for verdict in verdicts):
            raise JudgeUnavailable("DeepSeek returned a non-object verdict")
        return verdicts

    def score(self, offers: list[Offer], ctx: QueryContext) -> None:
        if not offers:
            return
        key = self._cache_key(offers, ctx)
        verdicts = self._read_cache(key)
        if verdicts is None:
            verdicts = self._request(offers, ctx)
            self._write_cache(key, verdicts)
        for offer, verdict in zip(offers, verdicts):
            _apply(offer, verdict, self.name)


class ReplayJudge:
    """Applies previously saved LLM verdicts — useful for offline, reproducible runs."""

    name = "replay"

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def score(self, offers: list[Offer], ctx: QueryContext) -> None:
        data = json.loads(self.path.read_text())
        by_key = data.get("by_key") or {}
        by_offer = data.get("verdicts") or {}
        missing: list[Offer] = []
        for offer in offers:
            verdict = by_key.get(offer.text) or by_offer.get(offer.title)
            if verdict is None:
                missing.append(offer)
                continue
            _apply(offer, verdict, self.name)
        # A partial verdict file should not silently zero the rest of the field.
        if missing:
            from .rubric import RubricJudge

            RubricJudge().score(missing, ctx)
