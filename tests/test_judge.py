import unittest

import _path  # noqa: F401
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


if __name__ == "__main__":
    unittest.main()
