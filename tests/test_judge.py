import json
import os
import unittest
from unittest import mock

import _path  # noqa: F401
from orange.services.judge import (
    JudgeUnavailable,
    build_judge,
    llm_demanded,
    normalize_llm,
    resolve_judge_mode,
)
from orange.services.judge.llm import DeepSeekJudge, build_user_prompt
from orange.services.judge.rubric import RubricJudge, score_offer
from orange.domain.models import Offer, QueryContext


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext(budget_per_person=1000.0, party_size=2, time="20:00")

    def test_lunch_only_offer_is_useless_at_dinner(self):
        offer = Offer(title="10% off", offer_type="percent", value=10.0, conditions=["lunch-only"])
        score, *_ = score_offer(offer, self.ctx)
        self.assertLessEqual(score, 5.0)

    def test_min_spend_above_party_budget_gutted(self):
        offer = Offer(title="30% off", offer_type="percent", value=30.0, min_spend=2500.0)
        score, _, flags, _ = score_offer(offer, self.ctx)
        self.assertLessEqual(score, 5.0)
        self.assertIn("min-spend-above-party-budget", flags)

    def test_bank_offer_penalised(self):
        plain = Offer(title="15% off", offer_type="percent", value=15.0)
        bank = Offer(title="15% off", offer_type="percent", value=15.0, source="bankOffers", conditions=["card-required"])
        self.assertGreater(score_offer(plain, self.ctx)[0], score_offer(bank, self.ctx)[0])

    def test_up_to_is_discounted(self):
        firm = Offer(title="20% off", offer_type="percent", value=20.0)
        capped = Offer(title="20% off", offer_type="percent", value=20.0, conditions=["up-to"])
        self.assertGreater(score_offer(firm, self.ctx)[0], score_offer(capped, self.ctx)[0])

    def test_flat_offer_scaled_by_bill(self):
        small = Offer(title="₹500 off", offer_type="flat", value=500.0, per_person_ref=1000.0)
        score, effective, _, _ = score_offer(small, self.ctx)
        self.assertAlmostEqual(effective, 25.0)  # ₹500 on the ₹2,000 party bill
        self.assertEqual(score, 25.0)

    def test_spend_override_changes_the_reference_bill(self):
        ctx = QueryContext(budget_per_person=1000.0, party_size=2, spend_per_person=400.0)
        offer = Offer(title="₹200 off", offer_type="flat", value=200.0)
        _, effective, _, _ = score_offer(offer, ctx)
        self.assertAlmostEqual(effective, 25.0)  # ₹200 / ₹800 bill
        _, default_effective, _, _ = score_offer(offer, QueryContext(budget_per_person=1000.0))
        self.assertAlmostEqual(default_effective, 10.0)  # ₹200 / ₹2,000 budget-sized bill

    def test_party_size_scales_the_bill(self):
        ctx = QueryContext(budget_per_person=1000.0, party_size=4)
        offer = Offer(title="₹300 off", offer_type="flat", value=300.0, per_person_ref=600.0)
        _, effective, _, _ = score_offer(offer, ctx)
        self.assertAlmostEqual(effective, 12.5)  # ₹300 / ₹2,400

    def test_min_spend_above_budget_reports_the_unlock(self):
        offer = Offer(title="30% off", offer_type="percent", value=30.0, min_spend=2500.0)
        score, effective, flags, rationale = score_offer(offer, self.ctx)
        self.assertEqual(score, 0.0)
        self.assertEqual(effective, 0.0)
        self.assertIn("min-spend-above-party-budget", flags)
        self.assertIn("unlocks at ₹2,500", rationale)

    def test_min_spend_above_bill_inflates_the_denominator(self):
        ctx = QueryContext(budget_per_person=1000.0, party_size=2)
        offer = Offer(title="₹100 off above ₹800", offer_type="flat", value=100.0, min_spend=800.0, per_person_ref=350.0)
        _, effective, flags, _ = score_offer(offer, ctx)
        self.assertAlmostEqual(effective, 12.5)  # ₹100 / ₹800, not / ₹700
        self.assertIn("min-spend-tight", flags)

    def test_weekend_rules_only_bite_at_weekends(self):
        friday = QueryContext(date="2026-11-27")
        saturday = QueryContext(date="2026-11-28")
        weekend_only = Offer(title="15% off", offer_type="percent", value=15.0, conditions=["weekend-excluded"])
        weekday_only = Offer(title="10% off", offer_type="percent", value=10.0, conditions=["weekday-only"])
        self.assertEqual(score_offer(weekend_only, friday)[0], 15.0)
        self.assertEqual(score_offer(weekend_only, saturday)[0], 0.0)
        self.assertEqual(score_offer(weekday_only, friday)[0], 10.0)
        self.assertEqual(score_offer(weekday_only, saturday)[0], 0.0)

    def test_daytime_window_is_invalid_at_dinner(self):
        offer = Offer(title="20% off", offer_type="percent", value=20.0, conditions=["daytime-only"])
        self.assertEqual(score_offer(offer, self.ctx)[0], 0.0)
        lunch = QueryContext(time="13:00", time_end="14:00")
        self.assertEqual(score_offer(offer, lunch)[0], 20.0)

    def test_breakfast_offer_is_invalid_at_dinner(self):
        offer = Offer(title="20% off", offer_type="percent", value=20.0, conditions=["breakfast-only"])
        self.assertEqual(score_offer(offer, self.ctx)[0], 0.0)

    def test_judge_mutates_offers(self):
        offers = [Offer(title="Flat 20% off", offer_type="percent", value=20.0)]
        RubricJudge().score(offers, self.ctx)
        self.assertEqual(offers[0].judged_by, "rubric")
        self.assertGreater(offers[0].score, 0)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class LLMSwitchTests(unittest.TestCase):
    def test_normalize_accepts_on_off_auto(self):
        self.assertEqual(normalize_llm("on"), "on")
        self.assertEqual(normalize_llm(" OFF "), "off")
        self.assertEqual(normalize_llm(None), "auto")
        self.assertEqual(normalize_llm(""), "auto")
        with self.assertRaises(ValueError):
            normalize_llm("maybe")

    def test_auto_follows_the_key(self):
        self.assertEqual(resolve_judge_mode("auto", None, environ={}), "rubric")
        self.assertEqual(resolve_judge_mode("auto", None, environ={"DEEPSEEK_API_KEY": "k"}), "llm")

    def test_off_beats_a_configured_key(self):
        env = {"DEEPSEEK_API_KEY": "k", "ORANGE_LLM": "off"}
        self.assertEqual(resolve_judge_mode("auto", None, environ=env), "rubric")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(build_judge("auto"), RubricJudge)

    def test_on_without_key_raises_at_build_time(self):
        self.assertEqual(resolve_judge_mode("auto", "on", environ={}), "llm")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(JudgeUnavailable) as caught:
                build_judge("llm")
        self.assertIn("DEEPSEEK_API_KEY", str(caught.exception))
        self.assertIn("--llm off", str(caught.exception))

    def test_env_switch_overrides_the_key(self):
        self.assertEqual(resolve_judge_mode("auto", None, environ={"ORANGE_LLM": "on"}), "llm")
        both = {"ORANGE_LLM": "off", "DEEPSEEK_API_KEY": "k"}
        self.assertEqual(resolve_judge_mode("auto", None, environ=both), "rubric")

    def test_contradictions_are_errors(self):
        for judge, llm in (("rubric", "on"), ("llm", "off"), ("replay", "on")):
            with self.assertRaises(ValueError):
                resolve_judge_mode(judge, llm, environ={})

    def test_replay_stays_offline(self):
        self.assertEqual(resolve_judge_mode("replay", None, environ={"DEEPSEEK_API_KEY": "k"}), "replay")

    def test_llm_demanded_tracks_explicit_requests(self):
        self.assertTrue(llm_demanded("auto", "on", environ={}))
        self.assertTrue(llm_demanded("auto", None, environ={"ORANGE_LLM": "on"}))
        self.assertTrue(llm_demanded("llm", None, environ={}))
        # auto + a key is implicit, not a demand: API failures may fall back.
        self.assertFalse(llm_demanded("auto", None, environ={"DEEPSEEK_API_KEY": "k"}))
        self.assertFalse(llm_demanded("rubric", None, environ={}))


class DeepSeekJudgeTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext(budget_per_person=1000.0, party_size=2, time="20:00")

    def _judge(self, **kwargs):
        return DeepSeekJudge(
            api_key="test-key",
            model="deepseek-chat",
            cache=False,
            base_url="https://api.example.test",
            **kwargs,
        )

    def test_request_scores_offers_and_uses_configured_model(self):
        verdicts = [{"index": 0, "score": 42, "effective_pct": 33.5, "flags": ["capped-up-to"], "rationale": "ok"}]
        payload = {"choices": [{"message": {"content": json.dumps({"verdicts": verdicts})}}]}
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _FakeResponse(payload)

        offers = [Offer(title="20% off up to ₹300", offer_type="percent", value=20.0)]
        with mock.patch("orange.services.judge.llm.urllib.request.urlopen", fake_urlopen):
            self._judge().score(offers, self.ctx)

        self.assertEqual(offers[0].score, 42.0)
        self.assertEqual(offers[0].effective_pct, 33.5)
        self.assertEqual(offers[0].flags, ["capped-up-to"])
        self.assertEqual(offers[0].judged_by, "llm")
        request = captured["request"]
        self.assertEqual(request.full_url, "https://api.example.test/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_env_overrides_model_and_base_url(self):
        env = {"DEEPSEEK_MODEL": "deepseek-reasoner", "DEEPSEEK_BASE_URL": "https://proxy.test/"}
        with mock.patch.dict(os.environ, env, clear=True):
            judge = DeepSeekJudge(api_key="k")
        self.assertEqual(judge.model, "deepseek-reasoner")
        self.assertEqual(judge.endpoint, "https://proxy.test/chat/completions")

    def test_prompt_carries_reference_bill_and_weekday(self):
        ctx = QueryContext(budget_per_person=1000.0, party_size=2, date="2026-11-27")
        offer = Offer(title="₹300 off", offer_type="flat", value=300.0, per_person_ref=600.0)
        prompt = build_user_prompt([offer], ctx)
        self.assertIn('"bill_reference_inr": 1200.0', prompt)
        self.assertIn('"requested_weekday": "Friday"', prompt)
        self.assertIn('"is_weekend": false', prompt)

    def test_malformed_response_becomes_judge_unavailable(self):
        payload = {"choices": [{"message": {"content": "not json"}}]}

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(payload)

        with mock.patch("orange.services.judge.llm.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(JudgeUnavailable):
                self._judge().score([Offer(title="10% off")], self.ctx)

    def test_cache_key_changes_with_model_and_endpoint(self):
        offers = [Offer(title="10% off")]
        base = self._judge()._cache_key(offers, self.ctx)
        other_model = DeepSeekJudge(api_key="k", model="other", cache=False, base_url="https://api.example.test")
        other_host = DeepSeekJudge(api_key="k", model="deepseek-chat", cache=False, base_url="https://proxy.test")
        self.assertNotEqual(base, other_model._cache_key(offers, self.ctx))
        self.assertNotEqual(base, other_host._cache_key(offers, self.ctx))


if __name__ == "__main__":
    unittest.main()


class DeepSeekIntegrationTests(unittest.TestCase):
    """Wire-level check: a real localhost HTTP server, no urllib mocking."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            seen = []

            def do_POST(self):  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][1]["content"]
                offers = json.loads(prompt.split("\n\n", 1)[1])["offers"]
                Handler.seen.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body, "offers": len(offers)}
                )
                verdicts = [
                    {
                        "index": i,
                        "score": 88,
                        "effective_pct": 50,
                        "flags": ["loopback"],
                        "rationale": "loopback verdict",
                    }
                    for i in range(len(offers))
                ]
                payload = json.dumps({"choices": [{"message": {"content": json.dumps({"verdicts": verdicts})}}]})
                data = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        cls.Handler = Handler
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.Handler.seen.clear()
        self.ctx = QueryContext(budget_per_person=1000.0, party_size=2, time="20:00")
        self.judge = DeepSeekJudge(
            api_key="test-key", model="deepseek-chat", cache=False, base_url=f"http://127.0.0.1:{self.port}"
        )

    def test_full_request_response_cycle(self):
        offers = [
            Offer(title="Flat ₹200 off", offer_type="flat", value=200.0, per_person_ref=600.0),
            Offer(title="Free dessert", offer_type="perk"),
        ]
        self.judge.score(offers, self.ctx)

        self.assertEqual([o.score for o in offers], [88.0, 88.0])
        self.assertEqual([o.judged_by for o in offers], ["llm", "llm"])
        self.assertEqual(offers[0].flags, ["loopback"])

        seen = self.Handler.seen[0]
        self.assertEqual(seen["path"], "/chat/completions")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(seen["offers"], 2)

    def test_verdict_without_numeric_score_is_rejected(self):
        payload = {"choices": [{"message": {"content": json.dumps({"verdicts": [{"index": 0, "effective_pct": 10}]})}}]}

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(payload)

        with mock.patch("orange.services.judge.llm.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(JudgeUnavailable) as caught:
                self.judge.score([Offer(title="10% off")], self.ctx)
        self.assertIn("numeric score", str(caught.exception))


class CacheKeyTests(unittest.TestCase):
    def test_prompt_change_invalidates_cache(self):
        from orange.services.judge import llm as llm_module

        ctx = QueryContext()
        offers = [Offer(title="10% off", offer_type="percent", value=10.0)]
        judge = DeepSeekJudge(api_key="k", cache=False, base_url="https://api.example.test")
        before = judge._cache_key(offers, ctx)
        with mock.patch.object(llm_module, "SYSTEM_PROMPT", llm_module.SYSTEM_PROMPT + "\nNew rule."):
            after = judge._cache_key(offers, ctx)
        self.assertNotEqual(before, after)

    def test_cache_round_trip(self):
        import tempfile
        from pathlib import Path

        from orange.services.judge import llm as llm_module

        ctx = QueryContext()
        offers = [Offer(title="10% off", offer_type="percent", value=10.0)]
        judge = DeepSeekJudge(api_key="k", base_url="https://api.example.test")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(llm_module, "CACHE_DIR", Path(tmp)):
                key = judge._cache_key(offers, ctx)
                judge._write_cache(key, [{"score": 33}])
                self.assertEqual(judge._read_cache(key), [{"score": 33}])
        self.assertIsNone(judge._read_cache(key))
