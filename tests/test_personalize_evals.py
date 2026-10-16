"""AI letter personalization guardrails, and the extraction eval harness (fake clients, no API key)."""
import copy
from types import SimpleNamespace as NS

from disputeiq import evals
from disputeiq.agent import ask
from disputeiq.models import Account, Flag
from disputeiq.personalize import personalize
from tests.test_agent import REPORT, FakeClient, text, tool_use

FLAG = Flag(Account("Experian", "Northside Bank", "1187", "revolving", "Paid, closed", 412.0, 1000.0),
            "Paid but shows a balance", "This account is reported as paid and closed but still shows a $412 balance.",
            "FCRA § 607(b), § 611", 3)


def draft(paragraph):
    return FakeClient([NS(stop_reason="tool_use",
                          content=[NS(type="tool_use", name="write_paragraph", input={"paragraph": paragraph}, id="p")])])


def test_grounded_draft_is_used():
    d = personalize(FLAG, "I paid it off in March 2024 and have the payoff letter.", client=draft(
        "I paid my Northside Bank card in full in March 2024, and I have the payoff letter from the bank. "
        "Your report still shows a $412 balance on an account marked paid and closed."))
    assert d.used_ai and not d.problems and "March 2024" in d.text


def test_invented_amount_falls_back_to_template():
    d = personalize(FLAG, "I paid it off.", client=draft(
        "I paid this account in full with a final payment of $1,950 in June 2023, so the balance should be zero."))
    assert not d.used_ai and d.text == FLAG.reason
    assert any("$1,950" in p for p in d.problems) and any("June 2023" in p for p in d.problems)


def test_admitting_the_debt_is_blocked():
    d = personalize(FLAG, "It was paid.", client=draft(
        "I know I owe something on this card, but the $412 balance on this paid and closed account is wrong."))
    assert not d.used_ai and "admits owing the debt" in d.problems


def test_no_note_means_no_ai_call():
    assert personalize(FLAG, "  ").text == FLAG.reason


def test_coach_answers_get_fact_checked():
    turn, _ = ask("How much?", REPORT, client=FakeClient([tool_use("payoff_plan", {"target_percent": 30}),
                                                           text("Pay $1,180 now, or $3,000 to be safe.")]))
    assert [c.text for c in turn.grounding.verified] == ["$1,180"]
    assert [c.text for c in turn.grounding.unverified] == ["$3,000"]


def test_eval_oracle_scores_perfectly_and_a_sloppy_extractor_does_not():
    truths = {}

    def oracle(t):
        return truths[t]

    for i in range(6):
        r = evals.synthetic_report(7 + i)
        truths[evals.render(r, evals.LAYOUTS[i % 3])] = r
    perfect = evals.run(oracle, 6)["all"]
    assert perfect.recall == perfect.precision == perfect.flag_f1 == 1.0

    def sloppy(t):  # drops the last account and misreads every balance by $100
        r = copy.deepcopy(truths[t])
        r.accounts = r.accounts[:-1]
        for a in r.accounts:
            a.balance += 100
        return r

    bad = evals.run(sloppy, 6)["all"]
    assert bad.recall < 1 and bad.field_acc("balance") == 0 and bad.flag_f1 < 1


def test_every_layout_contains_every_account():
    r = evals.synthetic_report(9)
    for lay in evals.LAYOUTS:
        out = evals.render(r, lay)
        assert all(a.last4 in out for a in r.accounts)
