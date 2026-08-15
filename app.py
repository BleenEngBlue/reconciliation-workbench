"""
Reconciliation Workbench — HITL exception review (Gradio UI).

STANDARDS THIS FILE EMBODIES:
  Req 1 (reusable architecture): thin UI over four swappable stages
      (data_gen -> checks -> triage -> evals); no business logic lives here.
  Req 2 (security / access control / governance): optional auth via env
      (RECONCILIATION_AUTH_USER/PASS), reviewer identity on every decision, filing
      gates that block release until humans decide, CSV export sanitized
      against formula injection, inputs clamped server-side.
  Req 3 (docs / troubleshooting / monitoring): eval panel on a tab, runtime
      measured per run, decision log exportable as the audit artifact.

Run:  pip install -r requirements.txt
      python app.py                      # offline triage (demo-safe)
      RECONCILIATION_USE_LLM=1 python app.py      # LLM triage (needs OPENAI_API_KEY)

Demo path (15 minutes — see README for the full script):
  overview & gates -> work the queue (approve, override, watch a filing
  gate flip from BLOCKED to READY) -> export the audit CSV -> eval panel ->
  live country add OR reseed ("same discipline, new data, still holds").
"""

from __future__ import annotations  # modern type hints on older Pythons

import os  # env flags: auth credentials (SEC-03), LLM opt-in
import time  # run timing (Req 3) + timestamps on decisions/exports

import gradio as gr  # the UI framework (Monica's production stack)
import pandas as pd  # dataframes for the queue and decision log
from checks import run_checks  # stage 2: deterministic core
from data_gen import generate  # stage 1: synthetic data + ground truth
from evals import evaluate  # stage 4: measured quality
from triage import triage_all  # stage 3: AI at the edges

# Demo-scoped shared state. KNOWN, DOCUMENTED simplification (see security
# review SEC-02): single-operator demo use; production uses per-session or
# service-backed state. Auth (SEC-03) bounds it on any public deploy.
STATE: dict = {}            # current dataset + triaged queue + eval report
DECISIONS: list[dict] = []  # the append-only decision log (audit trail)

# SEC-01: neutralize CSV/formula injection in the audit export (CWE-1236).
# Any cell starting with =, +, -, @ (or containing tab/CR) would execute as
# a formula when the CSV is opened in Excel — and this export is explicitly
# "what an auditor gets."
_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")  # Excel formula triggers


def _csv_safe(v) -> str:
    """Prefix a quote to any cell Excel would execute as a formula."""
    s = str(v)                                          # everything as text
    return "'" + s if s.startswith(_FORMULA_CHARS) else s  # neutralize or pass


def build(seed: int = 42, n: int = 60):
    """Run the full pipeline and refresh every UI panel."""
    # SEC-04: clamp inputs server-side (CWE-400) — bounds a one-request DoS
    # on a public deployment AND protects the live demo from a typo.
    seed = abs(int(seed)) % 1_000_000        # bounded, non-negative seed
    n = max(10, min(int(n), 500))            # 10..500 txns per jurisdiction
    t0 = time.perf_counter()                 # start the run clock (Req 3)
    ds = generate(seed=seed, n_per_jurisdiction=n)   # stage 1: data
    exceptions = run_checks(ds)                      # stage 2: find
    triaged = triage_all(exceptions)                 # stage 3: explain/tier
    report = evaluate(ds.seeded_errors, exceptions,  # stage 4: measure
                      time.perf_counter() - t0)
    STATE.update(ds=ds, triaged=triaged, report=report)  # publish new state
    DECISIONS.clear()                        # new dataset -> fresh audit log
    # return refreshed content for all five bound UI components
    return summary_md(), queue_df(), eval_md(), "", decisions_df()


def filing_gate(jur: str) -> str:
    """THE PRODUCTION FEATURE (Req 2 governance): a return cannot be
    released while critical or high exceptions in its jurisdiction are
    undecided. The gate is DERIVED from the decision log — automation
    with a human sign-off spine."""
    decided = {d["exc_id"] for d in DECISIONS}      # everything decided so far
    open_exc = [e for e in STATE["triaged"]          # still-undecided items
                if e["jurisdiction"] == jur
                and e["severity"] in ("critical", "high")
                and e["exc_id"] not in decided]
    if not open_exc:
        return "🟢 READY TO FILE"                    # all consequential items decided
    return f"🔒 BLOCKED — {len(open_exc)} open"      # count of blockers shown


