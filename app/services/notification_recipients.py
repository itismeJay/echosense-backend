import logging
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.notifications.tokens import (
    is_structurally_valid_push_token,
    normalize_push_token,
    push_token_provider,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecipientSelection:
    controlled_test_mode: bool
    tokens: tuple[str, ...]
    configured_recipient_resolved: bool
    recipient_identifier_masked: str | None
    has_push_token: bool
    failure_reason: str | None = None
    configured_user_reference_present: bool = False
    selected_recipient_count: int = 0
    recipient_user_id: int | None = None
    recipient_email: str | None = None
    recipient_role: str | None = None
    account_active_status: str = "not_recorded"
    token_structurally_valid: bool = False
    token_provider: str = "not_recorded"
    token_duplicate_count: int = 0
    selected_recipient_source: str = "none"

    @property
    def eligible_recipient_count(self) -> int:
        return len(self.tokens)


def _masked_user_identifier(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return "user_id:configured"


def _account_active_status(user: User) -> str:
    is_active = getattr(user, "is_active", None)
    if is_active is True:
        return "active"
    if is_active is False:
        return "inactive"
    return "not_recorded"


def evaluate_controlled_recipient(
    users: Sequence[User],
    configured_user_id: int | None,
    *,
    duplicate_token_count: int = 0,
) -> RecipientSelection:
    masked_identifier = _masked_user_identifier(configured_user_id)
    configured_reference_present = configured_user_id is not None
    if not isinstance(configured_user_id, int) or isinstance(configured_user_id, bool):
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="invalid_user_id",
            configured_user_reference_present=configured_reference_present,
        )
    if configured_user_id <= 0:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="invalid_user_id",
            configured_user_reference_present=True,
        )
    if not users:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="user_not_found",
            configured_user_reference_present=True,
        )
    if len(users) != 1:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="multiple_users",
            configured_user_reference_present=True,
            selected_recipient_count=len(users),
            selected_recipient_source="controlled_user",
        )

    user = users[0]
    token = normalize_push_token(user.push_token)
    active_status = _account_active_status(user)
    common = {
        "configured_user_reference_present": True,
        "selected_recipient_count": 1,
        "recipient_user_id": user.id,
        "recipient_email": user.email,
        "recipient_role": user.role,
        "account_active_status": active_status,
        "token_duplicate_count": duplicate_token_count,
        "selected_recipient_source": "controlled_user",
    }
    if active_status == "inactive":
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=True,
            recipient_identifier_masked=masked_identifier,
            has_push_token=token is not None,
            failure_reason="inactive_user",
            token_structurally_valid=is_structurally_valid_push_token(token),
            token_provider=push_token_provider(token),
            **common,
        )
    if token is None:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=True,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="push_token_unavailable",
            **common,
        )
    if not is_structurally_valid_push_token(token):
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=True,
            recipient_identifier_masked=masked_identifier,
            has_push_token=True,
            failure_reason="invalid_push_token",
            token_provider=push_token_provider(token),
            **common,
        )
    if duplicate_token_count:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=True,
            recipient_identifier_masked=masked_identifier,
            has_push_token=True,
            failure_reason="duplicate_push_token",
            token_structurally_valid=True,
            token_provider="expo",
            **common,
        )

    return RecipientSelection(
        controlled_test_mode=True,
        tokens=(token,),
        configured_recipient_resolved=True,
        recipient_identifier_masked=masked_identifier,
        has_push_token=True,
        token_structurally_valid=True,
        token_provider="expo",
        **common,
    )


def _log_selection(selection: RecipientSelection) -> None:
    if selection.failure_reason is not None:
        logger.error(
            "[NOTIFICATION] controlled_test_recipient_unavailable reason=%s",
            selection.failure_reason,
        )
        return
    mode = "controlled_test" if selection.controlled_test_mode else "normal"
    logger.info(
        "[NOTIFICATION] mode=%s recipients=%d",
        mode,
        selection.eligible_recipient_count,
    )


async def resolve_notification_recipients(
    db: AsyncSession,
    *,
    school_id: UUID | None = None,
    emit_log: bool = True,
) -> RecipientSelection:
    if not settings.ECHOSENSE_CONTROLLED_TEST_MODE:
        query = select(User).where(User.push_token.isnot(None))
        if school_id is not None:
            query = query.where(User.school_id == school_id)
        result = await db.execute(query)
        users = list(result.scalars().all())
        tokens: list[str] = []
        seen_tokens: set[str] = set()
        for user in users:
            token = normalize_push_token(user.push_token)
            if not is_structurally_valid_push_token(token) or token in seen_tokens:
                continue
            seen_tokens.add(token)
            tokens.append(token)
        selection = RecipientSelection(
            controlled_test_mode=False,
            tokens=tuple(tokens),
            configured_recipient_resolved=False,
            recipient_identifier_masked=None,
            has_push_token=bool(tokens),
            configured_user_reference_present=(
                settings.ECHOSENSE_CONTROLLED_TEST_USER_ID is not None
            ),
            selected_recipient_count=len(users),
            selected_recipient_source="broadcast",
        )
    else:
        configured_user_id = settings.ECHOSENSE_CONTROLLED_TEST_USER_ID
        if not isinstance(configured_user_id, int) or isinstance(configured_user_id, bool):
            selection = evaluate_controlled_recipient([], configured_user_id)
        else:
            query = select(User).where(User.id == configured_user_id)
            if school_id is not None:
                query = query.where(User.school_id == school_id)
            result = await db.execute(query)
            users = list(result.scalars().all())
            duplicate_token_count = 0
            if len(users) == 1:
                normalized_token = normalize_push_token(users[0].push_token)
                if normalized_token is not None:
                    duplicate_query = select(func.count(User.id)).where(
                        User.id != users[0].id,
                        func.btrim(User.push_token) == normalized_token,
                    )
                    if school_id is not None:
                        duplicate_query = duplicate_query.where(User.school_id == school_id)
                    duplicate_result = await db.execute(duplicate_query)
                    duplicate_token_count = int(duplicate_result.scalar_one())
            selection = evaluate_controlled_recipient(
                users,
                configured_user_id,
                duplicate_token_count=duplicate_token_count,
            )

    if emit_log:
        _log_selection(selection)
    return selection
