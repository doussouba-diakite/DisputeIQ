"""Score the AI extraction on synthetic reports. See disputeiq/evals.py.

    python scripts/run_evals.py                      # Claude, 9 reports (needs ANTHROPIC_API_KEY)
    python scripts/run_evals.py --extractor oracle   # no key; checks the harness itself (should be 100%)
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from disputeiq import evals  # noqa: E402
from disputeiq.extract import MODEL, extract_with_claude  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--extractor", choices=["claude", "oracle"], default="claude")
p.add_argument("--reports", type=int, default=9)
args = p.parse_args()

if args.extractor == "oracle":
    truths = {}

    def extractor(text):  # returns the answer key: proves scoring is right
        return truths[text]

    for i in range(args.reports):
        t = evals.synthetic_report(7 + i)
        truths[evals.render(t, evals.LAYOUTS[i % 3])] = t
    model = "oracle (answer key)"
else:
    def extractor(text):
        print("  extracting…", flush=True)
        return extract_with_claude(text)
    model = MODEL

results = evals.run(extractor, args.reports)
md = f"# Extraction eval · {date.today():%B %d, %Y}\n\n" + evals.to_markdown(results, model)
misses = results["all"].misses
if misses:
    md += "\n\n## Mistakes\n" + "\n".join(f"- {m}" for m in misses[:40])
print(md)
out = ROOT / "evals" / f"results_{args.extractor}.md"
out.parent.mkdir(exist_ok=True)
out.write_text(md + "\n")
print(f"\nSaved {out.relative_to(ROOT)}")
