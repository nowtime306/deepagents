"""Bounded JSON Schema equivalence never removes data fields or validation constraints."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from app.errors import APIError
from app.mcp_service import MCPService
from app.resource_tools import operation_identity
from app.tool_protocol_bridge import canonical_schema, canonical_schema_key


def test_empty_properties_and_known_annotations_do_not_change_operation_identity():
    plain = {"type": "object"}
    decorated = {"type": "object", "properties": {}, "title": "caption", "description": "help",
                 "$comment": "editor note", "examples": [{"description": "sample data"}]}
    server = {"id": str(uuid4()), "url": "https://fixture.invalid/mcp"}
    tool = {"name": "write", "schema": plain, "read_only": False}
    assert canonical_schema(decorated) == canonical_schema(plain)
    assert operation_identity(server, tool) == operation_identity(server, tool | {"schema": decorated})
    assert canonical_schema({"type": "object", "properties": {"description": {"type": "string"}}}) != plain


def test_contextual_property_names_and_data_values_are_preserved_without_mutation():
    value = {"type": "object", "description": "schema help", "properties": {
        "description": {"type": "string", "description": "field help"},
        "title": {"type": "integer", "title": "field title"}},
        "default": {"description": "literal default", "title": 1},
        "enum": [{"description": "literal enum"}], "const": {"examples": [1]},
        "x-validator": {"description": "opaque extension", "properties": {}}}
    original = deepcopy(value)
    normalized = canonical_schema(value)
    assert normalized["properties"] == {"description": {"type": "string"}, "title": {"type": "integer"}}
    for key in ("default", "enum", "const", "x-validator"):
        assert normalized[key] == value[key]
    assert value == original


@pytest.mark.parametrize("constraint", [
    {"required": ["description"]}, {"additionalProperties": False}, {"minProperties": 1},
    {"default": {"description": "real default"}}, {"enum": [{"title": "real enum"}]},
    {"const": {"description": "real const"}}, {"$ref": "#/$defs/other"},
    {"properties": {"description": {"type": "number"}}},
    {"properties": {"description": {"type": "string", "minLength": 2}}},
    {"x-validator": {"description": "real opaque constraint"}},
])
def test_actual_constraints_still_change_the_action_hash(constraint):
    server = {"id": str(uuid4()), "url": "https://fixture.invalid/mcp"}
    schema = {"type": "object", "properties": {"description": {"type": "string"}}}
    tool = {"name": "write", "schema": schema, "read_only": False}
    assert operation_identity(server, tool) != operation_identity(server, tool | {"schema": schema | constraint})


@pytest.mark.parametrize("container", ["properties", "patternProperties", "$defs", "definitions", "dependentSchemas"])
def test_annotations_normalize_only_inside_known_schema_maps(container):
    schema = {container: {"description": {"type": "string", "description": "help"}}}
    assert canonical_schema(schema) == {container: {"description": {"type": "string"}}}


@pytest.mark.parametrize("container", ["allOf", "anyOf", "oneOf", "prefixItems"])
def test_annotations_normalize_inside_known_schema_arrays(container):
    assert canonical_schema({container: [{"type": "string", "title": "help"}]}) == {
        container: [{"type": "string"}]}


def test_plain_local_definition_ref_is_preserved_with_known_target_annotations_removed():
    schema = {"$defs": {"description": {"type": "string", "title": "help"}}, "$ref": "#/$defs/description"}
    assert canonical_schema(schema) == {"$defs": {"description": {"type": "string"}}, "$ref": "#/$defs/description"}


@pytest.mark.parametrize("ref", ["#/examples/0", "#/unknown/target", "https://external.invalid/schema",
                                    "#/$defs/a~1b", "#/$defs/a%20b", "#", "#/$defs/missing"])
def test_complex_or_unproven_references_keep_the_whole_schema_raw(ref):
    schema = {"title": "keep", "examples": [{"type": "string"}], "$defs": {"a": {"title": "keep"}}, "$ref": ref}
    assert canonical_schema(schema) == schema


def test_dynamic_references_and_opaque_locations_are_not_rewritten():
    value = {"title": "keep", "$dynamicRef": "#anchor", "properties": {}}
    assert canonical_schema(value) == value
    value = {"x-schema": {"title": "keep", "properties": {}}, "default": {"title": "keep"}}
    assert canonical_schema(value) == value


@pytest.mark.parametrize("actual_schema,accepted", [
    ({"type": "object", "title": "remote help"}, True),
    ({"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]}, False),
])
async def test_actual_http_invoke_accepts_equivalent_schema_and_rejects_real_change(actual_schema, accepted):
    stored = {"type": "object", "properties": {}}
    server = {"id": str(uuid4()), "tools": [{"name": "lookup", "enabled": True,
              "read_only": True, "schema": stored}]}
    effects = []

    class Database:
        async def fetch(self, *args):
            return [deepcopy(server)]

    class Session:
        async def list_tools(self):
            return ListToolsResult(tools=[Tool(name="lookup", input_schema=actual_schema,
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))])

        async def call_tool(self, name, arguments):
            effects.append(name)
            return CallToolResult(content=[TextContent(type="text", text="controlled")])

    @asynccontextmanager
    async def session(*args):
        yield Session()

    service = MCPService(Database(), None)
    service.session = session
    if accepted:
        await service.invoke(server["id"], "lookup", {})
        assert effects == ["lookup"]
    else:
        with pytest.raises(APIError):
            await service.invoke(server["id"], "lookup", {})
        assert effects == []


@pytest.mark.parametrize("remote_schema,expected_schema,accepted", [
    ({"type": "object", "title": "remote help"}, {"type": "object", "properties": {}}, True),
    ({"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
     {"type": "object"}, False),
    ({"type": "object", "default": {"description": "behavioral default"}}, {"type": "object"}, False),
    ({"type": "object", "properties": {"flag": {"const": 1}}},
     {"type": "object", "properties": {"flag": {"const": True}}}, False),
    ({"type": "object", "properties": {"flag": {"const": 0}}},
     {"type": "object", "properties": {"flag": {"const": False}}}, False),
    ({"type": "object", "properties": {"flag": {"enum": [0]}}},
     {"type": "object", "properties": {"flag": {"enum": [False]}}}, False),
    ({"type": "object", "properties": {"flag": {"default": {"nested": [1, 0]}}}},
     {"type": "object", "properties": {"flag": {"default": {"nested": [True, False]}}}}, False),
])
def test_actual_stdio_bridge_uses_same_constraint_boundary_without_starting_process(
    monkeypatch, remote_schema, expected_schema, accepted,
):
    from app import tool_protocol_bridge as bridge

    replies, effects = [], []

    class Stream:
        def fileno(self):
            return 10

        def close(self):
            pass

    class Process:
        stdin, stdout = Stream(), Stream()

        def wait(self):
            return 0

    def write(descriptor, payload):
        message = json.loads(bytes(payload))
        if "id" in message:
            if message["method"] == "initialize":
                result = {"protocolVersion": message["params"]["protocolVersion"]}
            elif message["method"] == "tools/list":
                result = {"tools": [{"name": "lookup", "inputSchema": remote_schema,
                    "annotations": {"readOnlyHint": False, "destructiveHint": True}}]}
            else:
                assert message["method"] == "tools/call"
                effects.append(message["method"])
                result = {"content": [{"type": "text", "text": "controlled"}]}
            replies.append(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode() + b"\n")
        return len(payload)

    def no_children(*args):
        raise ChildProcessError

    monkeypatch.setattr(bridge, "ctypes", SimpleNamespace(
        CDLL=lambda *args, **kwargs: SimpleNamespace(prctl=lambda *a: 0)))
    monkeypatch.setattr(bridge, "subprocess", SimpleNamespace(Popen=lambda *a, **k: Process(), PIPE=-1, DEVNULL=-3))
    monkeypatch.setattr(bridge, "os", SimpleNamespace(write=write, read=lambda *args: replies.pop(0),
        set_blocking=lambda *args: None, getpid=lambda: 999999999, waitpid=no_children, WNOHANG=1))
    monkeypatch.setattr(bridge, "signal", SimpleNamespace(signal=lambda *args: None, SIGTERM=15, SIGINT=2, SIG_IGN=1))
    monkeypatch.setattr(bridge, "select", SimpleNamespace(select=lambda read, write, error, timeout: (read, write, [])))
    flag = expected_schema.get("properties", {}).get("flag", {})
    arguments = {"flag": flag["const"]} if "const" in flag else {"flag": flag["enum"][0]} if "enum" in flag else {}
    body = {"command": ["controlled-no-process"], "transport": "stdio", "operation": "call", "tool_name": "lookup",
            "arguments": arguments, "expected_schema": expected_schema, "expected_read_only": False}
    if accepted:
        assert bridge.execute(body)["content"] and effects == ["tools/call"]
    else:
        with pytest.raises(RuntimeError, match="PROTOCOL_TOOL_INTERFACE_CHANGED"):
            bridge.execute(body)
        assert effects == []


@pytest.mark.parametrize("annotation,value", [("description", {"type": "string"}), ("title", False),
                                               ("$comment", []), ("examples", {"not": "an array"})])
def test_malformed_annotations_are_kept_raw_not_repaired(annotation, value):
    schema = {annotation: value, "properties": {}}
    assert canonical_schema(schema) == schema


def test_ref_base_identity_and_nonplain_names_remain_raw():
    for value in [
        {"$id": "https://other.invalid/schema", "$ref": "#/$defs/a", "$defs": {"a": {"type": "string"}}},
        {"$ref": "#/$defs/a b", "$defs": {"a b": {"type": "string"}}},
    ]:
        schema = value | {"title": "must remain", "properties": {}}
        assert canonical_schema(schema) == schema


@pytest.mark.parametrize("keyword,old_value,new_value", [
    ("const", True, 1), ("const", False, 0), ("enum", [False], [0]),
    ("default", {"nested": [True, False]}, {"nested": [1, 0]}),
])
@pytest.mark.parametrize("gate", ["live", "expected", "remote", "final"])
async def test_http_bool_number_changes_never_reuse_approved_write(monkeypatch, keyword, old_value, new_value, gate):
    schema = {"type": "object", "properties": {"flag": {keyword: old_value}}}
    changed = {"type": "object", "properties": {"flag": {keyword: new_value}}}
    declaration = {"name": "write", "enabled": True, "read_only": False, "schema": schema}
    current = {"id": str(uuid4()), "tools": [deepcopy(declaration)]}
    effects, approvals = [], []
    if gate == "live":
        current["tools"][0]["schema"] = changed

    class Database:
        async def fetch(self, *args):
            return [deepcopy(current)]

    class Session:
        async def list_tools(self):
            return ListToolsResult(tools=[Tool(name="write", input_schema=changed if gate == "remote" else schema,
                annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))])

        async def call_tool(self, name, arguments):
            effects.append(name)
            return CallToolResult(content=[TextContent(type="text", text="must not execute")])

    @asynccontextmanager
    async def session(*args):
        yield Session()

    async def authorize():
        approvals.append("authorized")
        if gate == "final":
            current["tools"][0]["schema"] = changed

    async def frozen(server, vault):
        return {"id": server["id"], "tools": [deepcopy(declaration)]}

    monkeypatch.setattr("app.resource_tools.frozen_server", frozen)
    service = MCPService(Database(), None)
    service.session = session
    expected = declaration | {"schema": changed if gate == "expected" else schema}
    with pytest.raises(APIError):
        await service.invoke(current["id"], "write",
                             {} if keyword == "default" else {"flag": old_value[0] if keyword == "enum" else old_value},
                             before_write=authorize, expected_tool=expected)
    assert effects == []
    assert approvals == (["authorized"] if gate == "final" else [])


@pytest.mark.parametrize("keyword,old,new", [("const", True, 1), ("const", False, 0),
                                            ("enum", [False], [0]),
                                            ("default", {"nested": [True, False]}, {"nested": [1, 0]})])
def test_strict_json_keys_and_operation_hash_keep_nested_boolean_number_distinct(keyword, old, new):
    before, after = {keyword: old}, {keyword: new}
    assert canonical_schema_key(before) != canonical_schema_key(after)
    server = {"id": str(uuid4()), "url": "https://fixture.invalid/mcp"}
    tool = {"name": "write", "schema": before, "read_only": False}
    assert operation_identity(server, tool) != operation_identity(server, tool | {"schema": after})
