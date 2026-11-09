//! A minimal fake JVM, just enough to run the JNI layer on the host.
//!
//! The device test in `notes/10-verification.md` is the real gate, but it can only say
//! "it crashed" or "it didn't". This harness says *which* call went wrong, and it can do
//! so before anything is pushed to a phone.
//!
//! # How it works
//!
//! A real `JNIEnv` is a pointer to a pointer to a table of function pointers. The table
//! layout is fixed by the NDK's `jni.h`, and `super::tests` already asserts that
//! `jni-sys`'s `v1_6` view reproduces it. So the table can be built here as a plain
//! `[usize; 233]`, with stand-ins installed at the slots the code under test reaches,
//! and handed over as a genuine `*mut JNIEnv`.
//!
//! That makes the harness sensitive to exactly the failures that matter: a call to the
//! wrong slot lands on [`unused`] (which records it) instead of on the intended stub,
//! and a name or signature missing its NUL terminator runs off the end of the literal
//! when the stub reads it back with `CStr::from_ptr`.
//!
//! Every slot is filled with [`unused`] before the specific stubs go in, so no field of
//! the reinterpreted struct ever holds a null function address.
//!
//! Test-only. Nothing here is compiled into the shipped library.

use core::cell::RefCell;
use core::ffi::c_char;
use core::sync::atomic::{AtomicUsize, Ordering};
use jni_sys::{
    jboolean, jclass, jfieldID, jint, jmethodID, jobject, jobjectArray, jsize, jstring,
    JNIEnv, JNINativeInterface_, JNINativeInterface__1_6, JNINativeMethod, JNI_OK,
};

// Slot indices from the NDK's `struct JNINativeInterface` — the same numbers asserted in
// `super::tests::native_table_slots_match_the_ndk_header`.
const FIND_CLASS: usize = 6;
const EXCEPTION_CLEAR: usize = 17;
const NEW_GLOBAL_REF: usize = 21;
const DELETE_GLOBAL_REF: usize = 22;
const DELETE_LOCAL_REF: usize = 23;
const NEW_OBJECT: usize = 28;
const GET_OBJECT_CLASS: usize = 31;
const GET_METHOD_ID: usize = 33;
const CALL_OBJECT_METHOD: usize = 34;
const CALL_LONG_METHOD: usize = 52;
const CALL_FLOAT_METHOD: usize = 55;
const CALL_DOUBLE_METHOD: usize = 58;
const CALL_VOID_METHOD: usize = 61;
const GET_STATIC_FIELD_ID: usize = 144;
const GET_STATIC_OBJECT_FIELD: usize = 145;
const NEW_STRING_UTF: usize = 167;
const GET_STRING_UTF_CHARS: usize = 169;
const RELEASE_STRING_UTF_CHARS: usize = 170;
const NEW_OBJECT_ARRAY: usize = 172;
const REGISTER_NATIVES: usize = 215;
const EXCEPTION_CHECK: usize = 228;

/// One past the last slot in the NDK header. The table is never indexed beyond this, and
/// it is at least as large as `jni-sys`'s struct — asserted in [`table`].
pub const SLOTS: usize = 233;

/// Calls that reached a slot the harness did not install a stub for.
static UNEXPECTED_CALLS: AtomicUsize = AtomicUsize::new(0);

/// One modelled call, in the order it happened.
///
/// The per-slot counters elsewhere in [`Recorder`] answer "how many"; this answers
/// "in what order", which is the question the location chain turns on. `onStart` before
/// `onResume` is the one ordering that matters most — reversed, the manager's
/// `locationManager` field is still null when the first provider asks it for a fix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    FindClass(String),
    GetStaticFieldId(String),
    GetStaticObjectField(String),
    NewObject { class: String, handle: jobject },
    CallVoid {
        receiver: jobject,
        method: String,
        argument: Option<jobject>,
    },
    /// A `CallObjectMethod`, recorded as the receiver and the method name. The *result* is
    /// deliberately not recorded: it is a fresh handle every time, so it carries no
    /// information a test can assert on — what matters is that the call happened, on which
    /// object, and through which method id.
    CallObject {
        receiver: jobject,
        method: String,
    },
    NewGlobalRef { into: jobject },
    DeleteGlobalRef { of: jobject },
}

