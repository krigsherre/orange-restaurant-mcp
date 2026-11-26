import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from orange.app.reporting import write_booking
from orange.services.booking import SelectionError, book_restaurant, select_row
from orange.domain.models import QueryContext, Restaurant, Slot
from orange.services.ranking import pick_slot
from orange.services.search import search


def row(rank, name, provider="swiggy_dineout", status="available", slot=None):
    return {
        "rank": rank,
        "name": name,
        "provider": provider,
        "provider_id": f"id_{rank}",
        "availability": {"status": status, "detail": "detail", "slot": slot} if slot else {"status": status, "detail": "detail"},
    }


def slot_at(time="8:00 PM", free=True, price=0.0):
    return {
        "date": "2026-11-27",
        "display_time": time,
        "window": f"{time}–9:30 PM",
        "is_free": free,
        "booking_price": price,
        "inventory": 4,
        "slot_id": "4001",
        "item_id": "id-ticket",
        "reservation_time": 1795789800,
    }


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row(1, "Beijing Bites", status="no-slot-in-window"),
            row(2, "Meghana Foods", "district", status="via-booking-link"),
            row(3, "Meghana Foods", slot=slot_at()),
            row(4, "Toit", slot=slot_at("9:00 PM")),
        ]

    def test_auto_skips_unbookable_and_takes_top(self):
        self.assertEqual(select_row(self.rows, auto=True)["rank"], 3)

    def test_auto_with_nothing_available_errors(self):
        with self.assertRaises(SelectionError):
            select_row([row(1, "A", status="no-slot-in-window")], auto=True)

    def test_rank_selection(self):
        self.assertEqual(select_row(self.rows, rank=4)["name"], "Toit")

    def test_rank_of_unbookable_row_explains_alternatives(self):
        with self.assertRaises(SelectionError) as ctx:
            select_row(self.rows, rank=1)
        self.assertIn("Bookable options", str(ctx.exception))
        self.assertIn("Meghana Foods", str(ctx.exception))

    def test_rank_out_of_range(self):
        with self.assertRaises(SelectionError):
            select_row(self.rows, rank=99)

    def test_name_ambiguity_lists_options(self):
        with self.assertRaises(SelectionError) as ctx:
            select_row(self.rows, name="Meghana Foods")
        self.assertIn("ambiguous", str(ctx.exception))

    def test_name_with_provider_disambiguates(self):
        picked = select_row(self.rows, name="Meghana Foods", provider="swiggy_dineout")
        self.assertEqual((picked["rank"], picked["provider"]), (3, "swiggy_dineout"))

    def test_district_link_only_row_explains_itself(self):
        with self.assertRaises(SelectionError) as ctx:
            select_row(self.rows, name="Meghana Foods", provider="district")
        self.assertIn("not bookable", str(ctx.exception))

    def test_name_not_bookable_is_explicit(self):
        with self.assertRaises(SelectionError) as ctx:
            select_row(self.rows, name="Beijing Bites")
        self.assertIn("not bookable", str(ctx.exception))

    def test_no_selection_errors(self):
        with self.assertRaises(SelectionError):
            select_row(self.rows)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.ctx = QueryContext(time="20:00", time_end="22:00", party_size=2)
        self.restaurant = Restaurant(
            provider="swiggy_dineout",
            provider_id="sw_meghana",
            name="Meghana Foods",
            distance_km=0.9,
            cost_for_two=1400,
            booking_url="",
        )
        self.slot = Slot(
            date="2026-11-27",
            display_time="8:00 PM",
            is_free=True,
            inventory=4,
            slot_id="4001",
            item_id="sw_meghana-ticket_5001",
            reservation_time=1795789800,
        )

    def test_fixture_mode_simulates(self):
        provider = type("P", (), {"mode": "fixture", "name": "swiggy_dineout"})()
        outcome = book_restaurant(provider, self.restaurant, self.slot, self.ctx)
        self.assertEqual(outcome.status, "simulation")
        self.assertTrue(outcome.simulated)
        self.assertTrue(outcome.booked)
        self.assertTrue(outcome.order_id.startswith("SIM-"))
        self.assertEqual(outcome.end_time, "9:30 PM")

    def test_simulation_is_deterministic(self):
        provider = type("P", (), {"mode": "fixture", "name": "swiggy_dineout"})()
        first = book_restaurant(provider, self.restaurant, self.slot, self.ctx).order_id
        second = book_restaurant(provider, self.restaurant, self.slot, self.ctx).order_id
        self.assertEqual(first, second)

    def test_live_without_confirm_does_not_call_provider(self):
        class Provider:
            mode = "live"
            name = "swiggy_dineout"

            def __init__(self):
                self.calls = 0

            def book(self, restaurant, slot, ctx):
                self.calls += 1

        provider = Provider()
        outcome = book_restaurant(provider, self.restaurant, self.slot, self.ctx, confirm=False)
        self.assertEqual(outcome.status, "confirmation-required")
        self.assertEqual(provider.calls, 0)
        self.assertFalse(outcome.booked)

    def test_live_with_confirm_delegates(self):
        from orange.domain.models import BookingOutcome

        class Provider:
            mode = "live"
            name = "swiggy_dineout"

            def __init__(self):
                self.calls = 0

            def book(self, restaurant, slot, ctx):
                self.calls += 1
                return BookingOutcome(
                    status="confirmed",
                    provider="swiggy_dineout",
                    restaurant=restaurant.name,
                    provider_id=restaurant.provider_id,
                    date=ctx.date,
                    start_time=slot.display_time,
                    end_time=slot.end_label,
                    party=ctx.party_size,
                    order_id="ORD-1",
                )

        provider = Provider()
        outcome = book_restaurant(provider, self.restaurant, self.slot, self.ctx, confirm=True)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(outcome.status, "confirmed")
        self.assertEqual(outcome.order_id, "ORD-1")

    def test_provider_exception_becomes_failed_status(self):
        class Provider:
            mode = "live"
            name = "swiggy_dineout"

            def book(self, *args):
                raise RuntimeError("slot gone")

        outcome = book_restaurant(Provider(), self.restaurant, self.slot, self.ctx, confirm=True)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("slot gone", outcome.message)

    def test_missing_slot_is_unavailable(self):
        provider = type("P", (), {"mode": "fixture", "name": "swiggy_dineout"})()
        outcome = book_restaurant(provider, self.restaurant, None, self.ctx)
        self.assertEqual(outcome.status, "unavailable")

    def test_write_booking_outputs(self):
        provider = type("P", (), {"mode": "fixture", "name": "swiggy_dineout"})()
        outcome = book_restaurant(provider, self.restaurant, self.slot, self.ctx)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = write_booking(tmp, outcome)
            self.assertTrue(json_path.exists())
            self.assertIn("Booking", md_path.read_text())


