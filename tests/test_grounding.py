"""The fact-checker: numbers in an AI answer must appear in trusted sources."""
from disputeiq.grounding import claims, verify


def test_finds_money_percent_and_dates():
    kinds = [c.kind for c in claims("Pay $1,180 to reach 30% by March 2027 (2026-11-19).")]
    assert sorted(kinds) == ["date", "date", "money", "percent"]


def test_numbers_in_tool_results_pass():
    g = verify("Pay $1,180 to get under 30%. Utilization is 71.2%.",
               ['{"pay_to_reach_target": 1180.0, "overall_utilization_percent": 71.2}'])
    assert g.ok and len(g.verified) == 3


def test_made_up_numbers_are_flagged():
    g = verify("Pay $2,500 and it comes off in June 2029.", ['{"pay_to_reach_target": 1180.0}'])
    assert not g.ok and {c.text for c in g.unverified} == {"$2,500", "June 2029"}


def test_fractions_count_as_percents_and_iso_dates_match_words():
    g = verify("You're at 81% and the deadline is November 2026.", ['{"u": 0.81, "deadline": "2026-11-19"}'])
    assert g.ok
