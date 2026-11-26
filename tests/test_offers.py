import unittest

import _path  # noqa: F401
from orange.normalize import extract_conditions, parse_cost_for_two, parse_distance, parse_offer


class OfferParsingTests(unittest.TestCase):
    def test_percent(self):
        offer = parse_offer("Flat 20% off on total bill")
        self.assertEqual(offer.offer_type, "percent")
        self.assertEqual(offer.value, 20.0)

    def test_flat_rupees(self):
        offer = parse_offer("Flat ₹200 off on bills above ₹1,200")
        self.assertEqual(offer.offer_type, "flat")
        self.assertEqual(offer.value, 200.0)
        self.assertEqual(offer.min_spend, 1200.0)

    def test_perk(self):
        offer = parse_offer("Complimentary dessert platter")
        self.assertEqual(offer.offer_type, "perk")

    def test_bogo_is_perk(self):
        offer = parse_offer("Buy 1 Get 1 on all coffees")
        self.assertEqual(offer.offer_type, "perk")

    def test_up_to_flag(self):
        offer = parse_offer("Up to 30% off on total bill", "On bills above ₹2,500")
        self.assertEqual(offer.offer_type, "percent")
        self.assertIn("up-to", offer.conditions)
        self.assertEqual(offer.min_spend, 2500.0)

    def test_weekend_exclusion(self):
        self.assertIn("weekend-excluded", extract_conditions("Not valid on weekends."))

    def test_weekday_window(self):
        self.assertIn("weekday-only", extract_conditions("Mon-Thu, 8 AM to 6 PM"))

    def test_lunch_only(self):
        self.assertIn("lunch-only", extract_conditions("Lunch only as per restaurant policy."))

    def test_card_required(self):
        self.assertIn("card-required", extract_conditions("Valid with HDFC Bank credit card only."))

    def test_unparsed(self):
        self.assertEqual(parse_offer("Loyalty points bonus").offer_type, "unknown")


class CostAndDistanceTests(unittest.TestCase):
    def test_cost_for_two(self):
        self.assertEqual(parse_cost_for_two("₹1,200 for two"), 1200.0)
        self.assertEqual(parse_cost_for_two("2000"), 2000.0)

    def test_distance_units(self):
        self.assertEqual(parse_distance("2.4 km"), 2.4)
        self.assertAlmostEqual(parse_distance("850 m"), 0.85)
        self.assertEqual(parse_distance(3.1), 3.1)
        self.assertIsNone(parse_distance(None))


if __name__ == "__main__":
    unittest.main()
