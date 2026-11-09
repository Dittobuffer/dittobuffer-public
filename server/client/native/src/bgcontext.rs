//! The object behind [`CreateBackgroundContext`](crate::CreateBackgroundContext).
//!
//! **Provenance, because this dossier has several builds and warns against mixing them.**
//! Every plugin address below (`0x92a5e8` for the export itself, `0x6e9fc8`, `0x6e6148`,
//! `0x84f508`, `0x663cd4`, `0x6a67d8`, the vtable at `0xb93af0`) is from the **0.429.2**
//! build — Binary Ninja `view_5` on `phd.bndb`. They are *not* the 0.427 reference numbers
//! in `notes/02-exported-symbols.md`. The two builds are the same code differing only in
//! obfuscation, so nothing here is version-dependent in *behaviour* — but a mixture of the
//! two is wrong in both directions, and an untagged address is not checkable. The
//! `libadventuresync.so` addresses are from that library, which is a third image again.
//!
//! **Those are BN's numbers, and BN rebases this image by `+0x400000`.** The file vaddrs are
//! `0x52a5e8`, `0x2e9fc8` and so on — subtract the constant. The one place it matters most is
//! the step 4b string table below, which is quoted as **file** addresses because that is what
//! the runtime dump and `llvm-objdump` both speak; read as `view_5` addresses they would land
//! four megabytes away, in code. `notes/README.md` has the full rule.
//!
//! `libadventuresync.so` — the Adventure Sync / Google Fitness bridge — imports that
//! symbol and is its only caller in the dumps. `sub_7660f0` there does:
//!
//! ```text
//! 0x766150  bl   CreateBackgroundContext
//! 0x766154  ldr  x8, [x0]           ; ctx->vtable
//! 0x766158  ldr  x9, [x8, #0x18]    ; the one slot it ever calls
//! 0x76615c  mov  x8, sp             ; sret, per the AAPCS64 indirect-result rule
//! 0x766160  blr  x9
//! 0x766164  ldr  q0, [sp]           ; the shared_ptr the slot wrote
//! 0x766168  stp  xzr, xzr, [sp]     ; ...which it then treats as moved-from
//! 0x76616c  ldr  x20, [x19, #0x20]  ; OLD control block
//! 0x766170  stur q0, [x19, #0x18]   ; cache = {ptr, ctrl}
//! 0x766174  cbz  x20, 0x7661a0      ; null ctrl -> skip the refcount release
//! ```
//!
//! The return value is dereferenced unconditionally, so returning null — what the stub
//! did — left the caller's cache at `arg1[0x18]` null, the `cbz x8, 0x766150` guard never
//! latched, and *every* call faulted at `0x766154` rather than only the first.
//!
//! # The shape, taken from the original
//!
//! `CreateBackgroundContext` @ `0x92a5e8` in the original `libNianticLabsPlugin.so` is
//! control-flow-flattened (an `adr`/`ldrsw`/`eor`/`mul`/`br x8` dispatcher over a
//! one-shot state machine). Unflattened it is four steps, and only the first and last
//! are observable here:
//!
//! 1. `operator new(0x18)`, then `obj[0] = &0xb93af0`, `obj[1] = 0`, `obj[2] = 0`.
//!    That allocation size and field order are the ABI copied below.
//! 2. `env->GetJavaVM(env, &vm)` — literally `ldr x8,[env]; ldr x8,[x8,#0x6d8]`.
//!    `0x6d8 / 8 = 219` is the `GetJavaVM` slot, the same one
//!    [`crate::jni::env_to_vm`] already uses and `jni::tests` pins.
//! 3. `sub_8b5398(vm)`: `vm->GetEnv(vm, &env, JNI_VERSION_1_4)`, the
//!    `ExceptionCheck`/`ExceptionDescribe`/`ExceptionClear` idiom, then a handful of
//!    subsystem initialisers.
//! 4. `setup_context_java(bgToken)` @ `0x6e9fc8` — two Java-side operations, not one:
//!
//!    **4a** — `env->NewGlobalRef(env, bgToken)`. `bgToken` is a `jobject` **local
//!    reference** living in the caller's frame, so this is what makes keeping it legal.
//!    See [`promote_token`].
//!
//!    **4b** — `GetObjectClass(bgToken)` → `GetMethodID(name1, sig1)` → *call* →
//!    `NewGlobalRef(result)`, then `FindClass(name2)` → `GetMethodID(name3, sig3)`, and a
//!    0x20-byte libc++ `__shared_ptr_pointer` holding the reference installed at
//!    `data_105e170` with replace-and-release. See [`cache_class_loader`].
//!
//!    The five strings are **not** readable in the image: they are written into `.data` at
//!    load and exist in cleartext only in a running process, so they were dumped out of one
//!    instead (0.429.2 on the device, `dd` over `/proc/<pid>/mem`). **File vaddrs**, per the
//!    provenance note above:
//!
//!    | | file vaddr | string |
//!    |---|---|---|
//!    | `name1` | `0x9ce394` | `getClassLoader` |
//!    | `sig1` | `0x9458cc` | `()Ljava/lang/ClassLoader;` |
//!    | `name2` | `0x88cae1` | `java/lang/ClassLoader` |
//!    | `name3` | `0x7d6c79` | `loadClass` |
//!    | `sig3` | `0x94f530` | `(Ljava/lang/String;)Ljava/lang/Class;` |
//!
//!    So 4b is the **app-`ClassLoader` cache**: the token's loader, promoted, plus the
//!    `loadClass` that reaches app classes through it. `FindClass` from a thread the JVM does
//!    not know about resolves against the *system* loader and cannot see them; `name2` is on
//!    the bootclasspath, so its `FindClass` succeeds anywhere. That is the hazard
//!    `notes/09-open-questions.md` raises, and this is the answer to it.
//!
//!    The 37-byte stack buffer this function XOR-6-decodes from immediates spells
//!    `"NianticContextServiceBase::SetContext"`. It is a log/name string, not `name1` —
//!    JNI method names carry no `::`.
//!
//! The vtable at `0xb93af0` holds exactly four entries. Only `+0x18` is ever called from
//! either dump: `0x6a67d8`, an `enable_shared_from_this`-style `shared_from_this()`.
//!
//! ## `sub_6a67d8`, unflattened
//!
//! It is *not* a getter — it builds the control block the context never had, and hands
//! back the first owning reference:
//!
//! ```text
//! ctrl = operator new(0x20)
//! ctrl[0x00] = &0xb944e8     ctrl[0x08] = 0        ctrl[0x10] = 0
//! ctrl[0x18] = this                                 ; the payload it deletes
//! ctrl[0x10]++                                       ; __add_weak()
//! this[0x08] = this                                  ; enable_shared_from_this
//! old = this[0x10]; this[0x10] = ctrl                ;   __weak_this_ = {this, ctrl}
//! if (old) release(old)
//! *x8 = this; x8[1] = ctrl                           ; the returned shared_ptr
//! ```
//!
//! # Why a control block at all, and why it is safe to build one
//!
//! `libadventuresync.so` holds that `{ptr, ctrl}` in a `shared_ptr` and eventually runs
//! libc++'s own destructor over it. libc++ is **statically linked into that library**, so
//! the ABI is readable rather than guessable. `__release_shared` @ `0x7808fc` — with
//! `__release_weak` inlined into it — is:
//!
//! ```text
//! fetch_add([ctrl+0x08], -1) == 0 ?
//!     ctrl->vtbl[+0x10](ctrl)                        ; __on_zero_shared
//!     [ctrl+0x10] != 0 && fetch_add(&[ctrl+0x10],-1) != 0 ? return : ...
//!         ctrl->vtbl[+0x20](ctrl)                    ; __on_zero_shared_weak
//! ```
//!
//! So: owners at `+0x08`, weak owners at `+0x10`, `__on_zero_shared` at vtable `+0x10`,
//! `__on_zero_shared_weak` at vtable `+0x20`. The counts are stored **one less than the
//! number of references** — a block created with `owners = 0` has exactly one owner and is
//! torn down by its first release, which is what the original sets and what
//! [`shared_from_this_impl`] reproduces.
//!
//! `__on_zero_shared` @ `0x469590` loads the payload and calls `payload->vtbl[+0x08](payload)`
//! — the **deleting destructor**. The control block owns the context and frees it, which is
//! what [`control_on_zero_shared`] does.
//!
//! # Deviations, and why
//!
//! * **No weak reference is counted.** The original stores `{this, ctrl}` into the context's
//!   `__weak_this_` and calls `__add_weak()`, so `weak_owners` starts at 1 and the block is
//!   freed only after the context's own `~__weak_ptr` decrements it — from libc++, inside
//!   `__on_zero_shared`. Nothing here models a `weak_ptr`, so [`CONTROL_WEAK_OWNERS`] starts
//!   at 0 instead: `__release_weak` sees zero and frees the block at the same point in the
//!   same release. The observable end state is identical; the machinery is not.
//! * **The old control block is not released.** `if (old) release(old)` only fires if
//!   `+0x18` is called twice on one context. `create` zeroes the field and the caller's own
//!   latch means it calls the slot once, so the branch is dead — and taking it would mean
//!   calling into a *different* library's `__release_weak` by name.
//! * **`+0x10` starts no session, and that is a known gap rather than a finding.** It is
//!   *not* unobserved — `libholoholo.so` calls it at `0xf7dc78` and `0xd209ac`, and
//!   `libadventuresync.so` at `0x361bdc`, with
//!   `(shared_ptr<SessionParams> const&, std::function<...> const&)`, to start an RPC
//!   session. Upstream's producer is `sub_6fd2e0`, an engine of 1057 instructions whose
//!   session reaches a `nia::api::background::RpcManager` that is in no artifact we hold;
//!   `notes/11-nia-api-background.md` is the dossier. So [`start_session`] reproduces the
//!   *contract* rather than the engine: status `0`, and a live [`Session`] in the response,
//!   because that is what the callers are written against. **No session is ever started** —
//!   the object answers `+0x38` with upstream's own "nothing to start", the two members at
//!   `+0x18` and `+0x28` are handed out through real getters, and the member's `+0x10`
//!   answers the background-token request with an empty token. The other seven slots are
//!   inert, and every slot that is reachable announces its first call. That is the residual
//!   gap, and it is a smaller and safer one than reporting failure was: a caller that ignores
//!   the status and reads the response reaches a real object instead of a null vtable.
//! * **The background-token request is answered with an empty pair.** Deliberate rather than
//!   a placeholder; [`request_token`] argues it at length, and the short version is that the
//!   only caller sees a null token as an ordinary state it is written to re-request, while a
//!   token object of our own would be a fabrication of a type no dump shows — and the game
//!   caches what it is handed and acts on it. So the background-token path stays dormant
//!   rather than being served with something invented.
//! * **`+0x20` was a crash, and writing nothing into the caller's buffer was the whole of
//!   it.** The slot returns a `shared_ptr` through `x8`, and the caller
//!   (`libadventuresync`'s `BGCoreUpdateBackgroundToken`) loads that buffer two instructions
//!   later and calls `vtbl[+0x10]` through the first word — which, when the stub left it
//!   alone, was a stale stack value rather than a pointer. The reported fault address was
//!   `0x4c4e6572626910`, whose low bytes spell `\0ibreNLP`: the leftovers of this stub's own
//!   log formatting. A slot that *returns through `x8`* and does not write it is not an
//!   inert slot, and the general rule is now stated where the getters are: every slot whose
//!   caller loads a buffer must write that buffer, whatever it has to say.
//! * **`+0x00` and `+0x08` are unobserved.** They are the context's D1/D0. Nothing in any
//!   dump reaches either, so a guessed destructor would risk a double free, while leaving
//!   them null would turn a call from a library not in the dumps into a jump through null.
//!   Non-null and inert is the only option safe in both directions.
//! * **Step 3 is not ported.** Upstream re-initialises subsystems and runs an exception
//!   dance against a `JNIEnv` it obtains from the VM; Dittobuffer resolves an env per call
//!   from a cached VM ([`crate::jni::cached_env`]), so there is nothing to restore.
//! * **The two process-globals are [`AtomicPtr`]s, not libc++ `shared_ptr`s.** Upstream's
//!   `data_105e170` (the loader) and `data_105e978` (`loadClass`) are a
//!   `__shared_ptr_pointer` and a bare `jmethodID` respectively, but unlike the context's
//!   vtable they are internal to this library — no other library reads them — so their layout
//!   is not an ABI boundary and need not be reproduced. What *is* reproduced is the
//!   observable part: the global reference is held, and the swap releases its predecessor.
//! * **`CallObjectMethod` is called directly rather than through the compiler's shim.**
//!   `0x6ea394` branches to `0x582fb8`, which has a varargs register-save prologue and loads
//!   `[x8, #0x118]` — slot 35, `CallObjectMethodV`. It is the variadic entry point the header
//!   declares, emitted because the call *site* is a variadic call. That call passes no
//!   varargs, so slot 34 and slot 35 receive identical arguments and slot 34 is what a
//!   non-variadic wrapper can express. See [`crate::jni::call_object_method`].
//! * **The local reference the call returned is not released.** Upstream does not either —
//!   there is no `DeleteLocalRef` anywhere in the function. A local lives until the native
//!   frame returns, and `CreateBackgroundContext` is at the top of one, so the frame's own
//!   teardown is what frees it. Deleting it early is not *wrong*, but it is not what the
//!   original does, and the two are indistinguishable from outside.
//! * **Only the lookup is ported; nothing consumes it.** Upstream reads `data_105e978` from
//!   the session machinery behind `vtbl[+0x10]`, which starts no session here — so the method
//!   id is resolved and no class is ever loaded through it. Resolving it *is* the observable effect
//!   of step 4b: it is a real `GetMethodID` on the real VM, it can really fail, and
//!   [`cache_class_loader`] handles that failure.
//! * **The optimiser deletes [`LOAD_CLASS_METHOD`] outright, and that is the right answer.**
//!   Verified by disassembling the release `.so`: there is no symbol for the static, and
//!   `create` branches from the `GetMethodID` call straight to its epilogue. A store to a
//!   private static that nothing ever reads is dead, and the compiler is entitled to say so.
//!   Nothing observable is lost in either direction — a `jmethodID` is an integer, not a
//!   reference, so the JVM has nothing to keep alive on its account. The static stays in the
//!   source because it is where the value belongs the moment a consumer exists; it goes live
//!   again by itself as soon as anything reads it, which is what the test
//!   [`tests::step_4b_caches_load_class_in_the_process_global`] pins in a build that does.
//!
//!   The other two globals are *not* elided, and they would be a genuine bug if they were:
//!   their swap results feed a `DeleteGlobalRef`, so removing the store would leak the
//!   predecessor's reference. Both are present in the release build's `.bss`.

use core::ffi::{c_void, CStr};
use core::ptr;
use core::sync::atomic::{AtomicIsize, AtomicPtr, AtomicU32, Ordering};
use jni_sys::{jclass, jmethodID, jobject, JNIEnv};
use std::alloc::{alloc, dealloc, Layout};

/// `name1`/`sig1` — `bgToken.getClassLoader()`.
const GET_CLASS_LOADER: (&CStr, &CStr) = (c"getClassLoader", c"()Ljava/lang/ClassLoader;");

/// `name2` — the class `loadClass` is resolved against.
///
/// A **bootclasspath** class, and that is the whole reason it is this one rather than the
/// app class the loader would be asked for: it is the only kind `FindClass` can resolve from
/// a thread the JVM has never seen. See [`cache_class_loader`].
const CLASS_LOADER_CLASS: &CStr = c"java/lang/ClassLoader";

/// `name3`/`sig3` — the lookup that reaches app classes through the token's loader.
const LOAD_CLASS: (&CStr, &CStr) = (c"loadClass", c"(Ljava/lang/String;)Ljava/lang/Class;");

/// The 24-byte object handed to `libadventuresync`.
///
/// `operator new(0x18)` upstream. `+0x08` and `+0x10` are the `__weak_ptr` cache that
/// [`shared_from_this_impl`] fills in, which is why [`create`] zeroes them.
#[repr(C)]
struct BackgroundContext {
    vtable: *const BackgroundContextVtable,
    weak_self: *mut BackgroundContext,
    weak_ctrl: *mut c_void,
}

/// `&0xb93af0` upstream: four pointers, called through `ctx[0]`.
///
/// The slot *offsets* are the ABI — the caller indexes `+0x18` directly — so a field
/// reordered here silently calls a different entry point. `+0x00`/`+0x08` are the
/// context's own destructors, established by `__on_zero_shared` calling `vtbl[+0x08]`.
#[repr(C)]
struct BackgroundContextVtable {
    /// `0x84f508` upstream. The **complete destructor (D1)**. Unobserved.
    ///
    /// It restores the vtable to `0xb93af0` and runs `operator delete` over the field at
    /// `+0x10` — the `__weak_ptr` cache, not the context itself. Nothing in any dump
    /// destroys a context in place, so this stays [`unobserved`]; see the coupling note on
    /// [`control_on_zero_shared`] before making it real.
    slot_00: unsafe extern "C" fn(*mut BackgroundContext),
    /// `0x663cd4` upstream. The **deleting destructor (D0)** — the same body as `+0x00`
    /// plus `j_free(this)`. Unobserved, for the same reason and with the same coupling.
    slot_08: unsafe extern "C" fn(*mut BackgroundContext),
    /// `0x6e6148` upstream. **Observed**, and answered with a failure here — a known gap.
    ///
    /// Three libraries call it, all with a `shared_ptr<SessionParams>` and a completion
    /// `std::function`: `libholoholo.so` at `0xf7dc78` and `0xd209ac` (different address
    /// space: that library also loads at `0x400000`) and `libadventuresync.so` at
    /// `0x361bdc`. Upstream blocks on an RPC and invokes the callback inline with the result;
    /// modelling that needs the whole `nia::api::background` request path — see
    /// `notes/11-nia-api-background.md`. Until then [`start_session`] invokes the callback
    /// itself to report failure, because a caller *promised* a callback and never given one
    /// does not degrade, it blocks: `libadventuresync` parks the main thread on
    /// `future<void>::get()` one instruction after this slot returns.
    ///
    /// Both parameters arrive **by `const` reference**, so each is one pointer rather than a
    /// struct by value: `x1` is the `shared_ptr` and `x2` the `std::function`. The body
    /// returns `void`, so unlike [`shared_from_this`] there is no indirect-result register
    /// to route around and the signature is expressible directly.
    slot_10: unsafe extern "C" fn(*mut BackgroundContext, *const c_void, *const c_void),
    /// `0x6a67d8` upstream — the slot every caller *starts* with, to take an owning handle.
    shared_from_this: SharedFromThis,
}

/// A `std::__ndk1::shared_ptr`: raw pointer, then control block.
///
/// The context's `+0x18` is the slot that writes the first one out, so the pointee defaults
/// to [`BackgroundContext`] and that use reads unchanged. The session's two members are pairs
/// of the same shape over a different pointee, which is the whole reason the type takes one.
#[repr(C)]
struct SharedPtr<T = BackgroundContext> {
    ptr: *mut T,
    ctrl: *mut ControlBlock,
}

