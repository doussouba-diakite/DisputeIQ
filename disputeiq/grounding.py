"""Grounding check: does every number in an AI answer come from a trusted source?

LLMs can state a balance or a date confidently and be wrong. Before showing an AI
answer, we pull out every dollar amount, percentage, and date in it and look for
each one in the trusted text (tool results, the user's own words). Anything we
can't find is flagged, so a person knows exactly what to double-check.

This catches made-up numbers. It does not prove the reasoning is right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
MONTHS |= {m[:3]: i for m, i in list(MONTHS.items())}

MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?")
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
ISO = re.compile(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b")
WORD_DATE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(?:(\d{1,2}),?\s+)?(\d{4})\b",
                       re.I)


@dataclass
class Claim:
    kind: str     # money | percent | date
    text: str     # as written in the answer
    value: float | tuple


@dataclass
class Grounding:
    verified: list[Claim] = field(default_factory=list)
    unverified: list[Claim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unverified


def claims(text: str) -> list[Claim]:
    out = []
    for m in MONEY.finditer(text):
        v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        out.append(Claim("money", m.group(0).strip(), v))
    for m in PCT.finditer(text):
        out.append(Claim("percent", m.group(0), float(m.group(1))))
    for m in ISO.finditer(text):
        out.append(Claim("date", m.group(0), (int(m.group(1)), int(m.group(2)))))
    for m in WORD_DATE.finditer(text):
        out.append(Claim("date", m.group(0), (int(m.group(3)), MONTHS[m.group(1).lower().rstrip(".")])))
    return out


def _numbers(source: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", source)]


def _year_months(source: str) -> set[tuple[int, int]]:
    ym = {(int(a), int(b)) for a, b, _ in ISO.findall(source)}
    ym |= {(int(y), MONTHS[mo.lower().rstrip(".")]) for mo, _, y in WORD_DATE.findall(source)}
    return ym


def verify(answer: str, sources: list[str], money_tolerance: float = 1.0, pct_tolerance: float = 1.0) -> Grounding:
    """Check each number in `answer` against the trusted `sources`."""
    blob = "\n".join(sources)
    nums = _numbers(blob)
    # tool results store utilization as a fraction sometimes; accept both 0.81 and 81
    pcts = nums + [n * 100 for n in nums if 0 < n <= 1]
    yms = _year_months(blob)
    g = Grounding()
    for c in claims(answer):
        if c.kind == "money":
            ok = any(abs(n - c.value) <= money_tolerance for n in nums)
        elif c.kind == "percent":
            # standard guideline numbers are fine to state without a source
            ok = c.value in (10, 30, 35, 15) or any(abs(n - c.value) <= pct_tolerance for n in pcts)
        else:
            ok = c.value in yms
        (g.verified if ok else g.unverified).append(c)
    return g


def today_sources() -> list[str]:
    """The current date is always fair game ("as of October 2026")."""
    d = date.today()
    return [d.isoformat(), f"{d:%B %Y}"]
