import httpx
import pytest
from test_core import case, target

from evaluation_runner.contracts import Check, Dataset, Target
from evaluation_runner.facts import verify


@pytest.mark.parametrize("kind", ["target", "dataset", "check"])
def test_boolean_schema_version_is_not_integer_one(kind):
    if kind == "target":
        data = target().model_dump()
        data["schema_version"] = True
        operation = lambda: Target.model_validate(data)
    elif kind == "dataset":
        operation = lambda: Dataset(schema_version=True, dataset_id="bad", dataset_version=1, cases=[case()])
    else:
        operation = lambda: Check(registered_id="operation_count", version=True, parameters={"expected": 0})
    with pytest.raises(ValueError):
        operation()


@pytest.mark.parametrize("name,parameters", [
    ("operation_count", {"expected": False}),
    ("user_message_count", {"expected": True}),
    ("operation_count", {"expected": -1}),
    ("operation_count", {"expected": 0, "script": "unreviewed"}),
    ("terminal_status", {"expected": "ANY"}),
    ("artifact_exists", {"path": "/../host-file"}),
    ("artifact_sha256", {"path": "/out.txt", "expected": "not-a-digest"}),
    ("unregistered_sql", {"sql": "SELECT 1"}),
    ("tool_correctness", {"available_tools": []}),
    ("output_equals", {"expected": {"value": float("nan")}}),
])
def test_unreviewed_or_wrongly_typed_check_rejected_on_import(name, parameters):
    with pytest.raises(ValueError):
        Check(registered_id=name, parameters=parameters)


def test_output_boolean_is_preserved_and_does_not_equal_numeric_zero():
    spec = Check(registered_id="output_equals", parameters={"expected": False})
    assert spec.parameters["expected"] is False
    assert verify(spec, {"run": {"output": 0}}, None).status == "FAIL"
    assert verify(spec, {"run": {"output": False}}, None).status == "PASS"


def test_forward_declared_but_unimplemented_check_blocks_before_business_post(tmp_path):
    from test_core import attestation

    from evaluation_runner.client import Platform
    from evaluation_runner.engine import run

    value, effects = target(), []

    def server(request):
        effects.append(request.method)
        return httpx.Response(200, json=attestation(value))

    platform = Platform(value, transport=httpx.MockTransport(server))
    specimen = case()
    specimen.validators = [Check(registered_id="protected_tests")]
    result = run(platform, value, Dataset(dataset_id="pending", dataset_version=1, cases=[specimen]), tmp_path)
    assert "POST" not in effects
    assert result["gate"] == "NEEDS_REVIEW"
    assert result["attempts"][0]["started"] is False
    platform.close()
