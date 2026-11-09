//! Entry tracing for the vtable slots.
//!
//! Most of the stub's log was `log_once`, which answers "did the game ever call this" but
//! goes silent afterwards — so a call that repeats forever looks exactly like a call that
//! happened once, and "the game is stuck" is indistinguishable from "the game finished".
//! This module logs every slot on entry with a global sequence number, so the last line in
//! logcat is literally the last thing the game asked for.
//!
//! Two rules keep that readable rather than a flood:
//!
//! * Slots the game polls every frame use [`Slot::enter_throttled`]. At ~60 Hz,
//!   `__android_log_print` per call would not just be noisy — it would push the interesting
//!   earlier lines out of logcat's ring buffer, which is the opposite of the point.
//! * Everything else logs unconditionally. Those are event-driven, so their call *rate* is
//!   itself a signal.
//!
//! [`summary`] renders a per-slot tally for the heartbeat line, which is how "it called 4000
//! frames and never once asked for an RPC" becomes visible without reading the whole log.

use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use crate::log;

/// Throttle for the slots the game polls every frame.
///
/// 60 is one line per second at the game's frame rate: frequent enough that the tail of the
/// log shows the poll is still happening right now, sparse enough that a minute of polling is
/// 60 lines rather than 3600.
pub const PER_FRAME: u32 = 60;

/// Monotonic across every slot, so lines from different slots interleave in real order.
/// Only consumed when a line is actually emitted, so the numbers stay contiguous.
static SEQ: AtomicU64 = AtomicU64::new(0);

fn seq() -> u64 {
    SEQ.fetch_add(1, Ordering::Relaxed) + 1
}

/// One vtable slot's call counter.
pub struct Slot {
    name: &'static str,
    count: AtomicU32,
}

impl Slot {
    pub const fn new(name: &'static str) -> Self {
        Self {
            name,
            count: AtomicU32::new(0),
        }
    }

    /// How many times this slot has been entered.
    pub fn calls(&self) -> u32 {
        self.count.load(Ordering::Relaxed)
    }

    pub fn name(&self) -> &'static str {
        self.name
    }

    /// Log the call, unthrottled. For event-driven slots.
    pub fn enter(&self) -> u32 {
        let n = self.count.fetch_add(1, Ordering::Relaxed) + 1;
        log::info(&format!("[{}] {}", seq(), self.name));
        n
    }

    /// Log the call with arguments.
    pub fn enter_with(&self, detail: &str) -> u32 {
        let n = self.count.fetch_add(1, Ordering::Relaxed) + 1;
        log::info(&format!("[{}] {}({})", seq(), self.name, detail));
        n
    }

    /// Log the first call, then every `every`th.
    ///
    /// The first call always logs: "it never ran at all" and "it ran 600 times before the
    /// throttle hid it" are the two cases this exists to tell apart, and a plain `% every`
    /// check reports both as silence for the first `every - 1` calls.
    pub fn enter_throttled(&self, every: u32) -> u32 {
        let n = self.count.fetch_add(1, Ordering::Relaxed) + 1;
        if n == 1 || (every != 0 && n % every == 0) {
            log::info(&format!("[{}] {} x{}", seq(), self.name, n));
        }
        n
    }
}

// Every vtable slot, in table order. Statics rather than a registry so a call is one
// relaxed add and a load, with no name lookup on the per-frame path.

