"""Dependency-free RSA (PKCS#1 v1.5 / SHA-256) signing and verification.

The platform has to run on a bare Python install, including inside the
Dittobuffer mobile app where ``cryptography`` is not available. Keys are stored
as compact JSON numbers (base64url big-endian) instead of PEM, so a room's
public key is a few hundred bytes of JSON and can be pinned by the app.

This is a deliberately small, auditable implementation:

* RSA-2048 key generation with Miller-Rabin prime testing
* ``SHA256withRSA`` signatures (what Java calls ``SHA256withRSA``)
* constant-time signature comparison
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets

DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
PUBLIC_EXPONENT = 65537


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _int_bytes(value: int) -> bytes:
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def canonical(value) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _probable_prime(value: int, rounds: int = 40) -> bool:
    if value < 2:
        return False
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % prime == 0:
            return value == prime
    d, s = value - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        base = secrets.randbelow(value - 3) + 2
        x = pow(base, d, value)
        if x in (1, value - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, value)
            if x == value - 1:
                break
        else:
            return False
    return True


def _prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _probable_prime(candidate):
            return candidate


def generate(bits: int = 2048) -> dict:
    """Return a private key dict; the public part is ``{"n", "e"}``."""
    while True:
        p = _prime(bits // 2)
        q = _prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if phi % PUBLIC_EXPONENT == 0:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        d = pow(PUBLIC_EXPONENT, -1, phi)
        return {
            "n": _b64(_int_bytes(n)),
            "e": PUBLIC_EXPONENT,
            "d": _b64(_int_bytes(d)),
            "bits": bits,
        }


def public(key: dict) -> dict:
    """Strip the private exponent so a key can be published safely."""
    return {"n": key["n"], "e": int(key.get("e", PUBLIC_EXPONENT))}


def fingerprint(key: dict) -> str:
    """Stable identifier for a public key."""
    return "sha256:" + hashlib.sha256(canonical(public(key))).hexdigest()


def _padding(n_bits: int, digest: bytes) -> bytes:
    target = (n_bits + 7) // 8
    payload = DIGEST_INFO + digest
    if target < len(payload) + 11:
        raise ValueError("RSA key too small for SHA-256 signature")
    return b"\x00\x01" + b"\xff" * (target - len(payload) - 3) + b"\x00" + payload


def sign(key: dict, message: bytes) -> str:
    """Return a base64url PKCS#1 v1.5 SHA-256 signature."""
    n = int.from_bytes(_unb64(key["n"]), "big")
    d = int.from_bytes(_unb64(key["d"]), "big")
    encoded = _padding(n.bit_length(), hashlib.sha256(message).digest())
    signature = pow(int.from_bytes(encoded, "big"), d, n)
    return _b64(_int_bytes(signature))


def verify(key: dict, message: bytes, signature: str) -> bool:
    """Verify a base64url PKCS#1 v1.5 SHA-256 signature without raising."""
    try:
        n = int.from_bytes(_unb64(key["n"]), "big")
        e = int(key.get("e", PUBLIC_EXPONENT))
        raw = _unb64(signature)
        if len(raw) > (n.bit_length() + 7) // 8:
            return False
        recovered = _int_bytes(pow(int.from_bytes(raw, "big"), e, n))
        expected = _padding(n.bit_length(), hashlib.sha256(message).digest())
        if len(recovered) < len(expected):
            recovered = b"\x00" * (len(expected) - len(recovered)) + recovered
        return secrets.compare_digest(recovered, expected)
    except (KeyError, ValueError, TypeError):
        return False


def envelope(kind: str, domain: str, body, key: dict, *, now_ms: int | None = None) -> dict:
    """Build a signed, replay-resistant JSON envelope."""
    import time

    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    value = {
        "v": 1,
        "kind": kind,
        "room": domain,
        "issued_ms": stamp,
        "nonce": secrets.token_hex(12),
        "body": body,
        # Public half travels with the envelope so a room can bind its name on
        # the first signed publish without any separate registration.
        "pub": public(key),
    }
    value["sig"] = sign(key, canonical(value))
    return value


def open_envelope(value: dict, key: dict, *, domain: str | None = None,
                  max_age_ms: int = 900_000) -> dict:
    """Validate an envelope and return its body, or raise ``ValueError``."""
    import time

    if not isinstance(value, dict):
        raise ValueError("Signed payload must be an object")
    if value.get("v") != 1:
        raise ValueError("Unsupported payload version")
    signature = value.get("sig")
    body = value.get("body")
    if not isinstance(signature, str) or not isinstance(body, dict):
        raise ValueError("Signed payload is incomplete")
    unsigned = {key_name: item for key_name, item in value.items() if key_name != "sig"}
    if not verify(key, canonical(unsigned), signature):
        raise ValueError("Signature verification failed")
    stamp = int(value.get("issued_ms") or 0)
    if abs(time.time() * 1000 - stamp) > max_age_ms:
        raise ValueError("Signed payload expired")
    if domain is not None and value.get("room") != domain:
        raise ValueError("Signed payload belongs to another room")
    return body
