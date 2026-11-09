#!/usr/bin/env python3
"""Unified APK patcher for the Dittobuffer kit.

Subcommands
-----------
server   Build a server-ready APK from the official 0.429.2 base/split inputs.
         Injects the rebuilt native plugin, the client agent bundle, the
         profile/CA, the embedded runtime and the Family launcher, then signs.
gadget   Inject Frida Gadget into an existing APK for non-rooted debugging.

`server` is the complete "make my phone work with this server" path. `gadget`
is the optional instrumentation path. Both use the verified toolchain in the
kit; the official APK inputs are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import ipaddress
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from pathlib import Path

from dittobuffer.paths import ROOT

KIT = ROOT
PYTHON = Path(sys.executable)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args: list[object]) -> None:
    print("  $ " + " ".join(str(a) for a in args))
    result = subprocess.run([str(a) for a in args], cwd=KIT)
    if result.returncode:
        raise SystemExit(f"Command failed ({result.returncode})")


def python() -> Path:
    if PYTHON.exists():
        return PYTHON
    return Path(sys.executable)


def provision(host: str, port: int, rotate: bool) -> None:
    state = KIT / ".private" / "provision-state.json"
    if state.exists():
        current = json.loads(state.read_text())
        if current.get("host") != host:
            rotate = True
            print(f"host changed {current.get('host')} -> {host}: rotating TLS")
    args: list[object] = [python(), KIT / "tools" / "transfer-runtime.py", "provision", "--destination", KIT, "--host", host, "--port", port]
    if rotate:
        args.append("--rotate-tls")
    run(args)


def required_file(path: Path, description: str) -> Path:
    if not path.is_file():
        raise SystemExit(f"{description} missing: {path}")
    with path.open("rb") as stream:
        if stream.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise SystemExit(f"{description} is an unrestored Git LFS pointer: {path}. Restore its exact LFS object first.")
    return path


def native_plugin(override: str | None = None) -> Path:
    if override:
        path = Path(override).expanduser()
        path = (KIT / path).resolve() if not path.is_absolute() else path.resolve()
    else:
        candidates = [
            KIT / "client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so",
            KIT / "client/plugin/libNianticLabsPlugin.so",
            KIT / "builds" / "libNianticLabsPlugin.so",
        ]
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    required_file(path, "ARM64 native plugin")
    with path.open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
        raise SystemExit(f"Native plugin must be a rebuilt little-endian ARM64 ELF library: {path}")
    return path


def decoded_input(part: str) -> Path:
    if part not in ("base", "split"):
        raise SystemExit("Decoded input must be base or split")
    private = KIT / ".staging" / "client-inputs" / part
    legacy = KIT / "builds" / ("client-" + part)
    path = private if private.is_dir() else legacy
    parent = KIT / (".staging" if path == private else "builds")
    if not path.is_dir() or not path.resolve().is_relative_to(parent.resolve()):
        raise SystemExit(f"Restore the independently obtained decoded {part} input at {private}")
    # Never follow a copied link while the builder edits its disposable input.
    for child in (path, *path.rglob("*")):
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise SystemExit(f"Decoded input contains a link; supply an ordinary independent copy: {child}")
    return path


def public_profile(path: Path, host: str, port: int) -> tuple[dict, Path]:
    required_file(path, "Provisioned public profile")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise SystemExit("Provisioned public profile must be a JSON object")
    forbidden = {"password", "passwd", "secret", "token", "credentials", "creds", "accounts", "account", "username", "privatekey", "apikey", "authorization", "cookie", "session"}
    def review(value):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized.replace("_", "") in forbidden or set(normalized.split("_")) & forbidden or normalized.endswith(("_private_key", "_api_key")):
                    raise SystemExit("Public APK profile contains credential/account material; supply a public profile only")
                review(child)
        elif isinstance(value, list):
            for child in value:
                review(child)
    review(profile)
    origin = urlsplit(profile.get("origin", ""))
    if origin.scheme != "https" or origin.username is not None or origin.password is not None or origin.hostname != host or origin.port != port or origin.path not in ("", "/") or origin.query or origin.fragment:
        raise SystemExit("Provisioned public profile origin differs from --host/--port; provision the intended endpoint first")
    ca_file = profile.get("ca_file")
    if not isinstance(ca_file, str) or not ca_file:
        raise SystemExit("Public profile must identify a project-local public CA certificate")
    ca = (KIT / ca_file).resolve()
    if not ca.is_relative_to(KIT.resolve()):
        raise SystemExit("Public CA certificate must remain inside the project")
    required_file(ca, "Public CA certificate")
    if b"PRIVATE KEY" in ca.read_bytes():
        raise SystemExit("ca_file points at private signing material; only a public certificate can enter the APK")
    return profile, ca


def output_root(relative: str) -> Path:
    path = (KIT / relative).resolve()
    if not path.is_relative_to(KIT.resolve()) or path != KIT.resolve() / relative:
        raise SystemExit("Patcher output must remain inside the project without links to other directories")
    path.mkdir(parents=True, exist_ok=True)
    return path


def server(args: argparse.Namespace) -> int:
    host = args.host
    if not host:
        state = KIT / ".private" / "provision-state.json"
        if state.exists():
            host = json.loads(state.read_text(encoding="utf-8")).get("host")
        if not host:
            raise SystemExit("Pass --host <LAN IPv4> (the address the phone will use).")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise SystemExit("--host must be the private or loopback IPv4 address used by the phone") from None
    if address.version != 4 or not (address.is_private or address.is_loopback) or not 1 <= args.port <= 65535:
        raise SystemExit("A private/loopback IPv4 and a valid TCP port are required")
    host = str(address)
    # Fail missing or unresolved inputs before provisioning changes anything.
    plugin = native_plugin(getattr(args, "plugin", None))
    builder = required_file(KIT / "scripts" / "build-standalone.py", "Standalone builder")
    inputs = {part: decoded_input(part) for part in ("base", "split")}
    if not args.no_provision:
        required_file(KIT / "tools" / "transfer-runtime.py", "Local provisioning helper")
        provision(host, args.port, args.rotate_tls)
    public = KIT / "builds" / "transfer-public-profile.json"
    profile, ca = public_profile(public, host, args.port)

    # .staging is already ignored by the repository. Each attempt gets fresh
    # decoded copies, so retries never reuse mutated inputs or overwrite a
    # previously built APK, the tracked public profile or the official cache.
    stage = output_root(".staging")
    output_dir = Path(tempfile.mkdtemp(prefix="patcher-", dir=stage))
    build_profile = output_dir / "public-profile.json"
    shutil.copy2(public, build_profile)
    for part, original in inputs.items():
        working = output_dir / part
        shutil.copytree(original, working)
        plugin_target = working / "lib/arm64-v8a/libNianticLabsPlugin.so"
        plugin_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin, plugin_target)
    run([python(), builder, "--profile", build_profile, "--output", output_dir])

    apk = required_file(output_dir / "dittobuffer-family.apk", "Built standalone APK")
    build_report = required_file(output_dir / "build.json", "Standalone build manifest")
    destination_root = output_root("apk/built")
    identity = output_dir.name.removeprefix("patcher-")
    destination = destination_root / f"dittobuffer-family-{host}-{identity}.apk"
    shutil.copy2(apk, destination)
    shutil.copy2(build_report, destination.with_suffix(".build.json"))
    report = {
        "status": "OK",
        "mode": "server",
        "origin": profile["origin"],
        "output": str(destination),
        "sha256": sha256(destination),
        "bytes": destination.stat().st_size,
        "working_directory": str(output_dir),
        "input_preservation": "Original decoded base/split, native plugin and tracked public profile are unchanged",
        "injected": {
            "native_plugin": {"path": str(plugin), "sha256": sha256(plugin)},
            "ca_certificate": {"path": str(ca), "sha256": sha256(ca)},
            "profile": str(build_profile),
            "agent_bundle": "assets/dittobuffer/agent.js",
            "embedded_runtime": "assets/dittobuffer/libdittobuffer_runtime.so",
            "launcher": "org.dittobuffer.launcher.FamilyActivity",
            "merged_split_libraries": "lib/arm64-v8a",
        },
        "android_install_and_gameplay": "NOT_TESTED",
    }
    report_path = destination.with_suffix(".inject.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OK", "output": str(destination), "origin": report["origin"]}, indent=2))
    print(f"report: {report_path}")
    return 0


def gadget(args: argparse.Namespace) -> int:
    command: list[object] = [python(), Path(__file__).with_name("frida_patch.py"), args.apk]
    if args.output:
        command += ["--output", args.output]
    if args.split:
        command += ["--split", *args.split]
    if args.script:
        command += ["--script", args.script]
    if args.gadget:
        command += ["--gadget", args.gadget]
    if args.abi:
        command += ["--abi", args.abi]
    if args.listen_port:
        command += ["--listen-port", str(args.listen_port)]
    if args.keep_work:
        command.append("--keep-work")
    if args.verbose:
        command.append("--verbose")
    run(command)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)

    server_parser = sub.add_parser("server", help="build a server-ready APK")
    server_parser.add_argument("--host", help="LAN IPv4 the phone will connect to")
    server_parser.add_argument("--port", type=int, default=8445)
    server_parser.add_argument("--plugin", help="rebuilt ARM64 libNianticLabsPlugin.so (default: maintained Cargo output, kit plugin or legacy build fallback)")
    server_parser.add_argument("--rotate-tls", action="store_true", help="always issue a new certificate")
    server_parser.add_argument("--no-provision", action="store_true", help="reuse the existing TLS/profile")
    server_parser.set_defaults(func=server)

    gadget_parser = sub.add_parser("gadget", help="inject Frida Gadget into an APK")
    gadget_parser.add_argument("apk")
    gadget_parser.add_argument("-o", "--output")
    gadget_parser.add_argument("--split", nargs="*")
    gadget_parser.add_argument("--script")
    gadget_parser.add_argument("--gadget")
    gadget_parser.add_argument("--abi")
    gadget_parser.add_argument("--listen-port", type=int)
    gadget_parser.add_argument("--keep-work", action="store_true")
    gadget_parser.add_argument("--verbose", action="store_true")
    gadget_parser.set_defaults(func=gadget)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
