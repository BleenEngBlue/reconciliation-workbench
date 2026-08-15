"""Connector layer — portfolio feeds in, canonical Dataset out.

THE INTEGRATION QUESTION ("how does this connect to data coming from 
upstream tax systems — transaction extracts, e-invoice networks, returns?"): 
each source system gets a CONNECTOR — a declarative field-mapping config 
plus a thin loader — that translates that system's native export shape into 
the workbench's canonical model. The pipeline downstream 
(checks → triage → evals → UI) is untouched: it cannot tell whether a Dataset 
came from the synthetic generator or from feeds, which is the point — the 
synthetic generator was always a stand-in for these feeds.

Design rules this file embodies:
  * MAPPING IS CONFIGURATION. Each connector's field map is data, not code —
    the exact artifact the production AI-assisted mapper would PROPOSE and a
    human would confirm; confirmed maps are versioned and reused.
  * UNITS AND LOCALES ARE THE CONNECTOR'S PROBLEM. Percent→decimal,
    comma-decimals, semicolon CSVs, nested JSON — all normalized here, so
    the deterministic core never sees a locale.
  * GROUND TRUTH TRAVELS WITH THE FIXTURE. feeds/known_answer_manifest.json
    plays the role of seeded errors: integration quality is measured
    (precision/recall), not assumed — the golden-set discipline applied to
    the ingestion seam itself.
"""
from __future__ import annotations

import csv
import json
import os

from data_gen import Dataset

FEED_DIR = "feeds"

# ------------------------------------------------------------------ maps
# Native tax-area codes -> canonical jurisdiction labels (as configured in
# data_gen.JURISDICTIONS). One dict per source system; pure data.
TAX_AREA_MAP = {
    "CA-STATE": "California (state)",
    "CO-DENVER": "Denver, CO (home rule)",
    "FR": "France (CTC / e-invoice)",
}

# SUT US transaction extract -> canonical transaction fields.
SUT_MAP = {
    "txn_id": "TXN_REF",
    "invoice_id": "INVOICE_NO",
    "entity": "LEGAL_ENTITY",
    "jurisdiction": ("TAX_AREA", TAX_AREA_MAP),   # via code map
    "amount": ("NET_AMT", float),
    "rate_applied": ("TAX_RATE_PCT", lambda v: round(float(v) / 100, 4)),  # % -> decimal
    "tax_amount": ("TAX_AMT", float),
}

# French ERP/VVC ledger extract -> canonical transaction fields.
FR_LEDGER_MAP = {
    "txn_id": "piece_id",
    "invoice_id": "facture",
    "entity": "societe",
    "amount": ("montant_ht", float),
    "rate_applied": ("taux_tva", lambda v: round(float(v.replace(",", ".")) / 100, 4)),
    "tax_amount": ("montant_tva", lambda v: float(v.replace(",", "."))),
}


def _apply(mapping: dict, row: dict) -> dict:
    """Apply one declarative field map to one native row."""
    out = {}
    for canon, spec in mapping.items():
        if isinstance(spec, str):                       # straight rename
            out[canon] = row[spec]
        else:
            field, conv = spec
            out[canon] = conv[row[field]] if isinstance(conv, dict) else conv(row[field])
    return out


# ------------------------------------------------------------ connectors
def load_sut(path=None) -> list[dict]:
    """SUT-style US extract (CSV, percent rates) -> canonical txns."""
    path = path or os.path.join(FEED_DIR, "sut_extract.csv")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        t = _apply(SUT_MAP, r)
        t["period"] = "2026-07"
        out.append(t)
    return out


def load_fr_ledger(path=None) -> list[dict]:
    """French ledger extract (semicolon CSV, comma decimals) -> canonical txns."""
    path = path or os.path.join(FEED_DIR, "fr_ledger_extract.csv")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    out = []
    for r in rows:
        t = _apply(FR_LEDGER_MAP, r)
        t["jurisdiction"] = "France (CTC / e-invoice)"
        t["period"] = "2026-07"
        out.append(t)
    return out


