"""Deterministic CSV/JSON/HTML management reporting, independent of evaluation."""
from __future__ import annotations

import hashlib
import html
import json
import platform
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.operations import OperationalResult, SQL_DIR


def _records(frame):
    # pandas handles numpy scalars, missing values and timestamps consistently.
    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=10))


def _bars(frame, label, value):
    selected = frame.sort_values([value, label], ascending=[False, True]).head(10)
    maximum = max(float(selected[value].max()), 1) if len(selected) else 1
    rows = []
    for _, row in selected.iterrows():
        width = float(row[value]) / maximum * 100
        rows.append(f'<div class="bar-row"><span>{html.escape(str(row[label]))}</span>'
                    f'<div class="track"><div class="bar" style="width:{width:.4f}%"></div></div>'
                    f'<strong>{float(row[value]):,.2f}</strong></div>')
    return ''.join(rows) or '<p>No observations.</p>'


def render_report(result: OperationalResult) -> str:
    tables = result.tables
    summary = _records(tables["summary"])[0]
    cards = [
        ("Internal payments", f'{summary["internal_payments"]:,}'),
        ("Payments with exceptions (all sources)", f'{summary["exception_transactions"]:,}'),
        ("Rule signals", f'{int(summary["exception_count"]):,}'),
        ("Current estimated exposure", f'₹{summary["current_exposure_inr"]:,.2f}'),
        ("Of which: overdue cash", f'₹{summary["overdue_cash_inr"]:,.2f}'),
        ("Historical late cash · excluded from current", f'₹{summary["historical_late_cash_inr"]:,.2f}'),
    ]
    cards_html = ''.join(f'<article><span>{html.escape(k)}</span><strong>{v}</strong></article>' for k, v in cards)
    def table(name, columns=None, limit=None):
        frame = tables[name]
        if columns is not None:
            frame = frame[columns]
        if limit:
            frame = frame.head(limit)
        return '<div class="table-wrap">' + frame.to_html(index=False, border=0, escape=True, float_format=lambda n: f'{n:,.2f}') + '</div>'
    rate = summary['exception_transaction_rate']
    rate_text = 'undefined (no observations)' if rate is None else f'{rate:.2%}'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payments reconciliation · operations report</title>
