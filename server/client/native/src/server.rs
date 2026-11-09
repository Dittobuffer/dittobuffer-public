//! Vtable slots 26-32: auth, RPC ids, send, session.
//!
//! These slots forward the game's traffic to a local dev server (`tools/devserver.py`)
//! instead of to Niantic. The point is not to emulate a backend — it is to **observe what
//! the game actually asks for**: method ids, payload sizes, ordering, the auth handshake.
//! The server is the instrument; this file is the pipe.
//!
//! # Threading
//!
//! `N2ServerSend` is called on the game's thread and must return promptly, so the HTTP round
//! trip happens on a worker thread and the reply is queued for delivery on the next
//! `N2PluginOnUpdate`. Delivery *must* happen on a thread the game called us on: invoking a
//! delegate re-enters the plugin, and the game's own thread is already inside the IL2CPP
//! runtime where that is legal. See [`crate::context::Completion`] for why the queue is a
//! separate `Arc<Mutex<_>>` rather than a field on the context's state.
//!
//! # Panics
//!
//! Release builds set `panic = "abort"`, so an unwind on a worker thread takes the game
//! process with it — and `catch_unwind` cannot intercept an abort, so it buys nothing here.
//! Everything below is written to be total instead: no indexing, no `unwrap`, no arithmetic
//! that can trap, and every allocation sized from a value that was bounds-checked first.

use crate::abi::*;
use crate::context::{with_ctx, Completion, Context};
use crate::log;
use crate::trace;
use core::ffi::{c_char, c_void};
use core::sync::atomic::{AtomicUsize, Ordering};
use prost::Message as _;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

/// The dev-channel message types, generated from `tools/devrpc.proto` by `build.rs`.
///
/// Only the envelope lives here. The game's payload schemas are in `tools/vbase.proto` and
/// stay on the dev server, because the stub never looks inside a body — it copies bytes in
/// and copies bytes out. Compiling 3351 message definitions into a library injected into the
/// game process would cost real size and build time and buy nothing.
mod devrpc {
    include!(concat!(env!("OUT_DIR"), "/devrpc.rs"));
}

/// The dev server. Hardcoded for bring-up: `N2ServerSetServer` does arrive from managed code
/// with Niantic's real endpoint, and we log it, but routing deliberately does not follow it.
///
/// `pub(crate)` so `JNI_OnLoad` can name it in the startup banner.
pub(crate) const DEV_BASE: &str = "http://127.0.0.1:8080";

const ENDPOINT_FILE: &str = "/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-server.url";
const VPN_MARKER: &str = "/data/user/0/org.librenlp.pokemongo.dev/files/dittobuffer-vpn-only";

#[cfg(test)]
mod endpoint_tests {
    use super::validated_endpoint;
    #[test]
    fn accepts_only_explicit_private_or_verified_tls_routes() {
        assert!(validated_endpoint("http://127.0.0.1:8080", false).is_some());
        assert!(validated_endpoint("https://family.example.org", false).is_some());
        assert!(validated_endpoint("http://100.90.1.2:8080", true).is_some());
        for url in ["http://192.168.1.2:8080", "http://example.org", "https://user:secret@example.org", "https://pgorelease.nianticlabs.com", "https://example.org/x", "https://example.org?token=x"] {
            assert!(validated_endpoint(url, false).is_none(), "{url}");
        }
        assert!(validated_endpoint("http://8.8.8.8", true).is_none());
    }
}

fn validated_endpoint(value: &str, vpn: bool) -> Option<String> {
    let url = reqwest::Url::parse(value.trim()).ok()?;
    if !url.username().is_empty() || url.password().is_some() || url.query().is_some()
        || url.fragment().is_some() || url.path() != "/" { return None; }
    let host = url.host_str()?;
    if host == "nianticlabs.com" || host.ends_with(".nianticlabs.com")
        || host == "pokemongolive.com" || host.ends_with(".pokemongolive.com") { return None; }
    let address = host.trim_matches(['[',']']).parse::<std::net::IpAddr>().ok();
    let loopback = address.is_some_and(|v| v.is_loopback());
    let private = address.is_some_and(|v| match v {
        std::net::IpAddr::V4(v) => v.is_private() || (v.octets()[0] == 100 && (64..128).contains(&v.octets()[1])),
        std::net::IpAddr::V6(v) => (v.segments()[0] & 0xfe00) == 0xfc00,
    });
    if url.scheme() != "https" && !(url.scheme() == "http" && (loopback || (vpn && private))) { return None; }
    Some(url.as_str().trim_end_matches('/').to_string())
}

fn endpoint() -> Option<&'static str> {
    static ENDPOINT: OnceLock<Option<String>> = OnceLock::new();
    ENDPOINT.get_or_init(|| match std::fs::metadata(ENDPOINT_FILE) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(DEV_BASE.to_string()),
        Ok(m) if m.len() <= 2048 => std::fs::read_to_string(ENDPOINT_FILE).ok()
            .and_then(|s| validated_endpoint(&s, std::path::Path::new(VPN_MARKER).is_file())),
        _ => None,
    }).as_deref()
}

/// Ceiling on worker threads. The game can issue RPCs faster than the LAN answers, and one
/// unbounded thread per request is an easy way to kill a game process.
const MAX_INFLIGHT: usize = 16;

/// Ceiling on undelivered completions, in case the game stops calling `OnUpdate`.
const MAX_PENDING: usize = 64;

/// Ceiling on each request body copied out of the borrowed blob, and on the batch total.
const MAX_BODY: usize = 8 << 20;

/// Ceiling on `count` from `N2ServerSend`. Bounds the allocations sized from it.
const MAX_RPC_COUNT: usize = 4096;

/// Clamp for the game-supplied timeout. Below the floor the LAN round trip would always
/// time out; above the ceiling a worker could sit parked for an implausible span.
const TIMEOUT_FLOOR_MS: u64 = 250;
const TIMEOUT_CEIL_MS: u64 = 30_000;

static INFLIGHT: AtomicUsize = AtomicUsize::new(0);

/// One process-wide client, built on first use.
///
/// `reqwest::blocking::Client` owns a background tokio runtime, so building one per request
/// would spawn a runtime per RPC. The per-request timeout goes on the request builder rather
/// than here, so callers with different timeouts still share this client.
fn client() -> Option<&'static reqwest::blocking::Client> {
    static CLIENT: OnceLock<Option<reqwest::blocking::Client>> = OnceLock::new();
    CLIENT
        .get_or_init(|| reqwest::blocking::Client::builder()
            .redirect(reqwest::redirect::Policy::none()).build().ok())
        .as_ref()
}

fn clamp_timeout(timeout_ms: i32) -> Duration {
    let ms = (timeout_ms.max(0) as u64).clamp(TIMEOUT_FLOOR_MS, TIMEOUT_CEIL_MS);
    Duration::from_millis(ms)
}

// --- wire format -------------------------------------------------------------
//
// Protobuf, in both directions, using the types generated from `tools/devrpc.proto`. The
// bodies the game hands us are themselves protobuf, so they travel as `bytes` fields and are
// never parsed or re-encoded here.
//
// Headers still carry the per-request context (rpc id, auth, location) rather than moving it
// into the message. That is deliberate: it is the same split the game's own protocol uses,
// and it keeps the dev server able to filter on a header without decoding a body.
//
// The previous JSON encoding carried each body as base64 alongside a separate `size` field,
// so a body had two descriptions that could disagree -- the dev server had to check for that
// and warn. A length-prefixed `bytes` field makes the disagreement unrepresentable.

/// POST a protobuf body and parse the protobuf reply, or `None` on any failure.
///
/// `base` is a parameter rather than a read of [`DEV_BASE`] so the HTTP path can be tested
/// against a listener in the same process.
fn post_proto<M>(
    base: &str,
    path: &str,
    body: &[u8],
    headers: &[(String, String)],
    timeout: Duration,
) -> Option<M>
where
    M: prost::Message + Default,
{
    let mut req = client()?
        .post(format!("{base}{path}"))
        .timeout(timeout)
        .header("content-type", "application/x-protobuf")
        .body(body.to_vec());
    for (k, v) in headers {
        // `header` defers a malformed name or value into an error rather than panicking.
        req = req.header(k.as_str(), v.as_str());
    }
    let resp = req.send().ok()?;
    if !resp.status().is_success() {
        log::info(&format!("{path} -> HTTP {}", resp.status().as_u16()));
        return None;
    }
    // Read then decode rather than `bytes()` directly: `bytes()` can fail on a short body,
    // and `decode` reports the same condition without an extra error path to reason about.
    let raw = resp.bytes().ok()?;
    M::decode(&raw[..]).ok()
}

// --- request copying ---------------------------------------------------------

/// A validated copy of one `N2ServerSend` batch.
struct Batch {
    /// `(method id, body)` per request, in order.
    items: Vec<(i32, Vec<u8>)>,
}

