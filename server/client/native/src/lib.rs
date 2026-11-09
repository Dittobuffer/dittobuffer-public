//! Rust stub reimplementation of `libNianticLabsPlugin.so`.
//!
//! The original library exposes exactly five C symbols (see `notes/02-exported-symbols.md`):
//! `GetN2Api`, `Java_com_nianticlabs_nia_unity_UnityUtil_nativeInit`,
//! `CreateBackgroundContext`, `UnityPluginLoad` and `UnityPluginUnload`.
//!
//! `[lib] name = "NianticLabsPlugin"` in `Cargo.toml` makes cargo emit
//! `libNianticLabsPlugin.so`, matching the original SONAME so that
//! `System.loadLibrary("NianticLabsPlugin")` resolves our build.

// `[lib] name = "NianticLabsPlugin"` is deliberately not snake_case: it is what makes
// cargo emit `libNianticLabsPlugin.so`, matching the original SONAME that
// `System.loadLibrary("NianticLabsPlugin")` resolves.
#![allow(non_snake_case)]

mod abi;
mod bgcontext;
mod context;
mod debug;
mod inspect;
mod jni;
mod live;
mod log;
mod map;
mod n2api;
mod plugin;
mod server;
mod trace;

pub use n2api::{GetN2Api, N2NativeBindings, N2_API};

use core::ffi::c_void;
use jni_sys::{jclass, jint, jobject, JNIEnv, JavaVM, JNI_VERSION_1_6};

/// `UnityUtil.nativeInit()` — registered by the JVM under this mangled name, and also
/// registered explicitly by [`register::register_all`](jni::register::register_all), so
/// `RegisterNatives` wins over mangled lookup and both routes land here.
///
/// This is where the original registers its 19 natives; it does not export a
/// `JNI_OnLoad` (notes/02).
///
/// # Safety
/// `env` must be a valid `JNIEnv*` and `class` a valid `jclass`.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn Java_com_nianticlabs_nia_unity_UnityUtil_nativeInit(
    env: *mut JNIEnv,
    _class: jclass,
) {
    jni::register::register_all(env);
}

/// Registers the natives as early as possible, at `System.loadLibrary` time.
///
/// **The original does not export this symbol.** It is an intentional addition: the
/// four background-worker classes also `loadLibrary("NianticLabsPlugin")` (notes/09), so
/// ordering against `UnityUtil.<clinit>` is not guaranteed, and registering here removes
/// that dependency. It is purely additive — the JVM only calls it if it is present.
///
/// This must not leave a pending exception; doing so makes the enclosing
/// `System.loadLibrary` throw. `register_all` clears every exception it can raise.
///
/// # Safety
/// `vm` must be the `JavaVM*` the JVM supplies.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn JNI_OnLoad(vm: *mut JavaVM, _reserved: *mut c_void) -> jint {
    // Cached before anything else can need it, and before the early return below: the 36
    // vtable slots receive no `JNIEnv`, so this is the one chance to keep hold of a way to
    // reach the VM for the rest of the session. Rebuilding the location chain depends on it.
    jni::set_vm(vm);
    let env = jni::env_from_vm(vm);
    if env.is_null() {
        log::info("JNI_OnLoad: GetEnv failed, deferring registration to nativeInit");
        return JNI_VERSION_1_6;
    }
    // The banner is the answer to "did the game load *our* library", which is otherwise
    // only inferable from logcat by the absence of a crash. Both facts it names are the ones
    // that matter when the game starts behaving unexpectedly during bring-up.
    log::info(&format!(
        "JNI_OnLoad: Rust stub 0.1.0, N2Server* -> {}",
        server::DEV_BASE
    ));
    jni::register::register_all(env);
    JNI_VERSION_1_6
}

