"""Running evaluations does not require dataset-management privileges."""

import pytest

from app.permissions import route_permission


@pytest.mark.parametrize(("method", "path", "permission"), [
    ("GET", "/capabilities", "evaluation.read"),
    ("GET", "/datasets", "evaluation.read"),
    ("GET", "/runs/123/report", "evaluation.read"),
    ("POST", "/datasets", "evaluation.manage"),
    ("POST", "/datasets/123/versions", "evaluation.manage"),
    ("POST", "/runs", "evaluation.run"),
    ("POST", "/runs/123/cancel", "evaluation.run"),
    ("POST", "/comparisons", "evaluation.manage"),
    ("POST", "/failure-candidates", "evaluation.manage"),
])
def test_evaluation_route_permission(method, path, permission):
    assert route_permission("/api/v1/agent-evaluations" + path, method) == permission