/// `&0xb944e8` upstream: libc++'s `__shared_weak_count` for this context.
///
/// Sizes and offsets are verified against libc++'s own release path, quoted in the module
/// docs — `owners` at `+0x08` and `weak` at `+0x10` are read and decremented by code that
/// is already in the caller's address space, so a field moved here corrupts the heap.
#[repr(C)]
struct ControlBlock {
    vtable: *const ControlBlockVtable,
    /// One *less* than the number of owning `shared_ptr`s — libc++ releases when the
    /// pre-decrement value is zero, so 0 means "one owner".
    shared_owners: AtomicIsize,
    /// Likewise one less than the number of weak references. See
    /// [`CONTROL_WEAK_OWNERS`] for why this starts at zero rather than one.
    weak_owners: AtomicIsize,
    /// The object the block owns, freed by whichever `on_zero_shared` its vtable names.
    ///
    /// Untyped because one block serves both the context and the session [`start_session`]
    /// hands back. The vtable is what says which of the two the payload is, and therefore
    /// which layout releases it — see [`control_on_zero_shared`] and [`session_on_zero_shared`].
    payload: *mut c_void,
}

/// What a freshly built block's `weak_owners` holds.
///
/// The original stores 1, having just called `__add_weak()` for the reference its
/// `__weak_this_` keeps. Nothing here keeps one, so this is 0 — which `__release_weak`
/// treats as "already at zero" and frees the block on, at the same point in the same
/// release. Writing 1 without holding a real reference would strand the block instead.
const CONTROL_WEAK_OWNERS: isize = 0;

/// `&0xb944e8` upstream. Five slots; the two libc++ calls are named against the addresses
/// in its release path.
#[repr(C)]
struct ControlBlockVtable {
    /// `0xac23b8` upstream. Complete destructor — never called on a block we free
    /// ourselves.
    complete_dtor: unsafe extern "C" fn(*mut ControlBlock),
    /// `0x6a48d8` upstream. Deleting destructor.
    deleting_dtor: unsafe extern "C" fn(*mut ControlBlock),
    /// `0x469590` upstream, reached as `vtbl[+0x10]` from `__release_shared`. Frees the
    /// payload — the original calls the context's own deleting destructor to do it.
    on_zero_shared: unsafe extern "C" fn(*mut ControlBlock),
    /// The `__get_deleter` slot. Answering `false` is the truth: there is no deleter.
    get_deleter: unsafe extern "C" fn(*mut ControlBlock) -> bool,
    /// `0x7c1070` upstream, reached as `vtbl[+0x20]` from `__release_weak`. Frees the block.
    on_zero_shared_weak: unsafe extern "C" fn(*mut ControlBlock),
}

/// The `+0x18` slot's type, which is not the same shape on both targets.
///
/// On aarch64 the pair is written through `x8`, so the slot holds a two-instruction thunk
/// that moves it out of the way; on the host it points straight at
/// [`shared_from_this_impl`], whose signature that thunk tail-calls into.
#[cfg(target_arch = "aarch64")]
type SharedFromThis = unsafe extern "C" fn();
#[cfg(not(target_arch = "aarch64"))]
type SharedFromThis = unsafe extern "C" fn(*mut BackgroundContext, *mut SharedPtr);

/// `ctx->vtable[+0x18]()` — hand back an owning `shared_ptr{this, ctrl}`.
///
/// # Why `x8` needs a thunk
///
/// `std::shared_ptr` has a non-trivial copy constructor, and AAPCS64 returns any such type
/// in memory through the indirect-result register `x8` *regardless of its size* — which is
/// why the caller is `mov x8, sp` and then `ldr q0, [sp]` rather than reading `x0`/`x1`. A
/// 16-byte struct returned from a plain `extern "C" fn` under Rust's rules would come back
/// in `x0`/`x1` instead, leaving the caller's `sp` holding whatever the previous occupant
/// left there.
///
/// The register cannot be named in Rust: taking the destination as an ordinary second
/// parameter would put it in `x1`, which at the call site still holds the caller's
/// `bgToken` — so the function would write a `shared_ptr` over a Java object reference.
/// Two instructions fix that and nothing else needs to be target-specific: `x0` is already
/// `this`, so the thunk only has to move `x8` into the second argument slot and branch.
///
/// The branch is a tail call, so the real function returns straight to the caller.
#[cfg(target_arch = "aarch64")]
#[unsafe(naked)]
unsafe extern "C" fn shared_from_this() {
    core::arch::naked_asm!(
        "mov x1, x8", // the sret pointer, out of the indirect-result register
        "b {imp}",    // x0 is already `this`; see `shared_from_this_impl`
        imp = sym shared_from_this_impl,
    );
}

/// The real `+0x18` body, reached from [`shared_from_this`] on device and called directly
/// by the tests on the host.
///
/// Builds the control block the context never had and writes `{this, ctrl}` to `out`.
///
/// A failed allocation degrades to `{this, null}` rather than returning nothing: the caller
/// dereferences `out.ptr` unconditionally, and the null control block is a shape it is
/// known to survive (its release path is guarded by `cbz`). See the module docs.
unsafe extern "C" fn shared_from_this_impl(this: *mut BackgroundContext, out: *mut SharedPtr) {
    if out.is_null() {
        return;
    }
    let raw = unsafe { alloc(Layout::new::<ControlBlock>()) } as *mut ControlBlock;
    if raw.is_null() {
        crate::log::info("[bgcontext] CreateBackgroundContext: control block allocation failed");
        unsafe {
            ptr::write(
                out,
                SharedPtr {
                    ptr: this,
                    ctrl: ptr::null_mut(),
                },
            )
        };
        return;
    }
    unsafe {
        ptr::write(
            raw,
            ControlBlock {
                vtable: &CONTROL_VTABLE,
                shared_owners: AtomicIsize::new(0),
                weak_owners: AtomicIsize::new(CONTROL_WEAK_OWNERS),
                payload: this as *mut c_void,
            },
        );
        // `__weak_this_`, which the original caches here too — the two stores are what
        // make a second `shared_from_this()` on this context return the same block rather
        // than a competing one. Nothing in the dumps makes that call; the fields are
        // written because the layout says they are, and a reader outside the dumps would
        // otherwise find a context that claims never to have been shared.
        if !this.is_null() {
            (*this).weak_self = this;
            (*this).weak_ctrl = raw as *mut c_void;
        }
        ptr::write(
            out,
            SharedPtr {
                ptr: this,
                ctrl: raw,
            },
        );
    }
}

/// Frees the context. Reached from libc++ as `vtbl[+0x10]` when the last owner lets go.
///
/// The original routes this through the context's deleting destructor; ours has no
/// destructor work to do, so it frees the allocation [`create`] made directly. Matching
/// the allocator is the whole requirement.
///
/// # Coupling: this and `vtbl[+0x08]` are the same free
///
/// The original reaches the 24 bytes through `payload->vtbl[+0x08]` — the context's own
/// **deleting destructor (D0)** at `0x663cd4`, which ends in `j_free(this)`. This function
/// bypasses that and calls `dealloc` itself.
///
/// So implementing `slot_08` for real means **rerouting this call to it**
/// (`(*payload).vtable->slot_08(payload)`), not adding a second free beside it. Both in
/// place would release the same allocation twice on the caller's first release, which is
/// exactly the failure the slot being inert avoids.
unsafe extern "C" fn control_on_zero_shared(ctrl: *mut ControlBlock) {
    if ctrl.is_null() {
        return;
    }
    let payload = unsafe { (*ctrl).payload };
    if !payload.is_null() {
        unsafe { dealloc(payload as *mut u8, Layout::new::<BackgroundContext>()) };
    }
}

/// Frees the control block itself. Reached from libc++ as `vtbl[+0x20]`.
unsafe extern "C" fn control_on_zero_shared_weak(ctrl: *mut ControlBlock) {
    if !ctrl.is_null() {
        unsafe { dealloc(ctrl as *mut u8, Layout::new::<ControlBlock>()) };
    }
}

/// The block's complete destructor. Unreached: nothing destroys a control block in place,
/// and freeing here would double up with `vtbl[+0x20]` on the path that does run.
unsafe extern "C" fn control_complete_dtor(_ctrl: *mut ControlBlock) {}

/// The block's deleting destructor.
unsafe extern "C" fn control_deleting_dtor(ctrl: *mut ControlBlock) {
    unsafe { control_on_zero_shared_weak(ctrl) };
}

/// Always `false` — there is no deleter to hand back.
unsafe extern "C" fn control_get_deleter(_ctrl: *mut ControlBlock) -> bool {
    false
}

/// A slot no dump reaches. Non-null, so a caller outside the dumps cannot jump through
/// null; inert, so a caller that did reach one changes nothing.
unsafe extern "C" fn unobserved(_this: *mut BackgroundContext) {}

/// The `0xe0`-byte object `+0x10` produces and hands to its completion callback.
///
/// Upstream builds it in `sub_6fd2e0` (`libNianticLabsPlugin.so`, file offset `0x2fd2e0`):
/// `operator new(0xe0)`, `&data_b91088` into the first word, then about fifteen collaborators
/// initialising the rest. None of that is reproduced — see [`start_session`] and
/// `notes/11-nia-api-background.md`.
///
/// What *is* reproduced is the shape, because that is what the callers are written against.
/// `StandaloneInit` (`libholoholo.so` `0xd209bc`) keeps the `shared_ptr` it is handed, and
/// `sendSoftSfidaLocationUpdate`'s callback (`0xf7e8ec`) calls straight through `vtbl[+0x38]`
/// on it **without reading the status first**. An empty `shared_ptr` is a null vtable
/// dereference there; a live object is not.
///
/// The size is upstream's on purpose: a consumer that reads a field past the vtable reads a
/// zero here rather than past the end of the allocation.
///
/// The two member pairs are *modelled* rather than zeroed, because [`slot_20`] and
/// [`slot_28`] are reached and upstream's getters are pure reads of them. Their offsets are
/// the ABI: `sub_674a78` reads `{+0x28, +0x30}` and `sub_6695cc` reads `{+0x18, +0x20}`, and
/// a field moved here makes a caller collect a pointer out of a field that is not one.
#[repr(C)]
struct Session {
    vtable: *const SessionVtable,
    /// `+0x08`..`+0x18`. Upstream's object has two words between its vtable and the first
    /// member pair, and nothing in the dumps says what they hold — but the getters read at
    /// fixed offsets, so the gap is written out rather than closed: a pair placed at `+0x08`
    /// would be self-consistent inside this file and wrong at every call site outside it.
    _unmodelled_08: [u8; SESSION_UNMODELLED_08],
    /// `+0x18`. The pair [`slot_28`] hands out — upstream's other refcounted member, the one
    /// no dump reaches.
    member_18: SharedPtr<SessionMember>,
    /// `+0x28`. The pair [`slot_20`] hands out, and the one `BGCoreUpdateBackgroundToken`
    /// collects before it asks for a background token.
    member_28: SharedPtr<SessionMember>,
    /// The rest of upstream's object — unnamed here because it is unwritten here.
    _unmodelled: [u8; SESSION_UNMODELLED],
}

/// The two words the first member pair sits behind. Not a size of its own — it is what puts
/// `member_18` at `+0x18` and `member_28` at `+0x28`, which is the whole of what the dumps
/// pin about the front of this object.
const SESSION_UNMODELLED_08: usize = 0x10;

/// `0xe0` — upstream's `operator new` argument in `sub_6fd2e0`, less everything above the
/// trailing gap: the vtable word, the two words in front of the members, and the two pairs.
const SESSION_UNMODELLED: usize = 0xe0
    - core::mem::size_of::<*const c_void>()
    - SESSION_UNMODELLED_08
    - 2 * core::mem::size_of::<SharedPtr<SessionMember>>();

