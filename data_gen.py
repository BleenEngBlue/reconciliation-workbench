"""
Synthetic multi-entity compliance dataset with SEEDED ERRORS.

STANDARD THIS FILE EMBODIES (Req 2 — data management): all demo data is
synthetic, classification-free, and regenerable from a seed (deterministic:
same seed -> same dataset -> reproducible demo and reproducible evals).
No PII, no real financial data, ever. Seeded errors are logged as ground
truth so quality is measurable (Req 3).

Models a multinational scenario: a parent with subsidiaries selling across
a US state (California), a home-rule spot (Denver, CO), and France 
(e-invoicing linked to e-reporting).
"""

from __future__ import annotations  # modern type hints on older Pythons

import random  # seeded RNG (non-crypto: fine —
from dataclasses import asdict, dataclass, field  # nothing security-sensitive

                                                 # derives from it; see review)

# ----------------------------------------------------------------- config

# Legal entities in the corporate family (parent + subsidiaries)
ENTITIES = ["Atlas Beverages Inc (US parent)",
            "Atlas Bottling West LLC",
            "Atlas Beverages France SAS"]

# A JURISDICTION IS CONFIGURATION, NOT CODE — the thesis of the whole
# platform, demonstrated here: every check, triage rule, eval, and UI
# element downstream is driven by this dict. 
#
# ** Adding a country is adding an entry (see Germany below). ** 
#
# regime "ctc" activates e-invoice/e-report generation and the
# invoice<->report linkage checks (France-style continuous transaction
# controls); regime "us" models US state/local return filing.
JURISDICTIONS = {
    "California (state)":        {"rate": 0.0725, "entity": ENTITIES[0], "regime": "us"},
    "Denver, CO (home rule)":    {"rate": 0.0481, "entity": ENTITIES[1], "regime": "us"},
    "France (CTC / e-invoice)":  {"rate": 0.20,   "entity": ENTITIES[2], "regime": "ctc"},
    # ---- LIVE DEMO MOVE (optional, ~20 seconds on screen) -----------------
    # Germany's B2B e-invoicing mandate phases in 2027-28. Uncomment the
    # line below, click "Regenerate dataset", and a fourth jurisdiction —
    # entity, checks, filing gate, eval coverage — appears end-to-end.
    # "This is what 'a couple dozen countries per year' looks like."
    "Germany (CTC 2027-28)":   {"rate": 0.19,   "entity": "Atlas Beverages GmbH", "regime": "ctc"},
    # ------------------------------------------------------------------------
}

# The error taxonomy — each seeded type maps to exactly one expected check
# (see evals.EXPECTED_CHECK); together they form the golden set.
ERROR_TYPES = [
    "WRONG_RATE",        # tax computed at a stale/incorrect rate
    "AMOUNT_MISMATCH",   # tax_amount != round(amount * rate)
    "DUPLICATE_INVOICE", # same invoice number issued twice
    "MISSING_EINVOICE",  # CTC transaction with no e-invoice (mandate breach)
    "ORPHAN_REPORT",     # CTC e-report line with no matching transaction
    "RETURN_UNDERSTATED",# period return total < sum of transactions
]


@dataclass
class Dataset:
    """Everything one reconciliation run needs, in plain lists of dicts."""
    transactions: list = field(default_factory=list)  # the sales ledger
    einvoices: list = field(default_factory=list)     # CTC jurisdictions only
    ereports: list = field(default_factory=list)      # CTC jurisdictions only
    returns: list = field(default_factory=list)       # one per jurisdiction
    seeded_errors: list = field(default_factory=list) # ground truth (Req 3)
    config_added: list = field(default_factory=list)  # jurisdictions merged from config (connector runs)

    def to_dict(self):
        """Plain-dict view for JSON export."""
        return asdict(self)


