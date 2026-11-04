"""F05-only persistent target expiry fence, independent of optional platform parent permission."""

import hashlib
import hmac
import threading
import time
from functools import partial
from importlib import import_module
from pathlib import Path

import f05_deadline_contract as proof

STARTED = "f05-guard-started.json"

def create(settings, expected_anchor):
    data = Path(settings.data_dir)
    if expected_anchor is None:
        if any((data / name).exists() for name in (proof.ANCHOR, proof.KEY, proof.CLAIM, proof.BINDING, proof.POISON, STARTED)):
            raise ValueError("Unexpected mandatory F05 deadline state")
        return None
    return F05DeadlineGuard(settings, expected_anchor)


class F05DeadlineGuard:
    def __init__(self, settings, expected_anchor):
        from psycopg.conninfo import conninfo_to_dict

        self.data, self.anchor_sha = Path(settings.data_dir), proof.hex_value(expected_anchor)
        self.child = self.data.parent.parent
        value, _ = proof.anchor(self.data, self.anchor_sha)
        self.identity = value["identity"]
        own = proof.read(self.data / "evaluation-instance.json")
        expected_database = "owner_agent_evaluation_" + self.identity["environment_id"].replace("-", "") + "_test"
        if (self.data != proof.data_directory(self.child, self.identity["environment_id"])
                or conninfo_to_dict(settings.database_url).get("dbname") != expected_database
                or settings.evaluation_instance_id != self.identity["environment_id"]
                or settings.evaluation_platform_commit != self.identity["candidate_commit"]
                or any(own.get(key) != self.identity[key] for key in ("environment_id", "owner_id"))
                or own.get("sandbox_namespace") != self.identity["namespace"]
                or proof.digest(proof.read(self.child / "suite-context.json")) != proof.digest(self.identity["suite_context"])):
            raise ValueError("F05 deadline target identity differs")
        self.lock, self.poisoned, self.local_expiry = threading.RLock(), False, None
        self.activated_here = False
        self.parent_suite_bound = False
        self.registered = set()
        self.before_tool = partial(self.check, "tool")
        self.before_submission = partial(self.check, "submission")
        self.start_bytes = None
        try:
            proof.write_once(self.data / STARTED, {"schema_version": 1, "anchor_sha256": self.anchor_sha})
            self.start_bytes = proof.bounded(self.data / STARTED)
        except FileExistsError:
            self.poisoned = True  # This fixed target is single-start, including a previously clean AWAITING instance.
        if (self.data / proof.POISON).exists() or ((self.data / proof.CLAIM).exists() and not (self.data / proof.BINDING).exists()):
            self.poisoned = True

    def poison(self):
        self.poisoned = True
        try:
            proof.write_once(self.data / proof.POISON, b"F05_DEADLINE_BLOCKED", raw=True)
        except (OSError, ValueError):
            pass  # Memory remains blocked; another guard never resumes an existing activation.

    def authenticated_record(self):
        _, key = proof.anchor(self.data, self.anchor_sha)
        raw = proof.bounded(self.child / "case-deadline.json")
        checksum = hashlib.sha256(raw).hexdigest()
        record, auth = proof.decode(raw), proof.read(self.child / proof.AUTH)
        if (set(auth) != {"schema_version", "contract", "anchor_sha256", "record_sha256", "mac"}
                or type(auth["schema_version"]) is not int or auth["schema_version"] != 1
                or auth["contract"] != proof.CONTRACT or auth["anchor_sha256"] != self.anchor_sha
                or auth["record_sha256"] != checksum
                or not hmac.compare_digest(proof.hex_value(auth["mac"]), proof.authentication(key, self.anchor_sha, checksum))):
            raise ValueError("F05 deadline authentication differs")
        scope = {name: self.identity[name] for name in ("environment_id", "owner_id", "namespace", "suite_context")}
        manifest = proof.read(self.child / "target" / "target-manifest.json")
        if (manifest.get("environment_id") != scope["environment_id"] or manifest.get("owner_id") != scope["owner_id"]
                or manifest.get("platform_commit") != self.identity["candidate_commit"]):
            raise ValueError("F05 manifest identity differs")
        scope["target_digest"] = proof.digest(manifest)
        if (set(record) != {"schema_version", "kind", "scope", "started_at_ms", "expires_at_ms", "limit_seconds"}
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["kind"] != "f05-parent-deadline" or proof.digest(record["scope"]) != proof.digest(scope)
                or any(type(record[name]) is not int for name in ("started_at_ms", "expires_at_ms", "limit_seconds"))
                or record["limit_seconds"] != 300 or record["started_at_ms"] <= 0
                or record["expires_at_ms"] != record["started_at_ms"] + 300_000):
            raise ValueError("F05 record scope or deadline differs")
        return record, checksum

    def activation(self, *, readonly=False):
        _, key = proof.anchor(self.data, self.anchor_sha)
        claim_path, binding_path = self.data / proof.CLAIM, self.data / proof.BINDING
        if not binding_path.exists():
            if readonly:
                raise ValueError("No retained F05 activation to read")
            if claim_path.exists():
                raise ValueError("F05 activation did not commit")
            if not (self.child / "case-deadline.json").exists() or not (self.child / proof.AUTH).exists():
                return None
            proof.write_once(claim_path, {"schema_version": 1, "anchor_sha256": self.anchor_sha})
            record, checksum = self.authenticated_record()
            value = {"schema_version": 1, "anchor_sha256": self.anchor_sha, "record_sha256": checksum,
                     "started_at_ms": record["started_at_ms"], "expires_at_ms": record["expires_at_ms"], "expired": False}
            value["mac"] = proof.activation_mac(key, value)
            proof.write_once(binding_path, value)
            self.activated_here = True
        value, claim = proof.read(binding_path), proof.read(claim_path)
        record, checksum = self.authenticated_record()
        if (claim != {"schema_version": 1, "anchor_sha256": self.anchor_sha}
                or type(claim.get("schema_version")) is not int
                or set(value) != {"schema_version", "anchor_sha256", "record_sha256", "started_at_ms", "expires_at_ms", "expired", "mac"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["expired"]) is not bool or value["anchor_sha256"] != self.anchor_sha
                or value["record_sha256"] != checksum
                or any(type(value[name]) is not int or value[name] != record[name] for name in ("started_at_ms", "expires_at_ms"))
                or not hmac.compare_digest(proof.hex_value(value["mac"]), proof.activation_mac(key, {name: item for name, item in value.items() if name != "mac"}))):
            raise ValueError("Original F05 activation changed")
        if not self.activated_here:
            return value  # Existing activation is read-only evidence, never a restart permission.
        now = time.time_ns() // 1_000_000
        if self.local_expiry is None:
            self.local_expiry = time.monotonic() + max(0, (value["expires_at_ms"] - now) / 1000)
        if not value["expired"] and (not value["started_at_ms"] <= now < value["expires_at_ms"] or time.monotonic() >= self.local_expiry):
            value["expired"] = True
            value["mac"] = proof.activation_mac(key, {name: item for name, item in value.items() if name != "mac"})
            proof.replace(binding_path, value)
        return value

    def capability(self):
        with self.lock:
            value = None
            try:
                if self.start_bytes is not None and proof.bounded(self.data / STARTED) != self.start_bytes:
                    raise ValueError("Original F05 startup proof changed")
                if self.cancelled():
                    self.poison()
            except (OSError, ValueError, TypeError, KeyError, RecursionError, AttributeError):
                self.poison()
            if (self.data / proof.POISON).exists():
                self.poisoned = True
            if self.poisoned and self.start_bytes is None and (self.data / proof.BINDING).exists():
                try:
                    value = self.activation(readonly=True)  # Metadata only; a disappearing binding cannot be recreated here.
                except (OSError, ValueError, KeyError, TypeError, RecursionError):
                    value = None
            if not self.poisoned:
                try:
                    proof.anchor(self.data, self.anchor_sha)
                    value = self.activation()
                except (OSError, ValueError, KeyError, TypeError, RecursionError):
                    self.poison()
            phase = ("BLOCKED" if self.poisoned else "AWAITING_RECORD" if value is None else "EXPIRED"
                     if value["expired"] else "ACTIVE" if self.activated_here else "BLOCKED")
            installed = self.registered == {"provider", "tool", "sandbox", "submission"}
            return {"contract": proof.CONTRACT, "required": True, "installed": installed,
                    "bound_identity_digest": proof.digest(self.identity), "boundaries": {name: name in self.registered for name in ("provider", "tool", "sandbox", "submission")},
                    "phase": phase, "deadline_bound": value is not None, "dispatch_allowed": installed and phase == "ACTIVE",
                    "record_sha256": value["record_sha256"] if value else None, "expires_at_ms": value["expires_at_ms"] if value else None}

    def cancelled(self):
        result_marker = self.child / "result" / "cancel.request"
        proof.safe(result_marker)
        if result_marker.exists():
            proof.bounded(result_marker, maximum=4096)
            return True
        context = self.identity["suite_context"]
        try:
            suite = proof.decode(proof.bounded(self.child.parent / "suite.json", maximum=1048576))
            same_suite = (type(suite.get("schema_version")) is int and suite["schema_version"] == 1
                          and suite.get("suite_id") == context["suite_id"]
                          and suite.get("candidate_commit") == context["candidate_commit"]
                          and suite.get("dataset_digest") == context["catalog_digest"]
                          and any(row.get("child") == self.child.name and row.get("case_id") == "AET-F05"
                                  and type(row.get("repeat_ordinal")) is int
                                  and row["repeat_ordinal"] == context["repeat_index"]
                                  for row in suite.get("attempts", [])))
        except (OSError, ValueError, TypeError, KeyError, RecursionError, AttributeError):
            if self.parent_suite_bound:
                raise ValueError("Established F05 suite cancellation authority is unreadable") from None
            return False  # No authenticated parent authority; its marker cannot cancel this target.
        if not same_suite:
            if self.parent_suite_bound:
                raise ValueError("Established F05 suite cancellation identity changed")
            return False
        self.parent_suite_bound = True
        suite_marker = self.child.parent / "cancel.request"
        proof.safe(suite_marker)
        if not suite_marker.exists():
            return False
        proof.bounded(suite_marker, maximum=4096)
        return True

    def check(self, boundary, *, case_id=None):
        from app.resource_runtime import current_scope

        if boundary not in {"provider", "tool", "sandbox", "submission"} or case_id not in {None, "AET-F05"}:
            raise RuntimeError("F05 effect scope differs")
        scope = current_scope()
        if (scope is not None and (str(scope.owner) != self.identity["owner_id"] or scope.mode not in {"work", "workflow", "resource"})
                or scope is None and boundary != "submission"):
            raise RuntimeError("F05 execution owner or mode differs")
        if not self.capability()["dispatch_allowed"]:
            raise RuntimeError("F05 deadline is not active")

    def install_runtime(self, application, state, parent):
        prior_provider = getattr(state, "parent_effect_guard", None)
        prior_effect = getattr(application.state.db, "evaluation_effect_guard", None)
        prior_wrap = getattr(application.state.db, "evaluation_wrap_tools", None)
        if prior_wrap is not None and getattr(prior_wrap, "__self__", None) is not parent:
            raise RuntimeError("Unreviewed existing F05 tool wrapper")

        def provider(case_id):
            self.check("provider", case_id=case_id)
            if prior_provider is not None:
                prior_provider(case_id)
            self.check("provider", case_id=case_id)

        async def effect(boundary):
            self.check(boundary)
            if prior_effect is not None:
                await prior_effect(boundary)
            self.check(boundary)

        state.parent_effect_guard = provider
        application.state.db.evaluation_effect_guard = effect
        application.state.db.evaluation_wrap_tools = partial(parent.wrap_tools, before_effect=self.before_tool)
        version = "platform-effect-permit-v1"
        if getattr(type(state), "parent_guard_contract", None) == version:
            self.registered.add("provider")
        for boundary, names in {"tool": ("app.capabilities", "app.mcp_service", "app.work_engine", "app.workflow_runtime"),
                                "sandbox": ("app.durable_sandboxes",)}.items():
            if all(getattr(import_module(name), "EVALUATION_EFFECT_GUARD_VERSION", None) == version for name in names):
                self.registered.add(boundary)
