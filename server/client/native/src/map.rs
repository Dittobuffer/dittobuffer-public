//! Vtable slots 24-25: compass heading and current location.
//!
//! Two sources feed these, in this order:
//!
//! 1. **The `N2Debug*` setters.** An explicit "pretend I'm here" outranks device truth, so
//!    a debug override wins while it is set. This is also what keeps
//!    `N2DebugCancelDebugLocation` meaningful: cancelling falls back to the live fix
//!    rather than to `PluginNotReady`.
//! 2. **The sensor natives** (`nativeLocationUpdate` / `nativeCompassUpdate`), via
//!    [`crate::live`]. The Java provider chain supplies measured fused/GPS fixes.

/// Return Success to preserve the managed startup contract while waiting for GPS.
/// The empty origin is INITIALIZED, never a RUNNING measured fix, so gameplay
/// must wait for a genuine provider fix. The periodic push and getter use the
/// same value and cannot disagree about its validity.
pub(crate) fn no_fix() -> LocationData {
    LocationData {
        latitude_deg: 0.0,
        longitude_deg: 0.0,
        altitude_m: 0.0,
        accuracy_m: 0.0,
        timestamp_s: now_s(),
        provider_status: PROVIDER_STATUS_INITIALIZED,
        location_type: LOCATION_TYPE_MEASURED,
    }
}

/// Seconds since the Unix epoch, as [`LocationData::timestamp_s`] carries them.
///
/// `.unwrap_or(0.0)` rather than a panic: a clock set before 1970 is not a reason to abort the
/// game process, and `panic = "abort"` in release makes any `unwrap` here a real hazard.
fn now_s() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

use crate::abi::*;
use crate::context::with_ctx;
use crate::live;
use crate::trace;

pub unsafe extern "C" fn get_compass_heading(handle: Handle, out: *mut CompassHeadingData) -> ApiError {
    trace::GET_COMPASS_HEADING.enter_throttled(trace::PER_FRAME);
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    match with_ctx(handle, |c| {
        let mut s = c.lock();
        match s.debug_heading.or_else(live::heading) {
            Some(h) => Some(h),
            None => {
                // These two are the only slots in this file that are polled per frame, so
                // the line is gated rather than suppressed: "the game asked for a heading
                // and got nothing" is a real answer, one repeated 60x/s is not.
                s.log_once("GetCompassHeading -> PluginNotReady (no fix yet)");
                None
            }
        }
    }) {
        Ok(Some(h)) => {
            *out = h;
            ApiError::Success
        }
        Ok(None) => ApiError::PluginNotReady,
        Err(e) => e,
    }
}

pub unsafe extern "C" fn get_current_location(handle: Handle, out: *mut LocationData) -> ApiError {
    trace::GET_CURRENT_LOCATION.enter_throttled(trace::PER_FRAME);
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    match with_ctx(handle, |c| {
        let mut s: std::sync::MutexGuard<'_, crate::context::State> = c.lock();
        match s.debug_location.or_else(live::location) {
            Some(l) => Some(l),
            None => {
                s.log_once("GetCurrentLocation -> INITIALIZED (no measured fix yet)");
                None
            }
        }
    }) {
        Ok(Some(l)) => {
            *out = l;
            ApiError::Success
        }
        Ok(None) => {
            // Written explicitly. This arm previously returned `Success` without touching
            // `*out`, which handed the game whatever was on the caller's stack: a
            // `LocationData` full of uninitialized doubles that varied per call site.
            *out = no_fix();
            ApiError::Success
        }
        Err(e) => e,
    }
}
