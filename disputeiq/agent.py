"""The DisputeIQ AI coach: Claude + tools + a loop.

Claude answers questions about the user's own report, but it doesn't do math or
legal judgment from memory. It calls these Python tools, which wrap the tested
rules engine and coach. Every number it states comes from a tool result, and the
app shows which tools were used, so the answer is checkable.

    user question ─► Claude ─► tool_use ─► Python runs the tool ─► tool_result ─► Claude ─► … ─► answer
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

from . import coach, rules
from .grounding import Grounding, today_sources, verify
from .models import Report
from .tracker import Dispute

MODEL = os.environ.get("DISPUTEIQ_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 6

SYSTEM = """You are DisputeIQ's credit coach. You help one person understand their own credit report.

Rules:
- Get facts ONLY from your tools. Never invent balances, dates, laws, or scores. If a tool can't answer, say so.
- Be warm, short, and specific. Plain words. Use the person's real account names and numbers from the tools.
- Explain the "why" in one sentence, then the next step.
- Only recommend disputing items a tool flagged or the person says are wrong. Never suggest disputing accurate information.
- Never ask for a Social Security number, full account numbers, or passwords.
- Disputing is free. Never suggest paying a credit repair company.
- You are not a lawyer or financial advisor; say so briefly when giving legal or money guidance.
"""

TOOLS = [
    {"name": "list_accounts",
     "description": "All accounts on the report, optionally for one bureau. Returns creditor, last4, type, status, "
                    "balance, credit limit, opened date, date of first delinquency.",
     "input_schema": {"type": "object", "properties": {
         "bureau": {"type": "string", "enum": ["Equifax", "Experian", "TransUnion"]}}}},
    {"name": "list_issues",
     "description": "Possible errors found by the rules engine, each with the reason, the law it cites, how strong "
                    "the dispute is (1-3), and whether to ask for deletion or correction.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "score_factors",
     "description": "Where the report stands on each FICO score factor (payment history, amounts owed, length of "
                    "history, new credit, credit mix) with a grade and a tip. Does not compute a score.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "payoff_plan",
     "description": "Credit card utilization overall and per card, and dollars to pay to reach a target "
                    "utilization percent.",
     "input_schema": {"type": "object", "properties": {
         "target_percent": {"type": "number", "description": "e.g. 30 or 10"}}, "required": ["target_percent"]}},
    {"name": "removal_date",
     "description": "When a negative account legally has to come off the report (7 years, or 10 for bankruptcy, "
                    "counted from 180 days after first delinquency).",
     "input_schema": {"type": "object", "properties": {
         "creditor": {"type": "string", "description": "Creditor name as it appears in list_accounts"}},
         "required": ["creditor"]}},
    {"name": "dispute_deadline",
     "description": "The date a bureau must finish investigating a dispute (FCRA § 611): 30 days after it received "
                    "the letter, or 45 if the consumer sent more information during that time.",
     "input_schema": {"type": "object", "properties": {
         "received_date": {"type": "string", "description": "YYYY-MM-DD"},
         "sent_more_info": {"type": "boolean"}}, "required": ["received_date"]}},
    {"name": "credit_builders",
     "description": "Credit-building options matched to this report (not sponsored), with cost and caveats.",
     "input_schema": {"type": "object", "properties": {}}},
]


def run_tool(name: str, args: dict, report: Report, disowned=None) -> object:
    """Execute one tool. Plain Python; this is where every fact comes from."""
    if name == "list_accounts":
        accts = report.by_bureau(args["bureau"]) if args.get("bureau") else report.accounts
        return [a.to_dict() | {"negative": a.negative} for a in accts]
    if name == "list_issues":
        return [{"account": f.account.label(), "bureau": f.account.bureau, "rule": f.rule, "reason": f.reason,
                 "law": f.law, "strength": f.strength, "request": f.request}
                for f in rules.run_rules(report, disowned)]
    if name == "score_factors":
        return [vars(f) for f in coach.factors(report)]
    if name == "payoff_plan":
        u, cards = coach.utilization(report)
        if u is None:
            return {"note": "No open credit cards with a limit on this report."}
        t = float(args["target_percent"]) / 100
        return {"overall_utilization_percent": round(u * 100, 1),
                "pay_to_reach_target": coach.pay_to_reach(report, t),
                "cards": [{"card": a.label(), "balance": a.balance, "limit": a.limit,
                           "utilization_percent": round(r * 100, 1),
                           "pay_to_reach_target": round(max(0.0, a.balance - t * a.limit), 2)} for a, r in cards]}
    if name == "removal_date":
        want = rules.norm(args["creditor"])
        hits = [a for a in report.accounts if want and want in rules.norm(a.creditor)]
        if not hits:
            return {"error": f"No account matching '{args['creditor']}'."}
        out = []
        for a in hits:
            left = rules.days_until_obsolete(a, report.consumer.report_date)
            out.append({"account": a.label(), "bureau": a.bureau, "negative": a.negative,
                        "first_delinquency": a.first_delinquency.isoformat() if a.first_delinquency else None,
                        "days_until_must_be_removed": left,
                        "already_past_limit": left is not None and left < 0})
        return out
    if name == "dispute_deadline":
        d = Dispute("", mailed=date.fromisoformat(args["received_date"]),
                    received=date.fromisoformat(args["received_date"]), sent_more_info=bool(args.get("sent_more_info")))
        return {"deadline": d.deadline().isoformat(), "law": "FCRA § 611(a)(1), 15 U.S.C. § 1681i(a)(1)"}
    if name == "credit_builders":
        return [vars(s) for s in coach.suggest(report)]
    return {"error": f"Unknown tool {name}"}


@dataclass
class Turn:
    answer: str
    tools_used: list[tuple[str, dict]] = field(default_factory=list)
    grounding: Grounding | None = None    # which numbers in the answer were found in tool results


def ask(question: str, report: Report, history: list[dict] | None = None, client=None,
        api_key: str | None = None, disowned=None) -> tuple[Turn, list[dict]]:
    """Run the agent loop for one question. Returns the answer and the updated message history."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    messages = list(history or []) + [{"role": "user", "content": question}]
    used, evidence = [], [question] + today_sources()
    for _ in range(MAX_STEPS):
        resp = client.messages.create(model=MODEL, max_tokens=1500, system=SYSTEM, tools=TOOLS, messages=list(messages))
        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return Turn(text, used, verify(text, evidence)), messages
        results = []
        for c in calls:
            used.append((c.name, dict(c.input)))
            try:
                out = run_tool(c.name, dict(c.input), report, disowned)
                evidence.append(json.dumps(out, default=str))
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(out, default=str)})
            except Exception as e:  # bad arguments: tell Claude so it can fix them
                results.append({"type": "tool_result", "tool_use_id": c.id, "is_error": True, "content": str(e)})
        messages.append({"role": "user", "content": results})
    return Turn("I couldn't finish that in a few steps. Try asking a narrower question.", used, Grounding()), messages


SUGGESTED = [
    "What's hurting my credit the most right now?",
    "Which disputes should I send first, and why?",
    "How much do I need to pay to get my cards under 30%?",
    "When does the Midtown Collections account have to come off?",
    "I mailed my Experian letter and it arrived on Oct 20. When's the deadline?",
    "Should I try Kikoff or a secured card?",
]
