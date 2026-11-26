#!/usr/bin/env python3
"""Orange chat — talk to the Koramangala booking agent.

    ./chat.py
    ./chat.py --once "cafes near me for tonight under 800"
    ./chat.py --live-book        # arm real reservations (default is simulation)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from orange.handlers.chat import ChatAgent, ChatLLM, ChatSession, ChatTools  # noqa: E402
from orange.handlers.chat.prompt import SYSTEM_PROMPT  # noqa: E402
from orange.env import load_env  # noqa: E402
from orange.repositories.factory import build_repositories  # noqa: E402
from orange.services.judge import JudgeUnavailable, build_judge  # noqa: E402

HELP = """\
/help      this help
/filters   show the current search filters and last booking
/reset     clear filters and results
/quit      leave
Anything else is sent to the agent."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat", description="Conversational restaurant booking for Koramangala.")
    parser.add_argument("--once", metavar="TEXT", help="send one message, print the reply, exit")
    parser.add_argument("--today", help="override today's date (YYYY-MM-DD); defaults to the system date")
    parser.add_argument("--live-book", action="store_true", help="allow real reservations (default: simulate)")
    parser.add_argument("--judge", choices=["rubric", "llm", "auto"], default="rubric", help="offer-scoring engine")
    parser.add_argument("--model", help="override DEEPSEEK_MODEL for the chat model")
    parser.add_argument("--fixtures", help="alternate fixture directory")
    parser.add_argument("--no-live", action="store_true", help="force fixtures even if provider tokens exist")
    return parser


def _print_reply(text: str) -> None:
    lines = (text or "").strip().splitlines() or [""]
    print("bot> " + lines[0])
    for line in lines[1:]:
        print("     " + (textwrap.fill(line, width=86, subsequent_indent="     ") if len(line) > 86 else line))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env()

    session = ChatSession(today=args.today)
    try:
        judge = build_judge(args.judge)
    except JudgeUnavailable as exc:
        print(f"judge unavailable: {exc}", file=sys.stderr)
        return 2

    repositories, _notes = build_repositories(session.context(), fixtures_dir=args.fixtures, live=not args.no_live)
    tools = ChatTools(session, repositories, judge=judge, live_booking=args.live_book)
    try:
        llm = ChatLLM(model=args.model)
    except Exception as exc:  # noqa: BLE001 - configuration problem, show it plainly
        print(f"chat unavailable: {exc}", file=sys.stderr)
        return 2

    prompt = SYSTEM_PROMPT.format(
        today=session.filters["date"],
        defaults=json.dumps(session.summary(), ensure_ascii=False),
    )
    agent = ChatAgent(llm, tools, prompt)

    if args.once:
        try:
            _print_reply(agent.say(args.once))
        except Exception as exc:  # noqa: BLE001
            print(f"chat error: {exc}", file=sys.stderr)
            return 1
        return 0

    booking_mode = "LIVE bookings enabled" if args.live_book else "bookings simulated"
    print(f"Orange — Koramangala concierge · {session.filters['date']} · {booking_mode}")
    print("Try: \"show me cafes for tonight under 800\" · /help · /quit\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/quit", "/exit", "quit", "exit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/filters":
            print(json.dumps(session.snapshot(), indent=2, ensure_ascii=False))
            continue
        if line == "/reset":
            print(json.dumps(session.reset(), indent=2, ensure_ascii=False))
            continue
        try:
            _print_reply(agent.say(line))
        except KeyboardInterrupt:
            print("\n(interrupted)")
        except Exception as exc:  # noqa: BLE001 - keep the REPL alive
            print(f"! {exc}", file=sys.stderr)
    print("bye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
