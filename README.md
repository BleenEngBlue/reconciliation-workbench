# Tax Reconciliation Workbench

**Designing reliable AI that a finance reviewer can trust.**
Deterministic core, AI at the edges, humans at the points of consequence.

A human-in-the-loop exception-review demo for multi-jurisdiction tax reconciliation.

**Live demo:** login-protected while it is a prototype (credentials on request)
**Case study:** https://wumonica.com/case-studies/tax-reconciliation-workbench/
**Portfolio:** https://wumonica.com

> **All data is synthetic**, generated from a seed or shipped as fixture files. No PII, no real financial data. Personal engineering prototype.

## At a glance

- **Built in 2 days.**
- **100% recall, zero false positives** against seeded ground truth (default seed), scored on every run.
- **Offline by default.** An API outage cannot break it.
- **Every decision is logged** to an exportable audit trail.

## How it works

```
Deterministic checks find exceptions
        ↓
AI triage explains each one and proposes a disposition
        ↓
A human approves or overrides
        ↓
Every decision is logged to an exportable audit trail
        ↓
An eval panel measures precision and recall against seeded ground truth
```

**The AI never decides what is wrong and never assigns severity.** Detection stays deterministic and auditable.

## Measured results

With the default seed, the eval panel reports:

| Run | Ground truth | Caught | Recall | Precision |
|---|---|---|---|---|
| Synthetic pipeline (`run_demo.py`) | 21 seeded errors | 21 | 1.0 | 1.0 |
| Connector-fed pipeline (`run_connector_demo.py`) | 11 planted errors | 11 | 1.0 | 1.0 |

- **Recall:** did we catch every seeded error?
- **Precision:** does every flag trace back to a real error?
- Both are computed and printed on every run, so quality is measured, not claimed.
- **Six error classes:** wrong rate, arithmetic mismatch, duplicate invoice, missing e-invoice (CTC mandate breach), orphan e-report, understated period return.

## Built for trust

- **Filing gates** block a return's release while critical or high exceptions in its jurisdiction are undecided.
- **Reviewer identity and timestamp** are recorded on every decision.
- **AI output is schema-constrained** and labeled with its source (`triage_mode` on every record). Any failure falls back to offline templates.
- **No silent data egress.** LLM mode needs both an API key and an explicit flag.
- **Audit CSV export** is sanitized against spreadsheet formula injection.
- **UI inputs are clamped** server-side.
- **Jurisdictions are configuration, not code.** Adding a country is one config entry and zero downstream code changes.

## Quickstart

Requires Python 3.10+.

```bash
pip install -r requirements.txt

python run_demo.py        # full pipeline in the terminal: data -> checks -> triage -> evals
python app.py             # Gradio UI at http://127.0.0.1:7860
```

`run_demo.py` flags: `--seed 7` (different dataset), `--n 100` (transactions per jurisdiction), `--json` (dump the full run record for diffing), `--llm` (LLM triage).

<details>
<summary><b>Connector demo: feeds in, same pipeline out</b></summary>

```bash
python make_feeds.py            # writes feeds/* (deterministic fixtures + known-answer manifest)
python run_connector_demo.py    # feeds -> connectors -> checks -> triage -> evals
```

The fixtures imitate real upstream export shapes: a US transaction extract (percent rates), French and German ledger extracts (semicolon CSVs, comma decimals, native field names), an e-invoice network status feed (nested JSON), and a period returns file. Each passes through a declarative field-mapping config into the same canonical model the app runs on. The downstream pipeline cannot tell the data didn't come from the generator.

`feeds/jurisdictions_config.json` adds a fourth country (entity, rate checks, e-invoice linkage, filing gate, eval coverage) with zero code changes downstream.

</details>

<details>
<summary><b>AI triage modes</b></summary>

**Offline (default):** deterministic explainer templates, transparent confidence scores, no network calls.

**LLM (opt-in):** requires **both** an API key and an explicit flag.

```bash
export OPENAI_API_KEY=sk-...          # PowerShell: $env:OPENAI_API_KEY="sk-..."
export RECONCILIATION_USE_LLM=1       # PowerShell: $env:RECONCILIATION_USE_LLM="1"
python app.py
```

</details>

<details>
<summary><b>Code map</b></summary>

| File | Role |
|---|---|
| `data_gen.py` | Synthetic multi-entity dataset with seeded errors (the golden set) |
| `checks.py` | Deterministic core: six reconciliation checks, pure code, zero AI |
| `triage.py` | AI at the edges: explanations + proposed dispositions, automation ladder |
| `evals.py` | Eval panel: per-type recall, precision, unexplained flags, runtime |
| `app.py` | Gradio UI: exception queue, filing gates, decision log, audit export |
| `connectors.py` | Declarative field maps: native feed shapes -> canonical model |
| `make_feeds.py` | Generates the fixture feeds + known-answer manifest |
| `run_demo.py` / `run_connector_demo.py` | One-command CLI runs of each pipeline |

</details>

<details>
<summary><b>Deploying publicly</b></summary>

`app.py` binds to localhost by default. For any public deployment, set `RECONCILIATION_AUTH_USER` and `RECONCILIATION_AUTH_PASS` as environment secrets (never in code). Authentication is then enforced, and the authenticated username flows into every decision-log row.

</details>

## License

Personal portfolio project, © 2026 Monica Wu. Contact me before reusing.
