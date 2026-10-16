"""The rules engine: plain Python checks for common, provable credit report errors.

The LLM never decides what is disputable. It only turns messy report text into
structured accounts (extract.py). These rules are deterministic, cited, and tested,
so every flag can be checked by a person.

Laws cited (Fair Credit Reporting Act, 15 U.S.C. § 1681 et seq.):
  § 607(b)  15 U.S.C. § 1681e(b)  reports must follow procedures for "maximum possible accuracy"
  § 605(a)  15 U.S.C. § 1681c(a)  most negative items can't be reported after 7 years
                                   (bankruptcies: 10 years)
  § 605(c)  15 U.S.C. § 1681c(c)  the 7 years starts 180 days after the delinquency began
  § 611     15 U.S.C. § 1681i     the bureau must reinvestigate a dispute within 30 days
  § 605B    15 U.S.C. § 1681c-2   blocking information that results from identity theft
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from .models import Account, Flag, Report

ACCURACY = "FCRA § 607(b), 15 U.S.C. § 1681e(b); § 611, 15 U.S.C. § 1681i"
OBSOLETE = "FCRA § 605(a), 15 U.S.C. § 1681c(a)"
IDENTITY = "FCRA § 611, 15 U.S.C. § 1681i; § 605B, 15 U.S.C. § 1681c-2"

SEVEN_YEARS = timedelta(days=round(7 * 365.25))
TEN_YEARS = timedelta(days=round(10 * 365.25))
GRACE = timedelta(days=180)


def norm(name: str) -> str:
    """'CAPITAL ONE, N.A.' and 'Capital One' are the same creditor."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(r"\b(na|n a|inc|llc|corp|bank|usa|the)\b", " ", name)
    return " ".join(name.split())


def key(a: Account) -> tuple[str, str]:
    return norm(a.creditor), a.last4


def duplicates(report: Report) -> list[Flag]:
    """The same account listed more than once on one bureau's report."""
    flags = []
    for bureau in {a.bureau for a in report.accounts}:
        groups = defaultdict(list)
        for a in report.by_bureau(bureau):
            groups[(key(a), round(a.balance))].append(a)
        for (_, bal), accts in groups.items():
            for extra in accts[1:]:  # keep the first, dispute the copies
                flags.append(Flag(
                    extra, "Duplicate account",
                    f"{extra.label()} appears {len(accts)} times on my {bureau} report with the same balance "
                    f"(${bal:,.0f}). It is one account and should be reported once.",
                    ACCURACY, strength=3))
    return flags


def obsolete(report: Report) -> list[Flag]:
    """Negative items past the legal reporting period."""
    flags = []
    today = report.consumer.report_date
    for a in report.accounts:
        if not a.negative or not a.first_delinquency:
            continue
        limit = TEN_YEARS if a.kind == "bankruptcy" else SEVEN_YEARS
        expires = a.first_delinquency + GRACE + limit
        if today > expires:
            years = 10 if a.kind == "bankruptcy" else 7
            flags.append(Flag(
                a, "Too old to report",
                f"{a.label()} first became delinquent in {a.first_delinquency:%B %Y}. The legal reporting "
                f"period ({years} years, starting 180 days after delinquency) ended {expires:%B %d, %Y}.",
                OBSOLETE, strength=3))
    return flags


def mismatches(report: Report, tolerance: float = 50) -> list[Flag]:
    """The same account reported differently by different bureaus."""
    flags = []
    groups = defaultdict(list)
    for a in report.accounts:
        if a.last4:
            groups[key(a)].append(a)
    for accts in groups.values():
        bureaus = {a.bureau for a in accts}
        if len(bureaus) < 2:
            continue
        balances = sorted({round(a.balance) for a in accts})
        statuses = {a.status.strip().lower() for a in accts}
        if balances[-1] - balances[0] > tolerance:
            low = min(accts, key=lambda a: a.balance)
            for a in accts:
                if round(a.balance) != round(low.balance):
                    flags.append(Flag(
                        a, "Balance doesn't match other bureaus",
                        f"{a.bureau} reports a ${a.balance:,.0f} balance on {a.label()}, but "
                        f"{low.bureau} reports ${low.balance:,.0f} for the same account.",
                        ACCURACY, strength=2, request="correct"))
        elif len(statuses) > 1:
            for a in accts:
                if a.negative and any(not b.negative for b in accts):
                    clean = next(b for b in accts if not b.negative)
                    flags.append(Flag(
                        a, "Status doesn't match other bureaus",
                        f"{a.bureau} reports {a.label()} as \"{a.status}\", but {clean.bureau} reports it as "
                        f"\"{clean.status}\".", ACCURACY, strength=2, request="correct"))
    return flags


def paid_with_balance(report: Report) -> list[Flag]:
    """An account marked paid or settled that still shows money owed."""
    flags = []
    for a in report.accounts:
        s = a.status.lower()
        if any(w in s for w in ("paid", "settled")) and "unpaid" not in s and a.balance > 0:
            flags.append(Flag(
                a, "Paid but shows a balance",
                f"{a.label()} is reported as \"{a.status}\" but still shows a ${a.balance:,.0f} balance. "
                f"A paid account should show $0.", ACCURACY, strength=3, request="correct"))
    return flags


def double_counted(report: Report) -> list[Flag]:
    """A debt sold to a collector, where the original creditor ALSO still reports a balance."""
    flags = []
    for bureau in {a.bureau for a in report.accounts}:
        accts = report.by_bureau(bureau)
        for c in (a for a in accts if a.kind == "collection" and a.original_creditor):
            for o in accts:
                if (o is not c and norm(o.creditor) == norm(c.original_creditor)
                        and o.kind == "charge-off" and o.balance > 0):
                    flags.append(Flag(
                        o, "Same debt counted twice",
                        f"{o.label()} still shows a ${o.balance:,.0f} balance, but the debt was sold to "
                        f"{c.creditor}, which reports ${c.balance:,.0f}. Only one company can be owed; "
                        f"the original creditor's balance should be $0.", ACCURACY, strength=2, request="correct"))
    return flags


def not_mine(accounts: list[Account]) -> list[Flag]:
    """Accounts the consumer says they never opened."""
    return [Flag(a, "Not my account",
                 f"I did not open {a.label()} and have no record of it. Please verify it or remove it.",
                 IDENTITY, strength=2) for a in accounts]


ALL_RULES = (duplicates, obsolete, mismatches, paid_with_balance, double_counted)


def run_rules(report: Report, disowned: list[Account] | None = None) -> list[Flag]:
    flags = [f for rule in ALL_RULES for f in rule(report)]
    flags += not_mine(disowned or [])
    # one flag per account per rule, strongest first
    seen, out = set(), []
    for f in sorted(flags, key=lambda f: -f.strength):
        k = (id(f.account), f.rule)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def days_until_obsolete(a: Account, today: date) -> int | None:
    if not a.negative or not a.first_delinquency:
        return None
    limit = TEN_YEARS if a.kind == "bankruptcy" else SEVEN_YEARS
    return (a.first_delinquency + GRACE + limit - today).days