/// What the stubs saw.
#[derive(Default, Debug)]
pub struct Recorder {
    /// One entry per class `RegisterNatives` was called for: the method rows it was given.
    pub registered: Vec<Vec<(String, String, *mut core::ffi::c_void)>>,
    /// Class names that reached `FindClass`, in order.
    pub find_class: Vec<String>,
    /// Class names in here make `FindClass` return null and set a pending exception.
    pub missing: Vec<String>,
    pub delete_local_ref: usize,
    pub exception_clear: usize,
    pub exception_check: usize,
    /// A `NewStringUTF` payload, and the count of calls.
    pub new_strings: Vec<String>,
    /// A `NewObjectArray` call: `(length, element class, initial element)`.
    pub new_object_array: Option<(jsize, jclass, jobject)>,
    /// What `GetStringUTFChars` should return for the next `string_from_java` call.
    pub next_string: Option<&'static str>,
    pub get_string_utf_chars: usize,
    pub release_string_utf_chars: usize,
    /// Make `RegisterNatives` report failure.
    pub fail_register: bool,
    /// Whether an exception is currently pending, as far as the fake VM knows.
    pub pending_exception: bool,
    /// What the fake `android.location.Location` reports, in [`LOCATION_GETTERS`] order:
    /// `(latitude, longitude, altitude, accuracy, time_ms)`.
    pub location: (f64, f64, f64, f32, i64),
    /// Every `(name, signature)` pair that reached `GetMethodID`, in order.
    pub method_ids: Vec<(String, String)>,
    /// Per-entry counts, so a test can assert each getter was resolved exactly once.
    pub call_double: usize,
    pub call_float: usize,
    pub call_long: usize,
    /// Make `GetMethodID` set a pending exception and return null.
    pub fail_get_method_id: bool,

    // --- the location chain -------------------------------------------------
    /// Every modelled call, in order. See [`Event`].
    pub events: Vec<Event>,
    /// Every class `FindClass` has resolved, deduplicated. A `jclass` is
    /// `CLASS_BASE + index into this`, which is what lets a later stub name the class it was
    /// handed instead of treating every reference as interchangeable.
    pub classes: Vec<String>,
    /// Every object `NewObject` has made, as `(handle, class)`. A `jobject` is
    /// `OBJECT_BASE + index into this`, for the same reason.
    pub objects: Vec<(jobject, String)>,
    /// Every global reference `NewGlobalRef` has made, as `(global, object it came from)`.
    ///
    /// Kept so that a call on a global reference can still be traced back to the object behind
    /// it — which is the whole point of the thing, and a property a harness that handed back
    /// the same handle could not check.
    pub global_refs: Vec<(jobject, jobject)>,
    /// What `UnityPlayer.currentContext` reads as.
    pub current_context: Option<jobject>,
    /// What `UnityPlayer.currentActivity` reads as — the fallback.
    pub current_activity: Option<jobject>,
    /// Make `GetStaticFieldID` throw, the way an R8 rename or a reshuffled field would.
    pub static_field_missing: bool,
    /// Class names whose constructor throws. The chain must skip these and carry on.
    pub throwing_constructors: Vec<String>,
    /// Method names whose `CallVoidMethod` throws.
    pub throwing_calls: Vec<String>,
    /// Every call dispatched through `CallVoidMethod`, as `(receiver, method name, argument)`.
    ///
    /// The argument is `Some` only for the methods that take one. Recording it is the whole
    /// point: `addLocationProvider` is dispatched with `CallVoidMethod`, which is **variadic**
    /// in the JNI header, so a call that forgets to pass the provider is not a compile error
    /// and does not crash — it hands the JVM whatever is in the register. Without this field
    /// the fake JVM could not tell that call apart from a correct one.
    pub calls_void: Vec<(jobject, String, Option<jobject>)>,
    /// Every call dispatched through `CallObjectMethod`, as `(receiver, method name)`.
    ///
    /// There is no argument column: unlike [`calls_void`](Self::calls_void), the one caller
    /// resolves a `()Ljava/lang/ClassLoader;`, so the variadic tail is empty by construction
    /// rather than by the callee remembering to leave it out.
    pub calls_object: Vec<(jobject, String)>,
    pub new_global_refs: usize,
    pub delete_global_refs: usize,
    pub get_object_class: usize,
}

