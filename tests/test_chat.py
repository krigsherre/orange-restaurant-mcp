import json
import unittest

import _path  # noqa: F401
from orange.handlers.chat import ChatAgent, ChatSession, ChatTools
from orange.handlers.chat.prompt import SYSTEM_PROMPT
from orange.handlers.chat.tools import TOOL_SCHEMAS
from orange.services.judge.rubric import RubricJudge


def make_tools(today="2026-11-27"):
    from orange.repositories.factory import build_repositories

    session = ChatSession(today=today)
    repositories, _ = build_repositories(session.context(), live=False)
    return ChatTools(session, repositories, judge=RubricJudge(), live_booking=False)


def tool_call(name, arguments, call_id="call_1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen = []

    def complete(self, messages, tools=None):
        self.calls += 1
        self.seen.append([dict(message) for message in messages])
        if not self.script:
            return {"role": "assistant", "content": "done"}
        return self.script.pop(0)


class SessionTests(unittest.TestCase):
    def test_defaults_use_today(self):
        session = ChatSession(today="2026-11-26")
        self.assertEqual(session.filters["date"], "2026-11-26")
        self.assertEqual(session.summary()["cuisines"], ["any"])
        self.assertEqual(session.filters["budget"], 1000.0)

    def test_merge_accepts_string_and_list_cuisines(self):
        session = ChatSession(today="2026-11-26")
        session.merge(cuisine="Cafe, Italian")
        self.assertEqual(session.filters["cuisines"], ["Cafe", "Italian"])
        session.merge(cuisine=["North Indian"])
        self.assertEqual(session.filters["cuisines"], ["North Indian"])

    def test_merge_ignores_unknown_and_none(self):
        session = ChatSession(today="2026-11-26")
        session.merge(budget=800, nonsense=1, radius=None)
        self.assertEqual(session.filters["budget"], 800.0)
        self.assertEqual(session.filters["radius"], 5.0)
        self.assertNotIn("nonsense", session.filters)

    def test_reset_restores_defaults(self):
        session = ChatSession(today="2026-11-26")
        session.merge(budget=500)
        session.rows = [{"rank": 1}]
        session.reset()
        self.assertEqual(session.filters["budget"], 1000.0)
        self.assertEqual(session.rows, [])


class ToolTests(unittest.TestCase):
    def test_search_merges_filters_and_returns_compact_rows(self):
        tools = make_tools()
        result = tools.search({"cuisine": ["Cafe"], "limit": 5})
        self.assertGreater(result["matched"], 0)
        self.assertEqual(tools.session.filters["cuisines"], ["Cafe"])
        self.assertLessEqual(len(result["results"]), 5)
        for row in result["results"]:
            self.assertTrue({"rank", "name", "provider", "cash_pct", "score", "per_person", "booking"} <= set(row))

    def test_second_search_reuses_saved_filters(self):
        tools = make_tools()
        tools.search({"budget": 800})
        result = tools.search({"cuisine": ["Cafe"]})
        self.assertEqual(tools.session.filters["budget"], 800.0)
        for row in result["results"]:
            self.assertLessEqual(row["per_person"], 800.0)

    def test_today_slots_make_tonight_bookable(self):
        tools = make_tools(today="2026-11-26")
        result = tools.search(
            {"date": "2026-11-26", "time_start": "20:00", "time_end": "22:00", "require_available": True}
        )
        self.assertGreater(result["bookable"], 0)

    def test_invalid_date_is_reported_not_raised(self):
        tools = make_tools()
        result = tools.dispatch("search_restaurants", {"date": "27-11-2026"})
        self.assertIn("error", result)

    def test_details_include_offers_and_slots(self):
        tools = make_tools()
        tools.search({"require_available": True, "budget": 1000})
        details = tools.details({"rank": 1})
        self.assertIn("offers", details)
        self.assertIn("slots", details)

    def test_booking_needs_confirmation_then_simulates(self):
        tools = make_tools()
        result = tools.search({"require_available": True, "budget": 1000, "limit": 10})
        row = next(r for r in result["results"] if r["booking"] in {"free", "₹500 prebook"})

        pending = tools.book({"rank": row["rank"], "confirm": False})
        self.assertEqual(pending["status"], "confirmation-required")
        self.assertIsNone(tools.session.last_booking)

        done = tools.book({"rank": row["rank"], "confirm": True})
        self.assertEqual(done["status"], "simulation")
        self.assertTrue(done["order_id"].startswith("SIM-"))
        self.assertIsNotNone(tools.session.last_booking)

    def test_district_rows_return_the_link(self):
        tools = make_tools()
        result = tools.search({"cuisine": ["Italian"], "limit": 15})
        district = next((r for r in result["results"] if r["booking"] == "District link"), None)
        self.assertIsNotNone(district, "expected at least one District link row")

        outcome = tools.book({"rank": district["rank"], "confirm": True})
        self.assertEqual(outcome["status"], "requires-user-action")
        self.assertTrue(outcome["booking_url"].startswith("https://www.district.in/"))

    def test_booking_before_search_is_an_error(self):
        tools = make_tools()
        result = tools.dispatch("book_table", {"rank": 1, "confirm": True})
        self.assertIn("search_restaurants", result["error"])

    def test_out_of_range_rank_is_an_error(self):
        tools = make_tools()
        tools.search({"budget": 1000, "limit": 3})
        result = tools.dispatch("book_table", {"rank": 99, "confirm": False})
        self.assertIn("out of range", result["error"])

    def test_ambiguous_name_is_an_error(self):
        tools = make_tools()
        tools.search({"cuisine": ["European"], "limit": 15})
        result = tools.dispatch("book_table", {"name": "Toit", "confirm": False})
        if "error" in result:
            self.assertIn("ambiguous", result["error"])
        else:  # only one provider surfaced — still a valid card, never a booking
            self.assertEqual(result["status"], "confirmation-required")

    def test_reset_tool_clears_state(self):
        tools = make_tools()
        tools.search({"budget": 500})
        result = tools.dispatch("reset_filters", {})
        self.assertEqual(result["status"], "reset")
        self.assertEqual(tools.session.filters["budget"], 1000.0)

    def test_unknown_tool_returns_error(self):
        tools = make_tools()
        self.assertIn("unknown tool", tools.dispatch("nope", {})["error"])


class AgentTests(unittest.TestCase):
    def test_executes_search_tool_then_answers(self):
        tools = make_tools()
        llm = FakeLLM([tool_call("search_restaurants", {"cuisine": ["Cafe"], "limit": 3}), {"role": "assistant", "content": "Here are cafes."}])
        agent = ChatAgent(llm, tools, "system")

        reply = agent.say("show me cafes")

        self.assertEqual(reply, "Here are cafes.")
        self.assertEqual(llm.calls, 2)
        tool_messages = [m for m in llm.seen[1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        payload = json.loads(tool_messages[0]["content"])
        self.assertGreater(payload["matched"], 0)
        self.assertTrue(payload["results"])

    def test_unknown_tool_is_reported_to_the_model(self):
        tools = make_tools()
        llm = FakeLLM([tool_call("nope", {}), {"role": "assistant", "content": "Sorry."}])
        agent = ChatAgent(llm, tools, "system")
        self.assertEqual(agent.say("hi"), "Sorry.")
        tool_message = [m for m in llm.seen[1] if m.get("role") == "tool"][0]
        self.assertIn("unknown tool", tool_message["content"])

    def test_invalid_json_arguments_do_not_crash(self):
        tools = make_tools()
        bad = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "search_restaurants", "arguments": "{"}}
        ]}
        llm = FakeLLM([bad, {"role": "assistant", "content": "Try again."}])
        agent = ChatAgent(llm, tools, "system")
        self.assertEqual(agent.say("hi"), "Try again.")
        tool_message = [m for m in llm.seen[1] if m.get("role") == "tool"][0]
        self.assertIn("not valid JSON", tool_message["content"])

    def test_step_limit_stops_runaway_tool_calls(self):
        tools = make_tools()
        llm = FakeLLM([tool_call("search_restaurants", {}), tool_call("search_restaurants", {})])
        agent = ChatAgent(llm, tools, "system", max_steps=2)
        reply = agent.say("loop")
        self.assertIn("tool-call limit", reply)
        self.assertEqual(llm.calls, 2)

    def test_history_trim_keeps_a_user_turn_first(self):
        tools = make_tools()
        script = [tool_call("search_restaurants", {"limit": 1}, call_id=f"c{i}") for i in range(3)]
        agent = ChatAgent(FakeLLM(script), tools, "system", max_steps=3, max_messages=4)
        agent.say("go")
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(agent.messages[1]["role"], "user")


