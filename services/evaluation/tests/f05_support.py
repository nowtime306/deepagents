"""Small test fixtures and exact official script loading; no product-module override."""
import importlib.util
from pathlib import Path
from uuid import uuid4

from evaluation_runner.contracts import Target

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


def load_script(filename):
    if filename not in {"evaluation-suite.py", "evaluation-target.py"}:
        raise ValueError("Only the two fixed official entrypoints are test-loadable")
    spec = importlib.util.spec_from_file_location("f05_test_" + filename.replace("-", "_"), ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target():
    return Target(environment_id=str(uuid4()), api_base_url="http://127.0.0.1:8199", platform_commit="a" * 40,
                  owner_id=str(uuid4()), required_migrations=["084_vector_compatibility.sql"],
                  fixture_profile={"id": "controlled-v1", "version": 1}, resource_bindings={"AET-W01": "fake"})


def attestation(value):
    return {"environment_id": value.environment_id, "owner_id": value.owner_id,
            "platform_commit": value.platform_commit, "schema_migrations": value.required_migrations,
            "database_is_test": True, "permissions": ["evaluation.read"],
            "fixture_profile": value.fixture_profile.model_dump(),
            "execution_limits": {"schema_version": 1, "enforcement": "controlled-model-factory-v1",
                                 "scope": "resource-run-root", "profiles": {
                                     "AET-A02": {"mode": "ask", "max_model_calls": 1, "max_tokens": 15},
                                     "AET-F01": {"mode": "workflow", "max_model_calls": 4, "max_tokens": 60}}}}
