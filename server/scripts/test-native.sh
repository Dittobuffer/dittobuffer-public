#!/bin/sh
# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export CARGO_HOME="$ROOT/.tools/cargo"
export RUSTUP_HOME="$ROOT/.tools/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_TARGET_DIR="$ROOT/client/native/target"
CHANNEL="$(python3 -c 'import pathlib,sys,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["toolchain"]["channel"])' "$ROOT/client/native/rust-toolchain.toml")"
if test -z "${PROTOC:-}"; then
  if test -x "$ROOT/.tools/protoc/bin/protoc"; then
    PROTOC="$ROOT/.tools/protoc/bin/protoc"
  else
    PROTOC="$(command -v protoc || true)"
  fi
fi
if test -z "$PROTOC" || ! test -x "$PROTOC"; then
  printf '%s\n' 'Missing protoc: provide an executable PROTOC or install protoc on PATH.' >&2
  exit 1
fi
export PROTOC
cd "$ROOT/client/native"
cargo "+$CHANNEL" test --locked --target x86_64-unknown-linux-gnu --lib