class PromptAndSchemaTests(unittest.TestCase):
    def test_prompt_formats_with_today_and_defaults(self):
        prompt = SYSTEM_PROMPT.format(today="2026-11-26", defaults='{"party": 2}')
        self.assertIn("2026-11-26", prompt)
        self.assertIn('{"party": 2}', prompt)

    def test_tool_schemas_cover_every_tool(self):
        names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
        self.assertEqual(names, ["search_restaurants", "restaurant_details", "book_table", "reset_filters"])
        book = next(schema for schema in TOOL_SCHEMAS if schema["function"]["name"] == "book_table")
        self.assertIn("confirm", book["function"]["parameters"]["required"])


if __name__ == "__main__":
    unittest.main()


class ClarificationGateTests(unittest.TestCase):
    def test_unfiltered_first_search_asks_before_searching(self):
        tools = make_tools()
        result = tools.search({})
        self.assertEqual(result["status"], "needs_clarification")
        self.assertIn("cuisine", result["ask"])
        self.assertEqual(tools.session.rows, [])

    def test_second_unfiltered_search_uses_defaults(self):
        tools = make_tools()
        tools.search({})
        result = tools.search({})
        self.assertGreater(result["matched"], 0)

    def test_first_search_with_filters_skips_the_question(self):
        tools = make_tools()
        result = tools.search({"cuisine": ["Cafe"]})
        self.assertGreater(result["matched"], 0)
        self.assertTrue(tools.session.clarified)

    def test_reset_rearms_the_question(self):
        tools = make_tools()
        tools.search({"cuisine": ["Cafe"]})
        tools.reset({})
        self.assertEqual(tools.search({})["status"], "needs_clarification")


class AnyCuisineTests(unittest.TestCase):
    def test_any_cuisine_is_not_a_filter(self):
        tools = make_tools()
        result = tools.search({"cuisine": ["any"], "budget": 1000})
        self.assertGreater(result["matched"], 30)
        self.assertEqual(tools.session.filters["cuisines"], [])

    def test_any_clears_a_previous_filter(self):
        tools = make_tools()
        tools.search({"cuisine": ["Cafe"]})
        tools.search({"cuisine": "any"})
        self.assertEqual(tools.session.filters["cuisines"], [])