/// Copy the borrowed request arrays into owned data.
///
/// Every bound is checked *before* the allocation it sizes. `Vec::with_capacity` on an
/// unchecked `count`, or a slice built from an unchecked `sizes[i]`, is an immediate abort
/// inside the game process — the one failure mode in this file that cannot be survived.
///
/// # Safety
/// Either `count` is 0, or `methods` and `sizes` point to at least `count` elements and
/// `bodies` points to at least `sum(sizes)` bytes. All are borrowed for this call only.
unsafe fn copy_batch(
    count: i32,
    methods: *const i32,
    bodies: *const c_void,
    sizes: *const i32,
) -> Option<Batch> {
    if count < 0 {
        return None;
    }
    let count = count as usize;
    if count > MAX_RPC_COUNT {
        return None;
    }
    if count > 0 && (methods.is_null() || sizes.is_null()) {
        return None;
    }

    let mut total: usize = 0;
    let mut lengths = Vec::with_capacity(count);
    for i in 0..count {
        let size = *sizes.add(i);
        if size < 0 {
            return None;
        }
        let size = size as usize;
        if size > MAX_BODY {
            return None;
        }
        total = total.checked_add(size)?;
        if total > MAX_BODY {
            return None;
        }
        lengths.push(size);
    }
    if total > 0 && bodies.is_null() {
        return None;
    }

    // One contiguous blob: request `i` occupies the next `sizes[i]` bytes.
    let blob: &[u8] = if total == 0 {
        &[]
    } else {
        core::slice::from_raw_parts(bodies as *const u8, total)
    };

    let mut items = Vec::with_capacity(count);
    let mut offset = 0usize;
    for i in 0..count {
        let size = lengths.get(i).copied().unwrap_or(0);
        let end = offset.saturating_add(size);
        // `get(..)` rather than indexing: a range past the end yields an empty body instead
        // of a panic. It cannot happen given the arithmetic above, which is the point.
        let body = blob.get(offset..end).unwrap_or_default().to_vec();
        offset = end;
        items.push((*methods.add(i), body));
    }
    Some(Batch { items })
}

/// Copy a borrowed byte array, rejecting negative or oversized lengths.
///
/// # Safety
/// `p` must be null or point to at least `size` bytes.
unsafe fn copy_bytes(p: *const u8, size: i32) -> Option<Vec<u8>> {
    if size < 0 {
        return None;
    }
    let size = size as usize;
    if size == 0 {
        return Some(Vec::new());
    }
    if size > MAX_BODY || p.is_null() {
        return None;
    }
    Some(core::slice::from_raw_parts(p, size).to_vec())
}

// --- completions -------------------------------------------------------------

/// Where an arriving reply has to go: the per-request delegate pair and the id the game is
/// waiting on.
///
/// `callback_handle` is a `usize` rather than a `*mut c_void` because this value is built on
/// the game's thread and consumed on a worker's. Raw pointers are not `Send`; the opaque
/// GCHandle is never dereferenced on this side, so an integer carries it across just as well.
/// It converts back to a pointer only inside [`deliver_rpc`], back on the game's thread.
#[derive(Clone, Copy)]
struct ReplyTarget {
    callback_handle: usize,
    call: CallCallbackDelegate,
    free: FreeHandleDelegate,
    rpc_id: i32,
}

/// The reply sent when the real one could not be obtained.
fn server_error(target: ReplyTarget) -> Completion {
    Completion::Rpc {
        callback_handle: target.callback_handle,
        call: target.call,
        free: target.free,
        rpc_id: target.rpc_id,
        status: RPC_STATUS_SERVER_ERROR,
        payloads: Vec::new(),
    }
}

/// Queue a finished round trip for the next `OnUpdate`.
fn push(pending: &Mutex<Vec<Completion>>, c: Completion) {
    let mut q = pending.lock().unwrap_or_else(|e| e.into_inner());
    if q.len() >= MAX_PENDING {
        // An evicted `Rpc` completion takes its GCHandle with it: the only thing that frees
        // one is `free` on the game thread, and we are not there. Bounded and one-shot, but
        // worth a line rather than a silent leak.
        q.remove(0);
        log::info("N2Server: completion queue full; dropped the oldest");
    }
    q.push(c);
}

/// Hand finished round trips to the game.
///
/// Called from a vtable slot the game invoked (`OnUpdate` / `Sync`), so this runs on a
/// thread already inside the managed runtime — which is what makes it legal to call back in.
/// It must be called with the [`Context`] state lock **not** held: the callback re-enters the
/// plugin (a location getter, say) and would deadlock against the same mutex.
///
/// # Safety
/// Must be called from a thread the game supplied, with the state lock released, and the
/// callback delegates must still be live.
pub unsafe fn deliver_all(ctx: &Context, ready: Vec<Completion>) {
    for c in ready {
        match c {
            Completion::AuthTicket(ticket) => {
                log::info(&format!("auth ticket acquired ({} chars)", ticket.len()));
                ctx.lock().auth_ticket = Some(ticket);
                // The game learns the session is live from this callback and from nowhere
                // else. `NativeChannelAuthEvent` is the whole of the auth state machine --
                // `NativeEventHandler` has one handler per value -- so with the ticket in
                // hand the transition it is waiting for is `GotAuthToken`. Without it the
                // game authenticates, issues one RPC, and then sits still.
                deliver_event(
                    ctx,
                    EventSlot::ChannelAuth,
                    &channel_auth_payload(NativeChannelAuthEvent::GotAuthToken),
                );
            }
            Completion::MapSettings(settings) => {
                // ARM64 ClientSettingsUpdated: pointer/int/padding, repeated for
                // gameboard. Managed copies both arrays synchronously. Keep the
                // owned bytes alive until the callback returns.
                let mut payload = Vec::with_capacity(32);
                payload.extend_from_slice(&(settings.as_ptr() as u64).to_le_bytes());
                payload.extend_from_slice(&(settings.len() as i32).to_le_bytes());
                payload.extend_from_slice(&[0u8; 4]);
                payload.extend_from_slice(&(settings.as_ptr() as u64).to_le_bytes());
                payload.extend_from_slice(&[0u8; 8]);
                deliver_event(ctx, EventSlot::ClientSettingsUpdated, &payload);
            }
            Completion::Event { slot, payload } => deliver_event(ctx, slot, &payload),
            Completion::Rpc {
                callback_handle,
                call,
                free,
                rpc_id,
                status,
                payloads,
            } => {
                deliver_rpc(callback_handle, call, free, rpc_id, status, &payloads);
            }
        }
    }
}

/// Fire one of the six `SetCallbacks` event handles.
///
/// Same convention as [`deliver_rpc`] — `call(gchandle, buffer)` — with one difference that
/// matters: **the handle is not freed.** The six are registered once for the session and
/// fired for its whole life, so the `free` that correctly follows every RPC reply would here
/// be a use-after-free on the next fire. (The per-request handle passed to `N2ServerSend` is
/// the opposite: it belongs to one request and is freed exactly once.)
///
/// A null handle is not an error. Managed passes null for handles it registered no listener
/// for — but it is also exactly what a mis-ordered `SetCallbacks` would look like, so it gets
/// a line rather than passing silently.
///
/// The payload is a borrowed slice that must outlive the call. Managed reads it synchronously
/// and never retains it, the same contract as a reply's payload buffers.
///
/// # Safety
/// Must be called from a thread the game supplied, with the state lock **not** held: the
/// callback re-enters the plugin and would deadlock against the same mutex.
unsafe fn deliver_event(ctx: &Context, slot: EventSlot, payload: &[u8]) {
    // Checked before the handle, because a short payload is a bug in the *producer* and would
    // otherwise stay invisible for as long as the game had no listener registered.
    //
    // Managed is told no length: it casts the pointer to the receiver's struct and reads it.
    // So a payload shorter than that struct is not a truncated event, it is an out-of-bounds
    // read inside the game process -- and with `panic = "abort"` and no debug assertions in
    // release, a wild read rather than a caught one. Dropping the event is the only answer
    // that survives, and it is strictly better than the crash: the game is stalled either way,
    // but this way it stays alive long enough to say why.
    let want = payload_width(slot);
    if payload.len() < want {
        log::info(&format!(
            "event {slot:?}: {} byte payload but the receiver reads {want}; dropped",
            payload.len()
        ));
        return;
    }
    let (handle, call) = {
        let s = ctx.lock();
        (s.callbacks.events[slot as usize], s.callbacks.call)
    };
    let Some(call) = call else {
        log::info(&format!("event {slot:?}: no call delegate registered"));
        return;
    };
    if handle.is_null() {
        log::info(&format!("event {slot:?}: handle not registered; dropped"));
        return;
    }
    log::info(&format!("event {slot:?}: firing {} byte(s)", payload.len()));
    call(handle, payload.as_ptr() as *mut u8);
}

/// Queue one of the six event handles for the next `OnUpdate`.
///
/// Producers call this rather than firing directly: they run inside `with_ctx` holding the
/// state lock, and delivering takes that lock again — so the event travels through the queue
/// the same way a reply does, and reaches the game on a thread it called us on.
pub fn raise(ctx: &Context, slot: EventSlot, payload: Vec<u8>) {
    let pending = ctx.pending();
    push(&pending, Completion::Event { slot, payload });
}

