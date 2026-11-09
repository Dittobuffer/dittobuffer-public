//! The 19 native methods under `com.nianticlabs.nia.*`, registered by
//! [`register::register_all`](super::register::register_all).
//!
//! Every body is a stub. The contract that matters for the drop-in test is that the JVM
//! finds and calls each one; none of them throws, blocks, or dereferences an argument.
//!
//! Each service holds a `long nativeClassPointer` — a raw `this` from the real native
//! side (notes/06). A stub has no such object, so nothing here reads it: the methods
//! that would need it are exactly the methods that do no work.
//!
//! # What is deliberately missing
//!
//! * `nativeCheckServerTrusted` / `nativeCheckClientTrusted` accept every chain. The
//!   real plugin rejects chains that fail its pinning check; the stub has no pinning
//!   data (and no networking — every server entry point returns `NotImplementedYet`),
//!   so there is nothing to check against.
//! * Play Integrity is inert: [`native_get_gcp_project_number`] returns `"0"` and
//!   [`native_get_api_key`] returns null, so attestation cannot succeed.

use core::ptr;
use jni_sys::{
    jboolean, jclass, jfloat, jfloatArray, jint, jlong, jmethodID, jobject, jobjectArray,
    jstring, JNIEnv,
};

use super::{
    call_double_method, call_float_method, call_long_method, clear_exception, delete_local_ref,
    find_class, get_method_id, string_from_java,
};
use crate::abi::{
    location_payload, CompassHeadingData, EventSlot, LocationData, LogLevelInternal,
    LOCATION_TYPE_MEASURED,
};
use crate::jni::location_service;
use crate::live;
use crate::log;
use crate::server;

// ---------------------------------------------------------------------------
// contextservice/ContextService
// ---------------------------------------------------------------------------

/// `ContextService.setActivityProviderClass(String)` — static.
///
/// `ContextService` is the superclass of all six services, and this is the only method
/// it declares. Nothing in the 0.429 smali calls it; it is registered because it is
/// `native` and so must resolve if it ever is called.
///
/// # Safety
/// JNI entry point; `provider` is a `jstring`.
pub unsafe extern "system" fn set_activity_provider_class(
    _env: *mut JNIEnv,
    _clazz: jclass,
    _provider: jstring,
) {
    log::info("ContextService.setActivityProviderClass");
}

// ---------------------------------------------------------------------------
// location/NianticLocationManager
// ---------------------------------------------------------------------------

/// `NianticLocationManager.nativeAddLocationProviders(Context)`.
///
/// The link that was missing. `onStart` reaches this to ask the native side for the providers
/// it should manage, and the real library answered by constructing them; the stub used to log
/// and return, so the manager's `providers` map stayed empty and nothing downstream —
/// `requestLocationUpdates`, the listener, and finally [`native_location_update`] — ever ran.
///
/// `this` **is** the manager, and it is used as the receiver rather than the global reference
/// in [`location_service`]: this body runs re-entrantly, from inside the `onStart` call that
/// created it, so the global may not be published yet.
///
/// Both providers are constructed, and a failure in either is logged and skipped rather than
/// aborting the other: `"fused"` is the one that produces a fix indoors, but it depends on
/// Google Play Services and can fail on a device where `"gps"` works fine.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_add_location_providers(
    env: *mut JNIEnv,
    this: jobject,
    context: jobject,
) {
    // `"gps"` first. Both providers report a name and `addLocationProvider` keys its map on
    // it, so the order decides which one wins if the names ever collide — and the platform
    // provider is the one that cannot fail on construction.
    location_service::add_provider(env, this, context, location_service::GPS_PROVIDER_CLASS);
    location_service::add_provider(env, this, context, location_service::FUSED_PROVIDER_CLASS);
}

/// `NianticLocationManager.nativeGpsStatusUpdate(int, SatelliteInfo[])`.
///
/// Hot path. Silent by design — see the note on [`native_location_update`].
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_gps_status_update(
    _env: *mut JNIEnv,
    _this: jobject,
    _status: jint,
    _satellites: jobjectArray,
) {
}

/// `NianticLocationManager.nativeLocationUpdate(String, Location, int, Context)`.
///
/// Builds a [`LocationData`] from the Android `Location` and records it for
/// `N2MapGetCurrentLocation` to read back. The fix lands in a process-global slot rather
/// than in a `Context` — see [`crate::live`].
///
/// This is the entry point the whole location path hangs from, and it was entered zero
/// times until [`crate::jni::location_service`] started constructing the manager that
/// calls it.
///
/// It carries **two** signals, not one. Java reports the position and the provider's status
/// through this same method but never on the same call, so the null-`Location` branch below is
/// not a degenerate case to skip past — it is half the protocol, and skipping it is what left
/// the game reading `Status:Undefined` on a correct position. See [`live::set_status`].
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_location_update(
    env: *mut JNIEnv,
    _this: jobject,
    provider: jstring,
    location: jobject,
    status: jint,
    _context: jobject,
) {
    // Java sends its two halves through this one native and never together: a status arrives on
    // a call with a null `Location`, and a fix arrives without one. See [`live::set_status`]
    // for the sequence. Returning early here — which is what this body used to do — throws the
    // status away, and every fix reaches the game as `UNDEFINED` even though its coordinates
    // are perfect.
    if location.is_null() {
        let status = status.max(0) as u32;
        // Only a transition is worth a line. There are a handful per provider per session —
        // registered, initialised, running, stopped — and this is the one place the status is
        // ever visible, since the call that carries a fix always reports `UNDEFINED`.
        let provider = string_from_java(env, provider).unwrap_or_else(|| "<null>".to_owned());
        if live::set_provider_status(&provider, status) {
            log::info(&format!(
                "nativeLocationUpdate: provider {provider:?} status -> {status}"
            ));
        }
        return;
    }
    let Some(fix) = read_location(env, location, status) else {
        // Diagnosed inside; the exception a failed lookup left behind has been cleared,
        // and the previously recorded fix (if any) is deliberately left intact — a bad
        // sample should not erase a good one.
        return;
    };
    live::set_location(fix);
    // Re-read: `set_location` splices in the status that arrived on its own call, so the
    // payload carries the merged value rather than the `UNDEFINED` this call was handed.
    let Some(fix) = live::location() else {
        return;
    };
    // A fix arriving *is* the `locationUpdate` event — the same one `debug::set_device_location`
    // raises, and the whole reason `DeviceManager.NotifyLocationUpdate` exists. Recording the
    // fix without raising this is what left the game's location pipeline silent: it polls
    // `GetCurrentLocation` once at boot and never again, because it expects to be *told*.
    //
    // Queued, not fired: this body runs on a Java service thread, and a delegate may only be
    // invoked on a thread the game called us on. `OnUpdate` drains the queue on that thread.
    server::raise_detached(EventSlot::LocationUpdate, location_payload(&fix));

    if live::claim_first_location_log() {
        // Only on the first fix: this fires at the location rate. Reading the provider
        // name costs another JNI call, so it is paid once, and it answers the question
        // the smoke test actually asks — which provider is delivering.
        let provider = string_from_java(env, provider).unwrap_or_else(|| "<null>".to_owned());
        log::info(&format!(
            "nativeLocationUpdate: first fix {:.6},{:.6} +/-{:.1}m t={:.3} from {provider:?} (provider_status {})",
            fix.latitude_deg,
            fix.longitude_deg,
            fix.accuracy_m,
            fix.timestamp_s,
            fix.provider_status,
        ));
    }
}

