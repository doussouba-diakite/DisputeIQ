from datetime import date

from disputeiq.coach import factors, pay_to_reach, reminders_ics, suggest, unique_accounts, utilization
from disputeiq.models import Account, Consumer, Report

TODAY = date(2026, 10, 1)


def rep(*a):
    return Report(Consumer("T", "", TODAY), list(a))


def card(balance, limit, **kw):
    base = dict(bureau="Equifax", creditor="Card Co", last4="1111", kind="revolving", status="Open, current",
                balance=balance, limit=limit, opened=date(2020, 1, 1))
    base.update(kw)
    return Account(**base)


def test_utilization_and_paydown():
    r = rep(card(600, 1000), card(200, 1000, creditor="Other", last4="2222"))
    u, per = utilization(r)
    assert round(u, 2) == 0.40 and len(per) == 2
    assert pay_to_reach(r, 0.30) == 200 and pay_to_reach(r, 0.10) == 600
    assert pay_to_reach(rep(card(50, 1000)), 0.30) == 0


def test_closed_cards_dont_count():
    assert utilization(rep(card(0, 5000, status="Paid, closed")))[0] is None


def test_same_account_across_bureaus_counts_once_worst_case():
    r = rep(card(400, 1000), card(900, 1000, bureau="TransUnion"))
    assert len(unique_accounts(r)) == 1 and round(utilization(r)[0], 2) == 0.90


def test_factor_weights_match_fico():
    assert [f.weight for f in factors(rep(card(50, 1000)))] == [35, 30, 15, 10, 10]


def test_clean_low_util_report_grades_good_on_payment_and_amounts():
    f = {x.name: x.grade for x in factors(rep(card(50, 1000)))}
    assert f["Payment history"] == "Good" and f["Amounts owed"] == "Good"


def test_suggestions_for_thin_file_with_no_cards():
    titles = {s.title for s in suggest(rep(Account("Equifax", "Loan Co", kind="installment", status="Current",
                                                   balance=500, opened=date(2025, 1, 1))))}
    assert {"Experian Boost", "Secured credit card", "Become an authorized user"} <= titles


def test_reminders_calendar_file():
    ics = reminders_ics([("Card Co", 31)], start=date(2026, 10, 16))
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == 1
    assert "RRULE:FREQ=MONTHLY" in ics and "DTSTART;VALUE=DATE:20261028" in ics  # day clamped to 28
    assert "TRIGGER:-P3D" in ics
