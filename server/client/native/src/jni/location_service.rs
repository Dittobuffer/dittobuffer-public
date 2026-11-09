//! The Java location chain, driven from native.
//!
//! # Why this module exists
//!
//! No fix has ever arrived from Java: `NianticLocationManager.nativeLocationUpdate` has been
//! called zero times, so [`crate::live`] stays empty and every read of the game's position
//! falls through to the synthesized origin. The cause is not a wrong argument — it is that
//! the chain never starts:
//!
//! ```text
//! NianticLocationManager.onStart()  -> addLocationProviders(context)
//!   -> nativeAddLocationProviders(context)      [ our native, previously a log-only no-op ]
//!   -> manager.addLocationProvider(provider)    [ native must construct the provider ]
//!   -> provider.onStart() ... then onResume()
//!   -> requestLocationUpdates / getLastKnownLocation
//!   -> listener -> notifyLocationUpdate(...) -> nativeLocationUpdate(...)
//! ```
//!
//! `com/nianticlabs/nia/location/NianticLocationManager` is referenced nowhere in the 0.429
//! APK outside its own file — no `new-instance`, no `<init>` call site. It is a native-facing
//! class: the real `libNianticLabsPlugin.so` built it with `NewObject`, and this stub did not.
//! So `onStart()` never ran, no provider was ever registered, and the last link was
//! unreachable.
//!
//! This module builds that object and drives its lifecycle. `notes/06-object-model.md`
//! reconstructed the shape; this is the implementation of it.
//!
//! # `nativeClassPointer` is zero, deliberately
//!
//! Every `ContextService` takes the native side's `this` as a `long`, and the real library
//! passes its C++ object so that the natives reachable from Java can find their way home with
//! `GetLongField`. This stub has no such object — [`crate::live`] is the substitute, for the
//! reason recorded there. Nothing in Java reads the pointer except `resetNativeClassPointer`,
//! so passing 0 changes no behaviour; it is a deviation from upstream all the same, and it is
//! the one thing here that a future reader should not mistake for an oversight.
//!
//! # Everything is total
//!
//! `panic = "abort"`, and these are the first JNI calls this crate makes from a vtable slot,
//! so a panic in here is a dead game rather than a stack trace. Every step logs and returns
//! `false` instead, leaving the slot empty so a later `OnUpdate` can retry.
//!
//! Every `CallVoidMethod` is followed by an exception check, because one of them needs it:
//! `AbstractNianticLocationProvider.onResume` calls `getLastKnownLocation(getProviderName())`
//! and catches only `SecurityException`, so an `IllegalArgumentException` — a provider the
//! platform `LocationManager` has never heard of — escapes through `manager.onResume()` and
//! arrives as a *pending exception on our thread*. Handing that back to the game would make
//! the next unrelated JNI call fail.

use core::ffi::CStr;
use core::ptr;
use core::sync::atomic::{AtomicPtr, AtomicUsize, Ordering};
use jni_sys::{jclass, jmethodID, jobject, JNIEnv, _jobject};

use super::{
    call_void_method, call_void_method_object, cached_env, clear_exception, delete_global_ref,
    delete_local_ref, exception_occurred, find_class, get_method_id, get_object_class,
    new_global_ref, new_object_context, new_object_context_pointer,
};
use crate::log;

// --- the Java contract -------------------------------------------------------
//
// These names and descriptors come from the 0.429 smali, not from a header. Unlike
// `android.location.Location` — a framework class R8 cannot rename — the
// `com.nianticlabs.nia.*` classes are obfuscated per release, so every one of these is a
// version-dependent string. Failures are handled, but silently, which is why each step logs
// what it was doing.

/// The manager itself. Constructed by [`attach`], never by Java.
pub const MANAGER_CLASS: &CStr = c"com/nianticlabs/nia/location/NianticLocationManager";

/// `<init>(Context, long)`. The `long` is `nativeClassPointer`; see the module note.
const MANAGER_CTOR: (&CStr, &CStr) = (c"<init>", c"(Landroid/content/Context;J)V");

