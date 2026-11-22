"""Thin client for the OpenCode HTTP API.

Only the endpoints the supervisor needs. Everything is synchronous except the
event stream, which is a generator so callers can stop iterating.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any, Self

import httpx

from .config import Settings


class OpenCodeError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"{path} -> HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


class OpenCode:
    def __init__(self, settings: Settings, timeout: float = 60.0):
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.url,
            auth=settings.auth,
            timeout=timeout,
            headers={"accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise OpenCodeError(response.status_code, response.text, path)
        if response.status_code == 204 or not response.content:
            return None
        payload = response.json()
        return payload.get("data", payload)

    def info(self) -> dict:
        return self._request("GET", "/api/info")

    def list_models(self) -> list[dict]:
        return self._request("GET", "/api/model")

    def resolve_model(self, ref: str) -> dict:
        """Resolve ``provider/model`` against the server's model list.

        Provider IDs and model IDs can both contain slashes (for example
        ``vendor/vendor/model-name``), so exact string splitting is not
        enough. Match on the model ID, then on a suffix.
        """
        provider, _, rest = ref.partition("/")
        if not rest:
            raise ValueError(f"model must look like provider/model, got {ref!r}")

        models = [m for m in self.list_models() if m.get("providerID") == provider]
        if not models:
            raise ValueError(f"provider {provider!r} is not available")

        for model in models:
            if model.get("modelID") in (rest, ref) or model.get("id") in (rest, ref):
                return {"providerID": provider, "id": model["modelID"]}

        suffix = [m for m in models if str(m.get("modelID", "")).endswith("/" + rest)]
        if len(suffix) == 1:
            return {"providerID": provider, "id": suffix[0]["modelID"]}

        available = ", ".join(f"{m['providerID']}/{m['modelID']}" for m in models[:10])
        raise ValueError(f"cannot resolve {ref!r}; provider offers: {available}")

    def model_ref(self, model: str | dict | None) -> dict | None:
        if model is None:
            return None
        if isinstance(model, dict):
            return model
        return self.resolve_model(model)

    # -- sessions ---------------------------------------------------------

    def create_session(
        self,
        directory: str,
        *,
        title: str | None = None,
        agent: str | None = None,
        model: str | dict | None = None,
        parent_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"location": {"directory": directory}}
        if title:
            body["title"] = title
        if agent:
            body["agent"] = agent
        resolved = self.model_ref(model)
        if resolved:
            body["model"] = resolved
        if parent_id:
            body["parentID"] = parent_id
        return self._request("POST", "/api/session", json=body)

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/api/session/{session_id}")

    def list_sessions(self, limit: int = 20) -> list[dict]:
        return self._request("GET", "/api/session", params={"limit": str(limit)})

    def active_sessions(self) -> dict[str, dict]:
        return self._request("GET", "/api/session/active")

    def latest_session_at(self, directory: str) -> dict | None:
        for session in self.list_sessions(limit=50):
            if session.get("location", {}).get("directory") == directory:
                return session
        return None

    def prompt(self, session_id: str, text: str) -> dict:
        return self._request("POST", f"/api/session/{session_id}/prompt", json={"text": text})

    def messages(self, session_id: str, limit: int = 40, order: str = "desc") -> list[dict]:
        return self._request(
            "GET",
            f"/api/session/{session_id}/message",
            params={"limit": str(limit), "order": order},
        )

    def wait(self, session_id: str, timeout: float = 3600.0) -> None:
        """Block until the session is idle.

        Prefers the server's wait route, which knows about queued inputs. Falls
        back to polling if that route is unavailable.
        """
        try:
            self._request(
                "POST",
                f"/api/experimental/session/{session_id}/wait",
                timeout=httpx.Timeout(timeout, connect=10.0),
            )
            return
        except OpenCodeError as exc:
            if exc.status not in (404, 503):
                raise

        started = False
        deadline = time.monotonic() + timeout
        grace = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if session_id in self.active_sessions():
                started = True
            elif started or time.monotonic() > grace:
                return
            time.sleep(2.0)
        raise TimeoutError(f"session {session_id} still running after {timeout:.0f}s")

    def diff(self, session_id: str) -> Any:
        return self._request("GET", f"/api/session/{session_id}/diff")

    # -- one-shot generation (judge) --------------------------------------

    def generate_text(self, prompt: str, model: str | dict | None = None) -> str:
        body: dict[str, Any] = {"prompt": prompt}
        resolved = self.model_ref(model)
        if resolved:
            body["model"] = resolved
        data = self._request("POST", "/api/experimental/generate/text", json=body)
        return data["text"]

    # -- events -----------------------------------------------------------

    def events(self, types: set[str] | None = None) -> Iterator[dict]:
        """Yield live server events. Live-only: no replay on reconnect."""
        with self._client.stream("GET", "/api/event", timeout=None) as response:
            if response.status_code >= 400:
                body = response.read().decode(errors="replace")
                raise OpenCodeError(response.status_code, body, "/api/event")

            data_lines: list[str] = []
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                    continue
                if line.strip() or not data_lines:
                    continue
                raw = "\n".join(data_lines)
                data_lines.clear()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if types is None or event.get("type") in types:
                    yield event
