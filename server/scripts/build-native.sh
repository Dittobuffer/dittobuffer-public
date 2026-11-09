#!/bin/sh
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
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$ROOT/.tools/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android28-clang"
export CC_aarch64_linux_android="$CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"
export AR_aarch64_linux_android="$ROOT/.tools/android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-ar"
if ! test -x "$CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"; then
  printf '%s\n' 'Missing pinned NDK linker: run scripts/setup-native.sh first.' >&2
  exit 1
fi
cd "$ROOT/client/native"
cargo "+$CHANNEL" build --locked --release --target aarch64-linux-android
printf '%s\n' "$CARGO_TARGET_DIR/aarch64-linux-android/release/libNianticLabsPlugin.so"
