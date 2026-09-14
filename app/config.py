from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is invalid."""


def _read_secret(path: str, label: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigurationError(f"Unable to read {label} file") from error
    if len(value) < 16:
        raise ConfigurationError(f"{label} must contain at least 16 characters")
    return value


@dataclass(frozen=True)
class SessionAllowlist:
    groups: frozenset[str]
    friends: frozenset[str]

    def permits(self, kind: str, identifier: str) -> bool:
        candidates = self.groups if kind == "group" else self.friends
        return identifier in candidates


def _load_allowlist(path: str | None) -> SessionAllowlist | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        groups = value.get("groups", [])
        friends = value.get("friends", [])
        if not isinstance(groups, list) or not isinstance(friends, list):
            raise TypeError
        return SessionAllowlist(
            groups=frozenset(str(item) for item in groups),
            friends=frozenset(str(item) for item in friends),
        )
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as error:
        raise ConfigurationError("Invalid session allowlist file") from error


@dataclass(frozen=True)
class Settings:
    qce_base_url: str
    qce_token: str
    adapter_token: str
    timeout_seconds: float
    cache_seconds: float
    allowlist: SessionAllowlist | None

    @classmethod
    def from_environment(cls) -> Settings:
        base_url = os.environ.get("QCE_BASE_URL", "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError("QCE_BASE_URL must be an HTTP(S) URL")
        return cls(
            qce_base_url=base_url,
            qce_token=_read_secret(
                os.environ.get("QCE_TOKEN_FILE", "/run/secrets/qce/token"), "QCE token"
            ),
            adapter_token=_read_secret(
                os.environ.get("ADAPTER_TOKEN_FILE", "/run/secrets/adapter/token"),
                "adapter token",
            ),
            timeout_seconds=float(os.environ.get("QCE_TIMEOUT_SECONDS", "45")),
            cache_seconds=float(os.environ.get("SESSION_CACHE_SECONDS", "30")),
            allowlist=_load_allowlist(os.environ.get("SESSION_ALLOWLIST_FILE")),
        )
