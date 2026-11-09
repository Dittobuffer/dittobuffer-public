"""Interactive Dittobuffer console with slash commands."""

from __future__ import annotations

import argparse
import json
import re
import os
import shlex
import sys
import time
import webbrowser
from pathlib import Path

from dittobuffer.cli import ascii as art
from dittobuffer.cli.services import DEFAULT_CONFIG, ServiceManager, doctor, http_json, port_open
from dittobuffer.paths import ROOT

VERSION = "1.0.0"

COMMANDS = [
    ("/help", "list every command"),
    ("/status", "server, admin, relay and device state"),
    ("/start [all|server|admin|relay]", "start background services"),
    ("/stop [all|server|admin|relay]", "stop background services"),
    ("/restart [all|server|admin|relay]", "restart one service or everything"),
    ("/reconnect", "rebuild ADB tunnels, relay and relaunch the app"),
    ("/devices", "list attached Android devices"),
    ("/doctor", "check the local toolchain and assets"),
    ("/logs <service> [lines]", "tail server, admin or gnirehtet logs"),
    ("/players", "who is online, from the admin API"),
    ("/spawns", "active spawns near the player"),
    ("/raids", "active and upcoming raids"),
    ("/pois", "admin gyms and Pokestops"),
    ("/open", "open the admin panel in a browser"),
    ("/patch <apk>", "inject Frida Gadget plus the runtime"),
    ("/config", "resolved configuration and endpoints"),
    ("/clear", "clear the screen"),
    ("/quit", "stop the console"),
]

ALIASES = {"relay": "gnirehtet", "tunnel": "gnirehtet", "game": "server", "panel": "admin", "all": "all"}


def colour(text: str, code: str) -> str:
    if os.environ.get("NO_COLOR") or not getattr(sys.stdout, "isatty", lambda: False)():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def dim(text: str) -> str:
    return colour(text, "38;5;245")


def ok(text: str) -> str:
    return colour(text, "38;5;42")


def warn(text: str) -> str:
    return colour(text, "38;5;214")


def bad(text: str) -> str:
    return colour(text, "38;5;203")


