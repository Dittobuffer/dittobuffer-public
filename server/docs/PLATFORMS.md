# Platform support

Dittobuffer is a Python server with a browser admin panel. The Android client
and the server are separate concerns; the server itself does not depend on a
particular CPU architecture.

## Host matrix

| Host | Start | Notes |
|---|---|---|
| Windows 10/11 x64 | `START-SERVER.bat` | Uses `.venv\Scripts\python.exe`; ADB is optional. |
| Linux x86_64 | `bash start-server.sh` | Creates `.venv` on first use. |
| Linux ARM64 (Raspberry Pi 4/5, ARM servers) | `bash start-server.sh` | Same source; use a 64-bit Python 3.11+ build. |
| Linux ARMv7 | `bash start-server.sh` | 32-bit Python works, but the supplied Android APK is arm64-only. |
| Linux RISC-V 64 | `bash start-server.sh` | Python 3.11+ is required; install `python3-venv` and `python3-pip`. |
| macOS Intel / Apple silicon | `bash start-server.sh` or double-click `start-server.command` | Use Homebrew Python 3.13 if the system Python is older than 3.11. |
| Docker amd64 | `docker compose up --build` | Server and admin are separate containers. |
| Docker arm64 | `docker compose up --build` | `python:3.13-slim` publishes an arm64 manifest. |
| Docker riscv64 | `docker compose -f docker-compose.riscv64.yml up --build` | Best-effort Debian trixie image; `cryptography` may need to compile on older RISC-V toolchains. |
| Android client | `adb install -r artifacts/dittobuffer-family-127.0.0.1.apk` | **arm64-v8a only**; the native game libraries are supplied as arm64. |

## Linux/macOS first run

```bash
bash scripts/bootstrap.sh
bash start-server.sh
```

Use `./start-server.sh` instead when execute permissions were preserved.

Then open <http://127.0.0.1:8080> for the admin panel. The game server listens on
`https://127.0.0.1:8445`; the plain-HTTP map origin listens on
`http://127.0.0.1:8081/map/tiles`.

For a USB-connected phone, preserve the loopback design:

```bash
adb reverse tcp:8445 tcp:8445
adb reverse tcp:8080 tcp:8080
adb reverse tcp:8081 tcp:8081
```

## RISC-V notes

The Python application is architecture-independent. The only likely obstacle is
a dependency without a prebuilt RISC-V wheel. If `pip install -r requirements.txt`
tries to compile `cryptography`, install the distribution's Rust toolchain and
OpenSSL headers first:

```bash
sudo apt install build-essential libssl-dev libffi-dev pkg-config rustc cargo
```

Do not expect the Android APK to install on a RISC-V Android device. The
standalone build contains arm64 game libraries from the supplied 0.429.2 APK.

## Docker notes

The compose file publishes only loopback addresses. That is intentional: the
admin panel has no authentication and the game server is designed to be reached
through `adb reverse`, not exposed to a LAN or the public internet.

The map origin (`8081`) is part of the server container, so both the Docker
image and the compose port list include it.