/// Wires the providers. Reaches `nativeAddLocationProviders`, which is where the providers are
/// actually built — so this call is not finished when it returns.
const MANAGER_ON_START: (&CStr, &CStr) = (c"onStart", c"()V");

/// Asks the providers for updates. **This is the one that produces a fix**: `onStart` only
/// creates each provider's `LocationManager`.
const MANAGER_ON_RESUME: (&CStr, &CStr) = (c"onResume", c"()V");

const MANAGER_ON_PAUSE: (&CStr, &CStr) = (c"onPause", c"()V");
const MANAGER_ON_STOP: (&CStr, &CStr) = (c"onStop", c"()V");

/// `addLocationProvider(NianticLocationProvider)`. Called on the manager from
/// [`native_add_location_providers`](super::natives::native_add_location_providers).
pub const ADD_PROVIDER: (&CStr, &CStr) = (
    c"addLocationProvider",
    c"(Lcom/nianticlabs/nia/location/NianticLocationProvider;)V",
);

/// The provider's own constructor, shared by both concrete providers.
pub const PROVIDER_CTOR: (&CStr, &CStr) = (c"<init>", c"(Landroid/content/Context;)V");

/// Platform-only, backed by `android.location.LocationManager`. Its `onStart` cannot throw.
///
/// `GnssLocationProvider` would also do, except that it reports the provider name `"gps"`
/// too — and `addLocationProvider` keys its map on that name, so registering both would
/// silently drop one. This is the one that drives `GpsStatus$Listener`, which is what
/// `nativeGpsStatusUpdate` expects.
pub const GPS_PROVIDER_CLASS: &CStr = c"com/nianticlabs/nia/location/GpsLocationProvider";

/// Google Play Services backed. The one that yields a fix indoors, and also the one that can
/// fail silently: `resumeProvider` discards the `Task` it gets from GMS, so a client that
/// never connects produces no error and no fix. Registered second so that a failure here
/// cannot cost us the platform provider.
pub const FUSED_PROVIDER_CLASS: &CStr = c"com/nianticlabs/nia/location/FusedLocationProvider";

/// Where the context comes from.
///
/// `currentContext` is set **unconditionally** in the `UnityPlayer` constructor, while
/// `currentActivity` is only set when that context happens to be an `Activity` — so
/// `currentContext` is the primary and `currentActivity` the fallback, not the other way
/// round. `UnityPlayer` is constructed before managed code can reach `OnStart`.
const UNITY_PLAYER_CLASS: &CStr = c"com/unity3d/player/UnityPlayer";
const CURRENT_CONTEXT: (&CStr, &CStr) = (c"currentContext", c"Landroid/content/Context;");
const CURRENT_ACTIVITY: (&CStr, &CStr) = (c"currentActivity", c"Landroid/app/Activity;");

// --- state -------------------------------------------------------------------

/// The constructed manager, as a JNI **global** reference.
///
/// `AtomicPtr` rather than a `Mutex`, and not only for the usual reason: `attach` reaches
/// `nativeAddLocationProviders` *re-entrantly*, from inside `CallVoidMethod(onStart)`. A lock
/// held across that call would be taken twice on one thread — a self-deadlock, which with
/// `panic = "abort"` is a hung game. An atomic has no such state to get stuck in.
///
/// A global reference rather than the local one `NewObject` returns: the local dies with the
/// native frame that made it, and Java's `providers` map would be left holding an object whose
/// methods we can no longer call.
static MANAGER: AtomicPtr<_jobject> = AtomicPtr::new(ptr::null_mut());

/// Frames between attach attempts, once the first has failed. See [`retry_attach`].
const RETRY_FRAMES: usize = 60;

static ATTEMPTS: AtomicUsize = AtomicUsize::new(0);
static FAILURES: AtomicUsize = AtomicUsize::new(0);

/// True once a manager exists. One relaxed-ish load: this is on the `OnUpdate` path.
pub fn is_attached() -> bool {
    !MANAGER.load(Ordering::Acquire).is_null()
}