<style>
body{{font-family:system-ui,sans-serif;color:#172b3a;background:#f3f6f8;margin:0;line-height:1.5}}
main{{max-width:1200px;margin:auto;padding:32px}}h1{{font-size:32px;line-height:1.2}}h2{{margin-top:34px}}
.kicker{{color:#007b73;font-weight:700;letter-spacing:.08em}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}
article,section{{background:white;border:1px solid #dbe4e9;border-radius:10px;padding:20px}}article span{{display:block;font-size:13px;color:#456}}article strong{{display:block;font-size:26px;margin-top:6px}}
.notice{{border-left:4px solid #007b73;padding:12px 18px;background:#e7f2f0}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;font-size:12px;width:100%}}th,td{{padding:9px;text-align:left;border-bottom:1px solid #e2e8ed;white-space:nowrap}}th{{background:#eaf0f4}}
.bar-row{{display:grid;grid-template-columns:minmax(110px,1fr) 3fr 100px;gap:12px;align-items:center;margin:9px 0;font-size:13px}}.bar-row span{{overflow-wrap:anywhere}}.track{{background:#eaf0f4;height:18px}}.bar{{background:#007b73;height:100%}}footer{{color:#567;margin-top:28px}}@media(max-width:600px){{main{{padding:16px}}.bar-row{{grid-template-columns:100px 1fr 85px;font-size:11px}}}}
</style></head><body><main>
<p class="kicker">PAYMENTS RECONCILIATION INTELLIGENCE / PHASE 2</p>
<h1>Operational reconciliation report</h1>
<p>Reporting cutoff: <strong>{html.escape(result.metadata['as_of_utc'])} UTC</strong> · INR</p>
<p class="notice">Controlled synthetic demonstration. Exposure is an investigation estimate, not realized loss, recovered cash, or production accuracy.</p>
<div class="cards">{cards_html}</div>
<p>Exception transaction rate: <strong>{rate_text}</strong> = distinct flagged transactions / all observed transaction IDs.
Pending payments: {summary['pending_payments']:,}. Orphan transactions: {summary['orphan_transactions']:,}.</p>
<h2>Where current exposure is concentrated</h2><section>{_bars(tables['merchant_metrics'], 'dimension_value', 'current_exposure_inr')}</section>
<h2>Rule signal counts</h2><section>{_bars(tables['exception_type_metrics'], 'exception_type', 'exception_count')}</section>
<p>One payment can trigger several rules. Rule-level amounts must not be added to estimate portfolio exposure.</p>
<h2>Prioritized investigation queue · first 25</h2>
{table('investigation_queue', ['priority_rank','transaction_id','merchant_id','severity','exception_types','current_exposure_inr','age_days','case_status','recommended_actions'], 25)}
<h2>Aging and overdue cash</h2>{table('aging_metrics')}
<h2>Daily payment cohorts</h2>{table('daily_metrics', ['dimension_value','internal_payments','exception_count','exception_transactions','exception_transaction_rate','current_exposure_inr','overdue_cash_inr'])}
<h2>Settlement delay by provider</h2>{table('settlement_delay_metrics')}
<h2>Payment-method concentration</h2>{table('payment_method_metrics', ['dimension_value','observed_transactions','exception_transactions','exception_transaction_rate','current_exposure_inr','exposure_share'])}
<h2>Gross and net source controls</h2>{table('summary', ['internal_gross_inr','gateway_gross_inr','gateway_net_inr','bank_gross_inr','bank_net_inr'])}
<p>Source totals retain duplicates and orphans. Comparable deltas in the CSVs exclude ambiguous joins. Daily payment cohorts use internal creation date, with source event date for orphans; daily_settlements.csv uses actual bank settlement date.</p>
<h2>Interpretation and controls</h2>
<ul><li>Financial discrepancy exposure uses the largest current monetary rule estimate per transaction to avoid overlapping signals. Independent losses on one transaction may therefore be understated.</li>
<li>Overdue expected net cash is separate from financial discrepancies. Late cash already received is historical and is excluded from current exposure.</li>
<li>Status disagreements and missing gateway records with observed settlement are data-integrity investigations with zero direct estimated exposure.</li>
<li>Queue score = severity tier × 100 + min(current exposure / ₹10,000, 30) + min(age in days, 30). Stable tie-breaking uses amount, age, transaction ID.</li>
<li>Provider attribution is unknown unless supplied in gateway_id. No provider is inferred from transaction IDs.</li>
<li>This is an event-time snapshot, without ingestion history, business-day calendars, FX, refunds, split settlements, or a persistent case-resolution workflow.</li></ul>
<footer>All full tables are available as adjacent CSV files. Run configuration, versions, source hashes and output hashes are recorded in manifest.json.</footer>
</main></body></html>'''


def write_reports(result: OperationalResult, output_dir: str | Path, *, source_hashes=None) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, frame in result.tables.items():
        path = output / f"{name}.csv"
        frame.to_csv(path, index=False, float_format="%.6f", date_format="%Y-%m-%dT%H:%M:%S", lineterminator="\n")
        paths.append(path)
    summary = output / "summary.json"
    summary.write_text(json.dumps(_records(result.tables['summary'])[0], indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    report = output / "management_report.html"
    report.write_text(render_report(result), encoding="utf-8")
    paths.extend([summary, report])
    manifest = dict(result.metadata)
    manifest.update({
        "versions": {"python": platform.python_version(), "duckdb": duckdb.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "source_sha256": source_hashes or {},
        "implementation_sha256": {str(p.relative_to(SQL_DIR.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted([*SQL_DIR.glob('*.sql'), Path(__file__), Path(__file__).with_name('operations.py')])},
        "output_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)},
    })
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    return report
