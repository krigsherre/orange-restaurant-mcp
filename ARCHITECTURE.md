# Architecture

Four layers, one dependency direction. Nothing points back up.

```
handlers/   CLI + chat (parse input, render output, call one use case)
    ↓
app/        one ranking/booking run; report writers
    ↓
services/   search, ranking, booking, offer judging (business rules)
    ↓
repositories/  Swiggy / District adapters, MCP client, protocols
    ↓
domain/     models + parsing + pure rules (no I/O, no imports from orange)
```

## What lives where

| Layer | Module | Owns | Must not |
|---|---|---|---|
| `domain/` | `models.py` | `Offer`, `Slot`, `Restaurant`, `QueryContext`, `BookingOutcome` | import any other Orange package |
| | `normalize.py` | offer parsing, ₹/km parsing, haversine | touch MCP or files |
| | `cuisine.py`, `timeutil.py` | pure matching and time math | know about search |
| `repositories/` | `base.py` | `RestaurantRepository`, `ReservationRepository` ports | contain business rules |
| | `swiggy.py`, `district.py` | MCP calls, payload mapping, fixture replay | rank or filter |
| | `factory.py` | pick live vs fixture per provider | know about the CLI |
| `services/` | `search.py` | radius, budget, cuisine, availability window | print or write files |
| | `ranking.py` | judge glue, slot choice, comparator | call MCP directly |
| | `booking.py` | row selection, confirm gate, booking orchestration | write reports |
| | `judge/` | offer scoring (rubric / LLM / replay) | know about providers |
| `app/` | `pipeline.py` | one discover → search → judge → rank → book run | parse argv or render |
| | `reporting.py` | markdown / CSV / JSON / booking output | compute scores |
| `handlers/` | `cli.py` | flags, console table, exit codes | contain ranking logic |
| | `chat/` | tool schemas, agent loop, REPL, session state | query MCP directly (tools go through services) |

## Two request flows

**CLI**

```
handlers/cli.py
  → app/pipeline.run()
      → repositories.factory.build_repositories()      # live or fixture
      → services.search.search()                       # all filters in one call
      → services.judge + services.ranking              # score, then sort
      → services.booking.select_row/book_restaurant    # optional
      → app/reporting.write_*                          # ranked.{md,csv,json}
```

**Chat**

```
handlers/chat/agent.py  (DeepSeek tool-calling loop)
  → handlers/chat/tools.py  dispatch(search_restaurants | details | book_table | reset)
      → services.search / services.ranking / services.booking
          → repositories → domain
```

The chat handler owns the repositories it is given; `ChatTools` never constructs them.

## Dependency rule, enforced by review and imports

- `domain` imports only the standard library.
- `repositories` imports `domain` only.
- `services` imports `domain` and repository **ports**; booking receives a repository object, it does
  not import a concrete adapter.
- `app` may import services, repositories and reporting.
- `handlers` may import app/services/domain; `handlers/chat/tools.py` receives repositories by injection.

`tests/` is the regression net: 165 tests, including an import-boundary check by construction — a
violation shows up as a circular import or a failing patch target.

## Extension points

| Change | Touch |
|---|---|
| New aggregator | `repositories/<name>.py` implementing the port, register in `factory.py` |
| New filter | field on `QueryContext` → `services/search.py` → CLI flag in `handlers/cli.py` |
| New offer rule | `services/judge/rubric.py` (+ prompt in `judge/llm.py`) |
| New chat capability | schema + handler in `handlers/chat/tools.py` |
| New output format | `app/reporting.py`; never in services |
| New front-end (HTTP, voice) | `handlers/`, reusing `app/pipeline.py` or the services directly |

## Why `app/` exists

`pipeline.run()` is the only place that knows the full order of operations and owns side effects
(report files, booking records). Handlers stay thin: the CLI parses and prints, chat converts the same
capabilities into tools. That is why `python -m orange` and `./chat.py` can share every business rule
without sharing a single line of transport code.