/// Report a failure at a rate that stays useful.
///
/// The retry runs at [`RETRY_FRAMES`], so an unconditional line would be one a second for the
/// life of a session in which — say — location permission was denied at boot. The first few
/// say what went wrong; the periodic one says it is still wrong.
fn failed(what: &str) -> bool {
    let n = FAILURES.fetch_add(1, Ordering::AcqRel);
    if n < 3 || n.is_multiple_of(RETRY_FRAMES) {
        log::info(&format!("location chain: {what} (failure {})", n + 1));
    }
    false
}

// --- attach ------------------------------------------------------------------

/// Build the manager and start it. The env-less entry point, for the vtable slots.
///
/// # Safety
/// Callable from any thread the JVM has attached; it is a no-op otherwise.
pub unsafe fn attach() -> bool {
    let env = cached_env();
    if env.is_null() {
        // Not an error: `OnStart` can beat `JNI_OnLoad` only if the library were loaded
        // twice, and this also covers a call from a thread the JVM never attached.
        return failed("no JNIEnv (VM not cached, or this thread is unattached)");
    }
    attach_with_env(env)
}

/// [`attach`] against an env the caller already has — the `nativeInit` path, and the tests.
///
/// Idempotent: a second call with a manager already in place returns true without touching
/// the VM.
///
/// # Safety
/// JNI entry point; `env` must be the calling thread's.
pub unsafe fn attach_with_env(env: *mut JNIEnv) -> bool {
    if env.is_null() {
        return false;
    }
    if is_attached() {
        return true;
    }

    if !super::vm_is_cached() {
        // Only reachable from a test or from `nativeInit`, which is itself evidence the VM
        // exists. Logged because the alternative is a silent no-op that looks like a
        // permission problem.
        log::info("location chain: attaching with an env but no cached VM");
    }

    let Some(context) = unity_player_context(env) else {
        return failed("UnityPlayer has no currentContext/currentActivity yet");
    };
    let Some(gref) = construct_manager(env, context) else {
        delete_local_ref(env, context);
        return false;
    };
    // Done with the local: Java holds its own reference, and `nativeAddLocationProviders`
    // receives one as an argument rather than reading this.
    delete_local_ref(env, context);

    // Publish before starting it, so the re-entrant `nativeAddLocationProviders` — which runs
    // inside the `onStart` below — finds a manager. If another thread won the race, ours is
    // the redundant one and goes away.
    if MANAGER
        .compare_exchange(ptr::null_mut(), gref, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        delete_global_ref(env, gref);
        return true;
    }

    // Order matters, and reversing it is a NullPointerException in Java: `onStart` is what
    // gives each provider its `LocationManager`, and `onResume` is the first thing that asks
    // it for anything.
    if !call_lifecycle(env, "onStart", MANAGER_ON_START) {
        // `onStart` failing is not fatal to the object — it exists and is published — but
        // every later call will fail the same way, so say so and let `retry_attach` try again.
        return false;
    }
    if !call_lifecycle(env, "onResume", MANAGER_ON_RESUME) {
        return false;
    }

    FAILURES.store(0, Ordering::Release);
    log::info("location chain: NianticLocationManager started and resumed");
    true
}

/// One attempt per [`RETRY_FRAMES`] frames, and only while unattached.
///
/// `OnStart` fires before `UnityPlayer` is guaranteed to exist, so a first attempt that fails
/// there must not be the last one. Called from `OnUpdate`, which is the only thing that runs
/// reliably afterwards.
///
/// # Safety
/// Callable from any thread the JVM has attached.
pub unsafe fn retry_attach() {
    if is_attached() {
        return;
    }
    // The counter is bumped on every frame and read modulo the cadence, so the retry lands on
    // a fixed phase rather than drifting with whenever the first failure happened.
    if !ATTEMPTS.fetch_add(1, Ordering::AcqRel).is_multiple_of(RETRY_FRAMES) {
        return;
    }
    attach();
}

