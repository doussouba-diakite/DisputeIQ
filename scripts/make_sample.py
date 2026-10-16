"""Generate samples/sample_report.pdf: a FICTIONAL tri-bureau credit report.

Every name, number, and company here is made up. The report is built to contain
one example of each error the rules engine catches, plus some clean accounts.
Usage: python scripts/make_sample.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "samples" / "sample_report.pdf"
HEADER = "Creditor | Acct | Type | Status | Balance | Opened | First delinquency | Original creditor"

ROWS = {
    "EQUIFAX": [
        "Hudson Federal Credit | 4421 | revolving | Open, current | $450 | 2021-03 | — | —",
        "Dept of Education Servicing | 0091 | installment | Current | $12,400 | 2024-09 | — | —",
        "Midtown Collections | 7781 | collection | Collection | $820 | 2018-06 | 2017-11 | Metro Card Services",
        "City Wireless | 5502 | charge-off | Charged off | $640 | 2020-01 | 2023-02 | —",
        "Apex Recovery Group | 9921 | collection | Collection | $640 | 2023-09 | 2023-02 | City Wireless",
        "Brooklyn Style Card | 2290 | revolving | Paid, closed | $0 | 2019-05 | — | —",
    ],
    "EXPERIAN": [
        "Hudson Federal Credit | 4421 | revolving | Open, current | $450 | 2021-03 | — | —",
        "Dept of Education Servicing | 0091 | installment | Current | $12,400 | 2024-09 | — | —",
        "Apex Recovery Group | 9921 | collection | Collection | $640 | 2023-09 | 2023-02 | City Wireless",
        "Apex Recovery Group | 9921 | collection | Collection | $640 | 2023-09 | 2023-02 | City Wireless",
        "QuickCash Lending | 6604 | installment | Open, 60 days late | $2,300 | 2025-11 | 2026-06 | —",
        "Brooklyn Style Card | 2290 | revolving | Paid, closed | $0 | 2019-05 | — | —",
    ],
    "TRANSUNION": [
        "Hudson Federal Credit | 4421 | revolving | Open, 30 days late | $1,250 | 2021-03 | 2026-08 | —",
        "Dept of Education Servicing | 0091 | installment | Current | $12,400 | 2024-09 | — | —",
        "Midtown Collections | 7781 | collection | Collection | $820 | 2018-06 | 2017-11 | Metro Card Services",
        "Northside Auto Finance | 1187 | installment | Paid, closed | $1,200 | 2020-07 | — | —",
        "Brooklyn Style Card | 2290 | revolving | Paid, closed | $0 | 2019-05 | — | —",
    ],
}


def main():
    OUT.parent.mkdir(exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=letter)
    w, h = letter
    y = h - 0.7 * inch

    def line(text, size=8.5, font="Courier", gap=13):
        nonlocal y
        if y < 0.8 * inch:
            c.showPage()
            y = h - 0.7 * inch
        c.setFont(font, size)
        c.drawString(0.6 * inch, y, text)
        y -= gap

    line("FICTIONAL SAMPLE CREDIT REPORT - NOT A REAL PERSON - FOR DEMO ONLY", 11, "Helvetica-Bold", 20)
    line("CONSUMER: Jordan Rivera", 10, "Helvetica")
    line("ADDRESS: 120 Example Ave, Apt 4B, Brooklyn, NY 11201", 10, "Helvetica")
    line("REPORT DATE: 2026-10-01", 10, "Helvetica", 22)
    for bureau, rows in ROWS.items():
        line(bureau, 12, "Helvetica-Bold", 16)
        line(HEADER, 7.5, "Courier-Bold")
        for r in rows:
            line(r, 7.5)
        y -= 10
    c.save()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
