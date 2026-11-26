# Command reference

Two equivalent entry points — `./run.py` is a zero-install wrapper around the package:

```bash
./run.py --help
PYTHONPATH=src python3 -m orange --help
```

Exit codes: `0` success · `1` selection failed (rank/name not bookable) · `2` configuration error
(bad flag, conflicting switches, explicit LLM requested without a key).

---

## 1. Search & rank (read-only)

```bash
./run.py                                        # Koramangala, ≤5 km, ≤₹1000/person, 20:00–22:00
./run.py --require-available                    # only places with a slot in that window
./run.py --time-start 19:30 --time-end 21:30    # your own window
./run.py --cuisine Cafe                         # cafes (matches "Coffee" too)
./run.py --cuisine "North Indian,Chinese"       # OR across cuisines
./run.py --budget 800 --radius 3                # tighter money and distance
./run.py --party 4 --date 2026-11-28            # 4 guests, another day
./run.py --spend 500                            # value offers at ₹500/person, not cost-for-two÷2
./run.py --top 10                               # print fewer rows
```

All filters combine in one `search()` pass:

```bash
./run.py --cuisine "North Indian,Chinese" --budget 900 --radius 3 \
         --time-start 19:30 --time-end 21:30 --require-available --out out/combined
```

What you get: `out/ranked.md` (table + why each offer scored), `out/ranked.csv`, `out/ranked.json`.
The console table shows `Cash%` (discount at the reference bill) and `Score` (0–100 after T&Cs).

## 2. Book

```bash
./run.py --select 3                                    # dry run: resolve row 3, reserve nothing
./run.py --select "Meghana Foods" --provider swiggy_dineout   # dry run by name
./run.py --book --select 3                             # book that row (fixtures simulate)
./run.py --book --select "Meghana Foods" --provider swiggy_dineout
./run.py --book --auto                                 # reserve the top row with a slot
./run.py --book --auto --yes                           # live: skip the confirmation gate
./run.py --book --auto --simulate                      # force simulation even with a live token
./run.py --book --select 1                             # District link-only row: returns its booking URL
```

Rules: `--book` requires `--auto` or `--select`; without it you get a dry run. Live bookings need
`--yes`. Paid Swiggy prebook deals stop at `pending-payment` (cart created, no charge). District rows
return `requires-user-action` with their booking link. `--auto` prints the link-only rows it skipped
(`Auto skipped: #1 …`) and records them in `ranked.json` as `auto_skipped`.

Writes `out/booking.json` + `out/booking.md` next to the ranking.

## 3. Chat

```bash
./chat.py                                      # REPL
./chat.py --once "show me cafes for tonight under 800"
./chat.py --live-book                          # allow real reservations
./chat.py --judge llm                          # model-scored offers too
```

Chat flags: `--once TEXT`, `--today YYYY-MM-DD`, `--live-book`, `--judge rubric|llm|auto`,
`--model`, `--fixtures`, `--no-live`. REPL commands: `/help`, `/filters`, `/reset`, `/quit`.

## 4. Scoring engine (LLM / rubric / replay)

```bash
./run.py --llm off                                     # deterministic rubric, no network
./run.py --llm on                                      # DeepSeek; missing key is a hard error
./run.py --llm auto                                    # default: on if key present, else off
./run.py --judge rubric                                # exact engine, same as --llm off
./run.py --judge llm                                   # exact engine, same as --llm on
./run.py --judge replay --verdicts fixtures/offer_verdicts.sample.json
ORANGE_LLM=off ./run.py                                # env equivalent of --llm off
```

| `--llm` | Behaviour | Network |
|---|---|---|
| `on` | DeepSeek verdicts; a missing `DEEPSEEK_API_KEY` exits 2 | yes |
| `off` | rubric verdicts | no |
| `auto` | `on` when a key exists, else `off`; `ORANGE_LLM` overrides | only with a key |

Contradictions (`--judge rubric --llm on`) exit 2. A runtime API failure does **not** lose the
ranking: it warns on stderr, records `judge_fallback`, and ranks with the rubric.

## 5. Reproduce & verify

```bash
python3 runbook.py                     # wipes out/, runs 9 commands, writes out/commands.json
python3 runbook.py --keep              # same without deleting artifacts
PYTHONPATH=src python3 -m unittest discover -s tests -t tests    # 165 tests
python3 -m compileall -q src tests     # syntax check
```

`runbook.py` pins `ORANGE_LLM=off` and blank MCP tokens, so regeneration never spends API credits or
hits live providers.

## 6. All flags

| Flag | Default | Meaning |
|---|---|---|
| `--area` | Koramangala | label only |
| `--lat` / `--lng` | 12.9352 / 77.6245 | search anchor |
| `--budget` | 1000 | ₹ per person, hard filter |
| `--spend` | cost-for-two ÷ 2 | expected ₹/person for valuing offers (not a filter) |
| `--radius` | 5.0 | km, hard filter |
| `--party` | 2 | guests; scales the bill reference |
| `--cuisine` | — | repeatable, comma-separated, OR |
| `--date` | 2026-11-27 | YYYY-MM-DD |
| `--time`, `--time-start` | 20:00 | window start |
| `--time-end` | 22:00 | window end (inclusive) |
| `--duration` | 90 | minutes per table, for slot end time |
| `--require-available` | off | drop rows without a slot in the window |
| `--auto` | off | reserve the top-ranked row with a slot; link-only rows are reported as skipped |
| `--select` | — | rank number or restaurant name |
| `--provider` | — | disambiguate `--select NAME` |
| `--book` | off | actually reserve (fixtures simulate) |
| `--yes` | off | skip live confirmation gate |
| `--simulate` | off | force simulation |
| `--judge` | auto | `auto\|llm\|rubric\|replay` |
| `--llm` | auto | `auto\|on\|off` switch |
| `--verdicts` | — | JSON file for replay |
| `--fixtures` | bundled | alternate fixture directory |
| `--out` | out | output directory |
| `--no-live` | off | ignore provider tokens |
| `--top` | 25 | rows printed |

## 7. Environment

Loaded from `.env` (or `ORANGE_ENV_FILE`); real env vars always win.

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | enables LLM judging |
| `DEEPSEEK_MODEL` | `deepseek-chat` | model id |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | gateway/proxy override |
| `DEEPSEEK_TIMEOUT` | 60 | seconds |
| `ORANGE_LLM` | — | `on` / `off` / `auto` |
| `ORANGE_ENV_FILE` | `.env` | alternate env file |
| `SWIGGY_MCP_TOKEN` | — | live Swiggy Dineout |
| `DISTRICT_MCP_ENDPOINT` / `DISTRICT_MCP_TOKEN` | — | live District (no public endpoint documented) |

## 8. Outputs

| File | Contents |
|---|---|
| `out/ranked.md` | table, top 3, per-offer reasoning, exclusions |
| `out/ranked.csv` | spreadsheet view |
| `out/ranked.json` | full rows, rejected reasons, meta (`judge`, `judge_fallback`, counts, `auto_skipped`) |
| `out/booking.json` / `.md` | booking outcome, order id, next step |
| `out/commands.json` | runbook manifest: command → exit code → result → artifacts |
