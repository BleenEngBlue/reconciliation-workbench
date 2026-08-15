"""Connector demo — the whole pipeline fed by PORTFOLIO-SHAPED FEEDS.

    python make_feeds.py            # once: writes feeds/* (deterministic)
    python run_connector_demo.py    # feeds -> connectors -> checks -> triage -> evals

Same checks, same triage, same evals, same UI contract as run_demo.py —
the only change is WHERE THE DATA COMES FROM. That is the demonstration:
the synthetic generator was a stand-in; the seam was built for feeds.
"""
from __future__ import annotations

import time

from checks import run_checks
from connectors import build_dataset_from_feeds
from evals import evaluate
from triage import triage_all


def main() -> None:
    t0 = time.perf_counter()
    ds, manifest = build_dataset_from_feeds()          # stage 0: CONNECT
    exceptions = run_checks(ds)                        # stage 2: find
    triaged = triage_all(exceptions)                   # stage 3: explain/tier
    dt = time.perf_counter() - t0
    report = evaluate(manifest, exceptions, dt)        # stage 4: measure

    print("\n=== CONNECTOR-FED RECONCILIATION RUN (portfolio-shaped feeds) ===")
    print("sources: sut_extract.csv · fr_ledger_extract.csv · de_ledger_extract.csv · "
          "einvoice/de e-invoice feeds · period_returns.csv")
    added = getattr(ds, "config_added", [])
    if added:
        print(f"jurisdictions added FROM CONFIG (feeds/jurisdictions_config.json): {', '.join(added)}")
    print(f"transactions {len(ds.transactions)} | e-invoices {len(ds.einvoices)} | "
          f"e-reports {len(ds.ereports)} | returns {len(ds.returns)}")
    print(f"manifest errors {len(manifest)} | exceptions flagged {len(exceptions)} | "
          f"runtime {dt:.3f}s\n")

    print("--- EXCEPTION QUEUE (triaged, top 10) ---")
    for e in triaged[:10]:
        print(f"[{e['severity']:>8}] {e['check']:<18} conf={e['confidence']:.2f} "
              f"{e['tier']:<22} {e['jurisdiction']:<26} {e['summary'][:66]}")

    print("\n--- EVAL PANEL (vs known-answer manifest) ---")
    for typ, row in report["per_type"].items():
        prec = row["precision"] if row["precision"] is not None else "—"
        print(f"  {typ:<20} planted {row['seeded']:>2}  caught {row['caught']:>2}  "
              f"recall {row['recall']}  flagged {row['flagged']:>2}  precision {prec}")
    for k, v in report["totals"].items():
        print(f"  {k:<38} {v}")
    if report["unmatched"]:
        print("  unexplained flags:")
        for u in report["unmatched"]:
            print(f"    {u['exc_id']} {u['check']}: {u['summary'][:66]}")


if __name__ == "__main__":
    main()
