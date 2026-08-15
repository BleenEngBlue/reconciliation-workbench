"""
Deterministic reconciliation checks — the DETERMINISTIC CORE.

STANDARD THIS FILE EMBODIES (Req 1 — reusable architecture): pure code,
zero AI, zero I/O. Every exception is reproducible, explainable, and
auditable. The AI layer (triage.py) only explains and proposes dispositions;
it never decides what is wrong. "Deterministic core, AI at the edges,
humans at the points of consequence." Checks are driven entirely by the
JURISDICTIONS config — adding a country adds coverage without new code.

Each exception: {exc_id, check, severity, jurisdiction, entity, txn_id,
                 summary, evidence}
"""

from __future__ import annotations  # modern type hints on older Pythons

from collections import Counter  # for duplicate-invoice counting

from data_gen import JURISDICTIONS, Dataset  # data model + jurisdiction config

TOLERANCE = 0.02  # cents-level rounding tolerance for money comparisons


def run_checks(ds: Dataset) -> list[dict]:
    """Run every deterministic check over the dataset; return exceptions."""
    exceptions: list[dict] = []  # accumulates every exception raised
    n = 0                        # running counter for stable exception ids

    def add(check, severity, jur, entity, txn_id, summary, evidence):
        """Append one exception with a sequential id (EXC-0001, ...)."""
        nonlocal n                       # mutate the enclosing counter
        n += 1                           # next id number
        exceptions.append({              # the exception record itself
            "exc_id": f"EXC-{n:04d}", "check": check, "severity": severity,
            "jurisdiction": jur, "entity": entity, "txn_id": txn_id,
            "summary": summary, "evidence": evidence,  # evidence = raw numbers
        })

    # 1. RATE CHECK — recompute every transaction against the authoritative
    #    rate table (in production: the untouched upstream tax engine).
    for t in ds.transactions:
        expected = JURISDICTIONS[t["jurisdiction"]]["rate"]  # source of truth
        if abs(t["rate_applied"] - expected) > 1e-9:          # any drift at all
            add("RATE_CHECK", "high", t["jurisdiction"], t["entity"], t["txn_id"],
                f"Rate {t['rate_applied']:.4f} applied; authoritative rate is {expected:.4f}",
                {"amount": t["amount"], "tax_charged": t["tax_amount"],
                 "tax_at_correct_rate": round(t["amount"] * expected, 2)})

    # 2. ARITHMETIC CHECK — tax_amount must equal amount x rate_applied
    #    (catches manual overrides and data corruption).
    for t in ds.transactions:
        recomputed = round(t["amount"] * t["rate_applied"], 2)  # what it should be
        if abs(recomputed - t["tax_amount"]) > TOLERANCE:       # beyond rounding
            add("ARITHMETIC_CHECK", "high", t["jurisdiction"], t["entity"], t["txn_id"],
                f"Tax {t['tax_amount']:.2f} != amount x rate ({recomputed:.2f})",
                {"amount": t["amount"], "rate": t["rate_applied"],
                 "delta": round(t["tax_amount"] - recomputed, 2)})

    # 3. DUPLICATE INVOICE CHECK — the same invoice number posted twice
    #    means tax is double-counted (and possibly double-remitted).
    counts = Counter(t["invoice_id"] for t in ds.transactions)  # id -> count
    for inv, c in counts.items():
        if c > 1:                                               # duplicate found
            txns = [t for t in ds.transactions if t["invoice_id"] == inv]
            add("DUPLICATE_CHECK", "medium", txns[0]["jurisdiction"],
                txns[0]["entity"], txns[0]["txn_id"],
                f"Invoice {inv} appears {c} times",
                {"txn_ids": [t["txn_id"] for t in txns],
                 "double_counted_tax": round(sum(t["tax_amount"] for t in txns[1:]), 2)})

    # 4. CTC LINKAGE — every transaction in a CTC-regime jurisdiction must
    #    have an e-invoice on record (France today; any regime "ctc" entry
    #    in JURISDICTIONS tomorrow — jurisdiction is configuration).
    ctc_txn_ids_with_einv = {e["txn_id"] for e in ds.einvoices}  # fast lookup
    for t in ds.transactions:
        if JURISDICTIONS[t["jurisdiction"]]["regime"] == "ctc" \
                and not t["txn_id"].endswith("-D") \
                and t["txn_id"] not in ctc_txn_ids_with_einv:
            # (duplicates "-D" are excluded: their original carries the invoice)
            add("EINVOICE_LINKAGE", "high", t["jurisdiction"], t["entity"], t["txn_id"],
                "CTC-regime transaction has no e-invoice on record (mandate breach)",
                {"invoice_id": t["invoice_id"], "amount": t["amount"]})

    # 5. CTC ORPHAN REPORTS — e-report lines that reference no underlying
    #    transaction (integration replays, ghost data) inflate reported activity.
    txn_ids = {t["txn_id"] for t in ds.transactions}  # every known txn id
    for r in ds.ereports:
        if r["txn_id"] not in txn_ids:                # report with no parent
            jur = r.get("jurisdiction", "France (CTC / e-invoice)")  # from config
            add("ORPHAN_REPORT", "medium", jur,
                JURISDICTIONS.get(jur, {}).get("entity", "?"), r["txn_id"],
                f"E-report {r['report_id']} references no known transaction",
                {"reported_amount": r["amount"]})

    # 6. RETURN TIE-OUT — the period return must tie to the transaction
    #    ledger, per jurisdiction, on BOTH tax total and transaction count.
    for ret in ds.returns:
        jur = ret["jurisdiction"]                                  # which return
        jur_txns = [t for t in ds.transactions if t["jurisdiction"] == jur]
        ledger_tax = round(sum(t["tax_amount"] for t in jur_txns), 2)  # truth
        ledger_count = len(jur_txns)                               # truth count
        if abs(ledger_tax - ret["reported_tax"]) > TOLERANCE:      # $$ variance
            add("RETURN_TIEOUT", "critical", jur, jur_txns[0]["entity"], None,
                f"Return reports {ret['reported_tax']:.2f}; ledger totals {ledger_tax:.2f} "
                f"(variance {ledger_tax - ret['reported_tax']:+.2f})",
                {"reported_count": ret["reported_txn_count"], "ledger_count": ledger_count,
                 "variance": round(ledger_tax - ret["reported_tax"], 2)})
        if ledger_count != ret["reported_txn_count"]:              # count variance
            add("COUNT_TIEOUT", "medium", jur, jur_txns[0]["entity"], None,
                f"Return counts {ret['reported_txn_count']} transactions; ledger has {ledger_count}",
                {"delta": ledger_count - ret["reported_txn_count"]})

    return exceptions  # the full exception list, in check order


if __name__ == "__main__":
    # smoke test: run the checks standalone and print the first ten
    from data_gen import generate
    exs = run_checks(generate())
    print(f"{len(exs)} exceptions")
    for e in exs[:10]:
        print(f"  [{e['severity']:>8}] {e['check']:<18} {e['jurisdiction']:<26} {e['summary']}")
