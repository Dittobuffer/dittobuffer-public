"""Server workspace management for the Dittobuffer platform admin studio.

The platform is deliberately dependency free.  This module therefore talks to
the server through the things every release has on disk: JSON profiles, small
data files, SQLite databases, the Game Master directory and the server process
itself.  Every mutation is validated, written atomically and copied to an
external backup store before the original is replaced.

Security posture
----------------
* All paths are resolved inside the configured ``server/`` folder.  Absolute
  paths, ``..`` components and symlinks that escape the folder are rejected.
* Binary payloads are never editable through the text API.
* Mutating endpoints are protected by the platform admin token when one is set.
* The database editor only touches SQLite files that live inside the workspace.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_TEXT_BYTES = 4_000_000
MAX_DB_ROWS = 500
BACKUP_KEEP = 240
_TEXT_SUFFIXES = {
    ".json", ".json5", ".jsonc", ".md", ".txt", ".toml", ".ini", ".cfg",
    ".yaml", ".yml", ".proto", ".py", ".js", ".ts", ".css", ".html", ".sh",
    ".bat", ".ps1", ".sql", ".csv", ".xml", ".env", ".example", ".properties",
    ".gradle", ".kts", ".java", ".rs", ".c", ".h", ".gitignore", ".editorconfig",
}
_TEXT_NAMES = {
    ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig", "Makefile",
    "Dockerfile", "Dockerfile.riscv64", "LICENSE", "CHANGELOG", "README",
}
_BINARY_SUFFIXES = {
    ".so", ".dll", ".exe", ".apk", ".idsig", ".jks", ".keystore", ".pem", ".key",
    ".crt", ".cer", ".sqlite3", ".sqlite", ".db", ".db-wal", ".db-shm", ".zip",
    ".jar", ".class", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".ico", ".bundle", ".bin", ".binpb", ".gz", ".zip", ".7z", ".tar", ".ogg",
    ".wav", ".mp3", ".mp4", ".ttf", ".otf", ".woff", ".woff2", ".fbx", ".asset",
}
_EXCLUDED_DIRS = {
    ".git", ".github", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "node_modules", ".private", ".tools", ".staging", "logs", "run",
    "build", "dist", ".gradle", ".splash", "target",
}
_IGNORED_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
}
_PROFILE_NETWORK_MODES = {"loopback", "tls", "private-vpn"}
_PROFILE_AUTH_MODES = {"family", "local", "legacy"}
_PROFILE_REGISTRATION = {"open", "invite"}


def now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".admin-tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _is_text(name: str) -> bool:
    path = Path(name)
    if path.name in _TEXT_NAMES:
        return True
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return False
    return path.suffix.lower() in _TEXT_SUFFIXES or path.name.startswith(".")


def _cell(value):
    """JSON-safe representation of a SQLite cell."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"$base64": base64.b64encode(raw).decode("ascii"), "bytes": len(raw)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _decode_cell(value, column_type: str = ""):
    if isinstance(value, dict) and "$base64" in value:
        try:
            return base64.b64decode(str(value["$base64"]), validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid base64 blob value") from None
    if isinstance(value, dict) and "$null" in value:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def _human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.0f} B"


def _categories(relative: str) -> str:
    top = relative.split("/", 1)[0]
    if relative.startswith("config/"):
        return "Configuration"
    if relative.startswith("data/"):
        return "Game data"
    if relative.startswith("assets/game-master/"):
        return "Game Master"
    if relative.startswith("src/"):
        return "Server source"
    if relative.startswith("protocol/"):
        return "Protocol"
    if relative.startswith("tests/"):
        return "Tests"
    if relative.startswith("docs/"):
        return "Documentation"
    if relative.startswith("client/"):
        return "Client source"
    if relative.startswith("android/"):
        return "Android source"
    if relative.startswith("scripts/"):
        return "Scripts"
    if top in {"README.md", "CHANGELOG.md", "LICENSE", "Makefile", "pyproject.toml"}:
        return "Repository"
    return "Other"


def _section_for(relative: str) -> str:
    """Editor topic used by the UI's curated sections."""
    if relative.startswith("data/admin/") or relative.endswith("stop-loot.json"):
        return "loot"
    if relative.startswith("data/world/") or relative.startswith("assets/world/"):
        return "world"
    if relative.startswith("assets/game-master/") or relative.startswith("data/static/"):
        return "gamemaster"
    if "raid" in relative:
        return "raids"
    if "rocket" in relative:
        return "rocket"
    if "pvp" in relative:
        return "pvp"
    if "max-" in relative or "max_" in relative or "/max" in relative:
        return "max"
    if "research" in relative:
        return "research"
    if "liveops" in relative:
        return "liveops"
    return ""


class WorkspaceError(ValueError):
    """A user-correctable workspace error."""


class NotFound(WorkspaceError):
    """A file, profile, database or row does not exist."""


