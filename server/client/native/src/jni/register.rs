//! `RegisterNatives` for the 19 methods under `com.nianticlabs.nia.*`.
//!
//! The original drives this from `nativeInit` and does not export `JNI_OnLoad`
//! (notes/02). The stub exports one as well, so registration can also happen at
//! `loadLibrary` time; both entry points funnel through [`register_all`], and the
//! table below is the single source of truth for the JNI surface.
//!
//! Re-registering is harmless — `RegisterNatives` overwrites — so the only reason for
//! the `REGISTERED` flag is to keep `nativeInit` from repeating eight log lines.

use core::ffi::{c_char, c_void};
use core::sync::atomic::{AtomicBool, Ordering};
use jni_sys::{jint, JNIEnv, JNINativeMethod};
use std::ffi::CStr;

use super::natives;
use crate::log;

/// Set once every class has registered. A partial failure leaves it clear so the next
/// `nativeInit` retries the whole table.
static REGISTERED: AtomicBool = AtomicBool::new(false);

/// Build one `JNINativeMethod` row.
///
/// The JVM reads `name` and `signature` as NUL-terminated C strings, and a `&'static
/// str` has no implicit terminator — hence the explicit `\0` in the `concat!`. The
/// literals are `'static`, so the pointers stay valid for the process lifetime, which
/// is what lets the tables be ordinary stack locals rather than late-initialised statics.
macro_rules! method {
    ($name:literal, $signature:literal, $entry:path) => {
        JNINativeMethod {
            name: concat!($name, "\0").as_ptr() as *mut c_char,
            signature: concat!($signature, "\0").as_ptr() as *mut c_char,
            fnPtr: $entry as *mut c_void,
        }
    };
}

/// Register every method of one class.
///
/// Returns how many were registered: `0` on `FindClass` failure, otherwise
/// `methods.len()` if `RegisterNatives` succeeded.
///
/// # Safety
/// `env` must be a valid `JNIEnv*`.
unsafe fn register_class(env: *mut JNIEnv, class: &CStr, methods: &[JNINativeMethod]) -> usize {
    let name = class.to_string_lossy();
    let cls = super::find_class(env, class.as_ptr());
    if cls.is_null() {
        // FindClass throws NoClassDefFoundError. JNI is undefined while an exception is
        // pending, so clear it before the next class is looked up.
        super::clear_exception(env);
        log::info(&format!("JNI FindClass FAILED {name}"));
        return 0;
    }

    let count = methods.len() as jint;
    let ok = super::register_natives(env, cls, methods.as_ptr(), count);
    super::delete_local_ref(env, cls);

    if ok {
        log::info(&format!("JNI {name}: {count} registered"));
        methods.len()
    } else {
        super::clear_exception(env);
        log::info(&format!("JNI RegisterNatives FAILED {name}"));
        0
    }
}