class WindowTests(unittest.TestCase):
    def test_slot_before_window_excluded(self):
        ctx = QueryContext(time="20:00", time_end="22:00")
        slots = [Slot(date="2026-11-27", display_time="6:30 PM")]
        self.assertIsNone(pick_slot(slots, ctx))

    def test_slot_after_window_excluded(self):
        ctx = QueryContext(time="20:00", time_end="22:00")
        slots = [Slot(date="2026-11-27", display_time="10:30 PM")]
        self.assertIsNone(pick_slot(slots, ctx))

    def test_boundary_slots_included(self):
        ctx = QueryContext(time="20:00", time_end="22:00")
        self.assertIsNotNone(pick_slot([Slot(date="2026-11-27", display_time="8:00 PM")], ctx))
        self.assertIsNotNone(pick_slot([Slot(date="2026-11-27", display_time="10:00 PM")], ctx))

    def test_search_rejects_unavailable_with_reason(self):
        ctx = QueryContext(time="20:00", time_end="22:00", require_availability=True)
        with_slot = Restaurant(provider="p", provider_id="1", name="Has", distance_km=1, cost_for_two=800)
        with_slot.slots = [Slot(date="2026-11-27", display_time="8:00 PM")]
        without = Restaurant(provider="p", provider_id="2", name="Hasnt", distance_km=1, cost_for_two=800)
        result = search([with_slot, without], ctx)
        kept, rejected = result.kept, result.rejected
        self.assertEqual([r.name for r in kept], ["Has"])
        self.assertEqual(rejected[0]["reason"], "no-availability")

    def test_slot_end_uses_duration(self):
        ctx = QueryContext(time="20:00", time_end="22:00", duration_min=120)
        slot = Slot(date="2026-11-27", display_time="8:00 PM", duration_min=120)
        self.assertEqual(slot.window_label, "8:00 PM–10:00 PM")


