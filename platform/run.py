#!/usr/bin/env python3
"""Run the Dittobuffer Platform.

    python run.py --host 0.0.0.0 --port 8780

Everything is optional: without HiveMQ the HTTP API still serves signed room
snapshots. With HiveMQ the same rooms also update live over MQTT.

The first interactive start asks for one room address. The platform creates and
hosts that room automatically, so a deleted ``config.json`` or ``data/*.json``
does not leave the installation stuck.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dittobuffer_platform import presence  # noqa: E402
from dittobuffer_platform.bridge import Bridge  # noqa: E402
from dittobuffer_platform.http_api import serve  # noqa: E402
from dittobuffer_platform.pois import PoiImporter  # noqa: E402
from dittobuffer_platform.rooms import Platform, normalise_room  # noqa: E402
from dittobuffer_platform.rsa_min import generate  # noqa: E402
from dittobuffer_platform.server_admin import ServerWorkspace  # noqa: E402
from dittobuffer_platform.store import Store  # noqa: E402
from dittobuffer_platform.world import WorldSimulator  # noqa: E402

DEFAULT_ROOM = "my-room"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else HERE / path


def _display_path(path: Path) -> str:
    """A portable path for console output; never print the machine's home path."""
    try:
        resolved = path.resolve()
        try:
            return resolved.relative_to(HERE.parent).as_posix()
        except ValueError:
            return resolved.name
    except OSError:
        return path.name


def _portable_server_root(path: Path) -> str:
    """Store the workspace relative to the platform when possible."""
    try:
        return Path(os.path.relpath(path, HERE)).as_posix()
    except ValueError:
        return str(path)


def discover_server_root(configured: str | None, base: Path = HERE) -> tuple[Path, bool]:
    """Find the server tree in either supported release layout.

    Release layout:      release/platform + release/server
    Source-clone layout: clone/platform + clone/src/dittobuffer

    The configured value is tried first, then the two conventional locations.
    A directory only counts when it contains the actual server package and a
    config folder, so an unrelated ``server`` folder cannot be selected.
    """
    base = Path(base)
    raw = Path(str(configured or "../server"))
    fallback = raw if raw.is_absolute() else (base / raw)
    candidates = [fallback, fallback / "server", base.parent / "server", base.parent,
                  base.parent.parent / "server", Path.cwd() / "server", Path.cwd()]
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "src" / "dittobuffer").is_dir() and (resolved / "config").is_dir():
            return resolved, True
    return fallback.resolve(), False


def load_config(path: Path) -> dict:
    default = {
        "host": "127.0.0.1",
        "port": 8780,
        "data": "data/platform.json",
        "key": "data/platform-key.json",
        "room_address": "",
        "mqtt": {"enabled": True, "host": "broker.hivemq.com", "port": 8883, "tls": True},
        "admin_token": "",
        "public_url": "",
        "name": "Dittobuffer Platform",
        "server_root": "../server",
        "server_profile": "",
        "poi_import": {"enabled": True, "radius_m": 1200, "interval_s": 60,
                       "limit": 80, "gym_percent": 20},
    }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    default.update(value)
    return default


