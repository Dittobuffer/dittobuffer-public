//! FFI types shared by the vtable and the JNI layer.
//!
//! Layouts and values are taken from the managed side, which is the authority:
//! `Niantic.Platform.Ditto.cs` (bindings) and `Niantic.Platform.DittoApi.cs`
//! (enums, callback payloads). See `notes/03-n2api-vtable.md`.

use core::ffi::{c_char, c_void};

/// Opaque plugin context, created by `N2PluginCreateContext`.
pub type Handle = *mut c_void;

/// `N2EntryPoints.ApiError` — `Niantic.Platform.Ditto.cs:70-78`.
///
/// `Success` is the only value that does not raise on the managed side: every
/// binding funnels the return through `CheckResult`, which throws otherwise.
#[repr(i32)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ApiError {
    Success = 0,
    PluginNotReady = 1,
    NotSupported = 2,
    NotImplementedYet = 3,
    InternalError = 4,
    InvalidParameters = 5,
}

/// `Niantic.Platform.Ditto.Plugin.LogLevelInternal` — `Niantic.Platform.Ditto.cs:400-408`.
#[repr(i32)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum LogLevelInternal {
    Off = 0,
    Fatal = 1,
    Error = 2,
    Warning = 3,
    Info = 4,
    Verbose = 5,
}

impl LogLevelInternal {
    /// The vtable passes the level as a raw `i32`, so decode defensively.
    pub fn from_i32(v: i32) -> Option<Self> {
        Some(match v {
            0 => Self::Off,
            1 => Self::Fatal,
            2 => Self::Error,
            3 => Self::Warning,
            4 => Self::Info,
            5 => Self::Verbose,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Fatal => "fatal",
            Self::Error => "error",
            Self::Warning => "warn",
            Self::Info => "info",
            Self::Verbose => "verbose",
        }
    }
}

/// `Niantic.Platform.DittoApi.PluginState` — `Niantic.Platform.DittoApi.cs:536-546`.
///
/// Reported through `N2PluginGetState`. `Active` is the value the game waits for.
// Mirrors the managed enum. Not every variant is constructed by the stub — `Idle` is
// reachable in the real plugin's state machine but has no stub path — and that is fine.
#[allow(dead_code)]
#[repr(i32)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PluginState {
    Undefined = 0,
    Init = 1,
    Start = 2,
    Stop = 3,
    Pause = 4,
    Resume = 5,
    Active = 6,
    Idle = 7,
}

/// `Niantic.Platform.Ditto.Auth.SessionOptionsData` — one `int` at `+0x00`.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct SessionOptionsData {
    pub prevent_account_creation: i32,
}

/// `Niantic.Platform.Ditto.Geo.CompassHeadingData` — two `double`s.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct CompassHeadingData {
    pub heading_deg: f64,
    pub timestamp_s: f64,
}

/// `LocationType.Measured` (`DittoApi.cs:329-334`).
///
/// A fix arriving through `nativeLocationUpdate` is measured by definition — the other two
/// values describe positions derived from something else, which only the `N2Debug*` slot
/// can express.
pub const LOCATION_TYPE_MEASURED: u32 = 1;

/// `ProviderStatus.UNDEFINED` — `ServiceStatus.ordinal()` 0.
///
/// Not a status so much as the absence of one, and Java sends it constantly: every
/// location-bearing `nativeLocationUpdate` carries it, because the listener that reports a fix
/// has no status to report. See [`crate::live::set_status`] for why that matters.
pub const PROVIDER_STATUS_UNDEFINED: u32 = 0;
/// Provider is initialized but has not produced a usable measured fix.
pub const PROVIDER_STATUS_INITIALIZED: u32 = 2;

/// `ProviderStatus.RUNNING` (`DittoApi.cs:337-344`).
///
/// The same six constants in the same order as Java's `ServiceStatus` — see
/// [notes/04-jni-surface.md] — so this is the C# value, not a translation of it. A real fix
/// inherits the ordinal Java sent on a separate call. The no-fix response uses INITIALIZED
/// until a measured location arrives; it must not advertise a usable origin coordinate.
pub const PROVIDER_STATUS_RUNNING: u32 = 3;

