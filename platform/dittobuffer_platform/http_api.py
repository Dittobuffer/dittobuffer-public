"""JSON HTTP API for the platform. No framework, no template engine."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from . import rooms as room_mod
from .server_admin import NotFound as WorkspaceNotFound, WorkspaceError
from .ui import INDEX, STATIC

log = logging.getLogger("dittobuffer-platform.http")
MAX_BODY = 12_000_000
_TILE_CACHE: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()
_TILE_LOCK = threading.Lock()
_TILE_CACHE_MAX = 512
_TILE_SOURCES = (
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
)


def _fetch_tile(z: int, x: int, y: int) -> bytes | None:
    key = (z, x, y)
    with _TILE_LOCK:
        cached = _TILE_CACHE.get(key)
        if cached is not None:
            _TILE_CACHE.move_to_end(key)
            return cached
    for template in _TILE_SOURCES:
        request = Request(template.format(z=z, x=x, y=y), headers={
            "User-Agent": "Dittobuffer-Platform/1.1 (admin map)",
            "Accept": "image/png,image/*;q=0.8",
        })
        try:
            with urlopen(request, timeout=8) as response:
                payload = response.read()
                content_type = response.headers.get("Content-Type", "")
            if payload.startswith(b"\x89PNG\r\n\x1a\n") and "image" in content_type:
                with _TILE_LOCK:
                    _TILE_CACHE[key] = payload
                    _TILE_CACHE.move_to_end(key)
                    while len(_TILE_CACHE) > _TILE_CACHE_MAX:
                        _TILE_CACHE.popitem(last=False)
                return payload
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            log.warning("Map tile %s/%s/%s failed: %s", z, x, y, error)
    return None
STATIC_TYPES = {
    "app.css": "text/css; charset=utf-8",
    "core.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "pages.js": "application/javascript; charset=utf-8",
    "sw.js": "application/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
}


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Api:
    def __init__(self, platform, bridge=None, admin_token: str = "", public_url: str = "",
                 poi_importer=None, workspace=None, recovery=None, world=None):
        self.platform = platform
        self.bridge = bridge
        self.admin_token = admin_token
        self.public_url = public_url.rstrip("/")
        self.poi_importer = poi_importer
        self.workspace = workspace
        self.recovery = recovery
        self.world = world

    def status(self) -> dict:
        value = self.platform.status()
        value["public_url"] = self.public_url
        value["defaults"] = self.platform.defaults()
        value["hosted"] = self.platform.hosted_domain
        value["rooms_public"] = [self.platform.public_room(room) for room in self.platform.available()
                                 if room.get("settings", {}).get("public", True)]
        return value

    def bootstrap(self, domain: str) -> dict:
        room = self.platform.get(domain)
        if room is None:
            raise KeyError(domain)
        if not room.get("settings", {}).get("public", True):
            raise PermissionError("Room is private")
        room.setdefault("stats", {})["bootstraps"] = room.get("stats", {}).get("bootstraps", 0) + 1
        value = self.platform.bootstrap(domain)
        return value


def handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DittobufferPlatform/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        # ------------------------------------------------------------ helpers
        def send(self, status: int, body: bytes, content_type: str,
                 cache: str = "no-store", extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, status: int, value, extra: dict | None = None):
            return self.send(status, _json_bytes(value), "application/json; charset=utf-8",
                             extra=extra)

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY:
                raise ValueError("Payload too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw)
            except ValueError:
                raise ValueError("Body must be JSON") from None
            if not isinstance(value, dict):
                raise ValueError("Body must be a JSON object")
            return value

        def admin_ok(self) -> bool:
            if not api.admin_token:
                return True
            supplied = self.headers.get("X-Platform-Token", "")
            import secrets
            return secrets.compare_digest(supplied, api.admin_token)

        def _admin_ready(self, require_workspace: bool = True) -> bool:
            if not self.admin_ok():
                self.send_json(401, {"error": "Platform token required"})
                return False
            if require_workspace and api.workspace is None:
                self.send_json(503, {"error": "This optional server tool needs a server workspace"})
                return False
            return True

        def _admin_error(self, error: Exception):
            if isinstance(error, WorkspaceNotFound):
                return self.send_json(404, {"error": str(error)})
            if isinstance(error, PermissionError):
                return self.send_json(403, {"error": str(error)})
            if isinstance(error, (WorkspaceError, ValueError, TypeError)):
                return self.send_json(400, {"error": str(error)})
            log.exception("Unhandled admin request")
            return self.send_json(500, {"error": "Internal admin error"})

        def admin_get(self, parsed, parts):
            query = parse_qs(parsed.query)
            suffix = parts[2:]
            standalone = (suffix in (["status"], ["profiles"], ["backups"], ["platform"],
                                     ["liveops"], ["pois"], ["rooms"], ["world"],
                                     ["spawns"], ["raids"])
                          or (len(suffix) == 2 and suffix[0] == "rooms"))
            if not self._admin_ready(require_workspace=not standalone):
                return None
            try:
                workspace = api.workspace
                if suffix == ["status"]:
                    if workspace is None:
                        return self.send_json(200, {
                            "root": "", "available": False,
                            "platform": api.platform.status(), "active_profile": "",
                            "profile": None, "validation": {"errors": [], "warnings": []},
                            "server": {}, "database": {}, "game_master": {},
                            "assets": {}, "documents": 0, "databases": 0, "backups": 0,
                        })
                    return self.send_json(200, workspace.status())
                if suffix == ["profiles"]:
                    return self.send_json(200, workspace.profiles() if workspace else {
                        "profiles": [], "active": "", "root": ""})
                if suffix == ["profile"]:
                    return self.send_json(200, workspace.profile(query.get("name", [None])[0]))
                if suffix == ["documents"]:
                    return self.send_json(200, workspace.documents(
                        query.get("q", [""])[0], query.get("category", [""])[0]))
                if suffix == ["document"]:
                    return self.send_json(200, workspace.document(
                        query.get("path", [""])[0]))
                if suffix == ["backups"]:
                    return self.send_json(200, workspace.backups() if workspace else {
                        "backups": [], "root": ""})
                if suffix == ["databases"]:
                    return self.send_json(200, workspace.databases())
                if suffix == ["map"]:
                    return self.send_json(200, workspace.map_data())
                if suffix == ["db", "tables"]:
                    return self.send_json(200, workspace.tables(
                        query.get("db", [""])[0]))
                if suffix == ["db", "schema"]:
                    return self.send_json(200, workspace.schema(
                        query.get("db", [""])[0], query.get("table", [""])[0]))
                if suffix == ["db", "rows"]:
                    return self.send_json(200, workspace.rows(
                        query.get("db", [""])[0], query.get("table", [""])[0],
                        query.get("limit", ["100"])[0], query.get("offset", ["0"])[0],
                        query.get("q", [""])[0]))
                if suffix == ["game-master"]:
                    return self.send_json(200, workspace.game_master())
                if suffix == ["logs"]:
                    return self.send_json(200, workspace.logs())
                if suffix == ["log"]:
                    return self.send_json(200, workspace.tail_log(
                        query.get("name", [""])[0], query.get("tail", ["200"])[0]))
                if suffix == ["server"]:
                    return self.send_json(200, workspace.server_status())
                if suffix == ["liveops"]:
                    return self.send_json(200, api.platform.liveops())
                if suffix == ["world"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.status())
                if suffix == ["spawns"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, {"spawns": api.world.spawns(),
                                                "settings": api.world.settings()["spawns"]})
                if suffix == ["raids"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, {"raids": api.world.raids(),
                                                "settings": api.world.settings()["raids"]})
                if suffix == ["pois"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    return self.send_json(200, api.poi_importer.query(
                        limit=query.get("limit", ["5000"])[0], q=query.get("q", [""])[0]))
                if suffix == ["platform"]:
                    return self.send_json(200, workspace.platform_settings() if workspace else {})
                if suffix == ["rooms"]:
                    return self.send_json(200, {
                        "rooms": [api.platform.public_room(room) for room in api.platform.rooms()],
                        "hosted": api.platform.hosted_domain,
                    })
                if len(suffix) == 2 and suffix[0] == "rooms":
                    room = api.platform.get(str(suffix[1]).strip().lower())
                    if room is None:
                        raise WorkspaceNotFound("Unknown room")
                    events = list(reversed(room.get("events", [])))[:20]
                    return self.send_json(200, {
                        "room": api.platform.public_room(room), "events": events,
                        "platform_public": api.platform.public_key,
                        "bootstrap_allowed": room["domain"] == api.platform.hosted_domain,
                    })
                return self.send_json(404, {"error": "Unknown admin endpoint"})
            except Exception as error:
                return self._admin_error(error)

        def admin_post(self, parts, body):
            suffix = parts[2:]
            if suffix == ["recovery"]:
                if not self.admin_ok():
                    return self.send_json(401, {"error": "Platform token required"})
                if api.recovery is None:
                    return self.send_json(501, {"error": "Recovery is not available"})
                try:
                    return self.send_json(200, api.recovery(body))
                except (ValueError, TypeError) as error:
                    return self.send_json(400, {"error": str(error)})
                except Exception:
                    log.exception("Unhandled platform recovery request")
                    return self.send_json(500, {"error": "Platform recovery failed"})
            standalone = suffix in (["platform"], ["liveops"], ["pois"], ["pois", "delete"],
                                    ["pois", "clear"], ["world"], ["world", "tick"],
                                    ["spawns"], ["spawns", "delete"], ["spawns", "clear"],
                                    ["raids"], ["raids", "delete"], ["raids", "clear"])
            if not self._admin_ready(require_workspace=not standalone):
                return None
            try:
                workspace = api.workspace
                if suffix == ["liveops"]:
                    return self.send_json(200, api.platform.save_liveops(body.get("settings", body)))
                if suffix == ["pois"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    return self.send_json(200, api.poi_importer.save_row(body))
                if suffix == ["pois", "delete"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    return self.send_json(200, {"deleted": api.poi_importer.delete_row(
                        str(body.get("id") or ""))})
                if suffix == ["pois", "clear"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    return self.send_json(200, api.poi_importer.clear(bool(body.get("auto_only", True))))
                if suffix == ["world"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.save_settings(body.get("settings", body)))
                if suffix == ["world", "tick"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.tick(force=True))
                if suffix == ["spawns"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.add_spawn(body))
                if suffix == ["spawns", "delete"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, {"deleted": api.world.delete_spawn(str(body.get("id") or ""))})
                if suffix == ["spawns", "clear"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.clear_spawns(bool(body.get("auto_only", True))))
                if suffix == ["raids"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.add_raid(body))
                if suffix == ["raids", "delete"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, {"deleted": api.world.delete_raid(str(body.get("id") or ""))})
                if suffix == ["raids", "clear"]:
                    if api.world is None:
                        return self.send_json(501, {"error": "World simulation is not available"})
                    return self.send_json(200, api.world.clear_raids(bool(body.get("auto_only", True))))
                if suffix == ["profile"]:
                    name = body.get("name")
                    data = body.get("data")
                    if not isinstance(name, str) or not name:
                        raise ValueError("A profile name is required")
                    if not isinstance(data, dict):
                        raise ValueError("Profile data must be a JSON object")
                    return self.send_json(200, workspace.save_profile(name, data))
                if suffix == ["profile", "delete"]:
                    return self.send_json(200, workspace.delete_profile(str(body.get("name") or "")))
                if suffix == ["document"]:
                    path = body.get("path")
                    if not isinstance(path, str) or not path:
                        raise ValueError("A file path is required")
                    if "text" in body:
                        return self.send_json(200, workspace.save_document(
                            path, text=body.get("text")))
                    return self.send_json(200, workspace.save_document(
                        path, value=body.get("value")))
                if suffix == ["restore"]:
                    return self.send_json(200, workspace.restore_backup(str(body.get("backup") or "")))
                if suffix == ["db", "insert"]:
                    return self.send_json(200, workspace.insert_row(
                        str(body.get("database") or ""), str(body.get("table") or ""),
                        body.get("values") or {}))
                if suffix == ["db", "update"]:
                    return self.send_json(200, workspace.update_row(
                        str(body.get("database") or ""), str(body.get("table") or ""),
                        body.get("key") or {}, body.get("values") or {}))
                if suffix == ["db", "delete"]:
                    return self.send_json(200, workspace.delete_row(
                        str(body.get("database") or ""), str(body.get("table") or ""),
                        body.get("key") or {}))
                if suffix == ["db", "sql"]:
                    return self.send_json(200, workspace.sql(
                        str(body.get("database") or ""), str(body.get("statement") or ""),
                        bool(body.get("write", False))))
                if suffix == ["server", "start"]:
                    return self.send_json(200, workspace.start_server())
                if suffix == ["server", "stop"]:
                    return self.send_json(200, workspace.stop_server())
                if suffix == ["server", "restart"]:
                    workspace.stop_server()
                    return self.send_json(200, workspace.start_server())
                if suffix == ["logs", "clear"]:
                    return self.send_json(200, workspace.clear_log(str(body.get("name") or "")))
                if suffix == ["platform"]:
                    if workspace is None:
                        raise WorkspaceError("Platform configuration is not available")
                    return self.send_json(200, workspace.save_platform_settings(body))
                return self.send_json(404, {"error": "Unknown admin endpoint"})
            except Exception as error:
                return self._admin_error(error)

        # --------------------------------------------------------------- verbs
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path)
            parts = [unquote(item) for item in path.path.split("/") if item]
            if not parts:
                return self.send(200, INDEX.encode("utf-8"), "text/html; charset=utf-8",
                                 extra={"Content-Security-Policy":
                                        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                        "script-src 'self'; worker-src 'self'; manifest-src 'self'; "
                                        "img-src 'self' data:; connect-src 'self'; font-src 'self' data:; "
                                        "base-uri 'none'; frame-ancestors 'none'"})
            if len(parts) == 5 and parts[:2] == ["api", "tiles"] and parts[4].endswith(".png"):
                try:
                    z, x, y = int(parts[2]), int(parts[3]), int(parts[4][:-4])
                except ValueError:
                    return self.send_json(400, {"error": "Invalid tile coordinates"})
                count = 1 << z if 0 <= z <= 19 else 0
                if not count or not 0 <= y < count:
                    return self.send_json(404, {"error": "Tile outside map"})
                payload = _fetch_tile(z, x % count, y)
                if payload is None:
                    return self.send_json(502, {"error": "Map tile unavailable"})
                return self.send(200, payload, "image/png", cache="public, max-age=86400")
            if parts == ["favicon.ico"]:
                return self.send(200, _icon(), "image/svg+xml", cache="public, max-age=86400")
            if len(parts) == 2 and parts[0] == "assets":
                content_type = STATIC_TYPES.get(parts[1])
                if content_type is None:
                    return self.send_json(404, {"error": "Unknown asset"})
                try:
                    payload = (STATIC / parts[1]).read_bytes()
                except OSError:
                    return self.send_json(404, {"error": "Asset not found"})
                extra = {"Service-Worker-Allowed": "/"} if parts[1] == "sw.js" else None
                return self.send(200, payload, content_type, cache="no-cache", extra=extra)
            if len(parts) >= 2 and parts[:2] == ["api", "admin"]:
                return self.admin_get(path, parts)
            if parts == ["api", "health"]:
                return self.send_json(200, {"status": "ok", "rooms": api.platform.status()["rooms"],
                                            "mqtt": api.platform.mqtt["connected"]})
            if parts == ["api", "status"]:
                return self.send_json(200, api.status())
            if parts == ["api", "rooms"]:
                return self.send_json(200, {"rooms": [api.platform.public_room(room)
                                                      for room in api.platform.available()],
                                            "hosted": api.platform.hosted_domain})
            if len(parts) == 3 and parts[:2] == ["api", "rooms"]:
                room = api.platform.get(parts[2])
                hosted = api.platform.hosted_domain
                if room is not None and hosted and room["domain"] != hosted:
                    room = None
                if room is None:
                    return self.send_json(404, {"error": "Unknown room"})
                events = list(reversed(room.get("events", [])))[:20]
                return self.send_json(200, {"room": api.platform.public_room(room),
                                            "events": events,
                                            "platform_public": api.platform.public_key})
            if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "bootstrap":
                try:
                    return self.send_json(200, api.bootstrap(parts[2]),
                                          extra={"Cache-Control": "no-store"})
                except KeyError:
                    return self.send_json(404, {"error": "Unknown room"})
                except PermissionError as error:
                    return self.send_json(403, {"error": str(error)})
            if parts == ["api", "defaults"]:
                return self.send_json(200, {"defaults": api.platform.defaults()})
            if parts == ["api", "poi-import"]:
                if api.poi_importer is None:
                    return self.send_json(501, {"error": "POI import is not available"})
                return self.send_json(200, api.poi_importer.status())
            if parts == ["api", "pois"]:
                if api.poi_importer is None:
                    return self.send_json(501, {"error": "POI import is not available"})
                query = parse_qs(path.query)
                try:
                    value = api.poi_importer.query(
                        query.get("lat", [None])[0], query.get("lng", [None])[0],
                        query.get("radius", [None])[0], query.get("kind", [None])[0],
                        query.get("q", [""])[0], query.get("limit", ["200"])[0])
                except (TypeError, ValueError) as error:
                    return self.send_json(400, {"error": str(error)})
                return self.send_json(200, value)
            if parts == ["api", "platform"]:
                return self.send_json(200, api.platform.status())
            return self.send_json(404, {"error": "Unknown endpoint"})

        def do_POST(self):
            path = urlparse(self.path)
            parts = [unquote(item) for item in path.path.split("/") if item]
            try:
                body = self.body()
                if len(parts) >= 2 and parts[:2] == ["api", "admin"]:
                    return self.admin_post(parts, body)
                if parts == ["api", "rooms"]:
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    created = api.platform.create(body)
                    return self.send_json(201, created)
                if parts == ["api", "defaults"]:
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(200, {"defaults": api.platform.save_defaults(body)})
                if parts == ["api", "poi-import"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(200, api.poi_importer.save(body))
                if parts == ["api", "poi-import", "scan"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(202, api.poi_importer.scan_now())
                if parts == ["api", "poi-import", "clear"]:
                    if api.poi_importer is None:
                        return self.send_json(501, {"error": "POI import is not available"})
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(200, api.poi_importer.clear())
                if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "host":
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(200, {"room": api.platform.host(parts[2])})
                if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "unhost":
                    if not self.admin_ok():
                        return self.send_json(401, {"error": "Platform token required"})
                    return self.send_json(200, api.platform.unhost(parts[2]))
                if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "state":
                    envelope = body.get("envelope", body)
                    known = api.platform.publish_state(parts[2], envelope)
                    if api.bridge:
                        api.bridge.publish(parts[2], "state", envelope)
                    return self.send_json(200, {"room": known})
                if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "settings":
                    envelope = body.get("envelope", body)
                    known = api.platform.publish_settings(parts[2], envelope)
                    if api.bridge:
                        api.bridge.publish(parts[2], "settings", envelope)
                    return self.send_json(200, {"room": known})
                if len(parts) == 4 and parts[:2] == ["api", "rooms"] and parts[3] == "presence":
                    domain = parts[2]
                    room = api.platform.get(domain)
                    if room is None:
                        return self.send_json(404, {"error": "Unknown room"})
                    if not self.admin_ok():
                        from . import rsa_min
                        try:
                            rsa_min.open_envelope(body, room["host_public"], domain=domain)
                        except ValueError:
                            return self.send_json(401, {"error": "Signed room payload required"})
                    api.platform.touch_presence(domain, body.get("client_id") or self.client_address[0],
                                                body.get("version", 0))
                    return self.send_json(200, {"ok": True})
                return self.send_json(404, {"error": "Unknown endpoint"})
            except KeyError:
                return self.send_json(404, {"error": "Unknown room"})
            except PermissionError as error:
                return self.send_json(403, {"error": str(error)})
            except (ValueError, TypeError) as error:
                return self.send_json(400, {"error": str(error)})
            except Exception:
                log.exception("Unhandled platform request")
                return self.send_json(500, {"error": "Internal platform error"})

        def do_DELETE(self):
            path = urlparse(self.path)
            parts = [unquote(item) for item in path.path.split("/") if item]
            if len(parts) == 3 and parts[:2] == ["api", "rooms"]:
                try:
                    token = self.headers.get("X-Room-Token", "")
                    if not token and not self.admin_ok():
                        return self.send_json(401, {"error": "Manage token required"})
                    removed = api.platform.delete(parts[2], token)
                    return self.send_json(200 if removed else 404, {"deleted": removed})
                except PermissionError as error:
                    return self.send_json(403, {"error": str(error)})
            return self.send_json(404, {"error": "Unknown endpoint"})

    return Handler


def _icon() -> bytes:
    return (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            b'<rect width="64" height="64" rx="16" fill="#6750A4"/>'
            b'<circle cx="32" cy="32" r="16" fill="none" stroke="#fff" stroke-width="5"/>'
            b'<path d="M16 32h32" stroke="#fff" stroke-width="5"/>'
            b'<circle cx="32" cy="32" r="5" fill="#fff"/></svg>')


def serve(platform, host: str = "127.0.0.1", port: int = 8780, bridge=None,
          admin_token: str = "", public_url: str = "", poi_importer=None, workspace=None,
          recovery=None, world=None):
    api = Api(platform, bridge=bridge, admin_token=admin_token, public_url=public_url,
              poi_importer=poi_importer, workspace=workspace, recovery=recovery, world=world)
    server = ThreadingHTTPServer((host, port), handler(api))
    server.daemon_threads = True
    return server
