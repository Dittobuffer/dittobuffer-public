//! The JNI layer, built directly on `jni-sys`.
//!
//! Every native entry point the JVM can reach lives here, so that the `JNIEnv`
//! double-indirection (`JNIEnv` in `jni-sys` is itself a bare `*const
//! JNINativeInterface_`) is dereferenced in exactly one place — `table` — rather than
//! at every call site.
//!
//! These wrappers are total: none of them panics, because `panic = "abort"` turns a
//! panic in here into a dead game process.

pub mod location_service;
pub mod natives;
pub mod register;

#[cfg(test)]
pub(crate) mod fake_jni;

use core::ffi::{c_char, c_void};
use core::ptr;
use core::sync::atomic::{AtomicPtr, Ordering};
use jni_sys::{
    jclass, jfieldID, jint, jlong, jmethodID, jobject, jobjectArray, jstring, JNIEnv,
    JNINativeInterface__1_6, JNINativeMethod, JavaVM, JNI_OK, JNI_VERSION_1_6,
};

/// The JNI function table behind an env pointer.
///
/// `jni-sys` exposes the table as a union of per-version views rather than one flat
/// struct, so the version has to be named. `v1_6` is the one to use: it is the version
/// requested from `GetEnv`, it carries every entry declared up to and including 1.6,
/// and the JNI table is append-only, so the earlier entries sit at identical offsets in
/// every view.
///
/// The returned reference is `'static` because the table lives as long as the VM and
/// outlives every thread that reaches it; it never escapes this module.
unsafe fn table(env: *mut JNIEnv) -> Option<&'static JNINativeInterface__1_6> {
    if env.is_null() {
        None
    } else {
        Some(&(**env).v1_6)
    }
}

// --- the VM cache ------------------------------------------------------------
//
// The 36 `N2*` vtable slots are handed a `Handle` and nothing else — no `JNIEnv`. So a slot
// that needs to call Java (which is what rebuilding the location chain requires) has no env
// to call it with. `JNI_OnLoad` and `nativeInit` are both given one, so the `JavaVM*` is
// cached from there and an env resolved per call.
//
// The thread does not need attaching: every caller is a Java thread already — `OnStart` and
// the rest arrive from `PluginBindings`, and the location callbacks arrive on Java's own
// `ContextService` handler thread. `GetEnv` therefore succeeds and no `AttachCurrentThread`
// is ever needed.

/// The `JavaVM*` from `JNI_OnLoad`. Null until the JVM loads the library.
static VM: AtomicPtr<JavaVM> = AtomicPtr::new(ptr::null_mut());

/// Cache the VM. Called from `JNI_OnLoad` and `nativeInit`; a null `vm` is ignored, so a
/// caller that could not get an env does not erase a VM an earlier caller did store.
pub unsafe fn set_vm(vm: *mut JavaVM) {
    if !vm.is_null() {
        VM.store(vm, Ordering::Release);
    }
}

/// The calling thread's env, resolved through the cached VM.
///
/// Null when the VM is not cached yet *or* the thread is unattached. Callers must treat null
/// as "not yet" and return, never as an error to report — at `JNI_OnLoad` time there is
/// nothing wrong, the VM simply has not finished initialising.
pub unsafe fn cached_env() -> *mut JNIEnv {
    env_from_vm(VM.load(Ordering::Acquire))
}

/// True once a VM has been cached, whether or not this thread is attached.
///
/// Distinct from `!cached_env().is_null()` on purpose: the two answer different questions, and
/// a caller diagnosing a failure wants to know which one it hit.
pub fn vm_is_cached() -> bool {
    !VM.load(Ordering::Acquire).is_null()
}

/// Resolve a class by its JNI path, e.g. `com/nianticlabs/nia/unity/UnityUtil`.
///
/// A failure here throws `NoClassDefFoundError`, leaving a pending exception — the
/// caller must [clear it](clear_exception) before making any further JNI call.
pub unsafe fn find_class(env: *mut JNIEnv, name: *const c_char) -> jclass {
    match table(env) {
        Some(t) => (t.FindClass)(env, name),
        None => ptr::null_mut(),
    }
}

/// Discard a pending exception.
///
/// JNI is only defined while no exception is pending, so every path that can leave one
/// behind — a failed `FindClass`, in practice — has to clear it before continuing.
pub unsafe fn clear_exception(env: *mut JNIEnv) {
    if let Some(t) = table(env) {
        // `jboolean` is a plain `bool` in jni-sys 0.4, so the raw bool is already the test.
        if (t.ExceptionCheck)(env) {
            (t.ExceptionClear)(env);
        }
    }
}