/// `Niantic.Platform.Ditto.Geo.LocationData` — fields `0x00..0x2C`, size `0x30`.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct LocationData {
    pub latitude_deg: f64,
    pub longitude_deg: f64,
    pub altitude_m: f64,
    pub accuracy_m: f64,
    pub timestamp_s: f64,
    pub provider_status: u32,
    pub location_type: u32,
}

/// `N2EntryPoints.CallCallbackDelegate` — `Niantic.Platform.Ditto.cs:44`.
///
/// `handle` is a managed **GCHandle**, not a plugin handle. Pass back the value that
/// arrived in one of the six `*EventHandle` arguments. `buffer` is native-owned and
/// only needs to stay valid for the duration of the call; managed never frees it.
pub type CallCallbackDelegate = unsafe extern "C" fn(handle: *mut c_void, buffer: *mut u8);

/// `N2EntryPoints.FreeHandleDelegate` — `Niantic.Platform.Ditto.cs:46`.
///
/// The managed body is exactly `GCHandle.FromIntPtr(pointer).Free()`, so this frees a
/// GC handle only. Never call it with the plugin context handle.
pub type FreeHandleDelegate = unsafe extern "C" fn(handle: *mut c_void);

/// One response payload — `Niantic.Platform.Ditto.Rpc.MarshallableByteArray`
/// (`DittoApi.cs:772-776`), rebuilt as `PayloadArr` by Mimikyu (`lib/src/Rotom.cpp:426`).
///
/// `size` sits at offset 8, not 4: the pointer aligns the struct to 8, so there are four
/// bytes of padding after it. Getting this wrong reads the low half of the pointer as a
/// length.
#[repr(C)]
#[derive(Debug)]
pub struct PayloadArr {
    pub data: *mut u8,
    pub size: i32,
}

/// The buffer handed to the per-request callback — `RpcManager.MarshallableRpcResponseEventParams`
/// (`DittoApi.cs:778-785`), rebuilt as `RpcRes` by Mimikyu (`lib/src/Rotom.cpp:430`).
///
/// Same four fields, same widths, same order in both sources, which is why this layout is
/// treated as settled rather than inferred. Note it is **not** preceded by a length or
/// terminator: managed reads exactly `count` payloads and never trusts anything else.
#[repr(C)]
#[derive(Debug)]
pub struct RpcRes {
    /// `rpc_id` first, `status` second — the reverse of `ApiError`'s convention elsewhere.
    pub rpc_id: i32,
    /// An `RpcStatus`, not an `ApiError`. Success is **1**.
    pub status: i32,
    pub payloads: *mut PayloadArr,
    pub payload_count: i32,
}

/// `RpcStatus.Success` — `DittoApi.cs:919-941`. `Undefined` is 0 and the next real value is
/// `BadResponse` = 3, so 2 is a gap in the enum, not a status.
///
/// Only the tests name this: the reply's status comes from the dev server on the wire, and
/// the ABI value is recovered rather than computed. Kept because a bare `1` at the
/// assertion site would not say *why* 1 is right.
#[allow(dead_code)]
pub const RPC_STATUS_SUCCESS: i32 = 1;
/// `RpcStatus.ServerError`. What the stub reports when the dev server is unreachable, so
/// the game is told something definite instead of waiting on a callback that never comes.
pub const RPC_STATUS_SERVER_ERROR: i32 = 6;

// --- the six `SetCallbacks` event handles ------------------------------------

/// Which of the six event handles a callback belongs to.
///
/// The discriminant is the index into [`crate::context::Callbacks::events`], and that order
/// is the order `PluginBindings.SetCallbacks` passes them in — `Ditto.cs`:
/// `channelAuth, locationUpdate, downstreamAction, syncRequested, clientSettingsUpdated,
/// mapObjectUpdated`. Only the two the stub fires are named; the other four have no payload
/// layout recovered yet and inventing one would be worse than the silence.
#[repr(usize)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EventSlot {
    ChannelAuth = 0,
    LocationUpdate = 1,
    ClientSettingsUpdated = 4,
}