/// Read `UnityPlayer.currentContext`, falling back to `currentActivity`.
///
/// Returns a **local** reference owned by the caller.
///
/// # Safety
/// `env` must be the calling thread's.
unsafe fn unity_player_context(env: *mut JNIEnv) -> Option<jobject> {
    let class = find_class(env, UNITY_PLAYER_CLASS.as_ptr());
    if class.is_null() {
        clear_exception(env);
        failed("FindClass(com/unity3d/player/UnityPlayer)");
        return None;
    }

    let context = static_object_field(env, class, CURRENT_CONTEXT);
    let context = match context {
        Some(c) => c,
        None => {
            // Not necessarily a failure: `currentActivity` is null on a context that is not
            // an `Activity`, and this is the documented fallback.
            match static_object_field(env, class, CURRENT_ACTIVITY) {
                Some(c) => c,
                None => {
                    delete_local_ref(env, class);
                    failed("UnityPlayer.currentContext and currentActivity are both null");
                    return None;
                }
            }
        }
    };

    delete_local_ref(env, class);
    Some(context)
}

/// Read one static object field, clearing the `NoSuchFieldError` a failure throws.
///
/// # Safety
/// `class` must be a valid class reference and `env` the calling thread's.
unsafe fn static_object_field(
    env: *mut JNIEnv,
    class: jclass,
    field: (&CStr, &CStr),
) -> Option<jobject> {
    let (name, sig) = field;
    let id = super::get_static_field_id(env, class, name.as_ptr(), sig.as_ptr());
    if id.is_null() {
        clear_exception(env);
        log::info(&format!(
            "location chain: no static field UnityPlayer.{}",
            name.to_string_lossy(),
        ));
        return None;
    }
    // A null *value* is not a pending exception, so this returns `Some(null)` only if the JVM
    // somehow did; the null check belongs to the caller, which is why the read is inline.
    let value = super::get_static_object_field(env, class, id);
    if value.is_null() {
        return None;
    }
    Some(value)
}

/// `NewObject(NianticLocationManager, context, 0)` and promote the result to a global ref.
///
/// # Safety
/// JNI entry point; `context` must be a live `android.content.Context`.
unsafe fn construct_manager(env: *mut JNIEnv, context: jobject) -> Option<jobject> {
    let class = find_class(env, MANAGER_CLASS.as_ptr());
    if class.is_null() {
        clear_exception(env);
        failed("FindClass(NianticLocationManager)");
        return None;
    }
    let Some(ctor) = method(env, class, MANAGER_CTOR) else {
        delete_local_ref(env, class);
        return None;
    };
    // `nativeClassPointer = 0` — see the module note.
    let object = new_object_context_pointer(env, class, ctor, context, 0);
    delete_local_ref(env, class);
    if object.is_null() {
        clear_exception(env);
        return some_or_failed("NianticLocationManager.<init> returned null");
    }
    // A constructor that threw leaves the exception pending even though the call "finished".
    if exception_occurred(env) {
        delete_local_ref(env, object);
        return some_or_failed("NianticLocationManager.<init> threw");
    }

    let gref = new_global_ref(env, object);
    delete_local_ref(env, object);
    if gref.is_null() {
        return some_or_failed("NewGlobalRef(NianticLocationManager) returned null");
    }
    Some(gref)
}

/// `failed`, in the shape an `Option`-returning function needs. Keeps the call sites from
/// having to write `{ failed(..); None }` and risk forgetting the `None`.
fn some_or_failed(what: &str) -> Option<jobject> {
    failed(what);
    None
}

/// Resolve a method, clearing the `NoSuchMethodError` a failure throws.
///
/// # Safety
/// `class` must be valid and `env` the calling thread's.
pub unsafe fn method(
    env: *mut JNIEnv,
    class: jclass,
    pair: (&CStr, &CStr),
) -> Option<jmethodID> {
    let (name, sig) = pair;
    let id = get_method_id(env, class, name.as_ptr(), sig.as_ptr());
    if id.is_null() {
        // JNI is only defined with no exception pending, so this cannot be deferred to the
        // caller: the `?` that follows would otherwise leave one behind.
        clear_exception(env);
        log::info(&format!(
            "location chain: no method {}{}",
            name.to_string_lossy(),
            sig.to_string_lossy(),
        ));
        return None;
    }
    Some(id)
}

