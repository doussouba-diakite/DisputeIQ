"""DisputeIQ: find provable errors on your credit report and get mail-ready dispute letters."""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from disputeiq.coach import factors, pay_to_reach, reminders_ics, suggest, unique_accounts, utilization
from disputeiq.extract import load_report
from disputeiq.letters import ADDRESSES, build_letters_pdf
from disputeiq.models import BUREAUS
from disputeiq.rules import GRACE, SEVEN_YEARS, TEN_YEARS, key, run_rules
from disputeiq.tracker import Dispute

st.set_page_config(page_title="DisputeIQ", page_icon="🛡️", layout="wide")
SAMPLE = Path(__file__).parent / "samples" / "sample_report.pdf"
EMERALD, AMBER, RED, SLATE = "#10b981", "#f59e0b", "#f43f5e", "#94a3b8"
STRENGTH = {3: ("Strong", EMERALD), 2: ("Good", AMBER), 1: ("Weak", SLATE)}

st.markdown("""
<style>
.block-container {padding-top: 1.3rem; max-width: 1250px;}
.hero {background: radial-gradient(circle at 15% 20%, rgba(16,185,129,.35), transparent 45%),
       radial-gradient(circle at 85% 0%, rgba(56,189,248,.25), transparent 40%), #0f172a;
       border: 1px solid rgba(148,163,184,.18); border-radius: 22px; padding: 28px 32px; margin-bottom: 18px;}
.hero h1 {margin: 0; font-size: 2.4rem; letter-spacing: -1px;}
.hero h1 span {color: #10b981;}
.hero p {color: #cbd5e1; margin: 6px 0 0;}
.stats {display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 18px;}
.stat {background: rgba(15,23,42,.7); border: 1px solid rgba(148,163,184,.18); border-radius: 14px; padding: 12px 16px;}
.stat b {font-size: 1.7rem; display: block;} .stat span {color: #94a3b8; font-size: .82rem;}
.card {background: #111a2e; border: 1px solid rgba(148,163,184,.16); border-radius: 16px; padding: 16px 18px;
       margin-bottom: 10px;}
.card h4 {margin: 2px 0 4px; font-size: 1.02rem;} .muted {color: #94a3b8; font-size: .86rem;}
.pill {display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .72rem; font-weight: 700;
       letter-spacing: .4px; margin-right: 6px; color: #0b1220;}
.law {font-size: .78rem; color: #7dd3fc; margin-top: 6px;}
.meter {display:flex; gap:4px; margin-top:8px;} .meter i {flex:1; height:6px; border-radius:9px; background:#1e293b;}
.step {color:#10b981; font-weight:700; font-size:.8rem; letter-spacing:2px; text-transform:uppercase;}
</style>""", unsafe_allow_html=True)


def api_key() -> str | None:
    try:
        return st.secrets.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


@st.cache_data(show_spinner=False)
def read_report(data: bytes, key_: str | None):
    return load_report(data, key_)


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🛡️ DisputeIQ")
    st.caption("Fix errors on your credit report. Build your credit. Never miss a payment.")
    source = st.radio("Report", ["Try the sample report", "Upload my report (PDF)"], label_visibility="collapsed")
    data = None
    if source.startswith("Try"):
        data = SAMPLE.read_bytes()
        st.info("A **fictional** report for Jordan Rivera, built to include one of each common error.")
    else:
        up = st.file_uploader("Credit report PDF", type="pdf",
                              help="Get yours free at annualcreditreport.com")
        user_key = st.text_input("Anthropic API key", type="password",
                                 help="Used once to read your report. Not stored.") if not api_key() else None
        if up:
            data = up.read()
        st.caption("🔒 Your report is processed in memory and never saved. Never upload someone else's report.")
    st.markdown("---")
    st.caption("Not legal advice. Disputing is free; you never need to pay anyone to do it.")

