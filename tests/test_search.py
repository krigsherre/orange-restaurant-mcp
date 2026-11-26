import unittest

import _path  # noqa: F401
from orange.domain.models import QueryContext, Restaurant, Slot
from orange.services.search import search


def restaurant(name, *, cuisines=("North Indian",), distance=1.0, cost=1000, provider="swiggy_dineout", pid=None):
    return Restaurant(
        provider=provider,
        provider_id=pid or name.lower().replace(" ", "_"),
        name=name,
        cuisines=list(cuisines),
        distance_km=distance,
        cost_for_two=cost,
    )


def slot(time="8:00 PM", date="2026-11-27"):
    return Slot(date=date, display_time=time, is_free=True, inventory=4)


class UnifiedSearchTests(unittest.TestCase):
    def test_cuisine_and_window_resolved_in_one_call(self):
        ctx = QueryContext(cuisines=["Cafe"], time="20:00", time_end="22:00", require_availability=True)

        cafe_open = restaurant("Cafe Open", cuisines=["Cafe"])
        cafe_open.slots = [slot("8:30 PM")]
        cafe_closed = restaurant("Cafe Closed", cuisines=["Cafe"])
        cafe_closed.slots = [slot("6:30 PM")]
        indian = restaurant("Dhaba", cuisines=["North Indian"])
        indian.slots = [slot("8:00 PM")]

        result = search([cafe_open, cafe_closed, indian], ctx)

        self.assertEqual([r.name for r in result.kept], ["Cafe Open"])
        reasons = {item["name"]: item["reason"] for item in result.rejected}
        self.assertEqual(reasons["Cafe Closed"], "no-availability")
        self.assertEqual(reasons["Dhaba"], "cuisine-mismatch")

    def test_display_only_mode_keeps_rows_without_slots(self):
        ctx = QueryContext(cuisines=["Cafe"], require_availability=False)
        no_slot = restaurant("Cafe No Data", cuisines=["Cafe"])
        result = search([no_slot], ctx)
        self.assertEqual([r.name for r in result.kept], ["Cafe No Data"])
        self.assertFalse(result.rejected)

    def test_loader_called_only_for_survivors(self):
        ctx = QueryContext()
        near = restaurant("Near")
        far = restaurant("Far", distance=9.0)
        probed = []

        def loader(r):
            probed.append(r.name)
            return [slot()]

        search([near, far], ctx, slot_loader=loader)
        self.assertEqual(probed, ["Near"])

    def test_probe_cap_applies_only_when_annotating(self):
        ctx = QueryContext(require_availability=False)
        rows = [restaurant(f"R{i}") for i in range(5)]
        result = search(rows, ctx, slot_loader=lambda r: [slot()], max_probe=2)
        self.assertEqual(result.probed, 2)

    def test_hard_filter_ignores_probe_cap(self):
        ctx = QueryContext(require_availability=True)
        rows = [restaurant(f"R{i}") for i in range(5)]
        result = search(rows, ctx, slot_loader=lambda r: [slot()], max_probe=2)
        self.assertEqual(result.probed, 5)
        self.assertEqual(len(result.kept), 5)

    def test_loader_failure_marks_unknown_and_filters_under_hard_mode(self):
        ctx = QueryContext(require_availability=True)

        def boom(_):
            raise RuntimeError("mcp timeout")

        result = search([restaurant("Flaky")], ctx, slot_loader=boom)
        self.assertFalse(result.kept)
        self.assertEqual(result.rejected[0]["reason"], "no-availability")

    def test_loader_failure_does_not_raise_in_display_mode(self):
        ctx = QueryContext(require_availability=False)

        def boom(_):
            raise RuntimeError("mcp timeout")

        result = search([restaurant("Flaky")], ctx, slot_loader=boom)
        self.assertEqual([r.name for r in result.kept], ["Flaky"])

    def test_rejection_detail_is_useful(self):
        ctx = QueryContext(cuisines=["Italian"])
        result = search([restaurant("Dhaba", cuisines=["North Indian"])], ctx)
        self.assertIn("serves North Indian", result.rejected[0]["detail"])


if __name__ == "__main__":
    unittest.main()
