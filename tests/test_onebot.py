"""OneBot adapter integration tests against a real WebSocket server.

A test client plays the NapCat role: connects, answers get_login_info probes,
injects events and replies to API requests — exercising connect / auth /
event flow / echo matching / timeout / disconnect cleanup / reconnection.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets

from app.adapters.onebot_v11.server import OneBotV11Server
from app.config.settings import OneBotConfig
from app.message.event import GroupMessageEvent

BASE_PORT = 18100


def make_config(port: int, token: str = "", api_timeout: float = 1.5) -> OneBotConfig:
    return OneBotConfig(
        host="127.0.0.1",
        port=port,
        path="/onebot/v11/ws",
        access_token=token,
        api_timeout=api_timeout,
    )


class NapCatMock:
    """Minimal OneBot client for driving the server under test."""

    def __init__(self, ws) -> None:  # type: ignore[no-untyped-def]
        self.ws = ws

    async def recv(self, timeout: float = 3.0) -> dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        return json.loads(raw)

    async def recv_none(self, timeout: float = 0.4) -> bool:
        """True if no message arrives within ``timeout``."""
        try:
            await self.recv(timeout)
            return False
        except TimeoutError:
            return True

    async def send_event(self, payload: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(payload))

    async def send_result(self, request: dict[str, Any], data: Any, retcode: int = 0) -> None:
        await self.ws.send(
            json.dumps(
                {
                    "status": "ok" if retcode == 0 else "failed",
                    "retcode": retcode,
                    "data": data,
                    "echo": request["echo"],
                }
            )
        )

    async def answer_login_probe(self) -> dict[str, Any]:
        request = await self.recv()
        assert request["action"] == "get_login_info"
        await self.send_result(request, {"user_id": 10001, "nickname": "CatooBot"})
        return request


def group_payload(text: str = "/ping", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "post_type": "message",
        "self_id": 10001,
        "time": 1700000000,
        "message_type": "group",
        "message_id": 9,
        "user_id": 3,
        "group_id": 4,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"user_id": 3, "nickname": "A"},
    }
    payload.update(overrides)
    return payload


async def _connect(port: int = BASE_PORT, token: str = "") -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await websockets.connect(
        f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers, open_timeout=3
    )


class TestConnectionAndAuth:
    async def test_wrong_path_rejected(self) -> None:
        cfg = make_config(18101)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            with pytest.raises(websockets.InvalidStatus) as excinfo:
                await websockets.connect("ws://127.0.0.1:18101/other/path")
            assert excinfo.value.response.status_code == 404
        finally:
            await srv.stop()

    async def test_missing_token_rejected(self) -> None:
        cfg = make_config(18102, token="secret")
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            with pytest.raises(websockets.InvalidStatus) as excinfo:
                await websockets.connect("ws://127.0.0.1:18102/onebot/v11/ws")
            assert excinfo.value.response.status_code == 401
        finally:
            await srv.stop()

    async def test_wrong_token_rejected(self) -> None:
        cfg = make_config(18103, token="secret")
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            with pytest.raises(websockets.InvalidStatus) as excinfo:
                await _connect(18103, "wrong-token")
            assert excinfo.value.response.status_code == 401
        finally:
            await srv.stop()

    async def test_bearer_token_accepted(self) -> None:
        cfg = make_config(18104, token="secret")
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            async with await _connect(18104, "secret") as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                await asyncio.sleep(0.2)  # let the server process the login result
                assert srv.connected
                assert srv.self_id == 10001
        finally:
            await srv.stop()

    async def test_query_param_token_accepted(self) -> None:
        cfg = make_config(18105, token="secret")
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            async with websockets.connect(
                "ws://127.0.0.1:18105/onebot/v11/ws?access_token=secret"
            ) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                assert srv.connected
        finally:
            await srv.stop()


class TestEventFlow:
    async def test_event_received_and_typed(self) -> None:
        cfg = make_config(18106)
        events: list[Any] = []

        async def on_event(event) -> None:  # type: ignore[no-untyped-def]
            events.append(event)

        srv = OneBotV11Server(cfg, event_handler=on_event)
        await srv.start()
        try:
            async with await _connect(18106) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                await mock.send_event(group_payload("/ping"))
                await asyncio.sleep(0.2)
                assert len(events) == 1
                assert isinstance(events[0], GroupMessageEvent)
                assert events[0].message.text == "/ping"
        finally:
            await srv.stop()

    async def test_invalid_json_does_not_kill_connection(self) -> None:
        cfg = make_config(18107)
        events: list[Any] = []

        async def on_event(event) -> None:  # type: ignore[no-untyped-def]
            events.append(event)

        srv = OneBotV11Server(cfg, event_handler=on_event)
        await srv.start()
        try:
            async with await _connect(18107) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                await ws.send("this is not json")
                await ws.send(json.dumps({"post_type": "message", "bogus": True}))  # invalid event
                await mock.send_event(group_payload("/ping"))
                await asyncio.sleep(0.2)
                assert len(events) == 1  # only the valid one arrived
                assert srv.connected
        finally:
            await srv.stop()

    async def test_event_handler_reply_does_not_deadlock(self) -> None:
        """Regression: a handler that awaits an API call must not block the
        recv loop (previously deadlocked until the API timeout)."""
        cfg = make_config(18116, api_timeout=5.0)
        reply_requests: list[dict[str, Any]] = []

        async def on_event(event) -> None:  # type: ignore[no-untyped-def]
            # Simulate a command handler replying to the message.
            await srv.call_api("send_group_msg", {"group_id": 4, "message": []})

        srv = OneBotV11Server(cfg, event_handler=on_event)
        await srv.start()
        try:
            async with await _connect(18116) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                await mock.send_event(group_payload("/ping"))
                # The reply request must arrive while the connection is alive.
                request = await mock.recv(3.0)
                reply_requests.append(request)
                assert request["action"] == "send_group_msg"
                await mock.send_result(request, {"message_id": 88})
                assert srv.connected
        finally:
            await srv.stop()

    async def test_meta_events_ignored_gracefully(self) -> None:
        cfg = make_config(18108)
        events: list[Any] = []

        async def on_event(event) -> None:  # type: ignore[no-untyped-def]
            events.append(event)

        srv = OneBotV11Server(cfg, event_handler=on_event)
        await srv.start()
        try:
            async with await _connect(18108) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                await mock.send_event(
                    {
                        "post_type": "meta_event",
                        "self_id": 10001,
                        "meta_event_type": "heartbeat",
                        "interval": 5000,
                    }
                )
                await asyncio.sleep(0.1)
                assert len(events) == 1
                assert srv.connected
        finally:
            await srv.stop()


class TestApiCalls:
    async def test_call_api_roundtrip(self) -> None:
        cfg = make_config(18109)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            async with await _connect(18109) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()

                async def call() -> Any:
                    return await srv.call_api("send_group_msg", {"group_id": 4, "message": []})

                task = asyncio.create_task(call())
                request = await mock.recv()
                assert request["action"] == "send_group_msg"
                assert "echo" in request
                await mock.send_result(request, {"message_id": 77})
                assert await task == {"message_id": 77}
        finally:
            await srv.stop()

    async def test_api_error_raises(self) -> None:
        from app.core.exceptions import OneBotApiError

        cfg = make_config(18110)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            async with await _connect(18110) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()

                async def call() -> Any:
                    return await srv.call_api("send_group_msg", {"group_id": -1})

                task = asyncio.create_task(call())
                request = await mock.recv()
                await mock.send_result(request, None, retcode=1200)
                with pytest.raises(OneBotApiError) as excinfo:
                    await task
                assert excinfo.value.retcode == 1200
        finally:
            await srv.stop()

    async def test_api_timeout(self) -> None:
        from app.core.exceptions import ApiTimeoutError

        cfg = make_config(18111, api_timeout=0.3)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            async with await _connect(18111) as ws:
                mock = NapCatMock(ws)
                await mock.answer_login_probe()
                with pytest.raises(ApiTimeoutError):
                    await srv.call_api("get_friend_list")
                # server still responsive afterwards
                assert srv.connected
        finally:
            await srv.stop()

    async def test_call_when_disconnected_raises(self) -> None:
        from app.core.exceptions import AdapterNotConnected

        cfg = make_config(18112)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            with pytest.raises(AdapterNotConnected):
                await srv.call_api("get_login_info")
        finally:
            await srv.stop()


class TestDisconnectAndReconnect:
    async def test_disconnect_fails_pending_and_allows_reconnect(self) -> None:
        from app.core.exceptions import AdapterDisconnected

        cfg = make_config(18113)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            ws = await _connect(18113)
            mock = NapCatMock(ws)
            await mock.answer_login_probe()

            pending = asyncio.create_task(srv.call_api("get_friend_list"))
            request = await mock.recv()

            # drop the connection without answering
            await ws.close()
            with pytest.raises(AdapterDisconnected):
                await pending
            del request

            await asyncio.sleep(0.2)
            assert not srv.connected

            # NapCat restarts and reconnects — no server restart needed
            async with await _connect(18113) as ws2:
                mock2 = NapCatMock(ws2)
                await mock2.answer_login_probe()
                assert srv.connected

                async def call() -> Any:
                    return await srv.call_api("get_group_info", {"group_id": 1})

                task = asyncio.create_task(call())
                req = await mock2.recv()
                assert req["action"] == "get_group_info"
                await mock2.send_result(req, {"group_id": 1, "group_name": "test"})
                assert (await task)["group_name"] == "test"
        finally:
            await srv.stop()

    async def test_new_connection_supersedes_old(self) -> None:
        cfg = make_config(18114)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            ws1 = await _connect(18114)
            mock1 = NapCatMock(ws1)
            await mock1.answer_login_probe()
            first_conn = srv.connection

            async with await _connect(18114) as ws2:
                mock2 = NapCatMock(ws2)
                await mock2.answer_login_probe()
                assert srv.connection is not first_conn
                assert first_conn.closed  # old connection was closed
                # old socket should receive close frame shortly
                try:
                    await asyncio.wait_for(ws1.recv(), 2.0)
                except websockets.ConnectionClosed:
                    pass
                else:
                    pytest.fail("old connection was not closed")
            await asyncio.sleep(0.1)
        finally:
            await srv.stop()

    async def test_server_survives_client_churn(self) -> None:
        cfg = make_config(18115)
        srv = OneBotV11Server(cfg)
        await srv.start()
        try:
            for _ in range(3):
                async with await _connect(18115) as ws:
                    mock = NapCatMock(ws)
                    await mock.answer_login_probe()
                    await mock.send_event(group_payload("/ping"))
                    await asyncio.sleep(0.1)
            await asyncio.sleep(0.2)  # let the server process the last disconnect
            assert not srv.connected
        finally:
            await srv.stop()
