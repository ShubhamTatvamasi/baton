"""Settings and OpenCode service discovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

STATE_FILE = Path.home() / ".local" / "state" / "opencode" / "service.json"
PRIVATE_FILE = Path.home() / ".config" / "opencode" / "service.json"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    url: str
    password: str
    username: str = "opencode"
    model: str | None = None  # "providerID/modelID" for the judge, optional

    @property
    def auth(self) -> tuple[str, str]:
        return (self.username, self.password)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc


def load_settings(project: Path = Path(".")) -> Settings:
    """Discover the running OpenCode service.

    Order: environment, service registration, private config. A project
    ``.baton/config.json`` can pin the judge model.
    """
    state = _read(STATE_FILE)
    private = _read(PRIVATE_FILE)

    url = os.environ.get("OPENCODE_URL") or state.get("url")
    password = os.environ.get("OPENCODE_PASSWORD") or state.get("password") or private.get("password")
    username = os.environ.get("OPENCODE_USERNAME", "opencode")

    if not url:
        raise ConfigError(
            "no OpenCode service found. Start it with `opencode service start` "
            "or set OPENCODE_URL."
        )
    if not password:
        raise ConfigError(
            "no service password found. Set OPENCODE_PASSWORD or check "
            f"{STATE_FILE}."
        )

    model = os.environ.get("SUPER_MODEL")
    project_config = project / ".baton" / "config.json"
    if project_config.exists():
        model = _read(project_config).get("judge_model") or model
    if not model:
        model = _read(OPENCODE_CONFIG).get("model")

    return Settings(url=url.rstrip("/"), password=password, username=username, model=model)
