from datetime import date
from pathlib import Path

import pytest

from disputeiq.extract import load_report, parse_date
from disputeiq.letters import build_letters_pdf
from disputeiq.models import Account, Consumer, Report
from disputeiq.rules import (double_counted, duplicates, mismatches, norm, not_mine, obsolete, paid_with_balance,
                             run_rules)
from disputeiq.tracker import Dispute

TODAY = date(2026, 10, 1)


def rep(*accounts):
    return Report(Consumer("Test Person", "1 Main St, Town, NY 10001", TODAY), list(accounts))


def acct(**kw):
    base = dict(bureau="Equifax", creditor="Hudson Credit", last4="1111", kind="revolving",
                status="Open, current", balance=100.0)
    base.update(kw)
    return Account(**base)


# ── duplicates ───────────────────────────────────────────────────────────────
def test_duplicate_on_same_bureau_is_flagged_once():
    a, b = acct(), acct()
    flags = duplicates(rep(a, b))
    assert len(flags) == 1 and flags[0].account is b and flags[0].strength == 3


def test_same_account_on_different_bureaus_is_not_a_duplicate():
    assert duplicates(rep(acct(bureau="Equifax"), acct(bureau="Experian"))) == []


def test_creditor_names_are_normalized():
    assert norm("CAPITAL ONE BANK, N.A.") == norm("Capital One")


# ── 7-year rule ──────────────────────────────────────────────────────────────
def test_collection_older_than_7_years_plus_180_days_is_obsolete():
    old = acct(kind="collection", status="Collection", first_delinquency=date(2018, 9, 1))
    assert len(obsolete(rep(old))) == 1


def test_collection_inside_the_window_is_not_obsolete():
    # 2019-06 + 180 days + 7 years = late 2026, after TODAY
    recent = acct(kind="collection", status="Collection", first_delinquency=date(2019, 6, 1))
    assert obsolete(rep(recent)) == []


def test_bankruptcy_uses_ten_years():
    bk = acct(kind="bankruptcy", status="Discharged", first_delinquency=date(2017, 1, 1))
    assert obsolete(rep(bk)) == []  # 9 years old: still reportable


def test_positive_accounts_never_expire():
    assert obsolete(rep(acct(first_delinquency=date(2000, 1, 1)))) == []


# ── cross-bureau ─────────────────────────────────────────────────────────────
def test_balance_mismatch_flags_the_higher_bureau():
    flags = mismatches(rep(acct(bureau="Equifax", balance=450), acct(bureau="TransUnion", balance=1250)))
    assert [f.account.bureau for f in flags] == ["TransUnion"] and flags[0].request == "correct"


def test_small_rounding_differences_are_ignored():
    assert mismatches(rep(acct(bureau="Equifax", balance=450), acct(bureau="Experian", balance=470))) == []


def test_status_mismatch_flags_the_negative_one():
    flags = mismatches(rep(acct(bureau="Equifax"), acct(bureau="Experian", status="30 days late")))
    assert [f.account.bureau for f in flags] == ["Experian"]


# ── other rules ──────────────────────────────────────────────────────────────
def test_paid_account_with_balance():
    assert len(paid_with_balance(rep(acct(status="Paid, closed", balance=1200)))) == 1
    assert paid_with_balance(rep(acct(status="Paid, closed", balance=0))) == []
    assert paid_with_balance(rep(acct(status="Unpaid", balance=50))) == []


def test_debt_sold_to_collector_but_still_owed_to_original():
    orig = acct(creditor="City Wireless", kind="charge-off", status="Charged off", balance=640)
    coll = acct(creditor="Apex Recovery", kind="collection", status="Collection", balance=640,
                original_creditor="City Wireless", last4="9999")
    flags = double_counted(rep(orig, coll))
    assert len(flags) == 1 and flags[0].account is orig


def test_not_mine():
    assert not_mine([acct()])[0].rule == "Not my account"


def test_clean_report_has_no_flags():
    assert run_rules(rep(acct(), acct(bureau="Experian"), acct(bureau="TransUnion"))) == []


# ── end to end on the fictional sample ───────────────────────────────────────
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_report.pdf"


def test_sample_report_finds_each_planted_error():
    report = load_report(SAMPLE.read_bytes())
    assert report.consumer.name == "Jordan Rivera" and len(report.accounts) == 20
    rules = {f.rule for f in run_rules(report)}
    assert rules == {"Duplicate account", "Too old to report", "Paid but shows a balance",
                     "Balance doesn't match other bureaus", "Same debt counted twice"}


def test_letters_pdf_is_built_per_bureau():
    report = load_report(SAMPLE.read_bytes())
    pdf = build_letters_pdf(run_rules(report), report.consumer, TODAY)
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000


# ── tracker + parsing ────────────────────────────────────────────────────────
def test_deadline_is_30_days_after_receipt_plus_15_with_more_info():
    d = Dispute("Experian", mailed=date(2026, 10, 1), received=date(2026, 10, 5))
    assert d.deadline() == date(2026, 11, 4)
    d.sent_more_info = True
    assert d.deadline() == date(2026, 11, 19)
    assert d.status(date(2026, 12, 1))[0] == "Overdue"


@pytest.mark.parametrize("s,expected", [("2021-03", date(2021, 3, 1)), ("03/2021", date(2021, 3, 1)),
                                        ("2021-03-15", date(2021, 3, 15)), ("—", None), (None, None)])
def test_parse_date(s, expected):
    assert parse_date(s) == expected