/// `&data_b91088` upstream: **ten** slots, ending at `+0x50`.
///
/// Recovered from the running process rather than from the file — `.data` is relocated at
/// load — by reading `module_base + 0x791088`. The word after the tenth slot is `1` rather
/// than a pointer into the module's executable range, which is what fixes the length; the six
/// words before this table are the tail of a different one and belong to another type.
///
/// Four of the ten have a caller in the dumps — `+0x20`, `+0x28` and `+0x38` here, and
/// `+0x10` on the context — and of those only `+0x38` has a signature *proven* rather than
/// inferred from where a decompiler placed its stack variables. The rest are inert —
/// [`announce_slot`] — and each says so the first time it is reached.
#[repr(C)]
struct SessionVtable {
    /// `0x69e1ac` upstream. Complete destructor (D1). Unobserved, and inert for the reason
    /// [`control_on_zero_shared`] records: the block frees the session itself, so a second
    /// free here would release the same allocation twice.
    slot_00: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x7bb358` upstream. Deleting destructor (D0). Unobserved, same coupling.
    slot_08: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x620f94` upstream. `int64_t(void*)` — 60 bytes, the smallest slot in the table.
    ///
    /// One argument, as the offsets note says; the second `void*` it was written with would
    /// have been a claim about `x1` that the 60 bytes do not support.
    slot_10: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x8acee8` upstream. `int64_t(void*)` — 84 bytes.
    slot_18: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x674a78` upstream — [`slot_20`]: an owning copy of the member pair at `{+0x28, +0x30}`.
    ///
    /// The only session slot whose *body* is known in full: it copies the pair out and
    /// `++shared_owners` on its control block, and does nothing else — 22 instructions with no
    /// branch and no call.
    slot_20: SessionGetter,
    /// `0x6695cc` upstream — [`slot_28`]: the same for the pair at `{+0x18, +0x20}`.
    ///
    /// Upstream's twin of `+0x20`, differing only in the field it reads. No caller of it is in
    /// any dump, so what the returned object *is* is unproven — but that it is a getter of the
    /// same shape is not: the two bodies are the same code over different offsets.
    slot_28: SessionGetter,
    /// `0x60eba0` upstream. 36 bytes — a getter of some kind.
    slot_30: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x4daf68` upstream — [`begin_auth`], the only slot whose signature is proven.
    slot_38: unsafe extern "C" fn(*mut Session, *const c_void, *const c_void),
    /// `0x6f13f0` upstream. `int64_t(void*, int32_t)` — 96 bytes.
    slot_40: unsafe extern "C" fn(*mut Session) -> usize,
    /// `0x9142e0` upstream. 531 basic blocks — the largest thing this vtable points at.
    slot_48: unsafe extern "C" fn(*mut Session) -> usize,
}

/// The refcounted object upstream's session holds two of, at `+0x18` and `+0x28`.
///
/// Upstream's type has no name in any artifact we hold: the plugin builds it inside
/// `sub_6fd2e0`, the 1057-instruction engine `notes/11-nia-api-background.md` describes, and
/// nothing of its constructor survives symbolically. What survives is a *shape*, from the one
/// caller in the dumps that collects one — `libadventuresync`'s `BGCoreUpdateBackgroundToken`:
///
/// ```text
/// 0x35b9c0  blr  x9                 ; session->vtbl[+0x20](sp) — collect the pair
/// 0x35b9c4  ldr  x22, [sp]          ; ...take the object out of it
/// 0x35b9f0  ldr  x8, [x22]          ; its vtable
/// 0x35b9f4  ldr  x9, [x8, #0x10]    ; the slot it then calls
/// 0x35b9f8  add  x8, sp, #0x10      ; sret — what that slot returns is 16 bytes
/// 0x35b9fc  add  x1, sp, #0x20      ; &std::function — a completion callback
/// 0x35ba00  mov  x0, x22
/// 0x35ba04  blr  x9
/// ```
///
/// A request that hands back a `shared_ptr` and takes something to report to: a background
/// token, asked for and delivered later. [`request_token`] is that slot, answered with
/// nothing, and the rest of the table is inert — nothing else reaches the object.
///
/// The size is upstream's *session* size rather than upstream's member size, which is unknown
/// and unrecoverable here. It is a scale, not a measurement, and it errs large deliberately
/// for the reason [`Session`] gives: a consumer reading a field past the vtable reads a zero
/// rather than past the end of the allocation.
#[repr(C)]
struct SessionMember {
    vtable: *const SessionMemberVtable,
    _unmodelled: [u8; MEMBER_UNMODELLED],
}

/// `0xe0` less the vtable word. See [`SessionMember`] for why the figure is a scale.
const MEMBER_UNMODELLED: usize = 0xe0 - core::mem::size_of::<*const c_void>();

/// The member's vtable — ten slots, of which `+0x10` is the only one any dump reaches.
///
/// The *length* is the session's rather than a measurement: no dump indexes this table past
/// `+0x10`, and a longer one costs nothing while a shorter one would turn a call through an
/// unmodelled index into a jump past the end of the table.
#[repr(C)]
struct SessionMemberVtable {
    /// Unobserved. Inert and non-null, as every unobserved slot here is — including the two
    /// libc++ would delete through, which cannot run: the control block owns the member.
    slot_00: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved, same coupling.
    slot_08: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// **The one slot with a caller** — [`request_token`].
    slot_10: TokenRequest,
    /// Unobserved.
    slot_18: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_20: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_28: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_30: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_38: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_40: unsafe extern "C" fn(*mut SessionMember) -> usize,
    /// Unobserved.
    slot_48: unsafe extern "C" fn(*mut SessionMember) -> usize,
}

/// The first-call gate for the session's slots, one bit each.
///
/// [`crate::context::State::log_once`] is the same idea, but it hangs off a `Context` and
/// there is none here. One word rather than ten `static`s so the whole table shares a cache
/// line, and so "which of these has the game reached" is a single value to read back.
static SLOTS_SEEN: AtomicU32 = AtomicU32::new(0);

/// One line per slot, in table order — and in the order the table is *initialised*, which is
/// what keeps an index here the same index there. `+0x38` is the one slot absent from it: it
/// reports a status rather than a first sighting, and reports it every call.
///
/// The index is the bit [`announce_slot`] sets; the offset in the message is not derived from
/// it, because the array is two short at the end — a coupling a test would have to check, so
/// the two lists are written next to each other instead.
///
/// `+0x20` and `+0x28` are in the table because they are *reached*: a getter announces its
/// first call exactly like an inert slot does, which is what makes "the game collected a
/// member" a line in logcat rather than an inference from the crash after it.
const SLOT_MESSAGES: [&str; 9] = [
    "[bgcontext] session +0x00 (D1) called; the block frees the session, not this slot",
    "[bgcontext] session +0x08 (D0) called; the block frees the session, not this slot",
    "[bgcontext] session +0x10 called; the stub has no session to drive",
    "[bgcontext] session +0x18 called; the stub has no session to drive",
    "[bgcontext] session +0x20 called: handing over the stub's member at +0x28, \
     which has no token to give",
    "[bgcontext] session +0x28 called: handing over the stub's member at +0x18",
    "[bgcontext] session +0x30 called; the stub has no session to drive",
    "[bgcontext] session +0x40 called; the stub has no session to drive",
    "[bgcontext] session +0x48 called; the stub has no session to drive",
];

/// Announce a session slot's first call, and stay silent afterwards.
///
/// Which slots a caller actually drives is the open question this leaves behind, and a
/// session polled per frame must answer it once rather than once per frame.
fn announce_slot(index: usize) {
    let bit = 1u32 << index;
    if SLOTS_SEEN.fetch_or(bit, Ordering::AcqRel) & bit == 0 {
        crate::log::info(SLOT_MESSAGES[index]);
    }
}

/// A session slot no dump reaches. Non-null, and returning zero rather than leaving `x0` at
/// whatever the previous call left there.
///
/// One per inert slot rather than one shared body: the offsets are the answer to "which part
/// of the session does the game use", and a shared body could not say. The three slots that
/// *are* reached are not in this list — `+0x20` and `+0x28` are [`slot_20`] and [`slot_28`],
/// whose result is a pair written through `x8`, and `+0x38` is [`begin_auth`].
macro_rules! inert_session_slot {
    ($name:ident, $index:expr) => {
        unsafe extern "C" fn $name(_this: *mut Session) -> usize {
            announce_slot($index);
            0
        }
    };
}

inert_session_slot!(dormant_00, 0);
inert_session_slot!(dormant_08, 1);
inert_session_slot!(dormant_10, 2);
inert_session_slot!(dormant_18, 3);
inert_session_slot!(dormant_30, 6);
inert_session_slot!(dormant_40, 7);
inert_session_slot!(dormant_48, 8);

/// The member's own gate, one bit per slot, for the same reason the session's has one.
static MEMBER_SLOTS_SEEN: AtomicU32 = AtomicU32::new(0);

/// One line per member slot, in table order, with `+0x10` the one that is modelled.
///
/// A line from this table is a *finding* rather than a note: nothing in any dump reaches the
/// member but `+0x10`, so a call anywhere else means the shape [`SessionMember`] reconstructs
/// is wrong, and which offset it arrived at is the beginning of the correction.
const MEMBER_SLOT_MESSAGES: [&str; 10] = [
    "[bgcontext] session member +0x00 called; only +0x10 is modelled",
    "[bgcontext] session member +0x08 called; only +0x10 is modelled",
    "[bgcontext] session member +0x10 asked for a token; the stub has none to give",
    "[bgcontext] session member +0x18 called; only +0x10 is modelled",
    "[bgcontext] session member +0x20 called; only +0x10 is modelled",
    "[bgcontext] session member +0x28 called; only +0x10 is modelled",
    "[bgcontext] session member +0x30 called; only +0x10 is modelled",
    "[bgcontext] session member +0x38 called; only +0x10 is modelled",
    "[bgcontext] session member +0x40 called; only +0x10 is modelled",
    "[bgcontext] session member +0x48 called; only +0x10 is modelled",
];

/// Announce a member slot's first call, and stay silent afterwards — the token request is
/// made on every background update, so per-call logging would be a flood.
fn announce_member_slot(index: usize) {
    let bit = 1u32 << index;
    if MEMBER_SLOTS_SEEN.fetch_or(bit, Ordering::AcqRel) & bit == 0 {
        crate::log::info(MEMBER_SLOT_MESSAGES[index]);
    }
}

/// A member slot no dump reaches — the shape of [`inert_session_slot`] over the member's own
/// table and gate, and for the same reason: the offset is the answer, so the bodies are one
/// per slot rather than one for all.
macro_rules! inert_member_slot {
    ($name:ident, $index:expr) => {
        unsafe extern "C" fn $name(_this: *mut SessionMember) -> usize {
            announce_member_slot($index);
            0
        }
    };
}

inert_member_slot!(member_dormant_00, 0);
inert_member_slot!(member_dormant_08, 1);
inert_member_slot!(member_dormant_18, 3);
inert_member_slot!(member_dormant_20, 4);
inert_member_slot!(member_dormant_28, 5);
inert_member_slot!(member_dormant_30, 6);
inert_member_slot!(member_dormant_38, 7);
inert_member_slot!(member_dormant_40, 8);
inert_member_slot!(member_dormant_48, 9);

/// The session's two getter slots, which are not the same shape on both targets.
///
/// Upstream answers both through `x8` — the two bodies end in a `stp`/`ldxr` pair over a
/// two-word buffer — so on device each slot holds a two-instruction thunk that moves the
/// indirect-result register out of the way, and on the host it points straight at the body
/// whose signature that thunk tail-calls into. [`shared_from_this`] carries the full argument
/// for why the register cannot be named in Rust; the thunks below are the same two
/// instructions with the same reasoning.
#[cfg(target_arch = "aarch64")]
type SessionGetter = unsafe extern "C" fn();
#[cfg(not(target_arch = "aarch64"))]
type SessionGetter = unsafe extern "C" fn(*mut Session, *mut SharedPtr<SessionMember>);

/// `+0x20` — upstream's `sub_674a78`, and the slot `BGCoreUpdateBackgroundToken` opens with.
///
/// The caller's register shape, from `libadventuresync` at `0x35b9b0`:
///
/// ```text
/// 0x35b9b0  ldr  x0, [x21, #0x28]   ; the session
/// 0x35b9b4  ldr  x8, [x0]
/// 0x35b9b8  ldr  x9, [x8, #0x20]    ; this slot
/// 0x35b9bc  mov  x8, sp             ; sret: the result is 16 bytes, so it goes to memory
/// 0x35b9c0  blr  x9
/// ```
///
/// `x1` is not set before the call, which is what makes the thunk safe: there is nothing in
/// it to lose.
///
/// Upstream's body is a pure read of the member pair at `{+0x28, +0x30}` plus an owning
/// reference to it. A slot that wrote *nothing* to the caller's `sp` is what the stub used to
/// do, and it is a crash rather than a missing feature: the caller loads the buffer at
/// `0x35b9c4` and calls `vtbl[+0x10]` through the word it finds there, which is whatever the
/// last occupant of that stack slot left behind.
#[cfg(target_arch = "aarch64")]
#[unsafe(naked)]
unsafe extern "C" fn slot_20() {
    core::arch::naked_asm!(
        "mov x1, x8", // the sret pointer, out of the indirect-result register
        "b {imp}",    // x0 is already `this`; see `slot_20_impl`
        imp = sym slot_20_impl,
    );
}

/// [`slot_20`]'s body: hand back the pair at `+0x28`.
unsafe extern "C" fn slot_20_impl(this: *mut Session, out: *mut SharedPtr<SessionMember>) {
    announce_slot(4);
    unsafe { hand_out(ptr::addr_of!((*this).member_28), out) };
}

/// `+0x28` — upstream's `sub_6695cc`: the same getter over the pair at `{+0x18, +0x20}`.
///
/// No caller of this one is in any dump, which is the only reason its answer is not the same
/// kind of known: the *shape* is upstream's, and the shape is all the stub can reproduce
/// anyway — it has no session to hand out either.
#[cfg(target_arch = "aarch64")]
#[unsafe(naked)]
unsafe extern "C" fn slot_28() {
    core::arch::naked_asm!(
        "mov x1, x8", // the sret pointer, out of the indirect-result register
        "b {imp}",    // x0 is already `this`; see `slot_28_impl`
        imp = sym slot_28_impl,
    );
}

/// [`slot_28`]'s body: hand back the pair at `+0x18`.
unsafe extern "C" fn slot_28_impl(this: *mut Session, out: *mut SharedPtr<SessionMember>) {
    announce_slot(5);
    unsafe { hand_out(ptr::addr_of!((*this).member_18), out) };
}

/// Both getters' body: copy the pair out, and take a reference to it if there is one.
///
/// The increment is upstream's, and it is what makes this a getter rather than a borrow —
/// `sub_674a78` ends in
///
/// ```text
/// *x8       = object;
/// x8[1]     = ctrl;
/// if (!ctrl) return;
/// do i = __stxr(__ldxr(ctrl + 8) + 1, ctrl + 8); while (i);
/// ```
///
/// — an unconditional `++shared_owners`, with the null check *on the block* and not on the
/// pair. Ours keeps the copy unconditional and guards the increment.
///
/// Both null checks are defensive rather than reachable: [`session_new`] builds both members
/// or neither, so a session with an empty pair never escapes that function. They are here
/// because a getter that faults is the exact failure this file exists to remove, and a guard
/// is cheaper than an argument about reachability.
///
/// The reference is real and the caller owns it: it releases through libc++'s own
/// `__release_shared`, and the last release frees the member — the same contract the context's
/// `+0x18` has.
unsafe fn hand_out(member: *const SharedPtr<SessionMember>, out: *mut SharedPtr<SessionMember>) {
    if out.is_null() {
        return;
    }
    let pair = if member.is_null() {
        SharedPtr {
            ptr: ptr::null_mut(),
            ctrl: ptr::null_mut(),
        }
    } else {
        unsafe { ptr::read(member) }
    };
    if !pair.ctrl.is_null() {
        unsafe { (*pair.ctrl).shared_owners.fetch_add(1, Ordering::AcqRel) };
    }
    unsafe { ptr::write(out, pair) };
}

/// The member's one modelled slot, which is the same two-shapes problem as the getters'.
#[cfg(target_arch = "aarch64")]
type TokenRequest = unsafe extern "C" fn();
#[cfg(not(target_arch = "aarch64"))]
type TokenRequest = unsafe extern "C" fn(*mut SessionMember, *const c_void, *mut SharedPtr<c_void>);

/// `+0x10` — the background-token request, answered with an empty token.
///
/// The one slot of the member any dump reaches, and the reason [`SessionMember`] exists at
/// all. The caller is `BGCoreUpdateBackgroundToken` again, twenty instructions after it
/// collected the member:
///
/// ```text
/// 0x35b9f0  ldr  x8, [x22]          ; the member
/// 0x35b9f4  ldr  x9, [x8, #0x10]    ; this slot
/// 0x35b9f8  add  x8, sp, #0x10      ; sret: the token pair
/// 0x35b9fc  add  x1, sp, #0x20      ; &std::function, the completion callback
/// 0x35ba04  blr  x9
/// ```
///
/// Here `x1` *is* live, so the thunk below moves `x8` to `x2` rather than to `x1` — the
/// callback has to survive into the body, because whether to call it is the body's decision.
///
/// # An empty pair, deliberately
///
/// The pair written out is `{null, null}`, and that is not a placeholder for a token we have
/// not built yet. It is the answer, for three reasons:
///
/// * **The only caller that exists checks for it.** `0x35b960` loads the cached pair and
///   `cbz` skips the entire fetch when it is null, so an empty token is a state the game
///   reaches in its own code and handles — and re-requesting on the next update is what
///   happens, which is exactly what a stub with no token service should look like from
///   outside.
/// * **Nothing waits on the callback.** The only `future::get()` in `libadventuresync` is at
///   `0x361c50`, on the session-*start* path that [`start_session`] completes inline. This
///   call's `std::function` is destroyed at `0x35ba60` if it was never invoked, so a request
///   that never completes blocks nothing.
/// * **A token of our own would be an invention with no evidence at all.** Unlike the session,
///   where upstream's `operator new(0xe0)` and a live vtable dump fix the shape, nothing here
///   is recoverable: not the type, not its size, not one of its methods. The game would cache
///   it and hand it to whatever a real background token is for, and it would be acting on
///   fabrications.
///
/// The gap that leaves is recorded in the module's deviations: this path stays dormant.
#[cfg(target_arch = "aarch64")]
#[unsafe(naked)]
unsafe extern "C" fn request_token() {
    core::arch::naked_asm!(
        "mov x2, x8", // the sret pointer; x1 is the caller's callback and must survive
        "b {imp}",    // x0 is already `this`; see `request_token_impl`
        imp = sym request_token_impl,
    );
}

/// [`request_token`]'s body — see it for why the answer is empty.
unsafe extern "C" fn request_token_impl(
    _this: *mut SessionMember,
    callback: *const c_void,
    out: *mut SharedPtr<c_void>,
) {
    announce_member_slot(2);
    if within_args_budget(&SEEN_TOKEN_ARGS) {
        log_token_args(callback);
    }
    if !out.is_null() {
        // An *empty* `shared_ptr` — two null words, and a caller that copies it reads both.
        unsafe {
            ptr::write(
                out,
                SharedPtr {
                    ptr: ptr::null_mut(),
                    ctrl: ptr::null_mut(),
                },
            )
        };
    }
}

/// Where libc++'s `std::function` keeps the pointer to the callable it holds.
const STDFUNCTION_CALLABLE: usize = 0x20;
/// Where `operator()` sits in the callable's vtable.
const STDFUNCTION_OPERATOR: usize = 0x30;

/// The `std::function` the member's `+0x10` receives, as three dumps read it (2026-09-23).
///
/// Five words, and the fifth is the finding: the word at `+0x20` is the object's *own* address,
/// which is `__f_` pointing at the start of the function's own buffer — so the callable is
/// `{vtable, capture, capture, capture}` stored inline, and `sizeof` is `0x28`. The object's
/// own last word is its `__f_`, and the `+0x28`/`+0x30` words that the same dumps show as `4`
/// and `-1` are the caller's next locals rather than anything of the function's.
///
/// That is what makes [`STDFUNCTION_CALLABLE`] `0x20` rather than a guess: the buffer has to
/// hold five words, and this NDK's `__value_func` — `__buf_`, `__f_`, `__t_`, three pointers —
/// is a layout the game's callers were not compiled against.
const STDFUNCTION: Shape = Shape {
    size: 0x28,
    fields: &[
        Field {
            offset: 0x00,
            kind: Kind::Word,
        },
        Field {
            offset: 0x08,
            kind: Kind::Word,
        },
        Field {
            offset: 0x10,
            kind: Kind::Word,
        },
        Field {
            offset: 0x18,
            kind: Kind::Word,
        },
        Field {
            offset: 0x20,
            kind: Kind::Word,
        },
    ],
};

/// Invoke a `std::function<void(int32_t const*, shared_ptr<T> const*)>` given by reference.
///
/// The walk is upstream's in both places it appears — `sub_6e6148` at `+0x10` and `sub_4daf68`
/// at `+0x38`: `*(__f_ + 0x20)` for the callable, then `vtable[+0x30]` for `operator()`.
///
/// Returns `false` when there was nothing to call, so a caller can tell "reported" from "there
/// was nobody to report to".
unsafe fn invoke_callback(
    callback: *const c_void,
    status: i32,
    response: *const *const c_void,
) -> bool {
    if callback.is_null() {
        return false;
    }
    let callable = unsafe {
        *callback
            .cast::<u8>()
            .add(STDFUNCTION_CALLABLE)
            .cast::<*const c_void>()
    };
    if callable.is_null() {
        // Upstream reaches `std::__throw_bad_function_call` here. An empty `std::function` is
        // a caller bug: there is nothing to report to, and nothing here can unwind.
        return false;
    }
    let vtable = unsafe { *callable.cast::<*const *const c_void>() };
    let operator: unsafe extern "C" fn(*const c_void, *const i32, *const c_void) = unsafe {
        core::mem::transmute(*vtable.add(STDFUNCTION_OPERATOR / core::mem::size_of::<*const c_void>()))
    };
    unsafe { operator(callable, &status, response.cast()) };
    true
}

// --- showing what the game passed in -------------------------------------------------
//
// Every slot below receives pointers into the game's own memory, and a dump of them is the
// only thing that answers what the game is actually asking for: which token it holds, which
// endpoint it dials, which library called us. All of it goes through `inspect`, so an address
// that is not there costs a line in logcat rather than a signal.

/// How many calls of a slot get their arguments shown.
///
/// Not one, because a slot's first call is often its least informative — the location callback
/// is called with a null fix before any provider has started, and the same shape appears here
/// — and not unbounded, because the token request is made on every background update, which is
/// the flood [`announce_member_slot`] already exists to avoid.
const ARGS_PER_SLOT: u32 = 3;

/// One counter per dumped slot, so a chatty slot cannot spend another slot's budget.
static SEEN_START_ARGS: AtomicU32 = AtomicU32::new(0);
static SEEN_AUTH_ARGS: AtomicU32 = AtomicU32::new(0);
static SEEN_TOKEN_ARGS: AtomicU32 = AtomicU32::new(0);

/// True while `counter`'s budget lasts, and false from then on.
fn within_args_budget(counter: &AtomicU32) -> bool {
    counter.fetch_add(1, Ordering::AcqRel) < ARGS_PER_SLOT
}

/// Show a hex block, one log line per row, each carrying its own `[bgcontext]` prefix.
///
/// The prefix is repeated per row rather than written once and continued, because a dump is
/// read with `grep bgcontext`: a continuation line that does not match the filter is a
/// continuation line nobody sees, and twelve characters a row is cheaper than that.
fn log_block(tag: &str, addr: usize, len: usize) {
    if addr == 0 {
        crate::log::info(&format!("{tag}: <null>"));
        return;
    }
    crate::log::info(&format!("{tag} @0x{addr:x}, {len} bytes:"));
    for row in crate::inspect::hex(addr, len).lines() {
        crate::log::info(&format!("[bgcontext]   {row}"));
    }
}

/// What one field of a game-side struct is, as far as the on-device dumps prove it.
///
/// Three kinds rather than a type per field, because that is what a dump can settle: it shows
/// *where* a field is and which libc++ type has that shape at that offset, and a `std::string`
/// or a `shared_ptr` is a shape — 24 bytes with a form bit in the first, two words with an
/// owner — rather than a name. Nothing here claims to know what a field *means*; the table
/// says what is there, which is the half that can be checked.
#[derive(Clone, Copy)]
enum Kind {
    /// A libc++ `std::string`: 24 bytes.
    String,
    /// A libc++ `shared_ptr`: the object and its control block.
    SharedPtr,
    /// A word the layout has a place for and no type of its own — shown as a value, and named
    /// as the mapping it points into when it points into one.
    Word,
}

#[cfg(test)]
impl Kind {
    /// How many bytes a field of this kind occupies.
    ///
    /// Only the tiling test asks: rendering a field never needs the width, because each kind
    /// reads its own extent by construction — a string's header says where its characters are,
    /// a pair is two words, a word is one. The test is where a wrong number would show up as a
    /// gap or an overrun in the table, so this lives beside it rather than in the dump.
    const fn width(self) -> usize {
        match self {
            Kind::String => 0x18,
            Kind::SharedPtr => 0x10,
            Kind::Word => 0x08,
        }
    }
}

/// One field: where it starts, from the object's address, and what is there.
struct Field {
    offset: usize,
    kind: Kind,
}

/// A struct the game hands over: how big it is, and the fields read out of it.
///
/// `size` is the caller's own `operator new` argument where a call site shows one, and it is
/// where the window past the fields starts. That the fields cover it exactly is therefore a
/// claim this file asserts on the host rather than a comment, and it is the claim that makes a
/// dump readable: every byte of the object is either a named field or past the object.
struct Shape {
    size: usize,
    fields: &'static [Field],
}

/// Where the object sits inside a `make_shared` allocation.
///
/// libc++'s `__shared_ptr_emplace` puts its own header first — the block's vtable and the two
/// counters — and the payload after it, so a pair whose object is exactly this far past its
/// control block came from one allocation. Every pair in the 2026-09-23 dumps has that shape,
/// which is what makes it worth a word on the line rather than a theory: the pairs that do not
/// are the ones in the same object whose object and block are separate allocations.
const MAKE_SHARED_PAYLOAD: usize = 0x18;

/// How many bytes of a refused field are shown beside the reason: one hex row, which is enough
/// to see whether the bytes are a string object read at the wrong offset or a value that is not
/// text, and short enough that the reason and the evidence stay on one log line.
const FIELD_PREVIEW: usize = 0x10;

/// A `std::string` field: the text with its size, and — for the long form — the capacity the
/// header carries, because a capacity below the size is what a misread header looks like.
///
/// Neither form says where its characters are when they are inside the object, so the heap
/// address is shown only for the long form: it is what tells a string the caller built in place
/// from a copy of one that lives elsewhere.
///
/// A refusal is rendered with its reason and with the bytes it was read from — the field's own
/// object first, then the characters it claimed, when the header got far enough to name them.
/// That second line is where a field that *is* a string and holds something that is not text
/// becomes visible: it is the difference between an offset in the table being wrong and a token
/// whose contents are, which are two different repairs.
fn render_string(addr: usize) -> String {
    match crate::inspect::string(addr) {
        Ok(s) => match s.capacity {
            Some(cap) => format!(
                "string({}, cap {cap}) \"{}\" at 0x{:x}",
                s.size, s.text, s.data
            ),
            None => format!("string({}) \"{}\"", s.size, s.text),
        },
        Err(why) => {
            let mut out = format!(
                "<not a string: {why}>  {}",
                crate::inspect::preview(addr, FIELD_PREVIEW)
            );
            if why.data != 0 && why.size != 0 {
                out.push_str(&format!(
                    "\nclaimed characters at 0x{:x}: {}",
                    why.data,
                    crate::inspect::preview(why.data, FIELD_PREVIEW)
                ));
            }
            out
        }
    }
}

/// A `shared_ptr` field: both words, the relation between them, and where the object's own
/// first word points.
///
/// The relation is read rather than assumed. `{0x0, 0x0}` is a finding of its own — an empty
/// pair is the value upstream's "nothing to hand over" branch tests — and one allocation
/// against two is which producer built the pair, which is the difference between an object the
/// game made and one that arrived from somewhere else.
fn render_pair(addr: usize) -> String {
    let Some(pair) = crate::inspect::shared_ptr(addr) else {
        return "<unreadable>".to_string();
    };
    let mut out = format!("shared_ptr {{0x{:x}, 0x{:x}}}", pair.ptr, pair.ctrl);
    if pair.ptr == 0 && pair.ctrl == 0 {
        return format!("{out}, empty");
    }
    if pair.ctrl == 0 {
        out.push_str(", no control block");
    } else if pair.ptr == pair.ctrl + MAKE_SHARED_PAYLOAD {
        out.push_str(&format!(", one allocation (obj = ctrl + {MAKE_SHARED_PAYLOAD:#x})"));
    } else {
        out.push_str(", object and control block are separate allocations");
    }
    // The object's first word, named rather than called a vtable: a dynamic type is exactly
    // what a caller wants to know about a pair, and what that word *is* is a claim this cannot
    // make — a class with no virtual functions has anything at all there.
    if let Some(first) = crate::inspect::word(pair.ptr).and_then(crate::inspect::points_at) {
        out.push_str(&format!(", first word -> {first}"));
    }
    out
}

/// A word with no type of its own: its value, and whether it points at the object it was read
/// from — which is what an inline `std::function`'s `__f_` does, and the one place a
/// self-pointer is the finding rather than a curiosity.
fn render_word(addr: usize, base: usize) -> String {
    let Some(value) = crate::inspect::word(addr) else {
        return "<unreadable>".to_string();
    };
    let mut out = format!("0x{value:x}");
    if value == base {
        out.push_str(" = this");
    }
    if let Some(origin) = crate::inspect::points_at(value) {
        out.push_str(&format!(" -> {origin}"));
    }
    out
}

/// One field as a log line: the offset column, then the reading.
fn render_field(field: &Field, base: usize) -> String {
    let addr = base + field.offset;
    let column = format!("  +{:#06x} ", field.offset);
    let body = match field.kind {
        Kind::String => render_string(addr),
        Kind::SharedPtr => render_pair(addr),
        Kind::Word => render_word(addr, base),
    };
    // A refusal may carry a second line of its own — the bytes it refused on — and it gets the
    // same offset column, so it reads as part of that field rather than as a field of its own.
    let lines = body.lines().collect::<Vec<_>>();
    format!("{column}{}", lines.join(&format!("\n{column}")))
}

/// Every field of `shape`, read from `at`, one line each.
///
/// A separate function from the logging because a host test can assert on a string and cannot
/// assert on logcat, and what these lines say — the offsets, the decoded values, the reasons
/// for a refusal — is the whole of what the dump is for.
fn render_fields(at: usize, shape: &Shape) -> String {
    let mut out = String::new();
    for field in shape.fields {
        out.push_str(&render_field(field, at));
        out.push('\n');
    }
    out
}

/// A game-side struct: the fields the dumps put in it, then the window past it.
///
/// The window is still a window — [`DUMP_WINDOW`] bytes from the object's start — but it is now
/// printed from the point the last field ends, because everything below that has a line of its
/// own. What it covers is the caller's frame: bytes no table explains, and the only place a
/// field the table is missing could still be seen.
fn log_shape(tag: &str, shape: &Shape, at: usize) {
    if at == 0 {
        crate::log::info(&format!("{tag}: <null>"));
        return;
    }
    crate::log::info(&format!("{tag} @0x{at:x}, 0x{:x} bytes:", shape.size));
    for line in render_fields(at, shape).lines() {
        crate::log::info(&format!("[bgcontext]{line}"));
    }
    log_block(
        &format!("{tag} past 0x{:x}, the caller's frame", shape.size),
        at + shape.size,
        DUMP_WINDOW - shape.size,
    );
}

/// Where the `std::function` in `callback` leads, as a fragment for a log line.
///
/// The two hops [`invoke_callback`] takes, read rather than called. The address is the point of
/// it: it says *which* caller reached the slot — `libadventuresync.so+0x…` against
/// `libholoholo.so+0x…` against the plugin itself — and that is the one thing a hex dump of the
/// argument cannot say, because the bytes look the same whichever of them it was.
fn describe_callback(callback: *const c_void) -> String {
    if callback.is_null() {
        return "callback <null>".to_string();
    }
    let at = callback as usize;
    let Some(callable) = crate::inspect::word(at + STDFUNCTION_CALLABLE) else {
        return format!("callback 0x{at:x} <unreadable>");
    };
    if callable == 0 {
        // Upstream reaches `std::__throw_bad_function_call` here, and nothing in this process
        // can unwind — so an empty `std::function` is worth saying out loud rather than
        // reading as "no callback".
        return format!("callback 0x{at:x} <empty>");
    }
    match crate::inspect::word(callable) {
        Some(vtable) => match crate::inspect::word(vtable + STDFUNCTION_OPERATOR) {
            Some(operator) => format!(
                "callback 0x{at:x}, operator() at {}",
                crate::inspect::describe(operator)
            ),
            None => format!("callback 0x{at:x}, callable 0x{callable:x} <no operator()>"),
        },
        None => format!("callback 0x{at:x}, callable 0x{callable:x} <unreadable>"),
    }
}

/// How far every argument dump reaches, counted from the object it was given.
///
/// The hex block covers the part of this window the field tables do *not* — so what it fixes is
/// how much of the caller's frame is shown, which is where a field the table is missing would
/// be, and where everything the caller keeps above the object is. A dump that stopped at the
/// object's own size could only ever confirm the table: it would show the fields where the
/// table puts them and say nothing about whether the game wrote a fifth one past them.
///
/// Reading past the end of an object is free rather than dangerous: [`crate::inspect::hex`]
/// goes through `/proc/self/mem`, so a byte in an unmapped page reads as zero bytes and prints
/// `<unreadable>` instead of faulting, and the last row of a block is itself a finding — it is
/// where the caller's mapping ends.
const DUMP_WINDOW: usize = 0x200;

/// `SessionParams`' size: what the caller allocates, from the `operator new(0x80)` at its own
/// call site in `notes/11` — and what the 2026-09-23 dumps show its fields filling exactly.
const SESSION_PARAMS_SIZE: usize = 0x80;

/// The object `+0x10` receives, as three live dumps read it (0.429.2, 2026-09-23).
///
/// **Not the map `notes/11` reconstructs from the caller's construction sequence.** That map
/// has a `std::string` at `+0x00`, `+0x18` and `+0x30`, a `bool` at `+0x48` and a `shared_ptr`
/// at `+0x50`; the object the game actually passes has a `shared_ptr` at `+0x00`, a long
/// `std::string` of 31 characters at `+0x10`, one word at `+0x28`, and the version string at
/// `+0x48` — `"0.429.2"`, whose first byte `0x0e` is `7 << 1` and not a `bool`. That is what
/// puts the pair the map expects at `+0x50` inside the version string's padding, which is why
/// this shape has no field there.
///
/// Three instances agree on every offset that is not a stale stack address, and the fields
/// cover the `0x80` the caller allocates exactly — a table one field short or one field long
/// would leave a hole or run past the end, which is the check the host test applies.
const SESSION_PARAMS: Shape = Shape {
    size: SESSION_PARAMS_SIZE,
    fields: &[
        Field {
            offset: 0x00,
            kind: Kind::SharedPtr,
        },
        Field {
            offset: 0x10,
            kind: Kind::String,
        },
        Field {
            offset: 0x28,
            kind: Kind::Word,
        },
        Field {
            offset: 0x30,
            kind: Kind::String,
        },
        Field {
            offset: 0x48,
            kind: Kind::String,
        },
        Field {
            offset: 0x60,
            kind: Kind::SharedPtr,
        },
        Field {
            offset: 0x70,
            kind: Kind::SharedPtr,
        },
    ],
};

/// What the game passed `+0x10`, one line per field of [`SESSION_PARAMS`].
fn log_session_params(params: *const c_void) {
    log_shape("[bgcontext] +0x10 params", &SESSION_PARAMS, params as usize);
}

/// `AuthParams`' size: `std::string kind` at `+0x00`, `std::string token` at `+0x18`.
const AUTH_PARAMS_SIZE: usize = 0x30;

/// The object `+0x38`'s `shared_ptr` points at, as three live dumps read it (2026-09-23).
///
/// Two fields, and both are strings: the kind, short and `"background"` in all three calls,
/// and a 156-character long one whose header reads `a1` — capacity 160 — with the same size
/// and a *different* heap pointer each call. A token copied into a fresh `AuthParams` per call
/// rather than one object reused, which is what the pair's own word says as well.
///
/// The map is `notes/11`'s and it survived contact: the two objects behind the three pairs are
/// the same shape, and the shape is what the caller builds as `AuthParams("background", …)`.
const AUTH_PARAMS: Shape = Shape {
    size: AUTH_PARAMS_SIZE,
    fields: &[
        Field {
            offset: 0x00,
            kind: Kind::String,
        },
        Field {
            offset: 0x18,
            kind: Kind::String,
        },
    ],
};

/// What the game passed `+0x38`.
///
/// Two hops, and both are worth a line. The first is the `shared_ptr` itself, because an empty
/// pair and a full one mean different things about the game and only one of them is about us.
/// The second is the `AuthParams` behind it: the kind, and the token the caller is asking to
/// have exchanged. That token is the whole point of the call, so it is the one field here whose
/// *contents* matter — and the one the decoder is most likely to refuse, which is why a refusal
/// now says which check refused it and shows the bytes it was looking at.
fn log_auth_params(auth: *const c_void) {
    let at = auth as usize;
    if at == 0 {
        crate::log::info("[bgcontext] +0x38 auth: <null>");
        return;
    }
    crate::log::info(&format!("[bgcontext] +0x38 auth: {}", render_pair(at)));
    let Some(pair) = crate::inspect::shared_ptr(at) else {
        return;
    };
    if pair.ptr == 0 {
        return;
    }
    log_shape("[bgcontext] +0x38 *auth", &AUTH_PARAMS, pair.ptr);
}

/// What the game passed the member's `+0x10` — the callback it wants the token handed to.
///
/// The walk [`invoke_callback`] takes reads the callable at `+0x20` and its `operator()` at
/// that callable's vtable `+0x30`, and the dumps show the object as [`STDFUNCTION`] has it.
/// What the callable then does is not in this dump at all: [`describe_callback`]'s line is what
/// answers that half, and it is the one that names the module the callback belongs to.
fn log_token_args(callback: *const c_void) {
    log_shape(
        "[bgcontext] member +0x10 callback",
        &STDFUNCTION,
        callback as usize,
    );
    crate::log::info(&format!(
        "[bgcontext] member +0x10 {}",
        describe_callback(callback)
    ));
}

/// `+0x38` — upstream's "begin auth" entry point, `sub_4daf68`.
///
/// The signature is proven rather than inferred. The live vtable holds `0x4daf68` at `+0x38`;
/// that function refcounts `arg2[1]` and releases through `ctrl->vtbl[+0x10]`, which is a
/// `shared_ptr<AuthParams> const*` and nothing else; and it dispatches `arg3` as
/// `*(__f_ + 0x20)` → `vtable[+0x30]`, the walk [`invoke_callback`] does. Its one caller,
/// `sendSoftSfidaLocationUpdate`'s callback at `0xf7e8ec`, constructs an
/// `AuthParams("background", token)` and a `std::function` and passes exactly those two.
///
/// Upstream has two paths. `if (!*(this + 0x90))` starts a session and reports it through the
/// real RPC; the other unlocks and invokes the callback with status **`1`** and an empty
/// response. The stub starts no session, so it always takes the second path — upstream's own
/// answer for "there is nothing to start", not an invention.
///
/// That answer is safe rather than assumed: the callback built at `0xf7ea28` (`__func` vtable
/// `0x1059378`, `operator()` = `0xf7f288`) opens with `if (!*arg2)` and dereferences the
/// response only when the status is zero.
unsafe extern "C" fn begin_auth(this: *mut Session, auth: *const c_void, callback: *const c_void) {
    if within_args_budget(&SEEN_AUTH_ARGS) {
        crate::log::info(&format!(
            "[bgcontext] +0x38 args: this=0x{:x} auth=0x{:x}, {}",
            this as usize,
            auth as usize,
            describe_callback(callback)
        ));
        log_auth_params(auth);
    }
    // An *empty* `shared_ptr` — two null words — because the parameter is a reference and a
    // caller that copies it reads both words through it.
    let response: [*const c_void; 2] = [ptr::null(); 2];
    if unsafe { invoke_callback(callback, 1, response.as_ptr().cast()) } {
        crate::log::info("[bgcontext] session +0x38 (begin auth) called; the stub has no session");
    }
}

/// Builds a member and its owning control block, or null if either allocation failed.
///
/// The same contract [`session_new`] states: the block it hands back holds the only
/// reference, so `shared_owners` starts at zero and the first release tears the member down.
unsafe fn member_new() -> *mut ControlBlock {
    let ctrl = unsafe { alloc(Layout::new::<ControlBlock>()) } as *mut ControlBlock;
    if ctrl.is_null() {
        return ptr::null_mut();
    }
    let member = unsafe { alloc(Layout::new::<SessionMember>()) } as *mut SessionMember;
    if member.is_null() {
        unsafe { dealloc(ctrl as *mut u8, Layout::new::<ControlBlock>()) };
        return ptr::null_mut();
    }
    unsafe {
        ptr::write(
            member,
            SessionMember {
                vtable: &SESSION_MEMBER_VTABLE,
                _unmodelled: [0; MEMBER_UNMODELLED],
            },
        );
        ptr::write(
            ctrl,
            ControlBlock {
                vtable: &MEMBER_CONTROL_VTABLE,
                shared_owners: AtomicIsize::new(0),
                weak_owners: AtomicIsize::new(CONTROL_WEAK_OWNERS),
                payload: member as *mut c_void,
            },
        );
    }
    ctrl
}

/// Builds a session, the two members it holds, and the session's owning control block.
/// Returns the block, or null if any allocation failed — in which case nothing is left
/// allocated.
///
/// `shared_owners` starts at zero — libc++ counts one *less* than the number of owners, so
/// that is "the reference the returned pair itself is", which is exactly what upstream's
/// `+0x10` holds before it calls the callback. A caller that copies the pair is what takes it
/// to a second reference. The members start at zero for the same reason: the session's fields
/// *are* that one reference, and [`session_on_zero_shared`] is what releases them.
unsafe fn session_new() -> *mut ControlBlock {
    // The members first, so the failure path is one branch rather than four. A session
    // without them would be a session whose getters hand out a null pair, which is a shape
    // upstream's callers never see.
    let member_18 = unsafe { member_new() };
    let member_28 = unsafe { member_new() };
    let ctrl = unsafe { alloc(Layout::new::<ControlBlock>()) } as *mut ControlBlock;
    let session = unsafe { alloc(Layout::new::<Session>()) } as *mut Session;

    if member_18.is_null() || member_28.is_null() || ctrl.is_null() || session.is_null() {
        // Nothing has been shared, so each free here is the one its own last release would
        // have run: for the members, both hooks of a block whose only owner is this; for the
        // two bare allocations, the deallocation itself.
        unsafe {
            discard_control(member_18);
            discard_control(member_28);
            if !ctrl.is_null() {
                dealloc(ctrl as *mut u8, Layout::new::<ControlBlock>());
            }
            if !session.is_null() {
                dealloc(session as *mut u8, Layout::new::<Session>());
            }
        }
        return ptr::null_mut();
    }

    unsafe {
        ptr::write(
            session,
            Session {
                vtable: &SESSION_VTABLE,
                // Zeroed rather than left as the allocator handed it over: a consumer that
                // reads one of these words gets a zero, not something that looks like a
                // pointer. Upstream's constructor writes them, but nothing says with what.
                _unmodelled_08: [0; SESSION_UNMODELLED_08],
                member_18: SharedPtr {
                    ptr: (*member_18).payload as *mut SessionMember,
                    ctrl: member_18,
                },
                member_28: SharedPtr {
                    ptr: (*member_28).payload as *mut SessionMember,
                    ctrl: member_28,
                },
                _unmodelled: [0; SESSION_UNMODELLED],
            },
        );
        ptr::write(
            ctrl,
            ControlBlock {
                vtable: &SESSION_CONTROL_VTABLE,
                shared_owners: AtomicIsize::new(0),
                weak_owners: AtomicIsize::new(CONTROL_WEAK_OWNERS),
                payload: session as *mut c_void,
            },
        );
    }
    ctrl
}

/// Frees a control block and its payload straight away, running both of its hooks.
///
/// Only for [`session_new`]'s failure path, where the block was never shared: it holds the
/// only reference, so running `on_zero_shared` and then `on_zero_shared_weak` is exactly what
/// its last release would have done, at a point where no other owner can exist. On a block
/// anybody else has seen, this is a double free.
unsafe fn discard_control(ctrl: *mut ControlBlock) {
    if ctrl.is_null() {
        return;
    }
    unsafe {
        ((*(*ctrl).vtable).on_zero_shared)(ctrl);
        ((*(*ctrl).vtable).on_zero_shared_weak)(ctrl);
    }
}

/// Releases one owner of a control block, freeing what it owns on the last.
///
/// libc++'s `__release_shared` @ `0x7808fc` is a decrement followed, when the *pre*-decrement
/// value was zero, by `__on_zero_shared` and then `__release_weak`. Reproduced here because
/// the stub holds these references itself: the pairs it writes live on its own stack, so no
/// libc++ `shared_ptr` destructor ever runs for them.
///
/// One body for all three payload kinds — the context, the session and the member — because
/// the block's own vtable is what says which is which and how to free it.
unsafe fn control_release(ctrl: *mut ControlBlock) {
    if ctrl.is_null() {
        return;
    }
    if unsafe { (*ctrl).shared_owners.fetch_sub(1, Ordering::AcqRel) } != 0 {
        return;
    }
    unsafe { ((*(*ctrl).vtable).on_zero_shared)(ctrl) };
    if unsafe { (*ctrl).weak_owners.fetch_sub(1, Ordering::AcqRel) } != 0 {
        return;
    }
    unsafe { ((*(*ctrl).vtable).on_zero_shared_weak)(ctrl) };
}

/// Frees the session and the references it holds. Reached from libc++ as `vtbl[+0x10]` when
/// the last owner lets go, and from [`start_session`] for the stub's own reference.
///
/// The member releases are upstream's destructor: `sub_6fd2e0` built a session that owns two
/// `shared_ptr`s, and a session freed without releasing them would leak both objects and both
/// blocks — this is the only place libc++ can learn about them, since the session's own
/// `vtbl[+0x08]` is inert and never runs.
///
/// The coupling [`control_on_zero_shared`] records applies to all three frees: the session's
/// deleting destructor is inert, so this must stay each allocation's only free.
unsafe extern "C" fn session_on_zero_shared(ctrl: *mut ControlBlock) {
    if ctrl.is_null() {
        return;
    }
    let payload = unsafe { (*ctrl).payload } as *mut Session;
    if payload.is_null() {
        return;
    }
    unsafe {
        control_release((*payload).member_18.ctrl);
        control_release((*payload).member_28.ctrl);
        dealloc(payload as *mut u8, Layout::new::<Session>());
    }
}

/// Frees the member. Reached from libc++ as `vtbl[+0x10]` when the last owner lets go — and
/// the last owner is the session, unless something outside kept a copy from a getter.
unsafe extern "C" fn member_on_zero_shared(ctrl: *mut ControlBlock) {
    if ctrl.is_null() {
        return;
    }
    let payload = unsafe { (*ctrl).payload };
    if !payload.is_null() {
        unsafe { dealloc(payload as *mut u8, Layout::new::<SessionMember>()) };
    }
}

/// `+0x10` — the session start, answered with a live session object and status zero.
///
/// Distinct from [`unobserved`] on purpose: that one is a bet that nothing calls it, this one
/// is known to be reached by three libraries. Upstream invokes the completion callback
/// **inline, exactly once**, with a status it hard-codes to zero — `sub_6e6148` stores `0`
/// before its only call site and has no failure branch at all. Everything downstream is
/// therefore written on the assumption that the third argument points at a live response, and
/// two of the three callers read it without looking at the status first.
///
/// # What is and is not reproduced
///
/// The engine behind this slot is not. Upstream's producer is `sub_6fd2e0`: 1057 instructions
/// driving about fifteen collaborators, whose session talks to a `nia::api::background::
/// RpcManager` that has no vtable, constructor or method in any image available — and the
/// plugin ships no HTTP stack for it to reach. `notes/11-nia-api-background.md` is the dossier.
///
/// The *contract* is reproduced exactly, because that is what the callers are written against:
/// status zero, and a live object of upstream's size and vtable shape in the response.
/// [`Session`] records why an empty pair is not survivable there.
///
/// # The freeze
///
/// `libadventuresync`'s `BGCoreUpdateBackgroundToken` builds a `std::promise<void>` around this
/// slot and calls `future::get()` on the next instruction, on the main thread. A callback that
/// never arrives is therefore the main thread parked for good, which is what the reported
/// freeze was. Invoking it inline, once, is upstream's behaviour and is what clears that.
///
/// # The residual gap
///
/// A caller that keeps the session gets a stub. `+0x38` answers with upstream's "nothing to
/// start"; `+0x20` and `+0x28` hand out the two members upstream's session holds, and the
/// member's own `+0x10` answers the background-token request with an empty token; the other
/// seven slots are inert. So nothing here will start, drive or stop a real session, and the
/// background-token path is dormant rather than served. That is recorded in the module's
/// deviations rather than left to be discovered on a device.
unsafe extern "C" fn start_session(
    this: *mut BackgroundContext,
    params: *const c_void,
    callback: *const c_void,
) {
    if within_args_budget(&SEEN_START_ARGS) {
        crate::log::info(&format!(
            "[bgcontext] +0x10 args: this=0x{:x} params=0x{:x}, {}",
            this as usize,
            params as usize,
            describe_callback(callback)
        ));
        log_session_params(params);
    }
    // A live session rather than an empty pair, because two of the three callers read the
    // response without looking at the status first. See [`Session`].
    let ctrl = unsafe { session_new() };
    let mut status: i32 = 1;
    // An *empty* `shared_ptr` — two null words — because the parameter is a reference and a
    // caller that copies it reads both words through it.
    let mut response: [*const c_void; 2] = [ptr::null(); 2];

    if ctrl.is_null() {
        crate::log::info(
            "[bgcontext] +0x10: session allocation failed; callback told it failed",
        );
    } else {
        response = [unsafe { (*ctrl).payload } as *const c_void, ctrl as *const c_void];
        // Zero, which is upstream's value and not a placeholder for a real answer: its only
        // call site stores it as a constant and has no branch that could store anything else.
        status = 0;
        crate::log::info(
            "[bgcontext] +0x10: session start -> stub session, status 0",
        );
    }

    unsafe { invoke_callback(callback, status, response.as_ptr().cast()) };

    // The stub's own reference. Upstream passes its local `shared_ptr` by reference and
    // destroys it at the same point, so a caller that kept a copy owns the object and a
    // caller that did not leaves nothing behind.
    unsafe { control_release(ctrl) };
}

static CONTROL_VTABLE: ControlBlockVtable = ControlBlockVtable {
    complete_dtor: control_complete_dtor,
    deleting_dtor: control_deleting_dtor,
    on_zero_shared: control_on_zero_shared,
    get_deleter: control_get_deleter,
    on_zero_shared_weak: control_on_zero_shared_weak,
};

/// [`CONTROL_VTABLE`]'s twin for sessions, differing only in what it frees.
static SESSION_CONTROL_VTABLE: ControlBlockVtable = ControlBlockVtable {
    complete_dtor: control_complete_dtor,
    deleting_dtor: control_deleting_dtor,
    on_zero_shared: session_on_zero_shared,
    get_deleter: control_get_deleter,
    on_zero_shared_weak: control_on_zero_shared_weak,
};

/// [`CONTROL_VTABLE`]'s twin for members, and the third block the stub builds — one per
/// payload type, because it is the block's vtable that decides how the payload is freed.
static MEMBER_CONTROL_VTABLE: ControlBlockVtable = ControlBlockVtable {
    complete_dtor: control_complete_dtor,
    deleting_dtor: control_deleting_dtor,
    on_zero_shared: member_on_zero_shared,
    get_deleter: control_get_deleter,
    on_zero_shared_weak: control_on_zero_shared_weak,
};

/// The slots the vtables hold, which are thunks on device and bare bodies on the host — the
/// two have different signatures, so the choice is made once each, here.
#[cfg(target_arch = "aarch64")]
const SHARED_FROM_THIS: SharedFromThis = shared_from_this;
#[cfg(not(target_arch = "aarch64"))]
const SHARED_FROM_THIS: SharedFromThis = shared_from_this_impl;

#[cfg(target_arch = "aarch64")]
const SLOT_20: SessionGetter = slot_20;
#[cfg(not(target_arch = "aarch64"))]
const SLOT_20: SessionGetter = slot_20_impl;

#[cfg(target_arch = "aarch64")]
const SLOT_28: SessionGetter = slot_28;
#[cfg(not(target_arch = "aarch64"))]
const SLOT_28: SessionGetter = slot_28_impl;

#[cfg(target_arch = "aarch64")]
const TOKEN_REQUEST: TokenRequest = request_token;
#[cfg(not(target_arch = "aarch64"))]
const TOKEN_REQUEST: TokenRequest = request_token_impl;

static VTABLE: BackgroundContextVtable = BackgroundContextVtable {
    slot_00: unobserved,
    slot_08: unobserved,
    slot_10: start_session,
    shared_from_this: SHARED_FROM_THIS,
};

/// The ten slots [`Session`] hands to its consumers, in upstream's order.
///
/// Every entry is non-null, and that is the property the table exists to provide: the
/// response is dereferenced through `+0x38` by a caller that never checks the status, and
/// kept for later use by another that does not check it either.
static SESSION_VTABLE: SessionVtable = SessionVtable {
    slot_00: dormant_00,
    slot_08: dormant_08,
    slot_10: dormant_10,
    slot_18: dormant_18,
    slot_20: SLOT_20,
    slot_28: SLOT_28,
    slot_30: dormant_30,
    slot_38: begin_auth,
    slot_40: dormant_40,
    slot_48: dormant_48,
};

/// The ten slots [`SessionMember`] gives whatever collects one.
///
/// The same property as [`SESSION_VTABLE`], over the object [`slot_20`] hands out: a caller
/// that jumps through `+0x10` — which is what the one caller in the dumps does — lands in a
/// real function, and so does a caller that jumps through anything else.
static SESSION_MEMBER_VTABLE: SessionMemberVtable = SessionMemberVtable {
    slot_00: member_dormant_00,
    slot_08: member_dormant_08,
    slot_10: TOKEN_REQUEST,
    slot_18: member_dormant_18,
    slot_20: member_dormant_20,
    slot_28: member_dormant_28,
    slot_30: member_dormant_30,
    slot_38: member_dormant_38,
    slot_40: member_dormant_40,
    slot_48: member_dormant_48,
};

/// The token's global reference, held process-wide in a single slot.
///
/// Upstream's `data_105e170`, and the same replace-and-release shape: a fresh token releases
/// the one before it. The caller latches on its first success, so in practice the
/// replacement path is cold — it is here because the original's is, and because a different
/// library also resolving this symbol must not strand the first token's reference.
///
/// **Nothing reads this, and that is the translation rather than an omission.** Holding the
/// reference is the entire point: upstream reads `data_105e170` from the session machinery
/// behind `vtbl[+0x10]`, which starts no session here. So the store is kept and the load has
/// no caller — but the reference is live in the VM either way, which keeps the Java object
/// alive.
static TOKEN_GLOBAL: AtomicPtr<c_void> = AtomicPtr::new(ptr::null_mut());

/// The loader `bgToken.getClassLoader()` yielded, as a global reference.
///
/// Upstream's `data_105e170` — the second half of step 4b, and the object a later
/// `loadClass` would be called on. Held for the same reason as [`TOKEN_GLOBAL`]: the
/// reference is what keeps the Java object alive, and the load that would use it has no
/// caller here yet.
static CLASS_LOADER_GLOBAL: AtomicPtr<c_void> = AtomicPtr::new(ptr::null_mut());

/// `ClassLoader.loadClass(String)`, as a `jmethodID` in a `*mut c_void`.
///
/// Upstream's `data_105e978`, a bare `jmethodID` in `.bss` — this is the one step-4b global
/// whose upstream type is *not* a `shared_ptr`, so the `AtomicPtr` here is a widening rather
/// than a stand-in.
///
/// **In an optimised build this static does not survive.** Nothing reads it, so the store at
/// the end of [`cache_class_loader`] is dead and LLVM removes both it and the storage — the
/// release `.so` has no symbol for it at all, and `create` branches straight from the
/// `GetMethodID` call to its epilogue. The *call* is still made: an indirect call through a
/// function pointer is not something the optimiser may delete. See the deviations block for
/// why that is the correct outcome rather than a defect.
static LOAD_CLASS_METHOD: AtomicPtr<c_void> = AtomicPtr::new(ptr::null_mut());

/// Allocate a context and hand back the pointer `libadventuresync` expects.
///
/// Never returns null unless the allocation fails, because a null here is not a value the
/// caller checks for — see the module docs. A null `env` is tolerated: the VM cache is
/// simply not updated, the token is not promoted, and the object is still built.
///
/// The context is owned by the control block [`shared_from_this_impl`] builds, and freed
/// with it. Until the caller's `shared_ptr` goes away it lives as long as the game, which
/// is the same lifetime the original gives it.
///
/// # Safety
/// `env`, when non-null, must be a valid `JNIEnv*`, and `bg_token`, when non-null, a valid
/// `jobject` in it.
pub unsafe fn create(env: *mut JNIEnv, bg_token: jobject) -> *mut c_void {
    // The observable half of upstream steps 2 and 3, and worth keeping on its own: this
    // is the only place on this path that tells Dittobuffer the `JavaVM`. `env_to_vm` and
    // `set_vm` both tolerate null, so no branch is needed here.
    crate::jni::set_vm(crate::jni::env_to_vm(env));

    // Bare `alloc` rather than `Box`, so an out-of-memory return is a value to check and
    // not an abort: `panic = "abort"` would take the game down, and this is the FFI edge.
    let raw = alloc(Layout::new::<BackgroundContext>()) as *mut BackgroundContext;
    if raw.is_null() {
        // Upstream's `operator new` would throw `bad_alloc`. There is nothing better to
        // do here either — 24 bytes failing means the process is already gone — but the
        // log line is the only evidence of *why* the caller is about to fault.
        crate::log::info("[bgcontext] CreateBackgroundContext: allocation failed");
        return ptr::null_mut();
    }

    // Field by field, matching the original's constructor: `operator new` does not zero,
    // and the two weak fields must read as "no control block yet" until the `+0x18` slot
    // fills them.
    ptr::write(
        raw,
        BackgroundContext {
            vtable: &VTABLE,
            weak_self: ptr::null_mut(),
            weak_ctrl: ptr::null_mut(),
        },
    );

    setup_context_java(env, bg_token);

    raw as *mut c_void
}

/// Upstream's `setup_context_java(bgToken)` @ `0x6e9fc8` — everything step 4 does with the
/// token, in the order the original does it.
///
/// Best-effort throughout: each half logs and returns on failure rather than unwinding the
/// allocation [`create`] already made, because a context without a cached loader is still a
/// context the caller dereferences unconditionally.
unsafe fn setup_context_java(env: *mut JNIEnv, bg_token: jobject) {
    // A null `env` cannot make a JNI call at all, and a null token is not an object to
    // promote. `new_global_ref` already treats a null token as "nothing to promote"; the
    // guard here is what keeps every call below under an env that can carry one.
    if env.is_null() || bg_token.is_null() {
        return;
    }
    promote_token(env, bg_token);
    cache_class_loader(env, bg_token);
}

/// Step 4a — `env->NewGlobalRef(env, bgToken)`, literally `mov x1, x19` /
/// `ldr x8, [x8, #0xa8]` / `blr x8` at `0x6ea2f0`.
unsafe fn promote_token(env: *mut JNIEnv, bg_token: jobject) {
    let global = crate::jni::new_global_ref(env, bg_token);
    if global.is_null() {
        crate::log::info("[bgcontext] CreateBackgroundContext: NewGlobalRef(bgToken) failed");
        return;
    }
    // The swap publishes the new reference and hands back the old one, so the release only
    // ever runs once the slot already points at its replacement. `delete_global_ref`
    // tolerates null, so the first call needs no branch.
    let previous = TOKEN_GLOBAL.swap(global as *mut c_void, Ordering::AcqRel);
    crate::jni::delete_global_ref(env, previous as jobject);
}

/// Step 4b @ `0x6ea348`–`0x6ea44c` — the application `ClassLoader` cache.
///
/// `getClassLoader()` on the token gives the loader that holds the app's own classes;
/// promoting it and resolving `loadClass` against it is what makes a class lookup possible
/// from a thread the JVM does not know about. `FindClass` on such a thread resolves against
/// the *system* loader and cannot see app classes at all — the hazard raised in
/// `notes/09-open-questions.md` — which is exactly why `name2` is a bootclasspath class and
/// not the class the caller actually wants.
///
/// Resolving the method is as far as this goes: the load itself belongs to the session
/// machinery behind `vtbl[+0x10]`, which is inert here.
unsafe fn cache_class_loader(env: *mut JNIEnv, bg_token: jobject) {
    // `0x6ea348`. Null means the token is not an instance of anything the VM can name, and
    // there is no loader to find.
    let token_class = crate::jni::get_object_class(env, bg_token);
    if token_class.is_null() {
        crate::log::info("[bgcontext] CreateBackgroundContext: GetObjectClass(bgToken) failed");
        return;
    }

    // `0x6ea368`–`0x6ea394`. The call site's third argument is the compiler's variadic
    // `CallObjectMethod` shim at `0x582fb8`, which forwards to `CallObjectMethodV`; the site
    // passes no varargs, so slot 34 is the same call — see the deviations block.
    let Some(get_class_loader) = method(env, token_class, GET_CLASS_LOADER) else {
        return;
    };
    let loader = crate::jni::call_object_method(env, bg_token, get_class_loader);
    if loader.is_null() {
        // Null is both "the method threw" and "it returned null", and neither leaves a
        // loader to keep. The distinction does not change what happens next, but a pending
        // `Throwable` outlives this function and poisons the next JNI call, so it is cleared
        // whichever of the two it was.
        crate::jni::clear_exception(env);
        crate::log::info("[bgcontext] CreateBackgroundContext: getClassLoader() returned null");
        return;
    }

    // `0x6ea3ac`. The result is a local reference; keeping it past this frame needs the
    // promotion. The local itself is left alone, as upstream leaves it — the frame's
    // teardown frees it.
    let global = crate::jni::new_global_ref(env, loader);
    if global.is_null() {
        crate::log::info("[bgcontext] CreateBackgroundContext: NewGlobalRef(classLoader) failed");
        return;
    }
    let previous = CLASS_LOADER_GLOBAL.swap(global as *mut c_void, Ordering::AcqRel);
    crate::jni::delete_global_ref(env, previous as jobject);

    // `0x6ea410`.
    let cl_class = crate::jni::find_class(env, CLASS_LOADER_CLASS.as_ptr());
    if cl_class.is_null() {
        // `find_class` is the one JNI call on this path that does not clear its own
        // exception; its caller owns that, per its own docs.
        crate::jni::clear_exception(env);
        crate::log::info("[bgcontext] CreateBackgroundContext: FindClass(java/lang/ClassLoader) failed");
        return;
    }

    // `0x6ea430`, then the store at `0x6ea44c`.
    if let Some(load_class) = method(env, cl_class, LOAD_CLASS) {
        LOAD_CLASS_METHOD.store(load_class as *mut c_void, Ordering::Release);
    }
}

/// Resolve a `(name, signature)` pair, or clear the exception, log and report nothing.
///
/// The same discipline as [`crate::jni::location_service::method`] — a failed `GetMethodID`
/// throws `NoSuchMethodError`, and JNI is only defined while nothing is pending, so the clear
/// cannot be left to the caller. Not that function itself: its log line names the location
/// chain, and that prefix is the only thing in logcat that says which subsystem refused.
unsafe fn method(env: *mut JNIEnv, class: jclass, pair: (&CStr, &CStr)) -> Option<jmethodID> {
    let (name, sig) = pair;
    let id = crate::jni::get_method_id(env, class, name.as_ptr(), sig.as_ptr());
    if id.is_null() {
        crate::jni::clear_exception(env);
        crate::log::info(&format!(
            "[bgcontext] CreateBackgroundContext: no method {}{}",
            name.to_string_lossy(),
            sig.to_string_lossy(),
        ));
        return None;
    }
    Some(id)
}

/// Serialize every test that touches the background context.
///
/// The three statics here are process-wide, but that is not the only reason for the lock:
/// [`create`] drives the fake JVM, so it also writes the cached `JavaVM` and — through the
/// unmodelled `GetJavaVM` slot — increments
/// [`fake_jni::unexpected_calls`](crate::jni::fake_jni::unexpected_calls), which other modules
/// assert is zero. All of that is one shared resource, so there is one lock for all of it,
/// owned by the harness. See [`crate::jni::fake_jni::test_guard`]. Take it *before*
/// [`fresh_env`], which resets the recorder.
#[cfg(test)]
pub fn test_guard() -> std::sync::MutexGuard<'static, ()> {
    crate::jni::fake_jni::test_guard()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jni::fake_jni;
    use core::mem::{offset_of, size_of};
    use core::sync::atomic::{AtomicI32, AtomicUsize, Ordering};