/// The one live context's completion queue, so a producer with no [`Context`] can raise an
/// event on the same pipe.
///
/// [`raise`] needs a `&Context` to resolve the event handle against, and one producer does not
/// have one: the JNI sensor natives are handed a `jobject` receiver and scalars — **no plugin
/// handle** — so they cannot reach the context at all (see [`crate::live`], which is
/// process-global for exactly this reason). Without this, a fix arriving from Java is recorded
/// and never announced, so the game's `DeviceManager` never hears about it and its location
/// pipeline never starts.
///
/// Publishing the *queue* rather than the context is the point: this queue is already
/// `Arc<Mutex<Vec<Completion>>>`, already `Send`, and already the designed channel for work
/// produced off-thread and delivered on the game's thread (see [`Completion`]). So this adds no
/// new concurrency primitive, and no `unsafe` — a detached producer holds only this `Arc`,
/// which stays valid for as long as it is held. A stored `*const Context` would instead be a
/// use-after-free waiting for `N2PluginDestroyContext`, which the game may call at any moment.
static PUBLISHED: Mutex<Option<Arc<Mutex<Vec<Completion>>>>> = Mutex::new(None);

/// Point detached producers at this context's queue. Called from `SetCallbacks`, which is the
/// moment the six handles start existing — before it there is nothing worth raising at.
pub fn publish(pending: Arc<Mutex<Vec<Completion>>>) {
    *PUBLISHED.lock().unwrap_or_else(|e| e.into_inner()) = Some(pending);
}

/// Stop raising detached events. Called from `DestroyContext`: the six handles die with the
/// context, so anything raised afterwards could only accumulate until [`MAX_PENDING`] evicts it.
pub fn unpublish() {
    *PUBLISHED.lock().unwrap_or_else(|e| e.into_inner()) = None;
}

/// Raise an event with no [`Context`] in hand.
///
/// Silent when nothing is published, which is the state before `SetCallbacks` and after
/// `DestroyContext`. A line there would be pure noise: `nativeLocationUpdate` runs at the
/// sensor rate, so it would flood logcat in exactly the window where it can do nothing.
pub fn raise_detached(slot: EventSlot, payload: Vec<u8>) {
    let published = PUBLISHED.lock().unwrap_or_else(|e| e.into_inner()).clone();
    if let Some(pending) = published {
        push(&pending, Completion::Event { slot, payload });
    }
}

unsafe fn deliver_rpc(
    callback_handle: usize,
    call: CallCallbackDelegate,
    free: FreeHandleDelegate,
    rpc_id: i32,
    status: i32,
    payloads: &[Vec<u8>],
) {
    let handle = callback_handle as *mut c_void;
    if handle.is_null() {
        // Managed always passes a real GCHandle, so this is unreachable — but delivering to
        // a null handle would be a leaked request at best.
        //
        // There is deliberately no matching check on `call`/`free`: those are non-nullable
        // function-pointer *types*, so `call as usize == 0` is something the optimiser is
        // entitled to fold away, and a null there is a contract violation on the managed
        // side that no amount of checking here could catch.
        log::info("N2Server: refusing to deliver a reply to a null handle");
        return;
    }

    // The payload buffers must outlive the call and no longer: managed reads them
    // synchronously and never retains the pointer, and no slot in the vtable exists with
    // which it could free them. Copying into locals is what pins that lifetime.
    let mut blobs: Vec<Vec<u8>> = payloads.to_vec();
    let mut arr: Vec<PayloadArr> = blobs
        .iter_mut()
        .map(|b| PayloadArr {
            data: b.as_mut_ptr(),
            size: b.len() as i32,
        })
        .collect();

    let mut res = RpcRes {
        rpc_id,
        status,
        // An empty Vec's pointer is non-null and aligned, matching the observed convention
        // that an empty array is a real pointer to zero elements rather than null.
        payloads: arr.as_mut_ptr(),
        payload_count: arr.len() as i32,
    };

    // The one line that separates "the game was never answered" from "the game was answered
    // and did nothing with it" -- which are otherwise the same silence in the log.
    log::info(&format!(
        "rpc {rpc_id}: delivering {} payload(s), status {status}",
        payloads.len()
    ));
    call(handle, &mut res as *mut RpcRes as *mut u8);
    // Exactly once per request. Managed frees only the GCHandle; the buffers above are ours
    // and die with this frame.
    free(handle);
}

/// The header set carrying per-request context alongside the batch.
fn context_headers(
    auth: &Option<String>,
    location: Option<LocationData>,
    rpc_id: i32,
) -> Vec<(String, String)> {
    let mut h = vec![("x-dittobuffer-rpc-id".to_string(), rpc_id.to_string())];
    if let Some(t) = auth {
        h.push(("authorization".to_string(), t.clone()));
    }
    if let Some(l) = location {
        h.push(("x-dittobuffer-lat".to_string(), l.latitude_deg.to_string()));
        h.push(("x-dittobuffer-lng".to_string(), l.longitude_deg.to_string()));
        h.push(("x-dittobuffer-acc".to_string(), l.accuracy_m.to_string()));
        h.push(("x-dittobuffer-fix-time".to_string(), l.timestamp_s.to_string()));
        h.push(("x-dittobuffer-provider".to_string(), l.provider_status.to_string()));
    }
    h
}

/// Do the round trip and turn it into a completion.
///
/// Total by construction: every path returns a completion, so the worker body has a single
/// exit and cannot skip its bookkeeping. A transport failure becomes `ServerError` rather
/// than a dropped request, so the game gets a definite answer instead of waiting forever on
/// a callback that never arrives.
fn fetch_reply(
    base: &str,
    body: &[u8],
    headers: &[(String, String)],
    timeout: Duration,
    expected: usize,
    target: ReplyTarget,
) -> Completion {
    let rpc_id = target.rpc_id;

    let Some(r) = post_proto::<devrpc::RpcResponse>(base, "/rpc", body, headers, timeout) else {
        log::info(&format!("rpc {rpc_id}: no reply; answering ServerError"));
        return server_error(target);
    };

    if r.rpc_id.is_some_and(|id| id != rpc_id) {
        // Not fatal — the id the game asked for is the one its callback is keyed on — but a
        // mismatch means the dev server paired a reply with the wrong request.
        log::info(&format!(
            "rpc {rpc_id}: server echoed {:?}; keeping the requested id",
            r.rpc_id
        ));
    }

    // `RpcPayload.method` is dropped here on purpose: it labels the payload in the dev
    // server's log, and the ABI pairs payloads with requests positionally, so there is
    // nothing on the managed side for it to travel to.
    let mut payloads: Vec<Vec<u8>> = Vec::with_capacity(expected);
    for p in &r.payloads {
        payloads.push(p.payload.clone());
    }
    // Exactly `count`: managed pairs `payloads[i]` with `methods[i]`, so a short array is an
    // out-of-bounds read in the game and a long one is silently extra. Neither is acceptable.
    payloads.resize(expected, Vec::new());

    Completion::Rpc {
        callback_handle: target.callback_handle,
        call: target.call,
        free: target.free,
        rpc_id,
        status: r.status.unwrap_or(RPC_STATUS_SERVER_ERROR),
        payloads,
    }
}

/// Start a worker thread.
///
/// `thread::Builder::spawn` rather than `thread::spawn`, because the latter panics when the
/// OS refuses a thread and a panic here aborts the game. Returns `false` if the thread could
/// not be started, in which case `f` has **not** run and its captures are still live for the
/// caller to use.
///
/// `f` must not panic: `panic = "abort"` in release means an unwind on this thread kills the
/// process.
fn spawn_worker<F>(name: &str, f: F) -> bool
where
    F: FnOnce() + Send + 'static,
{
    match std::thread::Builder::new().name(name.to_string()).spawn(f) {
        Ok(_) => true,
        Err(_) => {
            log::info(&format!("{name}: could not spawn a worker thread"));
            false
        }
    }
}

// --- vtable slots ------------------------------------------------------------