def load_einvoice_feed(path=None) -> tuple[list[dict], list[dict]]:
    """e-invoice status feed (nested JSON) -> canonical
    einvoices + ereports. The e-report mirrors the cleared document, exactly
    as France's linked e-invoicing/e-reporting regime implies."""
    path = path or os.path.join(FEED_DIR, "einvoice_feed.json")
    with open(path, encoding="utf-8") as f:
        feed = json.load(f)
    einvoices, ereports = [], []
    for d in feed["documents"]:
        einvoices.append({
            "invoice_id": d["invoiceNumber"],
            "txn_id": d["sourceRef"],
            "entity": d["sellerParty"]["name"],
            "amount": d["totals"]["net"],
            "tax_amount": d["totals"]["tax"],
            "status": d["clearanceStatus"].lower(),
        })
        ereports.append({
            "report_id": f"RPT-{d['documentId']}",
            "txn_id": d["sourceRef"],
            "jurisdiction": "France (CTC / e-invoice)",
            "amount": d["totals"]["net"],
            "tax_amount": d["totals"]["tax"],
        })
    return einvoices, ereports


def load_returns(path=None) -> list[dict]:
    """Period returns file -> canonical returns."""
    path = path or os.path.join(FEED_DIR, "period_returns.csv")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [{
        "jurisdiction": TAX_AREA_MAP[r["TAX_AREA"]],
        "period": r["PERIOD"],
        "reported_txn_count": int(r["TXN_COUNT"]),
        "reported_tax": float(r["TAX_REPORTED"]),
    } for r in rows]


def load_manifest(path=None) -> list[dict]:
    """The fixture's known-answer manifest — ground truth for the evals."""
    path = path or os.path.join(FEED_DIR, "known_answer_manifest.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)




# German ERP ledger extract -> canonical transaction fields.
DE_LEDGER_MAP = {
    "txn_id": "BelegNr",
    "invoice_id": "RechnungsNr",
    "entity": "Gesellschaft",
    "amount": ("Nettobetrag", float),
    "rate_applied": ("USt_Satz", lambda v: round(float(v.replace(",", ".")) / 100, 4)),
    "tax_amount": ("USt_Betrag", lambda v: float(v.replace(",", "."))),
}


def load_jurisdictions_config(path=None) -> list[str]:
    """THE COUNTRY MOVE, connector edition: jurisdictions arrive as DATA.
    feeds/jurisdictions_config.json entries are merged into the same
    JURISDICTIONS dict every check consumes — a new country is one JSON
    entry plus its feed files; zero code changes downstream."""
    import data_gen
    path = path or os.path.join(FEED_DIR, "jurisdictions_config.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    added = []
    for name, spec in cfg.items():
        TAX_AREA_MAP[spec.get("tax_area_code", name)] = name
        data_gen.JURISDICTIONS[name] = {
            "rate": spec["rate"], "entity": spec["entity"], "regime": spec["regime"],
        }
        added.append(name)
    return added


def load_de_ledger(path=None) -> list[dict]:
    """German ledger extract (semicolon CSV, comma decimals) -> canonical txns."""
    path = path or os.path.join(FEED_DIR, "de_ledger_extract.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    out = []
    for r in rows:
        t = _apply(DE_LEDGER_MAP, r)
        t["jurisdiction"] = "Germany (CTC 2027-28)"
        t["period"] = "2026-07"
        out.append(t)
    return out


def load_de_einvoice_feed(path=None) -> tuple[list[dict], list[dict]]:
    """German e-invoice feed (same network format as FR feed)."""
    path = path or os.path.join(FEED_DIR, "de_einvoice_feed.json")
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        feed = json.load(f)
    einvoices, ereports = [], []
    for d in feed["documents"]:
        einvoices.append({
            "invoice_id": d["invoiceNumber"], "txn_id": d["sourceRef"],
            "entity": d["sellerParty"]["name"], "amount": d["totals"]["net"],
            "tax_amount": d["totals"]["tax"], "status": d["clearanceStatus"].lower(),
        })
        ereports.append({
            "report_id": f"RPT-{d['documentId']}", "txn_id": d["sourceRef"],
            "jurisdiction": "Germany (CTC 2027-28)",
            "amount": d["totals"]["net"], "tax_amount": d["totals"]["tax"],
        })
    return einvoices, ereports


def build_dataset_from_feeds() -> tuple[Dataset, list[dict]]:
    """Assemble the canonical Dataset entirely from portfolio feeds."""
    added = load_jurisdictions_config()     # countries arrive as data
    ds = Dataset()
    ds.transactions = load_sut() + load_fr_ledger() + load_de_ledger()
    fr_e, fr_r = load_einvoice_feed()
    de_e, de_r = load_de_einvoice_feed()
    ds.einvoices, ds.ereports = fr_e + de_e, fr_r + de_r
    ds.returns = load_returns()
    ds.config_added = added                 # for the demo printout
    ds.seeded_errors = load_manifest()      # golden set, from the fixture
    return ds, ds.seeded_errors
