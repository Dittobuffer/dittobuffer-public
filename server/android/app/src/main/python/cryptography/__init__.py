"""Compatibility shim: Chaquopy has no usable `cryptography` wheel.

`dittobuffer.core.accounts` only needs a memory-hard password KDF with a
self-describing encoding. `cryptography.hazmat.primitives.kdf.argon2.Argon2id`
is therefore provided by a stdlib-scrypt implementation; see that module for
the stored format and its portability notes.
"""

from .exceptions import InvalidKey

__all__ = ["InvalidKey"]
