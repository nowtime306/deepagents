"""Fixed source inventory shared by isolated targets and their suite coordinator."""
import hashlib
import json
from pathlib import Path
import subprocess


# Editable sources are repository archives; installed packages are identity inputs
# only. No virtual environment is copied into an evaluation export.
DEPENDENCIES = {
    'deepagents.graph': ('libs/deepagents/deepagents/graph.py', 'editable-source'),
    'deepagents._messages_reducer': ('libs/deepagents/deepagents/_messages_reducer.py', 'editable-source'),
    'deepagents.middleware.filesystem': ('libs/deepagents/deepagents/middleware/filesystem.py', 'editable-source'),
    'langgraph.pregel.main': ('services/api/.venv/Lib/site-packages/langgraph/pregel/main.py', 'api-runtime'),
    'langgraph.channels.delta': ('services/api/.venv/Lib/site-packages/langgraph/channels/delta.py', 'api-runtime'),
    'langgraph.checkpoint.postgres.aio': ('services/api/.venv/Lib/site-packages/langgraph/checkpoint/postgres/aio.py', 'api-runtime'),
}


def runtime_origins(root: Path) -> dict[str, str]:
    """Resolve the API interpreter's actual modules, never the runner environment."""
    interpreter = root / 'services/api/.venv/Scripts/python.exe'
    code = ('import importlib.util,json;print(json.dumps({name:importlib.util.find_spec(name).origin '
            'for name in ' + repr(tuple(DEPENDENCIES)) + '}))')
    result = subprocess.run([str(interpreter), '-I', '-c', code], check=True,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    return json.loads(result.stdout)


def validate_runtime(root: Path) -> None:
    """Reject missing, changed, or differently resolved v2 execution dependencies."""
    contract = root / 'services/evaluation/profile_support/suite-capabilities-v2.json'
    recorded = json.loads(contract.read_bytes()).get('runtime_dependencies', {})
    if set(recorded) != set(DEPENDENCIES):
        raise ValueError('The reviewed v2 runtime dependency set is incomplete')
    origins = runtime_origins(root)
    if set(origins) != set(DEPENDENCIES):
        raise ValueError('The API interpreter resolved a different dependency set')
    for name, (relative, kind) in DEPENDENCIES.items():
        path = root / relative
        row = recorded[name]
        if (row.get('path') != relative or row.get('kind') != kind
                or not path.is_file() or Path(origins[name]).resolve() != path.resolve()
                or hashlib.sha256(path.read_bytes()).hexdigest() != row.get('sha256')):
            raise ValueError('The reviewed v2 API runtime dependency changed: ' + name)


def source_fingerprint(root: Path, *, profile_version: int = 1) -> dict[str, str]:
    if type(profile_version) is not int or profile_version not in {1, 2}:
        raise ValueError('Invalid source inventory profile version')
    if profile_version == 2:
        validate_runtime(root)
    sources=set((root/'services/api/app').rglob('*.py'))
    sources.update((root/'services/api/migrations').glob('*.sql'))
    sources.update((root/'services/evaluation/src/evaluation_runner').rglob('*.py'))
    sources.update((root/'services/evaluation/profile_support').rglob('*.py'))
    sources.update((root/'services/evaluation/profile_support').rglob('*.json'))
    sources.update((root/'services/evaluation/src/evaluation_runner').rglob('*.json'))
    sources.update((root/'services/evaluation/datasets').rglob('*.json'))
    sources.update((root/'scripts').glob('*evaluation*.py'))
    sources.update((root/'scripts').glob('*evaluation*.ps1'))
    sources.update(root/relative for relative, _ in DEPENDENCIES.values() if (root/relative).is_file())
    sources.update(path for path in [root/'services/evaluation/uv.lock',root/'services/evaluation/pyproject.toml',
                   root/'services/api/uv.lock',root/'services/api/pyproject.toml',
                   root/'services/evaluation/profile_support/suite-capabilities.json'] if path.is_file())
    return {str(path.relative_to(root)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)}
