"""OneBot 11 adapter: reverse-WebSocket server + typed API client."""

from app.adapters.onebot_v11.api import BotApi
from app.adapters.onebot_v11.client import OneBotV11Connection
from app.adapters.onebot_v11.server import OneBotV11Server

__all__ = ["BotApi", "OneBotV11Connection", "OneBotV11Server"]
