//! The plugin context handed out as an opaque pointer by `N2PluginCreateContext`.
//!
//! Java calls natives under `ContextService.callbackLock`, but that is a *Java* monitor
//! and does not serialise against Unity's main thread driving `N2PluginOnUpdate`. All
//! mutable state therefore lives behind a `Mutex` rather than assuming single-threaded
//! access.

use crate::abi::{ApiError, CallCallbackDelegate, CompassHeadingData, EventSlot, FreeHandleDelegate, Handle, LocationData, PluginState};
use std::collections::HashSet;
use std::ffi::CString;
use std::sync::{Arc, Mutex, MutexGuard};

/// Callback registration captured by `N2PluginSetCallbacks`.
#[derive(Clone, Copy, Default)]
pub struct Callbacks {
    pub call: Option<CallCallbackDelegate>,
    pub free: Option<FreeHandleDelegate>,
    /// Six GCHandle values, stored verbatim. Never dereference, never free.
    pub events: [*mut core::ffi::c_void; 6],
}

pub struct State {
    pub plugin_state: PluginState,
    pub product_name: Option<String>,
    pub product_version: Option<String>,
    pub user_agent: Option<String>,
    pub background_mode: bool,
    pub defer_location_permission: bool,
    pub http3_enabled: bool,
    pub snitch_port: Option<u16>,
    pub callbacks: Callbacks,
    pub next_rpc_id: i32,

    /// What the game passed to `N2ServerSetServer`. Logged, never routed to: the stub talks
    /// to the dev server instead, and keeping the value makes the difference visible.
    pub server_host: Option<String>,

    /// Ticket from `POST /get_auth_token`, sent back as `Authorization` on every `/rpc`.
    pub auth_ticket: Option<String>,

    /// Frame counter behind the periodic `OnUpdate` liveness line.
    pub update_count: u32,

    /// Call-site labels that have already logged themselves. See [`State::log_once`].
    pub seen: HashSet<&'static str>,

    /// Last value pushed by the debug setters; read back by the `N2Map*` getters.
    pub debug_location: Option<LocationData>,
    pub debug_heading: Option<CompassHeadingData>,

    /// Interned device-info strings. `out IntPtr` results are owned by native and never
    /// freed by managed, so these must be stable for the context's lifetime — never
    /// hand back a pointer to a temporary.
    pub country_code: Option<CString>,
    pub language_code: Option<CString>,
    pub timezone: Option<CString>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            plugin_state: PluginState::Undefined,
            product_name: None,
            product_version: None,
            user_agent: None,
            background_mode: false,
            defer_location_permission: false,
            http3_enabled: false,
            snitch_port: None,
            callbacks: Callbacks::default(),
            next_rpc_id: 1,
            server_host: None,
            auth_ticket: None,
            update_count: 0,
            seen: HashSet::new(),
            debug_location: None,
            debug_heading: None,
            country_code: None,
            language_code: None,
            timezone: None,
        }
    }
}

impl State {
    /// Log `label` the first time this context reaches a given call site, and stay silent
    /// afterwards.
    ///
    /// This exists for the on-device smoke test ([notes/10-verification.md]): "how far did
    /// the game get" is the whole success criterion, and an uninstrumented stub answers it
    /// only with the absence of a crash. Gating on first-call keeps the answer readable
    /// without flooding logcat when the game retries — the same reason the per-frame JNI
    /// bodies are silent.
    ///
    /// **Cold paths only.** `OnUpdate` and the `N2Map*` getters run every frame and must
    /// not call this; `OnUpdate` uses a counter instead.
    pub fn log_once(&mut self, label: &'static str) {
        if self.seen.insert(label) {
            crate::log::info(label);
        }
    }

    /// Intern `value` into `slot` on first use and return the stable pointer.
    pub fn intern(slot: &mut Option<CString>, value: &str) -> *const core::ffi::c_char {
        let s = slot.get_or_insert_with(|| {
            // Fall back to an empty string rather than panicking on interior NULs.
            CString::new(value).unwrap_or_default()
        });
        s.as_ptr()
    }
}