    /// A fake VM, with all three process-globals emptied first.
    ///
    /// They outlive a test, so they are cleared here rather than left for whatever runs next
    /// to trip over: a stale reference would show up there as an extra `DeleteGlobalRef` it
    /// never asked for, and a stale `jmethodID` as a call through a foreign handle.
    fn fresh_env() -> fake_jni::FakeEnv {
        let _ = TOKEN_GLOBAL.swap(ptr::null_mut(), Ordering::AcqRel);
        let _ = CLASS_LOADER_GLOBAL.swap(ptr::null_mut(), Ordering::AcqRel);
        let _ = LOAD_CLASS_METHOD.swap(ptr::null_mut(), Ordering::Release);
        fake_jni::reset();
        fake_jni::table()
    }

    /// The reference [`TOKEN_GLOBAL`] currently holds, in the type JNI calls it.
    ///
    /// A test-only accessor rather than a `pub(crate)` one: nothing outside these tests reads
    /// the slot, and a non-test accessor is dead code in the shipped library.
    fn held_token() -> jobject {
        TOKEN_GLOBAL.load(Ordering::Acquire) as jobject
    }

    /// The reference [`CLASS_LOADER_GLOBAL`] currently holds. See [`held_token`].
    fn held_loader() -> jobject {
        CLASS_LOADER_GLOBAL.load(Ordering::Acquire) as jobject
    }