/// Read the five `android.location.Location` getters this native needs.
///
/// `Location` is a **framework** class, so its getter names and signatures are stable and
/// R8 cannot rename them — unlike the `com.nianticlabs.nia.*` classes, which is why the
/// rest of this crate goes to such lengths to keep names and descriptors in step with the
/// smali.
///
/// The methods are resolved per fix rather than cached in a `static`. This runs about once
/// a second, so the cost is irrelevant, and a cache would add global mutable state plus a
/// "when is a `jmethodID` invalidated" question for no measurable gain.
///
/// # Safety
/// JNI entry point; `env` must be the calling thread's, `location` a `Location` or null.
unsafe fn read_location(
    env: *mut JNIEnv,
    location: jobject,
    status: jint,
) -> Option<LocationData> {
    if location.is_null() {
        return None;
    }
    let class = find_class(env, c"android/location/Location".as_ptr());
    if class.is_null() {
        // FindClass threw; clear before touching JNI again.
        clear_exception(env);
        log::info("nativeLocationUpdate: FindClass(android/location/Location) failed");
        return None;
    }
    let data = read_location_ok(env, class, location, status);
    delete_local_ref(env, class);
    data
}

/// The body of [`read_location`] once the class is in hand and the local ref will be
/// released by the caller.
///
/// # Safety
/// `class` must be the resolved `android/location/Location`.
unsafe fn read_location_ok(
    env: *mut JNIEnv,
    class: jclass,
    location: jobject,
    status: jint,
) -> Option<LocationData> {
    let latitude = getter(env, class, c"getLatitude", c"()D")?;
    let longitude = getter(env, class, c"getLongitude", c"()D")?;
    let altitude = getter(env, class, c"getAltitude", c"()D")?;
    let accuracy = getter(env, class, c"getAccuracy", c"()F")?;
    let time = getter(env, class, c"getTime", c"()J")?;

    Some(location_data_from(
        call_double_method(env, location, latitude),
        call_double_method(env, location, longitude),
        call_double_method(env, location, altitude),
        call_float_method(env, location, accuracy),
        call_long_method(env, location, time),
        status,
    ))
}

/// Resolve one method, clearing the `NoSuchMethodError` a failure throws.
///
/// `?` alone would bail out with a pending exception, and JNI is undefined while one is
/// pending — so every failure path goes through here.
///
/// # Safety
/// `class` must be a valid class reference and `env` the calling thread's.
unsafe fn getter(
    env: *mut JNIEnv,
    class: jclass,
    name: &core::ffi::CStr,
    signature: &core::ffi::CStr,
) -> Option<jmethodID> {
    let id = get_method_id(env, class, name.as_ptr(), signature.as_ptr());
    if id.is_null() {
        clear_exception(env);
        log::info(&format!(
            "nativeLocationUpdate: no method {}{}",
            name.to_string_lossy(),
            signature.to_string_lossy(),
        ));
        return None;
    }
    Some(id)
}

/// Assemble a `LocationData` from an Android `Location`'s getters.
///
/// Pure and total, so the arithmetic is unit-testable without a JVM.
///
/// The timestamp is the part worth reading twice: `Location.getTime()` is Unix
/// **milliseconds**, while `LocationData.TimestampS` is seconds — hence the division, not
/// a re-interpretation. `notes/05-marshalling.md` records how the convention was
/// established.
fn location_data_from(
    latitude_deg: f64,
    longitude_deg: f64,
    altitude_m: f64,
    accuracy_m: f32,
    time_ms: i64,
    status: i32,
) -> LocationData {
    LocationData {
        latitude_deg,
        longitude_deg,
        altitude_m,
        // `getAccuracy()` is the only float getter; the widen is exact for every `f32`.
        accuracy_m: f64::from(accuracy_m),
        timestamp_s: time_ms as f64 / 1000.0,
        // `NianticLocationManager.smali:260` calls `ServiceStatus.ordinal()` and passes the
        // result straight in, and the C# `ProviderStatus` names those same six constants in
        // that same order — so the ordinal *is* the enum and this is a copy, not a
        // translation. `max(0)` keeps a negative (unreachable via `ordinal`) from wrapping
        // into a huge `u32` on the managed side.
        provider_status: status.max(0) as u32,
        location_type: LOCATION_TYPE_MEASURED,
    }
}

