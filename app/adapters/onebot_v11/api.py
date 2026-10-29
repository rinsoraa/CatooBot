"""High-level OneBot API surface used by Bot / plugins.

Every method funnels through :meth:`OneBotCaller.call_api` so echo matching,
timeouts and disconnect cleanup stay in one place. Messages accept
:class:`Message`, plain ``str`` or a single :class:`Segment` for convenience.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.message.message import Message
from app.message.segment import Segment


class OneBotCaller(Protocol):
    """Anything that can perform a raw OneBot API call (the server/connection)."""

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any: ...


def _normalize_node(node: dict[str, Any]) -> dict[str, Any]:
    """One merged-forward node in OneBot 11 array form."""
    content = node.get("content", "")
    if isinstance(content, str):
        content = [{"type": "text", "data": {"text": content}}]
    elif not isinstance(content, list):
        content = [{"type": "text", "data": {"text": str(content)}}]
    return {
        "type": "node",
        "data": {
            "name": str(node.get("name", "")),
            "uin": str(node.get("uin", "")),
            "content": content,
        },
    }


def _serialize_message(message: Message | str | Segment) -> list[dict[str, Any]]:
    """Normalize user-provided message content into OneBot array format."""
    if isinstance(message, Message):
        return message.to_onebot()
    if isinstance(message, Segment):
        return [message.to_onebot()]
    if isinstance(message, str):
        return Message.text_message(message).to_onebot()
    raise TypeError(f"Unsupported message type: {type(message).__name__}")


class BotApi:
    """Typed convenience wrapper over :class:`OneBotCaller`."""

    def __init__(self, caller: OneBotCaller) -> None:
        self._caller = caller

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        return await self._caller.call_api(action, params, timeout)

    # ------------------------------------------------------------- messaging

    async def send_private_msg(self, user_id: int, message: Message | str | Segment) -> int:
        result = await self._caller.call_api(
            "send_private_msg",
            {"user_id": user_id, "message": _serialize_message(message)},
        )
        return int(result.get("message_id", -1)) if isinstance(result, dict) else -1

    async def send_group_msg(self, group_id: int, message: Message | str | Segment) -> int:
        result = await self._caller.call_api(
            "send_group_msg",
            {"group_id": group_id, "message": _serialize_message(message)},
        )
        return int(result.get("message_id", -1)) if isinstance(result, dict) else -1

    async def delete_msg(self, message_id: int) -> None:
        await self._caller.call_api("delete_msg", {"message_id": message_id})

    async def get_msg(self, message_id: int) -> dict[str, Any]:
        """Fetch one message (recall context, quote handling, moderation)."""
        result = await self._caller.call_api("get_msg", {"message_id": message_id})
        return dict(result) if isinstance(result, dict) else {}

    async def send_forward_msg(
        self,
        nodes: list[dict[str, Any]],
        *,
        group_id: int | None = None,
        user_id: int | None = None,
    ) -> int:
        """Send a merged-forward message (OneBot ``send_forward_msg``).

        Each node is ``{"name": str, "uin": str, "content": str | Message}``;
        the target follows the same rule as :meth:`send_msg`.
        """
        payload: dict[str, Any] = {"messages": [_normalize_node(node) for node in nodes]}
        if group_id is not None:
            payload["group_id"] = group_id
        elif user_id is not None:
            payload["user_id"] = user_id
        else:
            raise ValueError("send_forward_msg requires group_id or user_id")
        result = await self._caller.call_api("send_forward_msg", payload)
        return int(result.get("message_id", -1)) if isinstance(result, dict) else -1

    async def send_msg(
        self,
        message: Message | str | Segment,
        *,
        user_id: int | None = None,
        group_id: int | None = None,
    ) -> int:
        """Route by target: group_id wins if both are given."""
        if group_id is not None:
            return await self.send_group_msg(group_id, message)
        if user_id is not None:
            return await self.send_private_msg(user_id, message)
        raise ValueError("send_msg requires user_id or group_id")

    # ------------------------------------------------------------------ info

    async def get_login_info(self) -> dict[str, Any]:
        result = await self._caller.call_api("get_login_info")
        return dict(result) if isinstance(result, dict) else {}

    async def get_friend_list(self) -> list[dict[str, Any]]:
        result = await self._caller.call_api("get_friend_list")
        return list(result) if isinstance(result, list) else []

    async def get_group_info(self, group_id: int, no_cache: bool = False) -> dict[str, Any]:
        result = await self._caller.call_api(
            "get_group_info", {"group_id": group_id, "no_cache": no_cache}
        )
        return dict(result) if isinstance(result, dict) else {}

    async def get_group_list(self) -> list[dict[str, Any]]:
        result = await self._caller.call_api("get_group_list")
        return list(result) if isinstance(result, list) else []

    async def get_group_member_info(
        self, group_id: int, user_id: int, no_cache: bool = False
    ) -> dict[str, Any]:
        result = await self._caller.call_api(
            "get_group_member_info",
            {"group_id": group_id, "user_id": user_id, "no_cache": no_cache},
        )
        return dict(result) if isinstance(result, dict) else {}

    async def get_user_info(self, user_id: int) -> dict[str, Any]:
        """Nicely portable subset (NapCat supports get_stranger_info too)."""
        result = await self._caller.call_api(
            "get_stranger_info", {"user_id": user_id, "no_cache": False}
        )
        return dict(result) if isinstance(result, dict) else {}
