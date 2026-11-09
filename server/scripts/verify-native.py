"""Read-only ELF ABI checks with an optional explicit original library or APK.

Reports are new files under ignored run/. No source documentation is rewritten.
Passing these checks does not establish Android runtime compatibility.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import zipfile

from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "client/native/target/aarch64-linux-android/release/libNianticLabsPlugin.so"
REQUIRED = {"GetN2Api", "Java_com_nianticlabs_nia_unity_UnityUtil_nativeInit", "CreateBackgroundContext", "UnityPluginLoad", "UnityPluginUnload"}


def inspect(path):
    path = Path(path)
    label = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    if path.suffix.lower() == ".apk":
        member = "lib/arm64-v8a/libNianticLabsPlugin.so"
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(member)
        label += "!" + member
    else:
        raw = path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    symbols, dynamic = elf.get_section_by_name(".dynsym"), elf.get_section_by_name(".dynamic")
    if symbols is None:
        raise ValueError("ELF dynamic symbol table missing")
    exports = sorted(s.name for s in symbols.iter_symbols() if s["st_shndx"] != "SHN_UNDEF" and s["st_info"]["bind"] == "STB_GLOBAL")
    tags = list(dynamic.iter_tags()) if dynamic is not None else []
    return dict(file=label, architecture=elf.header["e_machine"], sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                exports=exports, needed=[t.needed for t in tags if t.entry.d_tag == "DT_NEEDED"],
                soname=[t.soname for t in tags if t.entry.d_tag == "DT_SONAME"])


def validate(result):
    if result["architecture"] != "EM_AARCH64":
        raise ValueError("Replacement must be an Android ARM64 ELF library")
    if not REQUIRED.issubset(result["exports"]):
        raise ValueError("Replacement is missing required native exports")
    if result["soname"] != ["libNianticLabsPlugin.so"]:
        raise ValueError("Replacement SONAME must be libNianticLabsPlugin.so")


def write_report(root, output, result):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not output.is_relative_to(root / "run"):
        raise ValueError("Native reports must stay inside ignored run/")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=ROOT / ARTIFACT)
    parser.add_argument("--original", type=Path, help="Optional explicit legally obtained original .so or ARM64 APK; read only")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    parser.add_argument("--output", type=Path, default=ROOT / "run" / ("native-build-" + stamp + ".json"))
    parser.add_argument("--no-report", action="store_true", help="Print validation only without creating any file")
    args = parser.parse_args(argv)
    try:
        replacement = inspect(args.artifact.resolve())
        validate(replacement)
        result = dict(replacement=replacement, required_exports_verified=True,
                      android_runtime="NOT_TESTED", note="Export and SONAME checks do not prove runtime ABI compatibility.")
        if args.original:
            result["original"] = inspect(args.original.resolve())
        if not args.no_report:
            write_report(ROOT, args.output, result)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ELFError) as error:
        parser.exit(1, "Native verification failed: " + str(error) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