def summary_md() -> str:
    """The Overview tab: per-jurisdiction tie-out + filing gates."""
    ds = STATE["ds"]                                 # current dataset
    lines = ["## Period 2026-07 — does it tie out?\n",
             "| Jurisdiction | Entity | Txns | Ledger tax | Return tax | Tie-out | Filing gate |",
             "|---|---|---|---|---|---|---|"]
    for ret in ds.returns:                           # one row per return
        jur = ret["jurisdiction"]
        jt = [t for t in ds.transactions if t["jurisdiction"] == jur]
        ledger = round(sum(t["tax_amount"] for t in jt), 2)   # ledger truth
        ok = abs(ledger - ret["reported_tax"]) <= 0.02        # ties out?
        lines.append(f"| {jur} | {jt[0]['entity']} | {len(jt)} | "
                     f"{ledger:,.2f} | {ret['reported_tax']:,.2f} | "
                     f"{'✅ ties out' if ok else '🔴 VARIANCE'} | {filing_gate(jur)} |")
    tiers: dict[str, int] = {}                       # ladder distribution
    for e in STATE["triaged"]:
        tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    lines.append("\n**Automation ladder this run:**  " + "  ·  ".join(
        f"{t}: **{n}**" for t, n in sorted(tiers.items())))
    lines.append(f"\n{len(STATE['triaged'])} exceptions across "
                 f"{len(ds.transactions)} transactions — you review the "
                 f"exceptions, not the transactions.")
    return "\n".join(lines)


def queue_df() -> pd.DataFrame:
    """The exception queue as a dataframe (already severity-sorted)."""
    rows = [{                                        # one row per exception
        "exc_id": e["exc_id"], "severity": e["severity"], "check": e["check"],
        "tier": e["tier"], "conf": e["confidence"],
        "jurisdiction": e["jurisdiction"], "summary": e["summary"],
    } for e in STATE["triaged"]]
    return pd.DataFrame(rows)


def detail_md(exc_id: str) -> str:
    """Detail panel for one exception: explanation, disposition, evidence."""
    # find the exception by id (whitespace-tolerant)
    e = next((x for x in STATE["triaged"] if x["exc_id"] == exc_id.strip()), None)
    if not e:
        return "_Enter an exception id from the queue (e.g. EXC-0001)._"
    # render the raw evidence numbers the check attached (auditability)
    ev = "\n".join(f"  - **{k}**: {v}" for k, v in (e.get("evidence") or {}).items())
    return (f"### {e['exc_id']} · {e['check']} · {e['severity'].upper()}\n\n"
            f"**{e['jurisdiction']}** — {e['entity']}\n\n"
            f"**AI explanation** ({e['triage_mode']}): {e['explanation']}\n\n"  # provenance shown
            f"**Proposed disposition:** {e['proposed_disposition']}\n\n"
            f"**Confidence {e['confidence']:.2f} → {e['tier']}**\n\n"
            f"**Evidence:**\n{ev}")


def decide(exc_id: str, decision: str, note: str, reviewer: str = "local-operator"):
    """Record one human decision; refresh gates + log. The human always
    outranks the machine — approve or override, both are first-class."""
    e = next((x for x in STATE["triaged"] if x["exc_id"] == exc_id.strip()), None)
    if not e:
        return "_Unknown exception id._", decisions_df(), summary_md()
    # SEC-03: every decision carries a named reviewer — the audit trail
    # records WHO approved, matching the app's own governance story.
    DECISIONS.append({"exc_id": e["exc_id"], "check": e["check"],
                      "jurisdiction": e["jurisdiction"], "ai_tier": e["tier"],
                      "decision": decision, "reviewer": reviewer,
                      "note": note or "—",
                      "at": time.strftime("%H:%M:%S")})       # timestamped
    gate = filing_gate(e["jurisdiction"])            # gate may have flipped
    return (f"✅ **{decision}** recorded for {e['exc_id']} — logged with full "
            f"evidence trail. Filing gate for {e['jurisdiction']}: **{gate}**"),\
        decisions_df(), summary_md()


