import tempfile
import unittest

import _path  # noqa: F401
from orange.cuisine import matches, parse_cuisine_args
from orange.models import QueryContext, Restaurant
from orange.pipeline import run
from orange.search import search


class CuisineMatchingTests(unittest.TestCase):
    def test_case_and_accent_insensitive(self):
        self.assertTrue(matches(["Café"], ["cafe"]))
        self.assertTrue(matches(["SOUTH INDIAN"], ["south indian"]))

    def test_alias_cafe_coffee(self):
        self.assertTrue(matches(["Coffee"], ["cafe"]))
        self.assertTrue(matches(["Cafe"], ["coffee"]))

    def test_alias_veg(self):
        self.assertTrue(matches(["Vegetarian"], ["veg"]))

    def test_word_containment(self):
        self.assertTrue(matches(["North Indian", "Mughlai"], ["indian"]))
        self.assertFalse(matches(["Continental"], ["indian"]))

    def test_no_false_positive_between_regions(self):
        self.assertFalse(matches(["South Indian"], ["North Indian"]))

    def test_empty_filter_matches_everything(self):
        self.assertTrue(matches(["Anything"], []))
        self.assertFalse(matches([], ["cafe"]))

    def test_parse_repeated_and_comma_separated(self):
        self.assertEqual(parse_cuisine_args(["Cafe, South Indian", "cafe"]), ["Cafe", "South Indian"])
        self.assertEqual(parse_cuisine_args(None), [])
        self.assertEqual(parse_cuisine_args([" , "]), [])


class CuisineFilterTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext(cuisines=["Cafe"])

    def test_select_keeps_only_matching(self):
        cafe = Restaurant(provider="p", provider_id="1", name="Cafe One", cuisines=["Cafe", "Coffee"], distance_km=1, cost_for_two=800)
        indian = Restaurant(provider="p", provider_id="2", name="Dhaba", cuisines=["North Indian"], distance_km=1, cost_for_two=800)
        result = search([cafe, indian], self.ctx)
        kept, rejected = result.kept, result.rejected
        self.assertEqual([r.name for r in kept], ["Cafe One"])
        self.assertEqual(rejected[0]["reason"], "cuisine-mismatch")

    def test_hard_constraints_win_the_reason(self):
        far = Restaurant(provider="p", provider_id="3", name="Far Cafe", cuisines=["Cafe"], distance_km=9, cost_for_two=800)
        rejected = search([far], self.ctx).rejected
        self.assertEqual(rejected[0]["reason"], "beyond-radius")

    def test_multi_cuisine_is_or(self):
        ctx = QueryContext(cuisines=["Cafe", "Italian"])
        rows = [
            Restaurant(provider="p", provider_id="1", name="A", cuisines=["Cafe"], distance_km=1, cost_for_two=800),
            Restaurant(provider="p", provider_id="2", name="B", cuisines=["Italian"], distance_km=1, cost_for_two=800),
            Restaurant(provider="p", provider_id="3", name="C", cuisines=["Biryani"], distance_km=1, cost_for_two=800),
        ]
        kept = search(rows, ctx).kept
        self.assertEqual({r.name for r in kept}, {"A", "B"})


class CuisinePipelineTests(unittest.TestCase):
    def test_end_to_end_cafe_filter(self):
        ctx = QueryContext(cuisines=["Cafe"])
        with tempfile.TemporaryDirectory() as tmp:
            result = run(ctx, judge_mode="rubric", out_dir=tmp, live=False)
        self.assertTrue(result.rows)
        for row in result.rows:
            self.assertTrue(
                any("cafe" in c.casefold() or "coffee" in c.casefold() for c in row["cuisines"]),
                f"{row['name']} {row['cuisines']}",
            )
        self.assertEqual({row["provider"] for row in result.rows}, {"swiggy_dineout", "district"})


if __name__ == "__main__":
    unittest.main()
