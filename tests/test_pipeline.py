import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from orange.handlers.cli import main
from orange.services.judge import JudgeUnavailable
from orange.domain.models import QueryContext
from orange.app.pipeline import run


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

    def test_sorted_by_cash_then_score_then_distance(self):
        for left, right in zip(self.rows, self.rows[1:]):
            self.assertGreaterEqual(left["cash_pct"], right["cash_pct"])
            if left["cash_pct"] == right["cash_pct"]:
                self.assertGreaterEqual(left["offer_score"], right["offer_score"])
                if left["offer_score"] == right["offer_score"]:
                    self.assertLessEqual(left["distance_km"], right["distance_km"])

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
        # Link-only rows outrank the booked one, so the skip is recorded and auditable.
        self.assertTrue(result.meta["auto_skipped"])
        self.assertEqual(result.meta["auto_skipped"][0]["rank"], 1)
        self.assertEqual(result.meta["auto_skipped"][0]["status"], "via-booking-link")

    def test_selecting_a_link_only_row_hands_back_the_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(self.ctx, judge_mode="rubric", out_dir=tmp, live=False, selection={"rank": 1})
        top = self.rows[0]
        self.assertEqual(top["availability"]["status"], "via-booking-link")
        self.assertEqual(result.booking.status, "requires-user-action")
        self.assertEqual(result.booking.restaurant, top["name"])
        self.assertEqual(result.booking.booking_url, top["booking_url"])
        self.assertEqual(result.meta["auto_skipped"], [])

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

    def test_rubric_run_records_no_fallback(self):
        self.assertEqual(self.result.meta["judge"], "rubric")
        self.assertEqual(self.result.meta["judge_requested"], "rubric")
        self.assertIsNone(self.result.meta["judge_fallback"])

    def test_llm_api_failure_falls_back_to_rubric(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "orange.app.pipeline.build_judge", side_effect=JudgeUnavailable("boom")
        ):
            result = run(self.ctx, judge_mode="llm", out_dir=tmp, live=False)
        self.assertEqual(result.meta["judge"], "rubric")
        self.assertIn("boom", result.meta["judge_fallback"])
        self.assertTrue(result.rows)

    def test_strict_llm_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(JudgeUnavailable):
                run(self.ctx, judge_mode="llm", judge_strict=True, out_dir=tmp, live=False)


class CliLLMSwitchTests(unittest.TestCase):
    def _env(self, **extra):
        # Never pick up the developer's real .env: these tests assert on missing keys.
        return {"ORANGE_ENV_FILE": os.devnull, **extra}

    def test_llm_off_runs_offline(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(main(["--llm", "off", "--out", tmp]), 0)
            self.assertTrue((Path(tmp) / "ranked.json").exists())

    def test_llm_on_without_a_key_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(main(["--llm", "on", "--out", tmp]), 2)
            self.assertFalse((Path(tmp) / "ranked.json").exists())

    def test_conflicting_switches_exit_2(self):
        with mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(main(["--judge", "rubric", "--llm", "on"]), 2)

    def test_env_on_without_a_key_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._env(ORANGE_LLM="on"), clear=True
        ):
            self.assertEqual(main(["--out", tmp]), 2)

    def test_env_off_with_a_key_still_runs_the_rubric(self):
        env = self._env(ORANGE_LLM="off", DEEPSEEK_API_KEY="fake")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(main(["--out", tmp]), 0)
            data = json.loads((Path(tmp) / "ranked.json").read_text())
        self.assertEqual(data["meta"]["judge"], "rubric")
        self.assertIsNone(data["meta"]["judge_fallback"])

    def test_spend_override_sets_the_bill_reference(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(main(["--llm", "off", "--spend", "500", "--out", tmp]), 0)
            data = json.loads((Path(tmp) / "ranked.json").read_text())
        self.assertTrue(data["results"])
        for row in data["results"]:
            self.assertEqual(row["bill_reference"], 1000.0)  # ₹500 × party of 2

    def test_dotenv_supplies_the_key(self):
        # A refused local socket fails fast; if .env were ignored the key would be
        # missing and the CLI would exit 2 instead of ranking with a fallback.
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "custom.env"
            env_file.write_text(
                "export DEEPSEEK_API_KEY=fake\n"
                "ORANGE_LLM=on\n"
                "DEEPSEEK_BASE_URL=http://127.0.0.1:9\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"ORANGE_ENV_FILE": str(env_file)}, clear=True), mock.patch(
                "orange.services.judge.llm.CACHE_DIR", Path(tmp) / "cache"
            ):
                self.assertEqual(main(["--out", tmp]), 0)
                data = json.loads((Path(tmp) / "ranked.json").read_text())
        self.assertEqual(data["meta"]["judge_requested"], "llm")
        self.assertEqual(data["meta"]["judge"], "rubric")
        self.assertIn("unreachable", data["meta"]["judge_fallback"])


if __name__ == "__main__":
    unittest.main()
