"""Pure local F05 authentication contract; identical bytes for runner and API profile."""

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from uuid import UUID

CONTRACT = "f05-deadline-guard-v1"
ANCHOR, KEY = "f05-deadline-anchor.json", "f05-deadline.key"
CLAIM, BINDING, POISON = "f05-activation-claim.json", "f05-activation.json", "f05-deadline-poison"
AUTH = "case-deadline.auth.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe(path):
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        raise ValueError("Linked F05 private proof")


def bounded(path, maximum=65536):
    safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Nonregular F05 private proof")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("Changed F05 private proof")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Oversized F05 private proof")
    return raw


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate F05 proof key")
        result[key] = value
    return result


def invalid_constant(_):
    raise ValueError("Nonfinite F05 proof")


def decode(raw):
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                       parse_constant=invalid_constant)
    json.dumps(value, allow_nan=False)
    return value


def read(path):
    return decode(bounded(path))


def write_once(path, value, *, raw=False):
    safe(path)
    payload = value if raw else json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600), "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def replace(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_once(temporary, value)
    safe(path)
    os.replace(temporary, path)


def publish_auth(child, value):
    """Publish the fully flushed authentication file without replacing an existing proof."""
    path = child / AUTH
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_once(temporary, value)
    safe(path)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def identifier(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Noncanonical F05 identity")
    return value


def hex_value(value, size=64):
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is None:
        raise ValueError("Invalid F05 proof digest")
    return value


def validate_identity(value):
    if set(value) != {"environment_id", "owner_id", "namespace", "candidate_commit", "suite_context"}:
        raise ValueError("Invalid F05 identity fields")
    for name in ("environment_id", "owner_id", "namespace"):
        identifier(value[name])
    hex_value(value["candidate_commit"], 40)
    context = value["suite_context"]
    if (context.get("case_id") != "AET-F05" or type(context.get("schema_version")) is not int
            or context["schema_version"] != 1 or type(context.get("repeat_index")) is not int
            or not 1 <= context["repeat_index"] <= 3 or context.get("rules_id") != "controlled-a20-v1"
            or context.get("candidate_commit") != value["candidate_commit"]):
        raise ValueError("Invalid F05 suite identity")
    identifier(context["suite_id"])
    for name in ("catalog_digest", "case_definition_digest"):
        hex_value(context[name])
    return value


def data_directory(child, environment):
    return child / "target" / ("data_" + UUID(identifier(environment)).hex + "_test")


def provision(child, identity):
    validate_identity(identity)
    data = data_directory(child, identity["environment_id"])
    safe(data)
    if not data.is_dir():
        raise ValueError("Missing isolated F05 data directory")
    key = secrets.token_bytes(32)
    anchor = {"schema_version": 1, "contract": CONTRACT, "definition_version": 2,
              "identity": identity, "key_sha256": hashlib.sha256(key).hexdigest()}
    write_once(data / KEY, key, raw=True)
    write_once(data / ANCHOR, anchor)
    return hashlib.sha256(bounded(data / ANCHOR)).hexdigest()


def initialize_target(child, environment, owner, namespace, candidate, pins):
    context_path = child / "suite-context.json"
    if not context_path.exists():
        return {}
    context = read(context_path)
    if context.get("case_id") != "AET-F05":
        return {}
    if (set(pins) != {"catalog_digest", "case_definition_digest", "rules_id", "definition_version"}
            or type(pins["definition_version"]) is not int or pins["definition_version"] != 2
            or any(context.get(name) != pins[name] for name in ("catalog_digest", "case_definition_digest", "rules_id"))):
        raise ValueError("F05 definition is not the reviewed target contract")
    identity = {"environment_id": environment, "owner_id": owner, "namespace": namespace,
                "candidate_commit": candidate, "suite_context": context}
    return {"f05_guard_required": True, "f05_anchor_sha256": provision(child, identity)}


def startup_anchor(child, state, private):
    context_path = child / "suite-context.json"
    context = read(context_path) if context_path.exists() else {}
    required = context.get("case_id") == "AET-F05"
    if not required:
        if state.get("f05_guard_required") is not None or private.get("f05_guard_required") is not None:
            raise ValueError("Unexpected required F05 target state")
        return None
    if (state.get("f05_guard_required") is not True or private.get("f05_guard_required") is not True
            or state.get("f05_anchor_sha256") != private.get("f05_anchor_sha256")):
        raise ValueError("Mandatory F05 fence state disappeared")
    data = data_directory(child, state["environment_id"])
    value, _ = anchor(data, hex_value(state["f05_anchor_sha256"]))
    if (value["identity"]["environment_id"] != state["environment_id"]
            or value["identity"]["owner_id"] != state["owner_id"]
            or value["identity"]["namespace"] != state["sandbox_namespace"]
            or value["identity"]["candidate_commit"] != state["platform_commit"]
            or digest(value["identity"]["suite_context"]) != digest(context)):
        raise ValueError("F05 restart identity changed")
    return state["f05_anchor_sha256"]


def anchor(data, expected_sha256):
    raw = bounded(data / ANCHOR)
    if hashlib.sha256(raw).hexdigest() != hex_value(expected_sha256):
        raise ValueError("F05 anchor changed")
    value = decode(raw)
    if (set(value) != {"schema_version", "contract", "definition_version", "identity", "key_sha256"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["definition_version"]) is not int or value["definition_version"] != 2
            or value["contract"] != CONTRACT):
        raise ValueError("Invalid F05 anchor")
    validate_identity(value["identity"])
    key = bounded(data / KEY, 32)
    if len(key) != 32 or hashlib.sha256(key).hexdigest() != hex_value(value["key_sha256"]):
        raise ValueError("F05 local authentication key changed")
    return value, key


def authentication(key, anchor_sha256, record_sha256):
    material = (CONTRACT + ":" + hex_value(anchor_sha256) + ":" + hex_value(record_sha256)).encode("ascii")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def activation_mac(key, value):
    material = (CONTRACT + ":activation:" + digest(value)).encode("ascii")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def sign_record(child, record, record_sha256):
    state = read(child / "target" / "target-state.json")
    data = data_directory(child, record["scope"]["environment_id"])
    original, key = anchor(data, state["f05_anchor_sha256"])
    expected = original["identity"]
    actual = {name: record["scope"][name] for name in ("environment_id", "owner_id", "namespace", "suite_context")}
    actual["candidate_commit"] = actual["suite_context"]["candidate_commit"]
    if digest(actual) != digest(expected):
        raise ValueError("Parent deadline belongs to another F05 anchor")
    value = {"schema_version": 1, "contract": CONTRACT, "anchor_sha256": state["f05_anchor_sha256"],
             "record_sha256": hex_value(record_sha256),
             "mac": authentication(key, state["f05_anchor_sha256"], record_sha256)}
    publish_auth(child, value)
    return value