pub static CREATE_CONTEXT: Slot = Slot::new("N2PluginCreateContext");
pub static DESTROY_CONTEXT: Slot = Slot::new("N2PluginDestroyContext");
pub static GET_DEVICE_COUNTRY_CODE: Slot = Slot::new("N2PluginGetDeviceCountryCode");
pub static GET_DEVICE_LANGUAGE_CODE: Slot = Slot::new("N2PluginGetDeviceLanguageCode");
pub static GET_DEVICE_TIMEZONE: Slot = Slot::new("N2PluginGetDeviceTimezone");
pub static GET_STATE: Slot = Slot::new("N2PluginGetState");
pub static ON_PAUSE: Slot = Slot::new("N2PluginOnPause");
pub static ON_RESUME: Slot = Slot::new("N2PluginOnResume");
pub static ON_START: Slot = Slot::new("N2PluginOnStart");
pub static ON_STOP: Slot = Slot::new("N2PluginOnStop");
pub static ON_UPDATE: Slot = Slot::new("N2PluginOnUpdate");
pub static SET_BACKGROUND_MODE: Slot = Slot::new("N2PluginSetBackgroundMode");
pub static SET_CALLBACKS: Slot = Slot::new("N2PluginSetCallbacks");
pub static SET_PRODUCT: Slot = Slot::new("N2PluginSetProduct");
pub static SET_USER_AGENT: Slot = Slot::new("N2PluginSetUserAgent");
pub static ENABLE_GLOBAL_HTTP3: Slot = Slot::new("N2PluginEnableGlobalHttp3");
pub static START_SNITCH: Slot = Slot::new("N2PluginStartSnitch");
pub static STOP_SNITCH: Slot = Slot::new("N2PluginStopSnitch");
pub static SYNC: Slot = Slot::new("N2PluginSync");
pub static GET_COMPASS_HEADING: Slot = Slot::new("N2MapGetCompassHeading");
pub static GET_CURRENT_LOCATION: Slot = Slot::new("N2MapGetCurrentLocation");
pub static REQUEST_AUTH_TOKEN: Slot = Slot::new("N2ServerRequestAuthToken");
pub static GENERATE_RPC_ID: Slot = Slot::new("N2ServerGenerateRpcId");
pub static SEND: Slot = Slot::new("N2ServerSend");
pub static SET_DEBUG_INFO: Slot = Slot::new("N2ServerSetDebugInfo");
pub static SEND_MAP_QUERY_REQUEST: Slot = Slot::new("N2ServerSendMapQueryRequest");
pub static SET_SERVER: Slot = Slot::new("N2ServerSetServer");
pub static TERMINATE_SESSION: Slot = Slot::new("N2ServerTerminateSession");
pub static DEBUG_SET_DEVICE_HEADING: Slot = Slot::new("N2DebugSetDeviceHeading");
pub static DEBUG_SET_DEVICE_LOCATION: Slot = Slot::new("N2DebugSetDeviceLocation");
pub static DEBUG_CANCEL_DEBUG_LOCATION: Slot = Slot::new("N2DebugCancelDebugLocation");
// Not a vtable slot, but on the same path into the plugin and worth counting with the rest.
pub static ADD_SNITCH_LOG: Slot = Slot::new("N2PluginAddSnitchLog");

/// Every slot, for [`summary`]. Kept beside the definitions so a new slot that is left out
/// of it is a visible omission rather than a silent one.
pub static ALL: &[&Slot] = &[
    &CREATE_CONTEXT,
    &DESTROY_CONTEXT,
    &GET_DEVICE_COUNTRY_CODE,
    &GET_DEVICE_LANGUAGE_CODE,
    &GET_DEVICE_TIMEZONE,
    &GET_STATE,
    &ON_PAUSE,
    &ON_RESUME,
    &ON_START,
    &ON_STOP,
    &ON_UPDATE,
    &SET_BACKGROUND_MODE,
    &SET_CALLBACKS,
    &SET_PRODUCT,
    &SET_USER_AGENT,
    &ENABLE_GLOBAL_HTTP3,
    &START_SNITCH,
    &STOP_SNITCH,
    &SYNC,
    &GET_COMPASS_HEADING,
    &GET_CURRENT_LOCATION,
    &REQUEST_AUTH_TOKEN,
    &GENERATE_RPC_ID,
    &SEND,
    &SET_DEBUG_INFO,
    &SEND_MAP_QUERY_REQUEST,
    &SET_SERVER,
    &TERMINATE_SESSION,
    &DEBUG_SET_DEVICE_HEADING,
    &DEBUG_SET_DEVICE_LOCATION,
    &DEBUG_CANCEL_DEBUG_LOCATION,
    &ADD_SNITCH_LOG,
];

