---
title: Reconciliation Workbench
emoji: 🧾
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.22.0
app_file: app.py
pinned: false
---

# Reconciliation Workbench

A human-in-the-loop exception-review demo for multi-jurisdiction tax
reconciliation. **All data is synthetic** — generated from a seed or shipped
as fixture files; no PII, no real financial data.

Pipeline: deterministic checks find exceptions → an AI triage layer explains
and proposes dispositions → a human approves or overrides → every decision is
logged to an exportable audit trail → an eval panel measures precision and
recall against seeded ground truth.

## Connector demo (CLI)

The repo also includes a **connector layer**: mock source feeds shaped like
real upstream systems — ERP ledger extracts in native locales (semicolon
CSVs, comma decimals, local field names), an e-invoice network status feed
(nested JSON), and a period returns file — each translated through a
declarative field-mapping config into the same canonical model the app runs
on. Jurisdictions themselves arrive as data: `feeds/jurisdictions_config.json`
adds a fourth country (entity, rate checks, e-invoice linkage, filing gate,
eval coverage) with zero code changes downstream.

```bash
python make_feeds.py            # writes feeds/* (deterministic fixtures, results appear in the terminal)
python run_connector_demo.py    # feeds → connectors → checks → triage → evals (results in terminal)
```

The fixtures ship with a known-answer manifest (planted errors), so the
connector-fed run is measured the same way as the synthetic one: recall,
precision, and unexplained-flag count, printed at the end of every run.

Access to the app is authenticated. This is a personal engineering prototype.