class ServerWorkspace:
    """Read/write access to one release ``server/`` folder."""

    def __init__(self, root, backup_dir=None, platform_config_path=None):
        self.root = Path(root).resolve() if root else None
        self.backup_dir = Path(backup_dir).resolve() if backup_dir else (
            self.root.parent / "platform" / "data" / "admin-backups" if self.root else None
        )
        self.platform_config_path = Path(platform_config_path).resolve() if platform_config_path else None
        self._lock = threading.RLock()
        self._status_cache = {"at": 0, "value": None}
        self._process = None
        self._pid_path = (self.backup_dir / "server.pid") if self.backup_dir else None

    # ------------------------------------------------------------------ paths
    @property
    def available(self) -> bool:
        return bool(self.root and self.root.is_dir())

    @property
    def display_root(self) -> str:
        """Portable workspace label for the browser; never expose a home path."""
        if self.platform_config_path and self.platform_config_path.is_file():
            try:
                value = json.loads(self.platform_config_path.read_text(encoding="utf-8"))
                configured = str(value.get("server_root") or "").strip()
                if configured and not Path(configured).is_absolute():
                    return configured
            except (OSError, ValueError):
                pass
        return "."

    def _require(self) -> Path:
        if not self.available:
            raise WorkspaceError(
                "Server workspace not found. Set server_root in platform/config.json "
                "to the release server/ folder.")
        return self.root

    def resolve(self, relative) -> Path:
        root = self._require()
        text = str(relative or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or text.startswith("~"):
            raise WorkspaceError("A relative path inside the server folder is required")
        candidate = (root / text).resolve()
        if candidate != root and not candidate.is_relative_to(root):
            raise WorkspaceError("Path escapes the server folder")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self._require()).as_posix()

    def _profile_path(self, name: str | None) -> Path:
        root = self._require()
        chosen = str(name or self.active_profile_name()).strip()
        if not chosen:
            raise NotFound("No server profile exists in config/")
        if not chosen.endswith(".json"):
            chosen += ".json"
        if chosen.startswith("config/"):
            relative = chosen
        else:
            relative = "config/" + chosen
        path = self.resolve(relative)
        if path.parent != root / "config":
            raise WorkspaceError("Profiles must live directly in config/")
        return path

    # --------------------------------------------------------------- profiles
    def profile_names(self) -> list[str]:
        root = self.root
        if not root or not (root / "config").is_dir():
            return []
        names = []
        for path in sorted((root / "config").glob("*.json")):
            if path.is_file():
                names.append(path.name)
        return names

    def active_profile_name(self) -> str:
        names = self.profile_names()
        configured = ""
        if self.platform_config_path and self.platform_config_path.is_file():
            try:
                value = json.loads(self.platform_config_path.read_text(encoding="utf-8"))
                configured = str(value.get("server_profile") or "")
            except (OSError, ValueError):
                configured = ""
        if configured.startswith("config/"):
            configured = configured[len("config/"):]
        if configured in names:
            return configured
        for preferred in ("server.json", "family.json", "development.json"):
            if preferred in names:
                return preferred
        return names[0] if names else ""

    def _profile_summary(self, path: Path) -> dict:
        value = {}
        error = ""
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            error = str(exc)
        if not isinstance(value, dict):
            value = {}
        network = value.get("network") if isinstance(value.get("network"), dict) else {}
        auth = value.get("authentication") if isinstance(value.get("authentication"), dict) else {}
        world = value.get("world") if isinstance(value.get("world"), dict) else {}
        features = {
            "raids": bool((value.get("raids") or {}).get("enabled", False)),
            "pvp": bool((value.get("pvp") or {}).get("enabled", False)),
            "max": bool((value.get("max_system") or {}).get("enabled", False)),
            "research": bool((value.get("research") or {}).get("enabled", False)),
            "social": bool((value.get("social") or {}).get("enabled", False)),
            "world": bool(world.get("enabled", False)),
            "liveops": bool((value.get("liveops") or {}).get("enabled", False)),
        }
        return {
            "name": path.name,
            "path": self.relative(path),
            "bytes": path.stat().st_size if path.exists() else 0,
            "modified_ms": int(path.stat().st_mtime * 1000) if path.exists() else 0,
            "valid": not error and isinstance(value, dict),
            "error": error,
            "host": value.get("host", ""),
            "port": value.get("port", 0),
            "database": value.get("database", ""),
            "network": network.get("mode", ""),
            "authentication": auth.get("mode", ""),
            "registration": auth.get("registration", ""),
            "profile": value.get("profile", ""),
            "features": features,
        }

    def profiles(self) -> dict:
        if not self.available:
            return {"profiles": [], "active": "", "root": self.display_root}
        return {
            "profiles": [self._profile_summary(self._profile_path(name))
                         for name in self.profile_names() if self._profile_path(name).exists()],
            "active": self.active_profile_name(),
            "root": self.display_root,
        }

    def profile(self, name: str | None = None) -> dict:
        path = self._profile_path(name)
        if not path.is_file():
            raise NotFound("Profile not found: " + str(name or self.active_profile_name()))
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise WorkspaceError(f"{path.name} is not valid JSON: {exc}") from None
        if not isinstance(value, dict):
            raise WorkspaceError("A profile must be a JSON object")
        return {
            "name": path.name,
            "path": self.relative(path),
            "data": value,
            "validation": self.validate_profile(value),
            "modified_ms": int(path.stat().st_mtime * 1000),
        }

    def validate_profile(self, value) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        if not isinstance(value, dict):
            return {"errors": ["Profile must be a JSON object"], "warnings": []}
        host = value.get("host")
        if not isinstance(host, str) or not host.strip():
            errors.append("host must be a non-empty string")
        port = value.get("port")
        if not isinstance(port, int) or isinstance(port, bool):
            errors.append("port must be an integer")
        elif not 1 <= port <= 65535:
            errors.append("port must be between 1 and 65535")
        elif port == 8080:
            warnings.append("Port 8080 is commonly used by other local services; choose a dedicated game port")
        database = value.get("database")
        if not isinstance(database, str) or not database.strip():
            errors.append("database must be a path inside the server folder")
        else:
            try:
                path = self.resolve(database)
                if path.is_dir():
                    errors.append("database points at a directory")
                if path.suffix.lower() not in (".sqlite3", ".sqlite", ".db"):
                    warnings.append("database does not use a .sqlite3/.sqlite/.db suffix")
                if not path.parent.is_dir():
                    warnings.append("database parent folder does not exist yet")
            except WorkspaceError as exc:
                errors.append(str(exc))
        network = value.get("network")
        if not isinstance(network, dict):
            errors.append("network must be an object")
        else:
            mode = network.get("mode")
            if mode not in _PROFILE_NETWORK_MODES:
                errors.append("network.mode must be loopback, tls or private-vpn")
            elif mode == "tls":
                for key in ("certificate", "private_key"):
                    item = network.get(key)
                    if not isinstance(item, str) or not item:
                        errors.append(f"network.{key} is required for TLS")
                    else:
                        try:
                            if not self.resolve(item).is_file():
                                warnings.append(f"network.{key} does not exist yet")
                        except WorkspaceError as exc:
                            errors.append(str(exc))
        auth = value.get("authentication")
        if not isinstance(auth, dict):
            warnings.append("authentication is missing; family accounts will be disabled")
        else:
            if auth.get("mode") not in _PROFILE_AUTH_MODES:
                errors.append("authentication.mode must be family, local or legacy")
            if auth.get("registration") and auth["registration"] not in _PROFILE_REGISTRATION:
                errors.append("authentication.registration must be open or invite")
        world = value.get("world")
        if isinstance(world, dict) and world.get("enabled"):
            manifest = world.get("manifest") or world.get("json")
            if manifest:
                try:
                    if not self.resolve(manifest).is_file():
                        warnings.append(f"world source does not exist yet: {manifest}")
                except WorkspaceError as exc:
                    errors.append(str(exc))
            else:
                warnings.append("world is enabled but has no manifest or json source")
        for block, reference in (("max_system", "reference"), ("pvp", "reference")):
            item = value.get(block)
            if isinstance(item, dict) and item.get("enabled") and item.get(reference):
                try:
                    if not self.resolve(str(item[reference])).is_file():
                        warnings.append(f"{block}.{reference} does not exist: {item[reference]}")
                except WorkspaceError as exc:
                    errors.append(str(exc))
        if isinstance(port, int) and isinstance(host, str) and host in {"0.0.0.0", "::"}:
            warnings.append("The game server binds all interfaces; use loopback or private VPN explicitly")
        return {"errors": errors, "warnings": warnings}

    def save_profile(self, name: str, data: dict, strict: bool = True) -> dict:
        if not isinstance(data, dict):
            raise WorkspaceError("Profile must be a JSON object")
        path = self._profile_path(name)
        validation = self.validate_profile(data)
        if strict and validation["errors"]:
            raise WorkspaceError("; ".join(validation["errors"]))
        if path.exists():
            self._backup(path)
        _atomic_write(path, _json_bytes(data))
        if self.platform_config_path and self.platform_config_path.is_file():
            try:
                settings = json.loads(self.platform_config_path.read_text(encoding="utf-8"))
                if isinstance(settings, dict):
                    settings["server_profile"] = "config/" + path.name
                    _atomic_write(self.platform_config_path, _json_bytes(settings))
            except (OSError, ValueError):
                pass
        self._invalidate()
        return self.profile(path.name)

    def delete_profile(self, name: str) -> dict:
        path = self._profile_path(name)
        if not path.is_file():
            raise NotFound("Profile not found")
        if len(self.profile_names()) <= 1:
            raise WorkspaceError("Keep at least one server profile")
        self._backup(path)
        path.unlink()
        self._invalidate()
        return self.profiles()

    # -------------------------------------------------------------- documents
    def documents(self, query: str = "", category: str = "") -> dict:
        root = self.root
        if not root or not root.is_dir():
            return {"documents": [], "categories": [], "root": self.display_root}
        needle = str(query or "").strip().casefold()
        wanted = str(category or "").strip().casefold()
        rows = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs
                       if name not in _EXCLUDED_DIRS and not name.startswith(".")
                       and not (Path(base) / name).is_symlink()]
            relative_base = Path(base).relative_to(root).as_posix()
            if relative_base == ".":
                relative_base = ""
            for name in files:
                if name in _IGNORED_FILES or not _is_text(name):
                    continue
                path = Path(base) / name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > MAX_TEXT_BYTES:
                    continue
                relative = f"{relative_base}/{name}" if relative_base else name
                category_name = _categories(relative)
                entry = {
                    "path": relative,
                    "name": name,
                    "suffix": path.suffix.lower(),
                    "bytes": size,
                    "modified_ms": int(path.stat().st_mtime * 1000),
                    "category": category_name,
                    "section": _section_for(relative),
                    "json": path.suffix.lower() in (".json", ".json5", ".jsonc"),
                }
                rows.append(entry)
        rows.sort(key=lambda row: (row["category"], row["path"]))
        categories = sorted({row["category"] for row in rows})
        if wanted:
            rows = [row for row in rows if row["category"].casefold() == wanted]
        if needle:
            rows = [row for row in rows
                    if needle in row["path"].casefold() or needle in row["name"].casefold()]
        return {"documents": rows, "categories": categories, "root": self.display_root,
                "total": len(rows)}

    def document(self, relative: str) -> dict:
        path = self.resolve(relative)
        if not path.is_file():
            raise NotFound("File not found: " + str(relative))
        if not _is_text(path.name):
            raise WorkspaceError("Only text files can be opened in the editor")
        if path.stat().st_size > MAX_TEXT_BYTES:
            raise WorkspaceError("File is larger than the 4 MB editor limit")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("File is not UTF-8 text") from None
        value = None
        json_error = ""
        if path.suffix.lower() in (".json", ".json5", ".jsonc"):
            try:
                value = json.loads(text)
            except ValueError as exc:
                json_error = str(exc)
        return {
            "path": self.relative(path),
            "name": path.name,
            "category": _categories(self.relative(path)),
            "section": _section_for(self.relative(path)),
            "bytes": path.stat().st_size,
            "modified_ms": int(path.stat().st_mtime * 1000),
            "text": text,
            "value": value,
            "json": path.suffix.lower() in (".json", ".json5", ".jsonc"),
            "json_error": json_error,
        }

    def save_document(self, relative: str, text: str | None = None, value=None) -> dict:
        path = self.resolve(relative)
        if not _is_text(path.name):
            raise WorkspaceError("Only text files can be edited")
        if text is None:
            if value is None:
                raise WorkspaceError("Provide file text or a JSON value")
            text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        if not isinstance(text, str):
            raise WorkspaceError("File text must be a string")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise WorkspaceError("File is larger than the 4 MB editor limit")
        is_json = path.suffix.lower() in (".json", ".json5", ".jsonc")
        if is_json:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise WorkspaceError(f"Invalid JSON: {exc}") from None
            if value is not None and parsed != value:
                raise WorkspaceError("The JSON text does not match the structured value")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._backup(path)
        _atomic_write(path, text.encode("utf-8"))
        return self.document(relative)

    # ---------------------------------------------------------------- backups
    def _backup(self, path: Path) -> Path | None:
        if not self.backup_dir:
            return None
        try:
            relative = self.relative(path)
        except (WorkspaceError, ValueError):
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        target = self.backup_dir / f"{stamp}-{now_ms() % 1000:03d}" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError:
            return None
        self._prune_backups()
        return target

    def _prune_backups(self) -> None:
        if not self.backup_dir or not self.backup_dir.is_dir():
            return
        entries = []
        for path in self.backup_dir.rglob("*"):
            if path.is_file() and path.name != "server.pid":
                try:
                    entries.append((path.stat().st_mtime, path))
                except OSError:
                    pass
        if len(entries) <= BACKUP_KEEP:
            return
        for _, path in sorted(entries)[: len(entries) - BACKUP_KEEP]:
            try:
                path.unlink()
            except OSError:
                pass

    def backups(self) -> dict:
        rows = []
        if self.backup_dir and self.backup_dir.is_dir():
            for path in self.backup_dir.rglob("*"):
                if not path.is_file() or path.name == "server.pid":
                    continue
                try:
                    relative = path.relative_to(self.backup_dir).as_posix()
                    stamp = relative.split("/", 1)[0]
                    rows.append({
                        "backup": relative,
                        "stamp": stamp,
                        "target": relative.split("/", 1)[1] if "/" in relative else "",
                        "bytes": path.stat().st_size,
                        "modified_ms": int(path.stat().st_mtime * 1000),
                    })
                except (OSError, ValueError):
                    continue
        rows.sort(key=lambda row: row["modified_ms"], reverse=True)
        return {"backups": rows[:300], "root": self.backup_dir.name if self.backup_dir else ""}

    def restore_backup(self, backup: str) -> dict:
        if not self.backup_dir:
            raise WorkspaceError("Backups are not configured")
        source = (self.backup_dir / str(backup or "")).resolve()
        if not source.is_relative_to(self.backup_dir) or not source.is_file():
            raise NotFound("Backup not found")
        relative = source.relative_to(self.backup_dir).as_posix()
        parts = relative.split("/", 1)
        if len(parts) != 2 or not parts[1]:
            raise WorkspaceError("Backup has no target file")
        target = self.resolve(parts[1])
        if target.exists():
            self._backup(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if target.suffix.lower() in (".json", ".json5", ".jsonc"):
            try:
                json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise WorkspaceError(f"Restored file is not valid JSON: {exc}") from None
        self._invalidate()
        return {"restored": parts[1], "bytes": target.stat().st_size}

    def _schema_text(self) -> str:
        """Read the server's own schema without importing server dependencies."""
        import ast
        root = self._require()
        parts = []
        for relative in ("src/dittobuffer/core/database.py",
                         "src/dittobuffer/gameplay/mega.py"):
            try:
                source = (root / relative).read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if not any(isinstance(target, ast.Name) and target.id == "SCHEMA"
                           for target in node.targets):
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, str) and value.strip():
                    parts.append(value)
        return "\n".join(parts)

    def ensure_database(self) -> dict:
        """Create the active profile's SQLite database on first launch.

        The release server intentionally ships without player data. The platform
        creates the configured database (and the server's own schema) so the
        Database and Players pages are useful before the game has been started.
        """
        summary = self._db_summary()
        if summary.get("exists"):
            return {**summary, "created": False}
        if not self.available:
            return {**summary, "created": False}
        try:
            relative = self._active_database_path()
        except (WorkspaceError, NotFound):
            relative = ""
        if not relative:
            return {**summary, "created": False}
        try:
            path = self.resolve(relative)
        except WorkspaceError:
            return {**summary, "created": False}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".platform-new")
        for old in (temporary, Path(str(temporary) + "-journal")):
            try:
                old.unlink()
            except OSError:
                pass
        try:
            schema = self._schema_text()
            conn = sqlite3.connect(temporary)
            try:
                if schema:
                    conn.executescript(schema)
                conn.commit()
            finally:
                conn.close()
            os.replace(temporary, path)
        except (OSError, sqlite3.Error):
            try:
                temporary.unlink()
            except OSError:
                pass
            return {**summary, "created": False}
        self._invalidate()
        value = self._db_summary()
        value["created"] = True
        return value

    def map_data(self) -> dict:
        """Return the live map overlay from the active server database."""
        value = {"database": "", "center": None, "players": [], "forts": [],
                 "spawns": [], "max_stations": []}
        if not self.available:
            return value
        try:
            relative = self._active_database_path()
        except (WorkspaceError, NotFound):
            return value
        value["database"] = relative
        if not relative:
            return value
        try:
            path = self.resolve(relative)
        except WorkspaceError:
            return value
        if not path.is_file():
            self.ensure_database()
        if not path.is_file():
            return value
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                names = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "admin_locations" in names:
                    query = ("SELECT l.player_id, l.lat, l.lng, "
                             + ("p.name " if "players" in names else "NULL AS name ")
                             + ("FROM admin_locations l LEFT JOIN players p ON p.id=l.player_id "
                                if "players" in names else "FROM admin_locations l ")
                             + "ORDER BY l.player_id LIMIT 100")
                    value["players"] = [dict(row) for row in conn.execute(query)]
                if "forts" in names:
                    value["forts"] = [dict(row) for row in conn.execute(
                        "SELECT id, kind, name, lat, lng FROM forts LIMIT 500")]
                if "spawns" in names:
                    value["spawns"] = [dict(row) for row in conn.execute(
                        "SELECT id, species, lat, lng, expires_ms FROM spawns "
                        "WHERE expires_ms > ? ORDER BY expires_ms LIMIT 500",
                        (int(time.time() * 1000),))]
                if "max_stations" in names:
                    value["max_stations"] = [dict(row) for row in conn.execute(
                        "SELECT id, name, lat, lng, end_ms FROM max_stations "
                        "WHERE end_ms > ? ORDER BY end_ms LIMIT 200",
                        (int(time.time() * 1000),))]
        except sqlite3.Error:
            return value
        for rows in (value["players"], value["forts"], value["spawns"], value["max_stations"]):
            for row in rows:
                try:
                    lat, lng = float(row.get("lat")), float(row.get("lng"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(lat) and math.isfinite(lng):
                    value["center"] = {"lat": round(lat, 6), "lng": round(lng, 6)}
                    break
            if value["center"]:
                break
        return value

    # ------------------------------------------------------------- databases
    def databases(self) -> dict:
        root = self.root
        if not root or not root.is_dir():
            return {"databases": [], "root": self.display_root}
        rows = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs
                       if name not in _EXCLUDED_DIRS and not name.startswith(".")
                       and not (Path(base) / name).is_symlink()]
            for name in files:
                suffix = Path(name).suffix.lower()
                if suffix not in (".sqlite3", ".sqlite", ".db"):
                    continue
                path = Path(base) / name
                try:
                    size = path.stat().st_size
                    relative = self.relative(path)
                    table_count = None
                    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as conn:
                        table_count = conn.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view')"
                        ).fetchone()[0]
                    rows.append({
                        "path": relative,
                        "name": name,
                        "bytes": size,
                        "tables": table_count,
                        "modified_ms": int(path.stat().st_mtime * 1000),
                        "profile": relative == str(self._active_database_path() or ""),
                    })
                except (OSError, sqlite3.Error):
                    rows.append({"path": self.relative(path), "name": name,
                                 "bytes": path.stat().st_size if path.exists() else 0,
                                 "tables": None, "modified_ms": 0, "profile": False})
        rows.sort(key=lambda row: (not row["profile"], row["path"]))
        return {"databases": rows, "root": self.display_root}

    def _active_database_path(self) -> str:
        try:
            profile = self.profile()
            database = profile["data"].get("database")
            return str(database) if database else ""
        except (WorkspaceError, NotFound):
            return ""

    def _db_path(self, relative: str) -> Path:
        path = self.resolve(relative)
        if path.suffix.lower() not in (".sqlite3", ".sqlite", ".db"):
            raise WorkspaceError("Only SQLite database files can be edited")
        if not path.is_file():
            raise NotFound("Database not found: " + str(relative))
        return path

    def _connect(self, path: Path, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        else:
            conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _table_check(self, conn: sqlite3.Connection, table: str) -> dict:
        row = conn.execute(
            "SELECT type, sql FROM sqlite_master WHERE name=? AND type IN ('table','view')",
            (table,)).fetchone()
        if row is None:
            raise NotFound("Table not found: " + str(table))
        return dict(row)

    def tables(self, database: str) -> dict:
        path = self._db_path(database)
        with self._connect(path, readonly=True) as conn:
            rows = []
            for row in conn.execute(
                    "SELECT name, type, sql FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY type, name"):
                name = row["name"]
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
                except sqlite3.Error:
                    count = None
                columns = conn.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
                rows.append({
                    "name": name,
                    "type": row["type"],
                    "rows": count,
                    "columns": len(columns),
                    "system": name.startswith("sqlite_"),
                })
        return {"database": self.relative(path), "tables": rows}

    def schema(self, database: str, table: str) -> dict:
        path = self._db_path(database)
        with self._connect(path, readonly=True) as conn:
            info = self._table_check(conn, table)
            columns = []
            for row in conn.execute(f"PRAGMA table_info({_quote(table)})"):
                columns.append({key: row[key] for key in
                                ("cid", "name", "type", "notnull", "dflt_value", "pk")})
            indexes = []
            for index in conn.execute(f"PRAGMA index_list({_quote(table)})"):
                try:
                    fields = [item["name"] for item in
                              conn.execute(f"PRAGMA index_info({_quote(index['name'])})")]
                except sqlite3.Error:
                    fields = []
                indexes.append({"name": index["name"], "unique": bool(index["unique"]),
                                "origin": index["origin"], "columns": fields})
            foreign = []
            for row in conn.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
                foreign.append({key: row[key] for key in
                                ("id", "seq", "table", "from", "to", "on_update", "on_delete")})
            rowid = True
            try:
                conn.execute(f"SELECT rowid FROM {_quote(table)} LIMIT 1").fetchone()
            except sqlite3.Error:
                rowid = False
            primary = [column["name"] for column in columns if column["pk"]]
            return {
                "database": self.relative(path), "table": table, "type": info["type"],
                "sql": info["sql"], "columns": columns, "indexes": indexes,
                "foreign_keys": foreign, "rowid": rowid, "primary_key": primary,
            }

    def _key_descriptor(self, conn: sqlite3.Connection, table: str) -> dict:
        # Tables with an implicit rowid can always be addressed safely;
        # WITHOUT ROWID tables fall back to their declared primary key.
        try:
            conn.execute(f"SELECT rowid FROM {_quote(table)} LIMIT 1").fetchone()
            return {"mode": "rowid", "columns": ["__rowid"]}
        except sqlite3.Error:
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")
                       if row["pk"]]
            if not columns:
                raise WorkspaceError(
                    "This table has no rowid or primary key; edit it through the SQL console")
            return {"mode": "pk", "columns": columns}

    def rows(self, database: str, table: str, limit: int = 100, offset: int = 0,
             query: str = "") -> dict:
        path = self._db_path(database)
        try:
            limit = max(1, min(MAX_DB_ROWS, int(limit)))
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            raise WorkspaceError("limit and offset must be integers") from None
        with self._connect(path, readonly=True) as conn:
            self._table_check(conn, table)
            key = self._key_descriptor(conn, table)
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
            where = ""
            params: list = []
            needle = str(query or "").strip()
            if needle:
                where = " WHERE " + " OR ".join(
                    f"CAST({_quote(column)} AS TEXT) LIKE ?" for column in columns)
                params = [f"%{needle}%"] * len(columns)
            total = conn.execute(
                f"SELECT COUNT(*) FROM {_quote(table)}{where}", params).fetchone()[0]
            select_prefix = 'rowid AS "__rowid", ' if key["mode"] == "rowid" else ""
            order = ('__rowid' if key["mode"] == "rowid" else ", ".join(key["columns"]))
            try:
                cursor = conn.execute(
                    f"SELECT {select_prefix}* FROM {_quote(table)}{where} "
                    f"ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset])
                result_columns = [item[0] for item in cursor.description]
                data = [{column: _cell(row[column]) for column in result_columns}
                        for row in cursor.fetchall()]
            except sqlite3.Error:
                cursor = conn.execute(
                    f"SELECT * FROM {_quote(table)}{where} LIMIT ? OFFSET ?",
                    params + [limit, offset])
                result_columns = [item[0] for item in cursor.description]
                data = [{column: _cell(row[column]) for column in result_columns}
                        for row in cursor.fetchall()]
                key = {"mode": "pk", "columns": []}
            return {
                "database": self.relative(path), "table": table, "columns": result_columns,
                "rows": data, "total": total, "limit": limit, "offset": offset,
                "key": key,
            }

    def _write_values(self, conn: sqlite3.Connection, table: str, values: dict) -> dict:
        if not isinstance(values, dict):
            raise WorkspaceError("values must be an object")
        columns = {row["name"]: row["type"] for row in
                   conn.execute(f"PRAGMA table_info({_quote(table)})")}
        if not columns:
            raise WorkspaceError("Unknown table")
        unknown = [name for name in values if name not in columns]
        if unknown:
            raise WorkspaceError("Unknown column(s): " + ", ".join(unknown))
        return {name: _decode_cell(value, columns[name]) for name, value in values.items()}

    def _where_key(self, conn: sqlite3.Connection, table: str, key: dict) -> tuple[str, list]:
        if not isinstance(key, dict) or not key:
            raise WorkspaceError("A row key is required")
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")}
        parts = []
        params = []
        for name, value in key.items():
            if name == "__rowid":
                parts.append("rowid = ?")
            elif name in columns:
                parts.append(f"{_quote(name)} = ?")
            else:
                raise WorkspaceError("Unknown key column: " + str(name))
            params.append(_decode_cell(value))
        return " AND ".join(parts), params

    def insert_row(self, database: str, table: str, values: dict) -> dict:
        path = self._db_path(database)
        self._backup(path)
        with self._connect(path) as conn:
            self._table_check(conn, table)
            clean = self._write_values(conn, table, values)
            if not clean:
                raise WorkspaceError("No values were supplied")
            names = ", ".join(_quote(name) for name in clean)
            marks = ", ".join("?" for _ in clean)
            try:
                cursor = conn.execute(
                    f"INSERT INTO {_quote(table)} ({names}) VALUES ({marks})",
                    list(clean.values()))
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                raise WorkspaceError(f"Insert failed: {exc}") from None
            return {"inserted": True, "rowid": cursor.lastrowid,
                    "rowcount": cursor.rowcount, "database": self.relative(path),
                    "table": table}

    def update_row(self, database: str, table: str, key: dict, values: dict) -> dict:
        path = self._db_path(database)
        with self._connect(path) as conn:
            self._table_check(conn, table)
            clean = self._write_values(conn, table, values)
            if not clean:
                raise WorkspaceError("No values were supplied")
            where, params = self._where_key(conn, table, key)
            names = ", ".join(f"{_quote(name)} = ?" for name in clean)
            count = conn.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE {where}", params).fetchone()[0]
            if count != 1:
                raise WorkspaceError(f"Refusing to update {count} rows; select exactly one row")
            self._backup(path)
            try:
                cursor = conn.execute(
                    f"UPDATE {_quote(table)} SET {names} WHERE {where}",
                    list(clean.values()) + params)
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                raise WorkspaceError(f"Update failed: {exc}") from None
            return {"updated": True, "rowcount": cursor.rowcount,
                    "database": self.relative(path), "table": table}

    def delete_row(self, database: str, table: str, key: dict) -> dict:
        path = self._db_path(database)
        with self._connect(path) as conn:
            self._table_check(conn, table)
            where, params = self._where_key(conn, table, key)
            count = conn.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE {where}", params).fetchone()[0]
            if count != 1:
                raise WorkspaceError(f"Refusing to delete {count} rows; select exactly one row")
            self._backup(path)
            try:
                cursor = conn.execute(
                    f"DELETE FROM {_quote(table)} WHERE {where}", params)
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                raise WorkspaceError(f"Delete failed: {exc}") from None
            return {"deleted": True, "rowcount": cursor.rowcount,
                    "database": self.relative(path), "table": table}

    def sql(self, database: str, statement: str, write: bool = False) -> dict:
        path = self._db_path(database)
        statement = str(statement or "").strip()
        if not statement:
            raise WorkspaceError("Enter a SQL statement")
        if write:
            self._backup(path)
        started = time.time()
        with self._connect(path, readonly=not write) as conn:
            deadline = started + 8.0
            conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20_000)
            try:
                cursor = conn.execute(statement)
                if cursor.description:
                    columns = [item[0] for item in cursor.description]
                    rows = [{column: _cell(row[column]) for column in columns}
                            for row in cursor.fetchmany(MAX_DB_ROWS)]
                else:
                    columns, rows = [], []
                if write:
                    conn.commit()
            except sqlite3.Error as exc:
                if write:
                    conn.rollback()
                raise WorkspaceError(f"SQL failed: {exc}") from None
        return {
            "database": self.relative(path), "columns": columns, "rows": rows,
            "rowcount": cursor.rowcount, "lastrowid": cursor.lastrowid,
            "write": bool(write), "elapsed_ms": int((time.time() - started) * 1000),
            "truncated": bool(cursor.description and len(rows) == MAX_DB_ROWS),
        }

    # ------------------------------------------------------------ game master
    def game_master(self) -> dict:
        root = self._require()
        groups = []
        for folder in ("assets/game-master", "data/static"):
            base = root / folder
            if not base.is_dir():
                groups.append({"folder": folder, "present": False, "files": []})
                continue
            files = []
            manifest = {}
            manifest_path = base / "manifest.json"
            if manifest_path.is_file():
                try:
                    value = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest = value if isinstance(value, dict) else {}
                except (OSError, ValueError):
                    manifest = {}
            for name in ("gmts.binpb.gz", "settings.binpb.gz", "manifest.json"):
                path = base / name
                if not path.is_file():
                    files.append({"name": name, "present": False})
                    continue
                raw = path.read_bytes()
                actual = hashlib.sha256(raw).hexdigest()
                expected = (manifest.get(name) or {}).get("sha256") if name != "manifest.json" else ""
                files.append({
                    "name": name, "present": True, "bytes": len(raw),
                    "sha256": actual, "expected_sha256": expected or "",
                    "verified": (actual == expected) if expected else None,
                    "modified_ms": int(path.stat().st_mtime * 1000),
                })
            groups.append({"folder": folder, "present": True, "files": files})
        sources = []
        for relative in ("data/static/max-reference.json", "data/static/pvp-reference.json",
                         "assets/game-master/manifest.json", "data/static/manifest.json"):
            path = root / relative
            if path.is_file():
                sources.append({"path": relative, "bytes": path.stat().st_size})
        return {
            "groups": groups,
            "sources": sources,
            "gmts_present": any(
                item["name"] == "gmts.binpb.gz" and item["present"]
                for group in groups for item in group["files"]),
            "apk_present": (root / "apk").is_dir() and any(
                (root / "apk").glob("*.apk")),
        }

    # ------------------------------------------------------------------ logs
    def logs(self) -> dict:
        root = self.root
        folder = root / "logs" if root else None
        rows = []
        if folder and folder.is_dir():
            for path in sorted(folder.glob("*.log")):
                try:
                    rows.append({"name": path.name, "bytes": path.stat().st_size,
                                 "modified_ms": int(path.stat().st_mtime * 1000)})
                except OSError:
                    pass
        return {"logs": rows, "folder": "logs"}

    def tail_log(self, name: str, lines: int = 200) -> dict:
        root = self._require()
        safe_name = Path(str(name or "")).name
        if safe_name != name or not safe_name.endswith(".log"):
            raise WorkspaceError("Choose a .log file from the logs folder")
        path = root / "logs" / safe_name
        if not path.is_file():
            raise NotFound("Log file not found")
        try:
            lines = max(1, min(500, int(lines)))
        except (TypeError, ValueError):
            lines = 200
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, 512 * 1024)
            handle.seek(size - block)
            raw = handle.read().decode("utf-8", "replace")
        text = "\n".join(raw.splitlines()[-lines:])
        return {"name": safe_name, "bytes": path.stat().st_size, "text": text,
                "modified_ms": int(path.stat().st_mtime * 1000)}

    def clear_log(self, name: str) -> dict:
        root = self._require()
        safe_name = Path(str(name or "")).name
        if safe_name != name or not safe_name.endswith(".log"):
            raise WorkspaceError("Choose a .log file from the logs folder")
        path = root / "logs" / safe_name
        if not path.is_file():
            raise NotFound("Log file not found")
        self._backup(path)
        path.write_bytes(b"")
        return {"cleared": safe_name}

    # -------------------------------------------------------- server process
    def _port_open(self, host: str, port: int, timeout: float = 0.35) -> bool:
        if not host:
            return False
        address = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
        try:
            with socket.create_connection((address, int(port)), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def _pid(self) -> int | None:
        if not self._pid_path or not self._pid_path.is_file():
            return None
        try:
            pid = int(self._pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if pid <= 0:
            return None
        try:
            os.kill(pid, 0)
            return pid
        except OSError:
            return None

    def server_status(self) -> dict:
        try:
            profile = self.profile()
        except (WorkspaceError, NotFound):
            return {"running": False, "managed": False, "pid": None, "port": None,
                    "url": "", "profile": "", "message": "No usable server profile"}
        data = profile["data"]
        host = str(data.get("host") or "127.0.0.1")
        port = int(data.get("port") or 8445)
        running = self._port_open(host, port)
        scheme = "https" if (data.get("network") or {}).get("mode") == "tls" else "http"
        return {
            "running": running,
            "managed": self._pid() is not None,
            "pid": self._pid(),
            "port": port,
            "host": host,
            "url": f"{scheme}://{host}:{port}",
            "health_url": f"{scheme}://{host}:{port}/health",
            "profile": profile["name"],
            "message": "Listening" if running else "Not listening",
        }

    def start_server(self) -> dict:
        root = self._require()
        status = self.server_status()
        if status.get("running"):
            return {**status, "message": "The server is already listening"}
        profile = self.profile()
        data = profile["data"]
        port = int(data.get("port") or 8445)
        env = os.environ.copy()
        source = str(root / "src")
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "") + os.pathsep + str(root)
        logs = root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / "server-platform.log"
        handle = open(log_path, "ab", buffering=0)
        handle.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} platform start ---\n".encode())
        command = [sys.executable, "-m", "dittobuffer.runtime.http",
                   "--config", profile["path"], "--port", str(port)]
        kwargs = {"cwd": str(root), "env": env, "stdout": handle, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        finally:
            handle.close()
        self._process = process
        if self._pid_path:
            self._pid_path.parent.mkdir(parents=True, exist_ok=True)
            self._pid_path.write_text(str(process.pid), encoding="utf-8")
        time.sleep(1.8)
        code = process.poll()
        if code is not None:
            if self._pid_path:
                self._pid_path.unlink(missing_ok=True)
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                pass
            raise WorkspaceError(
                f"The server exited immediately (code {code}). Log:\n{tail.strip()[-1200:]}")
        return self.server_status()

    def stop_server(self) -> dict:
        pid = self._pid()
        if pid is None:
            return {**self.server_status(), "message": "The server was not started by this manager"}
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.15)
            else:
                try:
                    os.kill(pid, getattr(signal, "SIGKILL", 9))
                except OSError:
                    pass
        if self._pid_path:
            self._pid_path.unlink(missing_ok=True)
        self._process = None
        time.sleep(0.4)
        return {**self.server_status(), "message": "Server stop requested"}

    # --------------------------------------------------------------- platform
    def platform_settings(self) -> dict:
        value = {}
        if self.platform_config_path and self.platform_config_path.is_file():
            try:
                loaded = json.loads(self.platform_config_path.read_text(encoding="utf-8"))
                value = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                value = {}
        show = {
            "name": value.get("name", "Dittobuffer Platform"),
            "host": value.get("host", "127.0.0.1"),
            "port": value.get("port", 8780),
            "public_url": value.get("public_url", ""),
            "admin_token": value.get("admin_token", ""),
            "server_root": value.get("server_root", "../server"),
            "server_profile": value.get("server_profile", ""),
            "mqtt": value.get("mqtt") or {},
            "poi_import": value.get("poi_import") or {},
        }
        return show

    def save_platform_settings(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise WorkspaceError("Settings must be an object")
        if not self.platform_config_path:
            raise WorkspaceError("Platform configuration path is not set")
        try:
            current = json.loads(self.platform_config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        for key in ("name", "public_url", "admin_token", "server_profile"):
            if key in body:
                if not isinstance(body[key], str):
                    raise WorkspaceError(f"{key} must be text")
                current[key] = body[key].strip()
        for key in ("mqtt", "poi_import"):
            if key in body:
                if not isinstance(body[key], dict):
                    raise WorkspaceError(f"{key} must be an object")
                current[key] = body[key]
        if "port" in body and body["port"] != current.get("port"):
            current.setdefault("_notes", {})["port_change_requires_restart"] = True
        self._backup(self.platform_config_path)
        _atomic_write(self.platform_config_path, _json_bytes(current))
        return self.platform_settings()

    # ---------------------------------------------------------------- status
    def _invalidate(self) -> None:
        self._status_cache = {"at": 0, "value": None}

    def _db_summary(self) -> dict:
        try:
            relative = self._active_database_path()
        except (WorkspaceError, NotFound):
            relative = ""
        if not relative:
            return {"path": "", "exists": False, "bytes": 0, "tables": 0,
                    "accounts": 0, "players": 0, "pokemon": 0}
        try:
            path = self.resolve(relative)
        except WorkspaceError:
            return {"path": relative, "exists": False, "bytes": 0, "tables": 0,
                    "accounts": 0, "players": 0, "pokemon": 0}
        value = {"path": relative, "exists": path.is_file(),
                 "bytes": path.stat().st_size if path.is_file() else 0,
                 "tables": 0, "accounts": 0, "players": 0, "pokemon": 0}
        if not path.is_file():
            return value
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as conn:
                names = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                value["tables"] = len(names)
                for key, table in (("accounts", "family_accounts"), ("players", "players"),
                                   ("pokemon", "pokemon")):
                    if table in names:
                        value[key] = conn.execute(
                            f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
        except sqlite3.Error:
            pass
        return value

    def status(self) -> dict:
        with self._lock:
            cached = self._status_cache
            if cached["value"] is not None and time.time() - cached["at"] < 4:
                return cached["value"]
            root = self.root
            value = {
                "root": self.display_root,
                "available": self.available,
                "platform": self.platform_settings(),
                "active_profile": "",
                "profile": None,
                "validation": {"errors": [], "warnings": []},
                "server": self.server_status() if self.available else {},
                "database": {"path": "", "exists": False, "bytes": 0, "tables": 0,
                             "accounts": 0, "players": 0, "pokemon": 0},
                "game_master": {"groups": [], "sources": [], "gmts_present": False,
                                "apk_present": False},
                "assets": {},
                "documents": 0,
                "databases": 0,
                "backups": len(self.backups().get("backups", [])),
                "generated_ms": now_ms(),
            }
            if self.available:
                try:
                    profile = self.profile()
                    value["active_profile"] = profile["name"]
                    value["profile"] = self._profile_summary(self._profile_path(profile["name"]))
                    value["validation"] = profile["validation"]
                except (WorkspaceError, NotFound) as exc:
                    value["validation"] = {"errors": [str(exc)], "warnings": []}
                value["database"] = self._db_summary()
                value["game_master"] = self.game_master()
                value["documents"] = len(self.documents().get("documents", []))
                value["databases"] = len(self.databases().get("databases", []))
                for relative, label in (
                        ("assets/pokemon", "Pokémon artwork"),
                        ("assets/avatar", "Avatar bundles"),
                        ("assets/client-runtime", "Client runtime resources"),
                        ("builds", "Unity asset caches"),
                        ("data/world/power-spots.sqlite3", "Power Spot index")):
                    path = root / relative
                    value["assets"][label] = {
                        "path": relative, "present": path.exists(),
                        "kind": "file" if path.is_file() else "folder" if path.is_dir() else "missing",
                    }
            self._status_cache = {"at": time.time(), "value": value}
            return value