if __name__ == "__main__":
    unittest.main()


class SwiggyBookTableTests(unittest.TestCase):
    """Verify the exact Swiggy Dineout booking calls without touching the network."""

    class FakeClient:
        def __init__(self, responses):
            self.responses = responses
            self.calls = []

        def call_tool(self, name, arguments=None):
            self.calls.append((name, arguments or {}))
            return self.responses[name]

    def setUp(self):
        from orange.repositories.swiggy import SwiggyDineoutProvider

        self.ctx = QueryContext(time="20:00", time_end="22:00", party_size=2)
        self.restaurant = Restaurant(provider="swiggy_dineout", provider_id="sw_1", name="Toit", distance_km=1.0, cost_for_two=2000)
        self.free_slot = Slot(
            date="2026-11-27", display_time="8:00 PM", is_free=True, slot_id="4001",
            item_id="sw_1-ticket_1", reservation_time=1795789800, inventory=2,
        )
        self.provider_cls = SwiggyDineoutProvider

    def test_free_slot_books_with_cash_and_exact_ids(self):
        client = self.FakeClient({"book_table": {"success": True, "data": {"orderId": "ORD-9", "status": "CONFIRMED"}}})
        provider = self.provider_cls(self.ctx, client=client)
        outcome = provider.book(self.restaurant, self.free_slot, self.ctx)
        self.assertEqual(outcome.status, "confirmed")
        self.assertEqual(outcome.order_id, "ORD-9")
        name, args = client.calls[0]
        self.assertEqual(name, "book_table")
        self.assertEqual(args["restaurantId"], "sw_1")
        self.assertEqual(args["slotId"], 4001)
        self.assertEqual(args["itemId"], "sw_1-ticket_1")
        self.assertEqual(args["guestCount"], 2)
        self.assertEqual(args["paymentMethod"], "Cash")

    def test_paid_deal_creates_cart_and_stops_before_payment(self):
        client = self.FakeClient({"create_cart": {"success": True, "data": {"cartKey": "CART-7"}}})
        slot = Slot(
            date="2026-11-27", display_time="8:00 PM", is_free=False, booking_price=500,
            slot_id="4002", item_id="sw_1-ticket_2", reservation_time=1795789800, inventory=2,
        )
        provider = self.provider_cls(self.ctx, client=client)
        outcome = provider.book(self.restaurant, slot, self.ctx)
        self.assertEqual(outcome.status, "pending-payment")
        self.assertIn("CART-7", outcome.message)
        self.assertEqual([call[0] for call in client.calls], ["create_cart"])
        self.assertEqual(client.calls[0][1]["cartType"], "DEAL_TICKET_PURCHASE")

    def test_pending_payment_response_is_surfaced(self):
        client = self.FakeClient({"book_table": {"success": True, "data": {"orderId": "ORD-P", "status": "PENDING_PAYMENT"}}})
        provider = self.provider_cls(self.ctx, client=client)
        outcome = provider.book(self.restaurant, self.free_slot, self.ctx)
        self.assertEqual(outcome.status, "pending-payment")


class DistrictBookingTests(unittest.TestCase):
    def test_district_hands_back_the_link(self):
        from orange.repositories.district import DistrictProvider

        ctx = QueryContext(time="20:00", time_end="22:00")
        provider = DistrictProvider(ctx)
        restaurant = Restaurant(
            provider="district", provider_id="dt_toit", name="Toit",
            booking_url="https://www.district.in/bengaluru/toit-koramangala",
        )
        outcome = provider.book(restaurant, Slot(date="2026-11-27", display_time="8:00 PM"), ctx)
        self.assertEqual(outcome.status, "requires-user-action")
        self.assertTrue(outcome.booking_url.startswith("https://www.district.in/"))
        self.assertFalse(outcome.simulated)
