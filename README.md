# Orange — Koramangala restaurant booker, ranked by *real* offer value

Multi-aggregator discovery over **Swiggy Dineout** and **District by Zomato**. One search call
applies every user filter — 5 km from Koramangala, ≤ ₹1,000/person, cuisine, and a booking-time
window — then offers are scored on what they are actually worth after their terms and conditions.
Ties go to the closer restaurant, then alphabetically.

```bash
./run.py --require-available            # 6 bookable places, 8–10 PM
```

- **Status**: fixture-backed (no live MCP token here) · **125 tests**, no network · **9-command** reproducible runbook
- **Outputs**: [out/ranked.md](out/ranked.md) · [out/ranked.csv](out/ranked.csv) · [out/ranked.json](out/ranked.json) · [out/commands.json](out/commands.json)

---

## Result snapshot

Fixture run, `rubric` judge. Anchor **12.9352, 77.6245** (Koramangala centre — the coordinate
Swiggy's own docs publish). 30 discovered → 26 pass filters → **6 bookable** in the 8–10 PM window.

| # | Restaurant | Provider | Best offer | Cash % | Score | ₹/person | Dist | Slot | Booking |
|---:|---|---|---|---:|---:|---:|---:|---|---|
| 1 | Beijing Bites | swiggy_dineout | Flat ₹300 off on total bill | 25% | 10 | 600 | 1.1 km | — | no data |
| 2 | Fenny's Lounge | district | Buy 1 Get 1 on cocktails | 20% | 20 | 850 | 1.2 km | — | District link |
| 3 | Fenny's Lounge | swiggy_dineout | Happy hours: 1+1 on all drinks | 20% | 20 | 800 | 1.4 km | — | no slot |
| 4 | Onesta | swiggy_dineout | 1+1 on all pizzas | 20% | 20 | 500 | 1.5 km | 8:30–10:00 PM | free |
| 5 | Toit | swiggy_dineout | Flat 20% off on total bill | 20% | 17 | 1,000 | 1.1 km | 8:00–9:30 PM | ₹500 prebook |
| 6 | Chulha Chauki Da Dhaba | swiggy_dineout | 20% off up to ₹400 | 20% | 11 | 650 | 1.2 km | 8:00–9:30 PM | free |
| 7 | Meghana Foods | district | Flat ₹250 off | 17% | 14 | 750 | 0.7 km | — | District link |
| 8 | Truffles | swiggy_dineout | Flat 15% off on total bill | 15% | 15 | 400 | 2.1 km | 8:15–9:45 PM | free |
| 9 | Toit | district | Flat 15% off + free dessert | 15% | 12 | 1,000 | 1.0 km | — | District link |
| 10 | Dyu Art Cafe | district | 15% off up to ₹300 | 15% | 10 | 475 | 1.1 km | — | District link |

Cash % is the discount at the reference bill (provider cost for two, or `--spend`); Score is the
same value after usability penalties, so a card-gated 25% still tops the list but scores low.
Slot `8:00–9:30 PM` = qualifying slot in the window · Booking `free` = bookable now ·
`₹500 prebook` = paid deal · `District link` = bookable through District · `no slot`/`no data` =
not bookable.

Excluded: Prost Brew Pub (over budget), Big Brewsky (7.4 km), Windmills (5.4 km), Cafe Azzure
(no price published). A restaurant appears **twice** when both providers list it — one row per
provider, as specified, not a merge.

## Canonical runs

Every row below is reproducible with one command (see [Reproduce](#reproduce-the-whole-result-set)):

| Command | Ranked | Bookable | Booking |
|---|---:|---:|---|
| `./run.py --out out` | 26 | 6 | — |
| `./run.py --require-available --out out/available` | 6 | 6 | — |
| `./run.py --time-start 19:30 --time-end 21:00 --require-available --out out/window-1930-2100` | 6 | 6 | — |
| `./run.py --cuisine Cafe --out out/cafe` | 4 | 0 | — |
| `./run.py --cuisine "North Indian,Chinese" --out out/north-indian-chinese` | 8 | 3 | — |
| `./run.py --budget 800 --radius 3 --out out/budget-800` | 18 | 5 | — |
| `./run.py --cuisine "North Indian,Chinese" --budget 900 --radius 3 --time-start 19:30 --time-end 21:30 --require-available --out out/combined` | 3 | 3 | — |
| `./run.py --book --auto --out out/auto` | 26 | 6 | `simulation` |
| `./run.py --book --select "Meghana Foods" --provider swiggy_dineout --out out/select-3` | 26 | 6 | `simulation` |

`--cuisine Cafe` has **0 bookable** because the cafes' only slots are 6:30 PM, outside the 20:00–22:00
window — the two filters interact, as they should.

---

## Filters: one search call

| Constraint | Rule |
|---|---|
| Anchor | 12.9352, 77.6245 |
| Proximity | ≤ 5.0 km inclusive; provider road distance preferred, haversine fallback |
| Budget | `costForTwo ÷ 2 ≤ ₹1,000` inclusive |
| Cuisine | optional, OR across terms, case/accent-insensitive |
| Window | slot start ∈ [`--time-start`, `--time-end`], inclusive |
| Availability | hard filter via `--require-available` |
| Bill reference | cost for two (`per_person × party`), or `--spend` — valuation only, not a filter |
| Booking | one restaurant per run, user-selected or automated |

All of it lives in `search()` (`src/orange/search.py`):

```python
outcome = search(restaurants, ctx, slot_loader=provider.slots_for)
outcome.kept       # restaurants passing radius, budget, cuisine and the window
outcome.rejected   # [{"name", "reason", "detail"}] — one row per exclusion
```

Order inside the call: cheap checks → slot probing → availability filter. Probe rules:

- `ctx.require_availability=True` → **every** survivor is probed; an unprobed restaurant is never
  reported as unavailable.
- `False` → probing is capped at 40 for display, and rows beyond the cap keep `unknown` availability.
- A failed slot lookup becomes `unknown`/`no-availability`; it never raises.

Rejection precedence: `missing-name`, `unverifiable-proximity`, `beyond-radius`, `unknown-cost`,
`over-budget`, `cuisine-mismatch`, `no-availability`.

### Combined example: cuisine + window + budget + radius

One invocation, four filters, three survivors:

```bash
./run.py --cuisine "North Indian,Chinese" --budget 900 --radius 3 \
         --time-start 19:30 --time-end 21:30 --require-available --out out/combined
```

| # | Restaurant | Offer | Cash % | Score | ₹/person | Dist | Slot |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | Chulha Chauki Da Dhaba | 20% off up to ₹400 | 20% | 11 | 650 | 1.2 km | 8:00–9:30 PM |
| 2 | Meghana Foods | Flat ₹200 off (above ₹1,200) | 14% | 14 | 700 | 0.9 km | 7:30–9:00 PM |
| 3 | Empire Restaurant | no offer | 0% | 0 | 500 | 1.6 km | 8:00–9:30 PM |

30 discovered → 3 kept. The 27 exclusions show which filter did the work:

| Reason | Count | Example |
|---|---:|---|
| `cuisine-mismatch` | 15 | Onesta (Italian) |
| `beyond-radius` | 4 | Biryani Blues (3.6 km > 3 km) |
| `no-availability` | 4 | Beijing Bites (no slot 7:30–9:30 PM) |
| `over-budget` | 3 | Toit (₹1,000 > ₹900) |
| `unknown-cost` | 1 | Cafe Azzure (no price) |

Three things worth noticing:

- **Same restaurant, different verdicts.** Meghana survives on Swiggy (labels include "North Indian")
  but its District row is dropped (`Biryani, Andhra`) — provider taxonomies disagree, and the filter
  is literal about labels.
- **Availability is not a ranking hint.** Beijing Bites passes cuisine, budget and distance but has
  no slot in the window, so it is excluded rather than ranked with a dash.
- **The slot is chosen inside the window.** Meghana's 7:30 PM slot (closest to the 19:30 start) is
  picked and ends at 9:00 PM via the 90-minute duration.

### Cuisine

`--cuisine` is repeatable and comma-separated; terms combine with **OR** ("Cafe or Italian").
Matching is case/accent-insensitive substring on provider labels plus a small alias set
(`cafe↔coffee`, `veg↔vegetarian`, `dessert↔ice cream`). `"South Indian"` never matches `"North Indian"`.

`--cuisine Cafe` keeps 4 rows; of its 26 exclusions, 22 are `cuisine-mismatch` and 4 are the physical
constraints (radius/budget/price), because cuisine is evaluated **after** them.

### Availability & booking

Availability is a **window**, not a point: a slot qualifies only when it starts inside
`[--time-start, --time-end]` (default `20:00–22:00`). Rows show `start–end`, where end = start +
`--duration` (default 90 min).

| Case | Command | Behaviour |
|---|---|---|
| **User selects** | `--book --select 3` / `--book --select "Meghana Foods" --provider swiggy_dineout` | resolves that row, verifies it has a slot, books it; ambiguous names list every option |
| **Automated** | `--book --auto` | skips unbookable rows, books the highest-ranked restaurant with a slot |

Without `--book` the same flags are a dry run. Live mode additionally needs `--yes`, otherwise the
status is `confirmation-required` and no request is sent. Fixture mode returns `simulation` with a
deterministic `SIM-…` order id.

Statuses: `simulation` · `confirmed` · `pending-payment` · `requires-user-action` ·
`confirmation-required` · `unavailable` · `failed`. Paid Swiggy prebook deals never auto-charge —
`create_cart` builds the deal cart and stops for UPI approval. District has no booking API, so it
returns `requires-user-action` with the restaurant's `booking_url`.

---

## Offer scoring

Every offer gets two numbers: **cash %** (what it is worth at the reference bill) and a 0–100
**score** (cash minus usability penalties). Ranking uses cash first, score second.

The real bill is unknown before ordering, so offers are valued at a stated **reference bill**: the
provider's cost for two (`per_person × party`), or `--spend` when you supply an expectation. Lower
`--spend` makes flat discounts worth more; every percentage moves with it. Parse first
(`normalize.parse_offer`): percent beats flat beats perk; minimum spends and conditions are
extracted by keyword.

| Type | Base cash % at the reference bill |
|---|---|
| percent | `min(pct, 100)` |
| flat | `min(100, ₹off ÷ bill × 100)` |
| perk | dessert 12 · drink 8 · 1+1 20 · unlimited 25 · generic free 10 · unmapped 6 |

Then penalties only on the score (nothing is added, so a conditional 20% can never beat a clean 20%):

| Condition | Effect |
|---|---|
| "up to" cap | score × 0.7 |
| min spend above party budget | **0**; rationale says what unlocks it |
| min spend above the reference bill | bill raised to the minimum before computing % |
| weekend/weekday rule on the day it excludes | **0** (a Friday is not a weekend) |
| lunch / daytime / breakfast offer at dinner | **0** |
| card required / bank offer | score −15 (cash % unchanged) |
| non-stackable / advance booking | score −3 / −4 |

Best offer = the one with the highest cash %; rank key:
`(-cash_pct, -score, distance, name.casefold(), provider, provider_id)`. A card-gated ₹300 on a
₹1,200 bill is 25% and outranks a clean ₹200 on ₹1,400 (14%): the card shows in `flags` and costs
score, but it does not erase the cash. Flat offers are measured against the bill, not per-person
spend — ₹300 is 25% of ₹1,200, not 50% of ₹600.

### LLM scoring: on, off, auto

Scoring is a switchable stage. Both engines write the same two fields — cash % and score — so the
sort is identical: cash value at the reference bill desc, score desc, distance, name. The DeepSeek
prompt receives the reference bill, weekday/`is_weekend`, and per-offer terms, and is told to
compute `effective_pct` against the bill, never the budget cap.

| `--llm` | Behaviour | Network |
|---|---|---|
| `off` | deterministic rubric scores every offer | none |
| `on` | DeepSeek chat completions; a missing `DEEPSEEK_API_KEY` is a hard error | yes |
| `auto` (default) | `on` when the key is set, else `off`; `ORANGE_LLM` overrides | only with a key |

```bash
cp .env.example .env    # then fill DEEPSEEK_API_KEY
./run.py --llm on       # DeepSeek verdicts -> scores -> sorted shortlist
./run.py --llm off      # rubric verdicts   -> scores -> the same sort
```

`orange` loads `.env` from the working directory on every CLI run (variables already exported in
the shell win), so putting the key in the file is enough — no `export` needed. The canonical
runbook pins `ORANGE_LLM=off` and blank provider tokens, so artifact regeneration stays the free,
offline fixture+rubric snapshot even when `.env` is configured.

If the call itself fails, the run still produces a shortlist: it falls back to the rubric, prints a
warning on stderr, and records `judge_fallback` in `ranked.json` / `ranked.md`. `--llm on` without a
key exits 2 instead of silently downgrading. `--judge` still selects the exact engines —

- `rubric` deterministic, always available
- `llm` DeepSeek, strict JSON, cached in `.cache/offer_judge.json`
- `replay` saved verdicts (`--judge replay --verdicts fixtures/offer_verdicts.sample.json`), rubric
  fills any gaps

`DEEPSEEK_MODEL` and `DEEPSEEK_BASE_URL` override the API defaults; `DEEPSEEK_TIMEOUT` sets the
request timeout in seconds.

---

## MCP inventory (verified 2026-11-26)

### Swiggy — official, 4 servers, 62 tools

**Dineout** `POST mcp.swiggy.com/dineout` — 11 tools, the ones that matter for booking:
`search_restaurants_dineout` · `get_restaurant_details` · `render_restaurants_dineout` ·
`get_available_slots` · `book_table` · `create_cart` · `get_booking_status` ·
`check_payment_status` · `confirm_order` · `get_payment_options` · `report_error`

**Food** `POST mcp.swiggy.com/food` — 20 tools (delivery, not booking):
`create_address` · `delete_address` · `get_addresses` · `get_restaurant_menu` · `search_menu` ·
`search_restaurants` · `update_food_cart` · `get_food_cart` · `flush_food_cart` ·
`apply_food_coupon` · `fetch_food_coupons` · `get_payment_options` · `check_payment_status` ·
`confirm_order` · `place_food_order` · `get_food_orders` · `get_food_order_details` ·
`get_food_delivery_status` · `track_food_order` · `report_error`

**Instamart** `POST mcp.swiggy.com/im` — 16 tools (groceries):
`create_address` · `delete_address` · `get_addresses` · `search_products` · `your_go_to_items` ·
`update_cart` · `get_cart` · `clear_cart` · `get_payment_options` · `check_payment_status` ·
`confirm_order` · `checkout` · `get_orders` · `track_order` · `get_delivery_status` · `report_error`

**Scenes** `POST mcp.swiggy.com/scenes` — 15 tools (events at dineout venues):
`discover_events` · `get_event_details` · `get_event_venue_info` · `get_events_collection` ·
`get_saved_locations` · `search_events` · `create_event_cart` · `get_event_cart` ·
`list_event_shows` · `get_payment_options` · `check_payment_status` · `confirm_order` ·
`place_event_order` · `get_event_booking_status` · `report_error`

### District by Zomato — official consumer app, 12 tools
`search_dining_restaurants` · `get_dining_restaurants_details` · `get_dynamic_filters` ·
`get_dynamic_filters_event` · `get_dynamic_filters_movie` · `search_events` ·
`search_event_venues` · `search_artists` · `search_movies` · `search_theatres` ·
`search_shows` · `get_movie_critique_reviews`

Docs rule: `get_dynamic_filters` **before** `search_dining_restaurants`; filters combine with AND.

### Zomato legacy / community — 10 tools, mock data
`zomato_search_restaurants` · `zomato_get_menu` · `zomato_add_to_cart` ·
`zomato_remove_from_cart` · `zomato_view_cart` · `zomato_clear_cart` ·
`zomato_set_delivery_address` · `zomato_place_order` · `zomato_track_order` · `zomato_list_orders`

Zomato's public developer API was retired in 2021; dine-in discovery moved to District. Anything
labelled "Zomato MCP" outside District is a community wrapper, not official.

### Ownly — no public MCP
Rapido's Ownly is a Bengaluru food-**delivery** app. No published MCP server, no dine-in inventory,
so it cannot satisfy a table-booking query. Dropped from scope; `@ownly-app/mcp` on npm is an
unrelated personal-notes tool.

---

## Run it

Python ≥ 3.10, no third-party dependencies.

```bash
./run.py --require-available                 # only places with a slot in 8–10 PM
./run.py --book --auto                       # book the top-ranked bookable restaurant
./run.py --book --select 3                   # book the row you picked
./run.py --cuisine Cafe                      # cafes (matches "Coffee" too)
./run.py --cuisine "North Indian,Chinese"    # OR across cuisines
./run.py --llm off                           # rubric scoring, no network
./run.py --llm on                            # DeepSeek scoring (needs DEEPSEEK_API_KEY)
./run.py --spend 800                         # value offers at ₹800/person instead of the average
./run.py --judge replay --verdicts fixtures/offer_verdicts.sample.json
PYTHONPATH=src python3 -m orange --help
```

### Reproduce the whole result set

```bash
python3 runbook.py          # wipes out/, runs 9 commands, writes out/commands.json
python3 runbook.py --keep   # re-run without deleting existing artifacts
```

`out/commands.json` records each invocation:

```json
{
  "name": "book-auto",
  "command": "./run.py --book --auto --out out/auto",
  "exit_code": 0,
  "duration_ms": 82,
  "result": {
    "counts": { "ranked": 26, "rejected": 4 },
    "bookable": 6,
    "booking": { "status": "simulation", "restaurant": "Meghana Foods", "order_id": "SIM-472E415FBC" }
  },
  "artifacts": ["out/auto/booking.json", "out/auto/ranked.md"]
}
```

A clean run is 9 commands / 31 artifacts / 0 failures. Each run also writes its own
`ranked.json` / `booking.json`; `commands.json` is the manifest that links command → result → files.

### Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t tests   # 125 tests
```

### Going live

```bash
cp .env.example .env
# SWIGGY_MCP_TOKEN=...   from Swiggy Builders Club access
```

`orange` reads `.env` in the working directory automatically (`ORANGE_ENV_FILE` points elsewhere if
needed); exported shell variables take precedence over the file.

The Swiggy adapter then calls the real endpoint: `initialize` → `tools/list` →
`search_restaurants_dineout` across seed queries with pagination → `get_available_slots` per
candidate. A live reservation needs `--book` plus `--yes`. District needs
`DISTRICT_MCP_ENDPOINT` + `DISTRICT_MCP_TOKEN`; no public endpoint is documented, so its live path is
a **provisional adapter** — fixtures are the supported path until partner access exists.

---

## Files

```
run.py                       zero-install CLI
runbook.py                   canonical command set -> out/commands.json
src/orange/
  models.py                  Offer / Slot / Restaurant / QueryContext
  normalize.py               offer parsing, cost & distance parsing, haversine
  mcpl.py                    MCP JSON-RPC streamable-HTTP client (tools/list, tools/call)
  search.py                  THE filter: radius + budget + cuisine + availability window
  cuisine.py                 alias table, cuisine matching, CLI parsing
  timeutil.py                12h/24h parsing, window labels
  booking.py                 selection (auto/user), booking outcomes, reports
  providers/
    swiggy_dineout.py        Dineout tools, multi-query discovery, book_table/create_cart
    district.py              District dining tools, booking_url availability
  judge/
    rubric.py                deterministic T&C rubric (default + fallback)
    llm.py                   DeepSeek judge, verdict cache, replay judge
  rank.py                    slot picking, availability labels, comparator
  report.py                  markdown / CSV / JSON writers
  pipeline.py                orchestrates discover -> search -> judge -> rank -> book
fixtures/                    schema-accurate Koramangala payloads (synthetic)
tests/                       125 unit tests
out/                         generated reports + commands.json
```

---

## Limits, stated plainly

1. **Fixtures, not live data.** No Swiggy/District token exists here, so every number above comes
   from synthetic but schema-accurate payloads. Restaurant names are real Koramangala places; prices
   and offers are invented for the demo.
2. **The bill is a reference, not a receipt.** `costForTwo ÷ 2 × party` is the provider's average
   spend, not your order, and every % is computed against it. Pass `--spend N` to value offers at
   your own expectation; min-spend offers above the party budget score 0 and report the spend that
   unlocks them.
3. **Cuisine labels are provider-supplied** and shallow (`"Cafe"`, not the menu), so cuisine is a
   discovery filter, not a dietary guarantee.
4. **Swiggy's Dineout search returns no coordinates**, only a distance string, so the 5 km filter uses
   the provider's road distance there; haversine applies to District.
5. **Availability is one `get_available_slots` call per candidate.** Under a hard filter every
   survivor is probed; otherwise probing stops at 40 for display. District exposes no slot API.
6. **LLM judging needs `DEEPSEEK_API_KEY`.** The rubric encodes the same reasoning deterministically;
   it is a baseline, not a substitute for unusual T&C combinations.
7. **Booking is simulated here.** `book_table` is never reached without a token; a real reservation
   needs `SWIGGY_MCP_TOKEN` and `--book --yes`. Paid deals stop at the payment step by design, and
   District can only hand back its link.