// ---------------------------------------------------------------------------
// sensors/NianticSensorManager
// ---------------------------------------------------------------------------

/// `NianticSensorManager.nativeCompassUpdate(long, float)`.
///
/// Records a heading for `N2MapGetCompassHeading` to read back. Both arguments are scalars,
/// so unlike [`native_location_update`] this needs no JNI calls at all: there is no class
/// to resolve and nothing here can throw. See [`crate::live`] for where it goes.
///
/// Still unreachable, and for the same reason the location path was: nothing constructs
/// `NianticSensorManager`. Unlike the location manager there is no plan to build it — the
/// compass is not what the game is stuck on — so this stays a correct body with no caller.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_compass_update(
    _env: *mut JNIEnv,
    _this: jobject,
    timestamp_ms: jlong,
    heading_deg: jfloat,
) {
    let heading = heading_from(timestamp_ms, heading_deg);
    live::set_heading(heading);

    if live::claim_first_heading_log() {
        // The sensor rate is far higher than the frame rate, so this is gated hard.
        log::info(&format!(
            "nativeCompassUpdate: first heading {:.1} deg at t={:.3}",
            heading.heading_deg, heading.timestamp_s,
        ));
    }
}

/// Assemble a `CompassHeadingData` from the sensor callback's two scalars.
///
/// Same millisecond-to-second convention as [`location_data_from`], and established the
/// same way: `onSensorChanged` calls `System.currentTimeMillis()`
/// (`NianticSensorManager.smali:1198`), which reaches `nativeCompassUpdate` unmodified
/// through the `lastUpdateTimeMs` field (`:993`, its only write).
fn heading_from(timestamp_ms: i64, heading_deg: f32) -> CompassHeadingData {
    CompassHeadingData {
        heading_deg: f64::from(heading_deg),
        timestamp_s: timestamp_ms as f64 / 1000.0,
    }
}

/// `NianticSensorManager.nativeSensorUpdate(int, long, float[])` — hot path, silent.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_sensor_update(
    _env: *mut JNIEnv,
    _this: jobject,
    _sensor_type: jint,
    _timestamp_ms: jlong,
    _values: jfloatArray,
) {
}

// ---------------------------------------------------------------------------
// network/WebsocketController
// ---------------------------------------------------------------------------

/// `WebsocketController.nativeOnDidReceiveData(ByteBuffer, int)`.
///
/// Hot path — silent. The buffer arrives as a `java.nio.ByteBuffer`, so a real
/// implementation would need `GetDirectBufferAddress`; the stub never reads it, so it
/// never makes that call.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_on_did_receive_data(
    _env: *mut JNIEnv,
    _this: jobject,
    _buffer: jobject,
    _size: jint,
) {
}

/// `WebsocketController.nativeOnDidOpen()`.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_on_did_open(_env: *mut JNIEnv, _this: jobject) {
    log::info("WebsocketController.nativeOnDidOpen");
}

/// `WebsocketController.nativeOnDidFail(boolean)`.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_on_did_fail(
    _env: *mut JNIEnv,
    _this: jobject,
    permanent: jboolean,
) {
    log::info(&format!(
        "WebsocketController.nativeOnDidFail permanent={permanent}"
    ));
}

/// `WebsocketController.nativeOnDidClose(int)`.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_on_did_close(
    _env: *mut JNIEnv,
    _this: jobject,
    code: jint,
) {
    log::info(&format!("WebsocketController.nativeOnDidClose code={code}"));
}

// ---------------------------------------------------------------------------
// network/NianticTrustManager
// ---------------------------------------------------------------------------

/// `NianticTrustManager.nativeGetAcceptedIssuers()`.
///
/// Must not return null: this backs `X509TrustManager.getAcceptedIssuers()`, whose
/// callers iterate the result.
///
/// # Safety
/// JNI entry point; the returned array is a fresh JNI local reference.
pub unsafe extern "system" fn native_get_accepted_issuers(
    env: *mut JNIEnv,
    _this: jobject,
) -> jobjectArray {
    log::info("NianticTrustManager.nativeGetAcceptedIssuers -> empty array");
    super::empty_object_array(env, c"java/security/cert/X509Certificate".as_ptr())
}

/// `NianticTrustManager.nativeCheckServerTrusted(X509Certificate[], String)`.
///
/// Accepts every chain — see the module header. Not a hot path in a stub that never
/// opens a socket, but silent anyway so a real connection attempt stays readable.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_check_server_trusted(
    _env: *mut JNIEnv,
    _this: jobject,
    _chain: jobjectArray,
    _auth_type: jstring,
) {
}

/// `NianticTrustManager.nativeCheckClientTrusted(X509Certificate[], String)` — accepts
/// every chain, as above.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_check_client_trusted(
    _env: *mut JNIEnv,
    _this: jobject,
    _chain: jobjectArray,
    _auth_type: jstring,
) {
}

// ---------------------------------------------------------------------------
// network/NetworkConnectivity
// ---------------------------------------------------------------------------

/// `NetworkConnectivity.nativeNotifyNetworkStateChanged(boolean)`.
///
/// Silent: connectivity changes repeat for the life of the process, and the stub takes
/// no action on them.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_notify_network_state_changed(
    _env: *mut JNIEnv,
    _this: jobject,
    _connected: jboolean,
) {
}

// ---------------------------------------------------------------------------
// log/NLog
// ---------------------------------------------------------------------------

/// Android priorities from `android/log.h`.
const PRIO_VERBOSE: jint = 2;
const PRIO_WARN: jint = 5;
const PRIO_ERROR: jint = 6;
const PRIO_FATAL: jint = 7;

