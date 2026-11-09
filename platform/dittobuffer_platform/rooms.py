"""Rooms: signed, lightweight JSON documents exchanged over HiveMQ.

Trust model
-----------
* A domain is claimed once (first come, first served) and bound to the host's
  RSA public key. Only the holder of the matching private key can publish room
  state or change room settings.
* Every state document is a signed envelope (PKCS#1 v1.5 / SHA-256) with a
  monotonic room version, issue time and nonce. The platform verifies the
  signature before storing anything, and clients verify the same signature
  against the public key pinned at bootstrap. Publishing garbage to the open
  HiveMQ topic therefore cannot hijack a room.
* Browsers/apps receive a bootstrap document signed by the platform key, so a
  rogue broker cannot substitute a different host key.
* Assets, protocol buffers and the client itself are never uploaded here.
  A room document is only settings, POI/spawn summaries, event schedules and
  save metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from . import liveops as liveops_mod
from . import rsa_min

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,46}[a-z0-9]$")
MAX_STATE_BYTES = 1_500_000
MAX_ROOMS = 5000
RECENT_NONCES = 200
EVENT_LIMIT = 120


def now_ms() -> int:
    return int(time.time() * 1000)


def normalise_domain(value) -> str:
    """Turn a typed name into one MQTT-safe room name.

    A room is not a domain: "Fairview Community!" becomes
    "fairview-community". Nothing is registered in DNS and no TLD is required.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Give the room a name")
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:48].strip("-")
    if len(slug) < 2 or not SLUG.fullmatch(slug):
        raise ValueError("Use at least two letters or digits in the room name")
    return slug


# The UI says "room name"; older code and saves still say "domain".
normalise_room = normalise_domain


def digest(value) -> str:
    return hashlib.sha256(rsa_min.canonical(value)).hexdigest()