/// Call a `()V` on the manager, reporting whether it completed without throwing.
///
/// # Safety
/// JNI entry point.
unsafe fn call_lifecycle(env: *mut JNIEnv, label: &str, pair: (&CStr, &CStr)) -> bool {
    let manager = MANAGER.load(Ordering::Acquire);
    if manager.is_null() {
        return false;
    }
    // `GetObjectClass` on the instance rather than `FindClass` on a path: the object is
    // already here, and this way the manager's class cannot drift out from under a hard-coded
    // name.
    let class = get_object_class(env, manager);
    if class.is_null() {
        clear_exception(env);
        return failed("GetObjectClass(NianticLocationManager)");
    }
    let id = method(env, class, pair);
    delete_local_ref(env, class);
    let Some(id) = id else {
        return failed("resolving a NianticLocationManager lifecycle method");
    };

    call_void_method(env, manager, id);
    if exception_occurred(env) {
        // The exception is cleared by `exception_occurred`; the object itself is fine, so
        // this is reported rather than treated as a reason to tear the chain down.
        failed(&format!("NianticLocationManager.{label} threw (cleared)"));
        return false;
    }
    true
}

// --- lifecycle ---------------------------------------------------------------

/// # Safety
/// Callable from any thread the JVM has attached; a no-op otherwise.
pub unsafe fn resume() {
    let env = cached_env();
    if !env.is_null() {
        lifecycle_with_env(env, "onResume", MANAGER_ON_RESUME);
    }
}

/// # Safety
/// Callable from any thread the JVM has attached; a no-op otherwise.
pub unsafe fn pause() {
    let env = cached_env();
    if !env.is_null() {
        lifecycle_with_env(env, "onPause", MANAGER_ON_PAUSE);
    }
}

/// # Safety
/// Callable from any thread the JVM has attached; a no-op otherwise.
pub unsafe fn stop() {
    let env = cached_env();
    if !env.is_null() {
        lifecycle_with_env(env, "onStop", MANAGER_ON_STOP);
    }
}

/// [`resume`] and friends against an env the caller already has.
///
/// Deliberately **not** followed by anything: the manager's `locationManager` is nulled by
/// `onStop`, and `onPause` on a stopped manager would dereference it. The plugin's own state
/// machine does not call `OnPause` after `OnStop`, and this keeps that a property of the
/// caller rather than something to be rescued here.
///
/// # Safety
/// JNI entry point; `env` must be the calling thread's.
pub unsafe fn lifecycle_with_env(env: *mut JNIEnv, label: &str, pair: (&CStr, &CStr)) {
    if env.is_null() || !is_attached() {
        return;
    }
    call_lifecycle(env, label, pair);
}

/// Drop the manager.
///
/// Called from `DestroyContext`, before the plugin's own context goes: the global reference is
/// the one thing here that outlives both a native frame and the game's `Context`, so it is the
/// one thing that has to be handed back explicitly.
///
/// # Safety
/// Callable from any thread the JVM has attached.
pub unsafe fn detach() {
    let manager = MANAGER.swap(ptr::null_mut(), Ordering::AcqRel);
    if manager.is_null() {
        return;
    }
    let env = cached_env();
    if env.is_null() {
        // The VM is gone, so the reference cannot be released. One leaked reference at
        // teardown is preferable to dereferencing a dead VM.
        log::info("location chain: detach with no JNIEnv; the global ref is leaked");
        return;
    }
    delete_global_ref(env, manager);
    log::info("location chain: NianticLocationManager detached");
}

// --- providers ---------------------------------------------------------------

