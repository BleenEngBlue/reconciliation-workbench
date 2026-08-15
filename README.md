# Reconciliation Workbench

**Human-in-the-loop AI exception review for multi-jurisdiction tax compliance.**
Deterministic core, AI at the edges, humans at the points of consequence.

A deterministic rules engine finds every exception, an LLM triage layer explains
each one and proposes a disposition, a human approves or overrides, and every
decision lands in an exportable audit trail. An eval panel scores each run
against seeded ground truth — quality is measured, not claimed.

**All data is synthetic** — generated from a seed or shipped as fixture files.
No PII, no real financial data. This is a personal engineering prototype.

## Measured results

With the default seed, the eval panel reports:

| Run | Ground truth | Caught | Recall | Precision (false-positive control) |
|---|---|---|---|---|
| Synthetic pipeline (`run_demo.py`) | 21 seeded errors | 21 | 1.0 | 1.0 |
| Connector-fed pipeline (`run_connector_demo.py`) | 11 planted errors | 11 | 1.0 | 1.0 |

Seeded errors are the golden set: ground truth is known by construction, so
recall ("did we catch every seeded error?") and precision ("does every flag
trace back to a real one?") are computed on every run and printed with the
runtime. Six error classes are covered: wrong rate, arithmetic mismatch,
duplicate invoice, missing e-invoice (CTC mandate breach), orphan e-report,
and understated period return.

## Quickstart

Requires Python 3.10+.

```bash
pip install -r requirements.txt

python run_demo.py        # full pipeline in the terminal: data -> checks -> triage -> evals
python app.py             # Gradio UI at http://127.0.0.1:7860
```

Useful flags for `run_demo.py`: `--seed 7` (different dataset), `--n 100`
(transactions per jurisdiction), `--json` (dump the full run record for
diffing), `--llm` (LLM triage — see below).

### Connector demo — feeds in, same pipeline out

```bash
python make_feeds.py            # writes feeds/* (deterministic fixtures + known-answer manifest)
python run_connector_demo.py    # feeds -> connectors -> checks -> triage -> evals
```

The fixtures imitate real upstream export shapes — a US transaction extract
(percent rates), French and German ledger extracts (semicolon CSVs, comma
decimals, native field names), an e-invoice network status feed (nested JSON),
and a period returns file. Each passes through a declarative field-mapping
config into the same canonical model the app runs on; the downstream pipeline
cannot tell the data didn't come from the generator.

**Jurisdictions are configuration, not code.** `feeds/jurisdictions_config.json`
adds a fourth country — entity, rate checks, e-invoice linkage, filing gate,
and eval coverage — with zero code changes downstream.

### AI triage modes

Offline mode is the default: deterministic explainer templates, transparent
confidence scores, no network calls — the demo cannot be broken by an API
outage. LLM mode is opt-in and requires **both** an API key and an explicit
flag (no silent data egress just because a key exists):

```bash
export OPENAI_API_KEY=sk-...          # PowerShell: $env:OPENAI_API_KEY="sk-..."
export RECONCILIATION_USE_LLM=1       # PowerShell: $env:RECONCILIATION_USE_LLM="1"
python app.py
```

Either way, the AI never decides what is wrong and never assigns severity —
detection stays deterministic and auditable. AI output is schema-constrained,
provenance-labeled (`triage_mode` on every record), and falls back to offline
templates on any failure.

## How it works

| File | Role |
|---|---|
| `data_gen.py` | Synthetic multi-entity dataset with seeded errors (the golden set) |
| `checks.py` | Deterministic core — six reconciliation checks, pure code, zero AI |
| `triage.py` | AI at the edges — explanations + proposed dispositions, automation ladder |
| `evals.py` | Eval panel — per-type recall, precision, unexplained flags, runtime |
| `app.py` | Gradio UI — exception queue, filing gates, decision log, audit export |
| `connectors.py` | Declarative field maps: native feed shapes -> canonical model |
| `make_feeds.py` | Generates the fixture feeds + known-answer manifest |
| `run_demo.py` / `run_connector_demo.py` | One-command CLI runs of each pipeline |

Governance features worth noting: filing gates block a return's release while
critical or high exceptions in its jurisdiction are undecided; every decision
records the reviewer's identity and timestamp; the audit CSV export is
sanitized against spreadsheet formula injection; UI inputs are clamped
server-side.

## Deploying publicly

`app.py` binds to localhost by default. For any public deployment, set
`RECONCILIATION_AUTH_USER` and `RECONCILIATION_AUTH_PASS` as environment
secrets (never in code) and authentication is enforced; the authenticated
username then flows into every decision-log row.

## License

Personal portfolio project — © 2026 Monica Wu. Contact me before reusing.
