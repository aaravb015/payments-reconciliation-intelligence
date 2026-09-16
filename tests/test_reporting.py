import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from src.operations import run_operations
from src.reporting import write_reports
from test_operations import feeds


def test_reproducible_report_bytes_and_audit_manifest(feeds,tmp_path):
    result=run_operations(*feeds,as_of='2026-02-10')
    first,second=tmp_path/'a',tmp_path/'b'
    write_reports(result,first)
    write_reports(result,second)
    assert {p.name:p.read_bytes() for p in first.iterdir()} == {p.name:p.read_bytes() for p in second.iterdir()}
    manifest=json.loads((first/'manifest.json').read_text())
    assert manifest['as_of_utc']=='2026-02-10T00:00:00'
    assert len(manifest['output_sha256'])==14
    for name,digest in manifest['output_sha256'].items():
        assert hashlib.sha256((first/name).read_bytes()).hexdigest()==digest
    assert manifest['source_counts']['internal']['included_rows']==1
    assert manifest['implementation_sha256']
    assert json.loads((first/'summary.json').read_text())['current_exposure_inr']==0


def test_report_escapes_source_strings_and_empty_tables(feeds,tmp_path):
    i,g,b=feeds
    i.loc[0,'merchant_id']='<script>alert(1)</script>'
    path=write_reports(run_operations(i,g.iloc[:0],b.iloc[:0],as_of='2026-02-10'),tmp_path)
    report=path.read_text()
    assert '<script>' not in report
    assert '&lt;script&gt;' in report
    write_reports(run_operations(*(f.iloc[:0] for f in feeds),as_of='2026-02-10'),tmp_path/'empty')
    assert 'undefined (no observations)' in (tmp_path/'empty'/'management_report.html').read_text()


def test_cli_runs_with_only_operational_sources_and_preserves_ids(feeds,tmp_path):
    source=tmp_path/'input'
    source.mkdir()
    for frame,name in zip(feeds,['internal_payments.csv','gateway_transactions.csv','bank_settlements.csv']):
        frame.to_csv(source/name,index=False)
    # Deliberately invalid labels must never be parsed; deleting them is also safe.
    (source/'truth_exceptions.csv').write_bytes(b'\xff\xfe\x00')
    script=Path('scripts/run_operations.py').resolve()
    args=[sys.executable,str(script),'--input-dir',str(source),'--as-of','2026-02-10','--output-dir',str(tmp_path/'out')]
    subprocess.run(args,cwd=tmp_path,check=True,capture_output=True,text=True)
    facts=pd.read_csv(tmp_path/'out'/'reconciliation_facts.csv',dtype={'transaction_id':str})
    assert facts.iloc[0].transaction_id=='001'
    manifest=json.loads((tmp_path/'out'/'manifest.json').read_text())
    assert set(manifest['source_sha256'])=={'internal_payments.csv','gateway_transactions.csv','bank_settlements.csv'}
    (source/'truth_exceptions.csv').unlink()
    subprocess.run(args,cwd=tmp_path,check=True,capture_output=True,text=True)
    assert_frame_equal(facts,pd.read_csv(tmp_path/'out'/'reconciliation_facts.csv',dtype={'transaction_id':str}))