/// `N2ServerRequestAuthToken` — starts the session.
///
/// Posts to `/get_auth_token` and records the ticket on the worker's return. It does **not**
/// fire the channel-auth event handle: which of the six handles that is, and what
/// `MarshallableChannelAuth*` looks like, are still open ([notes/09-open-questions.md]), so
/// firing one would be guessing at a payload layout. The game may stall at auth as a result —
/// the request itself is the deliverable here.
pub unsafe extern "C" fn request_auth_token(
    handle: Handle,
    auth_id: *const c_char,
    token: *const u8,
    token_size: i32,
    session_options: *mut SessionOptionsData,
) -> ApiError {
    let auth_id = cstr_to_string(auth_id).unwrap_or_default();
    let Some(token) = copy_bytes(token, token_size) else {
        return ApiError::InvalidParameters;
    };
    trace::REQUEST_AUTH_TOKEN
        .enter_with(&format!("auth_id={auth_id:?}, {} token bytes", token.len()));
    let prevent_account_creation = if session_options.is_null() {
        None
    } else {
        Some((*session_options).prevent_account_creation != 0)
    };

    let pending = match with_ctx(handle, |c| c.pending()) {
        Ok(p) => p,
        Err(e) => return e,
    };

    let body = devrpc::AuthRequest {
        auth_id,
        token,
        // Recorded so the dev server's log is self-explanatory; it does not act on it.
        prevent_account_creation: prevent_account_creation.unwrap_or(false),
    }
    .encode_to_vec();

    let p = Arc::clone(&pending);
    if !spawn_worker("nlp-auth", move || {
        let Some(base) = endpoint() else { log::info("Invalid local endpoint configuration; authentication stopped"); return; };
        match post_proto::<devrpc::AuthResponse>(
            base,
            "/get_auth_token",
            &body,
            &[],
            Duration::from_secs(10),
        )
        .and_then(|r| r.ticket)
        {
            Some(ticket) => {
                push(&p, Completion::AuthTicket(ticket));
                // This configuration is independent of gameplay RPC payloads.
                // An unavailable/disabled map endpoint must not break login.
                if let Some(raw) = client().and_then(|c| c.get(format!("{base}/map/settings"))
                    .timeout(Duration::from_secs(10)).send().ok())
                    .filter(|r| r.status().is_success() && r.content_length().is_some_and(|n| n <= MAX_BODY as u64))
                    .and_then(|r| r.bytes().ok()) {
                    if !raw.is_empty() && raw.len() <= MAX_BODY {
                        push(&p, Completion::MapSettings(raw.to_vec()));
                    }
                }
            }
            None => log::info("get_auth_token: no ticket in reply"),
        }
    }) {
        return ApiError::InternalError;
    }
    ApiError::Success
}

/// Generates the next RPC id for the managed side.
///
/// A monotonic counter is enough: ids only need to be distinct within a session.
pub unsafe extern "C" fn generate_rpc_id(handle: Handle, out: *mut i32) -> ApiError {
    // Throttled: the id is named on the `N2ServerSend` line that used it, so this only has to
    // answer whether the game is still minting ids -- and whether it mints them without ever
    // sending them, which is a distinct way to be stuck.
    trace::GENERATE_RPC_ID.enter_throttled(trace::PER_FRAME);
    if out.is_null() {
        return ApiError::InvalidParameters;
    }
    match with_ctx(handle, |c| {
        let mut s = c.lock();
        let id = s.next_rpc_id;
        // Wrapping is fine; distinctness holds until 2^31 requests.
        s.next_rpc_id = s.next_rpc_id.wrapping_add(1);
        id
    }) {
        Ok(id) => {
            *out = id;
            ApiError::Success
        }
        Err(e) => e,
    }
}

/// `N2ServerSend` — the batch RPC entry point.
///
/// Copies the request out, hands it to a worker, and returns immediately. The reply reaches
/// the game through `call` on a later `OnUpdate`, followed by exactly one `free`.
pub unsafe extern "C" fn send(
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
) -> ApiError {
    let Some(batch) = copy_batch(count, methods, bodies, sizes) else {
        // Nonsense arguments are the caller's problem, and `CheckResult` throws on this —
        // which is the correct, visible outcome.
        return ApiError::InvalidParameters;
    };

    trace::SEND.enter_with(&format!(
        "rpc_id={rpc_id}, {} request(s), timeout={timeout_ms}ms, retry={retry_delay_ms}ms",
        batch.items.len()
    ));

    // Read the routing bits on this thread. The worker touches nothing derived from the
    // handle, so it cannot race `N2PluginDestroyContext`.
    let (pending, auth, location, debug_source) = match with_ctx(handle, |c| {
        let s = c.lock();
        (
            c.pending(),
            s.auth_ticket.clone(),
            s.debug_location.or_else(crate::live::location),
            s.debug_location.is_some(),
        )
    }) {
        Ok(v) => v,
        Err(e) => return e,
    };

    // Captured as a `usize` because raw pointers are not `Send` and this crosses a thread
    // boundary. It converts back to a pointer inside `deliver_rpc`, on the game's thread.
    let target = ReplyTarget {
        callback_handle: callback_handle as usize,
        call,
        free,
        rpc_id,
    };
    // Managed pairs `payloads[i]` with `methods[i]`, so the reply must carry exactly this many.
    let expected = batch.items.len();

    let n = INFLIGHT.fetch_add(1, Ordering::AcqRel);
    if n >= MAX_INFLIGHT {
        INFLIGHT.fetch_sub(1, Ordering::AcqRel);
        // Answer with ServerError rather than refusing: a refusal throws managed-side and
        // would take down a request that was already accepted, while a definite error the
        // game can retry is closer to what a real backend does under load.
        log::info("N2ServerSend: in-flight cap reached; answering ServerError");
        push(&pending, server_error(target));
        return ApiError::Success;
    }

    // `bytes` is length-prefixed, so there is no size field to keep in step with the body --
    // the mismatch the JSON encoding needed a runtime check for cannot be expressed.
    let body = devrpc::RpcRequest {
        rpc_id,
        timeout_ms,
        retry_delay_ms,
        requests: batch
            .items
            .iter()
            .map(|(method, bytes)| devrpc::RpcItem {
                method: *method,
                body: bytes.clone(),
            })
            .collect(),
    }
    .encode_to_vec();

    let mut headers = context_headers(&auth, location, rpc_id);
    headers.push(("x-dittobuffer-location-source".to_string(), if debug_source { "debug" } else { "device" }.to_string()));
    let timeout = clamp_timeout(timeout_ms);
    let p = Arc::clone(&pending);

    // Single exit in the thread body, so the in-flight count is decremented on every path.
    let spawned = spawn_worker("nlp-rpc", move || {
        let Some(base) = endpoint() else {
            push(&p, server_error(target));INFLIGHT.fetch_sub(1, Ordering::AcqRel);return;
        };
        push(
            &p,
            fetch_reply(base, &body, &headers, timeout, expected, target),
        );
        INFLIGHT.fetch_sub(1, Ordering::AcqRel);
    });

    if !spawned {
        INFLIGHT.fetch_sub(1, Ordering::AcqRel);
        // `f` never ran, so nothing else can be holding this request's answer. The game
        // still gets a definite reply rather than a callback that never fires.
        push(&pending, server_error(target));
        return ApiError::InternalError;
    }

    ApiError::Success
}

/// `N2ServerSetDebugInfo` — the original comment says the stub does not need it, so it is
/// ignored. Returning `Success` rather than `NotImplementedYet` is the point: `CheckResult`
/// throws on anything else, and there is no reason to stop the game over a slot nothing
/// depends on.
pub unsafe extern "C" fn set_debug_info(
    handle: Handle,
    _debug_info: *const c_void,
    _debug_size: i32,
) -> ApiError {
    // Throttled rather than the one-shot gate it had: this slot can be reached per frame, and
    // whether the game sets debug info once or continuously is exactly the kind of thing the
    // gate made invisible.
    trace::SET_DEBUG_INFO.enter_throttled(trace::PER_FRAME);
    with_ctx(handle, |_| {})
        .map(|_| ApiError::Success)
        .unwrap_or(ApiError::InvalidParameters)
}

/// `N2ServerSendMapQueryRequest` — reached via `GmoManager.Initialize` →
/// `RpcManager.SendMapQueryRequest`.
///
/// This slot has no per-request callback, so a reply has nowhere to go and is only logged.
/// The request is still posted, because the request is the thing being studied.
pub unsafe extern "C" fn send_map_query_request(
    handle: Handle,
    request_data: *const u8,
    size: i32,
) -> ApiError {
    let Some(blob) = copy_bytes(request_data, size) else {
        return ApiError::InvalidParameters;
    };
    trace::SEND_MAP_QUERY_REQUEST.enter_with(&format!("{size} bytes -> /map_query"));
    // Nothing is stored from this slot, but the handle is still validated so a null one is
    // rejected the same way every other slot rejects it.
    let auth = match with_ctx(handle, |c| c.lock().auth_ticket.clone()) { Ok(v) => v, Err(e) => return e };

    let body = devrpc::MapQueryRequest { body: blob }.encode_to_vec();

    spawn_worker("nlp-map", move || {
        let Some(base) = endpoint() else { return; };
        let headers = auth.map(|v| vec![("authorization".to_string(),v)]).unwrap_or_default();
        match post_proto::<devrpc::MapQueryResponse>(
            base,
            "/map_query",
            &body,
            &headers,
            Duration::from_secs(10),
        ) {
            Some(r) => log::info(&format!("map_query reply: status={:?}", r.status)),
            None => log::info("map_query: no reply"),
        }
    });
    // Fire and forget: there is no callback through which to report a failure.
    ApiError::Success
}

