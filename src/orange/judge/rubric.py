"""Deterministic rubric judge — the always-available fallback.

It encodes the same term-and-condition reasoning the LLM is asked to perform:
what the offer is really worth *for this booking*, after minimum spends,
day/time validity, card requirements and caps.
"""

from __future__ import annotations

from ..models import Offer, QueryContext

# Typical perceived value of a non-cash perk, in "effective percent" terms.
_PERK_BASE = (
    (("free dessert", "complimentary dessert", "free sweet"), 12.0),
    (("welcome drink", "free drink", "complimentary drink", "mocktail"), 8.0),
    (("1+1", "buy 1 get 1", "buy one get one", "2+1"), 20.0),
    (("unlimited", "bottomless"), 25.0),
    (("free", "complimentary"), 10.0),
)


def _base_effective_pct(offer: Offer, ctx: QueryContext) -> float:
    per_person = max(offer.per_person_ref or ctx.budget_per_person, 1.0)
    if offer.offer_type == "percent":
        return min(offer.value, 100.0)
    if offer.offer_type == "flat":
        return min(100.0, (offer.value / per_person) * 100.0)
    if offer.offer_type == "perk":
        low = offer.text.lower()
        for needles, value in _PERK_BASE:
            if any(n in low for n in needles):
                return value
        return 6.0
    return 0.0


def score_offer(offer: Offer, ctx: QueryContext) -> tuple[float, float, list[str], str]:
    """Return (score 0-100, effective_pct, flags, rationale)."""
    effective = _base_effective_pct(offer, ctx)
    score = effective
    flags: list[str] = []
    notes: list[str] = []
    conditions = set(offer.conditions)

    if offer.offer_type == "unknown":
        return 0.0, 0.0, ["unparsed-offer"], "Could not parse the offer value."

    if "up-to" in conditions:
        score *= 0.7
        flags.append("capped-up-to")
        notes.append("advertised as 'up to', so treat as a ceiling")

    per_person = max(offer.per_person_ref or ctx.budget_per_person, 1.0)
    expected_bill = per_person * ctx.party_size

    if offer.min_spend:
        if offer.min_spend > ctx.budget_for_party:
            score = min(score, 5.0)
            flags.append("min-spend-above-party-budget")
            notes.append(f"needs ₹{offer.min_spend:,.0f} spend vs ₹{ctx.budget_for_party:,.0f} party budget")
        elif offer.min_spend > expected_bill:
            score -= 12
            flags.append("min-spend-tight")
            notes.append(f"requires ₹{offer.min_spend:,.0f} minimum vs a typical ₹{expected_bill:,.0f} bill")

    if "weekend-excluded" in conditions:
        score -= 10
        flags.append("weekend-excluded")
        notes.append("not valid on weekends")

    if "weekday-only" in conditions:
        score -= 6
        flags.append("weekday-only")
        notes.append("weekdays only")

    if "lunch-only" in conditions and ctx.is_dinner:
        score = min(score, 5.0)
        flags.append("not-valid-at-requested-time")
        notes.append("lunch-only but you asked for dinner")

    if "card-required" in conditions or offer.source == "bankOffers":
        score -= 15
        flags.append("card-required")
        notes.append("needs a specific bank card")

    if "no-alcohol" in conditions:
        flags.append("food-only")
        notes.append("excludes alcohol")

    if "non-stackable" in conditions:
        score -= 3
        flags.append("non-stackable")

    if "advance-booking" in conditions:
        score -= 4
        flags.append("advance-booking")

    if "one-per-table" in conditions:
        flags.append("one-per-table")

    # No bonus for a clean offer: score stays bounded by effective value, so a
    # conditional 20% can never beat an unconditional 20%.

    score = round(max(0.0, min(100.0, score)), 1)
    effective = round(max(0.0, min(100.0, effective)), 1)

    if score <= 0:
        rationale = "Not usable for this booking."
    else:
        head = {
            "percent": f"{offer.value:.0f}% off",
            "flat": f"₹{offer.value:,.0f} off",
            "perk": "non-cash perk",
        }[offer.offer_type]
        rationale = head + (": " + "; ".join(notes) if notes else "")
    return score, effective, sorted(set(flags)), rationale[:160]


class RubricJudge:
    name = "rubric"
    judged_by = "rubric"

    def score(self, offers: list[Offer], ctx: QueryContext) -> None:
        for offer in offers:
            score, effective, flags, rationale = score_offer(offer, ctx)
            offer.score = score
            offer.effective_pct = effective
            offer.flags = flags
            offer.rationale = rationale
            offer.judged_by = self.judged_by