/// Allocates the background (Adventure Sync) context.
///
/// No *managed* caller exists in the dumps, but a native one does: `libadventuresync.so`
/// imports this symbol and calls it from `sub_7660f0`, then dereferences the result
/// without a null check and calls its vtable slot `+0x18`. Returning null — what this
/// stub used to do — left that caller's cache at `arg1[0x18]` null, so its own
/// `cbz` guard never latched and *every* call faulted again.
///
/// The real object model is small and is reproduced in `src/bgcontext.rs`, which is where
/// the vtable layout and the `x8`-based return convention are documented.
///
/// # Safety
/// `env` must be a valid `JNIEnv*`; it is used to cache the `JavaVM`. `bg_token`, when
/// non-null, is a `jobject` in `env` — it is promoted to a global reference, exactly as the
/// original does, so it must be a reference the VM recognises. `create` touches no field of
/// it and makes no method call on it; see the deviations block in `src/bgcontext.rs` for the
/// one Java-side operation this does *not* reproduce.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn CreateBackgroundContext(
    env: *mut JNIEnv,
    bg_token: *mut c_void,
) -> *mut c_void {
    bgcontext::create(env, bg_token as jobject)
}

/// Standard Unity native-plugin entry point. The original ignores its argument.
///
/// # Safety
/// Argument is ignored.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn UnityPluginLoad(_unity_interfaces: *mut c_void) {}

