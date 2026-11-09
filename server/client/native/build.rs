//! Generates the dev-channel message types from `tools/devrpc.proto`.
//!
//! Only that one file is compiled, and that is the point of it: `tools/vbase.proto` holds the
//! game's real payload schemas and belongs to the dev server alone. The stub never parses a
//! body, so it needs no schema for one.
//!
//! `prost-build` needs `protoc` on the host at build time (it does not run on the target).
//! That is a build-time-only dependency, so it does not follow the library onto the device.

fn main() {
    let proto = "devrpc.proto";
    println!("cargo:rerun-if-changed={proto}");

    prost_build::Config::new()
        .compile_protos(&[proto], &["."])
        .expect("compile tools/devrpc.proto -- is `protoc` on PATH?");
}