/// Release a local reference. Tolerates null so callers need not branch.
pub unsafe fn delete_local_ref(env: *mut JNIEnv, obj: jobject) {
    if obj.is_null() {
        return;
    }
    if let Some(t) = table(env) {
        (t.DeleteLocalRef)(env, obj);
    }
}

/// `RegisterNatives`. Returns true only on `JNI_OK`.
pub unsafe fn register_natives(
    env: *mut JNIEnv,
    class: jclass,
    methods: *const JNINativeMethod,
    count: jint,
) -> bool {
    match table(env) {
        Some(t) => (t.RegisterNatives)(env, class, methods, count) == JNI_OK,
        None => false,
    }
}

/// Create a Java string, or return null if `s` contains an interior NUL.
pub unsafe fn new_string(env: *mut JNIEnv, s: &str) -> jstring {
    let Ok(c) = std::ffi::CString::new(s) else {
        return ptr::null_mut();
    };
    match table(env) {
        Some(t) => (t.NewStringUTF)(env, c.as_ptr()),
        None => ptr::null_mut(),
    }
}

/// Copy a Java string into an owned `String`. `None` for null or non-UTF-8 input.
pub unsafe fn string_from_java(env: *mut JNIEnv, s: jstring) -> Option<String> {
    if s.is_null() {
        return None;
    }
    let t = table(env)?;
    // A null `isCopy` is legal here and saves a local.
    let chars = (t.GetStringUTFChars)(env, s, ptr::null_mut());
    if chars.is_null() {
        // Out of memory; ART leaves no exception for this one.
        return None;
    }
    let out = core::ffi::CStr::from_ptr(chars)
        .to_str()
        .ok()
        .map(str::to_owned);
    (t.ReleaseStringUTFChars)(env, s, chars);
    out
}

/// A zero-length array of `element_class`.
///
/// `NianticTrustManager.nativeGetAcceptedIssuers` must not return null — callers treat
/// the result as an array — so it returns one of these instead.
pub unsafe fn empty_object_array(env: *mut JNIEnv, element_class: *const c_char) -> jobjectArray {
    let cls = find_class(env, element_class);
    let Some(t) = table(env) else {
        return ptr::null_mut();
    };
    if cls.is_null() {
        // FindClass threw; clear before touching JNI again.
        clear_exception(env);
        return ptr::null_mut();
    }
    let arr = (t.NewObjectArray)(env, 0, cls, ptr::null_mut());
    (t.DeleteLocalRef)(env, cls);
    arr
}

/// Resolve a method on `class`, e.g. `("getTime", "()J")`.
///
/// A failure here throws `NoSuchMethodError`, leaving a pending exception — the same
/// discipline as [`find_class`] applies: clear it before any further JNI call.
pub unsafe fn get_method_id(
    env: *mut JNIEnv,
    class: jclass,
    name: *const c_char,
    signature: *const c_char,
) -> jmethodID {
    if class.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.GetMethodID)(env, class, name, signature),
        None => ptr::null_mut(),
    }
}

/// The class of an instance, as a local reference.
///
/// The alternative — `FindClass` on a hard-coded path — is the one thing that *cannot* be used
/// for the Niantic objects built from native: they come out of R8, so their paths have to be
/// spelled out in the smali's own terms and re-checked every version. When the object is
/// already in hand its class is free and cannot drift.
pub unsafe fn get_object_class(env: *mut JNIEnv, obj: jobject) -> jclass {
    if obj.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.GetObjectClass)(env, obj),
        None => ptr::null_mut(),
    }
}

/// Resolve a **static** field, e.g. `("currentContext", "Landroid/content/Context;")`.
///
/// Same discipline as [`get_method_id`]: a failure throws `NoSuchFieldError`, so the caller
/// must clear before the next JNI call.
pub unsafe fn get_static_field_id(
    env: *mut JNIEnv,
    class: jclass,
    name: *const c_char,
    signature: *const c_char,
) -> jfieldID {
    if class.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.GetStaticFieldID)(env, class, name, signature),
        None => ptr::null_mut(),
    }
}

/// Read a static object field. Null on a null env, class, or field.
pub unsafe fn get_static_object_field(
    env: *mut JNIEnv,
    class: jclass,
    field: jfieldID,
) -> jobject {
    if class.is_null() || field.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.GetStaticObjectField)(env, class, field),
        None => ptr::null_mut(),
    }
}

