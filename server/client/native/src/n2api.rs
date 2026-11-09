//! The 36-slot N2 vtable returned by `GetN2Api`.
//!
//! **Field order is the ABI.** The managed side reads this struct at the fixed offsets
//! given in `Niantic.Platform.Ditto.cs:152-191`, so the order here must match that
//! file exactly — in particular slot 0xE8 is `SetDebugInfo` and 0xF0 is
//! `SendMapQueryRequest`. See `notes/03-n2api-vtable.md`.

use crate::abi::*;
use core::ffi::{c_char, c_void};

pub type FnPluginCreateContext = unsafe extern "C" fn(out: *mut Handle) -> ApiError;
pub type FnPluginDestroyContext = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginAddSnitchLog =
    unsafe extern "C" fn(tag: *const c_char, group: *const c_char, lvl: i32, msg: *const c_char) -> ApiError;
pub type FnPluginAddSnitchMarker = unsafe extern "C" fn(msg: *const c_char) -> ApiError;
pub type FnPluginBeginTrace = unsafe extern "C" fn(label: *const c_char) -> ApiError;
pub type FnPluginDeferLocationPermissionRequest = unsafe extern "C" fn(handle: Handle, on: i32) -> ApiError;
pub type FnPluginEndTrace = unsafe extern "C" fn() -> ApiError;
pub type FnPluginGetDeviceCountryCode = unsafe extern "C" fn(handle: Handle, out: *mut *const c_char) -> ApiError;
pub type FnPluginGetDeviceLanguageCode = unsafe extern "C" fn(handle: Handle, out: *mut *const c_char) -> ApiError;
pub type FnPluginGetDeviceTimezone = unsafe extern "C" fn(handle: Handle, out: *mut *const c_char) -> ApiError;
pub type FnPluginGetState = unsafe extern "C" fn(handle: Handle, out: *mut i32) -> ApiError;
pub type FnPluginOnPause = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginOnResume = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginOnStart = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginOnStop = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginOnUpdate = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginSetBackgroundMode = unsafe extern "C" fn(handle: Handle, on: i32) -> ApiError;
pub type FnPluginSetCallbacks = unsafe extern "C" fn(
    handle: Handle,
    call: CallCallbackDelegate,
    free: FreeHandleDelegate,
    channel_auth: *mut c_void,
    location_update: *mut c_void,
    downstream_action: *mut c_void,
    sync_requested: *mut c_void,
    client_settings_updated: *mut c_void,
    map_object_updated: *mut c_void,
) -> ApiError;
pub type FnPluginSetProduct =
    unsafe extern "C" fn(handle: Handle, product_name: *const c_char, product_version: *const c_char) -> ApiError;
pub type FnPluginSetUserAgent = unsafe extern "C" fn(handle: Handle, user_agent: *const c_char) -> ApiError;
pub type FnPluginEnableGlobalHttp3 = unsafe extern "C" fn(handle: Handle, enable: i32) -> ApiError;
pub type FnPluginStartSnitch = unsafe extern "C" fn(handle: Handle, port: u16) -> ApiError;
pub type FnPluginStopSnitch = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnPluginSync = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnMapGetCompassHeading = unsafe extern "C" fn(handle: Handle, out: *mut CompassHeadingData) -> ApiError;
pub type FnMapGetCurrentLocation = unsafe extern "C" fn(handle: Handle, out: *mut LocationData) -> ApiError;
pub type FnServerRequestAuthToken = unsafe extern "C" fn(
    handle: Handle,
    auth_id: *const c_char,
    token: *const u8,
    token_size: i32,
    session_options: *mut SessionOptionsData,
) -> ApiError;
pub type FnServerGenerateRpcId = unsafe extern "C" fn(handle: Handle, out: *mut i32) -> ApiError;
pub type FnServerSend = unsafe extern "C" fn(
    handle: Handle,
    timeout_ms: i32,
    retry_delay_ms: i32,
    count: i32,
    methods: *const i32,
    bodies: *const c_void,
    sizes: *const i32,
    rpc_id: i32,
    callback_handle: *mut c_void,
    call: CallCallbackDelegate,
    free: FreeHandleDelegate,
) -> ApiError;
pub type FnServerSetDebugInfo = unsafe extern "C" fn(handle: Handle, debug_info: *const c_void, debug_size: i32) -> ApiError;
pub type FnServerSendMapQueryRequest = unsafe extern "C" fn(handle: Handle, request_data: *const u8, size: i32) -> ApiError;
pub type FnServerSetServer = unsafe extern "C" fn(handle: Handle, host_port: *const c_char) -> ApiError;
pub type FnServerTerminateSession = unsafe extern "C" fn(handle: Handle) -> ApiError;
pub type FnDebugSetDeviceHeading = unsafe extern "C" fn(handle: Handle, heading_deg: f64) -> ApiError;
pub type FnDebugSetDeviceLocation = unsafe extern "C" fn(
    handle: Handle,
    latitude_deg: f64,
    longitude_deg: f64,
    altitude_m: f64,
    accuracy_m: f64,
    provider_status: u32,
    location_type: u32,
) -> ApiError;
pub type FnDebugCancelDebugLocation = unsafe extern "C" fn(handle: Handle) -> ApiError;

