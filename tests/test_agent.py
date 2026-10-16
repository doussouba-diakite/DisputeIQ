"""Tests for the agent loop, using a fake Claude client (no API key, no network)."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from disputeiq.agent import TOOLS, ask, run_tool
from disputeiq.extract import load_report

REPORT = load_report((Path(__file__).resolve().parent.parent / "samples" / "sample_report.pdf").read_bytes())


class FakeClient:
    """Plays back scripted Claude responses and records what it was sent."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def tool_use(name, args, id_="t1"):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", name=name, input=args, id=id_)])


def text(t):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=t)])


def test_every_tool_runs_on_the_sample():
    args = {"payoff_plan": {"target_percent": 30}, "removal_date": {"creditor": "Midtown"},
            "dispute_deadline": {"received_date": "2026-10-20"}}
    for t in TOOLS:
        out = run_tool(t["name"], args.get(t["name"], {}), REPORT)
        assert out and "error" not in json.dumps(out, default=str)


def test_tool_results_are_real_numbers():
    plan = run_tool("payoff_plan", {"target_percent": 30}, REPORT)
    assert plan["pay_to_reach_target"] == 1180
    assert run_tool("dispute_deadline", {"received_date": "2026-10-20"}, REPORT)["deadline"] == "2026-11-19"
    old = run_tool("removal_date", {"creditor": "midtown collections"}, REPORT)
    assert all(r["already_past_limit"] for r in old)


def test_agent_loop_calls_tool_then_answers():
    client = FakeClient([tool_use("payoff_plan", {"target_percent": 30}), text("Pay $1,180.")])
    turn, history = ask("How much to get under 30%?", REPORT, client=client)
    assert turn.answer == "Pay $1,180." and turn.tools_used == [("payoff_plan", {"target_percent": 30})]
    # the second request carried the real tool result back to Claude
    sent = client.calls[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and json.loads(sent["content"])["pay_to_reach_target"] == 1180
    assert len(history) == 4  # question, tool call, tool result, answer


def test_bad_tool_arguments_are_returned_as_errors_not_crashes():
    client = FakeClient([tool_use("dispute_deadline", {"received_date": "not a date"}), text("Need a date.")])
    turn, _ = ask("deadline?", REPORT, client=client)
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and turn.answer == "Need a date."


def test_loop_stops_after_max_steps():
    client = FakeClient([tool_use("list_issues", {}, id_=f"t{i}") for i in range(10)])
    turn, _ = ask("loop forever", REPORT, client=client)
    assert "couldn't finish" in turn.answer and len(client.calls) == 6