/// The `android.location.Location` getters a fix is read from, in [`Recorder::location`]
/// order.
///
/// [`fake_get_method_id`] hands back `index + 1` as the `jmethodID`, which is what lets the
/// `Call*Method` stubs answer correctly *without* depending on the order the code under
/// test happens to resolve them in. That independence is the point: a swap of `getLatitude`
/// and `getLongitude` would otherwise move both values together and still look self-
/// consistent from the outside.
const LOCATION_GETTERS: [&str; 5] = [
    "getLatitude",
    "getLongitude",
    "getAltitude",
    "getAccuracy",
    "getTime",
];

/// Every method name the harness resolves, in the order ids are handed out.
///
/// The five getters stay first, so their ids are unchanged and every assertion already
/// written against [`Recorder::location`] keeps meaning the same thing — the test
/// [`tests::the_chain_methods_did_not_shift_the_getter_ids`] pins that. The rest is the
/// location chain: the manager and provider constructors, the four lifecycle methods, and the
/// one call that puts a provider into the manager.
///
/// A name outside this list is the harness telling the code under test that it asked for
/// something no test has modelled — a `NoSuchMethodError` and a pending exception, the same
/// shape as the real thing.
///
/// **Append only.** The list is the id space, so inserting a name anywhere but the end
/// renumbers everything after it.
const KNOWN_METHODS: [&str; 13] = [
    "getLatitude",
    "getLongitude",
    "getAltitude",
    "getAccuracy",
    "getTime",
    "<init>",
    "onStart",
    "onResume",
    "onPause",
    "onStop",
    "addLocationProvider",
    // The two the background-context token is resolved with — the location chain does not
    // use them, they are here so that a test can tell a correct lookup from a plausible one.
    "getClassLoader",
    "loadClass",
];

/// Base of the handle range `FindClass` hands out. Deliberately far from [`stub_ref`]'s `1`,
/// so a reference the harness did *not* mint reads as unknown rather than as the first class.
const CLASS_BASE: usize = 0x1000;

/// Base of the handle range `NewObject` hands out. See [`CLASS_BASE`].
const OBJECT_BASE: usize = 0x2000;

/// Base of the handle range `NewGlobalRef` hands out.
///
/// A global ref gets a handle of its *own*, not the one it was made from. If it did not, a
/// test could not tell "promoted the object and kept it" from "kept the local reference and
/// let the frame boundary invalidate it" — which is precisely the bug a global ref exists to
/// avoid.
const GLOBAL_BASE: usize = 0x3000;

/// The static fields of `com.unity3d.player.UnityPlayer` the chain reads, in id order.
const UNITY_PLAYER_FIELDS: [&str; 2] = ["currentContext", "currentActivity"];

thread_local! {
    static RECORDER: RefCell<Recorder> = RefCell::new(Recorder::default());
}

/// Run `f` against the recorder, then clear it.
pub fn with_recorder<R>(f: impl FnOnce(&mut Recorder) -> R) -> R {
    RECORDER.with(|r| f(&mut r.borrow_mut()))
}

/// Clear the recorder and the unexpected-call counter.
pub fn reset() {
    RECORDER.with(|r| *r.borrow_mut() = Recorder::default());
    UNEXPECTED_CALLS.store(0, Ordering::Release);
}

/// How many calls landed on an uninstalled slot. Anything but zero means the code under
/// test used a JNI entry the harness does not model, or used a modelled one at the wrong
/// index.
pub fn unexpected_calls() -> usize {
    UNEXPECTED_CALLS.load(Ordering::Acquire)
}

/// Serialize every test that drives the fake JVM.
///
/// The recorder itself is a `thread_local`, so tests do not share *that* — but two things do
/// cross threads, and both are process-global:
///
/// * [`UNEXPECTED_CALLS`], which [`reset`] zeroes and any unmodelled slot increments. A test
///   that asserts it is zero is asserting something about the whole process, so it is only
///   meaningful while nothing else is running.
/// * The `JavaVM` `crate::jni` caches from `GetJavaVM`, which [`create`](crate::bgcontext::create)
///   and the location chain both write.
///
/// A module that keeps its *own* mutex serialises its own tests and nothing else, so a test
/// asserting "no unexpected calls" can still see another module's. Every module that drives
/// this harness therefore shares this one lock — [`crate::bgcontext::test_guard`],
/// [`crate::jni::location_service::test_guard`], [`crate::live::test_guard`] and
/// `jni::register`'s `exclusive` all delegate here.
///
/// Acquiring it also [`reset`]s, because mutual exclusion is only half of what an
/// `unexpected_calls() == 0` assertion needs. The counter is *sticky*: a module's own
/// `reset` clears that module's state and nothing else, so without this the first test to
/// touch an unmodelled slot would leave a permanent 1 behind for whichever test happened to
/// run next. Serialising makes the count stable; clearing on entry makes it *this* test's.
/// Both are required, and getting only the first is a flake that looks like a race.
#[cfg(test)]
pub fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    reset();
    guard
}

