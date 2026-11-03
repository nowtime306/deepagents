"""Reviewed facts use official observations, never an Agent's success claim."""

import hashlib
import json

from .contracts import Check, Result

REGISTERED = {"terminal_status", "artifact_exists", "artifact_sha256", "operation_count", "output_equals", "user_message_count"}


def verify(spec: Check, evidence: dict, platform) -> Result:
    result = Result(registered_id=spec.registered_id, required=spec.required, status="BLOCKED")
    if spec.registered_id not in REGISTERED:
        result.reason = "Reviewed independent observer is not implemented"
        return result
    parameters = spec.parameters
    if spec.registered_id == "terminal_status":
        actual = evidence["run"]["status"]
        passed = actual == parameters["expected"]
    elif spec.registered_id == "operation_count":
        if "operations" not in evidence.get("observed", []) or not isinstance(evidence.get("operations"), list):
            result.reason = "Complete operation evidence has not been observed"
            return result
        actual = len(evidence["operations"])
        passed = actual == parameters["expected"]
    elif spec.registered_id == "user_message_count":
        messages = evidence.get("messages", {})
        if (evidence.get("root", {}).get("mode") != "ask" or "messages" not in evidence.get("observed", [])
                or not isinstance(messages.get("items"), list) or "next_before" not in messages
                or messages["next_before"] is not None):
            result.reason = "A complete authoritative Ask conversation observation is required"
            return result
        actual = sum(row.get("role") == "user" for row in messages["items"])
        passed = actual == parameters["expected"]
    elif spec.registered_id == "output_equals":
        actual = evidence.get("run", {}).get("output")
        if actual is None:
            result.reason = "No structured final-output observation"
            return result
        passed = json.dumps(actual, sort_keys=True) == json.dumps(parameters["expected"], sort_keys=True)
    else:
        if "artifacts" not in evidence.get("observed", []):
            result.reason = "The authoritative latest artifact snapshot has not been observed"
            return result
        matching = [item for item in evidence.get("artifacts", []) if item["path"] == parameters["path"]]
        if not matching:
            result.status, result.reason = "FAIL", "Expected artifact is absent from the official latest snapshot"
            return result
        if spec.registered_id == "artifact_exists":
            actual, passed = True, True
        else:
            row = matching[0]
            if row["size"] > 10 * 1024 * 1024:
                result.reason = "Artifact exceeds the reviewed verification size limit"
                return result
            response = platform.http.get("/api/v1/work-artifacts/" + row["id"] + "/download")
            response.raise_for_status()
            actual = hashlib.sha256(response.content).hexdigest()
            passed = actual == parameters["expected"] == row["digest"]
    result.status = "PASS" if passed else "FAIL"
    result.reason = "Independent observed fact matched" if passed else "Independent observed fact did not match"
    return result
