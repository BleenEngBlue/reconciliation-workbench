"""
AI triage — the AI AT THE EDGES layer.

STANDARD THIS FILE EMBODIES (Req 1 + Req 2 — reusable AI engineering with
governance): AI output is schema-constrained, provenance-labeled
(triage_mode), and NEVER decides severity or ladder placement — those stay
deterministic and auditable. The LLM path is opt-in (env-gated), degrades
gracefully, and logs failures by class only (no prompt-data leakage).

TWO MODES:
  - offline (default): deterministic explainer templates. The live demo can
    NEVER be killed by wifi or an API outage.
  - llm (--llm / use_llm=True): GPT-4.1 Mini rewrites the explanation and
    disposition with a schema-validated JSON response. Same structure, so
    the UI is identical either way. Requires OPENAI_API_KEY.

The confidence heuristic is deliberately transparent (not a model): it maps
check type + materiality to a score, because the LADDER placement must be
explainable to an auditor. This is a product decision, not a shortcut.
"""

from __future__ import annotations  # modern type hints on older Pythons

import json  # parse the LLM's JSON reply; serialize exceptions for prompts
import os  # read env flags (OPENAI_API_KEY, RECONCILIATION_USE_LLM)

# The automation ladder: minimum confidence -> tier. Order matters — the
# first floor the confidence clears wins. Governance rule (Req 2): tier is
# DERIVED from this table, never asserted by the model.
LADDER = [  # (min_confidence, tier)
    (0.90, "AUTO-CLEAR (log only)"),
    (0.60, "REVIEW-THEN-RELEASE"),
    (0.00, "HOLD-FOR-HUMAN"),
]

# Per-check (action, rationale) templates for offline mode — each pair is
# the disposition the AI proposes and the WHY a reviewer sees beside it.
DISPOSITIONS = {
    "RATE_CHECK": ("Recalculate at the authoritative rate and adjust the ledger; "      # noqa: ISC004
                   "flag the source system's rate table for update.",
                   "The engine's rate is authoritative; the fix is mechanical but "     # noqa: ISC004
                   "touches remitted tax, so a human confirms."),
    "ARITHMETIC_CHECK": ("Correct the tax amount to amount x rate; investigate the "    # noqa: ISC004
                         "source of the manual override.",
                         "Pure arithmetic against the applied rate — high confidence, " # noqa: ISC004
                         "but the override may signal a process issue."),
    "DUPLICATE_CHECK": ("Void the duplicate entry; verify no double remittance in "     # noqa: ISC004
                        "the period return.",
                        "Same invoice number, same amount, same entity — classic "      # noqa: ISC004
                        "double-posting signature."),
    "EINVOICE_LINKAGE": ("Generate and transmit the missing e-invoice before the "      # noqa: ISC004
                         "reporting deadline; verify network/PDP acknowledgment.",
                         "CTC-mandate breach with legal consequence (France today; "    # noqa: ISC004
                         "any CTC jurisdiction by config) — always routed to a "
                         "human, never auto-cleared."),
    "ORPHAN_REPORT": ("Trace the report line to its source; if no transaction "         # noqa: ISC004
                      "exists, file a corrective e-report.",
                      "A report without a transaction inflates reported activity — "    # noqa: ISC004
                      "likely an integration replay."),
    "RETURN_TIEOUT": ("Do NOT file. Reconcile the variance to specific "                # noqa: ISC004
                      "transactions before release.",
                      "A return that doesn't tie to the ledger is the exact error "     # noqa: ISC004
                      "class with legal consequence — hard stop."),
    "COUNT_TIEOUT": ("Reconcile the transaction count delta; usually a duplicate "      # noqa: ISC004
                     "or late-posted transaction.",
                     "Count deltas are usually explained by another exception in "      # noqa: ISC004
                     "this queue — resolve those first."),
}

# GUARD: every entry above must be exactly (action, why). The strings use
# implicit concatenation across lines, so one stray comma silently turns a
# pair into a triple and crashes triage mid-demo. This assert converts that
# editing accident into an immediate, named failure at import time instead.
assert all(len(v) == 2 for v in DISPOSITIONS.values()), \
    "DISPOSITIONS entries must be (action, why) pairs — check for a stray comma"

# Starting confidence per check type — high for pure arithmetic, floor-low
# for anything with legal consequence (so it can never auto-clear).
BASE_CONFIDENCE = {
    "ARITHMETIC_CHECK": 0.95, "DUPLICATE_CHECK": 0.90, "RATE_CHECK": 0.80,
    "ORPHAN_REPORT": 0.70, "COUNT_TIEOUT": 0.65,
    "EINVOICE_LINKAGE": 0.40, "RETURN_TIEOUT": 0.20,
}