def _clean_text(value, limit: int, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{field} is limited to {limit} characters")
    return text


class Platform:
    """The whole platform state, backed by one JSON save file."""

    def __init__(self, store, host_key: dict | None = None, config: dict | None = None):
        self.store = store
        self.host_key = host_key or rsa_min.generate()
        self.config = config or {}
        self.started_ms = now_ms()
        self.presence: dict[str, dict] = {}
        self.mqtt = {"connected": False, "error": "", "last_ms": 0, "received": 0,
                     "published": 0, "room_published_ms": 0, "room_error": ""}

    # ------------------------------------------------------------------ keys
    @property
    def public_key(self) -> dict:
        return rsa_min.public(self.host_key)

    def set_signing_key(self, key: dict) -> dict:
        """Replace the in-memory platform signing key."""
        if not isinstance(key, dict) or not str(key.get("n") or "") or not str(key.get("d") or ""):
            raise ValueError("A complete RSA private key is required")
        self.host_key = key
        return self.public_key

    def reset_rooms(self) -> None:
        """Forget every room binding while keeping platform defaults."""
        with self.store.lock:
            self.store.data["rooms"] = {}
            self.store.data.setdefault("platform", {})["hosted_room"] = ""
            self.store.save()

    def bootstrap(self, domain: str) -> dict:
        room = self.get(domain)
        if room is None:
            raise KeyError(domain)
        hosted = self.hosted_domain
        if hosted and room["domain"] != hosted:
            raise PermissionError("This room is not hosted on this platform")
        unsigned = {
            "v": 1,
            "platform": self.store.data.get("platform", {}).get("name", "Dittobuffer Platform"),
            "domain": room["domain"],
            "room": self.public_room(room),
            "host_public": room["host_public"],
            "state": room.get("state"),
            "host_fingerprint": room.get("host_fingerprint", ""),
            "settings": room.get("settings", {}),
            "defaults": self.defaults(),
            "issued_ms": now_ms(),
            "digest": digest(room.get("state") or {}),
        }
        unsigned["platform_public"] = self.public_key
        unsigned["sig"] = rsa_min.sign(self.host_key, rsa_min.canonical(unsigned))
        return unsigned

    # -------------------------------------------------------------- defaults
    def defaults(self) -> dict:
        """Platform-wide spawn mix applied when a room publishes none."""
        with self.store.lock:
            value = self.store.data.setdefault("platform", {}).get("defaults") or {}
            return json.loads(json.dumps(value))

    def save_defaults(self, value: dict) -> dict:
        if not isinstance(value, dict):
            raise ValueError("Platform defaults must be an object")
        percent = value.get("spawn_percent") or {}
        if not isinstance(percent, dict):
            raise ValueError("spawn_percent must be an object of species=percent")
        clean = {}
        total = 0.0
        for key, share in percent.items():
            text = str(key).strip()
            parts = text.split(":")
            if not parts[0].isdigit() or int(parts[0]) <= 0:
                raise ValueError("Spawn percentages need a species id")
            if len(parts) > 1 and not parts[1].isdigit():
                raise ValueError("Form must be a number")
            share = float(share)
            if not 0 <= share <= 100:
                raise ValueError("Spawn percentages must be between 0 and 100")
            clean[text] = round(share, 3)
            total += share
        if total > 100.001:
            raise ValueError("Spawn percentages add up to more than 100%")
        with self.store.lock:
            self.store.data.setdefault("platform", {})["defaults"] = {"spawn_percent": clean}
            self.store.save()
        return self.defaults()

    # --------------------------------------------------------------- live-ops
    def liveops(self) -> dict:
        """Platform-owned room live-ops; no server workspace required."""
        with self.store.lock:
            value = self.store.data.setdefault("platform", {}).get("liveops")
        try:
            return liveops_mod.normalise(value or {})
        except (TypeError, ValueError):
            return liveops_mod.defaults()

    def save_liveops(self, value: dict) -> dict:
        clean = liveops_mod.normalise(value)
        with self.store.lock:
            self.store.data.setdefault("platform", {})["liveops"] = clean
            self.store.save()
        return clean

    # --------------------------------------------------------------- hosting
    @property
    def hosted_domain(self) -> str:
        with self.store.lock:
            return str(self.store.data.setdefault("platform", {}).get("hosted_room") or "")

    def hosted(self) -> dict | None:
        domain = self.hosted_domain
        return self.get(domain) if domain else None

    def host(self, domain: str) -> dict:
        """Host one room on this PC. Hosting a second room moves the platform."""
        room = self.get(normalise_domain(domain))
        if room is None:
            raise KeyError(domain)
        with self.store.lock:
            self.store.data.setdefault("platform", {})["hosted_room"] = room["domain"]
            self.store.save()
        self._record(room, "Room hosted on this platform")
        return self.public_room(room)

    def unhost(self, domain: str = "") -> dict:
        with self.store.lock:
            current = str(self.store.data.setdefault("platform", {}).get("hosted_room") or "")
            if domain and normalise_domain(domain) != current:
                raise KeyError(domain)
            self.store.data["platform"]["hosted_room"] = ""
            self.store.save()
        return {"hosted": ""}

    def available(self) -> list[dict]:
        """Only the hosted room is advertised: a PC hosts one room at a time."""
        room = self.hosted()
        return [room] if room else []

    # ----------------------------------------------------------------- rooms
    def rooms(self) -> list[dict]:
        return sorted(self.store.rooms().values(), key=lambda room: room.get("created_ms", 0), reverse=True)

    def get(self, domain: str) -> dict | None:
        return self.store.room(str(domain).strip().lower())

    def room(self, domain: str) -> dict | None:
        """Alias used by tests and callers that prefer ``room``."""
        return self.get(domain)

    def register_room(self, domain: str, document: dict) -> dict:
        """Bind or refresh a room from the host's retained signed document.

        Room names need no registration: whoever holds the key that signs the
        document owns the name, and the key is pinned on first sight.
        """
        if not isinstance(document, dict):
            raise ValueError("Room document must be an object")
        # Room documents carry ``host_public``; signed envelopes carry ``pub``.
        host = document.get("host_public") or document.get("pub") or {}
        if not str(host.get("n") or ""):
            raise ValueError("Room document has no host key")
        unsigned = {key: value for key, value in document.items() if key != "sig"}
        if not rsa_min.verify(host, rsa_min.canonical(unsigned), str(document.get("sig") or "")):
            raise ValueError("Room document signature is invalid")
        if str(document.get("room") or "") != domain:
            raise ValueError("Room document belongs to another room")
        hosted = self.hosted_domain
        if hosted and domain != hosted:
            raise ValueError("This platform only hosts " + hosted)
        host = {"n": str(host["n"]), "e": int(host.get("e", 65537))}
        with self.store.lock:
            stamp = now_ms()
            room = self.store.data["rooms"].get(domain)
            if room is None:
                if len(self.store.data["rooms"]) >= MAX_ROOMS:
                    raise ValueError("The platform has reached its room limit")
                title = _clean_text(document.get("title") or domain, 80, "Title")
                token = secrets.token_urlsafe(32)
                room = {
                    "domain": domain, "room_id": secrets.token_hex(12),
                    "display_name": title, "title": title,
                    "description": _clean_text(document.get("description") or "", 400, "Description"),
                    "owner": "", "host_public": None, "host_fingerprint": "",
                    "manage_hash": hashlib.sha256(b"dittobuffer-room:" + token.encode()).hexdigest(),
                    "created_ms": stamp, "updated_ms": stamp, "last_publish_ms": 0,
                    "nonces": [], "events": [], "state": None, "settings": {},
                    "stats": {"publishes": 0, "rejected": 0, "bootstraps": 0},
                }
                self.store.data["rooms"][domain] = room
            if not room.get("host_public"):
                room["host_public"] = host
                room["host_fingerprint"] = rsa_min.fingerprint(host)
                self._record(room, "Room name claimed by its host")
            elif rsa_min.fingerprint(host) != room.get("host_fingerprint"):
                room.setdefault("stats", {})["rejected"] = room.get("stats", {}).get("rejected", 0) + 1
                raise ValueError("Room name is owned by a different host key")
            body = (document.get("state") or {}).get("body") or {}
            previous = (room.get("state") or {}).get("version", 0)
            room["title"] = _clean_text(document.get("title") or room.get("title") or domain,
                                        80, "Title")
            room["display_name"] = room["title"]
            if document.get("description") is not None:
                room["description"] = _clean_text(document.get("description") or "", 400,
                                                   "Description")
            room["state"] = {
                "version": int(body.get("version") or previous + 1),
                "issued_ms": int(document.get("issued_ms") or stamp),
                "digest": digest(body),
                "body": body,
                "sig": str(document.get("sig") or ""),
                "signed": {"kind": "room", "room": domain},
            }
            room["last_publish_ms"] = stamp
            room["updated_ms"] = stamp
            room.setdefault("stats", {})["publishes"] = room.get("stats", {}).get("publishes", 0) + 1
            self.store.save()
        return self.public_room(room)

    def public_room(self, room: dict) -> dict:
        return {
            "domain": room["domain"],
            "name": room.get("display_name") or room.get("title") or room["domain"],
            "room_id": room["room_id"],
            "title": room.get("title", ""),
            "description": room.get("description", ""),
            "owner": room.get("owner", ""),
            "created_ms": room.get("created_ms", 0),
            "updated_ms": room.get("updated_ms", 0),
            "version": (room.get("state") or {}).get("version", 0),
            "state_bytes": len(rsa_min.canonical(room.get("state") or {})),
            "last_publish_ms": room.get("last_publish_ms", 0),
            "host_fingerprint": room.get("host_fingerprint", ""),
            "players": len((self.presence.get(room["domain"]) or {}).get("clients", {})),
            "online": bool(room.get("last_publish_ms") and now_ms() - room["last_publish_ms"] < 120_000),
            "hosted": room["domain"] == self.hosted_domain,
        }

    def create(self, body: dict) -> dict:
        # One click is enough: a name is optional and so is the host key. The
        # key binds itself the first time the host publishes its signed room
        # document, so nothing needs pasting or pre-registering.
        typed = str(body.get("name") or body.get("domain") or body.get("title") or "").strip()
        host_public = body.get("host_public")
        if host_public is not None and (not isinstance(host_public, dict)
                                        or not str(host_public.get("n") or "")):
            raise ValueError("The host public key is malformed")
        if host_public:
            try:
                host_public = {"n": str(host_public["n"]), "e": int(host_public.get("e", 65537))}
            except (KeyError, TypeError, ValueError):
                raise ValueError("The host public key is malformed") from None
            if len(host_public["n"]) < 200:
                raise ValueError("Use an RSA key of at least 2048 bits")
        else:
            host_public = None
        with self.store.lock:
            if typed:
                # An explicitly chosen name is claimed once; only the one-click
                # generated name retries on a collision.
                domain = normalise_domain(typed)
                if domain in self.store.data["rooms"]:
                    raise ValueError("That room name is already taken")
            else:
                domain = "room-" + secrets.token_hex(3)
                while domain in self.store.data["rooms"]:
                    domain = "room-" + secrets.token_hex(3)
                typed = domain
            if len(self.store.data["rooms"]) >= MAX_ROOMS:
                raise ValueError("The platform has reached its room limit")
            token = secrets.token_urlsafe(32)
            stamp = now_ms()
            room = {
                "domain": domain,
                "room_id": secrets.token_hex(12),
                "display_name": _clean_text(body.get("name") or body.get("title") or typed,
                                            80, "Name"),
                "title": _clean_text(body.get("title") or body.get("name") or typed,
                                     80, "Title"),
                "description": _clean_text(body.get("description") or "", 400, "Description"),
                "owner": _clean_text(body.get("owner") or "", 60, "Owner"),
                "host_public": host_public,
                "host_fingerprint": rsa_min.fingerprint(host_public) if host_public else "",
                "manage_hash": hashlib.sha256(b"dittobuffer-room:" + token.encode()).hexdigest(),
                "created_ms": stamp,
                "updated_ms": stamp,
                "last_publish_ms": 0,
                "nonces": [],
                "events": [{"ms": stamp, "text": "Room created"}],
                "state": None,
                "settings": {},
                "stats": {"publishes": 0, "rejected": 0, "bootstraps": 0},
            }
            self.store.data["rooms"][domain] = room
            if body.get("host"):
                self.store.data.setdefault("platform", {})["hosted_room"] = domain
            self.store.save()
        self._record(room, "Room claimed by " + (room["owner"] or "host"))
        return {"room": self.public_room(room), "manage_token": token, "host_fingerprint": room["host_fingerprint"]}

    # -------------------------------------------------------------- publishes
    def _check_nonce(self, room: dict, envelope: dict) -> None:
        nonce = str(envelope.get("nonce") or "")
        if not nonce:
            raise ValueError("Payload has no nonce")
        recent = room.setdefault("nonces", [])
        if nonce in recent:
            raise ValueError("Payload was replayed")
        recent.append(nonce)
        if len(recent) > RECENT_NONCES:
            del recent[: len(recent) - RECENT_NONCES]
        stamp = int(envelope.get("issued_ms") or 0)
        if abs(now_ms() - stamp) > 900_000:
            raise ValueError("Payload is too old or from a skewed clock")

    def publish_state(self, domain: str, envelope: dict) -> dict:
        room = self.get(domain)
        if room is None:
            raise KeyError(domain)
        self._check_envelope(room, envelope, "state")
        body = envelope["body"]
        if not isinstance(body, dict):
            raise ValueError("Room state must be a JSON object")
        if len(rsa_min.canonical(body)) > MAX_STATE_BYTES:
            raise ValueError("Room state is larger than 1.5 MB")
        with self.store.lock:
            previous = (room.get("state") or {}).get("version", 0)
            version = int(body.get("version") or previous + 1)
            if version <= previous:
                version = previous + 1
            room["state"] = {
                "version": version,
                "issued_ms": int(envelope.get("issued_ms") or now_ms()),
                "digest": digest(body.get("data") if "data" in body else body),
                "body": body,
                "sig": envelope["sig"],
                "signed": {key: value for key, value in envelope.items() if key != "body"},
            }
            room["last_publish_ms"] = now_ms()
            room["updated_ms"] = now_ms()
            room.setdefault("stats", {})["publishes"] += 1
            self.store.save()
        return self.public_room(room)

    def publish_settings(self, domain: str, envelope: dict) -> dict:
        room = self.get(domain)
        if room is None:
            raise KeyError(domain)
        self._check_envelope(room, envelope, "room.settings")
        body = envelope["body"]
        if not isinstance(body, dict):
            raise ValueError("Room settings must be an object")
        with self.store.lock:
            room["settings"] = {
                "motd": _clean_text(body.get("motd") or "", 200, "MOTD"),
                "public": bool(body.get("public", True)),
                "max_players": max(1, min(500, int(body.get("max_players", 60)))),
                "theme": str(body.get("theme") or "auto") if body.get("theme") in ("auto", "light", "dark") else "auto",
                "updated_ms": now_ms(),
            }
            room["updated_ms"] = now_ms()
            self.store.save()
        self._record(room, "Settings updated")
        return self.public_room(room)

    def _check_envelope(self, room: dict, envelope: dict, kind: str) -> None:
        if not isinstance(envelope, dict) or envelope.get("kind") != kind:
            raise ValueError("Unexpected payload kind")
        if not room.get("host_public"):
            # First signed publish binds the room name to this key (trust on
            # first use), so creating a room never asks for a pasted key.
            candidate = envelope.get("pub")
            if not isinstance(candidate, dict) or not str(candidate.get("n") or ""):
                raise ValueError("Publish a signed room document first")
            unsigned = {key: value for key, value in envelope.items() if key != "sig"}
            if not rsa_min.verify(candidate, rsa_min.canonical(unsigned),
                                  str(envelope.get("sig") or "")):
                raise ValueError("Signature verification failed")
            with self.store.lock:
                room["host_public"] = {"n": str(candidate["n"]),
                                       "e": int(candidate.get("e", 65537))}
                room["host_fingerprint"] = rsa_min.fingerprint(room["host_public"])
                room["updated_ms"] = now_ms()
                self.store.save()
            self._record(room, "Host key bound on first signed publish")
        try:
            verified = rsa_min.open_envelope(envelope, room["host_public"], domain=room["domain"])
        except ValueError:
            room.setdefault("stats", {})["rejected"] = room.get("stats", {}).get("rejected", 0) + 1
            self._record(room, "Rejected an unsigned or forged payload")
            self.store.save()
            raise
        self._check_nonce(room, envelope)
        return verified

    def delete(self, domain: str, token: str = "") -> bool:
        room = self.get(domain)
        if room is None:
            return False
        token = str(token or "")
        if token:
            given = hashlib.sha256(b"dittobuffer-room:" + token.encode()).hexdigest()
            if not secrets.compare_digest(given, room.get("manage_hash", "")):
                raise PermissionError("Manage token does not match")
        return self.store.drop_room(domain)

    # -------------------------------------------------------------- presence
    def touch_presence(self, domain: str, client_id: str, version: int = 0) -> None:
        room = self.presence.setdefault(domain, {"clients": {}})
        room["clients"][str(client_id)[:64]] = {"ms": now_ms(), "version": int(version or 0)}
        cutoff = now_ms() - 90_000
        room["clients"] = {key: value for key, value in room["clients"].items() if value["ms"] >= cutoff}

    def _record(self, room: dict, text: str) -> None:
        events = room.setdefault("events", [])
        events.append({"ms": now_ms(), "text": str(text)[:200]})
        if len(events) > EVENT_LIMIT:
            del events[: len(events) - EVENT_LIMIT]

    # ------------------------------------------------------------------ info
    def status(self) -> dict:
        rooms = self.store.rooms()
        publishes = sum(room.get("stats", {}).get("publishes", 0) for room in rooms.values())
        return {
            "platform": self.store.data.get("platform", {}).get("name", "Dittobuffer Platform"),
            "version": 1,
            "rooms": len(rooms),
            "online": sum(1 for room in rooms.values() if room.get("last_publish_ms") and now_ms() - room["last_publish_ms"] < 120_000),
            "publishes": publishes,
            "started_ms": self.started_ms,
            "mqtt": self.mqtt,
            "platform_public": self.public_key,
            "fingerprint": rsa_min.fingerprint(self.host_key),
        }
