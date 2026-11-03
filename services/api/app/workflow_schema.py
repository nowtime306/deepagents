"""Bounded JSON Schema for workflow forms; no references or executable expressions."""

import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from app.errors import fail

KEYWORDS = {
    "type",
    "title",
    "description",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
}
TYPES = {"string", "number", "integer", "boolean", "null", "object", "array"}


def validate_schema(schema: dict, depth: int = 0) -> None:
    """Accept only a small, bounded subset, independent of network resolvers."""
    if (
        not isinstance(schema, dict)
        or depth > 6
        or schema.keys() - KEYWORDS
        or not isinstance(schema.get("type"), str)
        or schema["type"] not in TYPES
    ):
        raise ValueError("Schema 只支持六层以内的基本类型、对象、数组和长度/数值范围，不支持引用或表达式")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError("Schema 的字段类型或约束格式无效") from error
    if len(json.dumps(schema, ensure_ascii=False, allow_nan=False)) > 12000:
        raise ValueError("Schema 最多 12000 字符")
    if schema.get("additionalProperties", False) is not False:
        raise ValueError("对象不允许未声明字段，请设置 additionalProperties 为 false")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > 30:
        raise ValueError("对象最多声明 30 个字段")
    for child in props.values():
        validate_schema(child, depth + 1)
    if "items" in schema:
        validate_schema(schema["items"], depth + 1)
    if schema["type"] == "array" and "items" not in schema:
        raise ValueError("数组必须声明 items 类型")
    if set(schema.get("required", [])) - props.keys():
        raise ValueError("required 必须引用已声明字段")


def closed(schema: dict) -> dict:
    """Apply closed objects even when the author omitted additionalProperties."""
    result = dict(schema)
    if schema["type"] == "object":
        result["additionalProperties"] = False
        result["properties"] = {key: closed(value) for key, value in schema.get("properties", {}).items()}
    if "items" in schema:
        result["items"] = closed(schema["items"])
    return result


def parse_json(text: str) -> JsonValue:
    """Parse bounded workflow JSON without accepting nonfinite numbers."""
    return json.loads(
        text,
        parse_float=finite_number,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )


def validate_text(text: str, schema: dict, *, output: bool = False) -> None:
    """Keep text transport backward compatible; structured values use JSON text."""
    code = "WORKFLOW_OUTPUT_INVALID" if output else "WORKFLOW_INPUT_INVALID"
    try:
        value = text if schema["type"] == "string" else parse_json(text)
        error = next(Draft202012Validator(closed(schema)).iter_errors(value), None)
        if error:
            path = ".".join(str(item) for item in error.absolute_path) or "根值"
            fail(422, code, f"{'输出' if output else '输入'}在 {path} 不符合声明的 Schema")
    except (ValueError, RecursionError):
        fail(422, code, "结构化输入输出必须是有效 JSON，且不包含无限值")


def finite_number(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON")
    return result