/// The JNI surface: every class and method [`register_all`] binds.
///
/// Built per call rather than held in a `static`, because `JNINativeMethod` holds raw
/// pointers and so is not `Sync`. The name and signature strings are `'static` literals,
/// so the only per-call cost is the `Vec` itself — irrelevant for something that runs
/// once per process.
///
/// Kept as a separate function so the tests can walk the table without a live JVM.
fn surface() -> Vec<(&'static CStr, Vec<JNINativeMethod>)> {
    vec![
        (
            c"com/nianticlabs/nia/contextservice/ContextService",
            vec![method!(
                "setActivityProviderClass",
                "(Ljava/lang/String;)V",
                natives::set_activity_provider_class
            )],
        ),
        (
            c"com/nianticlabs/nia/location/NianticLocationManager",
            vec![
                method!(
                    "nativeAddLocationProviders",
                    "(Landroid/content/Context;)V",
                    natives::native_add_location_providers
                ),
                method!(
                    "nativeGpsStatusUpdate",
                    "(I[Lcom/nianticlabs/nia/location/SatelliteInfo;)V",
                    natives::native_gps_status_update
                ),
                method!(
                    "nativeLocationUpdate",
                    "(Ljava/lang/String;Landroid/location/Location;ILandroid/content/Context;)V",
                    natives::native_location_update
                ),
            ],
        ),
        (
            c"com/nianticlabs/nia/sensors/NianticSensorManager",
            vec![
                method!(
                    "nativeCompassUpdate",
                    "(JF)V",
                    natives::native_compass_update
                ),
                method!(
                    "nativeSensorUpdate",
                    "(IJ[F)V",
                    natives::native_sensor_update
                ),
            ],
        ),
        (
            c"com/nianticlabs/nia/network/WebsocketController",
            vec![
                method!(
                    "nativeOnDidReceiveData",
                    "(Ljava/nio/ByteBuffer;I)V",
                    natives::native_on_did_receive_data
                ),
                method!("nativeOnDidOpen", "()V", natives::native_on_did_open),
                method!("nativeOnDidFail", "(Z)V", natives::native_on_did_fail),
                method!("nativeOnDidClose", "(I)V", natives::native_on_did_close),
            ],
        ),
        (
            c"com/nianticlabs/nia/network/NianticTrustManager",
            vec![
                method!(
                    "nativeGetAcceptedIssuers",
                    "()[Ljava/security/cert/X509Certificate;",
                    natives::native_get_accepted_issuers
                ),
                method!(
                    "nativeCheckServerTrusted",
                    "([Ljava/security/cert/X509Certificate;Ljava/lang/String;)V",
                    natives::native_check_server_trusted
                ),
                method!(
                    "nativeCheckClientTrusted",
                    "([Ljava/security/cert/X509Certificate;Ljava/lang/String;)V",
                    natives::native_check_client_trusted
                ),
            ],
        ),
        (
            c"com/nianticlabs/nia/network/NetworkConnectivity",
            vec![method!(
                "nativeNotifyNetworkStateChanged",
                "(Z)V",
                natives::native_notify_network_state_changed
            )],
        ),
        (
            c"com/nianticlabs/nia/log/NLog",
            vec![method!(
                "nativeDispatchLogMessage",
                "(ILjava/lang/String;Ljava/lang/String;)V",
                natives::native_dispatch_log_message
            )],
        ),
        (
            c"com/nianticlabs/nia/platform/PlayIntegrityService",
            vec![
                method!(
                    "nativeAttestResponse",
                    "(Ljava/lang/String;Ljava/lang/String;ZILjava/lang/String;)V",
                    natives::native_attest_response
                ),
                method!(
                    "nativeGetApiKey",
                    "()Ljava/lang/String;",
                    natives::native_get_api_key
                ),
                method!(
                    "nativeGetGCPProjectNumber",
                    "()Ljava/lang/String;",
                    natives::native_get_gcp_project_number
                ),
            ],
        ),
        (
            c"com/nianticlabs/nia/unity/UnityUtil",
            vec![method!("nativeInit", "()V", natives::native_init)],
        ),
    ]
}

