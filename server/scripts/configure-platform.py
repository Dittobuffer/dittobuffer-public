#!/usr/bin/env python3
"""One-command configuration for Dittobuffer Platform.

Host a room on this server (a room is only a name; no DNS is involved):

    python scripts/configure-platform.py room --room "Family Test" --publish --verify
    python scripts/configure-platform.py room --room "Family Test" --url https://rooms.example.com --token TOKEN

Prepare the self-hosted registry (writes platform/config.json with a generated
admin token and a public URL):

    python scripts/configure-platform.py registry --public-url https://rooms.example.com
    python scripts/configure-platform.py registry --run

Nothing else needs to be edited by hand. The host key is created on first use
and players pin it automatically the first time they join the room.
"""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path} is not a JSON object")
    return value


def _lan_url(port: int) -> str:
    """Best-effort address another device on the same LAN can open."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            host = probe.getsockname()[0]
    except OSError:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def configure_room(args) -> int:
    from dittobuffer.platform import client as platform_client
    from dittobuffer.platform import rsa_min

    path = Path(args.config)
    if not path.is_file():
        raise SystemExit(f"missing config: {path}")
    config = _load(path)
    value = {
        "mode": "host",
        "enabled": True,
        "room": args.room,
        "title": args.title or args.room,
        "description": args.description,
    }
    if args.url:
        value["url"] = args.url
    if args.token:
        value["manage_token"] = args.token
    current = platform_client.save_settings(config, value, path)
    room = current["room"]
    fingerprint = rsa_min.fingerprint(platform_client.public_key())
    print(f"room          {room}")
    print(f"title         {current['title']}")
    print(f"host key      {fingerprint}")
    print(f"config        {path}")

    if not args.no_publish:
        try:
            result = platform_client.publish_now(config)
            print(f"published     broker={result['broker']} room={result['room']}")
        except Exception as error:  # network or broker refusal
            print(f"publish failed: {error}", file=sys.stderr)
            return 1

    if not args.no_verify:
        try:
            document = platform_client.discover(room, timeout=args.timeout)
            platform_client.verify_room(document, room)
            print(f"verified      title={document.get('title')} published_ms={document.get('issued_ms')}")
        except Exception as error:
            print(f"verify failed: {error}", file=sys.stderr)
            return 1

    print()
    print("Join from Dittobuffer: Settings -> Dittobuffer Platform -> enter the room name:")
    print(f"  {room}")
    print("A room is a name, not a domain: no DNS record or website is required.")
    return 0


def configure_registry(args) -> int:
    path = Path(args.config)
    config = _load(path) if path.is_file() else {}
    defaults = {
        "name": "Dittobuffer Platform",
        "host": "0.0.0.0",
        "port": 8780,
        "data": "data/platform.json",
        "key": "data/platform-key.json",
        "admin_token": "",
        "public_url": "",
        "mqtt": {"enabled": True, "host": "broker.hivemq.com", "port": 8883, "tls": True},
    }
    for key, value in defaults.items():
        config.setdefault(key, value)
    if args.host:
        config["host"] = args.host
    if args.port:
        config["port"] = args.port
    token = args.token or config.get("admin_token") or secrets.token_urlsafe(24)
    config["admin_token"] = token
    config["public_url"] = args.public_url or config.get("public_url") or _lan_url(int(config["port"]))
    # A prepared config is already set up: a later double-click must not prompt.
    config["setup_complete"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf8")
    print(f"config        {path}")
    print(f"registry      {config['public_url']}")
    print(f"admin token   {token}")
    print("mqtt          " + ("on" if config["mqtt"].get("enabled", True) else "off"))
    print()
    print("Start it with:")
    print(f"  python {ROOT / 'platform' / 'run.py'} --config {path}")
    if args.run:
        import os

        os.execv(sys.executable, [sys.executable, str(ROOT / "platform" / "run.py"),
                                  "--config", str(path)])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Dittobuffer Platform")
    sub = parser.add_subparsers(dest="command", required=True)

    room = sub.add_parser("room", help="host this server as a named room")
    room.add_argument("--room", required=True, help="room name shown to players")
    room.add_argument("--title", help="display name (defaults to the room name)")
    room.add_argument("--description", default="", help="optional room description")
    room.add_argument("--url", help="optional platform registry URL")
    room.add_argument("--token", help="platform registry admin token")
    room.add_argument("--config", default=str(ROOT / "config" / "family.json"))
    room.add_argument("--timeout", type=float, default=25.0)
    room.add_argument("--no-publish", action="store_true")
    room.add_argument("--no-verify", action="store_true")
    room.set_defaults(func=configure_room)

    registry = sub.add_parser("registry", help="prepare the self-hosted registry")
    registry.add_argument("--config", default=str(ROOT / "platform" / "config.json"))
    registry.add_argument("--host")
    registry.add_argument("--port", type=int)
    registry.add_argument("--public-url")
    registry.add_argument("--token")
    registry.add_argument("--run", action="store_true", help="start it after writing the config")
    registry.set_defaults(func=configure_registry)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