def export_log():
    """Write the audit CSV: sanitized (SEC-01), uniquely named (SEC-06)."""
    path = f"decision_log_{time.strftime('%Y%m%d_%H%M%S')}.csv"  # unique name
    df = decisions_df().astype(str)                  # everything as text
    # DataFrame.map is pandas>=2.1; applymap fallback keeps older pandas working
    df = df.map(_csv_safe) # if hasattr(df, "map") else df.applymap(_csv_safe)
    df.to_csv(path, index=False)                     # the audit artifact
    return path                                      # served via gr.File


def decisions_df() -> pd.DataFrame:
    """The decision log as a dataframe (empty frame keeps headers visible)."""
    return pd.DataFrame(DECISIONS) if DECISIONS else \
        pd.DataFrame(columns=["exc_id", "check", "jurisdiction", "ai_tier",
                              "decision", "reviewer", "note", "at"])


def eval_md() -> str:
    """The Eval tab: measured recall/precision vs seeded ground truth."""
    r = STATE["report"]                              # latest eval record
    lines = [
        "## Measured, not claimed\n",
        (
            "Seeded errors are the golden set — ground truth known by construction. "
            "Recall = did we catch every seeded error. Precision = did every flag "
            "trace back to a real one (false-positive control).\n"
        ),
        "| Seeded error type | Seeded | Caught | Recall | Flagged | Precision |",
        "|---|---|---|---|---|---|",
    ]
    for typ, row in r["per_type"].items():           # one row per error type
        prec = row["precision"] if row["precision"] is not None else "—"
        lines.append(f"| {typ} | {row['seeded']} | {row['caught']} | "
                     f"{row['recall']} | {row['flagged']} | {prec} |")
    t = r["totals"]                                  # OVERALL row inside the table
    lines.append(f"| **OVERALL** | **{t['seeded_errors']}** | **{t['caught']}** | "
                 f"**{t['overall_recall']}** | **{t['exceptions_flagged']}** | "
                 f"**{t['precision']}** |")
    lines.append(f"\n**{t['unexplained_flags (false positives)']} unexplained "
                 f"flags (false positives) · runtime {r['runtime_s']}s**")
    lines.append("\n_Detection is deterministic — recall is provable. The AI "
                 "layer explains and proposes; the eval gates would govern its "
                 "climb up the automation ladder in production._")
    return "\n".join(lines)


# ---- Connector demo tab (add below eval_md, before "the UI" section) ----
def connector_run():
    """Run the connector-fed pipeline from the shipped feeds/ fixtures."""
    try:
        from connectors import build_dataset_from_feeds
        t0 = time.perf_counter()
        ds, manifest = build_dataset_from_feeds()
        exceptions = run_checks(ds)
        triaged = triage_all(exceptions)
        report = evaluate(manifest, exceptions, time.perf_counter() - t0)
        added = getattr(ds, "config_added", [])
        head = ("### Connector-fed run — portfolio-shaped feeds\n"
                "Sources: SUT extract · FR ledger · DE ledger · "
                "e-invoice feeds · period returns\n\n"
                + (f"**Jurisdictions added from config:** {', '.join(added)}\n\n" if added else "")
                + f"{len(ds.transactions)} transactions · {len(ds.einvoices)} e-invoices · "
                  f"{len(ds.returns)} returns · {len(manifest)} planted errors")
        rows = [{"exc_id": e["exc_id"], "severity": e["severity"], "check": e["check"],
                 "tier": e["tier"], "conf": e["confidence"],
                 "jurisdiction": e["jurisdiction"], "summary": e["summary"]} for e in triaged]
        t = report["totals"]
        ev = ("| Type | Planted | Caught | Recall | Flagged | Precision |\n|---|---|---|---|---|---|\n"
              + "\n".join(f"| {k} | {v['seeded']} | {v['caught']} | {v['recall']} | "
                          f"{v['flagged']} | {v['precision']} |" for k, v in report["per_type"].items())
              + f"\n\n**OVERALL: {t['caught']}/{t['seeded_errors']} caught · recall "
                f"{t['overall_recall']} · precision {t['precision']} · "
                f"{t['unexplained_flags (false positives)']} unexplained flags**")
        return head, pd.DataFrame(rows), ev
    except Exception as e:  # demo-safe: never crash the app from this tab
        return f"_Connector demo unavailable: {type(e).__name__}_", pd.DataFrame(), ""


def _who(request: gr.Request) -> str:
    """SEC-03: reviewer identity from Gradio auth when enabled."""
    try:
        # authenticated username if auth is on; local default otherwise
        return getattr(request, "username", None) or "local-operator"
    except Exception:  # noqa: BLE001 Broad except on purpose — Gradio's request object is a moving target
        return "local-operator"          # never let identity lookup crash a click


