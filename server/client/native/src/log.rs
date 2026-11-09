//! Logging.
//!
//! `println!` does not reach logcat on Android, so on-device diagnostics go through
//! `__android_log_print` with a fixed, greppable tag. On the host (for the ABI tests)
//! they fall back to stderr.

/// Greppable tag for `adb logcat | grep Dittobuffer`.
///
/// The `Dittobuffer` spelling matches the `x-dittobuffer-*` headers the stub sends, so the name is
/// the same on both sides of the wire.
pub const TAG: &str = "Dittobuffer";

/// Android priorities from `android/log.h`. Note these are *not* the `LogLevelInternal`
/// values — see `crate::jni::natives::android_priority`.
pub const PRIO_INFO: i32 = 4;

#[cfg(target_os = "android")]
mod imp {
    use core::ffi::{c_char, c_int};
    use std::ffi::CString;

    #[link(name = "log")]
    extern "C" {
        fn __android_log_print(prio: c_int, tag: *const c_char, fmt: *const c_char, ...) -> c_int;
    }

    // A literal format string, so a message containing '%' cannot be interpreted as a
    // format directive.
    const FMT: &[u8] = b"%s\0";

    pub fn log_to(prio: i32, tag: &str, msg: &str) {
        // CString::new rejects interior NULs; drop the message rather than panicking.
        let (Ok(t), Ok(m)) = (CString::new(tag), CString::new(msg)) else {
            return;
        };
        unsafe {
            __android_log_print(prio, t.as_ptr(), FMT.as_ptr() as *const c_char, m.as_ptr());
        }
    }
}

#[cfg(not(target_os = "android"))]
mod imp {
    pub fn log_to(prio: i32, tag: &str, msg: &str) {
        eprintln!("[{tag}] ({prio}) {msg}");
    }
}

pub fn info(msg: &str) {
    imp::log_to(PRIO_INFO, TAG, msg);
}

/// Log at an explicit priority under a caller-supplied tag.
///
/// `NLog.nativeDispatchLogMessage` uses this so the managed `com.nianticlabs.nia.*`
/// logging keeps its own tag and level instead of being folded into ours.
pub fn at(prio: i32, tag: &str, msg: &str) {
    imp::log_to(prio, tag, msg);
}

/// `N2PluginAddSnitchLog` — `tag`/`group` come from the managed `LogApi`.
pub fn snitch(tag: &str, group: &str, level: &str, msg: &str) {
    imp::log_to(PRIO_INFO, TAG, &format!("snitch[{level}] {tag}/{group}: {msg}"));
}
