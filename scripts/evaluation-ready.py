"""Read-only cleanup evidence check, executed only by the independent evaluation venv."""
import argparse
import hashlib
import json
from pathlib import Path

from evaluation_runner import network
from evaluation_runner.contracts import Target
from evaluation_runner.fixed_audit import validate_ready
from evaluation_runner.journal import digest


def check(directory):
    state=json.loads((directory/'target-state.json').read_text(encoding='utf-8'))
    target=Target.model_validate_json((directory/'target-manifest.json').read_text(encoding='utf-8'))
    ready=validate_ready(directory.parent/'result')
    if (any(getattr(target,key)!=state[key] for key in ('environment_id','owner_id','platform_commit'))
            or ready['environment_id']!=state['environment_id'] or ready['owner_id']!=state['owner_id']
            or ready['target_digest']!=digest(target.model_dump())
            or ready['suite_context']['candidate_commit']!=state['platform_commit']):
        raise ValueError('Cleanup evidence belongs to a different recorded target')
    with (directory.parent/'result/report.json').open('rb') as source:
        report_bytes=source.read(131073)
    if len(report_bytes)>131072 or hashlib.sha256(report_bytes).hexdigest()!=ready['files']['report.json']:
        raise ValueError('Report changed after receipt validation')
    report=json.loads(report_bytes)
    if not report['attempts'] or any(row.get('cleanup_status')!='CLEAN' for row in report['attempts']):
        raise ValueError('Complete clean evidence is required before removing the database')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',required=True,type=Path)
    args=parser.parse_args()
    try:
        network.install_guard(None)
        check(args.state.resolve())
    except Exception as error:
        print(json.dumps({'status':'RETAINED','error_type':type(error).__name__}))
        raise SystemExit(3)
    print(json.dumps({'status':'VERIFIED'}))