/// Map an `NLog$LogLevel` ordinal onto an Android priority.
///
/// The level is `NLog$LogLevel.ordinal()`, which the smali pins to exactly the
/// `LogLevelInternal` numbering (`NLog$LogLevel.smali:54-109`): OFF=0, FATAL=1, ERROR=2,
/// WARNING=3, INFO=4, VERBOSE=5. The two enums are the same enum seen from either side
/// of the bridge, so the Rust and C++ copies share values.
///
/// `OFF` means the message is filtered, hence the `Option`. An out-of-range level is
/// treated as INFO rather than dropped: a message at an unknown level is still better
/// in logcat than lost.
fn android_priority(level: jint) -> Option<jint> {
    match LogLevelInternal::from_i32(level) {
        Some(LogLevelInternal::Off) => None,
        Some(LogLevelInternal::Fatal) => Some(PRIO_FATAL),
        Some(LogLevelInternal::Error) => Some(PRIO_ERROR),
        Some(LogLevelInternal::Warning) => Some(PRIO_WARN),
        Some(LogLevelInternal::Info) => Some(log::PRIO_INFO),
        Some(LogLevelInternal::Verbose) => Some(PRIO_VERBOSE),
        None => Some(log::PRIO_INFO),
    }
}

/// `NLog.nativeDispatchLogMessage(int, String, String)` — static.
///
/// Not a no-op. `NLog.redirectLog` (`NLog.smali:162-179`) does exactly one thing: call
/// this method. Every `NLog.d/i/w/e/v` in the Java stack funnels through here, so a
/// stub that dropped the message would silence the entire managed side of the plugin,
/// which is precisely the output the on-device smoke test needs to read.
///
/// # Safety
/// JNI entry point; `tag` and `message` are `jstring`s.
pub unsafe extern "system" fn native_dispatch_log_message(
    env: *mut JNIEnv,
    _clazz: jclass,
    level: jint,
    tag: jstring,
    message: jstring,
) {
    let Some(prio) = android_priority(level) else {
        return;
    };
    // Copy both before any further JNI call: the incoming references are only valid for
    // this frame, and GetStringUTFChars results are released inside the helper.
    let Some(msg) = super::string_from_java(env, message) else {
        return;
    };
    let tag = super::string_from_java(env, tag).unwrap_or_else(|| log::TAG.to_owned());
    log::at(prio, &tag, &msg);
}

// ---------------------------------------------------------------------------
// platform/PlayIntegrityService
// ---------------------------------------------------------------------------

/// `PlayIntegrityService.nativeAttestResponse(String, String, boolean, int, String)`.
///
/// The stub never requests an attestation, so this arriving at all is worth a line.
///
/// # Safety
/// JNI entry point; `error` is a `jstring` (nullable).
pub unsafe extern "system" fn native_attest_response(
    env: *mut JNIEnv,
    _this: jobject,
    _token: jstring,
    error: jstring,
    success: jboolean,
    error_code: jint,
    _details: jstring,
) {
    let err = super::string_from_java(env, error).unwrap_or_default();
    log::info(&format!(
        "PlayIntegrityService.nativeAttestResponse success={success} errorCode={error_code} error={err:?}"
    ));
}

/// `PlayIntegrityService.nativeGetApiKey()`.
///
/// Declared `private native` but never invoked in the 0.429 smali — the only references
/// are the declaration itself. Registered anyway so it resolves if a future build calls
/// it. Null is the "no key" answer; the real key is not recoverable from the dumps.
///
/// # Safety
/// JNI entry point.
pub unsafe extern "system" fn native_get_api_key(_env: *mut JNIEnv, _this: jobject) -> jstring {
    log::info("PlayIntegrityService.nativeGetApiKey -> null");
    ptr::null_mut()
}

/// `PlayIntegrityService.nativeGetGCPProjectNumber()`.
///
/// The caller runs `Long.parseLong` on the result and feeds it to
/// `PrepareIntegrityTokenRequest.setCloudProjectNumber`
/// (`PlayIntegrityService.smali:154-159`), so the string has to be numeric — a
/// placeholder like `"unset"` would throw `NumberFormatException` on a boot path.
/// `"0"` parses, and Play Integrity then fails on an unregistered project number, which
/// surfaces through the `nativeAttestResponse` error path the Java side already handles.
///
/// # Safety
/// JNI entry point; the returned string is a fresh JNI local reference.
pub unsafe extern "system" fn native_get_gcp_project_number(
    env: *mut JNIEnv,
    _this: jobject,
) -> jstring {
    log::info("PlayIntegrityService.nativeGetGCPProjectNumber -> \"0\"");
    super::new_string(env, "0")
}

// ---------------------------------------------------------------------------
// unity/UnityUtil
// ---------------------------------------------------------------------------