/// A finished round trip, produced by a worker thread and waiting to be handed to the game
/// on a thread the game itself called us on.
///
/// **Every field is deliberately `Send`.** That is the whole reason this queue is a separate
/// `Arc<Mutex<Vec<Completion>>>` rather than a field on [`State`]: a worker thread needs to
/// push into it, and `State` holds raw pointers, so sharing `State` would mean an
/// `unsafe impl Send` with a use-after-free hazard against `N2PluginDestroyContext` (the
/// worker would have to hold something derived from the handle, which the game can free at
/// any moment). Confining the shared state to values that are honestly `Send` gets the same
/// concurrency with no `unsafe` and no lifetime coupling to the context at all.
///
/// The GCHandle therefore travels as a `usize`. It is an opaque integer that is only ever
/// passed back to `call`/`free`; nothing on this side dereferences it, so the conversion
/// loses nothing and keeps the type `Send` for free.
pub enum Completion {
    /// An `N2ServerSend` reply, ready for its per-request callback.
    Rpc {
        /// A managed **GCHandle**, not a plugin handle. See `notes/05-marshalling.md`.
        callback_handle: usize,
        call: CallCallbackDelegate,
        free: FreeHandleDelegate,
        rpc_id: i32,
        /// An `RpcStatus`. Success is 1.
        status: i32,
        payloads: Vec<Vec<u8>>,
    },
    /// A ticket from `N2ServerRequestAuthToken`. Recorded, and answered with a `channelAuth`
    /// event carrying `GotAuthToken` -- see `server::deliver_all`.
    AuthTicket(String),
    /// Owned map composition bytes; pointers are formed only during delivery.
    MapSettings(Vec<u8>),
    /// One of the six `SetCallbacks` event handles, and the buffer to hand it.
    ///
    /// The slot travels as the enum rather than as the GCHandle because that handle lives on
    /// [`State`] behind a lock, and reaching it means taking that lock — which is the one
    /// thing a producer must not do while it is already holding it. Resolving the slot to a
    /// handle happens at delivery, on the game's thread, with the lock free.
    Event { slot: EventSlot, payload: Vec<u8> },
}

pub struct Context {
    state: Mutex<State>,
    /// See [`Completion`] for why this is not inside `state`.
    pending: Arc<Mutex<Vec<Completion>>>,
}

impl Context {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(State::default()),
            pending: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Lock the state, recovering from poisoning instead of panicking.
    pub fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A handle to the completion queue, for handing to a worker thread.
    ///
    /// Returns an owned `Arc`, which is exactly what a thread needs — and, unlike the
    /// context pointer, is safe to hold indefinitely.
    pub fn pending(&self) -> Arc<Mutex<Vec<Completion>>> {
        Arc::clone(&self.pending)
    }

    /// Take everything the workers have finished.
    ///
    /// **Never call this while holding [`Context::lock`].** The caller is expected to drain
    /// first and deliver afterwards for the same reason: invoking a delegate re-enters the
    /// plugin, and a re-entrant `lock()` on the same thread would deadlock.
    pub fn take_completions(&self) -> Vec<Completion> {
        let mut q = self.pending.lock().unwrap_or_else(|e| e.into_inner());
        // `mem::take` on an empty Vec allocates nothing, so the per-frame cost while idle
        // is nil — which matters, since this runs from `OnUpdate`.
        core::mem::take(&mut *q)
    }
}

impl Default for Context {
    fn default() -> Self {
        Self::new()
    }
}

/// Borrow the context behind a handle.
///
/// # Safety
/// `h` must be null or a pointer previously returned by `N2PluginCreateContext` that
/// has not since been passed to `N2PluginDestroyContext`.
pub unsafe fn with_ctx<R>(h: Handle, f: impl FnOnce(&Context) -> R) -> Result<R, ApiError> {
    if h.is_null() {
        return Err(ApiError::InvalidParameters);
    }
    Ok(f(&*(h as *const Context)))
}
