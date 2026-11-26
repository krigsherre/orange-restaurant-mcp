"""Deterministic rubric judge — the always-available fallback.

It encodes the same term-and-condition reasoning the LLM is asked to perform:
what the offer is really worth *for this booking*, after minimum spends,
day/time validity, card requirements and caps.

Values are percentages of the party's **reference bill** — the provider's cost for two
(per person × party size), or the caller's ``--spend`` expectation. The real bill is not
knowable before ordering, so every score is "at this assumed bill".
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


def _base_effective_pct(offer: Offer, bill: float) -> float:
    if offer.offer_type == "percent":
        return min(offer.value, 100.0)
    if offer.offer_type == "flat":
        return min(100.0, (offer.value / max(bill, 1.0)) * 100.0)
    if offer.offer_type == "perk":
        low = offer.text.lower()
        for needles, value in _PERK_BASE:
            if any(n in low for n in needles):
                return value
        return 6.0
    return 0.0


def score_offer(offer: Offer, ctx: QueryContext) -> tuple[float, float, list[str], str]:
    """Return (score 0-100, effective_pct, flags, rationale).

    ``effective_pct`` is the cash value at the party's reference bill. Score starts from
    that and only loses points for conditions that make the offer harder to use.
    """
    if offer.offer_type == "unknown":
        return 0.0, 0.0, ["unparsed-offer"], "Could not parse the offer value."

    flags: list[str] = []
    notes: list[str] = []
    conditions = set(offer.conditions)

    reference_bill = ctx.expected_bill(offer.per_person_ref)
    bill = reference_bill

    # Above the party's budget cap the offer cannot be used at all; say what unlocks it.
    if offer.min_spend and offer.min_spend > ctx.budget_for_party:
        return (
            0.0,
            0.0,
            ["min-spend-above-party-budget"],
            f"Needs ₹{offer.min_spend:,.0f} spend, above the ₹{ctx.budget_for_party:,.0f} party budget; "
            f"unlocks at ₹{offer.min_spend:,.0f}.",
        )

    # A minimum you can afford still inflates the bill the discount applies to.
    if offer.min_spend and offer.min_spend > bill:
        bill = offer.min_spend
        flags.append("min-spend-tight")
        notes.append(f"₹{offer.min_spend:,.0f} minimum raises the bill from ₹{reference_bill:,.0f}")

    effective = _base_effective_pct(offer, bill)
    score = effective

    if "up-to" in conditions:
        score *= 0.7
        flags.append("capped-up-to")
        notes.append("'up to' is a ceiling")

    # Weekday/weekend conditions only bite on the days they actually exclude.
    if "weekend-excluded" in conditions and ctx.is_weekend:
        score = 0.0
        effective = 0.0
        flags.append("not-valid-at-requested-time")
        notes.append("not valid on weekends")

    if "weekday-only" in conditions and ctx.is_weekend:
        score = 0.0
        effective = 0.0
        flags.append("not-valid-at-requested-time")
        notes.append("weekdays only")

    if "lunch-only" in conditions and ctx.is_dinner:
        score = 0.0
        effective = 0.0
        flags.append("not-valid-at-requested-time")
        notes.append("lunch-only but you asked for dinner")

    if "daytime-only" in conditions and ctx.is_dinner:
        score = 0.0
        effective = 0.0
        flags.append("not-valid-at-requested-time")
        notes.append("daytime-only window, invalid for dinner")

    if "breakfast-only" in conditions and ctx.is_dinner:
        score = 0.0
        effective = 0.0
        flags.append("not-valid-at-requested-time")
        notes.append("breakfast offer, invalid for dinner")

    if "card-required" in conditions or offer.source == "bankOffers":
        score -= 15
        flags.append("card-required")
        notes.append("needs a specific card")

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
        rationale = "Not usable for this booking." + (": " + "; ".join(notes) if notes else "")
    else:
        head = {
            "percent": f"{offer.value:.0f}% off",
            "flat": f"₹{offer.value:,.0f} off",
            "perk": "non-cash perk",
        }[offer.offer_type]
        detail = f"{effective:g}% of a ₹{bill:,.0f} bill"
        rationale = f"{head} = {detail}" + (f"; {'; '.join(notes)}" if notes else "")
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