// ---------------------------------------------------------------------------
// Stubs
// ---------------------------------------------------------------------------

/// Filler for every slot the harness does not install. Recording the call makes a
/// wrong-slot dispatch visible instead of merely wrong.
unsafe extern "system" fn unused() {
    UNEXPECTED_CALLS.fetch_add(1, Ordering::AcqRel);
}

/// A non-null `jclass`/`jobject`/`jstring` that is never dereferenced.
///
/// For the callee to walk away from a JNI call with a "valid" reference, the stand-in
/// only has to be non-null: `_jobject` is uninhabited, so no read through it is
/// meaningful and none is performed.
pub fn stub_ref() -> jobject {
    1usize as jobject
}

/// Alias of [`stub_ref`] for the string-shaped slots.
pub fn stub_string() -> jstring {
    stub_ref() as jstring
}

/// Mint an instance of `class` without driving a constructor.
///
/// For tests that need a *receiver* — a manager to call `addLocationProvider` on, say — without
/// the construction that would normally produce one. The object behaves like any other
/// `NewObject` result: [`fake_get_object_class`] can name its class, and a global reference
/// made from it can be traced back.
pub fn stub_object(class: &str) -> jobject {
    with_recorder(|r| {
        let handle = (OBJECT_BASE + r.objects.len()) as jobject;
        r.objects.push((handle, class.to_owned()));
        handle
    })
}

/// The handle for a class, registering it on first sight.
fn class_handle(r: &mut Recorder, name: &str) -> jclass {
    let index = match r.classes.iter().position(|c| c == name) {
        Some(i) => i,
        None => {
            r.classes.push(name.to_owned());
            r.classes.len() - 1
        }
    };
    (CLASS_BASE + index) as jclass
}

/// Name a `jclass` this harness minted. `None` for anything else — including
/// [`stub_ref`], whose whole point is to be an unnamed object.
fn class_name(r: &Recorder, class: jclass) -> Option<String> {
    (class as usize)
        .checked_sub(CLASS_BASE)
        .and_then(|i| r.classes.get(i))
        .cloned()
}

/// Whether a `()V`-shaped method actually takes an argument.
///
/// `CallVoidMethod` is variadic in the JNI header, so the harness can only *safely* read the
/// argument register for the methods that have one. This is the list; everything else in
/// [`KNOWN_METHODS`] is dispatched with an empty tail.
fn takes_an_argument(method: &str) -> bool {
    method == "addLocationProvider"
}

/// The name behind a `jmethodID`, or `None` if the harness did not mint it.
///
/// Public for the same reason [`stub_object`] is: a test that only ever handles a `jmethodID`
/// opaquely cannot tell "resolved the method it meant to" from "resolved some other method and
/// stored that", which is the failure the ids exist to make visible.
pub fn method_name(method: jmethodID) -> Option<&'static str> {
    (method as usize)
        .checked_sub(1)
        .and_then(|i| KNOWN_METHODS.get(i))
        .copied()
}

unsafe extern "system" fn fake_find_class(_env: *mut JNIEnv, name: *const c_char) -> jclass {
    let name = core::ffi::CStr::from_ptr(name).to_string_lossy().into_owned();
    with_recorder(|r| {
        if r.missing.iter().any(|m| m == &name) {
            r.pending_exception = true;
            return core::ptr::null_mut();
        }
        r.find_class.push(name.clone());
        r.events.push(Event::FindClass(name.clone()));
        class_handle(r, &name)
    })
}

unsafe extern "system" fn fake_exception_check(_env: *mut JNIEnv) -> jboolean {
    with_recorder(|r| {
        r.exception_check += 1;
        r.pending_exception
    })
}

unsafe extern "system" fn fake_exception_clear(_env: *mut JNIEnv) {
    with_recorder(|r| {
        r.exception_clear += 1;
        r.pending_exception = false;
    })
}

unsafe extern "system" fn fake_delete_local_ref(_env: *mut JNIEnv, _obj: jobject) {
    with_recorder(|r| r.delete_local_ref += 1);
}

