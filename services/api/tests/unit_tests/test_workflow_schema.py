"""Structured workflow contracts reject unsupported schemas and malformed input."""

import pytest

from app.errors import APIError
from app.workflow_schema import validate_schema, validate_text


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"name": {"type": "string", "maxLength": 20}}, "required": ["name"]},
        {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 10},
        {"type": "string", "enum": ["是", "否"]},
    ],
)
def test_supported_forms(schema):
    validate_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "https://example.com/schema"},
        {"type": "string", "pattern": "(a+)+$"},
        {"type": "array"},
        {"type": "object", "properties": {}, "required": ["missing"]},
        {"type": "object", "required": "bad"},
        {"type": "object", "additionalProperties": True},
    ],
)
def test_unsupported_schemas_fail(schema):
    with pytest.raises(ValueError):
        validate_schema(schema)


def test_types_required_fields_and_unknown_fields_are_checked():
    schema = {"type": "object", "properties": {"age": {"type": "integer", "minimum": 1}}, "required": ["age"]}
    validate_text('{"age": 4}', schema)
    for text in ["{}", '{"age":true}', '{"age":0}', '{"age":4,"extra":1}', "{", '{"age":NaN}']:
        with pytest.raises(APIError) as error:
            validate_text(text, schema)
        assert error.value.code == "WORKFLOW_INPUT_INVALID"
    with pytest.raises(APIError) as error:
        validate_text("invalid", schema, output=True)
    assert error.value.code == "WORKFLOW_OUTPUT_INVALID"


def test_old_text_transport_preserves_literal_json():
    validate_text('{"this":"is still text"}', {"type": "string"})
    with pytest.raises(APIError):
        validate_text("toolong", {"type": "string", "maxLength": 3})


def test_overflow_numbers_are_not_valid_json_values():
    with pytest.raises(APIError):
        validate_text("1e999", {"type": "number"})
    with pytest.raises(ValueError):
        validate_schema({"type": "number", "maximum": float("inf")})
