import json
import os
import subprocess
import sys

import pytest
from test_core import target

from evaluation_runner.contracts import Case, Dataset

CHILD = r'''
import json, locale, sys
from pathlib import Path
locale.setlocale(locale.LC_CTYPE, 'Chinese_China.936')
assert locale.getencoding().lower() in ('cp936', '936', 'gbk'), locale.getencoding()
assert sys.flags.utf8_mode == 0
from evaluation_runner import __main__ as cli
class Platform:
    def __init__(self, target): self.target=target
    def login(self): pass
    def attest(self): return {'environment_id': self.target.environment_id}
    def close(self): pass
cli.Platform=Platform
sys.argv=['evaluation_runner','preflight','--target-manifest',sys.argv[1],'--dataset',sys.argv[2],'--output',sys.argv[3]]
code=cli.main()
if code == 0:
    from evaluation_runner.journal import Journal
    from evaluation_runner.reports import write_report
    directory=Path(sys.argv[-1])
    journal=Journal(directory)
    journal.data['attempts']=[{'case_id':'AET-A02','repeat_index':1,'task_outcome':'PASS',
                              'cleanup_status':'CLEAN','case_snapshot':{'input':'中文📘'}}]
    journal.save(); journal.close()
    journal=Journal(directory)
    report=write_report(directory,journal.data)
    assert report['attempts'][0]['case_snapshot']['input']=='中文📘'
    journal.close()
    assert '评测' in (directory/'report.md').read_text(encoding='utf-8')
sys.exit(code)
'''


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows cp936 isolated interpreter regression")
@pytest.mark.parametrize("valid", [True, False])
def test_isolated_cp936_cli_and_journal_handle_utf8_chinese_emoji(tmp_path, valid):
    manifest, dataset_file, output = tmp_path / "target.json", tmp_path / "中文📘.json", tmp_path / "output"
    manifest.write_text(target().model_dump_json(), encoding="utf-8")
    specimen = Case(case_id="AET-A02", name="中文评测📘", mode="ask", input="中文📘", requirements=["AE-05"], tags=[],
                    fixture_profile={"id": "ask-plain", "version": 1},
                    validators=[{"registered_id": "terminal_status", "parameters": {"expected": "SUCCEEDED"}}])
    encoded = Dataset(dataset_id="中文📘", dataset_version=1, cases=[specimen]).model_dump_json()
    if valid:
        dataset_file.write_text(encoded, encoding="utf-8")
    else:
        dataset_file.write_bytes(b"\xff\xfe invalid UTF-8")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    completed = subprocess.run([sys.executable, "-I", "-c", CHILD, str(manifest), str(dataset_file), str(output)],
                               check=False, capture_output=True, env=environment, timeout=30)
    assert completed.returncode == (0 if valid else 2), completed.stderr.decode(errors="replace")
    assert (output / "preflight.json").exists() is valid
    if valid:
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        assert report["attempts"][0]["case_snapshot"]["input"] == "中文📘"
