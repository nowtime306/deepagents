"""An already-created skill reader must recheck grants and fixed versions."""

from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.errors import APIError


def test_existing_skill_reader_rejects_revoked_binding(client):
    connection, app = client
    name = "skill-" + uuid4().hex[:8]
    content = f"---\nname: {name}\ndescription: 验证\n---\n私有技能"
    result = connection.post(
        "/api/v1/capabilities/skills", files={"file": ("SKILL.md", content.encode())}, headers=ORIGIN
    )
    assert result.status_code == 201
    identifier = result.json()["id"]
    selection = {"knowledge_ids": [], "mcp_ids": [], "skill_ids": [identifier]}
    connection.put("/api/v1/capabilities/bindings", json=selection, headers=ORIGIN)
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM skills WHERE id=%s", (identifier,))[0][
        "owner_id"
    ]
    backend = connection.portal.call(
        app.state.capabilities.skill_backend, owner, selection, {f"/{name}/SKILL.md": content}
    )
    assert connection.portal.call(backend.aread, f"/{name}/SKILL.md").error is None
    connection.put("/api/v1/capabilities/bindings", json={**selection, "skill_ids": []}, headers=ORIGIN)
    with pytest.raises(APIError, match="技能授权或版本已变化"):
        connection.portal.call(backend.aread, f"/{name}/SKILL.md")