/// `N2EntryPoints.N2NativeBindings` — 36 pointers, 288 bytes.
///
/// Plain (non-`Option`) function pointers, so "every slot is non-null" is guaranteed by
/// construction rather than by convention.
#[repr(C)]
pub struct N2NativeBindings {
    pub n2_plugin_create_context: FnPluginCreateContext,                   // 0x00
    pub n2_plugin_destroy_context: FnPluginDestroyContext,                 // 0x08
    pub n2_plugin_add_snitch_log: FnPluginAddSnitchLog,                    // 0x10
    pub n2_plugin_add_snitch_marker: FnPluginAddSnitchMarker,              // 0x18
    pub n2_plugin_begin_trace: FnPluginBeginTrace,                         // 0x20
    pub n2_plugin_defer_location_permission_request: FnPluginDeferLocationPermissionRequest, // 0x28
    pub n2_plugin_end_trace: FnPluginEndTrace,                             // 0x30
    pub n2_plugin_get_device_country_code: FnPluginGetDeviceCountryCode,   // 0x38
    pub n2_plugin_get_device_language_code: FnPluginGetDeviceLanguageCode, // 0x40
    pub n2_plugin_get_device_timezone: FnPluginGetDeviceTimezone,          // 0x48
    pub n2_plugin_get_state: FnPluginGetState,                             // 0x50
    pub n2_plugin_on_pause: FnPluginOnPause,                               // 0x58
    pub n2_plugin_on_resume: FnPluginOnResume,                             // 0x60
    pub n2_plugin_on_start: FnPluginOnStart,                               // 0x68
    pub n2_plugin_on_stop: FnPluginOnStop,                                 // 0x70
    pub n2_plugin_on_update: FnPluginOnUpdate,                             // 0x78
    pub n2_plugin_set_background_mode: FnPluginSetBackgroundMode,          // 0x80
    pub n2_plugin_set_callbacks: FnPluginSetCallbacks,                     // 0x88
    pub n2_plugin_set_product: FnPluginSetProduct,                         // 0x90
    pub n2_plugin_set_user_agent: FnPluginSetUserAgent,                    // 0x98
    pub n2_plugin_enable_global_http3: FnPluginEnableGlobalHttp3,          // 0xA0
    pub n2_plugin_start_snitch: FnPluginStartSnitch,                       // 0xA8
    pub n2_plugin_stop_snitch: FnPluginStopSnitch,                         // 0xB0
    pub n2_plugin_sync: FnPluginSync,                                      // 0xB8
    pub n2_map_get_compass_heading: FnMapGetCompassHeading,                // 0xC0
    pub n2_map_get_current_location: FnMapGetCurrentLocation,              // 0xC8
    pub n2_server_request_auth_token: FnServerRequestAuthToken,            // 0xD0
    pub n2_server_generate_rpc_id: FnServerGenerateRpcId,                  // 0xD8
    pub n2_server_send: FnServerSend,                                      // 0xE0
    pub n2_server_set_debug_info: FnServerSetDebugInfo,                    // 0xE8
    pub n2_server_send_map_query_request: FnServerSendMapQueryRequest,     // 0xF0
    pub n2_server_set_server: FnServerSetServer,                           // 0xF8
    pub n2_server_terminate_session: FnServerTerminateSession,             // 0x100
    pub n2_debug_set_device_heading: FnDebugSetDeviceHeading,              // 0x108
    pub n2_debug_set_device_location: FnDebugSetDeviceLocation,            // 0x110
    pub n2_debug_cancel_debug_location: FnDebugCancelDebugLocation,        // 0x118
}

