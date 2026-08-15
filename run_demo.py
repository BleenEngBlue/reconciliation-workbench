"""
CLI runner — the whole pipeline end to end, no UI required.

STANDARD THIS FILE EMBODIES (Req 3 — troubleshooting & monitoring): one
command reproduces the entire pipeline anywhere, prints the eval record
(quality + latency), and can dump the full run to JSON for diffing between
changes — the continuous-improvement loop in its smallest form.

    python run_demo.py            # offline triage (default, demo-safe)
    python run_demo.py --llm      # LLM triage (needs OPENAI_API_KEY)
    python run_demo.py --seed 7   # different dataset
    python run_demo.py --json     # write results_recon.json for diffing
"""

from __future__ import annotations  # modern type hints on older Pythons

import argparse  # CLI flag parsing
import json  # JSON export of the full run record
import time  # wall-clock timing for the eval record

from checks import run_checks  # stage 2: deterministic exception finding
from data_gen import generate  # stage 1: synthetic data + ground truth
from evals import evaluate  # stage 4: measured quality vs ground truth
from triage import triage_all  # stage 3: AI explanation + ladder tiering


def main() -> None:
    """Parse flags, run the four-stage pipeline, print the eval record."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)           # dataset seed
    ap.add_argument("--n", type=int, default=60,
                    help="transactions per jurisdiction")
    ap.add_argument("--llm", action="store_true",
                    help="use LLM triage (needs OPENAI_API_KEY)")
    ap.add_argument("--json", action="store_true",
                    help="dump full results JSON")
    args = ap.parse_args()

    # ---- run the pipeline, timed end to end (Req 3: perf monitoring) ----
    t0 = time.perf_counter()                                   # start clock
    # clamp inputs like the UI does (SEC-04) so CLI misuse can't OOM either
    seed = abs(int(args.seed)) % 1_000_000                     # bounded seed
    n = max(10, min(int(args.n), 500))                         # bounded size
    ds = generate(seed=seed, n_per_jurisdiction=n)             # stage 1
    exceptions = run_checks(ds)                                # stage 2
    triaged = triage_all(exceptions, use_llm=args.llm)         # stage 3
    dt = time.perf_counter() - t0                              # stop clock
    report = evaluate(ds.seeded_errors, exceptions, dt)        # stage 4

    # ---- headline numbers ------------------------------------------------
    print(f"\n=== RECONCILIATION RUN  (seed={seed}) ===")
    print(f"transactions {len(ds.transactions)} | e-invoices {len(ds.einvoices)} | "
          f"e-reports {len(ds.ereports)} | returns {len(ds.returns)}")
    print(f"seeded errors {len(ds.seeded_errors)} | exceptions flagged {len(exceptions)} | "
          f"runtime {dt:.3f}s\n")

    # ---- the triaged queue (top 12 = what a reviewer sees first) --------
    print("--- EXCEPTION QUEUE (triaged, top 12) ---")
    for e in triaged[:12]:
        print(f"[{e['severity']:>8}] {e['check']:<18} conf={e['confidence']:.2f} "
              f"{e['tier']:<22} {e['jurisdiction']:<26} {e['summary'][:70]}")

    # ---- ladder distribution: how much work is automated vs human -------
    tiers: dict[str, int] = {}                       # tier -> count
    for e in triaged:
        tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    print("\n--- AUTOMATION LADDER DISTRIBUTION ---")
    for tier, count in sorted(tiers.items()):
        print(f"  {tier:<24} {count}")

    # ---- the eval panel: measured, not claimed (Req 3) ------------------
    print("\n--- EVAL PANEL (vs seeded ground truth) ---")
    for typ, row in report["per_type"].items():      # recall + precision per type
        prec = row["precision"] if row["precision"] is not None else "—"
        print(f"  {typ:<20} seeded {row['seeded']:>2}  caught {row['caught']:>2}  "
              f"recall {row['recall']}  flagged {row['flagged']:>2}  "
              f"precision {prec}")
    for k, v in report["totals"].items():            # precision/recall totals
        print(f"  {k:<38} {v}")
    if report["unmatched"]:                          # candidate false positives
        print("  unexplained flags:")
        for u in report["unmatched"]:
            print(f"    {u['exc_id']} {u['check']}: {u['summary'][:70]}")

    # ---- optional full-record export for run-to-run diffing -------------
    if args.json:
        out = {"exceptions": triaged, "eval": report}          # everything
        with open("results_recon.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)                        # pretty JSON
        print("\nfull record written to results_recon.json")


if __name__ == "__main__":
    main()  # single entry point
