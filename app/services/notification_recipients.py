import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecipientSelection:
    controlled_test_mode: bool
    tokens: tuple[str, ...]
    configured_recipient_resolved: bool
    recipient_identifier_masked: str | None
    has_push_token: bool
    failure_reason: str | None = None

    @property
    def eligible_recipient_count(self) -> int:
        return len(self.tokens)


def _masked_user_identifier(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return "user_id:configured"


def evaluate_controlled_recipient(
    users: Sequence[User],
    configured_user_id: int | None,
) -> RecipientSelection:
    masked_identifier = _masked_user_identifier(configured_user_id)
    if not isinstance(configured_user_id, int) or isinstance(configured_user_id, bool):
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="invalid_user_id",
        )
    if configured_user_id <= 0:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="invalid_user_id",
        )
    if not users:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="user_not_found",
        )
    if len(users) != 1:
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=False,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="multiple_users",
        )

    user = users[0]
    token = user.push_token
    if not isinstance(token, str) or not token.strip():
        return RecipientSelection(
            controlled_test_mode=True,
            tokens=(),
            configured_recipient_resolved=True,
            recipient_identifier_masked=masked_identifier,
            has_push_token=False,
            failure_reason="push_token_unavailable",
        )

    return RecipientSelection(
        controlled_test_mode=True,
        tokens=(token,),
        configured_recipient_resolved=True,
        recipient_identifier_masked=masked_identifier,
        has_push_token=True,
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
    emit_log: bool = True,
) -> RecipientSelection:
    if not settings.ECHOSENSE_CONTROLLED_TEST_MODE:
        result = await db.execute(select(User).where(User.push_token.isnot(None)))
        tokens = tuple(user.push_token for user in result.scalars().all())
        selection = RecipientSelection(
            controlled_test_mode=False,
            tokens=tokens,
            configured_recipient_resolved=False,
            recipient_identifier_masked=None,
            has_push_token=bool(tokens),
        )
    else:
        configured_user_id = settings.ECHOSENSE_CONTROLLED_TEST_USER_ID
        if not isinstance(configured_user_id, int) or isinstance(configured_user_id, bool):
            selection = evaluate_controlled_recipient([], configured_user_id)
        else:
            result = await db.execute(select(User).where(User.id == configured_user_id))
            selection = evaluate_controlled_recipient(
                list(result.scalars().all()),
                configured_user_id,
            )

    if emit_log:
        _log_selection(selection)
    return selection
