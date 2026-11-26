"""District by Zomato adapter.

District is Zomato's dine-out/events product and the live successor to Zomato's
consumer API (the old developer API was retired in 2021). Its MCP exposes 12 tools;
we use the dining pair:

  get_dynamic_filters        — required prerequisite (valid filter ids)
  search_dining_restaurants  — discovery with offers

Availability/booking is delegated to the returned ``booking_url``; District exposes no
public slot-booking tool, so availability is reported as "via booking link".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .mcp import McpHttpClient, extract_envelope
from ..domain.models import QueryContext, Restaurant, Slot
from ..domain.normalize import attach_offer_context, normalize_distance, parse_cost_for_two, parse_distance, parse_offer, parse_rating
from .base import load_json

SEED_QUERIES = ("Koramangala", "dining", "cafe", "bar")


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = extract_envelope(payload)
    for key in ("restaurants", "results", "dineout_restaurants"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def from_district_payload(payload: dict[str, Any], ctx: QueryContext) -> list[Restaurant]:
    restaurants: list[Restaurant] = []
    for raw in _records(payload):
        offers = []
        for rank, item in enumerate(raw.get("offers") or []):
            if isinstance(item, str):
                offers.append(parse_offer(item, "", provider_rank=rank))
            else:
                offers.append(
                    parse_offer(
                        item.get("title") or item.get("offer_title", ""),
                        item.get("terms") or item.get("description", ""),
                        provider_rank=rank,
                    )
                )
        location = raw.get("location") or {}
        lat = raw.get("latitude", location.get("lat"))
        lng = raw.get("longitude", location.get("lng"))
        record = Restaurant(
            provider="district",
            provider_id=str(raw.get("restaurant_id") or raw.get("res_id") or raw.get("id", "")),
            name=str(raw.get("name", "")).strip(),
            cuisines=[str(c) for c in raw.get("cuisines") or raw.get("cuisine") or []],
            locality=str(raw.get("locality", "")),
            area=str(raw.get("area", "Koramangala")),
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            distance_km=parse_distance(raw.get("distance_km", raw.get("distance"))),
            cost_for_two=parse_cost_for_two(raw.get("cost_for_two", raw.get("costForTwo"))),
            rating=parse_rating(raw.get("rating")),
            rating_count=int(raw.get("rating_count") or raw.get("total_ratings") or 0),
            offers=offers,
            booking_url=str(raw.get("booking_url", "")),
            raw=raw,
        )
        normalize_distance(record, ctx.lat, ctx.lng)
        attach_offer_context(record)
        restaurants.append(record)
    return restaurants


class DistrictRepository:
    name = "district"
    server = "https://mcp.district.in/dining"  # placeholder: District publishes no stable public endpoint
    # No reservation API: booking hands the user the restaurant's District link.
    books_via_link = True

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
            return from_district_payload(load_json(self.fixture), self.ctx)
        if not self.client:  # pragma: no cover
            raise RuntimeError("DistrictProvider needs a client or a fixture")

        # District requires filter ids before searching.
        filters = extract_envelope(self.client.call_tool("get_dynamic_filters", {"latitude": self.ctx.lat, "longitude": self.ctx.lng}))
        payload = self.client.call_tool(
            "search_dining_restaurants",
            {
                "query": "restaurants",
                "latitude": self.ctx.lat,
                "longitude": self.ctx.lng,
                "limit": self.limit,
                "filters": filters.get("available_filters", []),
            },
        )
        return from_district_payload(payload, self.ctx)

    def slots_for(self, restaurant: Restaurant, date: str) -> list[Slot]:
        return []  # availability is only exposed through booking_url

    def book(self, restaurant: Restaurant, slot: "Slot", ctx: QueryContext) -> "BookingOutcome":
        from ..domain.models import BookingOutcome

        return BookingOutcome(
            status="requires-user-action",
            provider=self.name,
            restaurant=restaurant.name,
            provider_id=restaurant.provider_id,
            date=ctx.date,
            start_time=slot.display_time if slot else "",
            end_time=slot.end_label if slot else "",
            party=ctx.party_size,
            booking_url=restaurant.booking_url,
            message="District exposes no booking API; finish the reservation at booking_url.",
        )


# Historical alias: the object is a repository, the old name said provider.
DistrictProvider = DistrictRepository
