import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from orange.models import QueryContext
from orange.pipeline import run


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ctx = QueryContext(
            area="Koramangala",
            lat=12.9352,
            lng=77.6245,
            budget_per_person=1000.0,
            radius_km=5.0,
            party_size=2,
            date="2026-11-27",
            time="20:00",
        )
        cls.result = run(cls.ctx, judge_mode="rubric", out_dir=cls.tmp.name, live=False)
        cls.rows = cls.result.rows
        cls.rejected = {item["name"]: item["reason"] for item in cls.result.rejected}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_row_meets_constraints(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertLessEqual(row["per_person"], 1000.0, row["name"])
            self.assertLessEqual(row["distance_km"], 5.0, row["name"])

    def test_sorted_by_offer_then_distance_then_name(self):
        for left, right in zip(self.rows, self.rows[1:]):
            self.assertGreaterEqual(left["offer_score"], right["offer_score"])
            if left["offer_score"] == right["offer_score"]:
                self.assertLessEqual(left["distance_km"], right["distance_km"])
                if left["distance_km"] == right["distance_km"]:
                    self.assertLessEqual(left["name"].casefold(), right["name"].casefold())

    def test_one_row_per_provider_not_merged(self):
        pairs = [(row["name"], row["provider"]) for row in self.rows]
        self.assertIn(("Toit", "swiggy_dineout"), pairs)
        self.assertIn(("Toit", "district"), pairs)
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_rejections_are_explained(self):
        self.assertEqual(self.rejected.get("Big Brewsky"), "beyond-radius")
        self.assertEqual(self.rejected.get("Prost Brew Pub"), "over-budget")
        self.assertEqual(self.rejected.get("Windmills Craftworks"), "beyond-radius")
        self.assertEqual(self.rejected.get("Cafe Azzure"), "unknown-cost")

    def test_both_sources_present(self):
        self.assertEqual({row["provider"] for row in self.rows}, {"swiggy_dineout", "district"})

    def test_availability_annotated(self):
        meghana = next(row for row in self.rows if row["name"] == "Meghana Foods" and row["provider"] == "swiggy_dineout")
        self.assertEqual(meghana["availability"]["status"], "available")
        district_toit = next(row for row in self.rows if row["name"] == "Toit" and row["provider"] == "district")
        self.assertEqual(district_toit["availability"]["status"], "via-booking-link")

    def test_outputs_written(self):
        out = Path(self.tmp.name)
        for name in ("ranked.json", "ranked.csv", "ranked.md"):
            self.assertTrue((out / name).exists(), name)
            self.assertGreater((out / name).stat().st_size, 0)

    def test_auto_booking_simulates_top_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(self.ctx, judge_mode="rubric", out_dir=tmp, live=False, selection={"auto": True})
            self.assertTrue((Path(tmp) / "booking.md").exists())
            self.assertTrue((Path(tmp) / "booking.json").exists())
        self.assertIsNotNone(result.booking)
        self.assertEqual(result.booking.status, "simulation")
        self.assertTrue(result.booking.order_id.startswith("SIM-"))

    def test_require_available_keeps_only_bookable(self):
        import dataclasses

        ctx = dataclasses.replace(self.ctx, require_availability=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = run(ctx, judge_mode="rubric", out_dir=tmp, live=False)
        self.assertTrue(result.rows)
        for row in result.rows:
            self.assertEqual(row["availability"]["status"], "available")
        self.assertTrue(any(item["reason"] == "no-availability" for item in result.rejected))

    def test_offerless_restaurant_scores_zero(self):
        empire = next(row for row in self.rows if row["name"] == "Empire Restaurant")
        self.assertEqual(empire["offer_score"], 0.0)
        self.assertIsNone(empire["best_offer"])


if __name__ == "__main__":
    unittest.main()
