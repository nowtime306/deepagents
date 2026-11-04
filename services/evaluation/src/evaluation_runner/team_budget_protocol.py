"""Private strict native-ledger recomputation; not a completed case/HTTP observer."""

from uuid import UUID

TOTAL = (
    "calls",
    "reported_calls",
    "unknown_calls",
    "pending_calls",
    "input_tokens",
    "output_tokens",
)


def fixed_call_sequence(rows, case_id):
    """Check each scripted native cost, not only a conserved whole-root sum."""
    expected = {
        "AET-W05": [
            ("lead", "chat", "KNOWN", 10, 5),
            ("reviewer", "chat", "KNOWN", 10, 5),
            ("reviewer", "chat", "UNKNOWN", None, None),
        ],
        "AET-W06": [
            ("lead", "chat", "KNOWN", 10, 5),
            ("reviewer", "chat", "KNOWN", 10, 5),
            ("reviewer", "chat", "KNOWN", 10, 5),
            ("reviewer", "embedding", "KNOWN", 7, 0),
            ("reviewer", "chat", "KNOWN", 10, 5),
            ("lead", "chat", "KNOWN", 15000, 933),
        ],
    }
    fields = ("role_name", "call_kind", "status", "input_tokens", "output_tokens")
    if (
        case_id not in expected
        or [tuple(row[key] for key in fields) for row in rows] != expected[case_id]
    ):
        raise ValueError(
            "Native role/kind/status/input/output sequence differs from the fixed script"
        )


def separate_call_ids(work_rows, index_rows):
    """A native provider invocation cannot count toward two resource roots."""
    work = [identifier(row["call_id"]) for row in work_rows]
    index = [identifier(row["call_id"]) for row in index_rows]
    if len(set(work + index)) != len(work) + len(index):
        raise ValueError(
            "Index and Work native calls must have globally unique call IDs"
        )


def identifier(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical UUID required")
    return value


def ledger(rows, *, owner, root, models, member):
    """Attribute native rows to a sealed role and exact parent, including member embeddings."""
    identifier(owner)
    identifier(root)
    identifier(member)
    total = dict.fromkeys(TOTAL, 0)
    seen = set()
    for row in rows:
        if set(row) != {
            "call_id",
            "owner_id",
            "run_id",
            "model_id",
            "model_revision",
            "role_name",
            "team_call_id",
            "call_kind",
            "status",
            "input_tokens",
            "output_tokens",
        }:
            raise ValueError(
                "Native ledger projection is incomplete or has unknown fields"
            )
        call = identifier(row["call_id"])
        if call in seen or row["owner_id"] != owner or row["run_id"] != root:
            raise ValueError("Duplicate or foreign native call")
        seen.add(call)
        role = "embedding" if row["call_kind"] == "embedding" else row["role_name"]
        if (
            row["call_kind"] not in {"chat", "embedding"}
            or role not in models
            or row["model_id"] != models[role]["id"]
            or type(row["model_revision"]) is not int
            or row["model_revision"] != models[role]["revision"]
        ):
            raise ValueError("Native model is not the sealed role/configuration")
        if row["call_kind"] == "embedding" or role == "reviewer":
            if row["role_name"] != "reviewer" or row["team_call_id"] != member:
                raise ValueError(
                    "Member chat/query embedding escaped parent attribution"
                )
        elif row["team_call_id"] is not None or row["role_name"] != "lead":
            raise ValueError("Lead has a foreign team attribution")
        status = row["status"]
        if status not in {"KNOWN", "PENDING", "UNKNOWN"}:
            raise ValueError("Unknown native status")
        total["calls"] += 1
        total[
            {
                "KNOWN": "reported_calls",
                "PENDING": "pending_calls",
                "UNKNOWN": "unknown_calls",
            }[status]
        ] += 1
        if status == "KNOWN":
            for key in ("input_tokens", "output_tokens"):
                if type(row[key]) is not int or row[key] < 0:
                    raise ValueError(
                        "Known native tokens must be finite nonnegative integers"
                    )
                total[key] += row[key]
        elif row["input_tokens"] is not None or row["output_tokens"] is not None:
            raise ValueError("Unknown usage cannot claim exact billed token totals")
    return total


def exhaustion(
    work_rows,
    index_rows,
    *,
    owner,
    root,
    resource_root,
    models,
    member,
    native_work,
    native_index,
    rejected_code,
    before_effects,
    after_effects,
):
    for usage in (native_work, native_index):
        if set(usage) != set(TOTAL) or any(
            type(value) is not int or value < 0 for value in usage.values()
        ):
            raise ValueError(
                "Complete native totals require strict nonnegative integers"
            )
    total = ledger(work_rows, owner=owner, root=root, models=models, member=member)
    fixed_call_sequence(work_rows, "AET-W06")
    separate_call_ids(work_rows, index_rows)
    if total != native_work or total != {
        "calls": 6,
        "reported_calls": 6,
        "unknown_calls": 0,
        "pending_calls": 0,
        "input_tokens": 15047,
        "output_tokens": 953,
    }:
        raise ValueError(
            "Parent/member/query native work usage must sum to exactly 16000"
        )
    if root == resource_root or len(index_rows) != 1:
        raise ValueError(
            "Initialization index must retain its separate real resource root"
        )
    index = index_rows[0]
    if (
        set(index)
        != {
            "call_id",
            "run_id",
            "owner_id",
            "model_id",
            "model_revision",
            "mode",
            "call_kind",
            "status",
            "input_tokens",
            "output_tokens",
        }
        or identifier(index["call_id"]) in {row["call_id"] for row in work_rows}
        or index["run_id"] != identifier(resource_root)
        or index["owner_id"] != owner
        or index["model_id"] != models["embedding"]["id"]
        or type(index["model_revision"]) is not int
        or index["model_revision"] != models["embedding"]["revision"]
        or index["mode"] != "resource"
        or index["call_kind"] != "embedding"
        or index["status"] != "KNOWN"
        or type(index["input_tokens"]) is not int
        or index["input_tokens"] != 7
        or type(index["output_tokens"]) is not int
        or index["output_tokens"] != 0
        or native_index
        != {
            "calls": 1,
            "reported_calls": 1,
            "unknown_calls": 0,
            "pending_calls": 0,
            "input_tokens": 7,
            "output_tokens": 0,
        }
    ):
        raise ValueError(
            "Initialization cannot substitute the in-run member query embedding"
        )
    expected = {"provider_calls", "tool_effects", "operations", "canary_files"}
    if (
        set(before_effects) != expected
        or set(after_effects) != expected
        or any(
            type(value) is not int or value < 0
            for value in (*before_effects.values(), *after_effects.values())
        )
        or rejected_code != "WORK_BUDGET_EXCEEDED"
        or before_effects["provider_calls"] != 7
        or before_effects != after_effects
        or after_effects["canary_files"] != 0
    ):
        raise ValueError(
            "The genuine post-exhaustion tool attempt needs a denial and zero new effects"
        )
    return {"work": total, "index": native_index, "case_calls": 7, "case_tokens": 16007}
