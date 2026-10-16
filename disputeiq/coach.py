"""Credit coach: where you stand on each score factor, what to pay, and what to try next.

Factor weights are FICO's published breakdown (myfico.com, "What's in my FICO Scores"):
payment history 35%, amounts owed 30%, length of credit history 15%, new credit 10%,
credit mix 10%. We don't compute a score (that formula is proprietary); we show where
the report is strong or weak on each factor and why.

Product facts in SUGGESTIONS were checked October 2026. DisputeIQ is not paid by any of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .models import Account, Report
from .rules import key

GOOD, OK, WORK = "Good", "Okay", "Needs work"


@dataclass
class Factor:
    name: str
    weight: int          # % of a FICO score
    grade: str           # Good | Okay | Needs work
    value: str           # the headline number, e.g. "71%"
    detail: str
    tip: str


@dataclass
class Suggestion:
    title: str
    what: str
    cost: str
    why_you: str
    watch_out: str
    url: str


def unique_accounts(report: Report) -> list[Account]:
    """One row per real-world account. When bureaus disagree, keep the highest balance (worst case)."""
    best: dict = {}
    for a in report.accounts:
        k = key(a) if a.last4 else (a.creditor, a.bureau, a.id)
        if k not in best or a.balance > best[k].balance:
            best[k] = a
    return list(best.values())


def open_revolving(accts: list[Account]) -> list[Account]:
    return [a for a in accts if a.kind == "revolving" and a.limit > 0 and "closed" not in a.status.lower()]


def utilization(report: Report) -> tuple[float | None, list[tuple[Account, float]]]:
    cards = open_revolving(unique_accounts(report))
    if not cards:
        return None, []
    total_limit = sum(a.limit for a in cards)
    overall = sum(a.balance for a in cards) / total_limit
    return overall, [(a, a.balance / a.limit) for a in cards]


def pay_to_reach(report: Report, target: float) -> float:
    """Dollars to pay across cards to bring overall utilization down to `target` (e.g. 0.30)."""
    cards = open_revolving(unique_accounts(report))
    if not cards:
        return 0.0
    need = sum(a.balance for a in cards) - target * sum(a.limit for a in cards)
    return max(0.0, round(need, 2))


def years(d1: date, d2: date) -> float:
    return (d2 - d1).days / 365.25


def factors(report: Report) -> list[Factor]:
    today = report.consumer.report_date
    accts = unique_accounts(report)
    out = []

    # Payment history (35%)
    bad = [a for a in accts if a.negative]
    on_time = 1 - len(bad) / len(accts) if accts else 1
    grade = GOOD if not bad else OK if on_time >= 0.85 else WORK
    out.append(Factor("Payment history", 35, grade, f"{on_time:.0%}",
                      f"{len(accts) - len(bad)} of {len(accts)} accounts have no late payments or collections.",
                      "Never miss a payment: set every account to autopay at least the minimum. "
                      "One 30-day late can stay for 7 years." if bad else "Keep it up. Autopay protects this."))

    # Amounts owed (30%)
    u, _ = utilization(report)
    if u is None:
        out.append(Factor("Amounts owed", 30, OK, "—", "No open credit cards with a limit on this report.",
                          "Utilization is measured on credit cards. A first card (even secured) gives you one."))
    else:
        grade = GOOD if u < 0.10 else OK if u < 0.30 else WORK
        tip = (f"Pay ${pay_to_reach(report, 0.30):,.0f} to get under 30%, or ${pay_to_reach(report, 0.10):,.0f} "
               f"to get under 10%." if u >= 0.10 else "Excellent. Under 10% is where the highest scores live.")
        out.append(Factor("Amounts owed", 30, grade, f"{u:.0%}",
                          "Credit card utilization: how much of your total card limits you're using.", tip))

    # Length of history (15%)
    dated = [a for a in accts if a.opened]
    if dated:
        avg = sum(years(a.opened, today) for a in dated) / len(dated)
        oldest = max(years(a.opened, today) for a in dated)
        grade = GOOD if avg >= 7 else OK if avg >= 3 else WORK
        out.append(Factor("Length of history", 15, grade, f"{avg:.1f} yrs",
                          f"Average account age. Your oldest account is {oldest:.1f} years old.",
                          "Keep your oldest card open, even if you rarely use it. Time is the only fix here."))

    # New credit (10%)
    recent = [a for a in dated if (today - a.opened).days <= 365]
    grade = GOOD if len(recent) <= 1 else OK if len(recent) == 2 else WORK
    out.append(Factor("New credit", 10, grade, str(len(recent)),
                      "Accounts opened in the last 12 months.",
                      "Space out new applications. Each one adds a hard inquiry." if recent
                      else "No recent applications. Good."))

    # Credit mix (10%)
    kinds = {a.kind for a in accts if not a.negative}
    has_rev, has_inst = "revolving" in kinds, bool(kinds & {"installment", "mortgage"})
    grade = GOOD if has_rev and has_inst else OK
    mix = " + ".join(k for k, v in (("Cards", has_rev), ("Loans", has_inst)) if v) or "None yet"
    out.append(Factor("Credit mix", 10, grade, mix, "Types of credit in good standing.",
                      "Don't take on debt just for mix. It's the smallest factor."
                      if grade == GOOD else "A small credit-builder loan or card can add the missing type."))
    return out


SUGGESTIONS = {
    "kikoff": Suggestion(
        "Kikoff Credit Account", "A small line of credit you use in Kikoff's online store; on-time payments are "
        "reported to all three bureaus.", "From $5/month (Basic plan, $750 line). No interest, no credit check.",
        "Adds a revolving account with a low balance, which helps both payment history and utilization.",
        "The line only works in Kikoff's store. Late payments get reported too.", "https://kikoff.com"),
    "self": Suggestion(
        "Self Credit Builder", "A credit-builder loan: you make monthly payments into a savings account and get "
        "the money back at the end.", "Plans start around $25/month, plus fees and interest.",
        "Adds an installment loan, which fills a gap in your credit mix.",
        "Fixed 24-month term. You pay interest and fees to borrow your own money.", "https://www.self.inc"),
    "boost": Suggestion(
        "Experian Boost", "Free. Adds on-time phone, utility, streaming, insurance, and some rent payments to your "
        "Experian file.", "Free.", "Turns bills you already pay on time into credit history.",
        "Only affects your Experian report, and not every lender uses scores that count it.",
        "https://www.experian.com/Boost"),
    "secured": Suggestion(
        "Secured credit card", "A real credit card backed by a refundable deposit (often $200+). Many graduate to a "
        "regular card.", "Refundable deposit; pick one with no annual fee.",
        "The most direct way to add a revolving account if you don't have one.",
        "Keep the balance low and pay in full. Check it reports to all three bureaus.",
        "https://www.consumerfinance.gov/ask-cfpb/what-is-a-secured-credit-card-en-2112/"),
    "authorized": Suggestion(
        "Become an authorized user", "A family member with a long, on-time card history adds you to their card.",
        "Free.", "Can add their account age and on-time history to your report.",
        "Their missed payments would hurt you too. Only do it with someone reliable.",
        "https://www.consumerfinance.gov/ask-cfpb/what-is-an-authorized-user-on-a-credit-card-en-2124/"),
}


def suggest(report: Report) -> list[Suggestion]:
    accts = unique_accounts(report)
    good = [a for a in accts if not a.negative]
    cards = open_revolving(accts)
    u, _ = utilization(report)
    dated = [a for a in accts if a.opened]
    avg_age = (sum(years(a.opened, report.consumer.report_date) for a in dated) / len(dated)) if dated else 0
    picks = ["boost"]  # free, low-risk, helps almost everyone
    if not cards:
        picks.append("secured")
    if len(good) < 4 or avg_age < 3:
        picks.append("authorized")
    if (u is not None and u >= 0.30) or len(cards) < 2:
        picks.append("kikoff")  # extra limit with a near-zero balance lowers utilization
    if not any(a.kind in ("installment", "mortgage") for a in good):
        picks.append("self")
    return [SUGGESTIONS[p] for p in dict.fromkeys(picks)]


def reminders_ics(items: list[tuple[str, int]], start: date | None = None, remind_days: int = 3) -> str:
    """Monthly calendar reminders (works with iPhone, Google, Outlook). items = [(name, due_day_of_month)]."""
    start = start or date.today()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DisputeIQ//Payment reminders//EN", "CALSCALE:GREGORIAN"]
    for i, (name, day) in enumerate(items):
        day = max(1, min(28, int(day)))  # 28 so it fires every month, even February
        first = start.replace(day=day) if start.day <= day else (start.replace(day=1) + timedelta(days=32)).replace(day=day)
        lines += ["BEGIN:VEVENT", f"UID:disputeiq-{i}-{first:%Y%m%d}@disputeiq", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{first:%Y%m%d}", "RRULE:FREQ=MONTHLY",
                  f"SUMMARY:Payment due: {name}",
                  "DESCRIPTION:Pay at least the minimum today. Paying before the statement date also lowers "
                  "your reported balance.",
                  "BEGIN:VALARM", f"TRIGGER:-P{remind_days}D", "ACTION:DISPLAY",
                  f"DESCRIPTION:{name} is due in {remind_days} days", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
