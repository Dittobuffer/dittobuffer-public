#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.tools"
export CARGO_HOME="$ROOT/.tools/cargo"
export RUSTUP_HOME="$ROOT/.tools/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
CHANNEL="$(python3 -c 'import pathlib,sys,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["toolchain"]["channel"])' "$ROOT/client/native/rust-toolchain.toml")"
if ! test -x "$CARGO_HOME/bin/rustup"; then
  curl --fail --location --proto '=https' --tlsv1.2 https://sh.rustup.rs -o "$ROOT/.tools/rustup-init.sh"
  sh "$ROOT/.tools/rustup-init.sh" -y --no-modify-path --profile minimal --default-toolchain "$CHANNEL"
fi
rustup toolchain install "$CHANNEL" --profile minimal --target aarch64-linux-android --no-self-update
if ! test -f "$ROOT/.tools/android-ndk-r27d-linux.zip"; then
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 https://dl.google.com/android/repository/android-ndk-r27d-linux.zip -o "$ROOT/.tools/android-ndk-r27d-linux.zip"
fi
# Validate every path, symlink and existing byte before extracting or repairing.
python3 "$ROOT/scripts/fix-ndk-modes.py" --extract
rustc "+$CHANNEL" --version
printf '%s\n' 'Rust and NDK setup complete. Supply protoc on PATH or through PROTOC before building.'