class Console:
    def __init__(self, manager: ServiceManager, config: Path):
        self.manager = manager
        self.config = config

    # -- helpers ---------------------------------------------------------
    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        widths = [max(len(str(h)), *(len(str(cell)) for cell in column)) if column else len(str(h))
                  for h, column in zip(headers, zip(*rows) if rows else [[] for _ in headers])]
        print("  " + "  ".join(dim(str(h).ljust(width)) for h, width in zip(headers, widths)))
        for row in rows:
            print("  " + "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))

    def resolve(self, name: str) -> str:
        return ALIASES.get(name.lower(), name.lower())

    # -- commands --------------------------------------------------------
    def cmd_help(self, _args: list[str]) -> None:
        print()
        print("  " + colour("slash commands", "1"))
        self.table(["command", "what it does"], [[c.ljust(32), d] for c, d in COMMANDS])
        print()
        print("  " + dim("everything also works as `dittobuffer <command>` on the command line"))
        print()

    def cmd_status(self, _args: list[str]) -> None:
        rows = []
        for status in self.manager.status():
            rows.append([
                status["name"],
                ok("\u25cf up") if status["running"] else bad("\u25cb down"),
                str(status["port"] or "-"),
                str(status["pid"] or "-"),
            ])
        devices = self.manager.devices()
        try:
            selected = self.manager.select_device()
            reversed_ports = self.manager.adb('reverse', '--list').stdout.count('tcp:')
        except Exception as error:
            selected, reversed_ports = str(error), 0
        print()
        self.table(["service", "state", "port", "pid"], rows)
        print()
        print("  " + f"device      {ok(selected) if selected in devices else warn(selected)}")
        print("  " + f"tunnels     {reversed_ports} reverse rule(s)")
        health = http_json(f"https://127.0.0.1:{self.manager.port}/health")
        if isinstance(health, dict):
            print("  " + dim(f"game server target={health.get('target')} auth={health.get('authentication')}"))
        print()

    def cmd_start(self, args: list[str]) -> None:
        targets = args or ["all"]
        for target in targets:
            name = self.resolve(target)
            names = list(self.manager.specs()) if name == "all" else [name]
            for item in names:
                started, message = self.manager.start(item)
                print("  " + (ok(message) if started else bad(message)))

    def cmd_stop(self, args: list[str]) -> None:
        targets = args or ["all"]
        for target in targets:
            name = self.resolve(target)
            names = list(self.manager.specs()) if name == "all" else [name]
            for item in names:
                print("  " + self.manager.stop(item)[1])

    def cmd_restart(self, args: list[str]) -> None:
        targets = args or ["all"]
        for target in targets:
            name = self.resolve(target)
            names = list(self.manager.specs()) if name == "all" else [name]
            for item in names:
                for message in self.manager.restart(item):
                    print("  " + message)

    def cmd_reconnect(self, _args: list[str]) -> None:
        print()
        for message in self.manager.reconnect():
            print("  " + message)
        print()

    def cmd_devices(self, _args: list[str]) -> None:
        devices = self.manager.devices()
        print()
        if not devices:
            print("  " + bad("no device attached"))
        for device in devices:
            model = self.manager.adb("shell", "getprop", "ro.product.model", serial=device).stdout.strip()
            release = self.manager.adb("shell", "getprop", "ro.build.version.release", serial=device).stdout.strip()
            print(f"  {ok(device)}  {model}  Android {release}")
        print()

    def cmd_doctor(self, _args: list[str]) -> None:
        print()
        for name, passed, detail in doctor(self.config):
            mark = ok("pass") if passed else warn("warn")
            print(f"  {mark}  {name.ljust(16)} {dim(detail)}")
        print()

    def cmd_logs(self, args: list[str]) -> None:
        name = self.resolve(args[0]) if args else "server"
        lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
        print()
        print(self.manager.tail(name, lines))
        print()

    def cmd_players(self, _args: list[str]) -> None:
        players = self.manager.players()
        print()
        if not players:
            print("  " + dim("admin panel offline or no players"))
            print()
            return
        rows = []
        for player in players:
            if not isinstance(player, dict):
                continue
            rows.append([
                str(player.get("name") or player.get("trainer_name") or player.get("player_id", ""))[:24],
                str(player.get("level", "-")),
                str(player.get("last_seen") or player.get("seen") or "-")[:19],
            ])
        self.table(["trainer", "level", "last seen"], rows)
        print()

    def _api(self, endpoint: str) -> None:
        payload = http_json(f"http://127.0.0.1:{self.manager.admin_port}/api/{endpoint}")
        print()
        if payload in (None, [], {}):
            print("  " + dim(f"no /api/{endpoint} data (admin panel offline?)"))
        else:
            print(json.dumps(payload, indent=2)[:4000])
        print()

    def cmd_spawns(self, _args: list[str]) -> None:
        self._api("spawns")

    def cmd_raids(self, _args: list[str]) -> None:
        self._api("raids")

    def cmd_pois(self, _args: list[str]) -> None:
        self._api("pois")

    def cmd_open(self, _args: list[str]) -> None:
        url = f"http://127.0.0.1:{self.manager.admin_port}"
        webbrowser.open(url)
        print("  " + ok(url))

    def cmd_patch(self, args: list[str]) -> None:
        if not args:
            print("  " + bad("usage: /patch path/to/app.apk [--gadget]"))
            return
        from dittobuffer.patcher import patch

        apk = Path(args[0]).expanduser()
        if not apk.is_absolute():
            apk = (Path.cwd() / apk).resolve()
        if not apk.is_file():
            print("  " + bad(f"no such APK: {apk}"))
            return
        argv = ["gadget", str(apk)]
        print("  " + dim("running: " + " ".join(argv)))
        code = patch.main(argv)
        print("  " + (ok("patcher finished") if code == 0 else bad(f"patcher exited {code}")))

    def cmd_config(self, _args: list[str]) -> None:
        settings = json.loads(self.config.read_text(encoding="utf-8")) if self.config.is_file() else {}
        print()
        print(f"  config      {self.config}")
        print(f"  host        {settings.get('host', '-')}")
        print(f"  game port   {settings.get('port', self.manager.port)}")
        print(f"  admin port  {self.manager.admin_port}")
        print(f"  profile     {settings.get('profile', '-')}")
        print(f"  database    {settings.get('database', '-')}")
        print(f"  root        {ROOT}")
        print()

    def cmd_clear(self, _args: list[str]) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def cmd_quit(self, _args: list[str]) -> bool:
        print("  " + dim("bye \u2014 services keep running in the background"))
        return False

    # -- loop ------------------------------------------------------------
    def dispatch(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return True
        if not line.startswith("/"):
            line = "/" + line
        try:
            parts = shlex.split(line)
        except ValueError as error:
            print("  " + bad(str(error)))
            return True
        name, args = parts[0][1:].lower(), parts[1:]
        handler = getattr(self, f"cmd_{name}", None)
        if handler is None:
            print("  " + bad(f"unknown command /{name}") + dim("  \u2014 try /help"))
            return True
        try:
            result = handler(args)
        except Exception as error:  # keep the console alive on command bugs
            print("  " + bad(f"{type(error).__name__}: {error}"))
            return True
        return False if result is False else True

    def run(self) -> int:
        print(art.banner(VERSION))
        print("  " + dim("type /help for commands, /quit to detach"))
        print()
        while True:
            try:
                line = input(colour("dittobuffer", "38;5;111") + colour(" \u276f ", "38;5;245"))
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not self.dispatch(line):
                return 0


def _utf8_console() -> None:
    """Windows terminals default to a legacy code page; the banner needs UTF-8."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _normalise(command: str) -> str:
    """Undo MSYS path rewriting of ``/status`` on Git Bash for Windows."""

    if len(command) > 2 and command[1] == ":" and command[2] in "/\\":
        command = "/" + Path(command.replace("\\", "/")).name
    return command


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="dittobuffer", description="Dittobuffer private family server console.")
    parser.add_argument("command", nargs="*", help="optional slash command to run instead of the interactive console")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--port", type=int, default=8445)
    parser.add_argument("--admin-port", type=int, default=8080)
    parser.add_argument("--patcher", metavar="APK", help="patch an APK and exit")
    parser.add_argument("--no-autostart", action="store_true", help="do not start the background services")
    parser.add_argument("--version", action="version", version=f"Dittobuffer {VERSION}")
    args = parser.parse_args(argv)

    manager = ServiceManager(Path(args.config), args.port, args.admin_port)
    console = Console(manager, Path(args.config))

    if args.patcher:
        from dittobuffer.patcher import patch

        return patch.main(["gadget", args.patcher])

    if not args.no_autostart:
        for name in ("server", "admin"):
            started, message = manager.start(name)
            if not started:
                print("  " + bad(message))

    if args.command:
        console.dispatch(" ".join([_normalise(args.command[0]), *args.command[1:]]))
        return 0
    return console.run()