    /// The `jmethodID` [`LOAD_CLASS_METHOD`] currently holds. See [`held_token`].
    fn held_load_class() -> jmethodID {
        LOAD_CLASS_METHOD.load(Ordering::Acquire) as jmethodID
    }

    /// A token the harness can name, which is what step 4b needs to get past
    /// `GetObjectClass` — [`fake_jni::stub_ref`] answers null there, deliberately.
    fn named_token() -> jobject {
        fake_jni::stub_object("com/nianticlabs/nia/unity/UnityPlayerToken")
    }

    /// The `operator new(0x18)` in the disassembly. The caller reads `[+0x18]` only via
    /// `*ctx` (the vtable), so a struct that grew here would move every field the caller
    /// touches — and a smaller one would be a buffer the caller reads past.
    #[test]
    fn context_is_the_twenty_four_bytes_the_original_allocates() {
        assert_eq!(size_of::<BackgroundContext>(), 0x18);
        assert_eq!(offset_of!(BackgroundContext, vtable), 0x00);
        assert_eq!(offset_of!(BackgroundContext, weak_self), 0x08);
        assert_eq!(offset_of!(BackgroundContext, weak_ctrl), 0x10);
    }

    /// Four entries in the original's `0xb93af0`, and the caller indexes the last one by
    /// byte offset rather than by name — `ldr x9, [x8, #0x18]`. Moving it is a call to
    /// the wrong function with the wrong registers.
    #[test]
    fn the_called_slot_is_at_0x18() {
        assert_eq!(size_of::<BackgroundContextVtable>(), 0x20);
        assert_eq!(offset_of!(BackgroundContextVtable, slot_00), 0x00);
        assert_eq!(offset_of!(BackgroundContextVtable, slot_08), 0x08);
        assert_eq!(offset_of!(BackgroundContextVtable, slot_10), 0x10);
        assert_eq!(offset_of!(BackgroundContextVtable, shared_from_this), 0x18);
    }