/// `Niantic.Platform.Ditto.Auth.AuthManager.NativeChannelAuthEvent` — `DittoApi.cs:871`.
///
/// This enum *is* the auth state machine: `NativeEventHandler` has one handler per value
/// (`HandleGotAuthToken`, `HandleSessionFailed`, …), and every transition the game makes
/// comes from the `channelAuth` callback delivering one of these.
///
/// Only `GotAuthToken` is fired, but the whole list is here because the interesting question
/// during a stall is "which of these should we be sending instead", and that question is
/// unanswerable from a single constant.
#[allow(dead_code)]
#[repr(i32)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NativeChannelAuthEvent {
    Undefined = 0,
    AuthTokenRequestFailed = 1,
    AuthTokenRequested = 2,
    GotAuthToken = 3,
    DeviceIncompatible = 4,
    UserNotFound = 5,
    AccessDenied = 6,
    AccessSuspended = 7,
    AccessRateLimited = 8,
    SessionTerminated = 9,
    SessionFailed = 10,
    GooglePlayNotReady = 11,
    LoginErrorBail = 12,
}

/// The `channelAuth` buffer: `MarshallableChannelAuthEventParams`, one `int32` at native
/// offset 0.
///
/// The dump prints that field at `0x10`, but the dump carries a class header on every type —
/// `MarshallableRpcResponseEventParams` prints `rpcId` at `0x10` while the `RpcRes` we
/// verified against Mimikyu has it at 0. Subtracting the header puts this field at 0 too, so
/// the buffer is a bare little-endian `i32`.
pub fn channel_auth_payload(evt: NativeChannelAuthEvent) -> Vec<u8> {
    (evt as i32).to_le_bytes().to_vec()
}

/// The `locationUpdate` buffer: `Geo.Location`, whose single field is a `LocationData` at
/// native offset 0 (`DittoApi.cs:718`), so the buffer *is* the struct we already marshal in
/// both directions.
///
/// Written field by field rather than by viewing the struct as bytes. Both are correct while
/// [`LocationData`] stays padding-free, but this one stays correct if it ever stops being —
/// and it says which endianness the buffer is, which a raw view cannot.
pub fn location_payload(l: &LocationData) -> Vec<u8> {
    let mut out = Vec::with_capacity(0x30);
    out.extend_from_slice(&l.latitude_deg.to_le_bytes());
    out.extend_from_slice(&l.longitude_deg.to_le_bytes());
    out.extend_from_slice(&l.altitude_m.to_le_bytes());
    out.extend_from_slice(&l.accuracy_m.to_le_bytes());
    out.extend_from_slice(&l.timestamp_s.to_le_bytes());
    out.extend_from_slice(&l.provider_status.to_le_bytes());
    out.extend_from_slice(&l.location_type.to_le_bytes());
    out
}

/// How many bytes the managed receiver for `slot` reads out of the buffer.
///
/// Managed is handed no length — `CallCallbackImpl` casts the pointer to a fixed-size struct
/// and reads it — so this is not a hint about a preferred size. It is the size the buffer must
/// be, or the game reads past our allocation. Kept beside the two builders above, which are
/// the only things that produce those buffers.
pub const fn payload_width(slot: EventSlot) -> usize {
    match slot {
        // `MarshallableChannelAuthEventParams`.
        EventSlot::ChannelAuth => core::mem::size_of::<i32>(),
        // `Geo.Location`, whose single field is a `LocationData` at offset 0.
        EventSlot::LocationUpdate => core::mem::size_of::<LocationData>(),
        EventSlot::ClientSettingsUpdated => 32,
    }
}

/// Convert an incoming UTF-8 `const char*` into an owned `String`.
///
/// Incoming strings are borrowed for the duration of the call only, so every use site
/// copies. Returns `None` for null, non-UTF-8, or interior-NUL input rather than
/// panicking.
///
/// # Safety
/// `p` must be null or a valid NUL-terminated C string.
pub unsafe fn cstr_to_string(p: *const c_char) -> Option<String> {
    if p.is_null() {
        return None;
    }
    core::ffi::CStr::from_ptr(p).to_str().ok().map(str::to_owned)
}