if not data:
    st.markdown('<div class="hero"><h1>Dispute<span>IQ</span></h1><p>Upload a credit report from '
                'annualcreditreport.com, or try the sample on the left.</p></div>', unsafe_allow_html=True)
    st.stop()

try:
    with st.spinner("Reading your report…"):
        report = read_report(data, api_key() or (locals().get("user_key") or None))
except Exception as e:  # missing key, bad PDF, API error
    st.error(f"Couldn't read that report: {e}")
    st.stop()

if not report.accounts:
    st.warning("No accounts found in that file. Is it a credit report PDF?")
    st.stop()

today = report.consumer.report_date
accounts = report.accounts
labels = {a.id: f"{a.label()} ({a.bureau})" for a in accounts}

# "Not mine" picks live in session state so the flags tab can use them.
disowned_ids = st.session_state.get("disowned", [])
disowned = [a for a in accounts if a.id in disowned_ids]
flags = run_rules(report, disowned)
flagged_ids = {id(f.account) for f in flags}

hello = report.consumer.name.split()[0] if report.consumer.name else "there"
st.markdown(f"""
<div class="hero"><h1>Dispute<span>IQ</span></h1>
<p>Hi {hello}. Here's what we found on your report from {today:%B %-d, %Y}.</p>
<div class="stats">
 <div class="stat"><b>{len(accounts)}</b><span>accounts across {len({a.bureau for a in accounts})} bureaus</span></div>
 <div class="stat"><b style="color:{AMBER}">{len(flags)}</b><span>possible errors found</span></div>
 <div class="stat"><b style="color:{EMERALD}">{sum(f.strength == 3 for f in flags)}</b><span>strong disputes</span></div>
 <div class="stat"><b>${sum(f.account.balance for f in flags if f.request == 'delete'):,.0f}</b><span>in balances you can ask to remove</span></div>
</div></div>""", unsafe_allow_html=True)

t_report, t_issues, t_letters, t_track, t_coach = st.tabs(
    ["① Your report", f"② Issues ({len(flags)})", "③ Letters", "④ 30-day tracker", "⑤ Credit coach"])

