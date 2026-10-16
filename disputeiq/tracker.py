"""The 30-day clock.

FCRA § 611(a)(1)(A): the bureau must finish its reinvestigation within 30 days of
RECEIVING the dispute. § 611(a)(1)(B): up to 15 more days if the consumer sends
additional relevant information during those 30 days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

BASE_DAYS = 30
EXTENSION_DAYS = 15


@dataclass
class Dispute:
    bureau: str
    mailed: date
    received: date | None = None       # from the certified-mail return receipt
    sent_more_info: bool = False
    result: str = ""                   # "", deleted, corrected, verified, no response

    def deadline(self) -> date | None:
        if not self.received:
            return None
        return self.received + timedelta(days=BASE_DAYS + (EXTENSION_DAYS if self.sent_more_info else 0))

    def status(self, today: date) -> tuple[str, str]:
        """(label, next step)."""
        if self.result:
            return self.result.title(), "Check your updated report to confirm the change."
        if not self.received:
            return "In the mail", "Enter the date on your green return-receipt card when it comes back."
        left = (self.deadline() - today).days
        if left >= 0:
            return f"{left} days left", "The bureau is investigating. Watch your mail and email."
        return "Overdue", ("The deadline passed with no answer. Send a follow-up letter and consider "
                           "filing a complaint at consumerfinance.gov/complaint.")
