# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Compatibility shim: Chaquopy has no usable `cryptography` wheel.

`dittobuffer.core.accounts` only needs a memory-hard password KDF with a
self-describing encoding. `cryptography.hazmat.primitives.kdf.argon2.Argon2id`
is therefore provided by a stdlib-scrypt implementation; see that module for
the stored format and its portability notes.
"""

from .exceptions import InvalidKey

__all__ = ["InvalidKey"]
