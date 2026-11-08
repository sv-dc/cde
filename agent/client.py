"""Thin HTTP client for the hub.

Every failure is raised as HubError with the status and the hub's message, so the
CLI can print the reason the hub gave instead of a stack trace.
"""

from __future__ import annotations

from typing import Any

import httpx


class HubError(RuntimeError):
    def __init__(self, status: int, detail: str, method: str, path: str):
        super().__init__(f"hub {method} {path} -> {status}: {detail}")
        self.status = status
        self.detail = detail


class HubUnreachable(RuntimeError):
    pass


class HubClient:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ plumbing

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise HubUnreachable(f"cannot reach hub at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise HubError(response.status_code, str(detail), method, path)
        if not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------- project

    def create_project(self, name: str) -> dict:
        return self._request("POST", "/projects", json={"name": name})

    def invite_party(self, project_id: str, name: str) -> dict:
        return self._request(
            "POST", f"/projects/{project_id}/parties", json={"name": name}
        )

    def claim_party(self, project_id: str, invite_token: str, public_key_b64: str) -> dict:
        return self._request(
            "POST",
            f"/projects/{project_id}/parties",
            json={"invite_token": invite_token, "public_key_b64": public_key_b64},
        )

    def announce_address(self, project_id: str, party_id: str, host: str, port: int) -> dict:
        return self._request(
            "POST",
            f"/projects/{project_id}/parties/{party_id}/address",
            json={"host": host, "port": port},
        )

    # ---------------------------------------------------------------- containers

    def create_container(
        self, project_id: str, number: str, title: str, type_: str, originator_party: str
    ) -> dict:
        return self._request(
            "POST",
            f"/projects/{project_id}/containers",
            json={
                "number": number,
                "title": title,
                "type": type_,
                "originator_party": originator_party,
            },
        )

    def list_containers(self, project_id: str) -> list[dict]:
        return self._request("GET", f"/projects/{project_id}/containers")

    # ----------------------------------------------------------------- revisions

    def post_revision(self, project_id: str, body: dict) -> dict:
        return self._request("POST", f"/projects/{project_id}/revisions", json=body)

    def list_revisions(self, project_id: str, since: str | None = None) -> list[dict]:
        params = {"since": since} if since else None
        return self._request("GET", f"/projects/{project_id}/revisions", params=params)

    # ------------------------------------------------------------------- holders

    def list_holders(self, project_id: str, revision_id: str) -> list[dict]:
        return self._request(
            "GET", f"/projects/{project_id}/revisions/{revision_id}/holders"
        )

    def register_holder(self, project_id: str, revision_id: str, party_id: str, path: str) -> dict:
        return self._request(
            "POST",
            f"/projects/{project_id}/revisions/{revision_id}/holders",
            json={"party_id": party_id, "path": path},
        )

    def stats(self) -> dict:
        return self._request("GET", "/stats")

    def health(self) -> dict:
        return self._request("GET", "/health")