# ── 1. Report ────────────────────────────────────────────────────────────────
with t_report:
    st.markdown('<div class="step">Step 1 · See everything in one place</div>', unsafe_allow_html=True)
    st.markdown("#### Your accounts over time")
    st.caption("Each bar runs from when the account opened. ⚠️ amber = a possible error. The dashed marker is when "
               "a negative item legally has to come off your report.")
    rows = sorted({(key(a), a.creditor, a.last4) for a in accounts}, key=lambda r: r[1])
    fig = go.Figure()
    for k, creditor, last4 in rows:
        group = [a for a in accounts if key(a) == k]
        a0 = group[0]
        name = f"{creditor} ••{last4}" if last4 else creditor
        bad = any(id(a) in flagged_ids for a in group)
        start = a0.opened or today - timedelta(days=365)
        fig.add_trace(go.Bar(y=[name], x=[(today - start).days * 86400000], base=[start.isoformat()],
                             orientation="h", marker_color=AMBER if bad else EMERALD, opacity=.85,
                             hovertext=f"{name}<br>{a0.status}<br>${a0.balance:,.0f}", hoverinfo="text",
                             showlegend=False))
        if a0.negative and a0.first_delinquency:
            limit = TEN_YEARS if a0.kind == "bankruptcy" else SEVEN_YEARS
            off = a0.first_delinquency + GRACE + limit
            fig.add_trace(go.Scatter(y=[name], x=[off.isoformat()], mode="markers",
                                     marker=dict(symbol="line-ns-open", size=22, color=RED, line=dict(width=3)),
                                     hovertext=f"Must come off by {off:%b %Y}", hoverinfo="text", showlegend=False))
    fig.add_vline(x=today.isoformat(), line_dash="dot", line_color=SLATE)
    fig.update_layout(height=60 + 46 * len(rows), barmode="overlay", margin=dict(l=0, r=0, t=10, b=0),
                      xaxis=dict(type="date", gridcolor="#1e293b"), yaxis=dict(autorange="reversed"),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", font_color="#e2e8f0")
    st.plotly_chart(fig, width="stretch")

    st.markdown("#### Same account, three bureaus")
    st.caption("Balances should match across bureaus. Cells that don't match are highlighted.")
    grid = {}
    for a in accounts:
        name = a.label()
        grid.setdefault(name, {})
        grid[name][a.bureau] = f"${a.balance:,.0f} · {a.status}"
    table = pd.DataFrame(grid).T.reindex(columns=list(BUREAUS)).fillna("—")

    def shade(row):
        vals = {v.split(" · ")[0] for v in row if v != "—"}
        return ["background-color: rgba(245,158,11,.22)" if len(vals) > 1 and v != "—" else "" for v in row]

    st.dataframe(table.style.apply(shade, axis=1), width="stretch")

    st.markdown("#### Any accounts you don't recognize?")
    st.multiselect("Mark accounts you never opened. We'll add them to your disputes.",
                   options=[a.id for a in accounts], format_func=lambda i: labels[i], key="disowned")

# ── 2. Issues ────────────────────────────────────────────────────────────────
with t_issues:
    st.markdown('<div class="step">Step 2 · You decide what to dispute</div>', unsafe_allow_html=True)
    if not flags:
        st.success("No provable errors found. Nice. Check again after your next report.")
    else:
        st.caption("Each issue was found by a rule you can check, and cites the law it's based on. "
                   "Turn on the ones you want in your letters. Only dispute what's really wrong.")
        for i, f in enumerate(flags):
            word, color = STRENGTH[f.strength]
            bars = "".join(f'<i style="background:{color if n < f.strength else "#1e293b"}"></i>' for n in range(3))
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(f"""<div class="card">
                    <span class="pill" style="background:{color}">{word.upper()}</span>
                    <span class="muted">{f.account.bureau} · {f.rule}</span>
                    <h4>{f.account.label()}</h4>
                    <div>{f.reason}</div>
                    <div class="law">⚖️ {f.law}</div>
                    <div class="meter">{bars}</div></div>""", unsafe_allow_html=True)
            with c2:
                st.toggle("Dispute", value=f.strength >= 2, key=f"ok_{i}")

# ── 3. Letters ───────────────────────────────────────────────────────────────
with t_letters:
    st.markdown('<div class="step">Step 3 · Print, sign, mail</div>', unsafe_allow_html=True)
    approved = [f for i, f in enumerate(flags) if st.session_state.get(f"ok_{i}", f.strength >= 2)]
    if not approved:
        st.info("Turn on at least one issue in Step 2.")
    else:
        cols = st.columns(3)
        for col, bureau in zip(cols, BUREAUS):
            mine = [f for f in approved if f.account.bureau == bureau]
            with col:
                st.markdown(f"""<div class="card"><h4>{bureau}</h4>
                    <div class="muted">{'<br>'.join(ADDRESSES[bureau])}</div>
                    <div style="margin-top:10px;font-size:1.6rem;font-weight:700;color:{EMERALD if mine else SLATE}">
                    {len(mine)}</div><div class="muted">item{'s' if len(mine) != 1 else ''} to dispute</div></div>""",
                            unsafe_allow_html=True)
        pdf = build_letters_pdf(approved, report.consumer, date.today())
        st.download_button("⬇️ Download letters + mailing checklist (PDF)", pdf, "dispute_letters.pdf",
                           "application/pdf", type="primary", width="stretch")
        st.caption("Each letter lists every error separately, with the facts and the law, as the CFPB recommends. "
                   "We never ask for your SSN or birth date: you write those in by hand.")

# ── 4. Tracker ───────────────────────────────────────────────────────────────
with t_track:
    st.markdown('<div class="step">Step 4 · Hold them to the deadline</div>', unsafe_allow_html=True)
    st.caption("Bureaus have 30 days from the day they receive your letter (FCRA § 611). Enter dates from your "
               "certified-mail receipts.")
    base = pd.DataFrame({"Bureau": list(BUREAUS), "Mailed": [None] * 3, "Received": [None] * 3,
                         "Sent more info": [False] * 3, "Result": [""] * 3})
    edited = st.data_editor(
        base, hide_index=True, width="stretch", key="tracker",
        column_config={"Mailed": st.column_config.DateColumn(), "Received": st.column_config.DateColumn(),
                       "Result": st.column_config.SelectboxColumn(options=["", "deleted", "corrected", "verified"])})
    cards = st.columns(3)
    for col, (_, r) in zip(cards, edited.iterrows()):
        if pd.isna(r["Mailed"]) or r["Mailed"] is None:
            label, step, pct = "Not mailed", "Download and mail your letter.", 0
        else:
            d = Dispute(r["Bureau"], r["Mailed"], None if pd.isna(r["Received"]) else r["Received"],
                        bool(r["Sent more info"]), r["Result"] or "")
            label, step = d.status(date.today())
            dl = d.deadline()
            pct = 100 if d.result else (0 if not dl else
                                        max(0, min(100, 100 - (dl - date.today()).days / 30 * 100)))
        with col:
            st.markdown(f"""<div class="card"><h4>{r['Bureau']}</h4>
                <div style="font-size:1.4rem;font-weight:700">{label}</div>
                <div class="meter"><i style="flex:{pct};background:{EMERALD}"></i><i style="flex:{100 - pct}"></i></div>
                <div class="muted" style="margin-top:8px">{step}</div></div>""", unsafe_allow_html=True)

# ── 5. Credit coach ──────────────────────────────────────────────────────────
GRADE = {"Good": EMERALD, "Okay": AMBER, "Needs work": RED}
with t_coach:
    st.markdown('<div class="step">Keep building · Your credit, factor by factor</div>', unsafe_allow_html=True)
    st.caption("FICO scores weigh five factors (myfico.com). We can't compute your exact score, but we can show "
               "where your report is strong and where to focus.")
    fs = factors(report)
    cols = st.columns(len(fs))
    for col, f in zip(cols, fs):
        with col:
            st.markdown(f"""<div class="card" style="min-height:270px">
                <div class="muted">{f.weight}% of score</div>
                <div class="meter"><i style="flex:{f.weight};background:#38bdf8"></i><i style="flex:{100 - f.weight}"></i></div>
                <h4 style="margin-top:10px">{f.name}</h4>
                <div style="font-size:{'1.6rem' if len(f.value) < 8 else '1.15rem'};font-weight:700;color:{GRADE[f.grade]}">{f.value}</div>
                <span class="pill" style="background:{GRADE[f.grade]}">{f.grade.upper()}</span>
                <div class="muted" style="margin-top:8px">{f.detail}</div>
                <div style="margin-top:8px;font-size:.88rem">💡 {f.tip}</div></div>""", unsafe_allow_html=True)

    st.markdown("#### Credit card utilization")
    u, per_card = utilization(report)
    if u is None:
        st.info("No open credit cards with a limit found. A secured card is the simplest way to start.")
    else:
        g1, g2 = st.columns([1, 1.4])
        with g1:
            gauge = go.Figure(go.Indicator(
                mode="gauge+number", value=round(u * 100), number={"suffix": "%", "font": {"color": "#e2e8f0"}},
                gauge={"axis": {"range": [0, 100], "tickcolor": SLATE}, "bar": {"color": "#e2e8f0", "thickness": .25},
                       "bgcolor": "#111a2e", "borderwidth": 0,
                       "steps": [{"range": [0, 10], "color": EMERALD}, {"range": [10, 30], "color": "#0f766e"},
                                 {"range": [30, 50], "color": AMBER}, {"range": [50, 100], "color": RED}]}))
            gauge.update_layout(height=250, margin=dict(l=35, r=40, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)",
                                font_color="#e2e8f0")
            st.plotly_chart(gauge, width="stretch")
            st.caption("Under 30% is the common guideline; under 10% is even better. When bureaus disagree, "
                       "we use the highest balance.")
        with g2:
            for a, r_ in per_card:
                color = EMERALD if r_ < .1 else "#0f766e" if r_ < .3 else AMBER if r_ < .5 else RED
                st.markdown(f"""<div class="card"><b>{a.label()}</b>
                    <span class="muted"> · ${a.balance:,.0f} of ${a.limit:,.0f}</span>
                    <div class="meter"><i style="flex:{min(100, r_ * 100):.0f};background:{color}"></i>
                    <i style="flex:{max(0, 100 - r_ * 100):.0f}"></i></div>
                    <div class="muted" style="margin-top:6px">{r_:.0%} used · pay ${max(0, a.balance - .3 * a.limit):,.0f}
                    to get this card under 30%</div></div>""", unsafe_allow_html=True)
            if u >= .1:
                st.markdown(f"**Plan:** pay **\\${pay_to_reach(report, .30):,.0f}** across your cards to get under 30% "
                            f"overall, or **\\${pay_to_reach(report, .10):,.0f}** to get under 10%. Pay before the "
                            "statement closing date: that's the balance that gets reported.")

    st.markdown("#### Never miss a payment")
    st.caption("Add each account's due day. Download one calendar file: it adds monthly reminders, 3 days early, to "
               "your iPhone, Google, or Outlook calendar.")
    payable = [a for a in unique_accounts(report) if not a.negative and a.balance > 0
               and not any(w in a.status.lower() for w in ("closed", "paid"))]
    plan = pd.DataFrame({"Account": [a.label() for a in payable], "Due day": [15] * len(payable),
                         "Remind me": [True] * len(payable)})
    plan = st.data_editor(plan, hide_index=True, width="stretch", key="due_days", disabled=["Account"],
                          column_config={"Due day": st.column_config.NumberColumn(min_value=1, max_value=31, step=1)})
    chosen = [(r["Account"], int(r["Due day"])) for _, r in plan.iterrows() if r["Remind me"]]
    if chosen:
        st.download_button(f"📅 Add {len(chosen)} payment reminder{'s' if len(chosen) != 1 else ''} to my calendar",
                           reminders_ics(chosen), "payment_reminders.ics", "text/calendar", width="stretch")

    st.markdown("#### Ways to build credit, picked for your report")
    st.caption("Not sponsored. DisputeIQ isn't paid by any company listed. Details checked October 2026; confirm on "
               "each site before signing up.")
    sug = suggest(report)
    sc = st.columns(min(3, len(sug)))
    for i, s_ in enumerate(sug):
        with sc[i % len(sc)]:
            st.markdown(f"""<div class="card" style="min-height:300px"><h4>{s_.title}</h4>
                <div class="muted">{s_.what}</div>
                <div style="margin-top:8px"><b>Cost:</b> {s_.cost}</div>
                <div style="margin-top:6px;color:{EMERALD}"><b>Why you:</b> {s_.why_you}</div>
                <div style="margin-top:6px;color:{AMBER}"><b>Watch out:</b> {s_.watch_out}</div>
                <div style="margin-top:8px"><a href="{s_.url}" target="_blank">Learn more ↗</a></div></div>""",
                        unsafe_allow_html=True)

st.markdown("---")
st.caption("DisputeIQ by Doussouba Diakite · Rules cite the Fair Credit Reporting Act (15 U.S.C. § 1681) · "
           "Not legal advice · Free ways to dispute: annualcreditreport.com, each bureau's website, "
           "consumerfinance.gov/complaint")
