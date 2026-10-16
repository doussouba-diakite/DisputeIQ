"""AI letter personalization, with guardrails.

The rules engine writes a correct but generic reason for each dispute. The user can
add their own details ("I paid this in full in March 2024, I have the receipt"), and
Claude rewrites the paragraph in first person so it reads like the user wrote it.

A dispute letter is a legal document, so the AI's draft has to pass checks before
it's used:
  1. Grounding: every dollar amount and date must come from the account record,
     the rule's finding, or the user's own note. (disputeiq/grounding.py)
  2. Safety: no admitting the debt is owed, no threats, no SSN, sensible length.
If either check fails, the letter keeps the rules-engine wording and the app says why.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .grounding import Grounding, verify
from .models import Flag

MODEL = os.environ.get("DISPUTEIQ_MODEL", "claude-sonnet-5-5")

SYSTEM = """You write one paragraph of a consumer's credit report dispute letter, in the consumer's own voice
(first person, calm, specific, plain English).

Use ONLY these facts: the account record, the finding, and the consumer's note. Do not add any amount, date,
account number, or event that isn't in them. Do not admit that any debt is owed unless the note says so. No threats,
no legal conclusions beyond the finding, no Social Security numbers. 2 to 4 sentences. Do not include the request
("please delete") or the law; the letter adds those."""

TOOL = {"name": "write_paragraph", "description": "Return the dispute paragraph.",
        "input_schema": {"type": "object", "properties": {"paragraph": {"type": "string"}},
                         "required": ["paragraph"]}}

BANNED = [(re.compile(r"\b(i|we) (do )?owe\b", re.I), "admits owing the debt"),
          (re.compile(r"\b(sue|lawsuit|attorney|lawyer)\b", re.I), "threatens legal action"),
          (re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"), "looks like a Social Security number"),
          (re.compile(r"\b\d{9,}\b"), "contains a full account number")]


@dataclass
class Draft:
    text: str                       # what goes in the letter
    used_ai: bool                   # False = fell back to the rules-engine wording
    problems: list[str] = field(default_factory=list)
    grounding: Grounding | None = None


def facts(flag: Flag, note: str) -> dict:
    return {"account": flag.account.to_dict(), "finding": flag.rule + ": " + flag.reason,
            "consumer_note": note.strip()}


def check(text: str, flag: Flag, note: str) -> tuple[list[str], Grounding]:
    g = verify(text, [json.dumps(facts(flag, note)), flag.reason, note])
    problems = [f"mentions {c.text}, which isn't in your account or your note" for c in g.unverified]
    problems += [why for pat, why in BANNED if pat.search(text) and not (why.startswith("admits") and pat.search(note))]
    n = len(text.split())
    if not 15 <= n <= 160:
        problems.append(f"is {n} words; it should be a short paragraph")
    return problems, g


def personalize(flag: Flag, note: str, client=None, api_key: str | None = None) -> Draft:
    if not note.strip():
        return Draft(flag.reason, used_ai=False)
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=600, system=SYSTEM, tools=[TOOL],
        tool_choice={"type": "tool", "name": "write_paragraph"},
        messages=[{"role": "user", "content": json.dumps(facts(flag, note), indent=1)}])
    text = next(b.input for b in resp.content if b.type == "tool_use")["paragraph"].strip()
    problems, g = check(text, flag, note)
    if problems:
        return Draft(flag.reason, used_ai=False, problems=problems, grounding=g)
    return Draft(text, used_ai=True, grounding=g)