unsafe extern "system" fn fake_register_natives(
    _env: *mut JNIEnv,
    _clazz: jclass,
    methods: *const JNINativeMethod,
    count: jint,
) -> jint {
    with_recorder(|r| {
        if r.fail_register {
            r.pending_exception = true;
            return -1;
        }
        let mut rows = Vec::new();
        for i in 0..count.max(0) as usize {
            // `CStr::from_ptr` on a literal without its NUL terminator would run past
            // the end of the string here — which is the point.
            let m = &*methods.add(i);
            rows.push((
                core::ffi::CStr::from_ptr(m.name).to_string_lossy().into_owned(),
                core::ffi::CStr::from_ptr(m.signature).to_string_lossy().into_owned(),
                m.fnPtr,
            ));
        }
        r.registered.push(rows);
        JNI_OK
    })
}

unsafe extern "system" fn fake_new_string_utf(
    _env: *mut JNIEnv,
    utf: *const c_char,
) -> jstring {
    let s = core::ffi::CStr::from_ptr(utf).to_string_lossy().into_owned();
    with_recorder(|r| r.new_strings.push(s));
    stub_ref() as jstring
}

unsafe extern "system" fn fake_get_string_utf_chars(
    _env: *mut JNIEnv,
    _str: jstring,
    _is_copy: *mut jboolean,
) -> *const c_char {
    with_recorder(|r| {
        r.get_string_utf_chars += 1;
        match r.next_string {
            Some(s) => s.as_ptr() as *const c_char,
            None => core::ptr::null(),
        }
    })
}

unsafe extern "system" fn fake_release_string_utf_chars(
    _env: *mut JNIEnv,
    _str: jstring,
    _chars: *const c_char,
) {
    with_recorder(|r| r.release_string_utf_chars += 1);
}

unsafe extern "system" fn fake_new_object_array(
    _env: *mut JNIEnv,
    len: jsize,
    clazz: jclass,
    init: jobject,
) -> jobjectArray {
    with_recorder(|r| r.new_object_array = Some((len, clazz, init)));
    stub_ref() as jobjectArray
}

/// Resolve a getter to `index + 1`, or fail the way a real `GetMethodID` does.
///
/// Returning the *index* rather than a fixed sentinel is what makes the stub honest: it
/// answers per name, so a mis-ordered resolve is visible as a wrong value rather than as a
/// silently consistent one.
unsafe extern "system" fn fake_get_method_id(
    _env: *mut JNIEnv,
    _class: jclass,
    name: *const c_char,
    sig: *const c_char,
) -> jmethodID {
    let name = core::ffi::CStr::from_ptr(name).to_string_lossy().into_owned();
    let sig = core::ffi::CStr::from_ptr(sig).to_string_lossy().into_owned();
    with_recorder(|r| {
        r.method_ids.push((name.clone(), sig));
        if r.fail_get_method_id {
            r.pending_exception = true;
            return core::ptr::null_mut();
        }
        match KNOWN_METHODS.iter().position(|g| *g == name) {
            // `+1`, because a null `jmethodID` is a failure and index 0 must not be one.
            Some(i) => (i + 1) as jmethodID,
            None => {
                r.pending_exception = true;
                core::ptr::null_mut()
            }
        }
    })
}

/// The class of the object behind a handle this harness minted, following one link of
/// global-reference indirection.
///
/// `None` for anything else, and null is the honest answer there — a plausible-looking
/// stand-in would hide a call on the wrong object, which is the failure this is here to catch.
fn object_class(r: &Recorder, obj: jobject) -> Option<String> {
    let of = r
        .global_refs
        .iter()
        .find(|(global, _)| *global == obj)
        .map_or(obj, |(_, of)| *of);
    (of as usize)
        .checked_sub(OBJECT_BASE)
        .and_then(|i| r.objects.get(i))
        .map(|(_, class)| class.clone())
}

unsafe extern "system" fn fake_get_object_class(_env: *mut JNIEnv, obj: jobject) -> jclass {
    with_recorder(|r| {
        r.get_object_class += 1;
        match object_class(r, obj) {
            Some(name) => class_handle(r, &name),
            None => core::ptr::null_mut(),
        }
    })
}