/// The table `GetN2Api` copies out. Functions are `Sync`, so this is a valid `static`.
pub static N2_API: N2NativeBindings = N2NativeBindings {
    n2_plugin_create_context: crate::plugin::create_context,
    n2_plugin_destroy_context: crate::plugin::destroy_context,
    n2_plugin_add_snitch_log: crate::plugin::add_snitch_log,
    n2_plugin_add_snitch_marker: crate::plugin::add_snitch_marker,
    n2_plugin_begin_trace: crate::plugin::begin_trace,
    n2_plugin_defer_location_permission_request: crate::plugin::defer_location_permission_request,
    n2_plugin_end_trace: crate::plugin::end_trace,
    n2_plugin_get_device_country_code: crate::plugin::get_device_country_code,
    n2_plugin_get_device_language_code: crate::plugin::get_device_language_code,
    n2_plugin_get_device_timezone: crate::plugin::get_device_timezone,
    n2_plugin_get_state: crate::plugin::get_state,
    n2_plugin_on_pause: crate::plugin::on_pause,
    n2_plugin_on_resume: crate::plugin::on_resume,
    n2_plugin_on_start: crate::plugin::on_start,
    n2_plugin_on_stop: crate::plugin::on_stop,
    n2_plugin_on_update: crate::plugin::on_update,
    n2_plugin_set_background_mode: crate::plugin::set_background_mode,
    n2_plugin_set_callbacks: crate::plugin::set_callbacks,
    n2_plugin_set_product: crate::plugin::set_product,
    n2_plugin_set_user_agent: crate::plugin::set_user_agent,
    n2_plugin_enable_global_http3: crate::plugin::enable_global_http3,
    n2_plugin_start_snitch: crate::plugin::start_snitch,
    n2_plugin_stop_snitch: crate::plugin::stop_snitch,
    n2_plugin_sync: crate::plugin::sync,
    n2_map_get_compass_heading: crate::map::get_compass_heading,
    n2_map_get_current_location: crate::map::get_current_location,
    n2_server_request_auth_token: crate::server::request_auth_token,
    n2_server_generate_rpc_id: crate::server::generate_rpc_id,
    n2_server_send: crate::server::send,
    n2_server_set_debug_info: crate::server::set_debug_info,
    n2_server_send_map_query_request: crate::server::send_map_query_request,
    n2_server_set_server: crate::server::set_server,
    n2_server_terminate_session: crate::server::terminate_session,
    n2_debug_set_device_heading: crate::debug::set_device_heading,
    n2_debug_set_device_location: crate::debug::set_device_location,
    n2_debug_cancel_debug_location: crate::debug::cancel_debug_location,
};

/// `void GetN2Api(N2NativeBindings* out)` — copies 288 bytes.
///
/// Note this returns **nothing**: the original tail-calls a bare `ret`, so its return
/// value is a leftover register, not an `ApiError`. See `notes/02-exported-symbols.md`.
///
/// # Safety
/// `out` must be null or point to at least 288 writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn GetN2Api(out: *mut N2NativeBindings) {
    if out.is_null() {
        return;
    }
    core::ptr::copy_nonoverlapping(&N2_API, out, 1);
}
