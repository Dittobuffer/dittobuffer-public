"""Process and device management for the Dittobuffer console.

The console is a thin supervisor: the authoritative server, the admin panel and
the gnirehtet relay all run as detached OS processes so they survive the
console, and the ADB reverse tunnels are restored on demand.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dittobuffer.paths import CONFIG, LOGS, ROOT

SRC = ROOT / "src"
TOOLS = ROOT / "tools"
RUN = ROOT / "run"

#: Android package of the family build. Kept stable so existing installs keep
#: their saved account after a repository rename.
APP_ID = "org.librenlp.pokemongo.dev"
APP_ACTIVITY = "org.dittobuffer.launcher.FamilyActivity"

DEFAULT_CONFIG = CONFIG / "family.json"


@dataclass
class Service:
    """One supervised background process."""

    name: str
    command: list[str]
    port: int | None = None
    log: Path | None = None
    health: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def pid_file(self) -> Path:
        return RUN / f"{self.name}.pid"


def _python_env() -> dict[str, str]:
    env = dict(os.environ)
    current = env.get("PYTHONPATH", "")
    parts = [str(SRC)] + ([current] if current else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def port_open(port: int | None, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def http_json(url: str, timeout: float = 2.0) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False, creationflags=0x08000000,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_identity(pid: int) -> str | None:
    """Bind a saved PID to this exact process lifetime before allowing a stop."""
    if pid <= 0:
        return None
    try:
        if os.name == 'nt':
            script = (f'$p=Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
                      'if($p){$p.CreationDate.ToUniversalTime().ToString("o");$p.ExecutablePath;$p.CommandLine}')
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                                    capture_output=True, text=True, check=False, timeout=10, creationflags=0x08000000)
            if result.returncode or not result.stdout.strip():
                return None
            raw = result.stdout.strip().encode('utf8')
        elif Path(f'/proc/{pid}/stat').exists():
            stat = Path(f'/proc/{pid}/stat').read_text()
            started = stat[stat.rfind(')')+2:].split()[19]
            raw = started.encode() + b'\0' + Path(f'/proc/{pid}/cmdline').read_bytes()
        else:
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='],
                                    capture_output=True, check=False, timeout=10)
            if result.returncode or not result.stdout.strip():
                return None
            raw = result.stdout.strip()
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


class DeviceSelectionError(ValueError):
    pass


class ServiceManager:
    """Start, stop and inspect the Dittobuffer background services."""

    def __init__(self, config: Path = DEFAULT_CONFIG, port: int = 8445, admin_port: int = 8080, serial: str | None = None):
        self.config = Path(config)
        self.port = port
        self.admin_port = admin_port
        self.map_port = self._map_tiles_port()
        self.serial = serial or os.environ.get('DITTOBUFFER_ADB_SERIAL')
        self._selected_serial = None
        RUN.mkdir(exist_ok=True)
        LOGS.mkdir(exist_ok=True)

    # -- definitions -----------------------------------------------------
    def specs(self) -> dict[str, Service]:
        python = sys.executable
        gnirehtet = TOOLS / "gnirehtet" / ("gnirehtet.exe" if os.name == "nt" else "gnirehtet")
        return {
            "server": Service(
                "server",
                [python, "-m", "dittobuffer.runtime.http", "--config", str(self.config), "--port", str(self.port)],
                port=self.port,
                log=LOGS / "server.log",
                health=f"https://127.0.0.1:{self.port}/health",
            ),
            "admin": Service(
                "admin",
                [python, "-m", "dittobuffer.admin.admin", "--config", str(self.config), "--port", str(self.admin_port)],
                port=self.admin_port,
                log=LOGS / "admin.log",
                health=f"http://127.0.0.1:{self.admin_port}/api/status",
            ),
            "gnirehtet": Service(
                "gnirehtet",
                [str(gnirehtet), "run"],
                port=31416,
                log=LOGS / "gnirehtet.log",
            ),
        }

    def spec(self, name: str) -> Service:
        try:
            return self.specs()[name]
        except KeyError:
            raise KeyError(f"unknown service '{name}'") from None

    def _loopback_server(self) -> bool:
        """Whether this profile is reached through adb reverse, not a relay VPN."""
        try:
            host = str(json.loads(self.config.read_text(encoding='utf-8')).get('host', '')).strip().lower()
        except (OSError, ValueError, TypeError):
            return False
        return host in ('127.0.0.1', 'localhost', '::1')

    def _map_tiles_port(self) -> int | None:
        """Loopback port the game process uses for the plain-HTTP map origin."""
        try:
            world = (json.loads(self.config.read_text(encoding='utf-8')).get('world') or {})
            port = world.get('map_tiles_port', 8081)
            return int(port) if port else None
        except (OSError, ValueError, TypeError):
            return 8081

    # -- lifecycle -------------------------------------------------------
    def start(self, name: str, wait: float = 25.0) -> tuple[bool, str]:
        spec = self.spec(name)
        if name == 'gnirehtet':
            try:
                spec.command.append(self.select_device())
            except DeviceSelectionError as error:
                return False, str(error)
        if self._owned_process(spec):
            return True, f"{name} already running"
        prior_pid = self._read_pid(spec)
        if prior_pid and _pid_alive(prior_pid):
            return False, f"{name} has an unverified live PID record; process and record left untouched"
        if port_open(spec.port):
            return False, f"port {spec.port} is occupied by an unverified process; left untouched"
        if name == "gnirehtet" and not Path(spec.command[0]).exists():
            return False, f"gnirehtet binary missing at {spec.command[0]}"
        spec.log.parent.mkdir(parents=True, exist_ok=True)
        handle = spec.log.open("ab")
        kwargs: dict = {"cwd": ROOT, "stdout": handle, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
                        "env": _python_env(), "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(spec.command, **kwargs)
        finally:
            handle.close()
        identity = _process_identity(process.pid)
        spec.pid_file.write_text(json.dumps({'pid':process.pid,'identity':identity,
            'root':str(ROOT.resolve()),'service':name,'command':spec.command}), encoding='utf8')
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if spec.port is None:
                return True, f"{name} started (pid {process.pid})"
            if port_open(spec.port):
                return True, f"{name} started on port {spec.port} (pid {process.pid})"
            if process.poll() is not None:
                return False, f"{name} exited with code {process.returncode}; see {spec.log}"
            time.sleep(0.4)
        return False, f"{name} did not open port {spec.port}; see {spec.log}"

    def _read_pid(self, spec: Service) -> int:
        try:
            value = json.loads(spec.pid_file.read_text(encoding='utf8'))
            return int(value.get('pid', 0) if isinstance(value, dict) else value)
        except (OSError, ValueError, TypeError):
            return 0

    def _owned_process(self, spec: Service) -> bool:
        try:
            record = json.loads(spec.pid_file.read_text(encoding='utf8'))
            if not isinstance(record, dict) or record.get('root') != str(ROOT.resolve()) or record.get('service') != spec.name:
                return False
            command = record.get('command')
            if not isinstance(command, list) or command[:len(spec.command)] != spec.command or not record.get('identity'):
                return False
            return _process_identity(int(record['pid'])) == record['identity']
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def stop(self, name: str) -> tuple[bool, str]:
        spec = self.spec(name)
        pid = self._read_pid(spec)
        if not _pid_alive(pid):
            spec.pid_file.unlink(missing_ok=True)
            return True, f"{name} not running"
        if not self._owned_process(spec):
            return False, f"{name} PID ownership could not be verified; process and record left untouched"
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False, creationflags=0x08000000)
        else:
            subprocess.run(["kill", "-TERM", str(pid)], capture_output=True, check=False)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        if _pid_alive(pid):
            return False, f"{name} did not stop; ownership record retained"
        spec.pid_file.unlink(missing_ok=True)
        return True, f"{name} stopped"

    def restart(self, name: str) -> list[str]:
        stopped, message = self.stop(name)
        return [message, self.start(name)[1]] if stopped else [message]

    def status(self) -> list[dict]:
        rows = []
        for name, spec in self.specs().items():
            pid = self._read_pid(spec)
            owned = self._owned_process(spec)
            rows.append({
                "name": name,
                "port": spec.port,
                "running": owned and port_open(spec.port),
                "pid": pid if owned else None,
                "occupied": port_open(spec.port),
                "log": spec.log,
            })
        return rows

    def tail(self, name: str, lines: int = 30) -> str:
        spec = self.spec(name)
        if not spec.log or not spec.log.exists():
            return f"no log for {name}"
        with spec.log.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:]).rstrip()

    def players(self) -> list:
        return http_json(f"http://127.0.0.1:{self.admin_port}/api/players") or []

    def status_json(self) -> dict:
        return http_json(f"http://127.0.0.1:{self.admin_port}/api/status") or {}

    # -- device ----------------------------------------------------------
    def adb(self, *args: str, timeout: float = 20.0, serial: str | None = None) -> subprocess.CompletedProcess:
        executable = shutil.which("adb") or "adb"
        if not args or args[0] in ('kill-server', 'start-server'):
            raise DeviceSelectionError('Global ADB lifecycle commands are not used by this checkout')
        if args[0] in ('devices', 'version'):
            command = [executable, *args]
        else:
            target = serial or self.select_device()
            if serial is not None and serial not in self.devices():
                raise DeviceSelectionError('Requested device is not connected: ' + serial)
            command = [executable, '-s', target, *args]
        options = {'creationflags':0x08000000} if os.name == 'nt' else {}
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, **options)

    def devices(self) -> list[str]:
        result = self.adb('devices', '-l')
        return [parts[0] for line in result.stdout.splitlines()[1:]
                if len(parts := line.split()) >= 2 and parts[1] == 'device']

    def select_device(self) -> str:
        devices = self.devices()
        selected = self._selected_serial or self.serial
        if selected:
            if selected not in devices:
                raise DeviceSelectionError('Selected device is unavailable; no other phone was chosen: ' + selected)
        else:
            physical = [device for device in devices if not device.startswith('emulator-')]
            if len(physical) != 1:
                raise DeviceSelectionError('Set DITTOBUFFER_ADB_SERIAL to one connected phone; no unique physical device is available')
            selected = physical[0]
        self._selected_serial = selected
        return selected

    def reverse(self) -> list[str]:
        messages = []
        try:
            self.select_device()
        except DeviceSelectionError as error:
            return [str(error)]
        ports = [self.port, self.admin_port]
        if self.map_port and self.map_port not in ports:
            ports.append(self.map_port)
        for port in ports:
            result = self.adb("reverse", f"tcp:{port}", f"tcp:{port}")
            messages.append(f"reverse tcp:{port}: {'ok' if result.returncode == 0 else result.stderr.strip()}")
        listing = self.adb("reverse", "--list").stdout.strip().splitlines()
        messages.append("tunnels: " + (", ".join(listing) if listing else "none"))
        return messages

    def relaunch_app(self) -> str:
        try:
            self.select_device()
        except DeviceSelectionError as error:
            return str(error)
        self.adb("shell", "am", "force-stop", APP_ID)
        time.sleep(1)
        result = self.adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}")
        return "app relaunched" if result.returncode == 0 else result.stderr.strip()

    def reconnect(self) -> list[str]:
        messages: list[str] = []
        try:
            serial = self.select_device()
        except DeviceSelectionError as error:
            return [str(error)]
        messages.append('selected phone: ' + serial)
        messages.extend(self.reverse())
        # A loopback server is reached through adb reverse. gnirehtet is not
        # needed there, and a dead relay leaves a VPN that turns every external
        # request into a timeout (the documented 100-200x slowdown).
        if self._loopback_server():
            self.stop('gnirehtet')
            self.adb('shell', 'am', 'force-stop', 'com.genymobile.gnirehtet')
            messages.append('gnirehtet not started; removed any dead VPN for the loopback server')
        else:
            messages.append(self.start('gnirehtet', wait=10)[1])
        messages.append(self.relaunch_app())
        return messages


def doctor(config: Path = DEFAULT_CONFIG) -> list[tuple[str, bool, str]]:
    """Environment checks shown by ``/doctor``."""

    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    checks.append(("config profile", Path(config).is_file(), str(config)))
    checks.append(("adb", bool(shutil.which("adb")), shutil.which("adb") or "not on PATH"))
    gnirehtet = TOOLS / "gnirehtet" / ("gnirehtet.exe" if os.name == "nt" else "gnirehtet")
    checks.append(("gnirehtet", gnirehtet.exists(), str(gnirehtet)))
    gadget = TOOLS / "frida-gadget" / "libgadget.so"
    checks.append(("frida gadget", gadget.exists(), str(gadget)))
    checks.append(("world snapshot", (ROOT / "data" / "world" / "current.json").exists(), "data/world/current.json"))
    checks.append(("pokemon assets", (ROOT / "assets" / "pokemon").exists(), "assets/pokemon"))
    checks.append(("base APK", any((ROOT / "apk").glob("*.apk")), "apk/"))
    return checks
