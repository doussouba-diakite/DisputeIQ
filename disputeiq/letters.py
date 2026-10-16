"""Mail-ready dispute letters, one per bureau.

Structure follows the CFPB's guidance: who you are, a clear statement that you are
disputing, each error described separately with the facts and what you want done,
and copies (never originals) of supporting documents.

The app never asks for a Social Security number or date of birth. The letter has
blank lines for the consumer to fill in by hand before mailing.
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .models import Consumer, Flag

# Checked October 2026 on each bureau's own "dispute by mail" page.
ADDRESSES = {
    "Equifax": ["Equifax Information Services LLC", "P.O. Box 740256", "Atlanta, GA 30374-0256"],
    "Experian": ["Experian", "P.O. Box 4500", "Allen, TX 75013"],
    "TransUnion": ["TransUnion Consumer Solutions", "P.O. Box 2000", "Chester, PA 19016-2000"],
}

ENCLOSURES = [
    "Copy of a government-issued photo ID",
    "Copy of a recent bill or bank statement showing my current address",
    "Copy of my credit report with the disputed items circled",
    "Copies of any supporting documents (payment records, letters, police or FTC identity theft report)",
]

CHECKLIST = [
    "Fill in your date of birth, last 4 of your SSN, and sign each letter.",
    "Attach COPIES of the enclosures listed. Keep all originals.",
    "Make a copy of the signed letter for your records.",
    "Send by USPS Certified Mail with Return Receipt, so you have proof of the date it arrived.",
    "When the green return-receipt card comes back, enter the date in DisputeIQ's tracker. "
    "The bureau has 30 days from that date to finish its investigation (FCRA § 611).",
]


def _styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Times-Roman", fontSize=11, leading=15)
    return {
        "body": body,
        "bold": ParagraphStyle("bb", parent=body, fontName="Times-Bold"),
        "item": ParagraphStyle("i", parent=body, leftIndent=14, spaceBefore=6),
        "small": ParagraphStyle("s", parent=body, fontSize=9, leading=12, textColor=colors.HexColor("#555555")),
        "h": ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=8),
    }


def letter_story(bureau: str, flags: list[Flag], consumer: Consumer, today: date, st: dict) -> list:
    s = []
    s.append(Paragraph(consumer.name or "Your full name", st["bold"]))
    for part in (consumer.address or "Your street address, City, State ZIP").split(", ", 1):
        s.append(Paragraph(part, st["body"]))
    s += [Paragraph("Date of birth: ____________ &nbsp;&nbsp; Last 4 of SSN: ________", st["body"]),
          Spacer(1, 12), Paragraph(f"{today:%B %d, %Y}", st["body"]), Spacer(1, 12)]
    s += [Paragraph(line, st["body"]) for line in ADDRESSES[bureau]]
    s += [Spacer(1, 14), Paragraph("<b>Re: Request to investigate and correct errors in my credit report</b>", st["body"]),
          Spacer(1, 10), Paragraph("To whom it may concern:", st["body"]), Spacer(1, 6),
          Paragraph(f"I am writing to dispute the following information in my {bureau} credit report. "
                    f"The items listed below are inaccurate or incomplete. I am asking you to investigate each "
                    f"one, correct or delete it as described, and send me the results and an updated copy of my "
                    f"report.", st["body"])]
    for i, f in enumerate(flags, 1):
        a = f.account
        acct = f"account ending in {a.last4}" if a.last4 else "account"
        ask = "delete this item" if f.request == "delete" else "correct this item so it is accurate"
        s.append(KeepTogether([
            Paragraph(f"<b>{i}. {a.creditor}, {acct}: {f.rule}</b>", st["item"]),
            Paragraph(f"{f.reason} Please {ask}.", st["item"]),
            Paragraph(f"<i>Basis: {f.law}</i>", st["item"]),
        ]))
    s += [Spacer(1, 10),
          Paragraph("Under the Fair Credit Reporting Act, § 611 (15 U.S.C. § 1681i), you must complete your "
                    "reinvestigation within 30 days of receiving this letter. If any item cannot be verified, it "
                    "must be promptly deleted (§ 611(a)(5)(A)).", st["body"]),
          Spacer(1, 10), Paragraph("Enclosed are copies of:", st["body"])]
    s += [Paragraph(f"• {e}", st["item"]) for e in ENCLOSURES]
    s += [Spacer(1, 18), Paragraph("Sincerely,", st["body"]), Spacer(1, 30),
          Paragraph("_______________________________", st["body"]), Paragraph(consumer.name or "", st["body"])]
    return s


def build_letters_pdf(flags: list[Flag], consumer: Consumer, today: date | None = None) -> bytes:
    """One PDF: a checklist page, then one letter per bureau that has approved disputes."""
    today = today or date.today()
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=inch, rightMargin=inch,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch, title="Dispute letters")
    story = [Paragraph("Before you mail", st["h"])]
    story += [Paragraph(f"{i}. {c}", st["body"]) for i, c in enumerate(CHECKLIST, 1)]
    story += [Spacer(1, 12), Paragraph("Made with DisputeIQ. This is not legal advice. You can also dispute "
                                       "online with each bureau for free.", st["small"])]
    for bureau in ADDRESSES:
        mine = [f for f in flags if f.account.bureau == bureau]
        if mine:
            story.append(PageBreak())
            story += letter_story(bureau, mine, consumer, today, st)
    doc.build(story)
    return buf.getvalue()
