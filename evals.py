"""
Eval panel — measured behavior, not claimed behavior.

STANDARD THIS FILE EMBODIES (Req 3 — monitoring & continuous improvement):
every AI/automation claim ships with a measurement. Seeded errors ARE the
golden set: ground truth is known by construction, so flag quality is a
measured property (precision/recall), not a vibe. The same discipline as the
Digital Twin's per-stage harness, pointed at reconciliation.

Mapping between seeded error types and the checks expected to catch them:
  WRONG_RATE         -> RATE_CHECK
  AMOUNT_MISMATCH    -> ARITHMETIC_CHECK
  DUPLICATE_INVOICE  -> DUPLICATE_CHECK (+ COUNT_TIEOUT side effect)
  MISSING_EINVOICE   -> EINVOICE_LINKAGE
  ORPHAN_REPORT      -> ORPHAN_REPORT
  RETURN_UNDERSTATED -> RETURN_TIEOUT
"""

from __future__ import annotations  # modern type hints on older Pythons

# Which deterministic check is EXPECTED to catch each seeded error type —
# this dict IS the eval contract (change a check, update the contract).
EXPECTED_CHECK = {
    "WRONG_RATE": "RATE_CHECK",
    "AMOUNT_MISMATCH": "ARITHMETIC_CHECK",
    "DUPLICATE_INVOICE": "DUPLICATE_CHECK",
    "MISSING_EINVOICE": "EINVOICE_LINKAGE",
    "ORPHAN_REPORT": "ORPHAN_REPORT",
    "RETURN_UNDERSTATED": "RETURN_TIEOUT",
}

# Checks that legitimately fire as SIDE EFFECTS of seeded errors (not false
# positives): a duplicate invoice also breaks the count tie-out, and any
# tax-amount error also shifts the return tie-out variance.
SIDE_EFFECT_CHECKS = {"COUNT_TIEOUT", "RETURN_TIEOUT"}


def evaluate(seeded: list[dict], exceptions: list[dict],
             runtime_s: float) -> dict:
    """Score flagged exceptions against seeded ground truth.

    Inputs: the ground-truth seed log, the flagged exceptions, and the
    pipeline runtime (Req 3: latency is part of every eval record).
    Returns a dict with per-type recall, overall precision/recall, and
    any unexplained flags (candidate false positives) for review.
    """
    per_type: dict[str, dict] = {}     # per-error-type recall rows
    matched_exc_ids: set[str] = set()  # exception ids traced to a seed

    # ---- RECALL: for each seeded error, did its expected check fire? ----
    for err_type, check in EXPECTED_CHECK.items():
        # all seeds of this type in this run (may be zero)
        seeds = [s for s in seeded if s["type"] == err_type]
        if not seeds:
            continue                    # nothing seeded -> nothing to score
        caught = 0                      # how many of these seeds were caught
        for s in seeds:
            # bind once: tid is None ONLY for jurisdiction-level seeds
            # (RETURN_UNDERSTATED); every txn-level seed carries a real id.
            # A local lets the type checker narrow it (dict subscripts can't).
            tid = s["txn_id"]
            # an exception "hits" a seed when the check matches AND either
            # (a) the seed is jurisdiction-level (tid None) and the exception
            #     is in that jurisdiction, or
            # (b) the seed's txn id appears in the exception's txn id
            hits = [e for e in exceptions if e["check"] == check and
                    (tid is None and e["jurisdiction"] == s["jurisdiction"]
                     or tid is not None and e["txn_id"] is not None
                     and tid in e["txn_id"])]
            # ORPHAN_REPORT ghosts carry only the source txn's last-5 digits,
            # so match on that suffix when the direct match found nothing.
            # (tid is never None for ORPHAN_REPORT seeds — the guard states
            # the invariant explicitly and satisfies static analysis.)
            if not hits and err_type == "ORPHAN_REPORT" and tid is not None:
                hits = [e for e in exceptions if e["check"] == check and
                        tid[-5:] in (e["txn_id"] or "")]
            if hits:
                caught += 1                                   # seed caught
                matched_exc_ids.update(h["exc_id"] for h in hits)  # remember
        # PER-TYPE PRECISION (false positives especially): of all
        # flags this check raised, how many trace back to a seeded error?
        flags_of_check = [e for e in exceptions if e["check"] == check]
        explained = sum(1 for e in flags_of_check
                        if e["exc_id"] in matched_exc_ids)
        # store the recall + precision row for this error type
        per_type[err_type] = {
            "seeded": len(seeds), "caught": caught,
            "recall": round(caught / len(seeds), 3),
            "flagged": len(flags_of_check),                   # flags raised
            "precision": round(explained / len(flags_of_check), 3)
                         if flags_of_check else None,         # none flagged
        }

    # ---- PRECISION: every flag must trace to a seed or a side effect ----
    unmatched = [e for e in exceptions if e["exc_id"] not in matched_exc_ids
                 and e["check"] not in SIDE_EFFECT_CHECKS]
    n_exc = len(exceptions)             # total flags raised this run
    true_pos = n_exc - len(unmatched)   # flags explained by ground truth
    total_seeded = sum(v["seeded"] for v in per_type.values())  # all seeds
    total_caught = sum(v["caught"] for v in per_type.values())  # all caught

    # ---- the eval record: everything a reviewer needs, in one dict ----
    return {
        "per_type": per_type,                       # recall per error type
        "totals": {
            "seeded_errors": total_seeded,
            "caught": total_caught,
            "overall_recall": round(total_caught / total_seeded, 3) if total_seeded else None,
            "exceptions_flagged": n_exc,
            "unexplained_flags (false positives)": len(unmatched),
            "precision": round(true_pos / n_exc, 3) if n_exc else None,
        },
        "runtime_s": round(runtime_s, 3),           # Req 3: perf monitoring
        "unmatched": [{"exc_id": e["exc_id"], "check": e["check"],
                       "summary": e["summary"]} for e in unmatched],
    }
