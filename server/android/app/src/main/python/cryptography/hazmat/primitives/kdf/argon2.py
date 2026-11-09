"""Argon2id API surface, backed by ``hashlib.scrypt`` inside the APK.

Chaquopy cannot install native ``cryptography`` or ``argon2`` wheels, and
``argon2pure`` ships no wheel at all. The server only needs a memory-hard
password KDF with a self-describing encoding, so this shim provides the same
``Argon2id`` class on top of stdlib scrypt (OpenSSL, native, ~16 MiB per hash).

Stored strings look like::

    $scrypt$ln=14,r=8,p=1$<base64 salt>$<base64 digest>

They record their own parameters, so a phone-local database stays readable.
They are deliberately *not* interchangeable with the desktop Argon2 hashes;
the APK keeps its own SQLite file, so the two never meet.
"""

import base64
import hashlib
import hmac

from cryptography.exceptions import InvalidKey

# 2**14 blocks * 8 r * 128 bytes = 16 MiB, close to the desktop Argon2id
# memory cost, and fast enough for an interactive login on a phone.
_LOG_N = 14
_R = 8
_P = 1
_MAX_MEM = 64 * 1024 * 1024


def _b64(raw):
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.b64decode(text + "=" * (-len(text) % 4))


class Argon2id:
    """Same constructor and methods as ``cryptography``'s Argon2id."""

    def __init__(self, salt, length=32, iterations=2, lanes=1, memory_cost=19456):
        salt = bytes(salt)
        if not salt:
            raise ValueError("salt must not be empty")
        self.salt = salt
        self.length = int(length)
        # Accepted for API compatibility with the desktop server. The scrypt
        # parameters above are fixed and recorded in the encoded string.
        self.iterations = int(iterations)
        self.lanes = int(lanes)
        self.memory_cost = int(memory_cost)

    def _derive(self, password):
        return hashlib.scrypt(
            password,
            salt=self.salt,
            n=1 << _LOG_N,
            r=_R,
            p=_P,
            dklen=self.length,
            maxmem=_MAX_MEM,
        )

    def derive_phc_encoded(self, password):
        raw = self._derive(bytes(password))
        return "$scrypt$ln=%d,r=%d,p=%d$%s$%s" % (
            _LOG_N, _R, _P, _b64(self.salt), _b64(raw),
        )

    @staticmethod
    def verify_phc_encoded(password, encoded):
        try:
            prefix, algorithm, params, salt, digest = encoded.split("$")
        except (AttributeError, ValueError):
            raise InvalidKey("invalid password hash")
        if prefix != "" or algorithm != "scrypt":
            raise InvalidKey("unsupported password hash")
        try:
            values = dict(part.split("=", 1) for part in params.split(","))
            log_n = int(values["ln"])
            r = int(values["r"])
            p = int(values["p"])
            salt_bytes = _unb64(salt)
            expected = _unb64(digest)
        except Exception:
            raise InvalidKey("invalid password hash")
        if not 10 <= log_n <= 20 or not 1 <= r <= 32 or not 1 <= p <= 16:
            raise InvalidKey("invalid password hash")
        if not salt_bytes or not expected:
            raise InvalidKey("invalid password hash")
        actual = hashlib.scrypt(
            bytes(password),
            salt=salt_bytes,
            n=1 << log_n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_MAX_MEM,
        )
        if not hmac.compare_digest(actual, expected):
            raise InvalidKey("password mismatch")
        return True
