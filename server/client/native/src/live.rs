//! The last sensor fix, as delivered by the JNI natives in [`crate::jni::natives`].
//!
//! # Why this is process-global rather than per-`Context`
//!
//! `nativeLocationUpdate` and `nativeCompassUpdate` are *instance* methods on the Java
//! service objects, reached through `RegisterNatives`. They receive a `jobject` receiver
//! and scalars — **no plugin handle** — so unlike every `N2*` vtable slot they have no
//! `Context` to write into.
//!
//! The real library solves this by having constructed those Java objects itself, passing a
//! C++ `this` as the constructor's `long nativeClassPointer`, which each native then reads
//! back with `GetLongField`. The stub now *does* construct them
//! ([`crate::jni::location_service`], [06-object-model.md](../../notes/06-object-model.md))
//! — but with `nativeClassPointer = 0`, so that correlation still does not exist and one
//! process-wide slot remains the honest substitute. It is correct because exactly one
//! `N2PluginCreateContext` ever happens in the game (`DittoPlugin`'s constructor), so
//! there is only ever one context for a fix to belong to. `CreateBackgroundContext` no
//! longer returns null (see [`crate::bgcontext`]), but it does not disturb that: the object
//! it returns is not a plugin handle, never reaches [`crate::context::with_ctx`], and no
//! `N2*` slot ever receives it.
//!
//! # These bodies are now the live path
//!
//! `location_service` builds the `NianticLocationManager` and drives its lifecycle, so the
//! manager's providers register real Android listeners and [`set_location`] and
//! [`set_heading`] are reached. That was the missing link: until the chain was rebuilt no
//! Java object under `com/nianticlabs/nia/location` existed at all, `nativeLocationUpdate`
//! was never entered, and every read of the game's position fell through to the synthesized
//! origin. The `N2Debug*` slots are no longer the only reachable way to move
//! `N2MapGetCurrentLocation`.

use crate::abi::{CompassHeadingData, LocationData, PROVIDER_STATUS_UNDEFINED};
use core::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard};

/// Both values a sensor native can deliver, plus the status stream beside them.
struct Live {
    location: Option<LocationData>,
    heading: Option<CompassHeadingData>,
    /// The newest `ServiceStatus` a provider has reported, as its ordinal.
    ///
    /// Held separately from the fix because Java delivers the two on *different calls* — see
    /// [`set_status`].
    status: u32,
    /// Java reports each provider independently. A disabled GPS listener must
    /// not invalidate a running fused provider's measured fix.
    providers: Vec<(String, u32)>,
}

static LIVE: Mutex<Live> = Mutex::new(Live {
    location: None,
    heading: None,
    status: PROVIDER_STATUS_UNDEFINED,
    providers: Vec::new(),
});

/// True once the first fix of each kind has been logged.
static LOGGED_LOCATION: AtomicBool = AtomicBool::new(false);
static LOGGED_HEADING: AtomicBool = AtomicBool::new(false);

/// Lock the store, recovering from poisoning instead of panicking — the same rule as
/// [`crate::context::Context::lock`].
///
/// Lock order is always context-then-this: `map.rs` takes its `State` guard before calling
/// in here, while the natives take only this one. Nothing acquires them the other way
/// round, so the two cannot deadlock.
fn lock() -> MutexGuard<'static, Live> {
    LIVE.lock().unwrap_or_else(|e| e.into_inner())
}

/// Record a fix delivered by `nativeLocationUpdate`.
///
/// The status is merged rather than taken from `data`: a location-bearing call **always**
/// carries `UNDEFINED`, because the listener that reports a fix
/// (`NianticLocationManager$2.onProviderLocation`) has no status to report and hard-codes that
/// constant. Taking it at face value is what left the game reading `Status:Undefined` on a
/// position that was otherwise correct.
pub fn set_location(mut data: LocationData) {
    let mut l = lock();
    if data.provider_status == PROVIDER_STATUS_UNDEFINED {
        data.provider_status = l.status;
    } else {
        l.status = data.provider_status;
    }
    l.location = Some(data);
}