/// `NewObject` against a `<init>(Landroid/content/Context;)V`.
///
/// The JNI header declares `NewObject` variadic, and a variadic call has to pass its arguments
/// at their *promoted* types — so rather than expose a `…` tail this module offers the two
/// constructor shapes the location chain actually uses, each spelled out against the smali
/// descriptor it matches. Getting the shape wrong is then a compile error rather than a wrong
/// word handed to a variadic.
pub unsafe fn new_object_context(
    env: *mut JNIEnv,
    class: jclass,
    ctor: jmethodID,
    context: jobject,
) -> jobject {
    if class.is_null() || ctor.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.NewObject)(env, class, ctor, context),
        None => ptr::null_mut(),
    }
}

/// `NewObject` against a `<init>(Landroid/content/Context;J)V` — the shape every
/// `ContextService` subclass carries, where the `long` is the native side's `this`.
pub unsafe fn new_object_context_pointer(
    env: *mut JNIEnv,
    class: jclass,
    ctor: jmethodID,
    context: jobject,
    pointer: jlong,
) -> jobject {
    if class.is_null() || ctor.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.NewObject)(env, class, ctor, context, pointer),
        None => ptr::null_mut(),
    }
}

/// Call a `()V` Java method.
///
/// Variadic in the header with an empty tail, for the same reason the `Call*Method` getters
/// above are: the methods this drives take no arguments.
///
/// A Java exception thrown by the body is *not* cleared here — a `void` call has no result to
/// report and the caller is the only one that knows whether a throw is tolerable. Every caller
/// in this crate must follow with an [`exception_occurred`] check.
pub unsafe fn call_void_method(env: *mut JNIEnv, obj: jobject, method: jmethodID) {
    if obj.is_null() || method.is_null() {
        return;
    }
    if let Some(t) = table(env) {
        (t.CallVoidMethod)(env, obj, method);
    }
}

/// Call a `(Lcom/nianticlabs/nia/location/NianticLocationProvider;)V` Java method.
///
/// The one-argument sibling of [`call_void_method`], and the reason it exists rather than a
/// single `…`-taking wrapper: `CallVoidMethod` is variadic in the header, so the argument has
/// to be passed at its promoted type by a call site that knows the descriptor. Getting the
/// shape wrong here is silent — a variadic callee reads whatever is in the register — which is
/// precisely the class of bug the fake JVM exists to catch, and precisely why the shape is
/// spelled out per signature instead of being left to the caller.
pub unsafe fn call_void_method_object(
    env: *mut JNIEnv,
    obj: jobject,
    method: jmethodID,
    arg: jobject,
) {
    if obj.is_null() || method.is_null() {
        return;
    }
    if let Some(t) = table(env) {
        (t.CallVoidMethod)(env, obj, method, arg);
    }
}

/// Check for and discard a pending exception, reporting whether there was one.
///
/// The counterpart to [`clear_exception`], for the call sites that need to *know*: a `void`
/// method that threw leaves the same observable state as one that quietly did nothing, and the
/// difference decides whether the Java object in hand is still usable.
pub unsafe fn exception_occurred(env: *mut JNIEnv) -> bool {
    match table(env) {
        Some(t) if (t.ExceptionCheck)(env) => {
            (t.ExceptionClear)(env);
            true
        }
        _ => false,
    }
}

/// Promote a local reference so it survives the native method that created it.
///
/// Returns null on failure (out of memory, or a null input), which is the same "not yet"
/// the rest of this module returns.
pub unsafe fn new_global_ref(env: *mut JNIEnv, obj: jobject) -> jobject {
    if obj.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.NewGlobalRef)(env, obj),
        None => ptr::null_mut(),
    }
}

/// Release a global reference. Tolerates null so callers need not branch.
pub unsafe fn delete_global_ref(env: *mut JNIEnv, obj: jobject) {
    if obj.is_null() {
        return;
    }
    if let Some(t) = table(env) {
        (t.DeleteGlobalRef)(env, obj);
    }
}

/// Call a `()D` Java method. Returns `0.0` on a null env.
///
/// The `Call*Method` entries are variadic in the JNI header; the getters this module
/// resolves take no arguments, so the variadic tail is simply empty.
pub unsafe fn call_double_method(env: *mut JNIEnv, obj: jobject, method: jmethodID) -> f64 {
    match table(env) {
        Some(t) => (t.CallDoubleMethod)(env, obj, method),
        None => 0.0,
    }
}

