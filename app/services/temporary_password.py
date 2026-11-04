"""Temporary passwords an administrator reads out or writes down for one person."""

import secrets

# No 0/O, 1/l/I: easy to read aloud and copy by hand.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GROUPS = 3
_GROUP_LENGTH = 4


def generate_temporary_password() -> str:
    """Like "Xk7p-Rm3v-9Qth": 12 random characters (about 69 bits), in groups of four."""

    return "-".join(
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LENGTH)) for _ in range(_GROUPS)
    )
