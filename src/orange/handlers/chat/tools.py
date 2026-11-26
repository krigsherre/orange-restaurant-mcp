"""Tool schemas and implementations exposed to the chat model."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ...services.booking import book_restaurant
from ...services.judge import OfferJudge
from ...domain.models import QueryContext, Restaurant
from ...services.ranking import judge_all, pick_slot, rank_rows
from ...services.search import search
from .session import ChatSession

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": (
                "Find restaurants in Koramangala that fit the user's filters and return a ranked shortlist. "
                "Call this for any request to see/list/find/compare restaurants. Only pass filters the user "
                "just stated or changed; omitted filters keep their saved values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cuisine": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more cuisines, OR semantics (e.g. ['Cafe'] or ['North Indian','Chinese']).",
                    },
                    "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today."},
                    "time_start": {"type": "string", "description": "Window start, 24h HH:MM (e.g. '19:30')."},
                    "time_end": {"type": "string", "description": "Window end, 24h HH:MM (e.g. '22:00')."},
                    "budget": {"type": "number", "description": "Max rupees per person."},
                    "radius": {"type": "number", "description": "Max distance in km from Koramangala centre."},
                    "party": {"type": "integer", "description": "Number of guests."},
                    "require_available": {
                        "type": "boolean",
                        "description": "Only keep restaurants with a bookable slot inside the window.",
                    },
                    "spend": {
                        "type": "number",
                        "description": "Optional expected spend per person used to value offers.",
                    },
                    "limit": {"type": "integer", "description": "How many rows to return (1-15, default 8)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restaurant_details",
            "description": "Full details for one restaurant from the last search: all offers with reasoning, slots, booking link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer", "description": "Rank from the last search results."},
                    "name": {"type": "string", "description": "Restaurant name, if the rank is unknown."},
                    "provider": {"type": "string", "description": "Optional provider to disambiguate a name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_table",
            "description": (
                "Book one restaurant from the last search. Call WITHOUT confirm first to produce a "
                "confirmation card; call with confirm=true only after the user explicitly agreed to that "
                "restaurant and slot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer", "description": "Rank from the last search results."},
                    "name": {"type": "string", "description": "Restaurant name, if the rank is unknown."},
                    "provider": {"type": "string", "description": "Optional provider to disambiguate a name."},
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true only after the user said yes to this exact restaurant and slot.",
                    },
                },
                "required": ["confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_filters",
            "description": "Clear the conversation's search filters back to defaults. Use when the user starts over.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

ANY_CUISINE = {
    "any", "all", "anything", "whatever", "no preference", "no-preference",
    "doesn't matter", "doesnt matter", "don't care", "dont care", "surprise me",
}


def _clean_cuisines(value: Any) -> list[str]:
    """'any cuisine' means no filter, not a cuisine literally named any."""
    items = value if isinstance(value, list) else [str(value)]
    cleaned: list[str] = []
    for item in items:
        for part in str(item).split(","):
            term = part.strip()
            if term and term.casefold() not in ANY_CUISINE:
                cleaned.append(term)
    return cleaned


_BOOKING_LABEL = {
    "via-booking-link": "District link",
    "no-slot-in-window": "no slot",
    "unknown": "no data",
}


class ToolError(ValueError):
    """A tool was called with arguments it cannot honour; returned to the model as an error."""


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    best = row.get("best_offer") or {}
    slot = row.get("availability", {}).get("slot") or {}
    status = row.get("availability", {}).get("status", "unknown")
    if status == "available":
        booking = "free" if slot.get("is_free") else f"₹{slot.get('booking_price', 0):,.0f} prebook"
    else:
        booking = _BOOKING_LABEL.get(status, status)
    return {
        "rank": row["rank"],
        "name": row["name"],
        "provider": row["provider"],
        "cuisines": row["cuisines"][:3],
        "offer": best.get("title"),
        "cash_pct": row["cash_pct"],
        "score": row["offer_score"],
        "per_person": row["per_person"],
        "distance_km": row["distance_km"],
        "slot": slot.get("window"),
        "booking": booking,
    }


class ChatTools:
    def __init__(
        self,
        session: ChatSession,
        repositories: list[Any],
        *,
        judge: OfferJudge,
        live_booking: bool = False,
    ) -> None:
        self.session = session
        self.judge = judge
        self.live_booking = live_booking
        self.repositories = repositories
        self.repositories_by_name = {repository.name: repository for repository in repositories}

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        handlers = {
            "search_restaurants": self.search,
            "restaurant_details": self.details,
            "book_table": self.book,
            "reset_filters": self.reset,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}; available: {', '.join(handlers)}"}
        try:
            return handler(arguments or {})
        except ToolError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the model gets the error and can adapt
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------ tools
    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        for key in ("date", "time_start", "time_end"):
            value = args.get(key)
            if value and not (_DATE if key == "date" else _TIME).match(str(value)):
                raise ToolError(f"invalid {key}: {value!r}")
        # The product promise is that the agent asks before it assumes. Enforce it
        # once per conversation: an unfiltered first search becomes a question.
        if not self.session.clarified:
            provided = any(
                args.get(key) is not None
                for key in ("cuisine", "time_start", "time_end", "budget", "party", "radius", "date")
            )
            if not provided:
                self.session.clarified = True
                return {
                    "status": "needs_clarification",
                    "ask": "Which cuisine (or any), what time window, and budget per person? Party size too, if not 2.",
                    "filters": self.session.summary(),
                }
            self.session.clarified = True

        limit = max(1, min(15, int(args.get("limit") or 8)))
        cuisine_arg = args.get("cuisine")
        if cuisine_arg is not None:
            cleaned = _clean_cuisines(cuisine_arg)
            if cleaned:
                self.session.merge(cuisine=cleaned)
            else:  # "any" explicitly clears a previous cuisine filter
                self.session.filters["cuisines"] = []
        self.session.merge(
            date=args.get("date"),
            time_start=args.get("time_start"),
            time_end=args.get("time_end"),
            budget=args.get("budget"),
            radius=args.get("radius"),
            party=args.get("party"),
            require_availability=args.get("require_available"),
            spend=args.get("spend"),
        )
        ctx = self.session.context()
        if self.session.discovered is None:
            found: list[Restaurant] = []
            for repository in self.repositories:
                found.extend(repository.search())
            self.session.discovered = found

        outcome = search(
            self.session.discovered,
            ctx,
            slot_loader=lambda restaurant: self.repositories_by_name[restaurant.provider].slots_for(restaurant, ctx.date),
        )
        judge_all(outcome.kept, ctx, self.judge)
        rows = rank_rows(outcome.kept, ctx)
        self.session.rows = rows
        self.session.kept = outcome.kept
        result: dict[str, Any] = {
            "filters": self.session.summary(),
            "matched": len(rows),
            "bookable": sum(1 for row in rows if row["availability"]["status"] == "available"),
            "rejected": dict(Counter(item["reason"] for item in outcome.rejected)),
            "results": [_compact(row) for row in rows[:limit]],
            "more_available": len(rows) > limit,
        }
        if not rows:
            result["hint"] = (
                "No matches. Tell the user which filter is tightest and offer to relax one "
                "(availability, budget, radius or cuisine)."
            )
        return result

    def details(self, args: dict[str, Any]) -> dict[str, Any]:
        row = self._resolve(args)
        restaurant = self._restaurant(row)
        ctx = self.session.context()
        return {
            "rank": row["rank"],
            "name": row["name"],
            "provider": row["provider"],
            "cuisines": row["cuisines"],
            "locality": row["locality"],
            "rating": row["rating"],
            "cash_pct": row["cash_pct"],
            "score": row["offer_score"],
            "per_person": row["per_person"],
            "distance_km": row["distance_km"],
            "availability": row["availability"].get("detail"),
            "booking_url": row.get("booking_url") or None,
            "offers": [
                {
                    "title": offer.title,
                    "type": offer.offer_type,
                    "value": offer.value,
                    "cash_pct": offer.effective_pct,
                    "score": offer.score,
                    "flags": offer.flags,
                    "rationale": offer.rationale,
                }
                for offer in sorted(restaurant.offers, key=lambda o: -o.effective_pct)
            ],
            "slots": [
                {
                    "window": slot.window_label,
                    "free": slot.is_free,
                    "price": slot.booking_price,
                    "inventory": slot.inventory,
                }
                for slot in restaurant.slots
                if ctx.slot_in_window(slot)
            ],
        }

    def book(self, args: dict[str, Any]) -> dict[str, Any]:
        row = self._resolve(args)
        status = row["availability"]["status"]
        if status == "via-booking-link":
            return {
                "status": "requires-user-action",
                "restaurant": row["name"],
                "booking_url": row.get("booking_url"),
                "message": "District exposes no booking API. Give the user this link to finish the reservation.",
            }
        if status != "available":
            return {
                "status": "unavailable",
                "restaurant": row["name"],
                "message": row["availability"].get("detail") or "No bookable slot in the window.",
            }
        slot = row["availability"].get("slot") or {}
        if not args.get("confirm"):
            return {
                "status": "confirmation-required",
                "restaurant": row["name"],
                "provider": row["provider"],
                "slot": slot.get("window"),
                "party": self.session.filters["party"],
                "instructions": "Show this card and ask for an explicit yes; only then call again with confirm=true.",
            }

        restaurant = self._restaurant(row)
        ctx = self.session.context()
        outcome = book_restaurant(
            self.repositories_by_name[restaurant.provider],
            restaurant,
            pick_slot(restaurant.slots, ctx),
            ctx,
            confirm=True,
            simulate=not self.live_booking,
        )
        self.session.last_booking = outcome
        return outcome.to_dict()

    def reset(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "reset", "filters": self.session.reset()}

    # ------------------------------------------------------------------ helpers
    def _resolve(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.session.rows:
            raise ToolError("No results yet. Call search_restaurants first.")
        rank = args.get("rank")
        if rank is not None:
            try:
                index = int(rank)
            except (TypeError, ValueError):
                raise ToolError(f"rank must be an integer, got {rank!r}") from None
            if not 1 <= index <= len(self.session.rows):
                raise ToolError(f"rank {index} is out of range (1-{len(self.session.rows)}); search again.")
            return self.session.rows[index - 1]

        name = args.get("name")
        if not name:
            raise ToolError("pass either rank or name")
        provider = args.get("provider")
        candidates = [
            row
            for row in self.session.rows
            if provider is None or str(row["provider"]).lower() == str(provider).lower()
        ]
        folded = str(name).casefold().strip()
        exact = [row for row in candidates if row["name"].casefold() == folded]
        matches = exact or [row for row in candidates if folded in row["name"].casefold()]
        if not matches:
            raise ToolError(f"no restaurant matching {name!r} in the last results")
        if len(matches) > 1:
            options = "; ".join(f"#{row['rank']} {row['name']} ({row['provider']})" for row in matches)
            raise ToolError(f"{name!r} is ambiguous: {options}")
        return matches[0]

    def _restaurant(self, row: dict[str, Any]) -> Restaurant:
        for restaurant in self.session.kept:
            if (restaurant.provider, restaurant.provider_id) == (row["provider"], row["provider_id"]):
                return restaurant
        raise ToolError("restaurant is no longer in the current result set; search again")
