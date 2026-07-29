import hashlib
import threading
import time
from dataclasses import dataclass

from app.notifications.push import generate_provider_test_id
from app.notifications.tokens import is_structurally_valid_push_token
from app.services.notification_recipients import RecipientSelection


PROVIDER_TEST_DRY_RUN_TTL_SECONDS = 15 * 60


class ProviderTestGateError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PendingProviderTest:
    test_id: str
    recipient_user_id: int
    token_digest: str
    created_monotonic: float


_pending_provider_tests: dict[str, PendingProviderTest] = {}
_pending_provider_tests_lock = threading.Lock()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _validate_selection(
    selection: RecipientSelection,
    *,
    confirmed_recipient_user_id: int,
    physical_device_confirmed: bool,
) -> str:
    if not selection.controlled_test_mode:
        raise ProviderTestGateError("controlled_mode_required")
    if selection.selected_recipient_count != 1:
        raise ProviderTestGateError("exactly_one_recipient_required")
    if not selection.configured_recipient_resolved:
        raise ProviderTestGateError("configured_recipient_unresolved")
    if selection.recipient_user_id != confirmed_recipient_user_id:
        raise ProviderTestGateError("recipient_identity_mismatch")
    if selection.account_active_status == "inactive":
        raise ProviderTestGateError("inactive_recipient")
    if not selection.has_push_token or len(selection.tokens) != 1:
        if selection.failure_reason == "duplicate_push_token":
            raise ProviderTestGateError("duplicate_push_token")
        if selection.failure_reason == "invalid_push_token":
            raise ProviderTestGateError("invalid_push_token")
        raise ProviderTestGateError("push_token_required")
    if not selection.token_structurally_valid:
        raise ProviderTestGateError("invalid_push_token")
    if selection.token_duplicate_count != 0:
        raise ProviderTestGateError("duplicate_push_token")
    if not physical_device_confirmed:
        raise ProviderTestGateError("physical_device_confirmation_required")

    token = selection.tokens[0]
    if not is_structurally_valid_push_token(token):
        raise ProviderTestGateError("invalid_push_token")
    return token.strip()


def _remove_expired_pending_tests(now: float) -> None:
    expired = [
        test_id
        for test_id, pending in _pending_provider_tests.items()
        if now - pending.created_monotonic > PROVIDER_TEST_DRY_RUN_TTL_SECONDS
    ]
    for test_id in expired:
        _pending_provider_tests.pop(test_id, None)


def create_provider_test_dry_run(
    selection: RecipientSelection,
    *,
    confirmed_recipient_user_id: int,
    physical_device_confirmed: bool,
) -> str:
    token = _validate_selection(
        selection,
        confirmed_recipient_user_id=confirmed_recipient_user_id,
        physical_device_confirmed=physical_device_confirmed,
    )
    test_id = generate_provider_test_id()
    now = time.monotonic()
    pending = PendingProviderTest(
        test_id=test_id,
        recipient_user_id=confirmed_recipient_user_id,
        token_digest=_token_digest(token),
        created_monotonic=now,
    )
    with _pending_provider_tests_lock:
        _remove_expired_pending_tests(now)
        # Only the most recently reviewed dry run may remain eligible for the
        # single-send endpoint in this process.
        _pending_provider_tests.clear()
        _pending_provider_tests[test_id] = pending
    return test_id


def consume_provider_test_dry_run(
    selection: RecipientSelection,
    *,
    test_id: str,
    confirmed_recipient_user_id: int,
    physical_device_confirmed: bool,
    approve_single_send: bool,
) -> str:
    if not approve_single_send:
        raise ProviderTestGateError("explicit_one_send_approval_required")
    token = _validate_selection(
        selection,
        confirmed_recipient_user_id=confirmed_recipient_user_id,
        physical_device_confirmed=physical_device_confirmed,
    )
    now = time.monotonic()
    with _pending_provider_tests_lock:
        _remove_expired_pending_tests(now)
        pending = _pending_provider_tests.pop(test_id, None)

    if pending is None:
        raise ProviderTestGateError("valid_dry_run_required")
    if pending.recipient_user_id != confirmed_recipient_user_id:
        raise ProviderTestGateError("dry_run_recipient_mismatch")
    if pending.token_digest != _token_digest(token):
        raise ProviderTestGateError("recipient_token_changed_after_dry_run")
    return token


def _clear_provider_test_dry_runs_for_testing() -> None:
    with _pending_provider_tests_lock:
        _pending_provider_tests.clear()
