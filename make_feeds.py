"""Generate portfolio-shaped mock feed fixtures + a known-answer manifest.

Each file imitates the EXPORT SHAPE of an existing demo portfolio system
(field names, formats, units) — deliberately NOT the workbench's canonical
schema, so the mapping layer has real work to do. Errors are planted and
logged to the manifest, preserving the golden-set discipline: ground truth
known by construction, so integration quality is measured, not assumed.

Run once:  python make_feeds.py   ->  writes feeds/*.csv|json
Deterministic (seeded) — same fixtures every run.
"""
from __future__ import annotations

import csv
import json
import os
import random

rng = random.Random(7)
os.makedirs("feeds", exist_ok=True)
manifest = []  # ground truth: every planted error, by type + reference

# --------------------------------------------------------------- helpers
CA_RATE, DEN_RATE, FR_RATE = 0.0725, 0.0481, 0.20


def us_row(i, jurisdiction, rate, entity):
    net = round(rng.uniform(250, 25_000), 2)
    return {
        "TXN_REF": f"US-{i:05d}",
        "INVOICE_NO": f"INV-US-{i:05d}",
        "LEGAL_ENTITY": entity,
        "TAX_AREA": jurisdiction,          # native: "CA-STATE" / "CO-DENVER"
        "DOC_DATE": f"2026-07-{rng.randint(1,28):02d}",
        "NET_AMT": f"{net:.2f}",
        "TAX_RATE_PCT": f"{rate*100:.2f}",  # native: PERCENT, not decimal
        "TAX_AMT": f"{round(net*rate,2):.2f}",
    }


# ------------------------------------------------ 1 · SUT US extract
rows = []
i = 0
for jur, rate, entity, n in (("CA-STATE", CA_RATE, "Atlas Beverages Inc", 40),
                             ("CO-DENVER", DEN_RATE, "Atlas Bottling West LLC", 40)):
    for _ in range(n):
        i += 1
        rows.append(us_row(i, jur, rate, entity))

# plant: WRONG_RATE x2 (CA @ 8.50%, Denver @ 5.31%)
for ref, pct in (("US-00007", 8.50), ("US-00052", 5.31)):
    r = next(x for x in rows if x["TXN_REF"] == ref)
    net = float(r["NET_AMT"])
    r["TAX_RATE_PCT"] = f"{pct:.2f}"
    r["TAX_AMT"] = f"{round(net*pct/100,2):.2f}"
    manifest.append({"type": "WRONG_RATE", "txn_id": ref,
                     "jurisdiction": "California (state)" if ref == "US-00007" else "Denver, CO (home rule)"})

# plant: AMOUNT_MISMATCH x2 (tax corrupted)
for ref, delta in (("US-00013", 99.0), ("US-00061", -37.5)):
    r = next(x for x in rows if x["TXN_REF"] == ref)
    r["TAX_AMT"] = f"{round(float(r['TAX_AMT'])+delta,2):.2f}"
    manifest.append({"type": "AMOUNT_MISMATCH", "txn_id": ref,
                     "jurisdiction": "California (state)" if ref == "US-00013" else "Denver, CO (home rule)"})

# plant: DUPLICATE_INVOICE (same INVOICE_NO posted twice)
dup_src = next(x for x in rows if x["TXN_REF"] == "US-00021")
dup = dict(dup_src)
dup["TXN_REF"] = "US-00021-R"          # a reposted line, same invoice number
rows.append(dup)
manifest.append({"type": "DUPLICATE_INVOICE", "txn_id": "US-00021",
                 "jurisdiction": "California (state)"})