/// Register all 19 natives across the 9 classes.
///
/// Idempotent, and safe on a null `env`. Every failure path clears the exception it
/// caused, because leaving one pending from `JNI_OnLoad` makes `System.loadLibrary`
/// throw.
///
/// # Safety
/// `env` must be null or a valid `JNIEnv*`.
pub unsafe fn register_all(env: *mut JNIEnv) {
    if REGISTERED.load(Ordering::Acquire) {
        return;
    }
    if env.is_null() {
        log::info("JNI registration skipped: null JNIEnv");
        return;
    }

    let surface = surface();
    let mut registered = 0usize;
    let mut total = 0usize;
    for (class, methods) in &surface {
        registered += register_class(env, class, methods);
        total += methods.len();
    }

    if registered == total {
        REGISTERED.store(true, Ordering::Release);
        log::info(&format!(
            "JNI registration complete: {registered}/{total} methods in {} classes",
            surface.len()
        ));
    } else {
        log::info(&format!(
            "JNI registration incomplete: {registered}/{total} methods; retrying on next nativeInit"
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The JNI surface transcribed from the 0.429 smali (`notes/04-jni-surface.md`), as
    /// `(class, [(method, signature)])`.
    ///
    /// Deliberately typed as plain text, separately from the `method!` invocations it
    /// checks. The two copies came from the same transcript but not from each other, so
    /// a transcription slip in either shows up here. Worth the duplication because a
    /// wrong signature registers *successfully* and only surfaces later as an
    /// `UnsatisfiedLinkError` at the first call — the far end of the boot sequence from
    /// anywhere that would explain it.
    const EXPECTED: [(&str, &[(&str, &str)]); 9] = [
        (
            "com/nianticlabs/nia/contextservice/ContextService",
            &[("setActivityProviderClass", "(Ljava/lang/String;)V")],
        ),
        (
            "com/nianticlabs/nia/location/NianticLocationManager",
            &[
                ("nativeAddLocationProviders", "(Landroid/content/Context;)V"),
                (
                    "nativeGpsStatusUpdate",
                    "(I[Lcom/nianticlabs/nia/location/SatelliteInfo;)V",
                ),
                (
                    "nativeLocationUpdate",
                    "(Ljava/lang/String;Landroid/location/Location;ILandroid/content/Context;)V",
                ),
            ],
        ),
        (
            "com/nianticlabs/nia/sensors/NianticSensorManager",
            &[
                ("nativeCompassUpdate", "(JF)V"),
                ("nativeSensorUpdate", "(IJ[F)V"),
            ],
        ),
        (
            "com/nianticlabs/nia/network/WebsocketController",
            &[
                ("nativeOnDidReceiveData", "(Ljava/nio/ByteBuffer;I)V"),
                ("nativeOnDidOpen", "()V"),
                ("nativeOnDidFail", "(Z)V"),
                ("nativeOnDidClose", "(I)V"),
            ],
        ),
        (
            "com/nianticlabs/nia/network/NianticTrustManager",
            &[
                ("nativeGetAcceptedIssuers", "()[Ljava/security/cert/X509Certificate;"),
                (
                    "nativeCheckServerTrusted",
                    "([Ljava/security/cert/X509Certificate;Ljava/lang/String;)V",
                ),
                (
                    "nativeCheckClientTrusted",
                    "([Ljava/security/cert/X509Certificate;Ljava/lang/String;)V",
                ),
            ],
        ),
        (
            "com/nianticlabs/nia/network/NetworkConnectivity",
            &[("nativeNotifyNetworkStateChanged", "(Z)V")],
        ),
        (
            "com/nianticlabs/nia/log/NLog",
            &[(
                "nativeDispatchLogMessage",
                "(ILjava/lang/String;Ljava/lang/String;)V",
            )],
        ),
        (
            "com/nianticlabs/nia/platform/PlayIntegrityService",
            &[
                (
                    "nativeAttestResponse",
                    "(Ljava/lang/String;Ljava/lang/String;ZILjava/lang/String;)V",
                ),
                ("nativeGetApiKey", "()Ljava/lang/String;"),
                ("nativeGetGCPProjectNumber", "()Ljava/lang/String;"),
            ],
        ),
        (
            "com/nianticlabs/nia/unity/UnityUtil",
            &[("nativeInit", "()V")],
        ),
    ];

    /// `*mut c_char` to `String`. Test-only; production code never reads these back.
    unsafe fn text(p: *mut c_char) -> String {
        std::ffi::CStr::from_ptr(p).to_string_lossy().into_owned()
    }

    #[test]
    fn surface_matches_the_smali_transcript() {
        let actual = surface();
        assert_eq!(
            actual.len(),
            EXPECTED.len(),
            "class count differs from notes/04"
        );

        for ((class, methods), (expected_class, expected_methods)) in actual.iter().zip(EXPECTED) {
            assert_eq!(class.to_str().expect("ASCII class name"), expected_class);
            assert_eq!(
                methods.len(),
                expected_methods.len(),
                "{expected_class}: method count"
            );

            for (method, (expected_name, expected_signature)) in methods.iter().zip(expected_methods)
            {
                unsafe {
                    // `method!` appends the NUL the JVM requires; strip it back off for
                    // comparison so EXPECTED stays readable as plain descriptors.
                    let name = text(method.name);
                    let signature = text(method.signature);
                    assert_eq!(name.trim_end_matches('\0'), *expected_name);
                    assert_eq!(signature.trim_end_matches('\0'), *expected_signature);
                }
                assert!(!method.fnPtr.is_null(), "{expected_name} has a null entry point");
            }
        }
    }

    /// 19 total, as `notes/04-jni-surface.md` records, counted from the *expected*
    /// transcript rather than from the table being tested.
    #[test]
    fn transcript_covers_nineteen_methods() {
        assert_eq!(EXPECTED.iter().map(|(_, m)| m.len()).sum::<usize>(), 19);
    }

    /// A class may not carry the same method twice: the second row would silently win.
    #[test]
    fn surface_has_no_duplicate_entries() {
        let mut seen = std::collections::HashSet::new();
        for (class, methods) in surface() {
            for method in methods {
                let key = unsafe { (class.to_string_lossy().into_owned(), text(method.name)) };
                assert!(seen.insert(key.clone()), "duplicate JNI entry {key:?}");
            }
        }
    }

    /// `REGISTERED` is process-global, so the tests below that drive `register_all`
    /// have to serialise and reset it.
    ///
    /// The lock is the harness's rather than one private to this module:
    /// [`register_all`] drives the fake JVM, and this test asserts
    /// [`crate::jni::fake_jni::unexpected_calls`] is zero — a claim about the whole
    /// process, which a private mutex would let other modules falsify. See
    /// [`crate::jni::fake_jni::test_guard`].
    fn exclusive() -> std::sync::MutexGuard<'static, ()> {
        let guard = crate::jni::fake_jni::test_guard();
        REGISTERED.store(false, Ordering::Release);
        crate::jni::fake_jni::reset();
        guard
    }

    /// The end-to-end check of the registration path, against the fake JVM.
    ///
    /// This exercises the real `register_all`: its `FindClass` calls, the method table
    /// it hands to `RegisterNatives`, and the local references it releases. What it
    /// cannot check is whether the JVM accepts the descriptors — only a device can say
    /// that — but it does prove the descriptors arrive intact and terminated.
    #[test]
    fn register_all_binds_the_full_surface() {
        let _guard = exclusive();
        let jni = crate::jni::fake_jni::table();
        unsafe { register_all(jni.env) };

        let (found, rows) = crate::jni::fake_jni::with_recorder(|r| {
            assert_eq!(r.exception_clear, 0, "no exception should have been raised");
            assert_eq!(r.exception_check, 0);
            (r.find_class.clone(), r.registered.clone())
        });

        assert_eq!(found.len(), 9, "every class should have been resolved");
        assert_eq!(rows.len(), 9, "every class should have been registered");
        assert_eq!(
            rows.iter().map(Vec::len).sum::<usize>(),
            19,
            "19 natives, per notes/04"
        );
        assert_eq!(
            crate::jni::fake_jni::unexpected_calls(),
            0,
            "a JNI call reached a slot the harness does not model"
        );
        assert!(REGISTERED.load(Ordering::Acquire));

        // Each row, in registration order, with a live entry point and a resolved class.
        for ((class, expected), actual) in EXPECTED.iter().zip(&rows) {
            assert!(found.iter().any(|f| f.as_str() == *class), "{class} never resolved");
            assert_eq!(actual.len(), expected.len(), "{class}");
            for ((name, signature, entry), (want_name, want_sig)) in
                actual.iter().zip(expected.iter())
            {
                assert_eq!(name.as_str(), *want_name);
                assert_eq!(signature.as_str(), *want_sig);
                assert!(!entry.is_null(), "{class}::{name} has a null entry point");
            }
        }

        // Every resolved class is released, and nothing else is.
        let released = crate::jni::fake_jni::with_recorder(|r| r.delete_local_ref);
        assert_eq!(released, 9);
    }

    /// `nativeInit` is reachable twice — the JVM calls the mangled export and
    /// `RegisterNatives` routes to the same body — so the second call must be free.
    #[test]
    fn register_all_is_idempotent() {
        let _guard = exclusive();
        let jni = crate::jni::fake_jni::table();
        unsafe { register_all(jni.env) };
        unsafe { register_all(jni.env) };

        let calls = crate::jni::fake_jni::with_recorder(|r| r.registered.len());
        assert_eq!(calls, 9, "second call should not re-register");
    }

    /// A class that will not resolve must not abort the other eight, and must not leave
    /// the exception pending — from `JNI_OnLoad` that would make `loadLibrary` throw.
    #[test]
    fn a_missing_class_is_cleared_and_does_not_stop_the_rest() {
        let _guard = exclusive();
        let jni = crate::jni::fake_jni::table();
        crate::jni::fake_jni::with_recorder(|r| {
            r.missing
                .push("com/nianticlabs/nia/network/NianticTrustManager".to_owned());
        });

        unsafe { register_all(jni.env) };

        crate::jni::fake_jni::with_recorder(|r| {
            assert_eq!(r.registered.len(), 8, "the other eight still register");
            assert_eq!(r.exception_clear, 1, "the pending exception is cleared");
            assert!(!r.pending_exception);
        });
        assert!(
            !REGISTERED.load(Ordering::Acquire),
            "a partial surface must be retried"
        );
    }

    /// A `RegisterNatives` failure is reported the same way as a `FindClass` failure.
    #[test]
    fn a_failed_registration_is_cleared_too() {
        let _guard = exclusive();
        let jni = crate::jni::fake_jni::table();
        crate::jni::fake_jni::with_recorder(|r| r.fail_register = true);

        unsafe { register_all(jni.env) };

        crate::jni::fake_jni::with_recorder(|r| {
            assert!(r.registered.is_empty());
            assert_eq!(r.exception_clear, 9);
        });
        assert!(!REGISTERED.load(Ordering::Acquire));
    }

    /// Registering with a null env must be a no-op, not a crash: the exported
    /// `nativeInit` symbol can be called by anything that resolves it.
    #[test]
    fn register_all_tolerates_null_env() {
        let _guard = exclusive();
        unsafe { register_all(core::ptr::null_mut()) };
        assert!(!REGISTERED.load(Ordering::Acquire));
        assert_eq!(
            crate::jni::fake_jni::with_recorder(|r| r.registered.len()),
            0
        );
    }
}
