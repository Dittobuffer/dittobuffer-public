"""Safely extract/repair the pinned Linux NDK ZIP inside project-local .tools.

Complete path, symlink and existing-byte validation precedes all writes. Existing
different files are preserved. Only verified ZIP symlink placeholders are replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import stat
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NDK = "android-ndk-r27d"


def relative_name(value):
    if not value or "\\" in value or "\0" in value or ":" in value:
        raise ValueError("Invalid ZIP path")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or path.parts[0] != NDK:
        raise ValueError("ZIP member leaves the pinned NDK directory: " + value)
    return path.as_posix().rstrip("/")


def confined(tools, relative, follow_leaf=True):
    path = tools / relative
    parent = path.parent.resolve()
    if not parent.is_relative_to(tools):
        raise ValueError("Existing parent leaves .tools")
    # Never write through an existing directory symlink, even one pointing to
    # another location inside .tools.
    for ancestor in path.parents:
        if ancestor == tools:
            break
        if ancestor.is_symlink():
            raise ValueError("Existing symlink ancestor in NDK destination")
    resolved = path.resolve() if follow_leaf else parent / path.name
    if not resolved.is_relative_to(tools / NDK):
        raise ValueError("Resolved NDK destination leaves its directory")
    return path


def stream_digest(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_archive(archive, tools, extract=False):
    rows, links, seen = [], {}, set()
    for info in archive.infolist():
        # ZipInfo.filename normalizes Windows backslashes and truncates NULs;
        # validate the original archive spelling before that normalization.
        name = relative_name(info.orig_filename)
        if name.casefold() in seen:
            raise ValueError("Duplicate ZIP destination: " + name)
        seen.add(name.casefold())
        mode = info.external_attr >> 16
        directory = info.is_dir() or stat.S_ISDIR(mode)
        kind = "directory" if directory else "symlink" if stat.S_ISLNK(mode) else "file"
        if stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
            raise ValueError("Unsupported ZIP file type: " + name)
        row = dict(info=info, name=name, kind=kind, mode=mode & 0o777)
        if kind == "symlink":
            if info.file_size > 4096:
                raise ValueError("Unexpected NDK symlink length")
            target = archive.read(info).decode("utf8")
            if not target or "\\" in target or "\0" in target or ":" in target or PurePosixPath(target).is_absolute() or PureWindowsPath(target).drive:
                raise ValueError("Invalid NDK symlink target")
            final = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if final != NDK and not final.startswith(NDK + "/"):
                raise ValueError("NDK symlink target leaves .tools")
            row["target"], row["target_bytes"] = target, target.encode("utf8")
            links[name] = final
        rows.append(row)
    # Resolve planned links as well as filesystem links before creating anything.
    for name, target in links.items():
        visited = {name}
        while True:
            prefix = next((part for part in links if target == part or target.startswith(part + "/")), None)
            if prefix is None:
                break
            if prefix in visited:
                raise ValueError("Cyclic NDK ZIP symlink")
            visited.add(prefix)
            target = links[prefix] + target[len(prefix):]
        confined(tools, target)
    for row in rows:
        name, kind = row["name"], row["kind"]
        if any(name.startswith(link + "/") for link in links):
            raise ValueError("ZIP member beneath a symlink: " + name)
        path = confined(tools, name, follow_leaf=kind != "symlink")
        if path.is_symlink():
            if kind != "symlink" or os.readlink(path) != row["target"]:
                raise ValueError("Existing NDK symlink differs; preserved")
            confined(tools, name)
        elif path.exists():
            if kind == "directory":
                if not path.is_dir():
                    raise ValueError("Existing NDK directory differs; preserved")
            elif not path.is_file():
                raise ValueError("Existing NDK file differs; preserved")
            elif kind == "symlink":
                if path.read_bytes() != row["target_bytes"]:
                    raise ValueError("Existing NDK symlink placeholder differs; preserved")
            else:
                with archive.open(row["info"]) as incoming, path.open("rb") as existing:
                    if path.stat().st_size != row["info"].file_size or stream_digest(incoming) != stream_digest(existing):
                        raise ValueError("Existing NDK bytes differ; preserved: " + name)
        elif kind == "file" and not extract:
            raise ValueError("NDK file missing; rerun with --extract: " + name)
    return rows


def repair(archive_path, tools, extract=False):
    tools = Path(tools)
    if tools.is_symlink():
        raise ValueError("The project .tools directory must not be a symlink")
    tools = tools.resolve()
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_relative_to(tools):
        raise ValueError("NDK archive must be inside project .tools")
    with zipfile.ZipFile(archive_path) as archive:
        rows = inspect_archive(archive, tools, extract)
        for row in rows:
            path = confined(tools, row["name"], follow_leaf=row["kind"] != "symlink")
            if row["kind"] == "directory":
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if row["kind"] == "symlink":
                    if path.is_symlink():
                        continue
                    if path.exists():
                        # Only the exact original ZIP symlink placeholder may
                        # be converted; no arbitrary existing file is removed.
                        if path.read_bytes() != row["target_bytes"]:
                            raise ValueError("NDK placeholder changed during repair; preserved")
                        temporary = path.with_name("." + path.name + ".link-" + uuid.uuid4().hex)
                        try:
                            # Creating the link first preserves the placeholder
                            # if Windows denies symlink creation.
                            temporary.symlink_to(row["target"])
                            if path.is_symlink() or path.read_bytes() != row["target_bytes"]:
                                raise ValueError("NDK placeholder changed during repair; preserved")
                            os.replace(temporary, path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    else:
                        path.symlink_to(row["target"])
                    continue
                if not path.exists():
                    temporary = path.with_name("." + path.name + ".extract-" + uuid.uuid4().hex)
                    try:
                        with archive.open(row["info"]) as incoming, temporary.open("xb") as outgoing:
                            while block := incoming.read(1024 * 1024):
                                outgoing.write(block)
                        os.link(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
            if row["mode"]:
                os.chmod(path, row["mode"])
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / ".tools/android-ndk-r27d-linux.zip")
    parser.add_argument("--extract", action="store_true", help="Create missing archive files after complete validation")
    args = parser.parse_args(argv)
    try:
        count = repair(args.archive, ROOT / ".tools", args.extract)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, "NDK verification failed: " + str(error) + "\n")
    print("NDK symlinks and modes verified/restored:", count)


if __name__ == "__main__":
    main()