/// `Name=n, Name=n, ...` for every slot that has been entered at least once.
///
/// Only non-zero slots: the full table is longer than any log line should be, and the
/// interesting information is which of them the game has *not* touched, which reads better as
/// an absence -- see [`never_called`] for that half.
pub fn summary() -> String {
    summary_of(ALL)
}

/// [`summary`] over an explicit slot list.
///
/// The list is a parameter so the formatting is testable: the slots in [`ALL`] are
/// process-global, and a test that entered one would change what every concurrently running
/// test sees.
pub fn summary_of(slots: &[&Slot]) -> String {
    let mut out = String::new();
    for slot in slots {
        let n = slot.calls();
        if n == 0 {
            continue;
        }
        if !out.is_empty() {
            out.push_str(", ");
        }
        out.push_str(slot.name());
        out.push('=');
        // Hand-rolled rather than `write!`: this is called from `OnUpdate`, and pulling
        // `core::fmt`'s machinery in for an integer is not worth the code size.
        out.push_str(&n.to_string());
    }
    if out.is_empty() {
        out.push_str("(none)");
    }
    out
}

/// Slots that have never been entered. The complement of [`summary`], named because "which
/// entry points is the game *not* using" is the question that finds a stalled boot.
pub fn never_called() -> String {
    never_called_of(ALL)
}

/// [`never_called`] over an explicit slot list. See [`summary_of`] for why.
pub fn never_called_of(slots: &[&Slot]) -> String {
    let mut out = String::new();
    for slot in slots {
        if slot.calls() != 0 {
            continue;
        }
        if !out.is_empty() {
            out.push_str(", ");
        }
        out.push_str(slot.name());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The throttle has to log call 1 or a slot that ran exactly once is invisible, which is
    /// the specific failure the trace exists to prevent.
    #[test]
    fn a_throttled_slot_still_reports_its_first_call() {
        static S: Slot = Slot::new("TestThrottled");
        assert_eq!(S.enter_throttled(10), 1);
        assert_eq!(S.calls(), 1);
        // Not 10 yet, so this one is silent -- but it still counts.
        assert_eq!(S.enter_throttled(10), 2);
        assert_eq!(S.calls(), 2);
    }

    #[test]
    fn every_slot_is_listed_exactly_once() {
        let mut names: Vec<&str> = ALL.iter().map(|s| s.name()).collect();
        let total = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), total, "a slot is listed in ALL twice");
    }

    /// The summary must not claim a slot ran when it has not, and the complement must name
    /// it -- those are the two halves of reading a stalled boot off the heartbeat line.
    #[test]
    fn the_summary_separates_called_from_never_called() {
        static USED: Slot = Slot::new("TestUsedOnlyHere");
        static FRESH: Slot = Slot::new("TestNeverTouched");
        let slots: &[&Slot] = &[&USED, &FRESH];
        USED.enter();

        let s = summary_of(slots);
        assert!(s.contains("TestUsedOnlyHere=1"), "got: {s}");
        assert!(!s.contains("TestNeverTouched"), "got: {s}");

        let n = never_called_of(slots);
        assert!(n.contains("TestNeverTouched"), "got: {n}");
        assert!(!n.contains("TestUsedOnlyHere"), "got: {n}");
    }

    /// A boot where nothing has been called yet must still produce a readable line rather
    /// than an empty string that reads as a formatting bug.
    #[test]
    fn an_empty_summary_says_so_rather_than_going_blank() {
        static UNTOUCHED: Slot = Slot::new("TestUntouchedForSummary");
        assert_eq!(summary_of(&[&UNTOUCHED]), "(none)");
        assert_eq!(never_called_of(&[&UNTOUCHED]), "TestUntouchedForSummary");
    }
}
