"""Swiggy Dineout adapter (endpoint: POST mcp.swiggy.com/dineout).

Tool contract implemented:
  search_restaurants_dineout  — discovery
  get_available_slots         — availability
  book_table                  — reservation (not invoked by the ranker)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .mcp import McpHttpClient, extract_envelope
from ..domain.models import Offer, QueryContext, Restaurant, Slot
from ..domain.normalize import attach_offer_context, normalize_distance, parse_cost_for_two, parse_distance, parse_offer, parse_rating
from .base import load_json

# Broad seed queries: Swiggy's search resolves one term at a time, so we fan out
# and de-duplicate by restaurant id. Cheap, read-only, and beats a single query.
SEED_QUERIES = (
    "Koramangala",
    "cafe",
    "pub",
    "bar",
    "biryani",
    "North Indian",
    "Continental",
    "Italian",
    "Chinese",
    "South Indian",
)


def _slots_from_payload(payload: dict[str, Any]) -> list[Slot]:
    data = extract_envelope(payload)
    slots: list[Slot] = []
    for raw in data.get("slots", []) or []:
        deals = raw.get("deals") or [{}]
        deal = deals[0]
        slots.append(
            Slot(
                date=str(raw.get("dateStr") or data.get("date") or ""),
                display_time=str(raw.get("displayTime") or ""),
                group=str(raw.get("slotGroupName") or ""),
                is_free=bool(deal.get("isFree", raw.get("isFree", True))),
                booking_price=float(deal.get("bookingPrice") or 0.0),
                inventory=int(raw.get("availableInventory") or deal.get("availableInventory") or 0),
                slot_id=str(deal.get("slotId") or raw.get("slotId") or ""),
                item_id=str(deal.get("itemId") or raw.get("itemId") or ""),
                reservation_time=int(raw.get("reservationTime") or deal.get("reservationTime") or 0),
            )
        )
    return slots


def from_dineout_payload(payload: dict[str, Any], ctx: QueryContext) -> list[Restaurant]:
    """Map a `search_restaurants_dineout` response into domain records."""
    data = extract_envelope(payload)
    restaurants: list[Restaurant] = []
    for raw in data.get("restaurants", []) or []:
        offers: list[Offer] = []
        for rank, item in enumerate(raw.get("offers") or []):
            offers.append(
                parse_offer(
                    item.get("offerTitle", ""),
                    item.get("offerDescription", ""),
                    source="offers",
                    provider_rank=rank,
                )
            )
        for rank, item in enumerate(raw.get("bankOffers") or []):
            offers.append(
                parse_offer(item.get("offerTitle", ""), item.get("offerDescription", ""), source="bankOffers", provider_rank=rank)
            )
        for rank, item in enumerate(raw.get("availableDeals") or []):
            offers.append(
                parse_offer(item.get("dealTitle", ""), item.get("dealDescription", ""), source="deals", provider_rank=rank)
            )
        rating = raw.get("rating")
        record = Restaurant(
            provider="swiggy_dineout",
            provider_id=str(raw.get("id", "")),
            name=str(raw.get("name", "")).strip(),
            cuisines=[str(c) for c in raw.get("cuisine") or []],
            locality=str(raw.get("locality", "")),
            area=str(raw.get("area", "")),
            distance_km=parse_distance(raw.get("distance")),
            distance_source="provider",
            cost_for_two=parse_cost_for_two(raw.get("costForTwo")),
            rating=parse_rating(rating),
            rating_count=int((rating or {}).get("count", 0)) if isinstance(rating, dict) else 0,
            offers=offers,
            raw=raw,
        )
        normalize_distance(record, ctx.lat, ctx.lng)
        attach_offer_context(record)
        restaurants.append(record)
    return restaurants


class SwiggyDineoutRepository:
    name = "swiggy_dineout"
    server = "https://mcp.swiggy.com/dineout"

    def __init__(
        self,
        ctx: QueryContext,
        *,
        client: McpHttpClient | None = None,
        fixture: str | Path | None = None,
        limit: int = 30,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.fixture = Path(fixture) if fixture else None
        self.limit = limit

    @property
    def mode(self) -> str:
        return "live" if self.client else "fixture"

    def search(self) -> list[Restaurant]:
        if self.fixture:
            return from_dineout_payload(load_json(self.fixture), self.ctx)
        if not self.client:  # pragma: no cover - guarded by factory
            raise RuntimeError("SwiggyDineoutProvider needs a client or a fixture")

        seen: dict[str, Restaurant] = {}
        for query in SEED_QUERIES:
            offset = 0
            while True:
                payload = self.client.call_tool(
                    "search_restaurants_dineout",
                    {
                        "query": query,
                        "latitude": self.ctx.lat,
                        "longitude": self.ctx.lng,
                        "limit": self.limit,
                        "offset": offset,
                    },
                )
                found = from_dineout_payload(payload, self.ctx)
                for restaurant in found:
                    seen.setdefault(restaurant.provider_id, restaurant)
                data = extract_envelope(payload)
                next_offset = data.get("nextOffset")
                if next_offset is None or not found:
                    break
                offset = int(next_offset)
        return list(seen.values())

    def slots_for(self, restaurant: Restaurant, date: str) -> list[Slot]:
        slots = self._slots(restaurant, date)
        for slot in slots:
            slot.duration_min = self.ctx.duration_min
        return slots

    def _slots(self, restaurant: Restaurant, date: str) -> list[Slot]:
        if self.fixture:
            payload = load_json(self.fixture)
            table = payload.get("slots") or {}
            return _slots_from_payload({"data": {"slots": table.get(restaurant.provider_id, [])}})
        if not self.client:  # pragma: no cover
            return []
        payload = self.client.call_tool(
            "get_available_slots",
            {
                "restaurantId": restaurant.provider_id,
                "date": date,
                "latitude": self.ctx.lat,
                "longitude": self.ctx.lng,
            },
        )
        return _slots_from_payload(payload)

    # ------------------------------------------------------------------ booking
    def book(self, restaurant: Restaurant, slot: Slot, ctx: QueryContext) -> "BookingOutcome":
        from ..domain.models import BookingOutcome

        if not self.client:  # pragma: no cover - guarded by book_restaurant
            raise RuntimeError("live booking requires an MCP client")

        if not slot.is_free:
            # Paid prebook: create the deal cart and stop before payment. Charging a card
            # without an explicit user payment step would be wrong.
            cart = extract_envelope(
                self.client.call_tool(
                    "create_cart",
                    {
                        "restaurantId": restaurant.provider_id,
                        "cartType": "DEAL_TICKET_PURCHASE",
                        "slotId": int(slot.slot_id or 0),
                        "itemId": slot.item_id,
                        "guestCount": ctx.party_size,
                    },
                )
            )
            return BookingOutcome(
                status="pending-payment",
                provider=self.name,
                restaurant=restaurant.name,
                provider_id=restaurant.provider_id,
                date=ctx.date,
                start_time=slot.display_time,
                end_time=slot.end_label,
                party=ctx.party_size,
                slot_id=str(slot.slot_id),
                message=f"Paid prebook created (cart {cart.get('cartKey', '?')}); user must approve UPI payment.",
                raw=cart,
            )

        payload = self.client.call_tool(
            "book_table",
            {
                "restaurantId": restaurant.provider_id,
                "slotId": int(slot.slot_id or 0),
                "itemId": slot.item_id,
                "reservationTime": slot.reservation_time,
                "guestCount": ctx.party_size,
                "latitude": ctx.lat,
                "longitude": ctx.lng,
                "paymentMethod": "Cash",
            },
        )
        data = extract_envelope(payload)
        order_id = str(data.get("orderId", ""))
        status = str(data.get("status", ""))
        if status.upper() == "PENDING_PAYMENT":  # pragma: no cover - live path
            return BookingOutcome(
                status="pending-payment",
                provider=self.name,
                restaurant=restaurant.name,
                provider_id=restaurant.provider_id,
                date=ctx.date,
                start_time=slot.display_time,
                end_time=slot.end_label,
                party=ctx.party_size,
                order_id=order_id,
                message="Swiggy returned PENDING_PAYMENT; complete UPI then confirm.",
                raw=data,
            )
        return BookingOutcome(
            status="confirmed",
            provider=self.name,
            restaurant=restaurant.name,
            provider_id=restaurant.provider_id,
            date=ctx.date,
            start_time=slot.display_time,
            end_time=slot.end_label,
            party=ctx.party_size,
            slot_id=str(slot.slot_id),
            order_id=order_id,
            message=str(data.get("status") or "Reservation confirmed."),
            raw=data,
        )


# Historical alias: the object is a repository, the old name said provider.
SwiggyDineoutProvider = SwiggyDineoutRepository