unsafe extern "system" fn fake_new_global_ref(_env: *mut JNIEnv, obj: jobject) -> jobject {
    if obj.is_null() {
        // A real `NewGlobalRef` on null returns null; the caller's "did this work" test is
        // then the same one it already makes for every other failure.
        return core::ptr::null_mut();
    }
    with_recorder(|r| {
        r.new_global_refs += 1;
        let handle = (GLOBAL_BASE + r.new_global_refs) as jobject;
        r.global_refs.push((handle, obj));
        r.events.push(Event::NewGlobalRef { into: handle });
        handle
    })
}

unsafe extern "system" fn fake_delete_global_ref(_env: *mut JNIEnv, obj: jobject) {
    if obj.is_null() {
        return;
    }
    with_recorder(|r| {
        r.delete_global_refs += 1;
        r.events.push(Event::DeleteGlobalRef { of: obj });
    })
}

unsafe extern "system" fn fake_get_static_field_id(
    _env: *mut JNIEnv,
    _class: jclass,
    name: *const c_char,
    _sig: *const c_char,
) -> jfieldID {
    let name = core::ffi::CStr::from_ptr(name).to_string_lossy().into_owned();
    with_recorder(|r| {
        r.events.push(Event::GetStaticFieldId(name.clone()));
        if r.static_field_missing {
            r.pending_exception = true;
            return core::ptr::null_mut();
        }
        match UNITY_PLAYER_FIELDS.iter().position(|f| *f == name) {
            Some(i) => (i + 1) as jfieldID,
            None => {
                r.pending_exception = true;
                core::ptr::null_mut()
            }
        }
    })
}

unsafe extern "system" fn fake_get_static_object_field(
    _env: *mut JNIEnv,
    _class: jclass,
    field: jfieldID,
) -> jobject {
    with_recorder(|r| {
        let index = (field as usize).checked_sub(1);
        let name = index.and_then(|i| UNITY_PLAYER_FIELDS.get(i)).copied();
        let Some(name) = name else {
            r.pending_exception = true;
            return core::ptr::null_mut();
        };
        r.events.push(Event::GetStaticObjectField(name.to_owned()));
        match name {
            "currentContext" => r.current_context.unwrap_or(core::ptr::null_mut()),
            _ => r.current_activity.unwrap_or(core::ptr::null_mut()),
        }
    })
}

/// `NewObject`, against either of the two constructor shapes
/// [`super::new_object_context`] and [`super::new_object_context_pointer`] model.
///
/// The class is checked before the constructor id: a call against a class the harness never
/// resolved, or against a `jmethodID` that is not `<init>`, is the mistake a variadic call
/// would otherwise carry all the way into the JVM.
unsafe extern "C" fn fake_new_object(
    _env: *mut JNIEnv,
    class: jclass,
    ctor: jmethodID,
) -> jobject {
    with_recorder(|r| {
        let Some(name) = class_name(r, class) else {
            r.pending_exception = true;
            return core::ptr::null_mut();
        };
        if method_name(ctor) != Some("<init>") {
            r.pending_exception = true;
            return core::ptr::null_mut();
        }
        if r.throwing_constructors.contains(&name) {
            r.pending_exception = true;
            return core::ptr::null_mut();
        }
        let handle = (OBJECT_BASE + r.objects.len()) as jobject;
        r.objects.push((handle, name.clone()));
        r.events.push(Event::NewObject {
            class: name,
            handle,
        });
        handle
    })
}

unsafe extern "C" fn fake_call_void_method(
    _env: *mut JNIEnv,
    obj: jobject,
    method: jmethodID,
    arg: jobject,
) {
    with_recorder(|r| {
        let name = method_name(method).unwrap_or("<unknown>").to_owned();
        // Only read the variadic tail for the method that actually takes one. `CallVoidMethod`
        // is variadic in the header, so a callee that reads an argument the caller never
        // passed is reading an indeterminate register — and the lifecycle calls in this crate
        // pass none. Gating on the name is what makes the read defined.
        let argument = if takes_an_argument(&name) { Some(arg) } else { None };
        r.calls_void.push((obj, name.clone(), argument));
        r.events.push(Event::CallVoid {
            receiver: obj,
            method: name.clone(),
            argument,
        });
        if r.throwing_calls.contains(&name) {
            r.pending_exception = true;
        }
    })
}

/// Decode the index [`fake_get_method_id`] encoded. `None` for null or foreign pointers.
fn getter_index(method: jmethodID) -> Option<usize> {
    (method as usize).checked_sub(1).filter(|i| *i < LOCATION_GETTERS.len())
}

