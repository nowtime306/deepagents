"""Closed version selection for CLI control checks, without platform execution grants."""

import hashlib
import importlib
from importlib.resources import files
from importlib.resources.abc import Traversable
from types import ModuleType

from evaluation_runner.contracts import Case, Dataset
from evaluation_runner.journal import digest

CASES = ("AET-A05", "AET-W07", "AET-W08", "AET-F07")
RULES_ID = "controlled-a20-v2"
MODULES = {case: f"evaluation_runner.profiles_v2.{case[4:].lower()}.handler" for case in CASES}
LIMITS = {
    "AET-A05": {"provider_calls": 5, "provider_tokens": 75, "roots": 2, "seconds": 120, "sandboxes": 0},
    "AET-W07": {"provider_calls": 1, "provider_tokens": 15, "roots": 1, "seconds": 300, "sandboxes": 2},
    "AET-W08": {"provider_calls": 6, "provider_tokens": 90, "roots": 1, "seconds": 300, "sandboxes": 1},
    "AET-F07": {"provider_calls": 1, "provider_tokens": 15, "roots": 4, "seconds": 120, "sandboxes": 0},
}
CATALOG_SHA = "fa27d961ba9dc74a6285b90467718aa97bafd60821bd652f75bb490949b8a5f8"
SOURCE_PINS: dict[str, str] = {
    "a05/handler.py": "c7a18ab0929d077cd31bec76ea4166863aa2a042ede636a94197c523b3ee8500",
    "a05/protocol.py": "857d203cd2e1d12128aae58bae4f38a01b7df30d96ff41ce8235c71ab3373435",
    "f07/contract.py": "77180bbf8c60c64e2f9937cecd86658b0be92b4c273ad955f632ab762f8f4269",
    "f07/handler.py": "2aa8190c894232cd1f60ae2084806762efcfe5c153e815ea2737c92520baaa91",
    "w07/handler.py": "aed703a0b7bbdd3c233b1225fb2968ff8add2a52b816f8dd94ed31691bf6f010",
    "w08/contract.py": "bf100ea8ab726dcf724c8d945864f0abd578fb0f5f98981efbcb4fed64872dcd",
    "w08/handler.py": "7aacb2dfa042f98245c60b4872010ecba3bcb240d2a96e09fd34c0ac73928427",
    "w08/validation.py": "b68e24e9d5de9843a3eeea27f9ef98269f3520d3fad1e1108be77d7161e424b5"
}


def _verified_package() -> Traversable:
    package = files(__package__)
    for name, expected in SOURCE_PINS.items():
        if hashlib.sha256(package.joinpath(name).read_bytes()).hexdigest() != expected:
            raise ValueError("Reviewed v2 handler or contract source changed")
    return package


def _catalog() -> Dataset:
    package = _verified_package()
    raw = package.joinpath("definitions/catalog.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA:
        raise ValueError("Reviewed v2 catalog bytes changed")
    dataset = Dataset.model_validate_json(raw)
    if tuple(case.case_id for case in dataset.cases) != CASES:
        raise ValueError("Fixed v2 catalog identity changed")
    if any(case.fixture_profile.version != 2 for case in dataset.cases):
        raise ValueError("Original v1 definitions cannot be relabeled as v2")
    return dataset


def _checked_handler(specimen: Case) -> ModuleType:
    module = importlib.import_module(MODULES[specimen.case_id])
    checker = getattr(module, "check_specimen", None) or getattr(module, "check", None)
    if checker is None:
        raise ValueError("Reviewed handler contract checker absent")
    checker(specimen)
    return module


def _budgets(specimen: Case) -> dict[str, object]:
    return {
        "declared": specimen.budgets.model_dump(),
        "actual_limits": dict(LIMITS[specimen.case_id]),
        "maximum_model_calls": specimen.budgets.max_model_calls,
        "maximum_tokens": specimen.budgets.max_tokens,
        "maximum_sandboxes": LIMITS[specimen.case_id]["sandboxes"],
        "sandbox_enforcement": "controlled-sandbox-reserve-v1",
    }


def _description(specimen: Case, dataset: Dataset) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": specimen.case_id,
        "profile_version": 2,
        "rules_id": RULES_ID,
        "catalog_digest": digest(dataset.model_dump(mode="json")),
        "case_definition_digest": digest(specimen.model_dump(mode="json")),
        "handler": MODULES[specimen.case_id],
        "implemented": True,
        "status": "CLI_CONTROLLED_ONLY",
        "business_admission": "NOT_GRANTED_BY_PROFILE_REGISTRATION",
        **_budgets(specimen),
    }


def selection(case_id: str, profile_version: int = 1) -> tuple[Case, Dataset, dict[str, object]]:
    """Select a fixed definition; default v1 retains its original implementation claims."""
    if type(profile_version) is not int or profile_version not in {1, 2}:
        raise ValueError("Only explicit fixed profile versions 1 and 2 are allowed")
    if profile_version == 1:
        from evaluation_runner import fixed_run

        dataset = fixed_run.catalog()
        specimen = next((case for case in dataset.cases if case.case_id == case_id), None)
        if specimen is None:
            raise ValueError("Unknown original case")
        return specimen, dataset, fixed_run.describe(specimen, dataset)
    if case_id not in CASES:
        raise ValueError("Unknown separately reviewed v2 case")
    dataset = _catalog()
    specimen = next(case for case in dataset.cases if case.case_id == case_id)
    _checked_handler(specimen)
    return specimen, dataset, _description(specimen, dataset)
