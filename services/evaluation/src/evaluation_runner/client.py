"""Formal authenticated HTTP transport and isolated-target attestation."""

import os

import httpx

from .contracts import Target


class Platform:
    def __init__(self, target: Target, *, transport=None):
        self.target = target
        self.expected_sandbox_namespace: str | None = None
        self.http = httpx.Client(base_url=target.api_base_url, headers={"Origin": target.api_base_url},
                                 timeout=15, follow_redirects=False, trust_env=False, transport=transport)

    def login(self) -> None:
        username, password = os.environ.get("EVALUATION_USERNAME"), os.environ.get("EVALUATION_PASSWORD")
        if not username or not password:
            raise ValueError("Dedicated evaluation username/password environment variables are required")
        response = self.http.post("/api/v1/auth/login", json={"username": username, "password": password})
        response.raise_for_status()

    def attest(self) -> dict:
        response = self.http.get("/api/v1/agent-evaluations/environment")
        response.raise_for_status()
        value = response.json()
        expected = self.target
        if (value.get("database_is_test") is not True or value.get("environment_id") != expected.environment_id
                or value.get("owner_id") != expected.owner_id or value.get("platform_commit") != expected.platform_commit
                or value.get("fixture_profile") != expected.fixture_profile.model_dump()
                or not set(expected.required_migrations).issubset(value.get("schema_migrations", []))):
            raise ValueError("Target attestation does not match the registered isolated environment")
        if not isinstance(value.get("permissions"), list) or not all(isinstance(p, str) for p in value["permissions"]):
            raise ValueError("Invalid target permission attestation")
        if self.expected_sandbox_namespace is not None and value.get("sandbox_namespace") != self.expected_sandbox_namespace:
            raise ValueError("The attested sandbox namespace changed")
        return value

    def get(self, path: str, **kwargs) -> dict:
        response = self.http.get(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def post(self, path, **kwargs):
        self.attest()
        return self.http.post(path, **kwargs)

    def close(self) -> None:
        self.http.close()