/// `UnityUtil.nativeInit()` — static.
///
/// Registered as well as exported under its mangled name: `RegisterNatives` takes
/// precedence over name-mangled lookup, so routing both here means the registration
/// happens whichever way the JVM resolves the method.
///
/// # Safety
/// JNI entry point; `env` must be a valid `JNIEnv*`, not the `null` the exported symbol
/// also tolerates.
pub unsafe extern "system" fn native_init(env: *mut JNIEnv, _clazz: jclass) {
    // The second chance to learn the VM, and cheap insurance: `JNI_OnLoad` is the documented
    // one and does run, but registration is deliberately duplicated across both entry points
    // (notes/09) precisely because their ordering against the game's class initialisers is not
    // guaranteed. A VM cached here costs one call and removes the last way for the location
    // chain to be unable to reach Java.
    super::set_vm(super::env_to_vm(env));
    super::register::register_all(env);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::{Completion, Context};
    use crate::jni::fake_jni;

    // Every test here that drives the fake JVM or asserts on it opens with
    // `let _g = live::test_guard();`.
    //
    // That guard is the harness's single process-wide lock, not a lock private to `live` —
    // see `crate::jni::fake_jni::test_guard`. Two things make it mandatory rather than
    // merely tidy: `fake_jni::unexpected_calls` counts over the whole process, so a test
    // asserting it is zero while another module runs is asserting something it cannot see;
    // and the counter is sticky, which the guard also clears on entry. Omitting it fails a
    // couple of runs in a hundred and looks exactly like a bug in the code under test.

    /// `NLog$LogLevel.ordinal()` onto `android/log.h` priorities.
    ///
    /// The two numbering schemes overlap only at INFO — 4 means INFO in both — so a
    /// pass-through would look almost right while mislabelling every other level.
    #[test]
    fn log_levels_map_onto_android_priorities() {
        assert_eq!(android_priority(0), None, "OFF is filtered, not forwarded");
        assert_eq!(android_priority(1), Some(PRIO_FATAL));
        assert_eq!(android_priority(2), Some(PRIO_ERROR));
        assert_eq!(android_priority(3), Some(PRIO_WARN));
        assert_eq!(android_priority(4), Some(log::PRIO_INFO));
        assert_eq!(android_priority(5), Some(PRIO_VERBOSE));
        assert_eq!(android_priority(99), Some(log::PRIO_INFO), "unknown level");
    }

    /// `redirectLog` is the only path the Java logging has, so both strings have to be
    /// read — and both `GetStringUTFChars` results released, or every log line leaks a
    /// local reference for the life of the frame.
    #[test]
    fn dispatch_log_message_reads_and_releases_both_strings() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| r.next_string = Some("forwarded\0"));

        unsafe {
            native_dispatch_log_message(
                jni.env,
                core::ptr::null_mut(),
                4,
                fake_jni::stub_string(),
                fake_jni::stub_string(),
            )
        };

        fake_jni::with_recorder(|r| {
            assert_eq!(r.get_string_utf_chars, 2);
            assert_eq!(r.release_string_utf_chars, 2);
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// A message at `OFF` is dropped before any JNI work — the Java side calls
    /// `redirectLog` unconditionally, so filtering has to happen on this side.
    #[test]
    fn dispatch_log_message_drops_off_levels() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe {
            native_dispatch_log_message(
                jni.env,
                core::ptr::null_mut(),
                0,
                fake_jni::stub_string(),
                fake_jni::stub_string(),
            )
        };

        assert_eq!(fake_jni::with_recorder(|r| r.get_string_utf_chars), 0);
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// `PlayIntegrityService` runs `Long.parseLong` on this and hands the result to
    /// `PrepareIntegrityTokenRequest` (`PlayIntegrityService.smali:154-159`). A
    /// human-readable placeholder would throw `NumberFormatException` during boot, so
    /// the assertion is deliberately `parse::<i64>`, not "is a string".
    #[test]
    fn gcp_project_number_parses_as_a_long() {
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe { native_get_gcp_project_number(jni.env, core::ptr::null_mut()) };

        let written = fake_jni::with_recorder(|r| r.new_strings.clone());
        assert_eq!(written.len(), 1, "exactly one string should be created");
        let value = &written[0];
        assert!(
            value.parse::<i64>().is_ok(),
            "Long.parseLong would throw on {value:?}",
        );
    }

    /// The other two integrity natives are deliberately empty-handed, and must be so
    /// without touching the VM.
    #[test]
    fn integrity_stubs_return_null_without_calling_jni() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe {
            assert!(native_get_api_key(jni.env, core::ptr::null_mut()).is_null());
            native_attest_response(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                core::ptr::null_mut(),
                false,
                0,
                core::ptr::null_mut(),
            );
        }

        // `nativeAttestResponse` reads the nullable error string; null means no call.
        assert_eq!(fake_jni::with_recorder(|r| r.get_string_utf_chars), 0);
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// A manager instance to hand `nativeAddLocationProviders` as its receiver, the way Java
    /// does when `onStart` calls it back.
    fn stub_manager() -> jobject {
        fake_jni::stub_object(
            location_service::MANAGER_CLASS
                .to_str()
                .expect("the class path is ASCII"),
        )
    }

    /// The link that was missing. `onStart` reaches this to collect the providers the manager
    /// should track, and the real library answered by constructing them — so with an empty
    /// body the `providers` map stayed empty, nothing ever called `requestLocationUpdates`,
    /// and `nativeLocationUpdate` was unreachable no matter how correct the rest was.
    #[test]
    fn add_location_providers_builds_both_and_hands_them_to_the_manager() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();
        let manager = stub_manager();
        let context = fake_jni::stub_ref();

        unsafe { native_add_location_providers(jni.env, manager, context) };

        fake_jni::with_recorder(|r| {
            let providers: Vec<(jobject, &str)> = r
                .objects
                .iter()
                .map(|(h, class)| (*h, class.as_str()))
                .filter(|(_, c)| *c != location_service::MANAGER_CLASS.to_str().unwrap())
                .collect();
            assert_eq!(
                providers.iter().map(|(_, c)| *c).collect::<Vec<_>>(),
                vec![
                    location_service::GPS_PROVIDER_CLASS.to_str().unwrap(),
                    location_service::FUSED_PROVIDER_CLASS.to_str().unwrap(),
                ],
                "both providers, gps first",
            );

            // Both handed over to the manager — and the **provider itself** has to be the
            // argument. This is the assertion that catches a `CallVoidMethod` dispatched with
            // an empty variadic tail: the call is not a compile error and does not crash, it
            // just puts nothing in the manager's map, so without checking the argument the
            // whole chain looks wired while no provider is ever registered.
            let added: Vec<(jobject, &str, Option<jobject>)> = r
                .calls_void
                .iter()
                .map(|(recv, m, arg)| (*recv, m.as_str(), *arg))
                .collect();
            assert_eq!(
                added,
                vec![
                    (manager, "addLocationProvider", Some(providers[0].0)),
                    (manager, "addLocationProvider", Some(providers[1].0)),
                ],
            );

            // The name matters: `addLocationProvider` keys its map on `getProviderName()`, so
            // these are the two keys the manager ends up holding.
            assert_eq!(
                location_service::simple_name(location_service::GPS_PROVIDER_CLASS),
                "GpsLocationProvider",
            );
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// One provider failing must not cost the other. `"fused"` is the one that produces a fix
    /// indoors, but it depends on Google Play Services; `"gps"` is platform-only and works
    /// when GMS does not. Neither may take the other down with it.
    #[test]
    fn a_provider_that_will_not_construct_is_skipped() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();
        let manager = stub_manager();
        // `FusedLocationProvider` is built second, so this is the case where the *first*
        // succeeded and the second did not — the one that would silently lose the indoor fix.
        fake_jni::with_recorder(|r| {
            r.throwing_constructors.push(
                location_service::FUSED_PROVIDER_CLASS
                    .to_str()
                    .unwrap()
                    .to_owned(),
            );
        });

        unsafe { native_add_location_providers(jni.env, manager, fake_jni::stub_ref()) };

        fake_jni::with_recorder(|r| {
            assert_eq!(
                r.calls_void.len(),
                1,
                "only the provider that was built should be handed over",
            );
            assert!(
                !r.pending_exception,
                "the skip must not leave an exception for the game's next JNI call",
            );
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// Java passes the manager it is calling into and the `Context` it holds. Neither being
    /// null is a reason to touch the VM — and, more to the point, not a reason to fault.
    #[test]
    fn add_location_providers_tolerates_null_arguments() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe {
            native_add_location_providers(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_ref(),
            );
            native_add_location_providers(jni.env, stub_manager(), core::ptr::null_mut());
        }

        assert_eq!(
            fake_jni::with_recorder(|r| r.objects.len()),
            1,
            "the stub manager, and nothing built from a null context",
        );
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// Backs `X509TrustManager.getAcceptedIssuers()`. Returning null here is a
    /// `NullPointerException` in whatever iterates it, so it must be a real empty array.
    #[test]
    fn accepted_issuers_is_an_empty_array_of_x509() {
        let _g = live::test_guard();
        fake_jni::reset();
        let jni = fake_jni::table();

        let array = unsafe { native_get_accepted_issuers(jni.env, core::ptr::null_mut()) };

        assert!(!array.is_null());
        fake_jni::with_recorder(|r| {
            assert!(
                r.find_class
                    .iter()
                    .any(|c| c == "java/security/cert/X509Certificate"),
                "the element class must be X509Certificate, got {:?}",
                r.find_class,
            );
            let (length, _class, initial) = r.new_object_array.expect("NewObjectArray not called");
            assert_eq!(length, 0, "empty, not null");
            assert!(initial.is_null());
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    // -----------------------------------------------------------------------
    // The sensor natives
    // -----------------------------------------------------------------------

    /// The conversions are the whole content of this feature, so they are tested as pure
    /// functions — no JVM, no globals, nothing to arrange.
    #[test]
    fn location_conversion_matches_the_recovered_mapping() {
        let fix = location_data_from(
            37.7749295,
            -122.4194155,
            12.5,
            3.25,
            1_700_000_000_500,
            3,
        );

        assert_eq!(fix.latitude_deg, 37.7749295);
        assert_eq!(fix.longitude_deg, -122.4194155);
        assert_eq!(fix.altitude_m, 12.5);
        assert_eq!(fix.accuracy_m, 3.25);
        assert_eq!(
            fix.timestamp_s, 1_700_000_000.5,
            "Location.getTime() is Unix milliseconds; LocationData is seconds",
        );
        assert_eq!(
            fix.provider_status, 3,
            "ServiceStatus.RUNNING ordinal is passed through, not translated",
        );
        assert_eq!(fix.location_type, LOCATION_TYPE_MEASURED);
        assert_eq!(fix.location_type, 1, "LocationType.Measured");
    }

    /// `getAccuracy()` is the only `()F` in the set, and the widen is the only place a
    /// value changes width. Exactness matters: a `f32`-to-`f64` widen must preserve the
    /// `f32` the framework handed over, decimal inaccuracy and all.
    #[test]
    fn accuracy_widens_from_float_without_rounding_to_the_decimal() {
        let fix = location_data_from(0.0, 0.0, 0.0, 0.1, 0, 0);
        assert_eq!(fix.accuracy_m, f64::from(0.1_f32));
        assert_ne!(
            fix.accuracy_m, 0.1_f64,
            "0.1f32 is not 0.1f64 — widening keeps the float's value, not its spelling",
        );
    }

    /// `ordinal()` cannot go negative, so this is belt-and-braces: a negative arriving as
    /// an `i32` must clamp rather than wrap into a huge `u32` on the managed side.
    #[test]
    fn a_negative_provider_status_clamps_instead_of_wrapping() {
        assert_eq!(location_data_from(0.0, 0.0, 0.0, 0.0, 0, -5).provider_status, 0);
    }

    #[test]
    fn heading_conversion_matches_the_recovered_mapping() {
        let h = heading_from(1_700_000_000_250, 271.5);
        assert_eq!(h.heading_deg, 271.5);
        assert_eq!(
            h.timestamp_s, 1_700_000_000.25,
            "currentTimeMillis reaches this native unmodified; it is milliseconds",
        );
    }

    /// The full JNI path, driven through the real `src/jni/mod.rs` wrappers.
    ///
    /// The signature strings are asserted exactly, because `()F` versus `()D` on
    /// The bug the device found: the position was right and the status was not.
    ///
    /// Java never sends the two together. `AbstractNianticLocationProvider` reports the status
    /// on a call with a **null** `Location` — `updateStatus(RUNNING)` at `:399`, one line
    /// before the listener that carries the fix — and `NianticLocationManager$2.onProviderLocation`
    /// then reports the fix hard-coding `UNDEFINED`. So a native that reads the status off the
    /// location-bearing call sees `UNDEFINED` on every fix, forever, however good the
    /// coordinates are. This is that exact pair of calls, in that order.
    #[test]
    fn a_fix_carries_the_status_java_reported_on_its_own_call() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.location = (1.5, 2.5, 3.5, 4.5, 1_700_000_000_000);
            r.next_string = Some("gps\0");
        });

        unsafe {
            // `updateStatus(RUNNING)` — no location, and the only place the status appears.
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                core::ptr::null_mut(),
                3,
                core::ptr::null_mut(),
            );
            // ... then the fix itself, which always says `UNDEFINED`.
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                0,
                core::ptr::null_mut(),
            );
        }

        let fix = live::location().expect("the fix should have been recorded");
        assert_eq!(fix.latitude_deg, 1.5);
        assert_eq!(
            fix.provider_status, 3,
            "the fix must take the status from the call that carried it, not the \
             UNDEFINED on its own",
        );
        assert_eq!(fix.location_type, LOCATION_TYPE_MEASURED);
        // The status-bearing call carries no `Location`, so it must not have touched JNI to
        // read one — and `string_from_java` is the only call it does make.
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// The same sequence with the status arriving only *after* a fix — `onPause`'s `STOPPED`.
    /// The fix already in hand has to pick it up, or the game goes on being told `Running`
    /// about a provider that has stopped.
    #[test]
    fn a_later_status_reaches_the_fix_already_held() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.location = (1.5, 2.5, 3.5, 4.5, 1_700_000_000_000);
            r.next_string = Some("gps\0");
        });

        unsafe {
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                0,
                core::ptr::null_mut(),
            );
        }
        // No status has been reported yet, so the fix is honest about not knowing one.
        assert_eq!(live::location().unwrap().provider_status, 0);

        unsafe {
            // `updateStatus(STOPPED)`.
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                core::ptr::null_mut(),
                1,
                core::ptr::null_mut(),
            );
        }
        assert_eq!(live::location().unwrap().provider_status, 1);
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// `getAccuracy` is precisely the mistake that would survive a values-only check — the
    /// fake widens whatever it is asked for, so only the resolved signature catches it.
    #[test]
    fn location_update_reads_a_fix_through_the_real_jni_path() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.location = (37.7749295, -122.4194155, 12.5, 3.25, 1_700_000_000_500);
            r.next_string = Some("gps\0");
        });

        // What `SetCallbacks` does in the game. Without it there is no queue published for a
        // context-less producer to raise onto, and the event assertion below would be vacuous
        // rather than failing -- so it has to happen before the call, not after.
        let ctx = Context::new();
        server::publish(ctx.pending());

        unsafe {
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                3,
                core::ptr::null_mut(),
            );
        }

        let fix = live::location().expect("the fix should have been recorded");
        assert_eq!(fix.latitude_deg, 37.7749295);
        assert_eq!(fix.longitude_deg, -122.4194155);
        assert_eq!(fix.altitude_m, 12.5);
        assert_eq!(fix.accuracy_m, 3.25);
        assert_eq!(fix.timestamp_s, 1_700_000_000.5);
        assert_eq!(fix.provider_status, 3);
        assert_eq!(fix.location_type, LOCATION_TYPE_MEASURED);

        // Recording the fix is only half of the job. The game does not poll for its position --
        // it calls `N2MapGetCurrentLocation` exactly once at boot and never again -- so it only
        // learns where it is from this event. A fix that is stored and never announced leaves
        // the location pipeline silent no matter what Java delivers, which is the stall this
        // asserts against. Asserted on the payload rather than just the count: the event is
        // what managed casts to `Geo.Location`, so a wrong buffer is as bad as no buffer.
        let ready = ctx.take_completions();
        assert_eq!(ready.len(), 1, "one fix must raise exactly one event");
        match &ready[0] {
            Completion::Event { slot, payload } => {
                assert_eq!(*slot, EventSlot::LocationUpdate);
                assert_eq!(payload, &location_payload(&fix));
                assert_eq!(payload.len(), 0x30);
            }
            _ => panic!("expected a locationUpdate event"),
        }
        // Drained, so the game is told once per fix rather than once per fix per frame.
        assert!(ctx.take_completions().is_empty());
        server::unpublish();

        fake_jni::with_recorder(|r| {
            let expected: Vec<(String, String)> = [
                ("getLatitude", "()D"),
                ("getLongitude", "()D"),
                ("getAltitude", "()D"),
                ("getAccuracy", "()F"),
                ("getTime", "()J"),
            ]
            .iter()
            .map(|(n, s)| ((*n).to_owned(), (*s).to_owned()))
            .collect();
            assert_eq!(r.method_ids, expected, "names, signatures and order");

            assert!(
                r.find_class.iter().any(|c| c == "android/location/Location"),
                "got {:?}",
                r.find_class,
            );
            assert_eq!(r.delete_local_ref, 1, "the class local ref is released");
            assert_eq!(r.exception_clear, 0, "nothing threw");
            assert!(!r.pending_exception);
            assert_eq!(r.call_double, 3);
            assert_eq!(r.call_float, 1);
            assert_eq!(r.call_long, 1);
            assert_eq!(r.get_string_utf_chars, 1, "the provider name, read once");
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// The compass native takes two scalars, so it must reach the VM for nothing at all —
    /// no class lookup, no getters, no exceptions.
    #[test]
    fn compass_update_converts_without_touching_the_vm() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe {
            native_compass_update(jni.env, core::ptr::null_mut(), 1_700_000_000_250, 271.5);
        }

        let h = live::heading().expect("the heading should have been recorded");
        assert_eq!(h.heading_deg, 271.5);
        assert_eq!(h.timestamp_s, 1_700_000_000.25);

        fake_jni::with_recorder(|r| {
            assert!(r.method_ids.is_empty());
            assert!(r.find_class.is_empty());
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// The end-to-end path, and the precedence decision that shapes it.
    #[test]
    fn a_live_fix_reaches_the_map_getters_and_yields_to_a_debug_override() {
        use crate::abi::{ApiError, CompassHeadingData, Handle, LocationData};

        let _g = live::test_guard();
        // `destroy_context` at the end tears the Java location chain down, and that chain lives
        // in a process-global shared with `location_service`'s own tests — but the guard above
        // is already that lock, so a second acquisition would deadlock, not help.
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.location = (1.0, 2.0, 3.0, 4.0, 1_000);
            r.next_string = Some("gps\0");
        });

        unsafe {
            let mut h: Handle = core::ptr::null_mut();
            assert!(matches!(crate::plugin::create_context(&mut h), ApiError::Success));

            let mut loc = LocationData::default();
            let mut heading = CompassHeadingData::default();

            // Nothing has reported yet. The location getter answers with a synthesized
            // origin rather than PluginNotReady, because managed throws on that error and
            // the game never reaches its map; the heading still refuses, since a fabricated
            // bearing would be acted on rather than merely tolerated.
            assert!(matches!(
                crate::map::get_current_location(h, &mut loc),
                ApiError::Success
            ));
            assert_eq!((loc.latitude_deg, loc.longitude_deg), (0.0, 0.0));
            assert!(matches!(
                crate::map::get_compass_heading(h, &mut heading),
                ApiError::PluginNotReady
            ));

            // A fix from the sensor native answers with no debug override in sight.
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                3,
                core::ptr::null_mut(),
            );
            native_compass_update(jni.env, core::ptr::null_mut(), 1_000, 45.0);

            assert!(matches!(
                crate::map::get_current_location(h, &mut loc),
                ApiError::Success
            ));
            assert_eq!((loc.latitude_deg, loc.longitude_deg), (1.0, 2.0));
            assert_eq!(loc.provider_status, 3);
            assert!(matches!(
                crate::map::get_compass_heading(h, &mut heading),
                ApiError::Success
            ));
            assert_eq!(heading.heading_deg, 45.0);

            // An explicit "pretend I'm here" outranks device truth.
            assert!(matches!(
                crate::debug::set_device_location(h, 9.0, 9.5, 0.0, 0.0, 5, 1),
                ApiError::Success
            ));
            assert!(matches!(
                crate::debug::set_device_heading(h, 350.0),
                ApiError::Success
            ));
            assert!(matches!(
                crate::map::get_current_location(h, &mut loc),
                ApiError::Success
            ));
            assert_eq!(loc.latitude_deg, 9.0);
            assert_eq!(loc.provider_status, 5);
            assert!(matches!(
                crate::map::get_compass_heading(h, &mut heading),
                ApiError::Success
            ));
            assert_eq!(heading.heading_deg, 350.0);

            // Cancelling resumes the live fix rather than resuming refusal — which is the
            // whole reason the override wins while it is set.
            assert!(matches!(
                crate::debug::cancel_debug_location(h),
                ApiError::Success
            ));
            assert!(matches!(
                crate::map::get_current_location(h, &mut loc),
                ApiError::Success
            ));
            assert_eq!(loc.latitude_deg, 1.0, "back to the device, not to PluginNotReady");
            assert!(matches!(
                crate::map::get_compass_heading(h, &mut heading),
                ApiError::Success
            ));
            assert_eq!(heading.heading_deg, 45.0);

            assert!(matches!(
                crate::plugin::destroy_context(h),
                ApiError::Success
            ));
        }
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// A fix that cannot be read must not erase the one already held. This is the whole
    /// reason `location_data_from` is only reached once every getter has resolved.
    #[test]
    fn a_failed_getter_clears_the_exception_and_keeps_the_previous_fix() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();

        fake_jni::with_recorder(|r| {
            r.location = (1.0, 2.0, 3.0, 4.0, 1_000);
            r.next_string = Some("gps\0");
        });
        unsafe {
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                3,
                core::ptr::null_mut(),
            );
        }
        let good = live::location().expect("the first fix");

        // Make every resolution throw, as a renamed or absent getter would.
        fake_jni::reset();
        fake_jni::with_recorder(|r| {
            r.fail_get_method_id = true;
            r.location = (8.0, 8.0, 8.0, 8.0, 8_000);
        });
        unsafe {
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                fake_jni::stub_ref(),
                3,
                core::ptr::null_mut(),
            );
        }

        fake_jni::with_recorder(|r| {
            assert_eq!(r.method_ids.len(), 1, "it bails on the first failure");
            assert_eq!(r.exception_clear, 1, "the NoSuchMethodError is cleared");
            assert!(
                !r.pending_exception,
                "no exception may be left pending — JNI is undefined while one is",
            );
            assert_eq!(r.delete_local_ref, 1, "the class ref is released anyway");
            assert_eq!(r.call_double, 0, "no getter was called");
            assert_eq!(r.get_string_utf_chars, 0, "and the provider was not read");
        });

        let after = live::location().expect("the previous fix must survive");
        assert_eq!(after.latitude_deg, good.latitude_deg);
        assert_eq!(after.timestamp_s, good.timestamp_s);
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }

    /// A null `Location` is the one failure with no exception to clear, and it must not be
    /// mistaken for a fix at `(0, 0)`.
    #[test]
    fn a_null_location_records_nothing() {
        let _g = live::test_guard();
        live::reset();
        fake_jni::reset();
        let jni = fake_jni::table();

        unsafe {
            native_location_update(
                jni.env,
                core::ptr::null_mut(),
                fake_jni::stub_string(),
                core::ptr::null_mut(),
                3,
                core::ptr::null_mut(),
            );
        }

        assert!(live::location().is_none());
        fake_jni::with_recorder(|r| {
            assert!(r.find_class.is_empty(), "not even the class is looked up");
        });
        assert_eq!(fake_jni::unexpected_calls(), 0);
    }
}