/// Call a `()F` Java method. Returns `0.0` on a null env.
pub unsafe fn call_float_method(env: *mut JNIEnv, obj: jobject, method: jmethodID) -> f32 {
    match table(env) {
        Some(t) => (t.CallFloatMethod)(env, obj, method),
        None => 0.0,
    }
}

/// Call a `()J` Java method. Returns `0` on a null env.
pub unsafe fn call_long_method(env: *mut JNIEnv, obj: jobject, method: jmethodID) -> i64 {
    match table(env) {
        Some(t) => (t.CallLongMethod)(env, obj, method),
        None => 0,
    }
}

/// Call a Java method that returns an object, and take the reference it produced.
///
/// The `Call*Method` entries are variadic in the header, so the tail is empty *by
/// construction*: the only caller resolves `()Ljava/lang/ClassLoader;`, which takes nothing.
/// A descriptor with arguments would need its own wrapper for the same reason
/// [`call_void_method_object`] exists.
///
/// Null on a null `env`, receiver or method — and also when the body threw, since a pending
/// `Throwable` makes JNI hand back null. The two are not distinguished here; the caller
/// checks with [`exception_occurred`], which is what separates "no class loader" from "the
/// call failed", the same way every other lookup in this module is checked.
pub unsafe fn call_object_method(
    env: *mut JNIEnv,
    obj: jobject,
    method: jmethodID,
) -> jobject {
    if obj.is_null() || method.is_null() {
        return ptr::null_mut();
    }
    match table(env) {
        Some(t) => (t.CallObjectMethod)(env, obj, method),
        None => ptr::null_mut(),
    }
}

/// Borrow the `JavaVM*` behind a `JNIEnv*`.
///
/// The inverse of [`env_from_vm`], for the entry points that are handed an env and need to
/// cache the VM from it. Null on a null env or a failed `GetJavaVM`.
pub unsafe fn env_to_vm(env: *mut JNIEnv) -> *mut JavaVM {
    let Some(t) = table(env) else {
        return ptr::null_mut();
    };
    let mut vm: *mut JavaVM = ptr::null_mut();
    if (t.GetJavaVM)(env, &mut vm) == JNI_OK {
        vm
    } else {
        ptr::null_mut()
    }
}