def generate(seed: int = 42, n_per_jurisdiction: int = 60,
             error_rate: float = 0.10) -> Dataset:
    """Build the synthetic dataset: clean books, then seed known errors."""
    rng = random.Random(seed)   # local RNG: deterministic per seed, no global state
    ds = Dataset()              # the container we fill and return
    txn_no = 0                  # global transaction counter across jurisdictions

    for jur, cfg in JURISDICTIONS.items():          # one pass per jurisdiction
        rate, entity, regime = cfg["rate"], cfg["entity"], cfg["regime"]
        jur_txns = []                               # this jurisdiction's slice

        # ---- clean transactions first ------------------------------------
        for i in range(n_per_jurisdiction):
            txn_no += 1                                       # next global id
            amount = round(rng.uniform(250, 25_000), 2)       # sale amount
            tax = round(amount * rate, 2)                     # correct tax
            t = {
                "txn_id": f"TXN-{txn_no:05d}",     # unique transaction id
                "invoice_id": f"INV-{txn_no:05d}", # 1:1 invoice id (pre-errors)
                "entity": entity,                  # which company sold
                "jurisdiction": jur,               # where tax is owed
                "period": "2026-07",               # the filing period
                "amount": amount,                  # net sale amount
                "rate_applied": rate,              # rate the system used
                "tax_amount": tax,                 # tax the system charged
            }
            jur_txns.append(t)

        # ---- seed errors into this jurisdiction's slice ------------------
        n_err = max(2, int(len(jur_txns) * error_rate))       # at least 2
        victims = rng.sample(range(len(jur_txns)), n_err)     # which txns
        for vi in victims:
            t = jur_txns[vi]                                  # the victim txn
            choices = ["WRONG_RATE", "AMOUNT_MISMATCH", "DUPLICATE_INVOICE"]
            if regime == "ctc":                               # CTC-only errors
                choices += ["MISSING_EINVOICE", "ORPHAN_REPORT"]
            err = rng.choice(choices)                         # pick one type
            if err == "WRONG_RATE":
                # apply a plausibly-wrong rate and recompute tax with it
                bad = round(rate + rng.choice([-0.005, 0.005, 0.0125]), 4)
                t["rate_applied"] = bad
                t["tax_amount"] = round(t["amount"] * bad, 2)
            elif err == "AMOUNT_MISMATCH":
                # corrupt the tax amount so it no longer equals amount x rate
                t["tax_amount"] = round(t["tax_amount"] + rng.choice([-37.5, 12.4, 99.0]), 2)
            elif err == "DUPLICATE_INVOICE":
                # post the same invoice twice (classic double-posting)
                dup = dict(t)                       # shallow copy of the txn
                dup["txn_id"] = t["txn_id"] + "-D"  # distinct txn id, same invoice
                jur_txns.append(dup)
            elif err == "MISSING_EINVOICE":
                t["_skip_einvoice"] = True          # marker: emit no e-invoice
            elif err == "ORPHAN_REPORT":
                t["_orphan_report"] = True          # marker: emit a ghost report
            # record the seed as ground truth — this IS the golden set
            ds.seeded_errors.append({
                "type": err, "txn_id": t["txn_id"], "jurisdiction": jur,
            })

        # ---- derived documents (CTC regimes only) ------------------------
        for t in jur_txns:
            if regime == "ctc":
                if not t.pop("_skip_einvoice", False):        # unless seeded out
                    ds.einvoices.append({                     # the e-invoice
                        "invoice_id": t["invoice_id"], "txn_id": t["txn_id"],
                        "entity": t["entity"], "amount": t["amount"],
                        "tax_amount": t["tax_amount"], "status": "accepted",
                    })
                ds.ereports.append({                          # the linked e-report
                    "report_id": f"RPT-{t['txn_id']}", "txn_id": t["txn_id"],
                    "jurisdiction": jur,                      # config-driven label
                    "amount": t["amount"], "tax_amount": t["tax_amount"],
                })
                if t.pop("_orphan_report", False):            # seeded ghost report
                    ds.ereports.append({
                        "report_id": f"RPT-GHOST-{t['txn_id']}",
                        "txn_id": f"TXN-GHOST-{t['txn_id'][-5:]}",  # no such txn
                        "jurisdiction": jur,
                        "amount": round(rng.uniform(500, 5000), 2),
                        "tax_amount": 0.0,
                    })
        ds.transactions.extend(jur_txns)          # commit the slice to the ledger

        # ---- period return (one per jurisdiction), possibly understated --
        total_tax = round(sum(t["tax_amount"] for t in jur_txns), 2)  # ledger truth
        n_txn = len(jur_txns)                                          # incl. dups
        understate = rng.random() < 0.67   # most runs include one return error
        if understate:
            # report LESS than the ledger says — the critical error class
            reported = round(total_tax - rng.uniform(150, 900), 2)
            ds.seeded_errors.append({
                "type": "RETURN_UNDERSTATED", "txn_id": None, "jurisdiction": jur,
            })
        else:
            reported = total_tax               # honest return this run
        ds.returns.append({                    # the filed (draft) return
            "jurisdiction": jur, "period": "2026-07",
            "reported_txn_count": n_txn, "reported_tax": reported,
        })

    return ds  # complete dataset + ground-truth seed log


if __name__ == "__main__":
    # smoke test: generate once and print shape + every seeded error
    d = generate()
    print(f"transactions: {len(d.transactions)}  einvoices: {len(d.einvoices)}  "
          f"ereports: {len(d.ereports)}  returns: {len(d.returns)}")
    print(f"seeded errors: {len(d.seeded_errors)}")
    for e in d.seeded_errors:
        print("  ", e)