    /// The pair the caller loads as one `q0` from `sp`. `__ptr_` first, `__cntrl_`
    /// second, both 8-byte — the libc++ layout.
    #[test]
    fn shared_ptr_is_pointer_then_control_block() {
        assert_eq!(size_of::<SharedPtr>(), 0x10);
        assert_eq!(offset_of!(SharedPtr, ptr), 0x00);
        assert_eq!(offset_of!(SharedPtr, ctrl), 0x08);
    }

    /// The `operator new(0x20)`, and the field offsets libc++ itself reads and decrements.
    /// These are not ours to choose: `__release_shared` and `__release_weak` are already
    /// in the caller's address space and will act on whatever is at `+0x08` and `+0x10`.
    #[test]
    fn control_block_is_the_libcxx_shared_weak_count_layout() {
        assert_eq!(size_of::<ControlBlock>(), 0x20);
        assert_eq!(offset_of!(ControlBlock, vtable), 0x00);
        assert_eq!(offset_of!(ControlBlock, shared_owners), 0x08);
        assert_eq!(offset_of!(ControlBlock, weak_owners), 0x10);
        assert_eq!(offset_of!(ControlBlock, payload), 0x18);
    }

    /// The two slots libc++ reaches from its release path, at the offsets recovered from
    /// `__release_shared` @ `0x7808fc`: `+0x10` is `__on_zero_shared`, `+0x20` is
    /// `__on_zero_shared_weak`. Swapping them makes libc++ free the block while the
    /// context is still live, or never free it at all.
    #[test]
    fn libcxx_finds_its_hooks_at_the_offsets_it_calls() {
        assert_eq!(size_of::<ControlBlockVtable>(), 0x28);
        assert_eq!(offset_of!(ControlBlockVtable, complete_dtor), 0x00);
        assert_eq!(offset_of!(ControlBlockVtable, deleting_dtor), 0x08);
        assert_eq!(offset_of!(ControlBlockVtable, on_zero_shared), 0x10);
        assert_eq!(offset_of!(ControlBlockVtable, get_deleter), 0x18);
        assert_eq!(offset_of!(ControlBlockVtable, on_zero_shared_weak), 0x20);
    }

    /// The whole point of the change: a non-null object, so the caller's latch at
    /// `arg1[0x18]` takes and the next call short-circuits instead of faulting again.
    #[test]
    fn create_returns_a_stamped_context_even_without_an_env() {
        unsafe {
            let ctx = create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext;
            assert!(!ctx.is_null());
            assert_eq!((*ctx).vtable as *const _, &VTABLE as *const _);
            // Zeroed, exactly as the original's constructor leaves them: no control block
            // exists until the `+0x18` slot runs, and nothing else may fill them in.
            assert!((*ctx).weak_self.is_null());
            assert!((*ctx).weak_ctrl.is_null());
        }
    }

    /// Upstream allocates on every call and the caller is what makes it once-only. Pinned
    /// so a future "optimisation" to a shared static is a deliberate choice rather than
    /// an accident: several libraries resolving this symbol would then share one object.
    #[test]
    fn each_call_allocates_a_fresh_context() {
        unsafe {
            let a = create(ptr::null_mut(), ptr::null_mut());
            let b = create(ptr::null_mut(), ptr::null_mut());
            assert!(!a.is_null() && !b.is_null());
            assert_ne!(a, b);
        }
    }

    /// What the caller receives, and what a real libc++ release will therefore act on.
    #[test]
    fn the_0x18_slot_returns_an_owning_shared_ptr() {
        unsafe {
            let ctx = create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext;
            let mut out = SharedPtr {
                ptr: ptr::dangling_mut::<BackgroundContext>(),
                ctrl: ptr::dangling_mut::<ControlBlock>(),
            };
            shared_from_this_impl(ctx, &mut out);

            assert_eq!(out.ptr, ctx);
            assert!(!out.ctrl.is_null(), "the block libc++ will decrement");
            let ctrl = out.ctrl;
            assert_eq!((*ctrl).vtable as *const _, &CONTROL_VTABLE as *const _);
            assert_eq!((*ctrl).payload, ctx as *mut c_void, "the payload __on_zero_shared frees");
            // One owner, so the caller's first release tears the block down...
            assert_eq!((*ctrl).shared_owners.load(Ordering::Relaxed), 0);
            // ...and frees it in that same release, since no weak reference is held.
            assert_eq!(
                (*ctrl).weak_owners.load(Ordering::Relaxed),
                CONTROL_WEAK_OWNERS,
            );

            // `__weak_this_`, which the original caches here too.
            assert_eq!((*ctx).weak_self, ctx);
            assert_eq!((*ctx).weak_ctrl, out.ctrl as *mut c_void);
        }
    }

    /// A second call returns the *same* block rather than a competing one, which is what
    /// the write-back to `+0x08`/`+0x10` is for. Nothing in the dumps makes this call, but
    /// a reader outside them would find a context that claims never to have been shared if
    /// the fields were left alone.
    #[test]
    fn the_context_remembers_the_block_it_was_given() {
        unsafe {
            let ctx = create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext;
            let mut first = SharedPtr {
                ptr: ptr::null_mut(),
                ctrl: ptr::null_mut(),
            };
            shared_from_this_impl(ctx, &mut first);

            // What the original's `enable_shared_from_this` would answer with.
            assert_eq!((*ctx).weak_self, ctx);
            assert_eq!(
                (*ctx).weak_ctrl as *mut ControlBlock,
                first.ctrl,
                "the cached block must be the one handed out",
            );
        }
    }

    /// Every slot is a real function. A null here is not a no-op: the caller would `blr`
    /// through it, and so would libc++.
    #[test]
    fn no_vtable_slot_is_null() {
        assert_ne!(VTABLE.slot_00 as usize, 0);
        assert_ne!(VTABLE.slot_08 as usize, 0);
        assert_ne!(VTABLE.slot_10 as usize, 0);
        assert_ne!(VTABLE.shared_from_this as usize, 0);

        assert_ne!(CONTROL_VTABLE.complete_dtor as usize, 0);
        assert_ne!(CONTROL_VTABLE.deleting_dtor as usize, 0);
        assert_ne!(CONTROL_VTABLE.on_zero_shared as usize, 0);
        assert_ne!(CONTROL_VTABLE.get_deleter as usize, 0);
        assert_ne!(CONTROL_VTABLE.on_zero_shared_weak as usize, 0);
    }