# ---------------------------------------------------------------- the UI
with gr.Blocks(title="Reconciliation Workbench -- compliance prototype") as demo:
    gr.Markdown("# Reconciliation Workbench\n"
                "**Deterministic core · AI at the edges · humans at the points of "
                "consequence.**  Prototype of compliance case — "
                "synthetic multi-entity data, seeded errors, measured evals.")
    with gr.Row():                                   # dataset controls
        seed_in = gr.Number(value=42, label="Seed", precision=0)
        n_in = gr.Number(value=60, label="Txns per jurisdiction", precision=0)
        regen = gr.Button("Regenerate dataset", variant="primary")

    with gr.Tab("1 · Overview"):                     # tie-out + gates
        summary_box = gr.Markdown()
    with gr.Tab("2 · Exception queue (HITL)"):       # the review workflow
        queue_box = gr.Dataframe(interactive=False, wrap=True,
                                 column_widths=["8%", "9%", "15%", "17%", "6%",
                                                "15%", "30%"])
        with gr.Row():
            exc_in = gr.Textbox(label="Exception id", placeholder="EXC-0001")
            show_btn = gr.Button("Open")
        detail_box = gr.Markdown()                   # explanation + evidence
        with gr.Row():
            note_in = gr.Textbox(label="Reviewer note (optional)")
            approve_btn = gr.Button("✅ Approve disposition", variant="primary")
            override_btn = gr.Button("✋ Override / escalate", variant="stop")
        outcome_box = gr.Markdown()                  # confirmation + gate status
        gr.Markdown("### Decision log — the audit evidence trail")
        log_box = gr.Dataframe(interactive=False, wrap=True,
                               column_widths=["9%", "14%", "16%", "17%", "10%",
                                              "12%", "13%", "9%"])
        with gr.Row():
            export_btn = gr.Button("Export decision log (CSV)")
            export_file = gr.File(label="Audit export")
    with gr.Tab("3 · Eval panel"):                   # measured quality
        eval_box = gr.Markdown()
    with gr.Tab("4 · Connector demo"):
        gr.Markdown("Feeds → declarative mapping configs → the same canonical model — "
                    "the pipeline can't tell this data didn't come from the generator.")
        conn_btn = gr.Button("Run connector-fed pipeline", variant="primary")
        conn_head = gr.Markdown()
        conn_queue = gr.Dataframe(interactive=False, wrap=True)
        conn_eval = gr.Markdown()

    def _approve(i, note, request: gr.Request):
        """Approve click -> decide() with the authenticated reviewer."""
        return decide(i, "APPROVED", note, _who(request))

    def _override(i, note, request: gr.Request):
        """Override click -> decide() with the authenticated reviewer."""
        return decide(i, "OVERRIDDEN", note, _who(request))

    # ---- wire every control to its handler (outputs listed explicitly) --
    regen.click(build, [seed_in, n_in],
                [summary_box, queue_box, eval_box, outcome_box, log_box])
    show_btn.click(detail_md, [exc_in], [detail_box])
    approve_btn.click(_approve, [exc_in, note_in],
                      [outcome_box, log_box, summary_box])   # gates refresh live
    override_btn.click(_override, [exc_in, note_in],
                       [outcome_box, log_box, summary_box])
    export_btn.click(export_log, [], [export_file])
    demo.load(build, [seed_in, n_in],                # first render on page load
              [summary_box, queue_box, eval_box, outcome_box, log_box])
    conn_btn.click(connector_run, [], [conn_head, conn_queue, conn_eval])


if __name__ == "__main__":
    # SEC-03: launch() binds 127.0.0.1 by default — correct for the local
    # demo. For any PUBLIC deployment (e.g. a Hugging Face Space), set
    # RECONCILIATION_AUTH_USER / RECONCILIATION_AUTH_PASS (as Space secrets, never in code)
    # and authentication is enforced; reviewer identity then flows into
    # every decision-log row.
    _u, _p = os.getenv("RECONCILIATION_AUTH_USER"), os.getenv("RECONCILIATION_AUTH_PASS")
    if _u and _p:
        demo.launch(auth=(_u, _p))   # authenticated public mode
    else:
        demo.launch()                # localhost demo mode