/// Build one provider and hand it to the manager.
///
/// Called from [`native_add_location_providers`](super::natives::native_add_location_providers)
/// with the manager as the receiver. It is **not** read from [`MANAGER`]: this runs
/// re-entrantly, inside `attach`'s `onStart` call, so the global may not be published yet —
/// and taking it as an argument is more honest anyway, since it is what Java passed.
///
/// Returns whether the provider was constructed *and* accepted.
///
/// # Safety
/// JNI entry point; `manager` must be the `NianticLocationManager` Java is calling into and
/// `context` its `Context` argument.
pub unsafe fn add_provider(
    env: *mut JNIEnv,
    manager: jobject,
    context: jobject,
    provider_class: &CStr,
) -> bool {
    let name = simple_name(provider_class);
    if env.is_null() || manager.is_null() || context.is_null() {
        return failed(&format!("{name}: null manager or context"));
    }

    let class = find_class(env, provider_class.as_ptr());
    if class.is_null() {
        clear_exception(env);
        return failed(&format!("{name}: FindClass"));
    }
    let Some(ctor) = method(env, class, PROVIDER_CTOR) else {
        delete_local_ref(env, class);
        return false;
    };
    let provider = new_object_context(env, class, ctor, context);
    delete_local_ref(env, class);
    if provider.is_null() {
        clear_exception(env);
        // Not fatal to the chain: the next candidate still gets its turn. This is the path
        // `FusedLocationProvider` takes on a device without a usable Play Services.
        return failed(&format!("{name}: constructor failed (skipped)"));
    }
    if exception_occurred(env) {
        delete_local_ref(env, provider);
        return failed(&format!("{name}: constructor threw (skipped)"));
    }

    let manager_class = get_object_class(env, manager);
    if manager_class.is_null() {
        clear_exception(env);
        delete_local_ref(env, provider);
        return failed("GetObjectClass(NianticLocationManager) for addLocationProvider");
    }
    let id = method(env, manager_class, ADD_PROVIDER);
    delete_local_ref(env, manager_class);
    let Some(id) = id else {
        delete_local_ref(env, provider);
        return false;
    };

    // The provider goes in as the argument: `addLocationProvider(NianticLocationProvider)V`
    // is what puts it in the manager's map, sets its listener, and calls `onStart()` on it.
    // Dispatching this with an empty tail would hand the JVM whatever sits in the argument
    // register — which is not a crash here so much as a silently empty `providers` map, and
    // therefore no fix, with every log line still reading as success.
    //
    // This is also where `nativeLocationUpdate` is first reached: `addLocationProvider` fires
    // it with a **null** `Location` and status `UNDEFINED` before the provider starts. So a
    // non-zero call count is not evidence that anything is working — only a non-null fix,
    // which arrives later from `onResume`, is.
    call_void_method_object(env, manager, id, provider);
    let threw = exception_occurred(env);
    delete_local_ref(env, provider);
    if threw {
        return failed(&format!("{name}: addLocationProvider threw"));
    }
    log::info(&format!("location chain: registered provider \"{name}\""));
    true
}

/// `com/example/Foo` -> `Foo`, for log lines.
pub fn simple_name(path: &CStr) -> &str {
    let whole = path.to_str().unwrap_or("provider");
    whole.rsplit('/').next().unwrap_or(whole)
}

// --- test seams --------------------------------------------------------------

/// Drop the manager without a VM, and zero the counters.
///
/// Test-only: [`MANAGER`] is a process-wide `static`, so a test that writes it has to start
/// from a known state. Production teardown goes through [`detach`], which releases the global
/// reference; this cannot, so it leaks one — which is what a test process wants.
#[cfg(test)]
pub fn reset() {
    MANAGER.store(ptr::null_mut(), Ordering::Release);
    ATTEMPTS.store(0, Ordering::Release);
    FAILURES.store(0, Ordering::Release);
}