def save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_key(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("n") and value.get("d"):
            return value
    except (OSError, ValueError):
        pass
    key = generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(key, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def setup(config: dict, path: Path) -> dict:
    """Ask for the room address, then write config.json.

    A room is a name players type, not a DNS domain. Keeping the first run to a
    single question is intentional; the public URL and listener can be changed
    later in the platform admin.
    """
    print("Dittobuffer Platform - first run setup")
    print("Press Enter to keep the value in brackets.\n")
    fallback = str(config.get("room_address") or config.get("hosted_room") or DEFAULT_ROOM)
    room = fallback
    while True:
        try:
            typed = input(f"Room address (name players type) [{fallback}]: ").strip()
        except EOFError:
            typed = ""
        try:
            room = normalise_room(typed or fallback)
            break
        except ValueError as error:
            print(f"  {error}")
    config["room_address"] = room
    if not config.get("public_url"):
        config["public_url"] = f"http://localhost:{config['port']}"
    config["setup_complete"] = True
    save_config(path, config)
    print(f"\nSaved {_display_path(path)}")
    print(f"Room address: {room}")
    print("Change it later in the admin under Platform & rooms.\n")
    return config


def ensure_room(platform: Platform, config: dict, path: Path) -> str:
    """Create and host the configured room after any data loss."""
    requested = str(config.get("room_address") or platform.hosted_domain or DEFAULT_ROOM).strip()
    try:
        room = normalise_room(requested)
    except ValueError:
        room = DEFAULT_ROOM
    if platform.get(room) is None:
        try:
            platform.create({"name": room, "title": room, "host": True})
        except ValueError:
            # A concurrent create or an older save already owns the name.
            if platform.get(room) is None:
                raise
            platform.host(room)
    else:
        platform.host(room)
    if config.get("room_address") != room:
        config["room_address"] = room
        save_config(path, config)
    return room


def reset_platform_files(config_path: Path, config: dict) -> tuple[Path, list[str]]:
    """Back up and remove the platform's JSON state.

    Only files owned by the platform are touched. Server profiles, player
    databases and game data are never part of this reset.
    """
    data_path = _resolve(str(config.get("data") or "data/platform.json"))
    key_path = _resolve(str(config.get("key") or "data/platform-key.json"))
    candidates = [
        config_path,
        data_path,
        key_path,
        data_path.parent / "pois.json",
        data_path.parent / "poi-import.json",
        data_path.parent / "world.json",
        data_path.parent / "platform.json",
        data_path.parent / "platform-key.json",
    ]
    unique = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in unique:
            unique.append(resolved)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = data_path.parent / "reset-backups" / stamp
    removed = []
    for path in unique:
        if not path.is_file():
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup / path.name)
        path.unlink()
        removed.append(str(path))
    return backup, removed


def make_recovery(config: dict, config_path: Path, data_path: Path,
                  key_path: Path, platform: Platform, publish=None):
    """Build the admin recovery callback.

    It backs up all state, rotates the platform signing key, clears room
    bindings, then creates and hosts a room with the supplied address. When
    ``publish`` is supplied the new retained room document is sent at once.
    """
    lock = threading.Lock()

    def recovery(body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Recovery settings must be an object")
        with lock:
            requested = body.get("room_address") or config.get("room_address") or platform.hosted_domain
            room = normalise_room(requested)
            public_url = body.get("public_url")
            if public_url is not None and not isinstance(public_url, str):
                raise ValueError("public_url must be text")
            rotate_key = bool(body.get("rotate_key", True))
            clear_rooms = bool(body.get("clear_rooms", True))

            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = data_path.parent / "recovery-backups" / stamp
            for path in (config_path, data_path, key_path):
                if path.is_file():
                    backup.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup / path.name)

            if clear_rooms:
                platform.reset_rooms()
            if rotate_key:
                key = generate()
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_text(json.dumps(key, indent=2), encoding="utf-8")
                try:
                    os.chmod(key_path, 0o600)
                except OSError:
                    pass
                platform.set_signing_key(key)

            config["room_address"] = room
            if public_url is not None:
                config["public_url"] = public_url.strip()
                config["setup_complete"] = True
            save_config(config_path, config)

            if platform.get(room) is None:
                platform.create({"name": room, "title": room, "host": True})
            else:
                platform.host(room)

            if publish is not None:
                try:
                    publish()
                except Exception:
                    logging.exception("Room publication after recovery failed")

            status = platform.status()
            return {
                "room": platform.public_room(platform.get(room)),
                "room_address": room,
                "public_url": config.get("public_url", ""),
                "fingerprint": status["fingerprint"],
                "platform_public": status["platform_public"],
                "rooms": status["rooms"],
                "backup": str(backup) if backup.exists() else "",
            }

    return recovery


def pause_if_double_clicked() -> None:
    """Keep the console open when run.py was started by double-clicking it."""
    if len(sys.argv) == 1 and sys.stdin.isatty():
        try:
            input("Press Enter to close...")
        except EOFError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Dittobuffer Platform")
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data")
    parser.add_argument("--key")
    parser.add_argument("--public-url")
    parser.add_argument("--room-address")
    parser.add_argument("--server-root",
                        help="folder containing the release server/ workspace")
    parser.add_argument("--no-mqtt", action="store_true")
    parser.add_argument("--setup", action="store_true",
                        help="ask for the room address and save it")
    parser.add_argument("--reset", action="store_true",
                        help="back up and remove platform config/data JSON, then set up again")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before --reset")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    if args.reset:
        if sys.stdin.isatty() and not args.yes:
            try:
                answer = input("Reset platform config, signing key and room data? Type RESET: ").strip()
            except EOFError:
                answer = ""
            if answer != "RESET":
                print("Reset cancelled.")
                return 1
        backup, removed = reset_platform_files(config_path, config)
        print(f"Removed {len(removed)} platform file(s). Backup: {_display_path(backup)}")
        config = load_config(config_path)
        config["setup_complete"] = False
        if sys.stdin.isatty():
            config = setup(config, config_path)

    interactive = sys.stdin.isatty()
    if args.setup or (interactive and not config.get("setup_complete")):
        config = setup(config, config_path)
    if args.host:
        config["host"] = args.host
    if args.port:
        config["port"] = args.port
    if args.data:
        config["data"] = args.data
    if args.key:
        config["key"] = args.key
    if args.public_url is not None:
        config["public_url"] = args.public_url
    if args.room_address:
        config["room_address"] = normalise_room(args.room_address)
    if args.server_root:
        config["server_root"] = args.server_root
    if args.no_mqtt:
        config.setdefault("mqtt", {})["enabled"] = False

    data_path = _resolve(config["data"])
    key_path = _resolve(config["key"])
    server_root, server_found = discover_server_root(config.get("server_root"))
    if server_found:
        portable_root = _portable_server_root(server_root)
        if config.get("server_root") != portable_root:
            config["server_root"] = portable_root
            save_config(config_path, config)
    workspace = ServerWorkspace(
        server_root,
        backup_dir=data_path.parent / "admin-backups",
        platform_config_path=config_path,
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        database_state = workspace.ensure_database()
        if database_state.get("created"):
            logging.info("Initialized server database: %s", database_state.get("path"))
    except Exception:
        logging.exception("Could not initialize the server database")
    if not server_found:
        logging.info("No server workspace found; running as a standalone Dittobuffer platform. "
                     "Rooms, HiveMQ and OSM POIs do not require a game server.")

    store = Store(data_path)
    store.data.setdefault("platform", {})["name"] = config["name"]
    platform = Platform(store, load_key(key_path), config)
    room = ensure_room(platform, config, config_path)
    try:
        poi_importer = PoiImporter(data_path.parent, config.get("poi_import") or {})
        poi_importer.start()
    except Exception:
        logging.exception("Automatic OSM POI importing failed to start")
        poi_importer = None
    world = WorldSimulator(data_path.parent, poi_importer, platform.liveops)
    world.start()
    settings = config.get("mqtt") or {}
    bridge = Bridge(platform, host=settings.get("host", "broker.hivemq.com"),
                    port=int(settings.get("port", 8883)), tls=bool(settings.get("tls", True)),
                    enabled=bool(settings.get("enabled", True)),
                    room_document=lambda: presence.room_document(
                        platform, workspace, poi_importer, world))
    recovery = make_recovery(config, config_path, data_path, key_path, platform,
                             publish=bridge.publish_room_now)
    bridge.start()
    server = serve(platform, config["host"], int(config["port"]), bridge=bridge,
                   admin_token=config.get("admin_token", ""), public_url=config.get("public_url", ""),
                   poi_importer=poi_importer, workspace=workspace, recovery=recovery,
                   world=world)
    poi_status = poi_importer.status() if poi_importer else {}
    poi_note = "import on" if poi_status.get("enabled") else "import off"
    print(f"Dittobuffer Platform: http://{config['host']}:{config['port']}  "
          f"room={room}  mqtt={'on' if settings.get('enabled', True) else 'off'}  "
          f"pois={poi_status.get('auto_pois', 0)} ({poi_note})  "
          f"spawns={len(world.spawns())}  raids={len(world.raids())}", flush=True)
    print(f"Platform fingerprint: {platform.status()['fingerprint']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        world.stop()
        if poi_importer:
            poi_importer.stop()
        bridge.stop()
        server.server_close()
    pause_if_double_clicked()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
