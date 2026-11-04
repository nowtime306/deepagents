"""Private metadata checks, never a Java/Agent/OS-isolation claim."""

from copy import deepcopy

import pytest

from evaluation_runner import fixed_java_client as draft


def report():
    empty = draft.sha(b"")
    receipt = {"exit_code": 0, "timed_out": False, "truncated": False, "stdout_sha256": empty, "stderr_sha256": empty}
    return {"schema_version": 1, "protocol": "java-max-v2", "source_sha256": "b" * 64, "program_sha256": "a" * 64,
        "tests_sha256": draft.TESTS_SHA, "compile": receipt.copy(), "adapter_compile": receipt.copy(),
        "javac_version_sha256": empty, "java_version_sha256": empty,
        "protection": {"uid": 1000, "gid": 1000, "blocked": True, "comparator_unchanged": True,
            "cwd": "/workspace", "workspace_writable": True, "parent_replace_blocked": True},
        "isolation": {"schema_version": 1, "architecture": "amd64", "uid": 1000, "gid": 1000, "euid": 1000, "egid": 1000,
            "saved_uid": 1000, "saved_gid": 1000, "no_supplementary_groups": True, "no_new_privs": 1,
            "seccomp_mode": 2, "no_inherited_sockets": True, "native_getpid_allowed": True, "policy_sha256": draft.LAUNCHER_SHA,
            "denied_errno": dict.fromkeys(("inet", "inet6", "socketpair", "execd_loopback", "connect", "x32", "compat",
                "syscall_425", "syscall_426", "syscall_427", "syscall_438"), 1)},
        "class_protection": {"adapter_blocked": True, "candidate_blocked": True, "digests_unchanged": True},
        "cases": [{**receipt, "case_index": index, "passed": True, "expected_sha256": draft.sha(str(expected).encode()),
                   "stdout_sha256": draft.sha(str(expected).encode())} for index, expected in enumerate(draft.EXPECTED)], "passed": True}


def baseline_report():
    value = report()
    value.update(source_sha256=draft.ORIGINAL_SHA, passed=False)
    for index, actual in enumerate((2, -7, 4)):
        value["cases"][index]["stdout_sha256"] = draft.sha(str(actual).encode())
        value["cases"][index]["passed"] = index == 2
    return value


def test_draft_stays_blocked_until_final_signature_program_is_frozen(monkeypatch):
    monkeypatch.setattr(draft, "PROGRAM_SHA", None)
    with pytest.raises(ValueError, match="Draft only"):
        draft.protocol(report(), "b" * 64)


def test_pure_success_requires_bound_source_and_all_actual_receipts(monkeypatch):
    monkeypatch.setattr(draft, "PROGRAM_SHA", "a" * 64)  # Engineering receipt fixture, never a production pin.
    assert draft.protocol(report(), "b" * 64) == "PASS"
    with pytest.raises(ValueError):
        draft.protocol(report(), "c" * 64)


@pytest.mark.parametrize("change", ["case_output", "claimed_pass", "missing_isolation", "boolean_uid", "wrong_program", "truncated"])
def test_receipt_claim_does_not_replace_protected_facts(monkeypatch, change):
    monkeypatch.setattr(draft, "PROGRAM_SHA", "a" * 64)
    value = deepcopy(report())
    if change == "case_output":
        value["cases"][0]["stdout_sha256"] = "c" * 64
    elif change == "claimed_pass":
        value["passed"] = False
    elif change == "missing_isolation":
        del value["isolation"]["denied_errno"]["execd_loopback"]
    elif change == "boolean_uid":
        value["protection"]["uid"] = True
    elif change == "wrong_program":
        value["program_sha256"] = "c" * 64
    else:
        value["cases"][0]["truncated"] = True
    with pytest.raises(ValueError):
        draft.protocol(value, "b" * 64)


def test_exit125_with_complete_isolation_is_known_functional_failure(monkeypatch):
    monkeypatch.setattr(draft, "PROGRAM_SHA", "a" * 64)
    value = report()
    value["cases"][0].update(exit_code=125, passed=False)
    value["passed"] = False
    assert draft.protocol(value, "b" * 64) == "FAIL"


def test_source_hash_and_diff_are_recomputable_from_actual_candidate_bytes():
    source = draft.ORIGINAL.replace("return a;", "return a>b?a:b;").encode()
    result = draft.source_bytes(source, {"path": "/Max.java", "digest": draft.sha(source), "size": len(source)})
    assert result["changed"] is True and result["source_utf8"].encode() == source
    assert result["source_sha256"] == draft.sha(source)
    assert result["original_sha256"] == draft.sha(draft.ORIGINAL.encode())


@pytest.mark.parametrize("missing", [None, False, "0"])
def test_zero_model_verifier_usage_must_not_be_inferred_from_unknown(missing):
    value = dict.fromkeys(("calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"), 0)
    assert draft.native_usage(value, calls=0) == value
    value["input_tokens"] = missing
    with pytest.raises(ValueError):
        draft.native_usage(value, calls=0)