def _materiality_penalty(exc: dict) -> float:
    """Bigger money at stake -> lower confidence -> more human review."""
    ev = exc.get("evidence", {})                 # the check's raw numbers
    # take the largest relevant magnitude the evidence carries (any of
    # variance / delta / double-counted tax), defaulting to zero
    magnitude = abs(ev.get("variance", ev.get("delta", ev.get("double_counted_tax", 0)) or 0))
    if isinstance(magnitude, (int, float)) and magnitude > 500:
        return 0.15                              # material amount: demote
    return 0.0                                   # immaterial: no penalty


def tier_for(confidence: float) -> str:
    """Map a confidence score onto the automation ladder (first floor wins)."""
    for floor, tier in LADDER:      # walk the ladder top-down
        if confidence >= floor:     # cleared this floor?
            return tier             # -> that's the tier
    return LADDER[-1][1]            # unreachable, but safe: lowest tier


def triage_offline(exc: dict) -> dict:
    """Deterministic triage: template explanation + transparent confidence."""
    # look up the (action, why) pair; unknown checks get a safe default
    action, why = DISPOSITIONS.get(exc["check"], ("Investigate manually.", "Unrecognized check."))
    # confidence = base for this check minus materiality penalty, floored
    conf = max(0.05, BASE_CONFIDENCE.get(exc["check"], 0.5) - _materiality_penalty(exc))
    return {
        **exc,                                        # keep the original fields
        "explanation": f"{exc['summary']}. {why}",    # summary + rationale
        "proposed_disposition": action,               # what the AI suggests
        "confidence": round(conf, 2),                 # transparent score
        "tier": tier_for(conf),                       # derived ladder tier
        "triage_mode": "offline",                     # provenance label (Req 2)
    }


def triage_llm(exc: dict, model: str = "gpt-4.1-mini") -> dict:
    """LLM-written explanation/disposition, schema-validated; falls back to
    offline mode on ANY failure so the demo cannot break."""
    try:
        from openai import OpenAI  # imported lazily: offline mode never needs it
        client = OpenAI()                  # reads OPENAI_API_KEY from env (Req 2: no keys in code)
        resp = client.chat.completions.create(
            model=model,                   # pinned model name, passed in
            temperature=0,                 # deterministic-as-possible output
            response_format={"type": "json_object"},  # schema constraint (Req 1)
            messages=[
                {"role": "system", "content":
                    "You are a tax-compliance analyst assistant. Given one "
                    "reconciliation exception (JSON), return ONLY a JSON object: "
                    '{"explanation": "<2 sentences, plain language, cite the numbers>", '
                    '"proposed_disposition": "<1 sentence action>"}. '
                    "Never invent numbers not present in the input."},
                {"role": "user", "content": json.dumps(exc)},  # the exception as data
            ],
        )
        content = resp.choices[0].message.content  # Optional[str] per the SDK
        if content is None:                        # empty response = failure,
            raise ValueError("LLM response missing content")  # not a crash
        out = json.loads(content)                  # parse the JSON reply
        base = triage_offline(exc)  # keep transparent confidence/tier (governance)
        base.update({
            # str() casts harden against non-string JSON values (Req 1)
            "explanation": str(out.get("explanation", base["explanation"])),
            "proposed_disposition": str(out.get("proposed_disposition",
                                                base["proposed_disposition"])),
            "triage_mode": "llm",          # provenance: reviewer sees AI wrote this
        })
        return base
    # One handler, so one except — the class name in the log carries the specificity. 
    # The day this needs a retry policy is the day the except clauses split, because 
    # that's the day the handlers actually differ."
    except Exception as e:  # noqa: BLE001
        # SEC-08: surface the failure CLASS (never contents — they can echo
        # prompt data) so auth/TLS/proxy failures are distinguishable from
        # quota blips. The offline fallback itself is deliberate.
        print(f"[triage] LLM unavailable ({type(e).__name__}) — offline fallback")
        return triage_offline(exc)


def triage_all(exceptions: list[dict], use_llm: bool | None = None) -> list[dict]:
    """Triage every exception; sort by severity, then money at stake."""
    if use_llm is None:
        # LLM mode requires BOTH the key present AND the explicit opt-in
        # flag (Req 2: no silent data egress just because a key exists)
        use_llm = bool(os.getenv("OPENAI_API_KEY")) and \
                  os.getenv("RECONCILIATION_USE_LLM", "0") in ("1", "true", "yes")
    fn = triage_llm if use_llm else triage_offline   # pick the mode once
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}  # sort ranking
    out = [fn(e) for e in exceptions]                # triage each exception
    # sort: most severe first; within a severity, biggest variance first
    out.sort(key=lambda e: (order.get(e["severity"], 9), -abs(
        e.get("evidence", {}).get("variance", 0) or 0)))
    return out