// `jni-sys` declares the `Call*Method` entries variadic (`..., ...`), so the stubs below
// mirror `extern "C"` rather than the `extern "system"` used above. On the two targets here
// the conventions are the same ABI, but matching the declaration keeps that a property of
// the compiler rather than of the platform.

unsafe extern "C" fn fake_call_double_method(
    _env: *mut JNIEnv,
    _obj: jobject,
    method: jmethodID,
) -> f64 {
    with_recorder(|r| {
        r.call_double += 1;
        match getter_index(method) {
            Some(0) => r.location.0,
            Some(1) => r.location.1,
            Some(2) => r.location.2,
            _ => 0.0,
        }
    })
}

unsafe extern "C" fn fake_call_float_method(
    _env: *mut JNIEnv,
    _obj: jobject,
    method: jmethodID,
) -> f32 {
    with_recorder(|r| {
        r.call_float += 1;
        match getter_index(method) {
            // `getAccuracy()F` is the only float getter here — and it is the one that
            // would be silently truncated by widening in the wrong direction.
            Some(3) => r.location.3,
            _ => 0.0,
        }
    })
}

unsafe extern "C" fn fake_call_long_method(
    _env: *mut JNIEnv,
    _obj: jobject,
    method: jmethodID,
) -> i64 {
    with_recorder(|r| {
        r.call_long += 1;
        match getter_index(method) {
            Some(4) => r.location.4,
            _ => 0,
        }
    })
}

/// `CallObjectMethod`, which the background-context token is resolved through.
///
/// Three fixed parameters, like its siblings above and for the same reason: the entry is
/// variadic in the header, so reading a fourth would read an indeterminate register. The one
/// caller resolves a no-argument method, so there is nothing to read.
///
/// `getClassLoader` answers with a *named* object rather than [`stub_ref`] — the whole point
/// of that call is that the result has a class of its own, and a stand-in the harness cannot
/// name would make `GetObjectClass` on it read as null, which is the same answer a genuine
/// failure gives. Every other name returns null: the harness does not know what it returns,
/// and inventing one would let a wrong lookup look like a working one.
unsafe extern "C" fn fake_call_object_method(
    _env: *mut JNIEnv,
    obj: jobject,
    method: jmethodID,
) -> jobject {
    let name = method_name(method).unwrap_or("<unknown>").to_owned();
    let answer = match name.as_str() {
        "getClassLoader" => stub_object("java/lang/ClassLoader"),
        _ => core::ptr::null_mut(),
    };
    with_recorder(|r| {
        r.calls_object.push((obj, name.clone()));
        r.events.push(Event::CallObject {
            receiver: obj,
            method: name,
        });
    });
    answer
}

// ---------------------------------------------------------------------------
// The harness
// ---------------------------------------------------------------------------

/// A fake `JNIEnv*` ready to hand to the code under test.
///
/// Holds the backing storage alive; the env pointer itself is leaked, which is fine for
/// a test process.
pub struct FakeEnv {
    /// Valid only while this `FakeEnv` is alive — the table lives here.
    pub env: *mut JNIEnv,
    /// Kept solely so the table outlives the pointer into it.
    _table: Box<[usize; SLOTS]>,
}

