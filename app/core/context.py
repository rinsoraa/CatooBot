"""Command execution context handed to every command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.message.event import MessageEvent
from app.message.message import Message
from app.message.segment import Segment

if TYPE_CHECKING:
    from app.core.bot import Bot


class Context:
    """Everything a command (or event handler) needs to act and reply."""

    def __init__(self, bot: Bot, event: MessageEvent, args: list[str] | None = None) -> None:
        self.bot = bot
        self.event = event
        self.args = args or []

    # ------------------------------------------------------------ identity

    @property
    def user_id(self) -> int:
        return self.event.user_id

    @property
    def group_id(self) -> int | None:
        return self.event.group_id

    @property
    def sender_name(self) -> str:
        return self.event.sender.display_name

    @property
    def is_private(self) -> bool:
        return self.event.is_private

    @property
    def is_group(self) -> bool:
        return self.event.is_group

    # ----------------------------------------------------------- permissions

    def role(self) -> str:
        return self.bot.permissions.role(self.user_id, group_role=self.event.sender.role)

    def is_superuser(self) -> bool:
        return self.bot.permissions.is_superuser(self.user_id)

    def is_admin(self) -> bool:
        return self.bot.permissions.is_admin(self.user_id, group_role=self.event.sender.role)

    def has_permission(self, required: str) -> bool:
        return self.bot.permissions.has(
            self.user_id, required, group_role=self.event.sender.role
        )

    # ------------------------------------------------------------- replies

    async def reply(self, message: Message | str | Segment) -> int:
        """Reply in the chat the event came from (private or group)."""
        if self.event.is_group and self.event.group_id is not None:
            return await self.bot.api.send_group_msg(self.event.group_id, message)
        return await self.bot.api.send_private_msg(self.event.user_id, message)

    async def send_private(self, user_id: int, message: Message | str | Segment) -> int:
        return await self.bot.api.send_private_msg(user_id, message)

    async def send_group(self, group_id: int, message: Message | str | Segment) -> int:
        return await self.bot.api.send_group_msg(group_id, message)

    # ---------------------------------------------------------- API access

    async def call_api(
        self, action: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        return await self.bot.call_api(action, params, timeout)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        scope = f"group={self.event.group_id}" if self.event.is_group else "private"
        return f"<Context user={self.user_id} {scope} args={self.args}>"
