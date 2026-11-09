//! Vtable slots 33-35: debug overrides for device heading and location.
//!
//! `DebugBindings` declares `IDebugBindings` as an empty interface, so these have no
//! managed call path in the dumps — they are reachable only through the raw vtable.
//! Implementing them makes the `N2Map*` getters testable without a server.

use crate::abi::*;
use crate::context::with_ctx;
use crate::server;
use crate::trace;

pub unsafe extern "C" fn set_device_heading(handle: Handle, heading_deg: f64) -> ApiError {
    trace::DEBUG_SET_DEVICE_HEADING.enter_with(&format!("{heading_deg}"));
    with_ctx(handle, |c| {
        c.lock().debug_heading = Some(CompassHeadingData {
            heading_deg,
            timestamp_s: 0.0,
        });
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn set_device_location(
    handle: Handle,
    latitude_deg: f64,
    longitude_deg: f64,
    altitude_m: f64,
    accuracy_m: f64,
    provider_status: u32,
    location_type: u32,
) -> ApiError {
    trace::DEBUG_SET_DEVICE_LOCATION
        .enter_with(&format!("{latitude_deg},{longitude_deg} acc={accuracy_m}"));
    let fix = LocationData {
        latitude_deg,
        longitude_deg,
        altitude_m,
        accuracy_m,
        timestamp_s: std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map(|v| v.as_secs_f64()).unwrap_or(0.0),
        provider_status,
        location_type,
    };
    // A fix arriving is the whole meaning of the `locationUpdate` event: it is what the real
    // native code calls `DeviceManager.NotifyLocationUpdate` for, at exactly this moment.
    let payload = location_payload(&fix);
    with_ctx(handle, |c| {
        c.lock().debug_location = Some(fix);
        // Queued rather than fired here, because this slot is inside `with_ctx` and so holds
        // the state lock, while delivering needs it again. `OnUpdate` fires it, on the game's
        // own thread.
        //
        // The other fix source, `live::set_location`, is *not* wired: the sensor natives
        // receive no plugin handle (see [crate::live]), so there is no context there to
        // resolve an event handle against. It is unreachable in the stub today; when the
        // Java services get constructed, this is the second place that needs a `raise`.
        server::raise(c, EventSlot::LocationUpdate, payload);
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn cancel_debug_location(handle: Handle) -> ApiError {
    trace::DEBUG_CANCEL_DEBUG_LOCATION.enter();
    with_ctx(handle, |c| {
        let mut s = c.lock();
        s.debug_location = None;
        s.debug_heading = None;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}