/// Standard Unity native-plugin teardown.
///
/// # Safety
/// Takes no arguments and touches no state.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn UnityPluginUnload() {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jni::location_service;
    use core::mem::{align_of, offset_of, size_of};

    type B = n2api::N2NativeBindings;

    /// The `memcpy` in the original copies 0x120 bytes: 36 slots x 8.
    #[test]
    fn vtable_is_288_bytes() {
        assert_eq!(size_of::<B>(), 0x120);
        assert_eq!(size_of::<B>(), 288);
        assert_eq!(align_of::<B>(), 8);
    }

    /// Offsets are the ABI: the managed side reads this struct by fixed offset, so a
    /// field reordered here silently swaps two entry points at runtime.
    #[test]
    fn vtable_offsets_match_managed_struct() {
        assert_eq!(offset_of!(B, n2_plugin_create_context), 0x00);
        assert_eq!(offset_of!(B, n2_plugin_destroy_context), 0x08);
        assert_eq!(offset_of!(B, n2_plugin_add_snitch_log), 0x10);
        assert_eq!(offset_of!(B, n2_plugin_add_snitch_marker), 0x18);
        assert_eq!(offset_of!(B, n2_plugin_begin_trace), 0x20);
        assert_eq!(offset_of!(B, n2_plugin_defer_location_permission_request), 0x28);
        assert_eq!(offset_of!(B, n2_plugin_end_trace), 0x30);
        assert_eq!(offset_of!(B, n2_plugin_get_device_country_code), 0x38);
        assert_eq!(offset_of!(B, n2_plugin_get_device_language_code), 0x40);
        assert_eq!(offset_of!(B, n2_plugin_get_device_timezone), 0x48);
        assert_eq!(offset_of!(B, n2_plugin_get_state), 0x50);
        assert_eq!(offset_of!(B, n2_plugin_on_pause), 0x58);
        assert_eq!(offset_of!(B, n2_plugin_on_resume), 0x60);
        assert_eq!(offset_of!(B, n2_plugin_on_start), 0x68);
        assert_eq!(offset_of!(B, n2_plugin_on_stop), 0x70);
        assert_eq!(offset_of!(B, n2_plugin_on_update), 0x78);
        assert_eq!(offset_of!(B, n2_plugin_set_background_mode), 0x80);
        assert_eq!(offset_of!(B, n2_plugin_set_callbacks), 0x88);
        assert_eq!(offset_of!(B, n2_plugin_set_product), 0x90);
        assert_eq!(offset_of!(B, n2_plugin_set_user_agent), 0x98);
        assert_eq!(offset_of!(B, n2_plugin_enable_global_http3), 0xA0);
        assert_eq!(offset_of!(B, n2_plugin_start_snitch), 0xA8);
        assert_eq!(offset_of!(B, n2_plugin_stop_snitch), 0xB0);
        assert_eq!(offset_of!(B, n2_plugin_sync), 0xB8);
        assert_eq!(offset_of!(B, n2_map_get_compass_heading), 0xC0);
        assert_eq!(offset_of!(B, n2_map_get_current_location), 0xC8);
        assert_eq!(offset_of!(B, n2_server_request_auth_token), 0xD0);
        assert_eq!(offset_of!(B, n2_server_generate_rpc_id), 0xD8);
        assert_eq!(offset_of!(B, n2_server_send), 0xE0);
        assert_eq!(offset_of!(B, n2_server_set_debug_info), 0xE8);
        assert_eq!(offset_of!(B, n2_server_send_map_query_request), 0xF0);
        assert_eq!(offset_of!(B, n2_server_set_server), 0xF8);
        assert_eq!(offset_of!(B, n2_server_terminate_session), 0x100);
        assert_eq!(offset_of!(B, n2_debug_set_device_heading), 0x108);
        assert_eq!(offset_of!(B, n2_debug_set_device_location), 0x110);
        assert_eq!(offset_of!(B, n2_debug_cancel_debug_location), 0x118);
        assert_eq!(offset_of!(B, n2_debug_cancel_debug_location), size_of::<B>() - 8);
    }

    /// The original scratch notes had 0xE8/0xF0 transposed. This is the single easiest
    /// mistake to reintroduce.
    #[test]
    fn debug_info_and_map_query_are_not_transposed() {
        assert_eq!(offset_of!(B, n2_server_set_debug_info), 0xE8);
        assert_eq!(offset_of!(B, n2_server_send_map_query_request), 0xF0);
        assert!(
            offset_of!(B, n2_server_set_debug_info) < offset_of!(B, n2_server_send_map_query_request)
        );
    }

    #[test]
    fn payload_struct_sizes_match_managed_layout() {
        assert_eq!(size_of::<abi::SessionOptionsData>(), 4);
        assert_eq!(size_of::<abi::CompassHeadingData>(), 0x10);
        assert_eq!(size_of::<abi::LocationData>(), 0x30);
    }

    /// These two are built by hand on every callback, so they are the last place a layout
    /// error would be caught by anything other than a crash inside the game.
    #[test]
    fn response_structs_match_marshalling_notes() {
        use abi::{PayloadArr, RpcRes};
        assert_eq!(size_of::<PayloadArr>(), 0x10);
        // `size` at 8, not 4 — the pointer aligns the struct, so 4 bytes of padding follow it.
        assert_eq!(offset_of!(PayloadArr, data), 0x00);
        assert_eq!(offset_of!(PayloadArr, size), 0x08);

        assert_eq!(size_of::<RpcRes>(), 0x18);
        assert_eq!(offset_of!(RpcRes, rpc_id), 0x00);
        assert_eq!(offset_of!(RpcRes, status), 0x04);
        assert_eq!(offset_of!(RpcRes, payloads), 0x08);
        assert_eq!(offset_of!(RpcRes, payload_count), 0x10);
    }

    /// `Success` is 1 and value 2 is absent from the enum — the easiest pair to get wrong,
    /// and the failure would be silent: a reply labelled with the wrong status is still a
    /// well-formed buffer.
    #[test]
    fn rpc_status_values_match_the_enum() {
        assert_eq!(abi::RPC_STATUS_SUCCESS, 1);
        assert_eq!(abi::RPC_STATUS_SERVER_ERROR, 6);
    }

    /// Plain function pointers, so non-null is guaranteed by construction. Assert it
    /// anyway: a future refactor to `Option<fn>` would make null representable.
    #[test]
    fn get_n2_api_fills_all_36_slots_with_non_null_pointers() {
        let mut buf = [0usize; 36];
        unsafe { GetN2Api(buf.as_mut_ptr() as *mut N2NativeBindings) };
        for (i, &p) in buf.iter().enumerate() {
            assert_ne!(p, 0, "vtable slot {i} is null");
        }
    }

    #[test]
    fn get_n2_api_tolerates_null() {
        unsafe { GetN2Api(core::ptr::null_mut()) };
    }

    #[test]
    fn context_lifecycle_reports_active_after_start_and_update() {
        use abi::{ApiError, PluginState};
        // `on_start` builds the Java location chain and `destroy_context` tears it down, so
        // this shares `location_service`'s process-global manager with its own tests.
        let _g = location_service::test_guard();
        unsafe {
            let mut h: abi::Handle = core::ptr::null_mut();
            assert!(matches!(plugin::create_context(&mut h), ApiError::Success));
            assert!(!h.is_null());

            let mut state: i32 = -1;
            assert!(matches!(plugin::get_state(h, &mut state), ApiError::Success));
            assert_eq!(state, PluginState::Init as i32);

            assert!(matches!(plugin::on_start(h), ApiError::Success));
            assert!(matches!(plugin::on_update(h), ApiError::Success));

            assert!(matches!(plugin::get_state(h, &mut state), ApiError::Success));
            assert_eq!(state, PluginState::Active as i32);

            assert!(matches!(plugin::destroy_context(h), ApiError::Success));
        }
    }

    #[test]
    fn null_handles_are_rejected_not_dereferenced() {
        use abi::{ApiError, Handle};
        let null: Handle = core::ptr::null_mut();
        unsafe {
            let mut state = 0i32;
            assert!(matches!(plugin::get_state(null, &mut state), ApiError::InvalidParameters));
            assert!(matches!(plugin::on_update(null), ApiError::InvalidParameters));
            assert!(matches!(plugin::destroy_context(null), ApiError::InvalidParameters));
            assert!(matches!(map::get_current_location(null, core::ptr::null_mut()), ApiError::InvalidParameters));
        }
    }

    /// `out IntPtr` results are interned, so repeated calls must return the same pointer.
    #[test]
    fn device_strings_are_stable_across_calls() {
        let _g = location_service::test_guard();
        use abi::Handle;
        use core::ffi::c_char;
        unsafe {
            let mut h: Handle = core::ptr::null_mut();
            plugin::create_context(&mut h);

            let mut a: *const c_char = core::ptr::null();
            let mut b: *const c_char = core::ptr::null();
            assert!(matches!(plugin::get_device_country_code(h, &mut a), abi::ApiError::Success));
            assert!(matches!(plugin::get_device_country_code(h, &mut b), abi::ApiError::Success));
            assert_eq!(a, b, "interned string pointer must not move between calls");
            assert!(!a.is_null());

            plugin::destroy_context(h);
        }
    }

    /// The debug setters must be readable back through the N2Map getters.
    #[test]
    fn debug_location_round_trips_through_map_getter() {
        use abi::{ApiError, CompassHeadingData, Handle, LocationData};
        // The map getters fall back to the process-global live store, and `destroy_context`
        // tears down the Java location chain. Both are guarded, and both guards are now the
        // harness's single lock — so one acquisition covers the pair. A second one would
        // deadlock rather than merely be redundant.
        let _g = live::test_guard();
        live::reset();
        unsafe {
            let mut h: Handle = core::ptr::null_mut();
            plugin::create_context(&mut h);

            // With no fix, the getter still succeeds -- on a synthesized origin -- rather
            // than returning PluginNotReady. The managed binding throws on that error, so
            // the game would stop before its map ever loaded; see `map::no_fix`.
            let mut loc = LocationData::default();
            assert!(matches!(map::get_current_location(h, &mut loc), ApiError::Success));
            assert_eq!((loc.latitude_deg, loc.longitude_deg), (0.0, 0.0));
            assert_eq!(loc.provider_status, abi::PROVIDER_STATUS_INITIALIZED);

            assert!(matches!(debug::set_device_location(h, 1.5, 2.5, 3.0, 4.0, 3, 1), ApiError::Success));
            assert!(matches!(map::get_current_location(h, &mut loc), ApiError::Success));
            assert_eq!(loc.latitude_deg, 1.5);
            assert!(loc.timestamp_s > 1_600_000_000.0);
            assert_eq!(loc.longitude_deg, 2.5);
            assert_eq!(loc.provider_status, 3);

            let mut heading = CompassHeadingData::default();
            assert!(matches!(debug::set_device_heading(h, 42.0), ApiError::Success));
            assert!(matches!(map::get_compass_heading(h, &mut heading), ApiError::Success));
            assert_eq!(heading.heading_deg, 42.0);

            // Cancelling falls back to the live store, which is empty here -- so back to
            // the synthesized origin, and the stale debug position must not linger.
            assert!(matches!(debug::cancel_debug_location(h), ApiError::Success));
            assert!(matches!(map::get_current_location(h, &mut loc), ApiError::Success));
            assert_eq!((loc.latitude_deg, loc.longitude_deg), (0.0, 0.0));
            assert_eq!(loc.provider_status, abi::PROVIDER_STATUS_INITIALIZED);

            plugin::destroy_context(h);
        }
    }

    /// The game is *told* its position through a `locationUpdate` event and can also ask for it
    /// through `N2MapGetCurrentLocation`. Those two answers have to agree, or the player
    /// visibly jumps between the pushed and the polled value.
    ///
    /// The timestamp is the deliberate exception — both sides stamp "now" when they build the
    /// value, and a fix that is fresh at each call is the point.
    #[test]
    fn the_synthesized_fix_is_dated_now_and_matches_the_getter() {
        use abi::{ApiError, Handle, LocationData};
        // One guard, not two: [`live::test_guard`] and [`location_service::test_guard`] are the
        // same lock now, so taking both deadlocks rather than merely being redundant.
        let _g = live::test_guard();
        live::reset();
        unsafe {
            let mut h: Handle = core::ptr::null_mut();
            plugin::create_context(&mut h);

            let mut polled = LocationData::default();
            assert!(matches!(map::get_current_location(h, &mut polled), ApiError::Success));
            let polled = abi::location_payload(&polled);

            // Built exactly as `on_update` builds it.
            let pushed = abi::location_payload(&map::no_fix());

            assert_eq!(pushed.len(), abi::payload_width(abi::EventSlot::LocationUpdate));
            // Position (four doubles) agrees...
            assert_eq!(&pushed[0x00..0x20], &polled[0x00..0x20]);
            // ...as do provider status and location type.
            assert_eq!(&pushed[0x28..0x30], &polled[0x28..0x30]);

            // And the timestamp is a real one rather than the 1970 a zeroed field reads as.
            // That zero is the bug this pins: a fix claiming to be 56 years old is stale on
            // arrival, so the managed side treats it as no usable position at all -- which is
            // the same trap `no_fix`'s `provider_status` comment already covers for the status.
            let ts = f64::from_le_bytes(pushed[0x20..0x28].try_into().expect("8 bytes"));
            assert!(ts > 1_600_000_000.0, "synthesized fix must be dated now, got {ts}");

            plugin::destroy_context(h);
        }
    }

    /// Both event payloads are read by managed as a fixed-size struct at offset 0. A length
    /// that disagrees means managed reads past our buffer into whatever follows it, which is
    /// a garbage `LocationData` at best and an unmapped read at worst.
    #[test]
    fn event_payloads_match_the_structs_managed_reads_them_as() {
        let loc = abi::LocationData {
            latitude_deg: 1.0,
            longitude_deg: 2.0,
            altitude_m: 3.0,
            accuracy_m: 4.0,
            timestamp_s: 5.0,
            provider_status: abi::PROVIDER_STATUS_RUNNING,
            location_type: abi::LOCATION_TYPE_MEASURED,
        };
        let bytes = abi::location_payload(&loc);
        // `Geo.Location` is a single `LocationData` at native offset 0, so the buffer is
        // exactly the struct -- the same 0x30 the `N2Map*` getters marshal.
        assert_eq!(bytes.len(), size_of::<abi::LocationData>());
        assert_eq!(bytes.len(), 0x30);

        // Field for field, in declaration order, little-endian: the layout `Ditto.cs`
        // declares (five doubles then two u32s) and the one `abi::LocationData` already has.
        assert_eq!(&bytes[0x00..0x08], &1.0f64.to_le_bytes());
        assert_eq!(&bytes[0x08..0x10], &2.0f64.to_le_bytes());
        assert_eq!(&bytes[0x10..0x18], &3.0f64.to_le_bytes());
        assert_eq!(&bytes[0x18..0x20], &4.0f64.to_le_bytes());
        assert_eq!(&bytes[0x20..0x28], &5.0f64.to_le_bytes());
        assert_eq!(&bytes[0x28..0x2c], &abi::PROVIDER_STATUS_RUNNING.to_le_bytes());
        assert_eq!(&bytes[0x2c..0x30], &abi::LOCATION_TYPE_MEASURED.to_le_bytes());

        // `MarshallableChannelAuthEventParams` is one int32, and the enum value is the
        // payload -- 3 is `GotAuthToken`.
        let auth = abi::channel_auth_payload(abi::NativeChannelAuthEvent::GotAuthToken);
        assert_eq!(auth, vec![3, 0, 0, 0]);
        assert_eq!(auth.len(), size_of::<i32>());

        // The builders and the widths describe the same two buffers, and `deliver_event`
        // drops anything shorter than the width. So a change to one that the other does not
        // follow would silently stop the event being delivered at all -- assert they agree.
        assert_eq!(bytes.len(), abi::payload_width(abi::EventSlot::LocationUpdate));
        assert_eq!(auth.len(), abi::payload_width(abi::EventSlot::ChannelAuth));
    }

    /// The slot index *is* the ABI: it indexes `Callbacks.events`, which is the order
    /// `PluginBindings.SetCallbacks` passes the six handles in. Getting one wrong fires a
    /// payload at a receiver that will read it as a different struct.
    #[test]
    fn event_slot_discriminants_match_the_set_callbacks_order() {
        assert_eq!(abi::EventSlot::ChannelAuth as usize, 0);
        assert_eq!(abi::EventSlot::LocationUpdate as usize, 1);
        // Six handles arrive; naming two of them must not shrink the array the other four
        // are indexed out of.
        assert_eq!(context::Callbacks::default().events.len(), 6);
    }

    #[test]
    fn rpc_ids_are_distinct_and_incrementing() {
        let _g = location_service::test_guard();
        use abi::{ApiError, Handle};
        unsafe {
            let mut h: Handle = core::ptr::null_mut();
            plugin::create_context(&mut h);
            let (mut a, mut b) = (0i32, 0i32);
            assert!(matches!(server::generate_rpc_id(h, &mut a), ApiError::Success));
            assert!(matches!(server::generate_rpc_id(h, &mut b), ApiError::Success));
            assert_ne!(a, b);
            assert_eq!(b, a + 1);
            plugin::destroy_context(h);
        }
    }

    /// The smoke test in notes/10-verification.md reads "how far did the game get" off
    /// these lines, so a gate that stopped gating would flood logcat, and one that never
    /// fired would silence the only evidence the stub produces.
    #[test]
    fn log_once_gates_repeats_per_label() {
        let ctx = context::Context::new();
        let mut s = ctx.lock();
        s.log_once("A");
        s.log_once("A");
        s.log_once("B");
        s.log_once("A");
        assert_eq!(s.seen.len(), 2);
        assert!(s.seen.contains("A") && s.seen.contains("B"));
    }
}
