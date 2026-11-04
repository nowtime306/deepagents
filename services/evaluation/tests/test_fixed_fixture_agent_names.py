"""Strict UNIQUE(owner_id,name) MockTransport regression; not a real API/PG test."""

import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from evaluation_runner import fixed_fixtures as module
from evaluation_runner.client import Platform
from evaluation_runner.contracts import Target
from evaluation_runner.journal import Journal, UnknownSubmission


class StrictAgentAPI:
    def __init__(self):
        self.agents, self.names, self.versions, self.posts = {}, set(), {}, []

    def platform(self, owner):
        target = Target(
            environment_id=str(uuid4()),
            owner_id=owner,
            api_base_url="http://127.0.0.1:29991",
            platform_commit="a" * 40,
            required_migrations=[],
            fixture_profile={"id": "controlled-v1", "version": 1},
        )

        def handler(request):
            if (
                request.method == "GET"
                and request.url.path == "/api/v1/agent-evaluations/environment"
            ):
                return httpx.Response(
                    200,
                    json={
                        "database_is_test": True,
                        "environment_id": target.environment_id,
                        "owner_id": owner,
                        "platform_commit": target.platform_commit,
                        "fixture_profile": target.fixture_profile.model_dump(),
                        "schema_migrations": [],
                        "permissions": ["agents.manage"],
                    },
                )
            body = json.loads(request.content)
            self.posts.append((owner, request.url.path, deepcopy(body)))
            if request.url.path == "/api/v1/agents":
                key = (owner, body["name"])
                if key in self.names:
                    return httpx.Response(
                        409, json={"error": {"code": "AGENT_NAME_CONFLICT"}}
                    )
                self.names.add(key)
                identifier = str(uuid4())
                self.agents[identifier] = {**body, "owner_id": owner, "id": identifier}
                return httpx.Response(201, json={"id": identifier, "revision": 1})
            if request.url.path.endswith("/publish"):
                identifier = request.url.path.split("/")[-2]
                assert body == {"revision": 1}
                assert self.agents[identifier]["owner_id"] == owner
                version = str(uuid4())
                self.versions[version] = deepcopy(self.agents[identifier])
                return httpx.Response(201, json={"id": version, "version": 1})
            raise AssertionError("Unexpected fixture effect")

        return Platform(target, transport=httpx.MockTransport(handler))


def fixture(platform, journal):
    attempt = {"attempt_id": "123456789012-constant-private-test"}
    journal.data["attempts"] = [attempt]
    return module.FixedFixtures(platform, journal, attempt)


def test_same_owner_same_prefix_two_models_publish_child_and_parent_without_name_conflict(
    tmp_path,
):
    api, owner = StrictAgentAPI(), str(uuid4())
    platform = api.platform(owner)
    with_journal = Journal(tmp_path)
    try:
        fixtures = fixture(platform, with_journal)
        child_model, parent_model = str(uuid4()), str(uuid4())
        child = fixtures.published_agent("AET-W05", child_model)
        team = [
            {
                "name": "reviewer",
                "version_id": child["agent_version_id"],
                "goal": "读取input.txt并审阅",
            }
        ]
        parent = fixtures.published_agent("AET-W05", parent_model, team=team)
        assert (
            len(
                {
                    child["agent_id"],
                    parent["agent_id"],
                    child["agent_version_id"],
                    parent["agent_version_id"],
                }
            )
            == 4
        )
        assert (
            api.agents[child["agent_id"]]["name"]
            != api.agents[parent["agent_id"]]["name"]
        )
        assert api.versions[child["agent_version_id"]]["model_id"] == child_model
        assert api.versions[parent["agent_version_id"]]["model_id"] == parent_model
        assert api.versions[child["agent_version_id"]]["team"] == []
        assert api.versions[parent["agent_version_id"]]["team"] == team
        assert all(
            value["status"] == "CONFIRMED"
            for value in fixtures.attempt["fixture_intents"].values()
        )
        assert {row["id"] for row in fixtures.attempt["created_resources"]} == {
            child["agent_id"],
            parent["agent_id"],
        }
        before = len(api.posts)
        assert fixtures.published_agent("AET-W05", parent_model, team=team) == parent
        assert (
            len(api.posts) == before
        )  # confirmed intent replay reads, no extra POST/random retry
        assert len(api.posts) == 4
    finally:
        platform.close()
        with_journal.close()


def test_existing_same_owner_name_still_rejected_and_fixture_does_not_retry(tmp_path):
    api, owner, model = StrictAgentAPI(), str(uuid4()), str(uuid4())
    platform = api.platform(owner)
    first, second = Journal(tmp_path / "first"), Journal(tmp_path / "second")
    try:
        fixture(platform, first).published_agent("AET-W05", model)
        other = fixture(platform, second)
        with pytest.raises(ValueError, match="409"):
            other.published_agent("AET-W05", model)
        intent = other.attempt["fixture_intents"]["agent-" + model]
        assert (intent["status"], intent["http_status"], intent["error_code"]) == (
            "REJECTED",
            409,
            "AGENT_NAME_CONFLICT",
        )
        before = len(api.posts)
        with pytest.raises(UnknownSubmission):
            other.published_agent("AET-W05", model)
        assert len(api.posts) == before
    finally:
        platform.close()
        first.close()
        second.close()


def test_same_name_other_owner_still_allowed(tmp_path):
    api, model = StrictAgentAPI(), str(uuid4())
    owners = [str(uuid4()), str(uuid4())]
    results = []
    for index, owner in enumerate(owners):
        platform, journal = api.platform(owner), Journal(tmp_path / str(index))
        try:
            results.append(fixture(platform, journal).published_agent("AET-W05", model))
        finally:
            platform.close()
            journal.close()
    rows = [api.agents[value["agent_id"]] for value in results]
    assert (
        rows[0]["name"] == rows[1]["name"]
        and rows[0]["owner_id"] != rows[1]["owner_id"]
    )