/// Record a status delivered by `nativeLocationUpdate` with no location.
///
/// Returns whether this *changed* the recorded status, so the caller can log the transition
/// without keeping its own copy. Returns `false` for `UNDEFINED`, which is not a status — see
/// below.
///
/// Java sends its two halves through one native and never together — the status always arrives
/// on a call with a null `Location`, and the location always arrives without one. In order, for
/// a provider that starts cleanly:
///
/// ```text
/// addLocationProvider  -> (name, null, UNDEFINED)    a provider was registered
/// onResume             -> (name, null, INITIALIZED)  requestLocationUpdates succeeded
/// first fix            -> (name, null, RUNNING)      AbstractNianticLocationProvider:397
/// every fix            -> (name, loc,  UNDEFINED)    the fix itself
/// ```
///
/// So the running status is only ever visible here, one call *before* the fix that needs it —
/// which is exactly why it has to be held rather than passed through.
#[cfg(test)]
pub fn set_status(status: u32) -> bool {
    let mut l = lock();
    // `UNDEFINED` is "no information", not a status. Java sends it when a provider is
    // registered and on every location-bearing call, so letting it through would wipe the
    // `RUNNING` that arrived moments earlier — the same bug, arriving from the other side.
    if status == PROVIDER_STATUS_UNDEFINED || status == l.status {
        return false;
    }
    l.status = status;
    // A fix already in hand carries the status as it was when it arrived, and the 1 Hz push
    // relays it from here. Rewriting it is what turns `onPause`'s `STOPPED` into something the
    // game can see before the next real fix shows up — or before there is one at all.
    if let Some(loc) = l.location.as_mut() {
        loc.provider_status = status;
    }
    true
}

/// Aggregate provider lifecycle reports, retaining RUNNING while any provider
/// is running. Return the per-provider change for transition-only logging.
pub fn set_provider_status(provider: &str, status: u32) -> bool {
    if status == PROVIDER_STATUS_UNDEFINED {
        return false;
    }
    let mut l = lock();
    if let Some(entry) = l.providers.iter_mut().find(|entry| entry.0 == provider) {
        if entry.1 == status {
            return false;
        }
        entry.1 = status;
    } else {
        l.providers.push((provider.to_owned(), status));
    }
    let effective = if l.providers.iter().any(|entry| entry.1 == 3) {
        3
    } else if l.providers.iter().any(|entry| entry.1 == 2) {
        2
    } else {
        status
    };
    l.status = effective;
    if let Some(fix) = l.location.as_mut() {
        fix.provider_status = effective;
    }
    true
}

/// Record a heading delivered by `nativeCompassUpdate`.
pub fn set_heading(data: CompassHeadingData) {
    lock().heading = Some(data);
}

/// The last fix, if a native has delivered one.
pub fn location() -> Option<LocationData> {
    lock().location
}

/// The last heading, if a native has delivered one.
pub fn heading() -> Option<CompassHeadingData> {
    lock().heading
}

/// True the first time it is called, false afterwards.
///
/// The per-`Context` gate is [`crate::context::State::log_once`], which is unavailable here
/// for the same reason there is no `Context`. These natives are hot — the compass fires at
/// the sensor rate, not the frame rate — so the first-call line is the only one worth
/// paying for.
pub fn claim_first_location_log() -> bool {
    !LOGGED_LOCATION.swap(true, Ordering::AcqRel)
}

/// See [`claim_first_location_log`].
pub fn claim_first_heading_log() -> bool {
    !LOGGED_HEADING.swap(true, Ordering::AcqRel)
}

/// Drop everything recorded. Test-only: the store is a process-wide `static`, so a test
/// that writes it has to start from a known state.
#[cfg(test)]
pub fn reset() {
    let mut l = lock();
    l.location = None;
    l.heading = None;
    l.status = PROVIDER_STATUS_UNDEFINED;
    l.providers.clear();
    LOGGED_LOCATION.store(false, Ordering::Release);
    LOGGED_HEADING.store(false, Ordering::Release);
}