with open("feeds/sut_extract.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# ------------------------------------------- 2 · FR ledger (ERP/VVC side)
fr_rows = []
for j in range(1, 41):
    net = round(rng.uniform(250, 25_000), 2)
    fr_rows.append({
        "piece_id": f"FR-{j:05d}",
        "facture": f"FA-{j:05d}",
        "societe": "Atlas Beverages France SAS",
        "date_doc": f"2026-07-{rng.randint(1,28):02d}",
        "montant_ht": f"{net:.2f}",           # native: French field names
        "taux_tva": "20,00",                   # native: comma decimal, percent
        "montant_tva": f"{round(net*FR_RATE,2):.2f}".replace(".", ","),
    })
with open("feeds/fr_ledger_extract.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(fr_rows[0].keys()), delimiter=";")  # native: semicolons
    w.writeheader()
    w.writerows(fr_rows)

# --------------------------------- 3 · e-invoice status feed
# every FR ledger piece SHOULD have a cleared e-invoice document...
docs = []
for r in fr_rows:
    docs.append({
        "documentId": f"DOC-{r['piece_id']}",
        "invoiceNumber": r["facture"],
        "sourceRef": r["piece_id"],
        "sellerParty": {"name": r["societe"], "country": "FR"},
        "totals": {"net": float(r["montant_ht"]),
                   "tax": float(r["montant_tva"].replace(",", "."))},
        "clearanceStatus": "CLEARED",
    })
# plant: MISSING_EINVOICE x2 — drop the docs for two ledger pieces
for ref in ("FR-00009", "FR-00027"):
    docs = [d for d in docs if d["sourceRef"] != ref]
    manifest.append({"type": "MISSING_EINVOICE", "txn_id": ref,
                     "jurisdiction": "France (CTC / e-invoice)"})
# plant: ORPHAN_REPORT — a ghost document referencing no ledger piece
docs.append({
    "documentId": "DOC-GHOST-00001", "invoiceNumber": "FA-99999",
    "sourceRef": "FR-99999", "sellerParty": {"name": "Atlas Beverages France SAS", "country": "FR"},
    "totals": {"net": 3120.00, "tax": 0.0}, "clearanceStatus": "CLEARED",
})
manifest.append({"type": "ORPHAN_REPORT", "txn_id": "FR-99999",
                 "jurisdiction": "France (CTC / e-invoice)"})

with open("feeds/einvoice_feed.json", "w", encoding="utf-8") as f:
    json.dump({"feedId": "einvoice-mock-2026-07", "documents": docs}, f, indent=1)

# ----------------------------------------------- 4 · period returns file
def total_tax(rs, key, conv=float):
    return round(sum(conv(r[key]) for r in rs), 2)

ca = [r for r in rows if r["TAX_AREA"] == "CA-STATE"]
den = [r for r in rows if r["TAX_AREA"] == "CO-DENVER"]
fr_tax = round(sum(float(r["montant_tva"].replace(",", ".")) for r in fr_rows), 2)

returns = [
    {"TAX_AREA": "CA-STATE", "PERIOD": "2026-07", "TXN_COUNT": len(ca),
     "TAX_REPORTED": f"{total_tax(ca, 'TAX_AMT'):.2f}"},
    {"TAX_AREA": "CO-DENVER", "PERIOD": "2026-07", "TXN_COUNT": len(den),
     # plant: RETURN_UNDERSTATED — report $412.18 less than the ledger sums to
     "TAX_REPORTED": f"{round(total_tax(den, 'TAX_AMT') - 412.18, 2):.2f}"},
    {"TAX_AREA": "FR", "PERIOD": "2026-07", "TXN_COUNT": len(fr_rows),
     "TAX_REPORTED": f"{fr_tax:.2f}"},
]
manifest.append({"type": "RETURN_UNDERSTATED", "txn_id": None,
                 "jurisdiction": "Denver, CO (home rule)"})

with open("feeds/period_returns.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(returns[0].keys()))
    w.writeheader()
    w.writerows(returns)

# --------------------------------------------------- 5 · the manifest
with open("feeds/known_answer_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1)



# ================================================================
# GERMANY — THE COUNTRY MOVE, connector edition. A new jurisdiction
# arrives as (a) one entry in feeds/jurisdictions_config.json and
# (b) its own feed files. NOTHING else changes.
# ================================================================
DE_RATE = 0.19
de_rows = []
for j in range(1, 31):
    net = round(rng.uniform(250, 25_000), 2)
    de_rows.append({
        "BelegNr": f"DE-{j:05d}",
        "RechnungsNr": f"RG-{j:05d}",
        "Gesellschaft": "Atlas Beverages GmbH",
        "Belegdatum": f"2026-07-{rng.randint(1,28):02d}",
        "Nettobetrag": f"{net:.2f}",
        "USt_Satz": "19,00",                        # native: comma decimal, percent
        "USt_Betrag": f"{round(net*DE_RATE,2):.2f}".replace(".", ","),
    })
with open("feeds/de_ledger_extract.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(de_rows[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(de_rows)

de_docs = []
for r in de_rows:
    de_docs.append({
        "documentId": f"DOC-{r['BelegNr']}", "invoiceNumber": r["RechnungsNr"],
        "sourceRef": r["BelegNr"],
        "sellerParty": {"name": r["Gesellschaft"], "country": "DE"},
        "totals": {"net": float(r["Nettobetrag"]),
                   "tax": float(r["USt_Betrag"].replace(",", "."))},
        "clearanceStatus": "CLEARED",
    })
# plant: MISSING_EINVOICE (DE) — drop one document
de_docs = [d for d in de_docs if d["sourceRef"] != "DE-00011"]
manifest.append({"type": "MISSING_EINVOICE", "txn_id": "DE-00011",
                 "jurisdiction": "Germany (CTC 2027-28)"})
# plant: WRONG_RATE (DE) — one ledger row at the old 16% crisis rate
r16 = next(x for x in de_rows if x["BelegNr"] == "DE-00023")
net16 = float(r16["Nettobetrag"])
r16["USt_Satz"] = "16,00"
r16["USt_Betrag"] = f"{round(net16*0.16,2):.2f}".replace(".", ",")
manifest.append({"type": "WRONG_RATE", "txn_id": "DE-00023",
                 "jurisdiction": "Germany (CTC 2027-28)"})
# rewrite the DE ledger with the planted rate error
with open("feeds/de_ledger_extract.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(de_rows[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(de_rows)
with open("feeds/de_einvoice_feed.json", "w", encoding="utf-8") as f:
    json.dump({"feedId": "einvoice-mock-DE-2026-07", "documents": de_docs}, f, indent=1)

# German period return (ties out; DE-23's recomputed 16% tax included as booked)
de_tax = round(sum(float(r["USt_Betrag"].replace(",", ".")) for r in de_rows), 2)
returns.append({"TAX_AREA": "DE", "PERIOD": "2026-07", "TXN_COUNT": len(de_rows),
                "TAX_REPORTED": f"{de_tax:.2f}"})
with open("feeds/period_returns.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(returns[0].keys()))
    w.writeheader()
    w.writerows(returns)

# THE CONFIG FILE — jurisdiction as configuration, shipped as data
with open("feeds/jurisdictions_config.json", "w", encoding="utf-8") as f:
    json.dump({
        "Germany (CTC 2027-28)": {"rate": 0.19, "entity": "Atlas Beverages GmbH",
                                   "regime": "ctc", "tax_area_code": "DE"},
    }, f, indent=1)

# refresh manifest with the German errors included
with open("feeds/known_answer_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1)
print(f"DE added: {len(de_rows)} rows, {len(de_docs)} docs; manifest now {len(manifest)} errors")

print(f"feeds written: {len(rows)} US rows, {len(fr_rows)} FR rows, "
      f"{len(docs)} e-invoice docs, {len(returns)} returns, "
      f"{len(manifest)} planted errors in manifest")