    /// `Setup_context_java` step 4a — `env->NewGlobalRef(env, bgToken)` at `0x6ea2f0`. The
    /// caller's token is a local reference in a frame that is about to end, so this is what
    /// makes keeping it legal at all.
    #[test]
    fn create_promotes_the_token_to_a_global_reference() {
        let _g = test_guard();
        let jni = fresh_env();
        let token = fake_jni::stub_ref();

        unsafe { create(jni.env, token) };

        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 1, "promoted exactly once, not zero or twice");
            assert_eq!(r.global_refs.len(), 1);
            assert_eq!(r.global_refs[0].1, token, "promoted *from* the caller's token");
        });
        assert!(!held_token().is_null(), "the reference is held");
    }

    /// The handle kept is the one `NewGlobalRef` minted, not the caller's. Holding the local
    /// handle instead is the bug the call exists to prevent — it reads as valid right up
    /// until the frame that owns it returns.
    #[test]
    fn the_reference_kept_is_the_global_one() {
        let _g = test_guard();
        let jni = fresh_env();
        let token = fake_jni::stub_ref();
        let ctx = unsafe { create(jni.env, token) };

        assert!(!ctx.is_null());
        let held = held_token();
        fake_jni::with_recorder(|r| {
            assert_eq!(r.global_refs[0].0, held, "the handle that was stored");
            assert_ne!(held, token, "a global ref is its own handle, not the local one");
        });
    }

    /// A second token releases the first one's reference rather than stranding it —
    /// upstream's `if (old) release(old)`, which is what the `data_105e170` swap does.
    #[test]
    fn a_second_create_releases_the_previous_tokens_reference() {
        let _g = test_guard();
        let jni = fresh_env();

        let first = unsafe { create(jni.env, fake_jni::stub_ref()) };
        let held_first = held_token();
        fake_jni::with_recorder(|r| assert_eq!(r.delete_global_refs, 0, "nothing to release yet"));

        let second = unsafe { create(jni.env, fake_jni::stub_ref()) };

        assert_ne!(first, second, "each call still allocates its own context");
        assert_ne!(held_token(), held_first, "the slot now holds the new token");
        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 2);
            assert_eq!(r.delete_global_refs, 1, "the predecessor, exactly once");
            // The one released is the one the slot used to hold.
            assert_eq!(r.global_refs[0].0, held_first as jobject);
        });
    }

    /// Nothing to promote, so nothing is promoted — and the object is still handed back,
    /// because the caller dereferences it unconditionally.
    #[test]
    fn a_null_token_promotes_nothing() {
        let _g = test_guard();
        let jni = fresh_env();

        let ctx = unsafe { create(jni.env, ptr::null_mut()) };

        assert!(!ctx.is_null());
        assert!(held_token().is_null());
        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 0);
            assert_eq!(r.delete_global_refs, 0);
        });
    }

    /// No env means no JNI call is possible at all — and the context is still built, which
    /// is the branch the existing tests above already rely on.
    #[test]
    fn a_null_env_promotes_nothing_and_still_builds_the_context() {
        let _g = test_guard();
        let _ = fresh_env();

        let ctx = unsafe { create(ptr::null_mut(), fake_jni::stub_ref()) };

        assert!(!ctx.is_null());
        assert!(held_token().is_null());
        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 0);
            assert!(!r.pending_exception, "a null env must not leave one behind");
        });
    }

    /// Step 4b, and the test the whole reversal exists for: the five strings are the ones
    /// recovered from the live process, in the order the original resolves them.
    ///
    /// Pinning the exact `(name, signature)` pairs rather than merely "two lookups happened"
    /// is the point. A wrong `name1` is not a crash and not a visible failure — it is a
    /// `NoSuchMethodError` that gets cleared and logged — so without this the stub could
    /// resolve anything at all and every other test here would still pass.
    #[test]
    fn step_4b_resolves_the_five_recovered_strings_in_order() {
        let _g = test_guard();
        let jni = fresh_env();
        let token = named_token();

        unsafe { create(jni.env, token) };

        fake_jni::with_recorder(|r| {
            let pairs: Vec<(&str, &str)> = r
                .method_ids
                .iter()
                .map(|(n, s)| (n.as_str(), s.as_str()))
                .collect();
            assert_eq!(
                pairs,
                vec![
                    ("getClassLoader", "()Ljava/lang/ClassLoader;"),
                    ("loadClass", "(Ljava/lang/String;)Ljava/lang/Class;"),
                ],
            );
            // `name2`. A path, not a method — it reaches the table as its own shape.
            assert_eq!(r.find_class, vec!["java/lang/ClassLoader".to_owned()]);
            // `GetObjectClass` on the token, before either lookup: it is what `name1` is
            // resolved *against*, so resolving the method first would be a call on a class
            // that was never obtained.
            assert_eq!(r.get_object_class, 1);
            // The call sits between the two lookups, on the token itself, not on the class.
            assert_eq!(
                r.calls_object,
                vec![(token, "getClassLoader".to_owned())],
                "the call is made on the token, not on its class",
            );
            assert!(!r.pending_exception, "and nothing was left throwing");
        });
    }

    /// The loader is promoted to a global reference and kept, exactly as the token is — both
    /// are locals in this frame, and both are read after it returns.
    #[test]
    fn step_4b_keeps_the_loader_as_a_global_reference() {
        let _g = test_guard();
        let jni = fresh_env();

        unsafe { create(jni.env, named_token()) };

        let held = held_loader();
        assert!(!held.is_null(), "the loader's reference is held");
        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 2, "the token, then the loader");
            assert_eq!(r.global_refs[1].0, held, "the handle that was stored");
            assert_eq!(r.delete_global_refs, 0, "nothing to release yet");
        });
    }

    /// `loadClass` is resolved and cached in the process-global, not merely looked up: that
    /// store at `0x6ea44c` is the last thing step 4b does, and it is what a later call from a
    /// worker thread would read.
    ///
    /// This is a statement about the source, not about the shipped `.so` — nothing reads the
    /// static outside these tests, so an optimised build has no store to observe. See the
    /// deviations block; the distinction is exactly why it is written down there.
    #[test]
    fn step_4b_caches_load_class_in_the_process_global() {
        let _g = test_guard();
        let jni = fresh_env();

        unsafe { create(jni.env, named_token()) };

        // The harness hands out `index + 1`, so this pins *which* name it resolved against
        // rather than only that something was stored.
        assert_eq!(
            fake_jni::method_name(held_load_class()),
            Some("loadClass"),
            "the cached id is the one for loadClass",
        );
    }

    /// A second call replaces the loader reference and releases the predecessor, the same
    /// replace-and-release the token slot does — and independently of it: a token the harness
    /// cannot name stops 4b *after* 4a has already run, which is the case below.
    #[test]
    fn a_second_loader_releases_the_previous_reference() {
        let _g = test_guard();
        let jni = fresh_env();

        unsafe { create(jni.env, named_token()) };
        let held_first = held_loader();
        assert!(!held_first.is_null());

        unsafe { create(jni.env, named_token()) };

        assert_ne!(held_loader(), held_first, "the slot holds the new loader");
        fake_jni::with_recorder(|r| {
            assert_eq!(r.new_global_refs, 4, "two tokens, two loaders");
            assert_eq!(r.delete_global_refs, 2, "one predecessor each, exactly once");
        });
    }

    /// The degraded path, and the reason it is not a test of step 4a: a token the harness
    /// cannot name makes `GetObjectClass` answer null, so 4b stops there — but 4a has already
    /// run, and every existing test above passes a token of exactly this kind. That is what
    /// keeps their `new_global_refs` counts meaning what they meant.
    #[test]
    fn an_unnamable_token_stops_step_4b_without_undoing_step_4a() {
        let _g = test_guard();
        let jni = fresh_env();

        unsafe { create(jni.env, fake_jni::stub_ref()) };

        assert!(!held_token().is_null(), "4a ran");
        assert!(held_loader().is_null(), "4b did not get as far as a loader");
        assert!(held_load_class().is_null());
        fake_jni::with_recorder(|r| {
            assert_eq!(r.get_object_class, 1, "it got as far as the class lookup");
            assert!(r.method_ids.is_empty(), "and no further: {:?}", r.method_ids);
            assert!(!r.pending_exception, "a null class is not a thrown one");
        });
    }

    /// A `GetMethodID` that throws is cleared before anything else runs, and the cache is
    /// left empty rather than holding an id the VM never handed out.
    #[test]
    fn a_failed_lookup_clears_its_exception_and_caches_nothing() {
        let _g = test_guard();
        let jni = fresh_env();
        fake_jni::with_recorder(|r| r.fail_get_method_id = true);

        unsafe { create(jni.env, named_token()) };

        assert!(held_load_class().is_null(), "nothing was cached");
        fake_jni::with_recorder(|r| {
            assert_eq!(r.method_ids.len(), 1, "it stopped at the first failure");
            assert!(r.exception_clear > 0, "the `NoSuchMethodError` was cleared");
            assert!(!r.pending_exception, "and is not left pending");
        });
    }

    /// A `FindClass` that throws is cleared too — and it is the one call in this path that
    /// does *not* clear its own, so the caller has to. Left pending it would poison every
    /// later JNI call in the process, including the location chain's.
    #[test]
    fn a_failed_find_class_is_cleared_and_caches_nothing() {
        let _g = test_guard();
        let jni = fresh_env();
        fake_jni::with_recorder(|r| r.missing.push("java/lang/ClassLoader".to_owned()));

        unsafe { create(jni.env, named_token()) };

        assert!(held_load_class().is_null(), "nothing was cached");
        fake_jni::with_recorder(|r| {
            // Both lookups before it ran; only the cache is missing.
            assert_eq!(r.method_ids.len(), 1, "getClassLoader, and not loadClass");
            assert!(r.exception_clear > 0, "the `NoClassDefFoundError` was cleared");
            assert!(!r.pending_exception);
        });
    }

    /// What the fake callback was handed. `-2` is a value no status can take, and `-1` a
    /// value no pointer to a live object can take, so an untouched sentinel is
    /// distinguishable from a write of zero — which is exactly the pair that "an empty
    /// `shared_ptr`" and "a null pointer" would otherwise collapse into.
    const UNTOUCHED_STATUS: i32 = -2;
    const UNTOUCHED_RESPONSE: isize = -1;
    static SEEN_STATUS: AtomicI32 = AtomicI32::new(UNTOUCHED_STATUS);
    /// The response's first word: the object itself.
    static SEEN_RESPONSE: AtomicIsize = AtomicIsize::new(UNTOUCHED_RESPONSE);
    /// The response's second word: the control block that owns it.
    static SEEN_RESPONSE_CTRL: AtomicIsize = AtomicIsize::new(UNTOUCHED_RESPONSE);
    /// The first word of that object — its vtable, and the thing a caller jumps through.
    static SEEN_SESSION_VTABLE: AtomicIsize = AtomicIsize::new(UNTOUCHED_RESPONSE);
    static SEEN_CALLS: AtomicUsize = AtomicUsize::new(0);

    /// A vtable entry `start_session` must *not* reach through.
    unsafe extern "C" fn not_the_call(_t: *const c_void, _s: *const i32, _r: *const c_void) {}

    /// The callback, shaped exactly as a libc++ `__func`'s `operator()` is: the callable
    /// itself, then the status, then the response.
    ///
    /// It reads the response the way the geofence caller does — through, without looking at
    /// the status first — because that is the read the whole change exists to make safe.
    unsafe extern "C" fn recording_call(
        _this: *const c_void,
        status: *const i32,
        response: *const c_void,
    ) {
        SEEN_CALLS.fetch_add(1, Ordering::AcqRel);
        if !status.is_null() {
            SEEN_STATUS.store(*status, Ordering::Release);
        }
        if !response.is_null() {
            let pair = response.cast::<*const c_void>();
            let object = *pair;
            SEEN_RESPONSE.store(object as isize, Ordering::Release);
            SEEN_RESPONSE_CTRL.store(*pair.add(1) as isize, Ordering::Release);
            if !object.is_null() {
                SEEN_SESSION_VTABLE.store(
                    *object.cast::<*const c_void>() as isize,
                    Ordering::Release,
                );
            }
        }
    }

    /// Builds the three levels of a libc++ `std::function` around `operator`, in caller-owned
    /// storage, and returns the pointer a slot takes as `std::function const&`:
    ///
    /// ```text
    /// function  +0x20 ->  callable  [0] ->  slots  +0x30 ->  operator()
    /// ```
    ///
    /// Built by hand rather than borrowed from libc++ because what is under test *is* that
    /// chain — the callable pointer at `+0x20`, then its vtable's `+0x30`. A real
    /// `std::function` here would be testing libc++ instead. The middle level is the one that
    /// is easy to drop: `__f_` points at the `__func`, whose *own* first word is its vtable,
    /// so collapsing the two turns `+0x30` into an offset from a function's entry point.
    ///
    /// The buffers belong to the caller because each level holds a pointer into the one
    /// before it, so none of the three may move. Every slot but `operator()` is
    /// [`not_the_call`], so an off-by-one lands on a call that does not count rather than on
    /// one that jumps anywhere.
    fn fake_function(
        slots: &mut [*const c_void; 8],
        callable: &mut [*const c_void; 1],
        function: &mut [*const c_void; 5],
        operator: unsafe extern "C" fn(*const c_void, *const i32, *const c_void),
    ) -> *const c_void {
        slots[0x30 / size_of::<*const c_void>()] = operator as *const c_void;
        callable[0] = slots.as_ptr() as *const c_void;
        function[0x20 / size_of::<*const c_void>()] = callable.as_ptr() as *const c_void;
        function.as_ptr().cast()
    }

    /// `+0x10` must leave the caller's `future` with a value, and the only way it can is by
    /// invoking the completion callback itself.
    #[test]
    fn the_0x10_slot_reports_success() {
        let _g = test_guard();
        let ctx = unsafe { create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext };
        let mut slots = [not_the_call as *const c_void; 8];
        let mut callable = [ptr::null::<c_void>(); 1];
        let mut function = [ptr::null::<c_void>(); 5];
        let callback = fake_function(&mut slots, &mut callable, &mut function, recording_call);

        SEEN_CALLS.store(0, Ordering::Release);
        SEEN_STATUS.store(UNTOUCHED_STATUS, Ordering::Release);

        unsafe { (VTABLE.slot_10)(ctx, ptr::null(), callback) };

        assert_eq!(SEEN_CALLS.load(Ordering::Acquire), 1, "invoked exactly once");
        assert_eq!(
            SEEN_STATUS.load(Ordering::Acquire),
            0,
            "upstream hard-codes zero before its only call site and has no failure branch"
        );
    }

    /// The other half of the contract: `StandaloneInit` keeps the response and drives it
    /// later, and `sendSoftSfidaLocationUpdate` jumps through its `+0x38` without reading the
    /// status first. Both need the object to exist and to have a real vtable in its first word.
    #[test]
    fn the_0x10_slot_hands_back_a_live_session() {
        let _g = test_guard();
        let ctx = unsafe { create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext };
        let mut slots = [not_the_call as *const c_void; 8];
        let mut callable = [ptr::null::<c_void>(); 1];
        let mut function = [ptr::null::<c_void>(); 5];
        let callback = fake_function(&mut slots, &mut callable, &mut function, recording_call);

        SEEN_RESPONSE.store(UNTOUCHED_RESPONSE, Ordering::Release);
        SEEN_RESPONSE_CTRL.store(UNTOUCHED_RESPONSE, Ordering::Release);
        SEEN_SESSION_VTABLE.store(UNTOUCHED_RESPONSE, Ordering::Release);

        unsafe { (VTABLE.slot_10)(ctx, ptr::null(), callback) };

        let object = SEEN_RESPONSE.load(Ordering::Acquire);
        assert_ne!(object, UNTOUCHED_RESPONSE, "the response was never read");
        assert_ne!(
            object, 0,
            "an empty shared_ptr is the null dereference this change exists to avoid"
        );
        assert_ne!(
            SEEN_RESPONSE_CTRL.load(Ordering::Acquire),
            0,
            "the object needs an owner, or a caller's copy has nothing to release"
        );

        let vtable = SEEN_SESSION_VTABLE.load(Ordering::Acquire) as *const SessionVtable;
        assert!(
            core::ptr::eq(vtable, &SESSION_VTABLE),
            "the object's first word is upstream's ten-slot layout, not a guess"
        );
    }

    /// `+0x10`'s dump runs over the caller's arguments and leaves them alone.
    ///
    /// What is assertable from here is bounded: the dump goes to logcat on device and to
    /// stderr — which libtest captures per test — on the host, so a test cannot read it back.
    /// These are the two things that are assertable, and they are the ones that matter: a slot
    /// called with real, well-formed arguments still does exactly what the tests above say it
    /// does, and nothing in the dump wrote into the caller's objects. The third failure mode —
    /// a fault in the reader — arrives as the test binary dying, which is the one this exists
    /// for: every read the dump makes is of memory that belongs to the caller, not to us.
    #[test]
    fn the_0x10_dump_reads_the_callers_arguments_without_altering_them() {
        let _g = test_guard();
        // A `SessionParams` carrying what [`SESSION_PARAMS`] says is in one: a pair, a long
        // string whose characters are on the heap, and the two short ones.
        let heap = "sfida_for_playground".as_bytes().iter().copied().chain([0]).collect::<Vec<u8>>();
        let mut params = [0u8; SESSION_PARAMS_SIZE];
        put_pair(&mut params, 0x00, 0x1000, 0x0fe8);
        put_long(&mut params, 0x10, &heap);
        put_short(&mut params, 0x30, "Pokémon GO");
        put_short(&mut params, 0x48, "0.429.2");
        put_pair(&mut params, 0x60, 0x2000, 0x1000);
        put_pair(&mut params, 0x70, 0x3000, 0x2fe8);
        let decoded = crate::inspect::string(params.as_ptr() as usize + 0x30)
            .map(|s| s.text)
            .unwrap_or_else(|why| format!("<{why}>"));
        assert_eq!(
            decoded, "Pokémon GO",
            "the fixture is what the dump's reader decodes, or the test proves nothing"
        );
        let params_before = params;

        let mut slots = [not_the_call as *const c_void; 8];
        let mut callable = [ptr::null::<c_void>(); 1];
        let mut function = [ptr::null::<c_void>(); 5];
        let callback = fake_function(&mut slots, &mut callable, &mut function, recording_call);
        let function_before = function;

        SEEN_CALLS.store(0, Ordering::Release);
        SEEN_STATUS.store(UNTOUCHED_STATUS, Ordering::Release);

        // A null context: the slot only prints it, and the object under test is not what this
        // is about.
        unsafe { (VTABLE.slot_10)(ptr::null_mut(), params.as_ptr().cast(), callback) };

        assert_eq!(SEEN_CALLS.load(Ordering::Acquire), 1, "still invoked exactly once");
        assert_eq!(SEEN_STATUS.load(Ordering::Acquire), 0, "still status zero");
        assert_eq!(params, params_before, "the dump wrote into the caller's params");
        assert_eq!(
            function, function_before,
            "the dump wrote into the caller's `std::function`"
        );
    }

    /// The dumps reach past the size the layout covers, which is what lets them falsify it.
    ///
    /// A window that had shrunk back to a struct size would fail silently: every field the table
    /// names would still be printed, and the block would still be well formed. What it could no
    /// longer do is show a byte the caller wrote past the last field — which is where a field
    /// the table is missing would be, and so the reason [`DUMP_WINDOW`] is larger than any
    /// [`Shape`]. The reach is asserted from the block's own first row rather than from the
    /// constant: the block starts where the fields end, and its last row is the window's.
    #[test]
    fn the_argument_dumps_run_past_the_size_the_table_covers() {
        let mut params = [0u8; DUMP_WINDOW];
        let marker = params.len() - 1;
        params[marker] = 0xab;
        for shape in [&SESSION_PARAMS, &AUTH_PARAMS, &STDFUNCTION] {
            assert!(
                DUMP_WINDOW > shape.size,
                "a window no larger than the table can only confirm it"
            );
            let block = crate::inspect::hex(
                params.as_ptr() as usize + shape.size,
                DUMP_WINDOW - shape.size,
            );
            let last = block.lines().last().expect("a window is rows, not nothing");
            assert!(last.contains("ab"), "the byte past the fields is in the block: {last}");
        }
        let tail = crate::inspect::hex(params.as_ptr() as usize + SESSION_PARAMS_SIZE, 0x10);
        assert!(tail.starts_with("+0x0000"), "the block counts from the fields' end: {tail}");
    }

    /// A libc++ short string written into `buf` at `offset`: the size and the flag in the first
    /// byte, the characters from `+1`.
    fn put_short(buf: &mut [u8], offset: usize, text: &str) {
        assert!(text.len() <= 22, "not a short string: {text}");
        buf[offset] = (text.len() as u8) << 1;
        buf[offset + 1..=offset + text.len()].copy_from_slice(text.as_bytes());
    }

    /// A libc++ long string written into `buf` at `offset`: the capacity with the flag in its
    /// low bit, the size at `+8`, the characters at `+16`.
    ///
    /// `chars` is the buffer the string points at and must outlive every read of `buf` — the
    /// terminator is the last byte of it, because that is where libc++ puts one.
    fn put_long(buf: &mut [u8], offset: usize, chars: &[u8]) {
        let size = chars.len() - 1;
        buf[offset..offset + 8].copy_from_slice(&((chars.len() | 1) as u64).to_le_bytes());
        buf[offset + 8..offset + 16].copy_from_slice(&(size as u64).to_le_bytes());
        buf[offset + 16..offset + 24]
            .copy_from_slice(&(chars.as_ptr() as usize as u64).to_le_bytes());
    }

    /// Two words into `buf` at `offset`.
    fn put_pair(buf: &mut [u8], offset: usize, ptr: usize, ctrl: usize) {
        buf[offset..offset + 8].copy_from_slice(&(ptr as u64).to_le_bytes());
        buf[offset + 8..offset + 16].copy_from_slice(&(ctrl as u64).to_le_bytes());
    }

    /// The layout each table claims is the layout the dump prints, checked against the object's
    /// own size rather than against itself.
    ///
    /// The property is that the fields **tile** the object: each one starts where the last ended
    /// and the last ends at the size the caller allocates. A table that is one field short
    /// leaves a hole — bytes in the object that the dump would print as the caller's frame — and
    /// one that is a field long runs past the end, where the next allocation's header is. Both
    /// read as a plausible dump, which is why this is asserted rather than reviewed.
    #[test]
    fn every_table_tiles_its_object_exactly() {
        for (name, shape) in [
            ("params", &SESSION_PARAMS),
            ("auth", &AUTH_PARAMS),
            ("std::function", &STDFUNCTION),
        ] {
            let mut end = 0;
            for field in shape.fields {
                assert_eq!(
                    field.offset, end,
                    "{name}: +{:#04x} is not where the field below it ended at {end:#x}",
                    field.offset
                );
                end += field.kind.width();
            }
            assert_eq!(end, shape.size, "{name}: the fields and the size disagree");
        }
    }

    /// The dump's lines are the reading of every field the table names.
    ///
    /// This is the half of a dump a host test can hold: a caller cannot read logcat back, but
    /// these lines are a string, and what they have to say is exactly what the two probes the
    /// dumps replaced could not — the 31-character string at `+0x10`, the version at `+0x48`,
    /// and, when a field refuses to decode, which check refused it and on what bytes.
    #[test]
    fn the_params_dump_reads_every_field_the_table_names() {
        let chars = "sfida_for_playground".as_bytes().iter().copied().chain([0]).collect::<Vec<u8>>();
        // A pointee whose first word points at itself, so the pair's own line has something to
        // name. Read back rather than trusted: the address as an integer says nothing to the
        // optimiser, and a store it can prove is never read is one it deletes.
        let mut pointee: [usize; 2] = [0, 0];
        let pointee_at = pointee.as_ptr() as usize;
        pointee[0] = pointee_at;
        assert_eq!(pointee[0], pointee_at, "the fixture reads back");

        let mut params = [0u8; SESSION_PARAMS_SIZE];
        put_pair(&mut params, 0x00, pointee_at, pointee_at - 0x18);
        put_long(&mut params, 0x10, &chars);
        put_short(&mut params, 0x30, "Pokémon GO");
        put_short(&mut params, 0x48, "0.429.2");
        put_pair(&mut params, 0x60, 0x2000, 0x1000);
        put_pair(&mut params, 0x70, pointee_at, 0);

        let dump = render_fields(params.as_ptr() as usize, &SESSION_PARAMS);
        for expected in [
            "+0x0000 shared_ptr",
            "one allocation (obj = ctrl + 0x18)",
            "first word -> ",
            r#"+0x0010 string(20, cap 20) "sfida_for_playground" at 0x"#,
            "+0x0028 0x0", // the word the layout has no type for: shown as a value
            r#"+0x0030 string(11) "Pokémon GO""#,
            r#"+0x0048 string(7) "0.429.2""#,
            "+0x0060 shared_ptr {0x2000, 0x1000}, object and control block are separate",
            "+0x0070 shared_ptr",
            ", no control block",
        ] {
            assert!(dump.contains(expected), "{expected:?} is missing from:\n{dump}");
        }

        // A field whose bytes are not a string says so, with the reason *and* the bytes — which
        // is the difference between "this offset in the table is wrong" and "this value is not
        // text", two different repairs. Written by hand rather than through `put_short`,
        // because what a wrong offset produces is a header claiming a size the form cannot hold.
        let too_long = b"not a version string at all";
        params[0x48] = (too_long.len() as u8) << 1;
        params[0x49..0x49 + too_long.len()].copy_from_slice(too_long);
        let refused = render_fields(params.as_ptr() as usize, &SESSION_PARAMS);
        assert!(
            refused.contains("+0x0048 <not a string: size 27 over the 22 limit>  "),
            "the reason, then the bytes it was read from: {refused}"
        );
        assert!(
            refused.contains("|6not a version s|"),
            "the bytes themselves are in the line: {refused}"
        );
    }


    /// The derived tables against the objects a real capture holds, byte for byte.
    ///
    /// The fixtures above are built *to* the tables, so they can only confirm them: a field at
    /// the wrong offset is written to the wrong offset and read back from it, and the test
    /// passes on a table that disagrees with the game. This one starts from bytes the game
    /// wrote — the params object of the 09-23 15:28:13 capture and the `+0x38` auth object of
    /// 15:28:14, transcribed from logcat — so an offset either table has wrong shows up as a
    /// field that decodes to something the game could not have meant.
    ///
    /// Two edits, and only two: the out-of-line pointers — the long string's characters and the
    /// auth token — are repointed at host buffers, because those bytes are on the game's heap
    /// and the capture does not carry them. Their *headers* are the device's, which is where the
    /// claim lives: the size, the capacity, and which offset the pointer sits at. Every short
    /// string and every pair here is the device's own byte.
    ///
    /// What this cannot show is the `first word ->` fragment on the pair lines: that reads the
    /// pointee, and the pointee is the game's heap. On the device the same bytes name the
    /// module the vtable belongs to.
    #[test]
    fn the_captured_objects_decode_field_by_field() {
        // `[bgcontext]   +0x0000  c8 95 09 51 79 00 00 00  b0 95 09 51 79 00 00 00` … through
        // the `+0x0070` row.
        let mut params = [
            0xc8, 0x95, 0x09, 0x51, 0x79, 0x00, 0x00, 0x00, 0xb0, 0x95, 0x09, 0x51, 0x79, 0x00,
            0x00, 0x00, 0x31, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1f, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x50, 0x65, 0x0f, 0xe1, 0x77, 0x00, 0x00, 0x00, 0x96, 0xa7,
            0x00, 0x00, 0x78, 0x00, 0x00, 0x00, 0x16, 0x50, 0x6f, 0x6b, 0xc3, 0xa9, 0x6d, 0x6f,
            0x6e, 0x20, 0x47, 0x4f, 0x00, 0x00, 0x00, 0x00, 0x78, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x0e, 0x30, 0x2e, 0x34, 0x32, 0x39, 0x2e, 0x32, 0x00, 0x73, 0xfe, 0xe9,
            0x7f, 0x00, 0x00, 0x00, 0x30, 0x2d, 0x07, 0x81, 0x77, 0x00, 0x00, 0x00, 0x60, 0xdd,
            0x0c, 0x91, 0x78, 0x00, 0x00, 0x00, 0x00, 0x64, 0x0d, 0x11, 0x78, 0x00, 0x00, 0x00,
            0xe8, 0x59, 0x08, 0xa1, 0x77, 0x00, 0x00, 0x00, 0xd0, 0x59, 0x08, 0xa1, 0x77, 0x00,
            0x00, 0x00,
        ];
        assert_eq!(params.len(), SESSION_PARAMS_SIZE, "the capture's object, whole");
        // The string the header at `+0x10` points at: 31 bytes and a terminator, the size the
        // device's `+0x18` word gives.
        let session = "s".repeat(31);
        let mut session_chars = session.as_bytes().to_vec();
        session_chars.push(0);
        let chars_at = session_chars.as_ptr() as usize;
        params[0x20..0x28].copy_from_slice(&((chars_at as u64).to_le_bytes()));

        let dump = render_fields(params.as_ptr() as usize, &SESSION_PARAMS);
        let expected = [
            // `obj == ctrl + 0x18`, which is what `make_shared` builds and what all nine pairs
            // in this capture have — read off the device's own two words.
            "+0x0000 shared_ptr {0x79510995c8, 0x79510995b0}, one allocation (obj = ctrl + 0x18)"
                .to_string(),
            format!("+0x0010 string(31, cap 48) \"{session}\" at 0x{chars_at:x}"),
            // A word the layout has a place for and no type: shown as its value.
            "+0x0028 0x780000a796".to_string(),
            r#"+0x0030 string(11) "Pokémon GO""#.to_string(),
            r#"+0x0048 string(7) "0.429.2""#.to_string(),
            // `0x78910cdd60` is not `0x78110d6400 + 0x18`, so this pair is not one allocation —
            // the two-argument form, which the dump says rather than assumes.
            "+0x0060 shared_ptr {0x78910cdd60, 0x78110d6400}, object and control block are \
             separate allocations"
                .to_string(),
            "+0x0070 shared_ptr {0x77a10859e8, 0x77a10859d0}, one allocation (obj = ctrl + 0x18)"
                .to_string(),
        ];
        for line in &expected {
            assert!(dump.contains(line.as_str()), "{line:?} is missing from:\n{dump}");
        }
        // The reading of the *field*, so the two failures this replaced cannot come back. The
        // version at `+0x48` was a byte read (`0x0e`) where the field is a seven-byte string,
        // and `+0x50` — which is inside that string's 24 bytes, its size and data words — was
        // printed as a pair. Nothing here has a reason to refuse.
        assert!(!dump.contains("<not a string"), "a real field was refused:\n{dump}");
        assert!(!dump.contains("+0x0050"), "the table invented a field:\n{dump}");

        // `[bgcontext] +0x38 *auth (0x30) @0x78110d7628` and its three rows: the kind, then a
        // long-form token whose characters are again the game's.
        let mut auth = [
            0x14, 0x62, 0x61, 0x63, 0x6b, 0x67, 0x72, 0x6f, 0x75, 0x6e, 0x64, 0x00, 0x00, 0x00,
            0x00, 0x00, 0xd0, 0xb3, 0xff, 0x1b, 0x76, 0x00, 0x00, 0x00, 0xa1, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x9c, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0xe2,
            0x12, 0x51, 0x79, 0x00, 0x00, 0x00,
        ];
        assert_eq!(auth.len(), AUTH_PARAMS.size, "the capture's auth, whole");
        // 156 characters under a capacity of 160: `0xa1` is `160 | __is_long_`.
        let token = "t".repeat(156);
        let mut token_chars = token.as_bytes().to_vec();
        token_chars.push(0);
        let token_at = token_chars.as_ptr() as usize;
        auth[0x28..0x30].copy_from_slice(&((token_at as u64).to_le_bytes()));

        let dump = render_fields(auth.as_ptr() as usize, &AUTH_PARAMS);
        for line in [
            r#"+0x0000 string(10) "background""#.to_string(),
            format!("+0x0018 string(156, cap 160) \"{token}\" at 0x{token_at:x}"),
        ] {
            assert!(dump.contains(line.as_str()), "{line:?} is missing from:\n{dump}");
        }
    }

    /// The `0xe0` the original allocates, with the two member pairs exactly where upstream's
    /// getters read them. These offsets are the ABI rather than an implementation detail:
    /// `sub_674a78` reads the pair at `{+0x28, +0x30}` and `sub_6695cc` the one at
    /// `{+0x18, +0x20}`, so a field inserted above either hands a caller a pointer out of
    /// whatever landed there instead — the same unread-buffer crash this file now guards
    /// against, arrived at from the other side.
    #[test]
    fn session_is_the_size_the_original_allocates_with_its_members_where_they_are_read() {
        assert_eq!(size_of::<Session>(), 0xe0, "upstream's `operator new` argument");
        assert_eq!(offset_of!(Session, vtable), 0x00);
        assert_eq!(offset_of!(Session, member_18), 0x18);
        assert_eq!(offset_of!(Session, member_28), 0x28);
        assert_eq!(
            offset_of!(Session, member_28) + size_of::<*const c_void>(),
            0x30,
            "the tail of that pair is the word the getter's `__stxr` bumps"
        );
        assert_eq!(offset_of!(Session, _unmodelled), 0x38);
    }

    /// A session holds *two* members, each its own object with its own block.
    ///
    /// One object in both fields would satisfy every call site in the dumps — the two getters
    /// are independent and `BGCoreUpdateBackgroundToken` only ever takes `+0x28`'s — so the
    /// mistake would be invisible from outside and unexplained at the next caller that reached
    /// the other. It is asserted here rather than left to the reader of `session_new`.
    #[test]
    fn a_new_session_holds_two_distinct_members() {
        let _g = test_guard();
        let ctrl = unsafe { session_new() };
        assert!(!ctrl.is_null(), "the session allocated");
        let session = unsafe { (*ctrl).payload } as *mut Session;

        let one = unsafe { (*session).member_18.ptr };
        let two = unsafe { (*session).member_28.ptr };
        let one_ctrl = unsafe { (*session).member_18.ctrl };
        let two_ctrl = unsafe { (*session).member_28.ctrl };

        assert!(!one.is_null() && !two.is_null(), "a getter must have something to hand out");
        assert_ne!(one, two, "two members, not one object stored twice");
        assert_eq!(
            unsafe { (*one).vtable },
            &SESSION_MEMBER_VTABLE as *const _,
            "the first word is the table whose +0x10 is asked for a token"
        );
        assert_eq!(unsafe { (*two).vtable }, &SESSION_MEMBER_VTABLE as *const _);
        assert!(
            core::ptr::eq(unsafe { (*one_ctrl).vtable }, &MEMBER_CONTROL_VTABLE),
            "the block that frees a member is the member's own table, not the session's"
        );
        assert!(core::ptr::eq(unsafe { (*two_ctrl).vtable }, &MEMBER_CONTROL_VTABLE));
        assert_eq!(
            unsafe { (*one_ctrl).payload },
            one as *mut c_void,
            "each block owns the member it came with"
        );
        assert_eq!(unsafe { (*two_ctrl).payload }, two as *mut c_void);
        // Zero owners is libc++'s *one* owner: the session's, because nothing has copied a
        // pair yet. A block that started at one would be freed by the session's own release
        // and leave the pairs it handed out dangling.
        assert_eq!(unsafe { (*one_ctrl).shared_owners.load(Ordering::Acquire) }, 0);
        assert_eq!(unsafe { (*two_ctrl).shared_owners.load(Ordering::Acquire) }, 0);

        unsafe { control_release(ctrl) };
    }

    /// The two getters, asserted on the *buffer* — which is where the crash was.
    ///
    /// Upstream's `sub_674a78` and `sub_6695cc` are pure reads: copy the member pair into the
    /// caller's `x8` buffer, bump its owner, done. A slot that returns through `x8` and leaves
    /// the buffer alone is not an inert slot — the caller reads its own stack as a
    /// `shared_ptr` and jumps through the first word, which is exactly what `libadventuresync`
    /// did at `0x35b9f0`. So both words are asserted, over a non-null sentinel: a getter that
    /// wrote one word of two must fail this test rather than a caller.
    #[test]
    fn the_two_getters_write_an_owning_pair_into_the_callers_buffer() {
        let _g = test_guard();
        let ctrl = unsafe { session_new() };
        let session = unsafe { (*ctrl).payload } as *mut Session;

        let sentinel = 0x10 as *mut c_void;
        let mut from_20: SharedPtr<SessionMember> = SharedPtr {
            ptr: sentinel.cast(),
            ctrl: sentinel.cast(),
        };
        let mut from_28: SharedPtr<SessionMember> = SharedPtr {
            ptr: sentinel.cast(),
            ctrl: sentinel.cast(),
        };

        unsafe { (SESSION_VTABLE.slot_20)(session, &mut from_20) };
        unsafe { (SESSION_VTABLE.slot_28)(session, &mut from_28) };

        assert_eq!(
            from_20.ptr,
            unsafe { (*session).member_28.ptr },
            "`+0x20` is upstream's getter over `{{+0x28, +0x30}}`"
        );
        assert_eq!(from_20.ctrl, unsafe { (*session).member_28.ctrl });
        assert_eq!(
            from_28.ptr,
            unsafe { (*session).member_18.ptr },
            "`+0x28` is upstream's getter over `{{+0x18, +0x20}}`"
        );
        assert_eq!(from_28.ctrl, unsafe { (*session).member_18.ctrl });
        assert_ne!(from_20.ptr, from_28.ptr, "two getters, not the same one twice");

        // The reference the caller now holds is real: one owner each, on top of the session's
        // own, because upstream's getters increment on the block rather than on the pair.
        assert_eq!(unsafe { (*from_20.ctrl).shared_owners.load(Ordering::Acquire) }, 1);
        assert_eq!(unsafe { (*from_28.ctrl).shared_owners.load(Ordering::Acquire) }, 1);

        // Handed straight back, they leave the session's reference as the only one — so the
        // release below is the one that frees both members and the session together, which is
        // the ownership the whole arrangement exists to produce.
        unsafe { control_release(from_20.ctrl) };
        unsafe { control_release(from_28.ctrl) };
        assert_eq!(
            unsafe { (*from_20.ctrl).shared_owners.load(Ordering::Acquire) },
            0,
            "the session's own reference, and nothing else"
        );
        assert_eq!(unsafe { (*from_28.ctrl).shared_owners.load(Ordering::Acquire) }, 0);
        unsafe { control_release(ctrl) };
    }

    /// The member's `+0x10` — the one slot of the member any dump reaches — and the answer it
    /// gives: an **empty** pair.
    ///
    /// Empty is a decision rather than a stub accident, and the evidence for it is the caller:
    /// `BGCoreUpdateBackgroundToken` caches the pair at `[this+0x128]`, null-checks it (`cbz`
    /// at `0x35b960`) and releases it, and never dereferences it anywhere. So empty is a state
    /// the game already handles and re-requests. What the test pins is that the buffer is
    /// written at all: a slot returning through `x8` that writes nothing is the crash this
    /// change is fixing, and it must not be able to come back through this slot.
    #[test]
    fn the_token_request_is_answered_with_an_empty_pair() {
        let _g = test_guard();
        let ctrl = unsafe { session_new() };
        let session = unsafe { (*ctrl).payload } as *mut Session;
        let member = unsafe { (*session).member_28.ptr };

        let sentinel = 0x10 as *mut c_void;
        let mut out: SharedPtr<c_void> = SharedPtr {
            ptr: sentinel,
            ctrl: sentinel.cast(),
        };

        unsafe { (SESSION_MEMBER_VTABLE.slot_10)(member, ptr::null(), &mut out) };

        assert!(out.ptr.is_null(), "an empty pair, not the sentinel left in place");
        assert!(out.ctrl.is_null(), "and with no owner to release");
        unsafe { control_release(ctrl) };
    }

    /// `+0x38` is the slot `sendSoftSfidaLocationUpdate`'s callback jumps through, and the
    /// only one whose signature is proven rather than inferred. Upstream's already-started
    /// path answers with status `1` and an empty response; the stub has no session to start,
    /// so it takes that path deliberately.
    ///
    /// Safe rather than merely faithful: that caller's `operator()` (`0xf7f288`) opens with
    /// `if (!*arg2)`, so a response it must not read is a response it does not read.
    #[test]
    fn the_0x38_slot_reports_no_session() {
        let _g = test_guard();
        let session = unsafe { session_new() };
        let object = unsafe { (*session).payload } as *mut Session;
        let mut slots = [not_the_call as *const c_void; 8];
        let mut callable = [ptr::null::<c_void>(); 1];
        let mut function = [ptr::null::<c_void>(); 5];
        let callback = fake_function(&mut slots, &mut callable, &mut function, recording_call);

        SEEN_CALLS.store(0, Ordering::Release);
        SEEN_STATUS.store(UNTOUCHED_STATUS, Ordering::Release);

        unsafe { (SESSION_VTABLE.slot_38)(object, ptr::null(), callback) };

        assert_eq!(SEEN_CALLS.load(Ordering::Acquire), 1, "invoked exactly once");
        assert_eq!(
            SEEN_STATUS.load(Ordering::Acquire),
            1,
            "upstream's own answer for a session that is not running"
        );
        unsafe { control_release(session) };
    }

    /// The `+0x10` signature, exercised rather than merely declared: a `shared_ptr` and a
    /// `std::function` arrive by `const` reference, so they are two pointers, and the slot
    /// has to tolerate both being null because a caller that has neither still calls it.
    ///
    /// An **empty** `std::function` is the second half and the more likely one: upstream
    /// reaches `std::__throw_bad_function_call` there, and a throw crossing this boundary
    /// would be an abort rather than a catch, so the slot checks instead.
    #[test]
    fn the_0x10_slot_tolerates_a_missing_callback() {
        let _g = test_guard();
        let ctx = unsafe { create(ptr::null_mut(), ptr::null_mut()) as *mut BackgroundContext };
        let empty = [ptr::null::<c_void>(); 0x28 / size_of::<*const c_void>()];

        SEEN_CALLS.store(0, Ordering::Release);
        unsafe { (VTABLE.slot_10)(ctx, ptr::null(), ptr::null()) };
        unsafe { (VTABLE.slot_10)(ctx, ptr::null(), empty.as_ptr().cast()) };
        assert_eq!(SEEN_CALLS.load(Ordering::Acquire), 0);
    }

    /// The property the whole table exists to provide, and the one an unexercised slot cannot
    /// promise: every entry is non-null, and calling any of them does not fault.
    ///
    /// The count is derived from `SessionVtable` rather than written out, so the loop cannot
    /// disagree with the type — and the walk is over the type's own words, so a slot that
    /// were left out of the initialiser would read as a hole here rather than as a jump into
    /// whatever the linker put next.
    ///
    /// The *length* is upstream's and comes from the live dump: `data_b91088` holds ten slots
    /// and the word after them is `1`, not a pointer into the plugin's executable range.
    #[test]
    fn every_session_slot_is_callable() {
        let _g = test_guard();
        let ctrl = unsafe { session_new() };
        assert!(!ctrl.is_null(), "the session allocated");
        let object = unsafe { (*ctrl).payload } as *mut Session;

        let words = &SESSION_VTABLE as *const SessionVtable as *const usize;
        let slots = size_of::<SessionVtable>() / size_of::<*const c_void>();
        for slot in 0..slots {
            assert_ne!(unsafe { *words.add(slot) }, 0, "slot {:#04x} is null", slot * 8);
        }

        // The two getters take a reference each, so they get a buffer each: one shared buffer
        // would overwrite the first pair and leak the reference it held.
        let mut from_20 = SharedPtr {
            ptr: ptr::null_mut(),
            ctrl: ptr::null_mut(),
        };
        let mut from_28 = SharedPtr {
            ptr: ptr::null_mut(),
            ctrl: ptr::null_mut(),
        };

        unsafe {
            (SESSION_VTABLE.slot_00)(object);
            (SESSION_VTABLE.slot_08)(object);
            (SESSION_VTABLE.slot_10)(object);
            (SESSION_VTABLE.slot_18)(object);
            (SESSION_VTABLE.slot_20)(object, &mut from_20);
            (SESSION_VTABLE.slot_28)(object, &mut from_28);
            (SESSION_VTABLE.slot_30)(object);
            (SESSION_VTABLE.slot_38)(object, ptr::null(), ptr::null());
            (SESSION_VTABLE.slot_40)(object);
            (SESSION_VTABLE.slot_48)(object);

            // Handed straight back, which is what leaves the session's own pair as the only
            // reference — and so makes the release below the one that frees them.
            control_release(from_20.ctrl);
            control_release(from_28.ctrl);
        }

        assert!(
            core::ptr::eq(unsafe { (*object).vtable }, &SESSION_VTABLE),
            "the vtable is the object's first word, which is what a caller reads"
        );
        assert_eq!(
            unsafe { (*ctrl).shared_owners.load(Ordering::Acquire) },
            0,
            "nothing above took a reference, so the stub still owns the only one"
        );
        unsafe { control_release(ctrl) };
    }

    /// The member's table, walked for the same reason the session's is.
    ///
    /// This is the table the game reaches *second*: `BGCoreUpdateBackgroundToken` collects a
    /// member through the session's `+0x20` and calls `member->vtbl[+0x10]`, so an index the
    /// stub never exercised is exactly where the next crash is waiting. The count comes from
    /// the type rather than from the initialiser, so a slot left out reads as a hole here
    /// rather than as a jump into whatever the linker put next.
    #[test]
    fn every_member_slot_is_callable() {
        let _g = test_guard();
        let ctrl = unsafe { session_new() };
        let session = unsafe { (*ctrl).payload } as *mut Session;
        let member = unsafe { (*session).member_28.ptr };

        let words = &SESSION_MEMBER_VTABLE as *const SessionMemberVtable as *const usize;
        let slots = size_of::<SessionMemberVtable>() / size_of::<*const c_void>();
        for slot in 0..slots {
            assert_ne!(unsafe { *words.add(slot) }, 0, "slot {:#04x} is null", slot * 8);
        }

        let mut out: SharedPtr<c_void> = SharedPtr {
            ptr: ptr::null_mut(),
            ctrl: ptr::null_mut(),
        };
        unsafe {
            (SESSION_MEMBER_VTABLE.slot_00)(member);
            (SESSION_MEMBER_VTABLE.slot_08)(member);
            (SESSION_MEMBER_VTABLE.slot_10)(member, ptr::null(), &mut out);
            (SESSION_MEMBER_VTABLE.slot_18)(member);
            (SESSION_MEMBER_VTABLE.slot_20)(member);
            (SESSION_MEMBER_VTABLE.slot_28)(member);
            (SESSION_MEMBER_VTABLE.slot_30)(member);
            (SESSION_MEMBER_VTABLE.slot_38)(member);
            (SESSION_MEMBER_VTABLE.slot_40)(member);
            (SESSION_MEMBER_VTABLE.slot_48)(member);
        }
        assert!(out.ptr.is_null() && out.ctrl.is_null(), "the token slot answered, and emptily");

        assert!(
            core::ptr::eq(unsafe { (*member).vtable }, &SESSION_MEMBER_VTABLE),
            "the member is driven through the table in its first word"
        );
        assert_eq!(
            unsafe { (*ctrl).shared_owners.load(Ordering::Acquire) },
            0,
            "no slot above took a reference to the session, so its release frees everything"
        );
        unsafe { control_release(ctrl) };
    }
}