/// `N2ServerSetServer` — records the endpoint the game wants, without following it.
pub unsafe extern "C" fn set_server(handle: Handle, host_port: *const c_char) -> ApiError {
    let host = cstr_to_string(host_port);
    trace::SET_SERVER.enter_with(&format!(
        "{} -> routing to {DEV_BASE} instead",
        host.as_deref().unwrap_or("<null>"),
    ));
    with_ctx(handle, |c| {
        c.lock().server_host = host;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

/// `N2ServerTerminateSession` — drops the ticket.
///
/// Queued completions are left to drain normally: freeing their handles here would race the
/// workers still producing them, and they are delivered on the next `OnUpdate` regardless.
pub unsafe extern "C" fn terminate_session(handle: Handle) -> ApiError {
    trace::TERMINATE_SESSION.enter();
    with_ctx(handle, |c| {
        c.lock().auth_ticket = None;
    })
    .map(|_| ApiError::Success)
    .unwrap_or(ApiError::InvalidParameters)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::Context;
    use std::sync::mpsc::{channel, Receiver};

    /// What the fake managed side saw in one callback.
    struct Seen {
        rpc_id: i32,
        status: i32,
        payloads: Vec<Vec<u8>>,
    }

    static CALLS: Mutex<Vec<Seen>> = Mutex::new(Vec::new());
    static FREES: Mutex<Vec<usize>> = Mutex::new(Vec::new());
    static GATE: Mutex<()> = Mutex::new(());

    /// Raw bytes seen by an event receiver, with the GCHandle they arrived on.
    ///
    /// Deliberately raw and separate from [`CALLS`]: the six event receivers read a *typed
    /// struct* out of the buffer, not an `RpcRes`, so those bytes are the only thing the two
    /// paths have in common and the only thing worth asserting on.
    static EVENTS: Mutex<Vec<(usize, Vec<u8>)>> = Mutex::new(Vec::new());

    /// Stands in for `CallCallbackImpl`: reads the buffer exactly as managed does.
    unsafe extern "C" fn record_call(handle: *mut c_void, buffer: *mut u8) {
        let res = &*(buffer as *const RpcRes);
        let mut payloads = Vec::new();
        if res.payload_count > 0 && !res.payloads.is_null() {
            for i in 0..res.payload_count as usize {
                let p = &*res.payloads.add(i);
                payloads.push(if p.size > 0 && !p.data.is_null() {
                    core::slice::from_raw_parts(p.data, p.size as usize).to_vec()
                } else {
                    Vec::new()
                });
            }
        }
        let _ = handle;
        CALLS.lock().unwrap_or_else(|e| e.into_inner()).push(Seen {
            rpc_id: res.rpc_id,
            status: res.status,
            payloads,
        });
    }

    /// Stands in for `DeleteCallbackImpl` — `GCHandle.FromIntPtr(p).Free()`.
    unsafe extern "C" fn record_free(handle: *mut c_void) {
        FREES
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(handle as usize);
    }

    /// How wide a struct the receiver for each registered handle reads, keyed by handle.
    ///
    /// The real managed side has exactly **one** `CallCallbackImpl` behind all six event
    /// handles -- `N2PluginSetCallbacks_` takes a single `callCallback` (`Ditto.cs:196`) --
    /// and tells them apart by the GCHandle it is handed. Modelling that here, rather than
    /// giving each slot its own Rust function, is what makes the slot-to-handle mapping the
    /// thing under test: a `deliver_event` that resolves the wrong slot's handle lands on a
    /// handle with no receiver and records nothing.
    static READERS: Mutex<Vec<(usize, usize)>> = Mutex::new(Vec::new());

    /// The one shared receiver, standing in for `CallCallbackImpl`.
    ///
    /// Reads `buffer` at the width the handle's real counterpart reads: 4 bytes for
    /// `MarshallableChannelAuthEventParams`, `size_of::<LocationData>()` for `Geo.Location`.
    unsafe extern "C" fn record_event(handle: *mut c_void, buffer: *mut u8) {
        let handle = handle as usize;
        let width = READERS
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .find(|(h, _)| *h == handle)
            .map(|(_, w)| *w);
        // No registered width means this handle is not one the test installed. Reading
        // nothing is the honest answer to that; reading a guessed width is how this harness
        // once read 0x30 bytes out of a 4-byte buffer.
        let bytes = match width {
            Some(w) if !buffer.is_null() => core::slice::from_raw_parts(buffer, w).to_vec(),
            _ => Vec::new(),
        };
        EVENTS
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push((handle, bytes));
    }

    /// Register one event handle, as a successful `SetCallbacks` would.
    ///
    /// `width` is the size of the struct the real receiver for this handle reads, so an
    /// assertion on the recorded length is an assertion that managed would not read past the
    /// buffer we handed it.
    fn register(ctx: &Context, slot: EventSlot, handle: usize, width: usize) {
        ctx.lock().callbacks.events[slot as usize] = handle as *mut c_void;
        READERS
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push((handle, width));
    }

    /// Install the shared `call` delegate, as `SetCallbacks` does once for the session.
    ///
    /// Separate from [`register`] because it is one registration covering all six slots, and
    /// keeping the two apart is what stops a test from accidentally modelling six receivers.
    fn register_caller(ctx: &Context) {
        ctx.lock().callbacks.call = Some(record_event);
    }

    fn events() -> Vec<(usize, Vec<u8>)> {
        EVENTS.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn reset() {
        CALLS.lock().unwrap_or_else(|e| e.into_inner()).clear();
        FREES.lock().unwrap_or_else(|e| e.into_inner()).clear();
        EVENTS.lock().unwrap_or_else(|e| e.into_inner()).clear();
        READERS.lock().unwrap_or_else(|e| e.into_inner()).clear();
    }

    /// Read a `LocationData` back out of a `locationUpdate` buffer.
    fn decode_location(b: &[u8]) -> LocationData {
        let f = |i: usize| f64::from_le_bytes(b[i..i + 8].try_into().expect("8 bytes"));
        let u = |i: usize| u32::from_le_bytes(b[i..i + 4].try_into().expect("4 bytes"));
        LocationData {
            latitude_deg: f(0x00),
            longitude_deg: f(0x08),
            altitude_m: f(0x10),
            accuracy_m: f(0x18),
            timestamp_s: f(0x20),
            provider_status: u(0x28),
            location_type: u(0x2c),
        }
    }

    fn target(rpc_id: i32) -> ReplyTarget {
        ReplyTarget {
            callback_handle: 0xDEC0DE,
            call: record_call,
            free: record_free,
            rpc_id,
        }
    }

    // --- the flat blob -------------------------------------------------------

    /// `bodies` is one contiguous buffer where request `i` occupies the next `sizes[i]`
    /// bytes. Getting this wrong mis-slices every body in the batch, and because the bytes
    /// are opaque protobuf nothing downstream would notice — the dev server would just log
    /// plausible-looking garbage.
    #[test]
    fn copy_batch_splits_the_flat_blob_in_order() {
        let methods = [10i32, 20, 30];
        let sizes = [2i32, 0, 3];
        let bodies = b"abcdef";
        let b = unsafe { copy_batch(3, methods.as_ptr(), bodies.as_ptr() as *const c_void, sizes.as_ptr()) }
            .expect("well-formed batch");

        assert_eq!(b.items.len(), 3);
        assert_eq!(b.items[0], (10, b"ab".to_vec()));
        // A zero-length request must not consume a byte, or every later body shifts.
        assert_eq!(b.items[1], (20, Vec::new()));
        assert_eq!(b.items[2], (30, b"cde".to_vec()));
    }

    #[test]
    fn copy_batch_accepts_an_empty_batch() {
        let b = unsafe { copy_batch(0, core::ptr::null(), core::ptr::null(), core::ptr::null()) };
        assert!(
            b.expect("count 0 is valid").items.is_empty(),
            "a zero-request batch must not dereference any of the pointers"
        );
    }

    /// Each of these would otherwise be an integer-widening bug feeding an allocation or a
    /// `from_raw_parts`, which aborts the game process rather than returning an error.
    #[test]
    fn copy_batch_rejects_nonsense_without_dereferencing() {
        let methods = [1i32];
        let sizes_bad = [-1i32];
        let sizes_huge = [i32::MAX];
        let count_huge = MAX_RPC_COUNT as i32 + 1;
        let bodies = b"x";

        unsafe {
            // A negative count, and one past the ceiling, with every pointer null — the
            // checks must run before any dereference.
            assert!(copy_batch(-1, core::ptr::null(), core::ptr::null(), core::ptr::null()).is_none());
            assert!(copy_batch(count_huge, core::ptr::null(), core::ptr::null(), core::ptr::null()).is_none());
            // count > 0 with a null array.
            assert!(copy_batch(1, core::ptr::null(), bodies.as_ptr() as *const c_void, sizes_bad.as_ptr()).is_none());
            assert!(copy_batch(1, methods.as_ptr(), bodies.as_ptr() as *const c_void, core::ptr::null()).is_none());
            // A negative and an oversized individual size.
            assert!(copy_batch(1, methods.as_ptr(), bodies.as_ptr() as *const c_void, sizes_bad.as_ptr()).is_none());
            assert!(copy_batch(1, methods.as_ptr(), bodies.as_ptr() as *const c_void, sizes_huge.as_ptr()).is_none());
            // A body needed but not supplied.
            let sizes_ok = [1i32];
            assert!(copy_batch(1, methods.as_ptr(), core::ptr::null(), sizes_ok.as_ptr()).is_none());
        }
    }

    #[test]
    fn copy_bytes_rejects_nonsense() {
        unsafe {
            assert!(copy_bytes(core::ptr::null(), -1).is_none());
            assert_eq!(copy_bytes(core::ptr::null(), 0), Some(Vec::new()));
            assert!(copy_bytes(core::ptr::null(), 4).is_none(), "a null source with a non-zero size");
            assert!(copy_bytes(core::ptr::null(), i32::MAX).is_none());
            assert_eq!(copy_bytes(b"ab".as_ptr(), 2), Some(b"ab".to_vec()));
        }
    }

    #[test]
    fn timeouts_are_clamped_into_a_usable_window() {
        assert_eq!(clamp_timeout(-1), Duration::from_millis(TIMEOUT_FLOOR_MS));
        assert_eq!(clamp_timeout(0), Duration::from_millis(TIMEOUT_FLOOR_MS));
        assert_eq!(clamp_timeout(1500), Duration::from_millis(1500));
        assert_eq!(clamp_timeout(i32::MAX), Duration::from_millis(TIMEOUT_CEIL_MS));
    }

    #[test]
    fn context_headers_carry_what_the_dev_server_needs() {
        let loc = LocationData {
            latitude_deg: 1.5,
            longitude_deg: -2.25,
            accuracy_m: 3.0,
            ..LocationData::default()
        };
        let h = context_headers(&Some("ticket-abc".into()), Some(loc), 42);
        let find = |k: &str| h.iter().find(|(hk, _)| hk == k).map(|(_, v)| v.as_str());
        assert_eq!(find("x-dittobuffer-rpc-id"), Some("42"));
        assert_eq!(find("authorization"), Some("ticket-abc"));
        assert_eq!(find("x-dittobuffer-lat"), Some("1.5"));
        assert_eq!(find("x-dittobuffer-lng"), Some("-2.25"));

        // With no session and no fix, the id is all that is left — and nothing bogus is
        // invented for the rest.
        let bare = context_headers(&None, None, 7);
        assert_eq!(bare.len(), 1);
        assert_eq!(bare[0].1, "7");
    }

    // --- delivery ------------------------------------------------------------

    /// The callback is the only way the game ever sees a reply, so this test covers the
    /// three things that would each be invisible-but-fatal on device: the struct layout, the
    /// payload count, and `free` being called exactly once per request.
    #[test]
    fn delivery_reads_back_through_the_confirmed_layout() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        let ready = vec![Completion::Rpc {
            callback_handle: 0xDEC0DE,
            call: record_call,
            free: record_free,
            rpc_id: 7,
            status: RPC_STATUS_SUCCESS,
            payloads: vec![b"one".to_vec(), Vec::new()],
        }];
        unsafe { deliver_all(&ctx, ready) };

        let calls = CALLS.lock().unwrap_or_else(|e| e.into_inner());
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].rpc_id, 7);
        assert_eq!(calls[0].status, RPC_STATUS_SUCCESS);
        assert_eq!(calls[0].payloads, vec![b"one".to_vec(), Vec::new()]);
        drop(calls);

        // Exactly once. Twice would be a double-free of the GCHandle in the game; zero
        // times leaks it.
        assert_eq!(
            *FREES.lock().unwrap_or_else(|e| e.into_inner()),
            vec![0xDEC0DE]
        );
    }

    #[test]
    fn a_ticket_completion_updates_the_session_even_with_no_callbacks_registered() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        assert!(ctx.lock().auth_ticket.is_none());
        unsafe { deliver_all(&ctx, vec![Completion::AuthTicket("t0ken".into())]) };

        assert_eq!(ctx.lock().auth_ticket.as_deref(), Some("t0ken"));
        assert!(CALLS.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
        assert!(FREES.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
    }

    // --- the six event handles -----------------------------------------------

    #[test]
    fn map_settings_callback_owns_payload_until_managed_copies_it() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();
        unsafe extern "C" fn copy_map(handle: *mut c_void, buffer: *mut u8) {
            let pointer = core::ptr::read_unaligned(buffer.cast::<u64>()) as *const u8;
            let length = core::ptr::read_unaligned(buffer.add(8).cast::<i32>());
            let bytes = if !pointer.is_null() && (0..=1024).contains(&length) {
                core::slice::from_raw_parts(pointer, length as usize).to_vec()
            } else { Vec::new() };
            EVENTS.lock().unwrap_or_else(|e| e.into_inner()).push((handle as usize, bytes));
        }
        let ctx = Context::new();
        ctx.lock().callbacks.call = Some(copy_map);
        register(&ctx, EventSlot::ClientSettingsUpdated, 0xC418, 32);
        unsafe { deliver_all(&ctx, vec![Completion::MapSettings(vec![10, 2, 8, 1])]) };
        assert_eq!(events(), vec![(0xC418, vec![10, 2, 8, 1])]);
        assert!(FREES.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
    }

    /// The channel-auth event is the only thing that tells the game its session is live, and
    /// all three ways it can be wrong are invisible on device: the wrong bytes, the wrong
    /// slot, or a freed handle. This covers all three.
    #[test]
    fn a_ticket_fires_channel_auth_with_got_auth_token() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        register_caller(&ctx);
        register(&ctx, EventSlot::ChannelAuth, 0xA17, size_of::<i32>());
        // A second, distinct handle on the neighbouring slot, so landing on the wrong one
        // fails rather than looking like a success.
        register(&ctx, EventSlot::LocationUpdate, 0xB18, size_of::<LocationData>());

        unsafe { deliver_all(&ctx, vec![Completion::AuthTicket("t0ken".into())]) };

        assert_eq!(
            events(),
            vec![(0xA17, vec![3, 0, 0, 0])],
            "GotAuthToken (3) as a little-endian i32, on the channelAuth handle"
        );
        assert_eq!(ctx.lock().auth_ticket.as_deref(), Some("t0ken"));

        // The six are registered once and fired for the session, so the `free` that correctly
        // follows every RPC reply would be a use-after-free on the next fire.
        assert!(
            FREES.lock().unwrap_or_else(|e| e.into_inner()).is_empty(),
            "an event handle must never be freed"
        );
    }

    /// Routed by slot, and the payload arrives as the `LocationData` the game reads it as.
    #[test]
    fn a_raised_location_update_reaches_the_location_update_handle() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        register_caller(&ctx);
        register(&ctx, EventSlot::ChannelAuth, 0xA17, size_of::<i32>());
        register(&ctx, EventSlot::LocationUpdate, 0xB18, size_of::<LocationData>());

        let fix = LocationData {
            latitude_deg: 52.5,
            longitude_deg: 13.4,
            altitude_m: 34.0,
            accuracy_m: 5.0,
            timestamp_s: 1_700_000_000.0,
            provider_status: PROVIDER_STATUS_RUNNING,
            location_type: LOCATION_TYPE_MEASURED,
        };
        raise(&ctx, EventSlot::LocationUpdate, location_payload(&fix));
        let queued = ctx.take_completions();
        assert_eq!(queued.len(), 1, "raise must queue exactly one event");
        unsafe { deliver_all(&ctx, queued) };

        let seen = events();
        assert_eq!(seen.len(), 1);
        assert_eq!(seen[0].0, 0xB18, "landed on the channelAuth handle instead");
        assert_eq!(seen[0].1.len(), 0x30);
        assert_eq!(decode_location(&seen[0].1), fix);
        assert!(CALLS.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
    }

    /// Managed reads a fixed-size struct and is handed no length, so a short buffer is an
    /// out-of-bounds read inside the game rather than a truncated event. The receiver must
    /// never see it.
    #[test]
    fn an_undersized_event_payload_is_dropped_rather_than_delivered() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        register_caller(&ctx);
        register(&ctx, EventSlot::LocationUpdate, 0xB18, size_of::<LocationData>());

        // Eight bytes where the receiver reads 0x30.
        raise(&ctx, EventSlot::LocationUpdate, vec![0u8; 8]);
        unsafe { deliver_all(&ctx, ctx.take_completions()) };

        assert!(
            events().is_empty(),
            "a payload shorter than the receiver's struct must not reach it"
        );
    }

    /// Managed passes null for handles it registered no listener for, so this is a normal
    /// state and must be a no-op rather than a call to address zero.
    #[test]
    fn a_null_event_handle_is_dropped_rather_than_called() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        let ctx = Context::new();
        register_caller(&ctx);
        ctx.lock().callbacks.events[EventSlot::ChannelAuth as usize] = core::ptr::null_mut();
        unsafe { deliver_all(&ctx, vec![Completion::AuthTicket("t".into())]) };

        assert!(events().is_empty());
        // The ticket is still recorded: a missing listener is not a reason to lose the session.
        assert_eq!(ctx.lock().auth_ticket.as_deref(), Some("t"));
    }

    /// Before `SetCallbacks` there is no `call` delegate at all, and the auth path still has
    /// to work — it is the one place a missing callback must not cascade.
    #[test]
    fn raise_queues_an_event_for_the_next_update() {
        let ctx = Context::new();
        raise(&ctx, EventSlot::LocationUpdate, vec![1, 2, 3]);
        let ready = ctx.take_completions();
        assert_eq!(ready.len(), 1);
        match &ready[0] {
            Completion::Event { slot, payload } => {
                assert_eq!(*slot, EventSlot::LocationUpdate);
                assert_eq!(payload, &vec![1, 2, 3]);
            }
            _ => panic!("expected an event completion"),
        }
        // Drained, so the next frame does not fire it again.
        assert!(ctx.take_completions().is_empty());
    }

    // --- detached producers, which have no `Context` --------------------------
    //
    // These take `live::test_guard()` rather than the local `GATE`: the queue published here is
    // the same process-global the JNI natives raise onto, and `natives::tests` drives those.
    // One lock across both keeps the two from publishing over each other.

    /// From boot until `SetCallbacks` there is no published queue, and the sensor natives are
    /// already able to run. An event raised in that window is dropped -- not buffered, and not
    /// a panic, since these run at the sensor rate.
    #[test]
    fn a_detached_raise_before_publishing_goes_nowhere() {
        let _g = crate::live::test_guard();
        unpublish();

        raise_detached(EventSlot::LocationUpdate, vec![1, 2, 3]);

        // Publishing afterwards must not reveal it: dropped means dropped, not queued.
        let ctx = Context::new();
        publish(ctx.pending());
        assert!(ctx.take_completions().is_empty());
        unpublish();
    }

    /// The reason `publish` exists: a producer with no `Context` -- the JNI sensor natives,
    /// which get a `jobject` receiver and scalars and no plugin handle -- puts an event on the
    /// same pipe `OnUpdate` drains.
    #[test]
    fn a_detached_raise_lands_on_the_published_queue() {
        let _g = crate::live::test_guard();
        unpublish();

        let ctx = Context::new();
        publish(ctx.pending());
        raise_detached(EventSlot::LocationUpdate, vec![9, 9, 9]);

        let ready = ctx.take_completions();
        assert_eq!(ready.len(), 1);
        match &ready[0] {
            Completion::Event { slot, payload } => {
                assert_eq!(*slot, EventSlot::LocationUpdate);
                assert_eq!(payload, &vec![9, 9, 9]);
            }
            _ => panic!("expected an event completion"),
        }
        unpublish();
    }

    /// `DestroyContext` unpublishes. The six handles die with the context, so a producer left
    /// raising into its queue would only fill it until `MAX_PENDING` began evicting.
    #[test]
    fn unpublishing_stops_detached_events() {
        let _g = crate::live::test_guard();
        unpublish();

        let ctx = Context::new();
        publish(ctx.pending());
        unpublish();
        raise_detached(EventSlot::LocationUpdate, vec![7]);

        assert!(ctx.take_completions().is_empty());
    }

    /// A null GCHandle would be a leaked request, so the guard is worth proving even though
    /// managed never passes one.
    #[test]
    fn delivery_refuses_a_null_handle() {
        let _g = GATE.lock().unwrap_or_else(|e| e.into_inner());
        reset();

        unsafe {
            deliver_rpc(0, record_call, record_free, 1, RPC_STATUS_SUCCESS, &[]);
        }
        assert!(CALLS.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
        assert!(FREES.lock().unwrap_or_else(|e| e.into_inner()).is_empty());
    }

    /// The queue is what stands between a fast game and a slow server. Unbounded, it is a
    /// memory leak that ends in an OOM kill; the cap is the whole reason it is bounded.
    #[test]
    fn the_completion_queue_is_capped() {
        let q = Mutex::new(Vec::new());
        for i in 0..MAX_PENDING + 8 {
            push(&q, Completion::AuthTicket(format!("t{i}")));
        }
        let g = q.lock().unwrap_or_else(|e| e.into_inner());
        assert_eq!(g.len(), MAX_PENDING);
        match g.last() {
            Some(Completion::AuthTicket(t)) => assert_eq!(t, &format!("t{}", MAX_PENDING + 7)),
            _ => panic!("the newest completion must survive"),
        }
    }

    // --- the wire, end to end ------------------------------------------------

    /// A one-shot HTTP server on a loopback port, answering every request with `body`.
    ///
    /// Plain `std::net`, and the request is read until headers *and* `Content-Length` bytes
    /// arrive rather than in a single `read` — the client writes headers and body in separate
    /// syscalls, and a one-shot read races that. That race is what made the previous
    /// hand-rolled client's socket test flaky.
    /// A reply as the dev server would send it, so the tests describe the wire rather than
    /// hand-written bytes that could drift from the generated types.
    fn reply(rpc_id: Option<i32>, status: Option<i32>, payloads: &[&[u8]]) -> Vec<u8> {
        devrpc::RpcResponse {
            rpc_id,
            status,
            payloads: payloads
                .iter()
                .enumerate()
                .map(|(i, p)| devrpc::RpcPayload {
                    // The stub discards this, so the value is arbitrary -- but it is set, so a
                    // test proving it is discarded is proving something.
                    method: 1000 + i as i32,
                    payload: p.to_vec(),
                })
                .collect(),
        }
        .encode_to_vec()
    }

    fn serve_once(status_line: &str, body: Vec<u8>) -> (String, Receiver<Vec<u8>>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let addr = listener.local_addr().expect("local_addr");
        let (tx, rx) = channel();
        let mut response =
            format!("{status_line}\r\nContent-Length: {}\r\nContent-Type: application/x-protobuf\r\nConnection: close\r\n\r\n", body.len())
                .into_bytes();
        response.extend_from_slice(&body);

        std::thread::spawn(move || {
            let Ok((mut sock, _)) = listener.accept() else {
                return;
            };
            let mut buf: Vec<u8> = Vec::new();
            let mut chunk = [0u8; 512];
            let mut want: Option<usize> = None;
            loop {
                if let Some(end) = want {
                    if buf.len() >= end {
                        break;
                    }
                }
                match std::io::Read::read(&mut sock, &mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        buf.extend_from_slice(&chunk[..n]);
                        if want.is_none() {
                            if let Some(p) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                                want = Some(p + 4 + content_length(&buf[..p]));
                            }
                        }
                    }
                }
            }
            let _ = std::io::Write::write_all(&mut sock, &response);
            let _ = std::io::Write::flush(&mut sock);
            // Raw bytes, not a lossy string: the request body is protobuf and can contain
            // anything, and a lossy conversion would corrupt exactly the bytes under test.
            let _ = tx.send(buf);
        });

        (format!("http://{addr}"), rx)
    }

    fn content_length(headers: &[u8]) -> usize {
        String::from_utf8_lossy(headers)
            .lines()
            .find_map(|l| {
                let (k, v) = l.split_once(':')?;
                k.eq_ignore_ascii_case("content-length")
                    .then(|| v.trim().parse().ok())?
            })
            .unwrap_or(0)
    }

    #[test]
    fn a_reply_is_decoded_and_padded_to_the_request_count() {
        let (base, _rx) = serve_once(
            "HTTP/1.1 200 OK",
            reply(Some(7), Some(RPC_STATUS_SUCCESS), &[&[0, 1, 2]]),
        );
        let c = fetch_reply(
            &base,
            b"{}",
            &[],
            Duration::from_secs(5),
            3,
            target(7),
        );
        match c {
            Completion::Rpc { rpc_id, status, payloads, .. } => {
                assert_eq!(rpc_id, 7);
                assert_eq!(status, RPC_STATUS_SUCCESS);
                // Padded to exactly 3: managed indexes `payloads[i]` against `methods[i]`,
                // so a short array is an out-of-bounds read in the game.
                assert_eq!(payloads.len(), 3);
                assert_eq!(payloads[0], vec![0, 1, 2]);
                assert_eq!(payloads[1], Vec::<u8>::new());
                assert_eq!(payloads[2], Vec::<u8>::new());
            }
            _ => panic!("expected an rpc completion"),
        }
    }

    #[test]
    fn a_longer_reply_is_truncated_to_the_request_count() {
        let (base, _rx) = serve_once(
            "HTTP/1.1 200 OK",
            reply(Some(1), Some(RPC_STATUS_SUCCESS), &[&[0], &[1], &[2]]),
        );
        match fetch_reply(&base, b"{}", &[], Duration::from_secs(5), 1, target(3)) {
            Completion::Rpc { payloads, .. } => assert_eq!(payloads, vec![vec![0]]),
            _ => panic!("expected an rpc completion"),
        }
    }

    /// The id the game is waiting on comes from the game, never from the reply.
    #[test]
    fn the_echoed_rpc_id_is_ignored() {
        let (base, _rx) = serve_once("HTTP/1.1 200 OK", reply(Some(999), Some(1), &[]));
        match fetch_reply(&base, b"{}", &[], Duration::from_secs(5), 0, target(7)) {
            Completion::Rpc { rpc_id, payloads, .. } => {
                assert_eq!(rpc_id, 7);
                assert!(payloads.is_empty());
            }
            _ => panic!("expected an rpc completion"),
        }
    }

    /// `RpcPayload.method` labels the payload in the dev server's log and has nowhere to go on
    /// the managed side, where payloads are paired with requests positionally. Dropping it is
    /// deliberate, so this pins that it stays dropped and cannot silently become an array on
    /// the wire that the ABI would then try to interpret.
    #[test]
    fn the_per_payload_method_is_dropped_rather_than_delivered() {
        let (base, _rx) = serve_once(
            "HTTP/1.1 200 OK",
            reply(Some(7), Some(RPC_STATUS_SUCCESS), &[b"ab", b"cd"]),
        );
        match fetch_reply(&base, b"{}", &[], Duration::from_secs(5), 2, target(7)) {
            Completion::Rpc { payloads, .. } => {
                assert_eq!(payloads, vec![b"ab".to_vec(), b"cd".to_vec()]);
            }
            _ => panic!("expected an rpc completion"),
        }
    }

    #[test]
    fn a_missing_status_becomes_server_error_rather_than_success() {
        let (base, _rx) = serve_once("HTTP/1.1 200 OK", reply(None, None, &[]));
        match fetch_reply(&base, b"{}", &[], Duration::from_secs(5), 0, target(7)) {
            // Defaulting to Success here would tell the game a request that never landed
            // had succeeded.
            Completion::Rpc { status, .. } => assert_eq!(status, RPC_STATUS_SERVER_ERROR),
            _ => panic!("expected an rpc completion"),
        }
    }

    /// Every one of these must still produce a completion. A dropped request means the
    /// callback never fires and the game waits on it forever.
    #[test]
    fn every_failure_still_answers_with_server_error() {
        let cases: [(&str, Vec<u8>); 3] = [
            ("HTTP/1.1 500 Internal Server Error", Vec::new()),
            // Not protobuf at all. `\xff` is a valid length-delimited tag but the length that
            // follows runs past the end of the buffer, so this is a real decode failure rather
            // than an empty message -- which would decode fine and take the success path.
            ("HTTP/1.1 200 OK", vec![0xff, 0xff, 0xff, 0xff]),
            ("HTTP/1.1 200 OK", b"not protobuf at all".to_vec()),
        ];
        for (status_line, body) in cases {
            let (base, _rx) = serve_once(status_line, body.clone());
            match fetch_reply(&base, b"{}", &[], Duration::from_secs(5), 2, target(7)) {
                Completion::Rpc { status, payloads, rpc_id, .. } => {
                    assert_eq!(status, RPC_STATUS_SERVER_ERROR, "for {body:?}");
                    assert!(payloads.is_empty(), "for {body:?}");
                    assert_eq!(rpc_id, 7);
                }
                _ => panic!("expected an rpc completion for {body:?}"),
            }
        }
    }

    /// The request the dev server will actually receive: this is the contract with
    /// `tools/devserver.py`, and it is the whole point of the exercise.
    #[test]
    fn the_request_on_the_wire_carries_the_methods_and_bodies() {
        let (base, rx) = serve_once("HTTP/1.1 200 OK", reply(Some(12), Some(1), &[]));

        let methods = [130i32, 5];
        let sizes = [2i32, 1];
        let bodies = b"xyz";
        let batch =
            unsafe { copy_batch(2, methods.as_ptr(), bodies.as_ptr() as *const c_void, sizes.as_ptr()) }
                .expect("batch");
        let wire = devrpc::RpcRequest {
            rpc_id: 12,
            timeout_ms: 3000,
            retry_delay_ms: 0,
            requests: batch
                .items
                .iter()
                .map(|(m, b)| devrpc::RpcItem {
                    method: *m,
                    body: b.clone(),
                })
                .collect(),
        }
        .encode_to_vec();

        let _ = fetch_reply(
            &base,
            &wire,
            &context_headers(&Some("tok".into()), None, 12),
            Duration::from_secs(5),
            2,
            target(12),
        );

        let raw = rx.recv_timeout(Duration::from_secs(5)).expect("the server saw a request");
        let head_end = raw
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .expect("a complete request head");
        let head = String::from_utf8_lossy(&raw[..head_end]);
        assert!(head.starts_with("POST /rpc HTTP/1.1"), "got: {head}");
        assert!(head.contains("x-dittobuffer-rpc-id: 12"), "got: {head}");
        assert!(head.contains("authorization: tok"), "got: {head}");
        assert!(
            head.contains("content-type: application/x-protobuf"),
            "got: {head}"
        );

        // Decoded with the generated type rather than compared against hand-written bytes, so
        // this fails if the schema and the encoder ever disagree.
        let sent = devrpc::RpcRequest::decode(&raw[head_end + 4..]).expect("valid protobuf body");
        assert_eq!(sent.rpc_id, 12);
        assert_eq!(sent.timeout_ms, 3000);
        assert_eq!(sent.requests.len(), 2);
        assert_eq!(sent.requests[0].method, 130);
        // "xy" then "z", split out of the one flat blob.
        assert_eq!(sent.requests[0].body, b"xy");
        assert_eq!(sent.requests[1].method, 5);
        assert_eq!(sent.requests[1].body, b"z");
    }
}

