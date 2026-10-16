"""Turn a credit report (PDF or text) into structured accounts.

Two paths:
  * extract_with_claude: any report. Claude reads the text and calls a
    `record_accounts` tool with structured data (tool use). Claude never decides
    what to dispute; that's the rules engine's job.
  * parse_sample: the bundled fictional sample report, no API key needed, so the
    demo works for anyone.
"""
from __future__ import annotations

import os
import re
from datetime import date

from .models import BUREAUS, Account, Consumer, Report

MODEL = os.environ.get("DISPUTEIQ_MODEL", "claude-sonnet-5-5")


def pdf_text(data: bytes) -> str:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def parse_date(s: str | None) -> date | None:
    """Accepts 2021-03, 2021-03-15, 03/2021."""
    if not s:
        return None
    s = s.strip()
    for pat, fmt in ((r"^(\d{4})-(\d{2})-(\d{2})$", "ymd"), (r"^(\d{4})-(\d{2})$", "ym"), (r"^(\d{2})/(\d{4})$", "my")):
        m = re.match(pat, s)
        if m:
            g = [int(x) for x in m.groups()]
            if fmt == "ymd":
                return date(g[0], g[1], g[2])
            if fmt == "ym":
                return date(g[0], g[1], 1)
            return date(g[1], g[0], 1)
    return None


def money(s: str) -> float:
    s = re.sub(r"[^0-9.\-]", "", s or "")
    return float(s) if s else 0.0


# ── The fictional sample report ──────────────────────────────────────────────
def parse_sample(text: str) -> Report:
    consumer = Consumer()
    accounts, bureau = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("CONSUMER:"):
            consumer.name = line.split(":", 1)[1].strip()
        elif line.startswith("ADDRESS:"):
            consumer.address = line.split(":", 1)[1].strip()
        elif line.startswith("REPORT DATE:"):
            consumer.report_date = parse_date(line.split(":", 1)[1]) or date.today()
        elif line.upper() in {b.upper() for b in BUREAUS}:
            bureau = line.title().replace("Transunion", "TransUnion")
        elif bureau and line.count("|") in (7, 8):
            f = [x.strip() for x in line.split("|")]
            if f[0].lower() == "creditor":
                continue  # header row
            accounts.append(Account(
                bureau=bureau, creditor=f[0], last4=f[1].replace("•", "").strip(), kind=f[2].lower(),
                status=f[3], balance=money(f[4]), opened=parse_date(f[5]),
                first_delinquency=parse_date(f[6]), original_creditor="" if f[7] in ("", "—", "-") else f[7],
                limit=money(f[8]) if len(f) > 8 else 0.0))
    for i, a in enumerate(accounts):
        a.id = f"a{i}"
    return Report(consumer, accounts)


# ── Claude tool use ──────────────────────────────────────────────────────────
TOOL = {
    "name": "record_accounts",
    "description": "Record every account (tradeline) found in the credit report, exactly as reported. "
                   "One entry per account per bureau. Do not judge or skip anything.",
    "input_schema": {
        "type": "object",
        "properties": {
            "consumer_name": {"type": "string"},
            "consumer_address": {"type": "string"},
            "report_date": {"type": "string", "description": "YYYY-MM-DD if shown"},
            "accounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "bureau": {"type": "string", "enum": list(BUREAUS)},
                        "creditor": {"type": "string"},
                        "last4": {"type": "string", "description": "Last 4 characters of the account number only"},
                        "kind": {"type": "string", "enum": ["revolving", "installment", "mortgage", "collection",
                                                            "charge-off", "bankruptcy", "other"]},
                        "status": {"type": "string"},
                        "balance": {"type": "number"},
                        "credit_limit": {"type": "number", "description": "Credit limit or high credit, if shown"},
                        "opened": {"type": "string", "description": "YYYY-MM or YYYY-MM-DD"},
                        "first_delinquency": {"type": "string", "description": "Date of first delinquency, if any"},
                        "original_creditor": {"type": "string"},
                    },
                    "required": ["bureau", "creditor", "kind", "status", "balance"],
                },
            },
        },
        "required": ["accounts"],
    },
}


def extract_with_claude(text: str, api_key: str | None = None, client=None) -> Report:
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=8000, tools=[TOOL], tool_choice={"type": "tool", "name": "record_accounts"},
        messages=[{"role": "user", "content": "Here is a credit report. Record every account.\n\n" + text[:150_000]}],
    )
    return report_from_tool_input(next(b.input for b in resp.content if b.type == "tool_use"))


def report_from_tool_input(data: dict) -> Report:
    """Turn Claude's `record_accounts` call into a Report (validated, typed, last-4 only)."""
    consumer = Consumer(name=data.get("consumer_name", ""), address=data.get("consumer_address", ""),
                        report_date=parse_date(data.get("report_date")) or date.today())
    accounts = []
    for i, a in enumerate(data.get("accounts", [])):
        accounts.append(Account(
            bureau=a["bureau"], creditor=a["creditor"], last4=re.sub(r"\W", "", str(a.get("last4", "")))[-4:],
            kind=a["kind"], status=a.get("status", ""), balance=float(a.get("balance") or 0),
            limit=float(a.get("credit_limit") or 0), opened=parse_date(a.get("opened")),
            first_delinquency=parse_date(a.get("first_delinquency")),
            original_creditor=a.get("original_creditor", ""), id=f"a{i}"))
    return Report(consumer, accounts)


def load_report(data: bytes, api_key: str | None = None) -> Report:
    text = pdf_text(data)
    if "FICTIONAL SAMPLE" in text:
        return parse_sample(text)
    return extract_with_claude(text, api_key)
