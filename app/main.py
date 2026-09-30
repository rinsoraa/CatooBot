"""CatooBot application entry point: wires config, logging, adapter, core."""

from __future__ import annotations

import asyncio
import sys

from app.adapters.onebot_v11.server import OneBotV11Server
from app.config.settings import load_config
from app.core.bot import Bot
from app.utils import console
from app.utils.logger import get_logger, setup_logging
from app.utils.narrator import narrate

VERSION = "v1.2"
TITLE = "C a t o o B o t"
SUBTITLE = "Multimodal + Sticker Runtime"
DEVELOPER = "Rinsora"


def print_banner() -> None:
    """Startup artwork, including the developer signature."""
    for line in console.banner(
        TITLE, SUBTITLE, version=VERSION, author=DEVELOPER, width=54, accent="bright_cyan"
    ):
        print(line)
    print()


async def run() -> int:
    config = load_config()
    setup_logging(
        config.logging.level,
        config.logging.log_dir,
        color=config.logging.color,
        narrate=config.logging.narrate,
    )
    log = get_logger("CatooBot")
    story = narrate()

    print_banner()
    story.note("boot", console.paint(f"启动 {VERSION} · developer {DEVELOPER}", "bright_white"))
    story.boot_step(
        "配置已加载",
        detail=(
            f"debug={config.bot.debug}, log={config.logging.level}, "
            f"ai={'on' if config.ai.enabled else 'off'}, "
            f"tools={'on' if config.tools.enabled else 'off'}, "
            f"agent={'on' if config.agent.enabled else 'off'}, "
            f"world={'on' if config.world.enabled else 'off'}"
        ),
    )
    log.info("Starting CatooBot %s (developer: %s)", VERSION, DEVELOPER)

    adapter = OneBotV11Server(config.onebot)
    bot = Bot(config, adapter)
    adapter.set_event_handler(bot.handle_event)

    try:
        await bot.start()
    except OSError as exc:
        story.boot_step("OneBot 端口绑定失败", detail=str(exc), ok=False)
        log.error("Failed to bind WebSocket server on %s: %s", config.onebot.url, exc)
        return 1

    await bot.print_ready_panel()
    await bot.lifecycle.wait_for_signal()
    await bot.shutdown()
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        # wait_for_signal usually handles this; this guards early exits.
        print()
        narrate().quiet("收到中断信号，正在收尾…")
        return 0


if __name__ == "__main__":
    sys.exit(main())