/// Serialize every test that touches the manager.
///
/// One process-wide `static`, several test threads, and a leaked manager from one test would
/// make the next one's `attach` a no-op that looks like a passing assertion. Take it *before*
/// [`reset`].
///
/// The lock itself lives in the harness rather than here, because the state that actually
/// races spans modules: [`reset`] and the unexpected-call counter are shared, and a test
/// asserting `unexpected_calls() == 0` was failing on calls made by `bgcontext`'s tests — which
/// held a different mutex, so it serialized nothing against this one. Four mutexes guarding one
/// shared process is not four guards, it is none. See [`crate::jni::fake_jni::test_guard`].
#[cfg(test)]
pub fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    crate::jni::fake_jni::test_guard()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jni::fake_jni::{self, Event};

    /// Class paths as the fake names them back. Derived from the production constants rather
    /// than restated, so a rename cannot leave the test asserting against a path the code no
    /// longer uses — which would pass while the device failed.
    fn path(c: &CStr) -> &str {
        c.to_str().expect("class paths are ASCII")
    }

    /// A fake VM with a context in place, ready for `attach_with_env`.
    fn env_with_context() -> fake_jni::FakeEnv {
        fake_jni::reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| r.current_context = Some(fake_jni::stub_ref()));
        jni
    }

    /// The whole point of the module, end to end: resolve the context out of `UnityPlayer`,
    /// build the manager, publish it, and drive `onStart` then `onResume` — in that order.
    ///
    /// The order is the assertion that matters. `onStart` is what gives each provider its
    /// `LocationManager`; `onResume` is the first thing that asks it for a fix. Reversed, Java
    /// throws, and the game gets nothing.
    #[test]
    fn attach_builds_the_manager_and_starts_it_before_resuming() {
        let _g = test_guard();
        reset();
        let jni = env_with_context();

        let attached = unsafe { attach_with_env(jni.env) };
        assert!(attached, "attach should have succeeded");
        assert!(is_attached());

        fake_jni::with_recorder(|r| {
            let (handle, class) = r
                .objects
                .iter()
                .find(|(_, c)| c == path(MANAGER_CLASS))
                .expect("no NianticLocationManager was constructed");
            assert_eq!(class, path(MANAGER_CLASS));
            assert_eq!(
                r.objects.len(),
                1,
                "only the manager should exist at this point, got {:?}",
                r.objects,
            );

            // Promoted before it was used, so the object outlives the native frame that made
            // it — and every call afterwards went to the *global*, not to the local.
            assert_eq!(r.new_global_refs, 1);
            let (gref, of) = r.global_refs[0];
            assert_eq!(of, *handle, "the global must wrap the manager");
            for (receiver, method, argument) in &r.calls_void {
                assert_eq!(*receiver, gref, "{method} went to the wrong reference");
                // The lifecycle calls take no argument; nothing in `attach` registers a
                // provider, so a non-`None` here would mean one was dispatched with a
                // variadic tail it has no descriptor for.
                assert_eq!(*argument, None, "{method} was passed an argument");
            }

            let calls: Vec<&str> = r
                .events
                .iter()
                .filter_map(|e| match e {
                    Event::CallVoid { method, .. } => Some(method.as_str()),
                    _ => None,
                })
                .collect();
            assert_eq!(
                calls,
                vec!["onStart", "onResume"],
                "onStart must come first; the events were {:?}",
                r.events,
            );
        });

        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// `currentContext` is the primary and `currentActivity` the fallback, not the reverse:
    /// `currentActivity` is only set when the context happens to be an `Activity`.
    #[test]
    fn the_context_falls_back_to_current_activity() {
        let _g = test_guard();
        reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.current_context = None;
            r.current_activity = Some(fake_jni::stub_ref());
        });

        assert!(unsafe { attach_with_env(jni.env) });

        // The fallback was read *after* the primary, and both were read.
        let reads: Vec<String> = fake_jni::with_recorder(|r| {
            r.events
                .iter()
                .filter_map(|e| match e {
                    Event::GetStaticObjectField(name) => Some(name.clone()),
                    _ => None,
                })
                .collect()
        });
        assert_eq!(reads, vec!["currentContext", "currentActivity"]);
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// No context means no manager — and, just as importantly, no pending exception left
    /// behind for the game's next JNI call to trip over.
    #[test]
    fn a_missing_context_attaches_nothing_and_leaves_no_exception() {
        let _g = test_guard();
        reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| {
            r.current_context = None;
            r.current_activity = None;
        });

        assert!(!unsafe { attach_with_env(jni.env) });
        assert!(!is_attached());
        assert!(
            !fake_jni::with_recorder(|r| r.pending_exception),
            "a pending exception would fail the game's next JNI call",
        );
        // Nothing was constructed, and nothing was leaked into a global ref.
        assert_eq!(fake_jni::with_recorder(|r| r.objects.len()), 0);
        assert_eq!(fake_jni::with_recorder(|r| r.new_global_refs), 0);
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// An unmapped `UnityPlayer` — the R8-renamed-field failure mode — is handled the same
    /// way, rather than reported as a crash.
    #[test]
    fn a_missing_unity_player_field_is_survivable() {
        let _g = test_guard();
        reset();
        let jni = fake_jni::table();
        fake_jni::with_recorder(|r| r.static_field_missing = true);

        assert!(!unsafe { attach_with_env(jni.env) });
        assert!(!is_attached());
        assert!(!fake_jni::with_recorder(|r| r.pending_exception));
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// A second `attach` must not build a second manager: `OnStart` can be called more than
    /// once over a session, and each one would otherwise leave another `providers` map and
    /// another set of Android listeners behind.
    #[test]
    fn attach_is_idempotent() {
        let _g = test_guard();
        reset();
        let jni = env_with_context();

        assert!(unsafe { attach_with_env(jni.env) });
        let after_first = fake_jni::with_recorder(|r| (r.objects.len(), r.calls_void.len()));

        assert!(unsafe { attach_with_env(jni.env) });
        assert_eq!(
            fake_jni::with_recorder(|r| (r.objects.len(), r.calls_void.len())),
            after_first,
            "the second attach did work",
        );
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// `onResume` throwing is cleared and reported, and the manager stays attached: the
    /// exception is Java's problem to have had, not a reason to tear down a live object.
    ///
    /// This is not hypothetical — `AbstractNianticLocationProvider.onResume` calls
    /// `getLastKnownLocation` and catches only `SecurityException`.
    #[test]
    fn a_throwing_on_resume_is_cleared_and_leaves_the_manager_attached() {
        let _g = test_guard();
        reset();
        let jni = env_with_context();
        fake_jni::with_recorder(|r| r.throwing_calls.push("onResume".to_owned()));

        assert!(!unsafe { attach_with_env(jni.env) }, "onResume threw");
        assert!(is_attached(), "the object exists and is published");
        assert!(
            !fake_jni::with_recorder(|r| r.pending_exception),
            "the exception must have been cleared, not handed to the game",
        );
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// The lifecycle calls reach the manager, and a pause after a stop is not sent — the
    /// manager's `locationManager` is null by then.
    #[test]
    fn lifecycle_calls_reach_the_manager() {
        let _g = test_guard();
        reset();
        let jni = env_with_context();
        assert!(unsafe { attach_with_env(jni.env) });

        unsafe {
            lifecycle_with_env(jni.env, "onPause", MANAGER_ON_PAUSE);
            lifecycle_with_env(jni.env, "onResume", MANAGER_ON_RESUME);
            lifecycle_with_env(jni.env, "onStop", MANAGER_ON_STOP);
        }

        let calls: Vec<String> = fake_jni::with_recorder(|r| {
            r.calls_void.iter().map(|(_, m, _)| m.clone()).collect()
        });
        assert_eq!(calls, vec!["onStart", "onResume", "onPause", "onResume", "onStop"]);
        assert_eq!(fake_jni::unexpected_calls(), 0);
        reset();
    }

    /// Lifecycle calls are dropped rather than dereferenced when nothing is attached, and
    /// `detach` releases the global reference exactly once.
    #[test]
    fn detach_releases_the_global_ref() {
        let _g = test_guard();
        reset();
        let jni = env_with_context();

        // Before any attach: nothing to call, and nothing to crash.
        unsafe { lifecycle_with_env(jni.env, "onResume", MANAGER_ON_RESUME) };
        assert_eq!(fake_jni::with_recorder(|r| r.calls_void.len()), 0);

        assert!(unsafe { attach_with_env(jni.env) });
        let gref = MANAGER.load(Ordering::Acquire);
        assert!(!gref.is_null());

        // `detach` resolves its own env, which the host test cannot provide, so exercise the
        // release directly against the fake.
        unsafe { delete_global_ref(jni.env, gref) };
        assert_eq!(fake_jni::with_recorder(|r| r.delete_global_refs), 1);

        reset();
        assert!(!is_attached());
        unsafe { lifecycle_with_env(jni.env, "onResume", MANAGER_ON_RESUME) };
        assert_eq!(
            fake_jni::with_recorder(|r| r.calls_void.len()),
            2,
            "only the attach pair; the post-detach call must be a no-op",
        );
        reset();
    }
}
