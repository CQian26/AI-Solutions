import json
import re
import time
from pathlib import Path
from typing import Any

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_key(key: str) -> str:
    return _KEY_SAFE_RE.sub("_", key)[:128]


class Cache:
    """Disk-backed JSON K/V cache with TTL.

    Files live at <root>/<namespace>__<safe-key>.json. Each file is a JSON
    object with `_set_at` (epoch seconds) and `value`. Reads honour TTL.
    """

    def __init__(self, root: Path, default_ttl: float = 86400.0, enabled: bool = True):
        self.root = Path(root)
        self.default_ttl = default_ttl
        self.enabled = enabled
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / f"{namespace}__{_safe_key(key)}.json"

    def get(self, namespace: str, key: str, ttl: float | None = None) -> Any | None:
        if not self.enabled:
            return None
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        set_at = float(data.get("_set_at", 0))
        if time.time() - set_at > (ttl if ttl is not None else self.default_ttl):
            return None
        return data.get("value")

    def set(self, namespace: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        p = self._path(namespace, key)
        payload = {"_set_at": time.time(), "value": value}
        p.write_text(json.dumps(payload), encoding="utf-8")

    def clear(self, namespace: str | None = None) -> None:
        if not self.enabled or not self.root.exists():
            return
        for child in self.root.iterdir():
            if namespace is None or child.name.startswith(f"{namespace}__"):
                try:
                    child.unlink()
                except OSError:
                    pass