/// Borrow the calling thread's `JNIEnv` from a `JavaVM*`.
///
/// Returns null if `vm` is null or the thread is not attached to the VM.
pub unsafe fn env_from_vm(vm: *mut JavaVM) -> *mut JNIEnv {
    if vm.is_null() {
        return ptr::null_mut();
    }
    let mut env: *mut c_void = ptr::null_mut();
    let rc = ((**vm).v1_4.GetEnv)(vm, &mut env, JNI_VERSION_1_6);
    if rc != JNI_OK {
        return ptr::null_mut();
    }
    env as *mut JNIEnv
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::mem::offset_of;

    /// Slot indices below come from the NDK's `jni.h` (`struct JNINativeInterface`, 233
    /// entries), which is the only authority on the table layout; each slot is one
    /// pointer, so offset = slot × 8.
    ///
    /// This is the check that matters for the whole JNI layer. `jni-sys` presents the
    /// table as a union of per-version views, so `(**env).v1_6.FindClass` is only
    /// correct if that view reproduces the header's ordering exactly. A mismatch does
    /// not fail to compile — it calls an unrelated function pointer with the wrong
    /// arguments, on device, at boot. Asserting the offsets turns that into a failed
    /// `cargo test`.
    #[test]
    fn native_table_slots_match_the_ndk_header() {
        type T = JNINativeInterface__1_6;
        assert_eq!(offset_of!(T, GetVersion), 4 * 8);
        assert_eq!(offset_of!(T, FindClass), 6 * 8);
        assert_eq!(offset_of!(T, ExceptionClear), 17 * 8);
        assert_eq!(offset_of!(T, NewGlobalRef), 21 * 8);
        assert_eq!(offset_of!(T, DeleteGlobalRef), 22 * 8);
        assert_eq!(offset_of!(T, DeleteLocalRef), 23 * 8);
        assert_eq!(offset_of!(T, NewObject), 28 * 8);
        assert_eq!(offset_of!(T, GetObjectClass), 31 * 8);
        assert_eq!(offset_of!(T, GetMethodID), 33 * 8);
        assert_eq!(offset_of!(T, CallObjectMethod), 34 * 8);
        // The slot the original's varargs shim at `0x582fb8` forwards to. Both are pinned
        // because the two differ by one pointer: reading the wrong one hands the callee a
        // `va_list` where it expects the arguments themselves.
        assert_eq!(offset_of!(T, CallObjectMethodV), 35 * 8);
        assert_eq!(offset_of!(T, CallLongMethod), 52 * 8);
        assert_eq!(offset_of!(T, CallFloatMethod), 55 * 8);
        assert_eq!(offset_of!(T, CallDoubleMethod), 58 * 8);
        assert_eq!(offset_of!(T, CallVoidMethod), 61 * 8);
        assert_eq!(offset_of!(T, GetLongField), 101 * 8);
        assert_eq!(offset_of!(T, GetStaticFieldID), 144 * 8);
        assert_eq!(offset_of!(T, GetStaticObjectField), 145 * 8);
        assert_eq!(offset_of!(T, NewStringUTF), 167 * 8);
        assert_eq!(offset_of!(T, GetStringUTFChars), 169 * 8);
        assert_eq!(offset_of!(T, ReleaseStringUTFChars), 170 * 8);
        assert_eq!(offset_of!(T, NewObjectArray), 172 * 8);
        assert_eq!(offset_of!(T, RegisterNatives), 215 * 8);
        assert_eq!(offset_of!(T, GetJavaVM), 219 * 8);
        assert_eq!(offset_of!(T, ExceptionCheck), 228 * 8);
        assert_eq!(offset_of!(T, GetDirectBufferAddress), 230 * 8);
    }

    /// `GetEnv` is slot 6 (`0x30`) of `JNIInvokeInterface`, not of the native table.
    #[test]
    fn invoke_table_slots_match_the_ndk_header() {
        assert_eq!(offset_of!(jni_sys::JNIInvokeInterface__1_4, GetEnv), 6 * 8);
    }

    /// Every wrapper is total on a null env. `nativeInit` is reachable by symbol name
    /// from anything that can `dlsym` it, so none of these may fault.
    #[test]
    fn wrappers_tolerate_a_null_env() {
        unsafe {
            let env: *mut JNIEnv = ptr::null_mut();
            assert!(table(env).is_none());
            assert!(find_class(env, c"java/lang/Object".as_ptr()).is_null());
            assert!(new_string(env, "x").is_null());
            assert!(string_from_java(env, ptr::null_mut()).is_none());
            assert!(empty_object_array(env, c"java/lang/Object".as_ptr()).is_null());
            assert!(!register_natives(env, ptr::null_mut(), ptr::null(), 0));
            assert!(env_from_vm(ptr::null_mut()).is_null());
            assert!(env_to_vm(ptr::null_mut()).is_null());
            assert!(get_method_id(env, ptr::null_mut(), c"x".as_ptr(), c"()V".as_ptr()).is_null());
            assert_eq!(call_double_method(env, ptr::null_mut(), ptr::null_mut()), 0.0);
            assert_eq!(call_float_method(env, ptr::null_mut(), ptr::null_mut()), 0.0);
            assert_eq!(call_long_method(env, ptr::null_mut(), ptr::null_mut()), 0);
            assert!(get_object_class(env, ptr::null_mut()).is_null());
            assert!(new_global_ref(env, ptr::null_mut()).is_null());
            assert!(!exception_occurred(env));
            assert!(get_static_field_id(env, ptr::null_mut(), c"x".as_ptr(), c"J".as_ptr()).is_null());
            assert!(get_static_object_field(env, ptr::null_mut(), ptr::null_mut()).is_null());
            // Must not fault despite the null env.
            clear_exception(env);
            delete_local_ref(env, ptr::null_mut());
            delete_global_ref(env, ptr::null_mut());
            call_void_method(env, ptr::null_mut(), ptr::null_mut());
        }
    }

    /// The two `NewObject` shapes guard on their inputs. Both take variadic tails in the
    /// header, so a null class reaching one would be a call into the JVM with a garbage
    /// `jclass` — the kind of fault that has no Rust frame to report from.
    #[test]
    fn new_object_shapes_tolerate_null_inputs() {
        unsafe {
            let env: *mut JNIEnv = ptr::null_mut();
            assert!(new_object_context(env, ptr::null_mut(), ptr::null_mut(), ptr::null_mut()).is_null());
            assert!(new_object_context_pointer(
                env,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                0
            )
            .is_null());
            // A class but no constructor is the failure `GetMethodID` predicts, and must be
            // caught here rather than handed to the JVM as a null `jmethodID`.
            let class = super::fake_jni::stub_ref() as jclass;
            assert!(new_object_context(env, class, ptr::null_mut(), ptr::null_mut()).is_null());
        }
    }
}
