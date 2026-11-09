//! Vtable slots 0-23: context lifecycle, device info, tracing, snitch, state machine.

use crate::abi::*;
use crate::context::{with_ctx, Context, State};
use crate::live;
use crate::jni::location_service;
use crate::log;
use crate::map;
use crate::server;
use crate::trace;
use core::ffi::{c_char, c_void};

pub unsafe extern "C" fn create_context(out: *mut Handle) -> ApiError {
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    let ctx = Box::into_raw(Box::new(Context::new()));
    (*ctx).lock().plugin_state = PluginState::Init;
    // Writes 8 bytes, matching PluginBindings.CreateContext's zeroed slot.
    *out = ctx.cast();
    trace::CREATE_CONTEXT.enter_with("state=Init");
    ApiError::Success
}

pub unsafe extern "C" fn destroy_context(handle: Handle) -> ApiError {
    if handle.is_null() {
        return ApiError::InvalidParameters;
    }
    // Before the context goes. The six event handles die with it, so a detached producer left
    // holding the published queue would raise events that can never be delivered -- they would
    // just pile up until `MAX_PENDING` started evicting them.
    server::unpublish();
    // The Java manager outlives both the game's `Context` and every native frame that touched
    // it: it is held by a JNI global reference, the one thing here that only an explicit
    // release frees. Leaving it would keep Android location listeners registered against a
    // dead plugin.
    location_service::detach();
    drop(Box::from_raw(handle.cast::<Context>()));
    trace::DESTROY_CONTEXT.enter();
    ApiError::Success
}

pub unsafe extern "C" fn get_state(handle: Handle, out: *mut i32) -> ApiError {
    // Throttled: the state itself is on the `OnUpdate` heartbeat line, so the only thing this
    // line needs to answer is whether the game is still asking.
    trace::GET_STATE.enter_throttled(trace::PER_FRAME);
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    match with_ctx(handle, |c| c.lock().plugin_state as i32) {
        Ok(v) => {
            // Writes 4 bytes.
            *out = v;
            ApiError::Success
        }
        Err(e) => e,
    }
}

