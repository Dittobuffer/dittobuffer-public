"""Android bootstrap: run the real Dittobuffer servers inside the app process.

Called from ServerRuntime.java on a background thread. The pushed tree under
the app's external files directory takes precedence over the APK bootstrap, so
server updates are `adb push` only.
"""

import io
import json
import os
import sys
import threading
import traceback

# The APK ships protobuf's pure-Python runtime (no compiled _upb), so pin the
# backend before anything imports google.protobuf.
os.environ.setdefault("PROTOBUF_PYTHON_IMPLEMENTATION", "python")

# The APK ships no system tz database; point zoneinfo at the bundled copy.
import zoneinfo as _zoneinfo
_tz = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tzdata", "zoneinfo")
if os.path.isdir(_tz):
    _zoneinfo.reset_tzpath([_tz])

GAME_PORT = 8445
ADMIN_PORT = 8080
MAP_PORT = 8081

_state = {"game": None, "admin": None, "root": None, "threads": []}


def _log(root, message):
    try:
        logs = os.path.join(root, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "mobile.log"), "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


def _prepare_config(root):
    """Copy family.json to mobile.json and disable data that is not shipped."""
    source = os.path.join(root, "config", "family.json")
    with open(source, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    def has(path):
        return os.path.exists(os.path.join(root, path))

    pokemon = config.setdefault("pokemon_assets", {})
    manifest = pokemon.get("manifest", "")
    pokemon["enabled"] = bool(pokemon.get("enabled")) and bool(manifest) and has(manifest)
    avatar = config.setdefault("avatar_assets", {})
    avatar_manifest = avatar.get("manifest", "")
    avatar["enabled"] = bool(avatar.get("enabled")) and bool(avatar_manifest) and has(avatar_manifest)
    # The client's own Unity cache is bundled when every registry entry was
    # verified into builds/m151-client-runtime-v3 at packaging time.
    resources = config.setdefault("client_resources", {})
    registry = resources.get("registry", "")
    resources["enabled"] = bool(registry) and has(registry)
    resources["allow_partial"] = True
    # Personal map-derived data (pokestops, power spots, walked world) is never
    # shipped, so switch off the features that would need it instead of failing.
    max_system = config.setdefault("max_system", {})
    candidates = max_system.get("candidates", "")
    if candidates and not has(candidates):
        max_system["enabled"] = False
    world = config.setdefault("world", {})
    world_manifest = world.get("manifest", "")
    if world_manifest and not has(world_manifest):
        world["enabled"] = False
    # The phone payload uses the same loopback ports as the desktop profile.
    config["host"] = "127.0.0.1"
    config["port"] = GAME_PORT
    config.setdefault("world", {})["map_tiles_port"] = MAP_PORT
    config["world"]["map_tiles_base"] = "http://127.0.0.1:%d/map/tiles" % MAP_PORT

    # Write beside the tree, not inside config/: an adb-pushed config may be
    # owned by the shell user, and the generated profile must never depend on
    # that directory being writable by the app.
    target = os.path.join(root, "mobile.json")
    try:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except PermissionError:
        target = os.path.join(root, "config", "mobile.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    return target


def _syspath(root):
    for path in (os.path.join(root, "src"), os.path.join(root, "protocol"), root):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def start(root):
    root = os.path.abspath(str(root))
    if _state["game"] is not None:
        return "ok"
    _state["root"] = root
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)
    os.chdir(root)
    _log(root, "starting root=%s" % root)

    # Pushed source/protocol must win over the copy unpacked from the APK.
    # `protocol` is itself a package, so its parent directory (root) is needed
    # too, in addition to `protocol/` for the bare `game_pb2` imports.
    _syspath(root)

    config_path = _prepare_config(root)

    from dittobuffer.runtime.http import create_server
    _log(root, "creating game server on %d" % GAME_PORT)
    game = create_server(config_path, GAME_PORT)
    game_thread = threading.Thread(target=game.serve_forever, name="dittobuffer-game", daemon=True)
    game_thread.start()
    _state["game"] = game
    _state["threads"].append(game_thread)
    _log(root, "game server listening")

    from http.server import ThreadingHTTPServer
    from dittobuffer.admin.admin import Admin, handler

    _log(root, "creating admin server on %d" % ADMIN_PORT)
    admin = Admin(config_path, ADMIN_PORT, "127.0.0.1")
    admin_server = ThreadingHTTPServer(("127.0.0.1", ADMIN_PORT), handler(admin))
    admin_thread = threading.Thread(target=admin_server.serve_forever, name="dittobuffer-admin", daemon=True)
    admin_thread.start()
    _state["admin"] = admin_server
    _state["admin_context"] = admin
    _state["threads"].append(admin_thread)
    _log(root, "admin server listening")
    return "ok"


def admin_url():
    """Return the operator link only to the in-process Android bridge.

    The capability never appears in status(), HTTP discovery or mobile logs.
    The website consumes the fragment and uses an HttpOnly session afterward.
    """
    base = "http://127.0.0.1:%d/" % ADMIN_PORT
    admin = _state.get("admin_context")
    if admin is None or _state.get("admin") is None:
        return base
    from dittobuffer.admin.access import access
    return base + "#admin=" + access(admin).key


def _write_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
    os.replace(temporary, path)


def room_name(value):
    """Canonical room name for the app UI (a name, never a domain)."""
    root = _state.get("root")
    if root:
        _syspath(os.path.abspath(str(root)))
    from dittobuffer.platform.client import normalise_room
    return normalise_room(value)


def connect_room(root, name):
    """Join a room by name alone: the retained document comes from HiveMQ."""
    root = os.path.abspath(str(root))
    _syspath(root)
    from dittobuffer.platform import client as platform_client
    room = platform_client.normalise_room(name)
    document = platform_client.discover(room)
    platform_client.verify_room(document, room)
    message = apply_platform(root, room, json.dumps(document))
    return 'Joined room "%s" (no domain needed). %s' % (room, message)


def apply_platform(root, room, bootstrap_text):
    """Verify a signed room document, adopt its settings, reset on a new room."""
    root = os.path.abspath(str(root))
    _syspath(root)
    from dittobuffer.platform import client as platform_client
    from dittobuffer.platform import rsa_min

    room = platform_client.normalise_room(room)
    value = json.loads(bootstrap_text)
    platform_client.verify_room(value, room)
    host = value.get("host_public") or {}
    fingerprint = rsa_min.fingerprint(host)
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    pin_path = os.path.join(data_dir, "platform-pin.json")
    try:
        with open(pin_path, "r", encoding="utf-8") as handle:
            pinned = json.load(handle)
    except (OSError, ValueError):
        pinned = {}
    rooms = pinned.get("rooms") if isinstance(pinned.get("rooms"), dict) else {}
    if rooms.get(room) and rooms[room] != fingerprint:
        raise ValueError("This room is now signed by a different key; refusing to trust it")
    rooms[room] = fingerprint
    _write_json(pin_path, {"rooms": rooms})

    # A new room means a new trainer world: clear local player saves, keep assets.
    marker = os.path.join(data_dir, "platform-domain.txt")
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            previous = handle.read().strip()
    except OSError:
        previous = ""
    cleared = False
    mobile_path = os.path.join(root, "mobile.json")
    try:
        with open(mobile_path, "r", encoding="utf-8") as handle:
            mobile = json.load(handle)
    except (OSError, ValueError):
        mobile = {}
    # Joining any room is a new world, including the very first one: the
    # previous local trainer save would otherwise leak progress, placed stops
    # and encountered Pokemon into somebody else's server.
    if previous != room:
        database = mobile.get("database")
        if database:
            target = database if os.path.isabs(database) else os.path.join(root, database)
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(target + suffix)
                except OSError:
                    pass
        # The bundled 0.429 client keeps its own platform selection and avatar
        # cache under the server tree; both belong to the old room.
        for name in ("platform-room.json", "client-cache"):
            victim = os.path.join(data_dir, name)
            try:
                if os.path.isdir(victim):
                    import shutil
                    shutil.rmtree(victim)
                elif os.path.exists(victim):
                    os.remove(victim)
            except OSError:
                pass
        cleared = True
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(room)

    body = (value.get("state") or {}).get("body") or {}
    liveops = body.get("liveops") or (body.get("data") or {}).get("liveops")
    if isinstance(liveops, dict):
        defaults = (value.get("defaults") or {}).get("spawn_percent")
        if defaults and not liveops.get("spawn_percent"):
            liveops = dict(liveops, spawn_percent=defaults)
        _write_json(os.path.join(data_dir, "liveops.json"), liveops)

    # The signed platform room carries the OSM places the platform downloaded.
    # Put them in the local admin overlay so WorldStore turns them into forts
    # even when the phone itself cannot reach Overpass.
    try:
        incoming = body.get("pois")
        if isinstance(incoming, list) and incoming:
            from dittobuffer.world import world_admin
            current = world_admin.load()
            imported = {}
            for raw in incoming:
                if not isinstance(raw, dict):
                    continue
                identity = str(raw.get("id") or "").strip()
                name = str(raw.get("name") or "").strip()
                try:
                    kind = int(raw.get("kind"))
                    lat = float(raw.get("lat"))
                    lng = float(raw.get("lng"))
                except (TypeError, ValueError):
                    continue
                if not identity.startswith("admin:") or not name:
                    continue
                if kind not in (0, 1) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    continue
                imported[identity] = {
                    "id": identity, "name": name[:80], "kind": kind,
                    "lat": lat, "lng": lng, "status": "APPROVED", "score": 100,
                    "category": "gym" if kind == 0 else "stop",
                    "reason": "Platform OSM import", "tags": "{}",
                    "photo_status": "PHOTO_PENDING", "auto_osm": True,
                }
            # Hand-placed local rows survive a room refresh.
            for row in current:
                if not row.get("auto_osm"):
                    imported.setdefault(str(row.get("id")), row)
            if imported:
                world_admin.save(list(imported.values()))
                _log(root, "platform POIs imported count=%d" % len(imported))
    except Exception as error:
        _log(root, "platform POI import failed: %s" % error)

    mobile["platform"] = {"mode": "player", "enabled": True, "room": room,
                          "domain": room, "title": value.get("title") or room}
    mobile["admin_readonly"] = True
    _write_json(mobile_path, mobile)
    _log(root, "platform room applied room=%s cleared=%s" % (room, cleared))
    return "Verified the room signature" + (" and cleared the previous local save." if cleared
                                            else ".")


def leave_platform(root):
    root = os.path.abspath(str(root))
    mobile_path = os.path.join(root, "mobile.json")
    try:
        with open(mobile_path, "r", encoding="utf-8") as handle:
            mobile = json.load(handle)
    except (OSError, ValueError):
        mobile = {}
    mobile["platform"] = {"mode": "local", "enabled": False, "room": "", "domain": ""}
    mobile["admin_readonly"] = False
    _write_json(mobile_path, mobile)
    try:
        os.remove(os.path.join(root, "data", "platform-domain.txt"))
    except OSError:
        pass
    return "ok"


def stop():
    _state.pop("admin_context", None)
    for key in ("admin", "game"):
        server = _state.get(key)
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
            _state[key] = None
    root = _state.get("root")
    if root:
        _log(root, "servers stopped")
    return "ok"


def status():
    return json.dumps({
        "running": _state["game"] is not None,
        "game": GAME_PORT,
        "admin": ADMIN_PORT,
        "map": MAP_PORT,
    })