/// Build a table of `[usize; SLOTS]` with the stubs installed and return an `JNIEnv*`
/// pointing at it.
pub fn table() -> FakeEnv {
    // The reinterpretation below is only in bounds if the struct fits.
    assert!(
        core::mem::size_of::<JNINativeInterface__1_6>() <= SLOTS * 8,
        "fake table is smaller than jni-sys's v1_6 view",
    );

    let mut slots: Box<[usize; SLOTS]> = Box::new([0; SLOTS]);
    let filler = unused as *const () as usize;
    for slot in slots.iter_mut() {
        *slot = filler;
    }

    slots[FIND_CLASS] = fake_find_class as *const () as usize;
    slots[EXCEPTION_CLEAR] = fake_exception_clear as *const () as usize;
    slots[NEW_GLOBAL_REF] = fake_new_global_ref as *const () as usize;
    slots[DELETE_GLOBAL_REF] = fake_delete_global_ref as *const () as usize;
    slots[DELETE_LOCAL_REF] = fake_delete_local_ref as *const () as usize;
    slots[NEW_OBJECT] = fake_new_object as *const () as usize;
    slots[GET_OBJECT_CLASS] = fake_get_object_class as *const () as usize;
    slots[GET_METHOD_ID] = fake_get_method_id as *const () as usize;
    slots[CALL_VOID_METHOD] = fake_call_void_method as *const () as usize;
    slots[CALL_OBJECT_METHOD] = fake_call_object_method as *const () as usize;
    slots[GET_STATIC_FIELD_ID] = fake_get_static_field_id as *const () as usize;
    slots[GET_STATIC_OBJECT_FIELD] = fake_get_static_object_field as *const () as usize;
    slots[CALL_LONG_METHOD] = fake_call_long_method as *const () as usize;
    slots[CALL_FLOAT_METHOD] = fake_call_float_method as *const () as usize;
    slots[CALL_DOUBLE_METHOD] = fake_call_double_method as *const () as usize;
    slots[NEW_STRING_UTF] = fake_new_string_utf as *const () as usize;
    slots[GET_STRING_UTF_CHARS] = fake_get_string_utf_chars as *const () as usize;
    slots[RELEASE_STRING_UTF_CHARS] = fake_release_string_utf_chars as *const () as usize;
    slots[NEW_OBJECT_ARRAY] = fake_new_object_array as *const () as usize;
    slots[REGISTER_NATIVES] = fake_register_natives as *const () as usize;
    slots[EXCEPTION_CHECK] = fake_exception_check as *const () as usize;

    // `JNIEnv` is `*const JNINativeInterface_`, so a `JNIEnv*` is a pointer to that —
    // exactly the two-level indirection a real JVM passes to a native method.
    let cell = Box::into_raw(Box::new(slots.as_ptr() as *const JNINativeInterface_));
    FakeEnv {
        env: cell as *mut JNIEnv,
        _table: slots,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The chain's method names are *appended* to the getters, so ids `1..=5` still mean what
    /// they meant. Several tests in `natives.rs` assert against [`Recorder::location`], which
    /// is decoded through those ids — reordering this list would silently repoint them at the
    /// wrong getters and the assertions would keep passing.
    #[test]
    fn the_chain_methods_did_not_shift_the_getter_ids() {
        for (i, getter) in LOCATION_GETTERS.iter().enumerate() {
            assert_eq!(KNOWN_METHODS[i], *getter, "id {} moved", i + 1);
        }
    }

    /// Every slot the harness installs has to be inside the table, and every handle range has
    /// to be distinguishable from every other — otherwise a `jclass`, a `jobject` or a global
    /// ref could compare equal and a test could pass by accident.
    #[test]
    fn the_handle_ranges_and_slots_do_not_collide() {
        let highest = GET_STATIC_OBJECT_FIELD;
        assert!(highest < SLOTS, "slot {highest} is outside the {SLOTS}-slot table");
        assert_ne!(stub_ref() as usize, CLASS_BASE);
        assert_ne!(stub_ref() as usize, OBJECT_BASE);
        assert_ne!(stub_ref() as usize, GLOBAL_BASE);
        assert_ne!(CLASS_BASE, OBJECT_BASE);
        assert_ne!(CLASS_BASE, GLOBAL_BASE);
        assert_ne!(OBJECT_BASE, GLOBAL_BASE);
    }

    /// The harness mints handles it can name again, and refuses to name ones it did not.
    #[test]
    fn handles_round_trip_through_their_names() {
        reset();
        with_recorder(|r| {
            let a = class_handle(r, "com/example/A");
            let b = class_handle(r, "com/example/B");
            let a2 = class_handle(r, "com/example/A");
            assert_eq!(a, a2, "the same class must get a stable handle");
            assert_ne!(a, b);
            assert_eq!(class_name(r, a).as_deref(), Some("com/example/A"));
            assert_eq!(class_name(r, b).as_deref(), Some("com/example/B"));
            assert_eq!(class_name(r, stub_ref() as jclass), None);

            assert_eq!(method_name(1 as jmethodID), Some("getLatitude"));
            // The id space runs to the end of the list and no further, whatever the list's
            // length is — the two background-context names are the current last entries.
            assert_eq!(
                method_name(KNOWN_METHODS.len() as jmethodID),
                Some("loadClass"),
            );
            assert_eq!(
                method_name((KNOWN_METHODS.len() - 1) as jmethodID),
                Some("getClassLoader"),
            );
            // A null `jmethodID` and an unminted pointer both have to read as unknown, or
            // the `<init>` guard in `fake_new_object` would accept a null constructor.
            assert_eq!(method_name(core::ptr::null_mut()), None);
            assert_eq!(method_name(0x9999 as jmethodID), None);
        });
    }
}
