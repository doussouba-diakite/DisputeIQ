"""Data shapes shared by every step of the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

BUREAUS = ("Equifax", "Experian", "TransUnion")
NEGATIVE_TYPES = {"collection", "charge-off", "late", "bankruptcy", "repossession", "foreclosure"}


@dataclass
class Account:
    bureau: str                       # Equifax | Experian | TransUnion
    creditor: str
    last4: str = ""                   # last 4 of the account number; never store the full number
    kind: str = "revolving"           # revolving | installment | mortgage | collection | charge-off | bankruptcy | other
    status: str = ""                  # as printed, e.g. "Paid, closed" or "Charged off"
    balance: float = 0.0
    opened: date | None = None
    first_delinquency: date | None = None   # FCRA reporting clock starts here (+180 days)
    original_creditor: str = ""       # for collections: who the debt came from
    id: str = ""

    @property
    def negative(self) -> bool:
        s = self.status.lower()
        return self.kind in NEGATIVE_TYPES or any(w in s for w in ("charged off", "collection", "late", "past due"))

    def label(self) -> str:
        tail = f" ••{self.last4}" if self.last4 else ""
        return f"{self.creditor}{tail}"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("opened", "first_delinquency"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class Consumer:
    name: str = ""
    address: str = ""
    report_date: date = field(default_factory=date.today)


@dataclass
class Report:
    consumer: Consumer
    accounts: list[Account]

    def by_bureau(self, bureau: str) -> list[Account]:
        return [a for a in self.accounts if a.bureau == bureau]


@dataclass
class Flag:
    account: Account
    rule: str                 # short name, e.g. "Duplicate account"
    reason: str               # plain-English explanation shown to the user and used in the letter
    law: str                  # citation, e.g. "FCRA § 611 (15 U.S.C. § 1681i)"
    strength: int             # 1-3: how strong the dispute is on the facts
    request: str = "delete"   # delete | correct
    approved: bool = False