#[cfg(test)]
mod rehearsal {
    //! Checks the client against the *real* dev server rather than a stand-in.
    //!
    //! The in-module tests use a hand-built HTTP stub, which proves the client is
    //! self-consistent but says nothing about whether `tools/devserver.py` and `server.rs`
    //! agree — and a disagreement is exactly what the device would otherwise be the first to
    //! discover, at the cost of a build/mount/launch cycle per iteration.
    //!
    //! Ignored by default because it needs the server running:
    //!
    //! ```text
    //! python3 tools/devserver.py &
    //! cargo test --target x86_64-unknown-linux-gnu --lib -- --ignored rehearsal
    //! ```

    use super::*;

    const LOCAL: &str = "http://127.0.0.1:8080";

    #[test]
    #[ignore = "needs tools/devserver.py listening on 127.0.0.1:8080"]
    fn the_dev_server_answers_a_batch_the_way_the_client_expects() {
        // A body with real protobuf structure, so the server's schema decoding is exercised
        // too -- these are the first two fields of a `DownloadSettingsActionProto`.
        let body = vec![0x08, 0x96, 0x01, 0x12, 0x02, b'h', b'i'];
        let wire = devrpc::RpcRequest {
            rpc_id: 4242,
            timeout_ms: 3000,
            retry_delay_ms: 0,
            requests: vec![
                devrpc::RpcItem {
                    method: 130,
                    body,
                },
                // Empty, which is the normal shape for a request that carries nothing and is
                // distinct from the field being absent.
                devrpc::RpcItem {
                    method: 5,
                    body: Vec::new(),
                },
            ],
        }
        .encode_to_vec();

        let headers = context_headers(&Some("rehearsal-ticket".into()), None, 4242);
        match fetch_reply(LOCAL, &wire, &headers, Duration::from_secs(5), 2, ReplyTarget {
            callback_handle: 0xDEC0DE,
            call: unreachable_call,
            free: unreachable_free,
            rpc_id: 4242,
        }) {
            Completion::Rpc { rpc_id, status, payloads, .. } => {
                assert_eq!(rpc_id, 4242, "the id the game asked for must come back");
                assert_eq!(status, RPC_STATUS_SUCCESS, "devserver.py's default status is 1");
                assert_eq!(
                    payloads.len(),
                    2,
                    "one payload per request, so managed's payloads[i]/methods[i] pairing holds"
                );
            }
            _ => panic!("expected an rpc completion"),
        }
    }

    unsafe extern "C" fn unreachable_call(_h: *mut c_void, _b: *mut u8) {
        panic!("the rehearsal never delivers, so this must not be called");
    }

    unsafe extern "C" fn unreachable_free(_h: *mut c_void) {}

    /// The auth path, which is the other half of the handshake.
    #[test]
    #[ignore = "needs tools/devserver.py listening on 127.0.0.1:8080"]
    fn the_dev_server_issues_a_ticket() {
        let wire = devrpc::AuthRequest {
            auth_id: "rehearsal".into(),
            token: b"\x01\x02\x03".to_vec(),
            prevent_account_creation: false,
        }
        .encode_to_vec();

        let reply: devrpc::AuthResponse =
            post_proto(LOCAL, "/get_auth_token", &wire, &[], Duration::from_secs(5))
                .expect("the dev server must answer /get_auth_token");
        assert!(
            reply.ticket.is_some_and(|t| !t.is_empty()),
            "an empty ticket would leave every later /rpc unauthenticated"
        );
    }
}
