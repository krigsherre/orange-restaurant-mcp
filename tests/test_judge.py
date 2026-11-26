import json
import os
import unittest
from unittest import mock

import _path  # noqa: F401
from orange.judge import (
    JudgeUnavailable,
    build_judge,
    llm_demanded,
    normalize_llm,
    resolve_judge_mode,
)
from orange.judge.llm import DeepSeekJudge
from orange.judge.rubric import RubricJudge, score_offer
from orange.models import Offer, QueryContext


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

    def test_flat_offer_scaled_by_budget(self):
        small = Offer(title="₹500 off", offer_type="flat", value=500.0)
        score, effective, _, _ = score_offer(small, self.ctx)
        self.assertAlmostEqual(effective, 50.0)
        self.assertEqual(score, 50.0)

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
        with mock.patch("orange.judge.llm.urllib.request.urlopen", fake_urlopen):
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

    def test_malformed_response_becomes_judge_unavailable(self):
        payload = {"choices": [{"message": {"content": "not json"}}]}

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(payload)

        with mock.patch("orange.judge.llm.urllib.request.urlopen", fake_urlopen):
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