pub unsafe extern "C" fn set_product(
    handle: Handle,
    product_name: *const c_char,
    product_version: *const c_char,
) -> ApiError {
    // Incoming strings are borrowed for the call only, so copy.
    let name = cstr_to_string(product_name);
    let version = cstr_to_string(product_version);
    // The *payload* is the point of tracing this one: if the game dies around here, the string
    // marshalling assumption in notes/05-marshalling.md is what is wrong, and this line is
    // what says so. Logged from the incoming values rather than the stored ones so a call on a
    // rejected handle is still visible.
    trace::SET_PRODUCT.enter_with(&format!(
        "{}, {}",
        name.as_deref().unwrap_or("<null>"),
        version.as_deref().unwrap_or("<null>"),
    ));
    with_ctx(handle, |c| {
        let mut s = c.lock();
        s.product_name = name;
        s.product_version = version;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn set_user_agent(handle: Handle, user_agent: *const c_char) -> ApiError {
    let ua = cstr_to_string(user_agent);
    trace::SET_USER_AGENT.enter_with(ua.as_deref().unwrap_or("<null>"));
    with_ctx(handle, |c| {
        c.lock().user_agent = ua;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn set_background_mode(handle: Handle, on: i32) -> ApiError {
    // Traced on every call, not gated per value as it was: this can be flipped on every
    // foreground/background transition, and *how often it flips* is the signal -- a game that
    // is being backgrounded and resumed in a loop looks identical to one sitting idle when
    // only the first of each value is logged.
    trace::SET_BACKGROUND_MODE.enter_with(if on != 0 { "1" } else { "0" });
    with_ctx(handle, |c| {
        c.lock().background_mode = on != 0;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn defer_location_permission_request(handle: Handle, on: i32) -> ApiError {
    with_ctx(handle, |c| {
        let mut s = c.lock();
        s.defer_location_permission = on != 0;
        s.log_once(if on != 0 {
            "DeferLocationPermissionRequest(1)"
        } else {
            "DeferLocationPermissionRequest(0)"
        });
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn enable_global_http3(handle: Handle, enable: i32) -> ApiError {
    trace::ENABLE_GLOBAL_HTTP3.enter_with(if enable != 0 { "1" } else { "0" });
    with_ctx(handle, |c| {
        c.lock().http3_enabled = enable != 0;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn start_snitch(handle: Handle, port: u16) -> ApiError {
    // Protocol unrecoverable from the available artifacts; accept the port and no-op.
    trace::START_SNITCH.enter_with(&format!("port={port} -> Success (protocol not implemented)"));
    with_ctx(handle, |c| {
        c.lock().snitch_port = Some(port);
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn stop_snitch(handle: Handle) -> ApiError {
    trace::STOP_SNITCH.enter();
    with_ctx(handle, |c| {
        c.lock().snitch_port = None;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

pub unsafe extern "C" fn set_callbacks(
    handle: Handle,
    call: CallCallbackDelegate,
    free: FreeHandleDelegate,
    channel_auth: *mut c_void,
    location_update: *mut c_void,
    downstream_action: *mut c_void,
    sync_requested: *mut c_void,
    client_settings_updated: *mut c_void,
    map_object_updated: *mut c_void,
) -> ApiError {
    with_ctx(handle, |c| {
        let mut s = c.lock();
        s.callbacks.call = Some(call);
        s.callbacks.free = Some(free);
        // All six are managed GCHandles. Store verbatim; never dereference, never free.
        s.callbacks.events = [
            channel_auth,
            location_update,
            downstream_action,
            sync_requested,
            client_settings_updated,
            map_object_updated,
        ];
        // The handle count is what validates the notes/06-object-model.md reconstruction:
        // all six are expected non-null.
        let attached = s.callbacks.events.iter().filter(|h| !h.is_null()).count();
        trace::SET_CALLBACKS.enter_with(&format!("{attached}/6 event handles"));
        // Published here rather than at context creation: the six handles do not exist until
        // this call, so before it a detached producer would have nothing to raise at. This is
        // what lets a fix arriving from Java reach `deliver_event` without a `Context`.
        server::publish(c.pending());
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

/// `N2PluginOnStart` is the transition into `Active` that the game waits for.
///
/// Also where the Java location chain is built. `NianticLocationManager` is a `ContextService`
/// and `OnStart` is when its lifecycle is meant to begin, so this is the transition that
/// matches — and nothing else in the plugin would do it, which is why no fix has ever reached
/// the game. See [`crate::jni::location_service`].
///
/// Tied to the transition succeeding rather than run unconditionally: `OnStart` on a dead
/// context means something is wrong upstream, and starting a location chain over it would
/// merely hide that.
pub unsafe extern "C" fn on_start(handle: Handle) -> ApiError {
    let outcome = transition(handle, &trace::ON_START, PluginState::Start, "state -> Start (2)");
    if outcome == ApiError::Success {
        // A failure here is not fatal and not reported upward: `UnityPlayer` may not exist
        // yet, and `on_update` retries. Raising it would make the game treat a retryable
        // condition as a dead plugin.
        location_service::attach();
    }
    outcome
}

pub unsafe extern "C" fn on_stop(handle: Handle) -> ApiError {
    let outcome = transition(handle, &trace::ON_STOP, PluginState::Stop, "state -> Stop (3)");
    if outcome == ApiError::Success {
        // Stopped providers release their `LocationManager`. Deliberately last in the
        // plugin's own lifecycle too: the manager's `locationManager` is null afterwards, so
        // a `pause` following this would dereference it.
        location_service::stop();
    }
    outcome
}

pub unsafe extern "C" fn on_pause(handle: Handle) -> ApiError {
    let outcome = transition(handle, &trace::ON_PAUSE, PluginState::Pause, "state -> Pause (4)");
    if outcome == ApiError::Success {
        location_service::pause();
    }
    outcome
}

pub unsafe extern "C" fn on_resume(handle: Handle) -> ApiError {
    let outcome = transition(handle, &trace::ON_RESUME, PluginState::Resume, "state -> Resume (5)");
    if outcome == ApiError::Success {
        // The one that produces a fix: `onStart` only gives each provider its
        // `LocationManager`, and this is what asks it for a position.
        location_service::resume();
    }
    outcome
}

/// Called every frame by the Unity main loop, so it must stay cheap and total.
///
/// Also the delivery point for `N2ServerSend` replies: a worker thread cannot invoke a game
/// callback, so it queues the completion and this drains the queue.
pub unsafe extern "C" fn on_update(handle: Handle) -> ApiError {
    trace::ON_UPDATE.enter_throttled(trace::PER_FRAME);
    // Outside the state lock, deliberately: attaching reaches Java (`CallVoidMethod(onStart)`)
    // and Java calls back into `nativeAddLocationProviders`, which raises on the published
    // queue. None of that re-enters the context lock today, but holding it across a foreign
    // call is a deadlock waiting for someone to add one.
    //
    // A no-op once attached -- one atomic load per frame.
    location_service::retry_attach();
    with_ctx(handle, |c| {
        {
            let mut s = c.lock();
            if s.plugin_state == PluginState::Start || s.plugin_state == PluginState::Resume {
                s.plugin_state = PluginState::Active;
            }
            s.update_count = s.update_count.wrapping_add(1);
            // Heartbeat rather than a line per frame: at ~60fps this is one every 10s, which is
            // enough to tell "the main loop is running" from "it stopped right after OnStart".
            //
            // Carries the running per-slot tally. That is what turns "the game is not getting
            // anywhere" into a specific claim: a heartbeat reading `N2PluginOnUpdate=1200,
            // N2MapGetCurrentLocation=1200` and nothing else says the main loop is alive and
            // polling, and that the game has not asked for anything else since boot.
            if s.update_count % UPDATE_HEARTBEAT == 0 {
                log::info(&format!(
                    "OnUpdate x{} (state {}) | {}",
                    s.update_count,
                    s.plugin_state as i32,
                    trace::summary()
                ));
                // Once, at the first heartbeat: the entry points the game has still never
                // used. On a healthy boot that is a short list; on a stalled one it is most
                // of the table, and it is the only place that difference is visible.
                if s.update_count == UPDATE_HEARTBEAT {
                    log::info(&format!("  never called: {}", trace::never_called()));
                }
            }
            // Feed the game's location pipeline. The game is *not* polling for its position --
            // it calls `N2MapGetCurrentLocation` once at boot and never again -- it waits to be
            // told, via the `locationUpdate` event.
            //
            // Java now does deliver fixes: `location_service` builds the manager and starts it,
            // so `nativeLocationUpdate` fires through `natives::native_location_update`. This
            // push stays anyway, for two reasons. It repeats the newest fix in between the
            // arrivals, which is what a stream from a fused provider looks like to a consumer
            // expecting one a second. And it is the safety net: with no Java fix at all -- a
            // denied permission, a device with no lock -- it still keeps the pipeline fed from
            // the debug override or the synthesized origin, so the game is never left with a
            // position that silently stops updating.
            //
            // The value is the same one `map::get_current_location` would serve -- debug
            // override, else the live sensor fix, else the synthesized origin -- so the polled
            // and the pushed position cannot drift apart.
            if s.update_count % LOCATION_PUSH_INTERVAL == 0 {
                let fix = s.debug_location.or_else(live::location).unwrap_or_else(map::no_fix);
                // Under the state lock, which `raise` permits: it only *queues*, and the queue
                // is a separate mutex. `deliver_all` below runs after the lock is released.
                server::raise(c, EventSlot::LocationUpdate, location_payload(&fix));
            }
        }
        // The state lock is released above, deliberately: the delegates invoked here
        // re-enter the plugin, and doing so under the lock would deadlock against ourselves.
        server::deliver_all(c, c.take_completions());
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

/// Frames between `OnUpdate` heartbeat lines.
const UPDATE_HEARTBEAT: u32 = 600;

/// Frames between `locationUpdate` events — about one a second at the ~60fps the game runs at.
///
/// Deliberately not [`UPDATE_HEARTBEAT`]. That one is a *log* cadence, tuned to stay quiet; this
/// is a *data* cadence, tuned to match what an Android fused location provider delivers, because
/// that is the stream the managed `DeviceManager` is written against.
const LOCATION_PUSH_INTERVAL: u32 = 60;

unsafe fn transition(
    handle: Handle,
    slot: &'static trace::Slot,
    to: PluginState,
    label: &'static str,
) -> ApiError {
    // Traced before the handle is checked, so a transition the game attempted on a dead
    // context is visible rather than looking like the game never asked.
    slot.enter_with(label);
    with_ctx(handle, |c| {
        c.lock().plugin_state = to;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

/// Drains completions too, not just `OnUpdate`: `Sync` is the other entry point the game
/// calls from its own thread, and it is the one that runs while the game is backgrounded —
/// exactly when `OnUpdate` has stopped being called.
pub unsafe extern "C" fn sync(handle: Handle) -> ApiError {
    trace::SYNC.enter();
    with_ctx(handle, |c| {
        server::deliver_all(c, c.take_completions());
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

// --- device info -------------------------------------------------------------
//
// Results are interned in the Context on first use so the pointer stays stable for the
// context's lifetime. Managed reads them with Marshal.PtrToStringAnsi and never frees
// them, so returning a temporary here would be a use-after-free in the game.

pub unsafe extern "C" fn get_device_country_code(handle: Handle, out: *mut *const c_char) -> ApiError {
    device_string(handle, &trace::GET_DEVICE_COUNTRY_CODE, out, |s| &mut s.country_code, "US")
}

pub unsafe extern "C" fn get_device_language_code(handle: Handle, out: *mut *const c_char) -> ApiError {
    device_string(handle, &trace::GET_DEVICE_LANGUAGE_CODE, out, |s| &mut s.language_code, "en")
}

pub unsafe extern "C" fn get_device_timezone(handle: Handle, out: *mut *const c_char) -> ApiError {
    device_string(handle, &trace::GET_DEVICE_TIMEZONE, out, |s| &mut s.timezone, "UTC")
}

unsafe fn device_string(
    handle: Handle,
    trace_slot: &'static trace::Slot,
    out: *mut *const c_char,
    slot: impl FnOnce(&mut State) -> &mut Option<std::ffi::CString>,
    fallback: &str,
) -> ApiError {
    // Throttled rather than one-shot: these were silent before, so "the game never asked" and
    // "the game asks every frame and always gets the same interned pointer" were the same
    // absence in the log. The first call always prints, so a single call is still visible.
    trace_slot.enter_throttled(trace::PER_FRAME);
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    match with_ctx(handle, |c| {
        let mut s = c.lock();
        State::intern(slot(&mut s), fallback)
    }) {
        Ok(p) => {
            *out = p;
            ApiError::Success
        }
        Err(e) => e,
    }
}

// --- snitch log transport ----------------------------------------------------

pub unsafe extern "C" fn add_snitch_log(
    tag: *const c_char,
    group: *const c_char,
    lvl: i32,
    msg: *const c_char,
) -> ApiError {
    let tag = cstr_to_string(tag).unwrap_or_default();
    let group = cstr_to_string(group).unwrap_or_default();
    let msg = cstr_to_string(msg).unwrap_or_default();
    let level = LogLevelInternal::from_i32(lvl).unwrap_or(LogLevelInternal::Info);
    // Unthrottled: the game rate-limits its own snitch channel, and these lines are the ones
    // that carry the managed side's own account of why it stopped.
    trace::ADD_SNITCH_LOG.enter_with(level.as_str());
    log::snitch(&tag, &group, level.as_str(), &msg);
    ApiError::Success
}

pub unsafe extern "C" fn add_snitch_marker(msg: *const c_char) -> ApiError {
    let msg = cstr_to_string(msg).unwrap_or_default();
    log::info(&format!("marker: {msg}"));
    ApiError::Success
}

pub unsafe extern "C" fn begin_trace(label: *const c_char) -> ApiError {
    let label = cstr_to_string(label).unwrap_or_default();
    log::info(&format!("begin trace: {label}"));
    ApiError::Success
}

pub unsafe extern "C" fn end_trace() -> ApiError {
    ApiError::Success
}
