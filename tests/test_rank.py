import unittest

import _path  # noqa: F401
from orange.models import Offer, QueryContext, Restaurant
from orange.normalize import haversine_km
from orange.rank import pick_slot, rank_rows
from orange.search import search


def restaurant(name, *, distance=1.0, cost=1000, provider="swiggy_dineout", score=None, pid=None):
    offers = []
    if score is not None:
        offer = Offer(title="x", score=score, effective_pct=score)
        offers.append(offer)
    return Restaurant(
        provider=provider,
        provider_id=pid or name.lower().replace(" ", "_"),
        name=name,
        distance_km=distance,
        cost_for_two=cost,
        offers=offers,
    )


class GeoAndBudgetTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext()

    def test_haversine_anchor_is_zero(self):
        self.assertEqual(haversine_km(12.9352, 77.6245, 12.9352, 77.6245), 0.0)

    def test_boundary_budget_inclusive(self):
        result = search([restaurant("Exactly", cost=2000)], self.ctx)
        kept, rejected = result.kept, result.rejected
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].per_person, 1000.0)
        self.assertFalse(rejected)

    def test_over_budget_rejected(self):
        rejected = search([restaurant("Pricey", cost=2004)], self.ctx).rejected
        self.assertEqual(rejected[0]["reason"], "over-budget")

    def test_boundary_radius_inclusive(self):
        kept = search([restaurant("Edge", distance=5.0)], self.ctx).kept
        self.assertEqual(len(kept), 1)

    def test_beyond_radius_rejected(self):
        rejected = search([restaurant("Far", distance=5.01)], self.ctx).rejected
        self.assertEqual(rejected[0]["reason"], "beyond-radius")

    def test_unknown_cost_rejected(self):
        rejected = search([restaurant("Mystery", cost=None)], self.ctx).rejected
        self.assertEqual(rejected[0]["reason"], "unknown-cost")

    def test_unverifiable_proximity_rejected(self):
        record = restaurant("NoGeo")
        record.distance_km = None
        rejected = search([record], self.ctx).rejected
        self.assertEqual(rejected[0]["reason"], "unverifiable-proximity")


class ComparatorTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext()

    def test_higher_offer_wins(self):
        rows = rank_rows([restaurant("Weak", score=10), restaurant("Strong", score=90)], self.ctx)
        self.assertEqual([r["name"] for r in rows], ["Strong", "Weak"])

    def test_tie_broken_by_distance(self):
        rows = rank_rows([restaurant("Far", distance=3.0, score=50), restaurant("Near", distance=1.0, score=50)], self.ctx)
        self.assertEqual([r["name"] for r in rows], ["Near", "Far"])

    def test_tie_broken_alphabetically_case_insensitive(self):
        rows = rank_rows(
            [restaurant("beta", distance=1.0, score=50), restaurant("Alpha", distance=1.0, score=50)],
            self.ctx,
        )
        self.assertEqual([r["name"] for r in rows], ["Alpha", "beta"])

    def test_no_offer_ranks_last(self):
        rows = rank_rows([restaurant("NoOffer"), restaurant("Tiny", score=1)], self.ctx)
        self.assertEqual([r["name"] for r in rows], ["Tiny", "NoOffer"])

    def test_ranking_is_deterministic(self):
        records = [restaurant(f"R{i}", distance=1.0 + i / 10, score=40) for i in range(5)]
        first = [r["name"] for r in rank_rows(list(records), self.ctx)]
        second = [r["name"] for r in rank_rows(list(reversed(records)), self.ctx)]
        self.assertEqual(first, second)


class SlotTests(unittest.TestCase):
    def test_picks_closest_slot(self):
        from orange.models import Slot

        slots = [
            Slot(date="2026-11-27", display_time="7:00 PM", group="Dinner"),
            Slot(date="2026-11-27", display_time="8:00 PM", group="Dinner"),
            Slot(date="2026-11-28", display_time="8:00 PM", group="Dinner"),
        ]
        chosen = pick_slot(slots, QueryContext(time="20:00"))
        self.assertEqual(chosen.display_time, "8:00 PM")


if __name__ == "__main__":
    unittest.main()
