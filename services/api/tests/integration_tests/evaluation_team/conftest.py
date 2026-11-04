"""Fixed checkout imports for this engineering directory; no environment or service setup."""

import sys
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "services/api/app").is_dir()
)
for relative in (
    "services/api",
    "services/api/tests/integration_tests",
    "services/evaluation/src",
    "services/evaluation/profile_support",
):
    sys.path.insert(0, str(ROOT / relative))
