"""Atomic JSON save files. One small file, no database, no background daemon."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DEFAULT = {
    "version": 1,
    "created_ms": 0,
    "platform": {"name": "Dittobuffer Platform"},
    "rooms": {},
}


class Store:
    """Thread-safe JSON document with atomic replace on every write."""

    def __init__(self, path: str | os.PathLike, data: dict | None = None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = data or self._read()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return json.loads(json.dumps(DEFAULT))
        if not isinstance(value, dict):
            return json.loads(json.dumps(DEFAULT))
        value.setdefault("version", 1)
        value.setdefault("platform", {"name": "Dittobuffer Platform"})
        value.setdefault("rooms", {})
        if not value.get("created_ms"):
            value["created_ms"] = int(time.time() * 1000)
        return value

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

    def rooms(self) -> dict:
        with self.lock:
            return self.data["rooms"]

    def room(self, domain: str) -> dict | None:
        with self.lock:
            return self.data["rooms"].get(domain)

    def put_room(self, domain: str, value: dict) -> dict:
        with self.lock:
            self.data["rooms"][domain] = value
            self.save()
            return value

    def drop_room(self, domain: str) -> bool:
        with self.lock:
            removed = self.data["rooms"].pop(domain, None) is not None
            if removed:
                self.save()
            return removed
