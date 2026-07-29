import re
from typing import Literal


PushTokenProvider = Literal["expo", "unknown", "not_recorded"]

_EXPO_PUSH_TOKEN_PATTERN = re.compile(r"^(?:Expo|Exponent)PushToken\[[^\]\s]+\]$")


def normalize_push_token(token: object) -> str | None:
    if not isinstance(token, str):
        return None
    normalized = token.strip()
    return normalized or None


def push_token_provider(token: object) -> PushTokenProvider:
    normalized = normalize_push_token(token)
    if normalized is None:
        return "not_recorded"
    if _EXPO_PUSH_TOKEN_PATTERN.fullmatch(normalized):
        return "expo"
    return "unknown"


def is_structurally_valid_push_token(token: object) -> bool:
    return push_token_provider(token) == "expo"
