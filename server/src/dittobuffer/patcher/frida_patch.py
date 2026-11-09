#!/usr/bin/env python3
"""Inject Frida Gadget into an Android APK for non-rooted devices.

The patcher decodes the APK with apktool, adds the gadget and its config to
lib/<abi>/, inserts System.loadLibrary("gadget") into the Application (or
launcher Activity) smali, rebuilds, aligns and signs the result with the kit
key. Additional split APKs can be re-signed with the same key so the install
set keeps one signature.

Only the ARM64 gadget shipped in patcher/gadget/arm64-v8a is bundled. Other
architectures can be supplied with --gadget and --abi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from dittobuffer.paths import ROOT

KIT = ROOT
TOOLS = ROOT / "tools"
APKTOOL = TOOLS / "apktool" / "apktool.jar"
FRAMEWORK = TOOLS / "apktool"
BUILD_TOOLS = TOOLS / "android-build-tools"
DEFAULT_GADGET = TOOLS / "frida-gadget" / "arm64-v8a" / "libgadget.so"
DEFAULT_KEYSTORE = ROOT / "src" / "dittobuffer" / "patcher" / "keys" / "dittobuffer-patch.jks"
LISTEN_CONFIG = {
    "interaction": {
        "type": "listen",
        "address": "127.0.0.1",
        "port": 27042,
        "on_load": "wait",
    },
    "runtime": "qjs",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_java() -> Path:
    java = shutil.which("java")
    if java:
        return Path(java)
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return candidate
    raise SystemExit("Java runtime not found. Install JDK 17+ and put it on PATH.")


def tool(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    candidate = BUILD_TOOLS / f"{name}{suffix}"
    if not candidate.exists():
        candidate = BUILD_TOOLS / f"{name}.bat"
    if not candidate.exists():
        raise SystemExit(f"Missing build tool: {name} (expected in {BUILD_TOOLS})")
    return candidate


def run(args: list[object], cwd: Path | None = None, quiet: bool = False) -> None:
    printable = " ".join(str(a) for a in args)
    if not quiet:
        print(f"  $ {printable}")
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.STDOUT if quiet else None,
        text=True,
    )
    if proc.returncode:
        if quiet and proc.stdout:
            print(proc.stdout[-4000:], file=sys.stderr)
        raise SystemExit(f"Command failed ({proc.returncode}): {printable}")


def decode(apk: Path, work: Path) -> Path:
    decoded = work / "decoded"
    if decoded.exists():
        shutil.rmtree(decoded)
    run([find_java(), "-jar", APKTOOL, "d", "-f", "-p", FRAMEWORK, "-o", decoded, apk])
    return decoded


def manifest_classes(decoded: Path) -> tuple[str | None, str | None]:
    manifest = (decoded / "AndroidManifest.xml").read_text(encoding="utf-8", errors="replace")
    app = re.search(r"<application\b[^>]*?android:name\s*=\s*\"([^\"]+)\"", manifest)
    launcher = None
    for activity in re.finditer(r"<activity\b[^>]*>(.*?)</activity>", manifest, re.S):
        body = activity.group(1)
        if "android.intent.action.MAIN" in body and "android.intent.category.LAUNCHER" in body:
            name = re.search(r"android:name\s*=\s*\"([^\"]+)\"", activity.group(0))
            if name:
                launcher = name.group(1)
                break
    return (app.group(1) if app else None), launcher


def find_smali(decoded: Path, class_name: str) -> Path | None:
    relative = class_name.strip(".").replace(".", "/") + ".smali"
    for directory in sorted(decoded.glob("smali*")):
        candidate = directory / relative
        if candidate.is_file():
            return candidate
    return None


def candidate_classes(decoded: Path) -> list[tuple[str, str]]:
    app, launcher = manifest_classes(decoded)
    roots: list[tuple[str, str]] = []
    if app:
        roots.append((app, "application"))
    if launcher and launcher != app:
        roots.append((launcher, "launcher-activity"))
    if not roots:
        raise SystemExit("No Application class or launcher Activity found in the manifest.")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, kind in roots:
        current = name
        for _ in range(6):
            if current in seen:
                break
            seen.add(current)
            result.append((current, kind))
            path = find_smali(decoded, current)
            if not path:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
            parent = re.search(r"^\.super (L[^;]+;)", text, re.M)
            if not parent:
                break
            current = parent.group(1)[1:-1].replace("/", ".")
            kind = "superclass"
    return result


METHOD_ORDER = (
    ("attachBaseContext", "attachBaseContext(Landroid/content/Context;)V"),
    ("onCreate", "onCreate(Landroid/os/Bundle;)V"),
    ("onCreate", "onCreate()V"),
    ("<clinit>", "static constructor <clinit>()V"),
    ("<clinit>", "<clinit>()V"),
)


def method_block(text: str, signature: str) -> tuple[int, int, int] | None:
    start = text.find(f".method {signature}")
    if start < 0:
        start = text.find(f".method public {signature}")
    if start < 0:
        start = text.find(f".method protected {signature}")
    if start < 0:
        start = text.find(f".method private {signature}")
    if start < 0:
        return None
    end = text.find(".end method", start)
    if end < 0:
        return None
    return start, end, text.find("\n", start) + 1


def param_registers(descriptor: str, is_static: bool) -> int:
    inner = descriptor[descriptor.find("(") + 1 : descriptor.find(")")]
    count = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if char in "JD":
            count += 2
        elif char in "L[":
            count += 1
            if char == "L":
                index = inner.find(";", index)
                if index < 0:
                    break
        else:
            count += 1
        index += 1
    return count if is_static else count + 1


def register_count(descriptor: str, header_line: str) -> int:
    is_static = " static " in header_line or header_line.startswith(".method static ")
    return param_registers(descriptor, is_static)


def inject(text: str, signature: str, verbose: bool) -> tuple[str, int]:
    found = method_block(text, signature)
    if not found:
        raise LookupError(signature)
    start, end, header_end = found
    block = text[start:end]
    header = block.splitlines()[0]
    locals_match = re.search(r"(\n\s*\.locals\s+)(\d+)", block)
    registers_match = re.search(r"(\n\s*\.registers\s+)(\d+)", block)
    if not locals_match and not registers_match:
        raise LookupError(f"{signature} has no .locals/.registers directive")
    method_name = signature.split("(")[0].split()[-1]
    descriptor = signature[signature.find("(") :]
    params = register_count(descriptor, header)
    if locals_match:
        locals_now = int(locals_match.group(2))
        if registers_match:
            raise LookupError("Both .locals and .registers present")
        new_locals = locals_now + 1
        replacement = f"{locals_match.group(1)}{new_locals}"
        block = block.replace(locals_match.group(0), replacement, 1)
        register = locals_now
    else:
        total = int(registers_match.group(2))
        locals_now = total - params
        if locals_now < 0:
            raise LookupError(f"{signature} register count is smaller than its parameters")
        new_total = total + 1
        block = block.replace(registers_match.group(0), f"{registers_match.group(1)}{new_total}", 1)
        # Parameters stay at the end; use the first new local register.
        block = block.replace(".registers ", ".locals ", 1)
        register = locals_now
        block = re.sub(r"(\.locals\s+)\d+", lambda m: f"{m.group(1)}{locals_now + 1}", block, count=1)
    load = (
        f'    const-string v{register}, "gadget"\n'
        f"    invoke-static {{v{register}}}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n"
    )
    prologue = re.search(r"\n\s*\.prologue\s*\n", block)
    if prologue:
        insert_at = prologue.end()
    else:
        marker = re.search(r"\n\s*\.locals\s+\d+\s*\n", block)
        insert_at = marker.end()
    block = block[:insert_at] + load + block[insert_at:]
    if verbose:
        print(f"  injected into {method_name} (register v{register})")
    return text[:start] + block + text[end:], register


def patch_smali(path: Path, verbose: bool) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    for name, signature in METHOD_ORDER:
        try:
            patched, _ = inject(text, signature, verbose)
            path.write_text(patched, encoding="utf-8", errors="surrogateescape")
            return name, signature
        except LookupError:
            continue
    raise SystemExit(
        f"Could not find attachBaseContext, onCreate or <clinit> in {path}."
    )


def gadget_config(args: argparse.Namespace) -> dict:
    if args.script:
        script = Path(args.script).resolve()
        return {
            "interaction": {
                "type": "script",
                "path": f"/data/local/tmp/{script.name}",
                "on_change": "reload",
            },
            "runtime": "qjs",
        }
    config = json.loads(json.dumps(LISTEN_CONFIG))
    config["interaction"]["address"] = args.listen_address
    config["interaction"]["port"] = args.listen_port
    return config


def ensure_keystore(keystore: Path, alias: str) -> None:
    if keystore.exists():
        return
    keystore.parent.mkdir(parents=True, exist_ok=True)
    keytool = find_java().with_name("keytool.exe" if os.name == "nt" else "keytool")
    if not keytool.exists():
        keytool = Path(os.environ.get("JAVA_HOME", "")) / "bin" / keytool.name
    run(
        [
            keytool,
            "-genkeypair",
            "-keystore",
            keystore,
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-alias",
            alias,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "3650",
            "-dname",
            "CN=Dittobuffer patch key",
        ]
    )


def sign(apk: Path, keystore: Path, alias: str, verbose: bool) -> None:
    apksigner = tool("apksigner")
    command = [
        apksigner,
        "sign",
        "--ks",
        keystore,
        "--ks-key-alias",
        alias,
        "--ks-pass",
        "pass:android",
        "--key-pass",
        "pass:android",
        apk,
    ]
    run(command, quiet=not verbose)
    run([apksigner, "verify", "--print-certs", apk], quiet=not verbose)


def align(src: Path, dst: Path) -> None:
    run([tool("zipalign"), "-f", "-P", "16", "4", src, dst])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path, help="input APK to patch")
    parser.add_argument("-o", "--output", type=Path, help="output APK (default: apk/patched/<name>-gadget.apk)")
    parser.add_argument("--gadget", type=Path, default=DEFAULT_GADGET, help="Frida Gadget .so for the target ABI")
    parser.add_argument("--abi", default="arm64-v8a", help="target ABI directory (default: arm64-v8a)")
    parser.add_argument("--script", type=Path, help="bundle a Frida script for script interaction instead of listen mode")
    parser.add_argument("--listen-address", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=27042)
    parser.add_argument("--keystore", type=Path, default=DEFAULT_KEYSTORE)
    parser.add_argument("--alias", default="dittobuffer")
    parser.add_argument("--split", type=Path, nargs="*", default=[], help="split APKs to re-sign with the same key")
    parser.add_argument("--keep-work", action="store_true", help="keep the decoded working directory")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    apk = args.apk.resolve()
    if not apk.is_file():
        raise SystemExit(f"APK not found: {apk}")
    gadget = args.gadget.resolve()
    if not gadget.is_file():
        raise SystemExit(f"Gadget not found: {gadget}")
    output = args.output or (KIT / "apk" / "patched" / f"{apk.stem}-gadget.apk")
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
    if f"lib/{args.abi}/" not in names and not any(n.startswith(f"lib/{args.abi}/") for n in names):
        print(f"warning: input has no lib/{args.abi}/ entries; the gadget is still added")

    work = Path(tempfile.mkdtemp(prefix="dittobuffer-patch-"))
    try:
        print(f"[1/6] decoding {apk.name}")
        decoded = decode(apk, work)
        print("[2/6] locating entry point")
        patched_class = None
        patched_kind = None
        entry = signature = None
        for class_name, kind in candidate_classes(decoded):
            smali = find_smali(decoded, class_name)
            if not smali:
                continue
            try:
                entry, signature = patch_smali(smali, args.verbose)
                patched_class, patched_kind = class_name, kind
                break
            except SystemExit:
                continue
        if not patched_class:
            raise SystemExit("No injectable method found in the Application/launcher class chain.")
        class_name, kind = patched_class, patched_kind
        print(f"  {kind}: {class_name} -> {entry}")

        print(f"[3/6] adding gadget ({args.abi})")
        lib_dir = decoded / "lib" / args.abi
        lib_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(gadget, lib_dir / "libgadget.so")
        config = gadget_config(args)
        (lib_dir / "libgadget.config.so").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        if args.script:
            shutil.copy2(Path(args.script).resolve(), work / Path(args.script).name)
            print(f"  script config points at /data/local/tmp/{Path(args.script).name}")

        print("[4/6] rebuilding")
        unsigned = work / "unsigned.apk"
        run([find_java(), "-jar", APKTOOL, "b", "-p", FRAMEWORK, "-o", unsigned, decoded], quiet=not args.verbose)

        print("[5/6] aligning and signing")
        aligned = work / "aligned.apk"
        align(unsigned, aligned)
        if output.exists():
            output.unlink()
        shutil.copy2(aligned, output)
        ensure_keystore(args.keystore.resolve(), args.alias)
        sign(output, args.keystore.resolve(), args.alias, args.verbose)

        signed_splits = []
        for split in args.split:
            split = split.resolve()
            split_out = output.parent / f"{split.stem}-gadget.apk"
            aligned_split = work / f"{split.stem}-aligned.apk"
            align(split, aligned_split)
            shutil.copy2(aligned_split, split_out)
            sign(split_out, args.keystore.resolve(), args.alias, args.verbose)
            signed_splits.append(str(split_out))

        print("[6/6] verifying")
        with zipfile.ZipFile(output) as archive:
            present = archive.namelist()
        assert f"lib/{args.abi}/libgadget.so" in present
        assert f"lib/{args.abi}/libgadget.config.so" in present
        report = {
            "status": "OK",
            "input": str(apk),
            "input_sha256": sha256(apk),
            "output": str(output),
            "output_sha256": sha256(output),
            "bytes": output.stat().st_size,
            "application_class": class_name,
            "entry": entry,
            "signature": signature,
            "abi": args.abi,
            "gadget_sha256": sha256(gadget),
            "config": config,
            "splits": signed_splits,
            "signer": str(args.keystore.resolve()),
        }
        report_path = output.with_suffix(".patch.json")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("status", "output", "entry", "abi", "splits")}, indent=2))
        print(f"report: {report_path}")
        if args.script:
            print(f"push the script: adb push {args.script} /data/local/tmp/{Path(args.script).name}")
        else:
            print(f"connect: adb forward tcp:{args.listen_port} tcp:{args.listen_port} && frida -H 127.0.0.1:{args.listen_port} -n <process>")
        return 0
    finally:
        if args.keep_work:
            print(f"work directory kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