/// Serialize every test that touches this store, or reads it back through
/// [`crate::map`].
///
/// Cargo runs the tests in one binary on several threads, and this store is a process-wide
/// `static` — so without a gate a live fix written by one test would be visible to another
/// asserting `PluginNotReady`. That is a flaky failure with no connection to the code under
/// test, which is the worst kind.
///
/// Hold it for the whole test body, and take it *before* [`reset`]:
///
/// ```ignore
/// let _g = live::test_guard();
/// live::reset();
/// ```
///
/// Recovering from poisoning, like [`lock`] — a failing test should report its own
/// assertion, not turn every later test into a poison panic.
#[cfg(test)]
pub fn test_guard() -> MutexGuard<'static, ()> {
    // The lock is the harness's, not this module's: the `natives` tests take this guard *and*
    // drive the fake JVM, so a mutex private to `live` would serialize them against each other
    // and against nothing else — leaving them free to race the location chain's tests over the
    // shared `unexpected_calls` counter. See [`crate::jni::fake_jni::test_guard`].
    crate::jni::fake_jni::test_guard()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::abi::LOCATION_TYPE_MEASURED;

    /// `ServiceStatus` ordinals, as Java's `ordinal()` sends them.
    const STOPPED: u32 = 1;
    const INITIALIZED: u32 = 2;
    const RUNNING: u32 = 3;

    fn fix(status: u32) -> LocationData {
        LocationData {
            latitude_deg: 1.0,
            longitude_deg: 2.0,
            altitude_m: 3.0,
            accuracy_m: 4.0,
            timestamp_s: 5.0,
            provider_status: status,
            location_type: LOCATION_TYPE_MEASURED,
        }
    }

    #[test]
    fn disabled_gps_does_not_invalidate_running_fused_fix() {
        let _g = test_guard();
        reset();
        set_provider_status("fused", RUNNING);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        set_provider_status("gps", 4);
        assert_eq!(location().unwrap().provider_status, RUNNING);
        set_provider_status("fused", STOPPED);
        assert_eq!(location().unwrap().provider_status, STOPPED);
    }

    #[test]
    fn secondary_initialization_preserves_running_provider() {
        let _g = test_guard();
        reset();
        set_provider_status("fused", RUNNING);
        set_provider_status("gps", INITIALIZED);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, RUNNING);
        set_provider_status("fused", STOPPED);
        assert_eq!(location().unwrap().provider_status, INITIALIZED);
        set_provider_status("gps", STOPPED);
        assert_eq!(location().unwrap().provider_status, STOPPED);
    }

    #[test]
    fn undefined_provider_status_and_reset_do_not_leak_state() {
        let _g = test_guard();
        reset();
        assert!(set_provider_status("fused", RUNNING));
        assert!(!set_provider_status("fused", PROVIDER_STATUS_UNDEFINED));
        assert!(!set_provider_status("fused", RUNNING));
        reset();
        set_provider_status("gps", STOPPED);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, STOPPED);
    }

    /// The bug this whole mechanism exists for. Java's location-bearing call *always* carries
    /// `UNDEFINED` — the listener that reports a fix has no status to report — so taking the
    /// value at face value is what left the game reading `Status:Undefined` on a position
    /// whose coordinates were perfect.
    #[test]
    fn a_fix_inherits_the_status_reported_on_its_own_call() {
        let _g = test_guard();
        reset();
        set_status(RUNNING);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, RUNNING);
    }

    /// `UNDEFINED` is the absence of a status, not a status. Java sends it when a provider is
    /// registered, so letting it through would wipe a `RUNNING` that arrived moments earlier —
    /// the same bug arriving from the other direction.
    #[test]
    fn undefined_never_overwrites_a_real_status() {
        let _g = test_guard();
        reset();
        assert!(set_status(RUNNING), "a real status is a change");
        assert!(!set_status(PROVIDER_STATUS_UNDEFINED), "and this is not");
        assert!(!set_status(RUNNING), "nor is a repeat of what is already held");
        // With no fix held, the refusal must not have left a stale one behind either.
        assert!(location().is_none());
        // The observable consequence: a fix arriving the ordinary way still comes out RUNNING.
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, RUNNING);
    }

    /// `onPause` reports `STOPPED` on a call with no location. The fix already in hand has to
    /// pick it up, or the game goes on being told `Running` about a provider that stopped.
    #[test]
    fn a_later_status_rewrites_the_fix_already_held() {
        let _g = test_guard();
        reset();
        set_status(INITIALIZED);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, INITIALIZED);

        set_status(RUNNING);
        assert_eq!(location().unwrap().provider_status, RUNNING);

        set_status(STOPPED);
        assert_eq!(location().unwrap().provider_status, STOPPED);
    }

    /// A fix that *does* carry a status keeps it, and that status becomes the one later
    /// `UNDEFINED` fixes inherit. Nothing in Java does this today, but the merge must not
    /// silently discard a status just because it arrived the ordinary way.
    #[test]
    fn a_fix_carrying_its_own_status_wins_and_is_then_inherited() {
        let _g = test_guard();
        reset();
        set_status(RUNNING);
        set_location(fix(STOPPED));
        assert_eq!(location().unwrap().provider_status, STOPPED);

        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(location().unwrap().provider_status, STOPPED);
    }

    /// Both stores are process-wide, so a test that leaves a status behind would hand it to
    /// whichever test runs next.
    #[test]
    fn reset_clears_the_status() {
        let _g = test_guard();
        reset();
        set_status(RUNNING);
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        reset();
        assert!(location().is_none());
        set_location(fix(PROVIDER_STATUS_UNDEFINED));
        assert_eq!(
            location().unwrap().provider_status,
            PROVIDER_STATUS_UNDEFINED,
            "a status from before the reset must not survive it",
        );
    }
}
