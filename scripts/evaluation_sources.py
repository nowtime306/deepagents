"""Fixed source inventory shared by isolated targets and their suite coordinator."""
import hashlib


def source_fingerprint(root):
    sources=set((root/'services/api/app').rglob('*.py'))
    sources.update((root/'services/api/migrations').glob('*.sql'))
    sources.update((root/'services/evaluation/src/evaluation_runner').rglob('*.py'))
    sources.update((root/'services/evaluation/profile_support').glob('*.py'))
    sources.update((root/'services/evaluation/datasets').glob('*.json'))
    sources.update((root/'scripts').glob('*evaluation*.py'))
    sources.update((root/'scripts').glob('*evaluation*.ps1'))
    sources.update(path for path in [root/'services/evaluation/uv.lock',root/'services/evaluation/pyproject.toml',
                   root/'services/api/uv.lock',root/'services/api/pyproject.toml',
                   root/'services/evaluation/profile_support/suite-capabilities.json'] if path.is_file())
    return {str(path.relative_to(root)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)}
