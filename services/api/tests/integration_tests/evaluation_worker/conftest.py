"""Fixed checkout import roots for this Worker engineering test directory only."""

import sys
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())
for relative in ("services/evaluation/src", "services/evaluation/profile_support", "scripts"):
    sys.path.insert(0, str(ROOT / relative))
