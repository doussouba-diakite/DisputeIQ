"""Evaluation harness for the AI extraction step.

Real credit reports look different at every bureau, and we can't put real reports in
a repo. So we generate synthetic reports with known answers, in three different
layouts, run the extractor on each, and score it:

  * account recall / precision: did it find every account, and only real ones?
  * field accuracy: creditor, type, status, balance, limit, opened, first delinquency
  * flag agreement: does the rules engine flag the same errors on the AI's output as
    on the ground truth? This is the number that matters to a user: an extraction
    mistake only hurts if it changes what gets disputed.

    python scripts/run_evals.py --reports 9           # needs ANTHROPIC_API_KEY
    python scripts/run_evals.py --extractor oracle    # sanity check, no key: should score 100%
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from .models import BUREAUS, Account, Consumer, Report
from .rules import norm, run_rules

CREDITORS = {"Apex Bank Card": "revolving", "Riverstone Credit Union Visa": "revolving",
             "Summit Card Services": "revolving", "Metro Retail Card": "revolving",
             "Harbor Auto Finance": "installment", "Maple Student Loans": "installment",
             "Pioneer Personal Loan": "installment", "Greenline Mortgage": "mortgage"}
COLLECTORS = ["Midtown Collections", "Eastbridge Recovery", "Allied Debt Partners"]
FIELDS = ("creditor", "kind", "status", "balance", "limit", "opened", "first_delinquency")
LAYOUTS = ("table", "narrative", "blocks")


# ── Synthetic reports with known answers ─────────────────────────────────────
def synthetic_report(seed: int) -> Report:
    """A random tri-bureau report with a few planted errors (duplicate, mismatch, obsolete collection)."""
    rng = random.Random(seed)
    today = date(2026, 10, 1)
    base = []
    cards = rng.sample([c for c, k in CREDITORS.items() if k == "revolving"], 2)
    loans = rng.sample([c for c, k in CREDITORS.items() if k != "revolving"], 2)
    for creditor in cards + loans:
        kind = CREDITORS[creditor]
        limit = rng.choice([500, 1000, 2500, 5000]) if kind == "revolving" else 0
        bal = round(rng.uniform(0, limit), 0) if limit else {"installment": rng.choice([4200, 11800]),
                                                              "mortgage": 186000}[kind]
        opened = date(rng.randint(2012, 2024), rng.randint(1, 12), 1)
        base.append(dict(creditor=creditor, last4=f"{rng.randint(1000, 9999)}", kind=kind, status="Open, current",
                         balance=bal, limit=limit, opened=opened, first_delinquency=None))
    old = rng.random() < .6
    dofd = date(2017 if old else 2023, rng.randint(1, 12), 1)
    base.append(dict(creditor=rng.choice(COLLECTORS), last4=f"{rng.randint(1000, 9999)}", kind="collection",
                     status="Collection", balance=rng.choice([340, 875, 1460]), limit=0,
                     opened=dofd, first_delinquency=dofd))
    accounts = []
    for b in BUREAUS:
        for row in base:
            accounts.append(Account(bureau=b, **row))
    if rng.random() < .6:  # balance mismatch on one bureau
        a = next(x for x in accounts if x.bureau == "TransUnion" and x.kind == "revolving" and x.limit)
        a.balance = a.limit
    if rng.random() < .5:  # duplicate on Experian
        src = next(x for x in accounts if x.bureau == "Experian")
        accounts.append(Account(**{**src.to_dict(), "opened": src.opened, "first_delinquency": src.first_delinquency}))
    for i, a in enumerate(accounts):
        a.id = f"a{i}"
    return Report(Consumer("Sam Example", "1 Test Street, Springfield, NY 10001", today), accounts)


def _money(x: float) -> str:
    return f"${x:,.0f}"


def render(report: Report, layout: str) -> str:
    """Write the report as text in one of three bureau-like layouts."""
    c = report.consumer
    out = [f"Prepared for: {c.name}", f"Address on file: {c.address}", f"Date of report: {c.report_date:%B %d, %Y}", ""]
    for b in BUREAUS:
        accts = report.by_bureau(b)
        out.append(f"===== {b.upper()} =====")
        if layout == "table":
            out.append("Creditor | Acct # | Type | Status | Balance | Limit | Opened | DOFD")
            for a in accts:
                out.append(" | ".join([a.creditor, f"XXXX-XXXX-{a.last4}", a.kind.title(), a.status, _money(a.balance),
                                       _money(a.limit) if a.limit else "N/A", f"{a.opened:%m/%Y}" if a.opened else "",
                                       f"{a.first_delinquency:%m/%Y}" if a.first_delinquency else ""]))
        elif layout == "narrative":
            for a in accts:
                s = (f"{a.creditor.upper()} (account ending {a.last4}) is a {a.kind} account opened in "
                     f"{a.opened:%B %Y}. Status: {a.status}. The current balance is {_money(a.balance)}")
                s += f" on a credit limit of {_money(a.limit)}." if a.limit else "."
                if a.first_delinquency:
                    s += f" This account first became delinquent in {a.first_delinquency:%B %Y}."
                out.append(s)
        else:
            for a in accts:
                out += [f"Account Name: {a.creditor}", f"Account Number: ****{a.last4}", f"Account Type: {a.kind}",
                        f"Pay Status: {a.status}", f"Balance: {_money(a.balance)}",
                        f"High Credit/Limit: {_money(a.limit) if a.limit else '-'}",
                        f"Date Opened: {a.opened:%m/%d/%Y}" if a.opened else "Date Opened: -",
                        f"Date of First Delinquency: {a.first_delinquency:%m/%d/%Y}" if a.first_delinquency else
                        "Date of First Delinquency: -", "-" * 30]
        out.append("")
    return "\n".join(out)


# ── Scoring ──────────────────────────────────────────────────────────────────
def _same(field_: str, t: Account, p: Account) -> bool:
    tv, pv = getattr(t, field_), getattr(p, field_)
    if field_ in ("balance", "limit"):
        return abs(tv - pv) < 1
    if field_ in ("opened", "first_delinquency"):
        return (tv and pv and (tv.year, tv.month) == (pv.year, pv.month)) or (not tv and not pv)
    if field_ == "creditor":
        return norm(tv) == norm(pv)
    if field_ == "status":
        return norm(tv).split()[:1] == norm(pv).split()[:1]
    return tv == pv


@dataclass
class Score:
    truth: int = 0
    predicted: int = 0
    matched: int = 0
    field_hits: dict = field(default_factory=lambda: {f: 0 for f in FIELDS})
    flags_truth: int = 0
    flags_pred: int = 0
    flags_agree: int = 0
    misses: list = field(default_factory=list)

    def add(self, other: "Score") -> "Score":
        for k in ("truth", "predicted", "matched", "flags_truth", "flags_pred", "flags_agree"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        for f in FIELDS:
            self.field_hits[f] += other.field_hits[f]
        self.misses += other.misses
        return self

    @property
    def recall(self):
        return self.matched / self.truth if self.truth else 1.0

    @property
    def precision(self):
        return self.matched / self.predicted if self.predicted else 1.0

    def field_acc(self, f):
        return self.field_hits[f] / self.matched if self.matched else 0.0

    @property
    def flag_f1(self):
        if not self.flags_truth and not self.flags_pred:
            return 1.0
        return 2 * self.flags_agree / (self.flags_truth + self.flags_pred)


def _flag_keys(report: Report) -> list:
    return sorted((f.rule, f.account.bureau, f.account.last4) for f in run_rules(report))


def score(truth: Report, pred: Report) -> Score:
    s = Score(truth=len(truth.accounts), predicted=len(pred.accounts))
    pool = list(pred.accounts)
    for t in truth.accounts:
        m = next((p for p in pool if p.bureau == t.bureau and p.last4 == t.last4), None)
        if not m:
            s.misses.append(f"missed {t.label()} ({t.bureau})")
            continue
        pool.remove(m)
        s.matched += 1
        for f in FIELDS:
            if _same(f, t, m):
                s.field_hits[f] += 1
            else:
                s.misses.append(f"{t.label()} ({t.bureau}) {f}: expected {getattr(t, f)!r}, got {getattr(m, f)!r}")
    ft, fp = _flag_keys(truth), _flag_keys(pred)
    s.flags_truth, s.flags_pred = len(ft), len(fp)
    rest = list(fp)
    for k in ft:
        if k in rest:
            rest.remove(k)
            s.flags_agree += 1
    return s


def run(extractor, n_reports: int = 9, seed: int = 7) -> dict[str, Score]:
    """extractor(text) -> Report. Returns a Score per layout plus 'all'."""
    results = {lay: Score() for lay in LAYOUTS}
    for i in range(n_reports):
        layout = LAYOUTS[i % len(LAYOUTS)]
        truth = synthetic_report(seed + i)
        results[layout].add(score(truth, extractor(render(truth, layout))))
    total = Score()
    for sc in results.values():
        total.add(sc)
    results["all"] = total
    return results


def to_markdown(results: dict[str, Score], model: str) -> str:
    cols = list(results)
    lines = [f"Model: `{model}`", "", "| Metric | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    rows = [("Accounts found (recall)", lambda s: s.recall), ("No made-up accounts (precision)", lambda s: s.precision)]
    rows += [(f"`{f}` correct", (lambda f: lambda s: s.field_acc(f))(f)) for f in FIELDS]
    rows += [("**Same disputes flagged (F1)**", lambda s: s.flag_f1)]
    for name, fn in rows:
        lines.append(f"| {name} | " + " | ".join(f"{fn(results[c]):.0%}" for c in cols) + " |")
    return "\n".join(lines)
