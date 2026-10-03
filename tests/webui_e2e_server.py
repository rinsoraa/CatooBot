#!/usr/bin/env python
"""Boot a real CatooBot + WebServer for the browser E2E suite (W6 §27/§28).

Run by Playwright's ``webServer`` block (see ``webui/playwright.config.ts``):

    .venv/Scripts/python.exe tests/webui_e2e_server.py   # Windows
    python tests/webui_e2e_server.py                     # Linux/macOS

Everything runs in a throwaway temp directory (DB, logs, stickers, WebUI
overrides), so the suite never touches the operator's data or config. The
character bible comes from the repo fixture (CI has no personal bible), the
OneBot adapter stays off (no port 8080), no AI keys are read, and the WebUI
listens on 127.0.0.1 with the E2E credentials below.

Boot uses the public Bot API only — the same sequence the E2E walk depends on:
Bot(config, adapter) → database.connect() → character.start() → tools/sandbox
start → WebServer(config.web, bot).start(). It deliberately skips ``bot.start()``
(no OneBot bind, no plugin load, no background schedulers, no character reset),
so the process stays small and deterministic. The WebUI's config admin service
is pointed at a temp ``overrides.yaml`` (its documented test seam) so the
settings mutation never writes into the repository.

Env:
    CATOOBOT_E2E_PORT      WebUI port (default 8611)
    CATOOBOT_E2E_PASSWORD  admin password for this run (default e2e-test-password)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.onebot_v11.server import OneBotV11Server  # noqa: E402
from app.config.settings import (  # noqa: E402
    AgentConfig,
    AIConfig,
    AIModelConfig,
    AIProviderConfig,
    AppConfig,
    BotConfig,
    DatabaseConfig,
    LoggingConfig,
    MediaConfig,
    MemoryConfig,
    OneBotConfig,
    SandboxConfig,
    ToolsConfig,
    WebConfig,
)
from app.core.bot import Bot  # noqa: E402
from app.utils.logger import setup_logging  # noqa: E402

DEFAULT_PORT = 8611
DEFAULT_PASSWORD = "e2e-test-password"  # a test value, never a real secret
BIBLE_FIXTURE = "tests/fixtures/character_bible.md"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def build_config(root: Path, port: int, password: str) -> AppConfig:
    """The E2E configuration: temp dirs, no OneBot port, no AI keys, WebUI on."""
    return AppConfig(
        bot=BotConfig(name="CatooBot E2E", debug=False),
        onebot=OneBotConfig(enabled=False),
        logging=LoggingConfig(
            level="INFO",
            log_dir=str(root / "logs"),
            color=False,
            narrate=False,
            narrate_world_ticks=False,
            narrate_thinking=False,
            watchdog_enabled=False,
            watch_config_enabled=False,
        ),
        database=DatabaseConfig(url=f"sqlite:///{(root / 'catoobot-e2e.db').as_posix()}"),
        # No keys in E2E: AI is off, but one provider/model entry exists so the
        # AI pages render a real row (marked 缺少 API Key / 未生效) instead of
        # an empty registry. The provider name must stay split_key-safe
        # (`[A-Za-z0-9_]+`): the config registry cannot parse hyphenated keys.
        ai=AIConfig(
            enabled=False,
            providers={
                "e2eoffline": AIProviderConfig(
                    type="openai_compatible",
                    base_url="http://127.0.0.1:9/v1",
                    api_key_env="CATOOBOT_E2E_FAKE_KEY",
                )
            },
            models=[
                AIModelConfig(
                    name="e2e-model", provider="e2eoffline", model="e2e-model", enabled=True
                )
            ],
        ),
        memory=MemoryConfig(enabled=True),
        media=MediaConfig(
            enabled=True,
            sticker_dir=str(root / "stickers"),
            media_dir=str(root / "media"),
            indexer_enabled=False,
        ),
        web=WebConfig(
            enabled=True,
            host="127.0.0.1",
            port=port,
            username="admin",
            password=password,
        ),
        # The life sandbox is on with the repo fixture bible; the runtime
        # scheduler stays off (we only need a booted, seeded world to read).
        sandbox=SandboxConfig(
            enabled=True,
            bible_path=BIBLE_FIXTURE,
            allow_ai_decisions=False,
            reset_on_bible_change=True,
        ),
        tools=ToolsConfig(enabled=True),
        agent=AgentConfig(enabled=True),
    )


async def _seed_fixture_data(bot: Bot) -> None:
    """Deterministic platform rows the walk asserts against.

    Only the temp DB is written: two QQ users with profiles, one group, three
    memories and two AI-usage rows. The suite then proves the WebUI read paths
    surface these exact rows (and their counts) — no fake UI data anywhere.
    """
    now = int(time.time())
    db = bot.database
    await db.execute(
        "INSERT OR REPLACE INTO users (user_id, nickname, last_seen, updated_at)"
        " VALUES (?, ?, ?, ?)",
        ("10001", "阿澈", now, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO users (user_id, nickname, last_seen, updated_at)"
        " VALUES (?, ?, ?, ?)",
        ("10002", "凛", now - 60, now - 60),
    )
    await db.execute(
        "INSERT INTO user_profiles"
        " (user_id, nickname_override, notes, tags, interaction_count, first_seen, last_seen,"
        "  initiative_enabled)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 1)"
        " ON CONFLICT(user_id) DO UPDATE SET nickname_override=excluded.nickname_override,"
        " notes=excluded.notes, tags=excluded.tags, interaction_count=excluded.interaction_count",
        ("10001", "澈澈", "E2E 种子资料", '["core"]', 7, now - 86400, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO groups (group_id, name, last_seen, updated_at) VALUES (?, ?, ?, ?)",
        ("20001", "猫图群", now - 30, now - 30),
    )
    await db.execute(
        "INSERT INTO group_profiles"
        " (group_id, notes, tags, interaction_count, first_seen, last_seen, participation_enabled)"
        " VALUES (?, ?, ?, ?, ?, ?, 1)"
        " ON CONFLICT(group_id) DO UPDATE SET notes=excluded.notes,"
        " interaction_count=excluded.interaction_count, participation_enabled=1",
        ("20001", "E2E 种子群资料", '["cat"]', 12, now - 172800, now - 30),
    )
    memories = (
        ("user:10001", "10001", None, "preference", "阿澈最喜欢猫娘表情包", 0.8, "semantic"),
        ("user:10001", "10001", None, "fact", "阿澈住在杭州", 0.6, "semantic"),
        ("user:10001", "10001", None, "event", "阿澈上周一起吃火锅", 0.5, "episodic"),
    )
    for index, (scope, user_id, group_id, category, content, importance, layer) in enumerate(
        memories
    ):
        await db.execute(
            "INSERT INTO memories"
            " (scope_key, user_id, group_id, category, content, content_hash, importance,"
            "  confidence, use_count, created_at, updated_at, layer, source, status, search_text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0.7, 0, ?, ?, ?, 'conversation', 'active', ?)",
            (
                scope,
                user_id,
                group_id,
                category,
                content,
                f"e2e-hash-{index}",
                importance,
                now - index * 3600,
                now - index * 3600,
                layer,
                content,
            ),
        )
    await db.execute(
        "INSERT INTO ai_usage (ts, provider, model, purpose, prompt_tokens, completion_tokens,"
        " total_tokens, latency_ms, attempt, ok, error_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, '')",
        (now - 120, "e2eoffline", "e2e-model", "chat", 120, 40, 160, 880.0),
    )
    await db.execute(
        "INSERT INTO ai_usage (ts, provider, model, purpose, prompt_tokens, completion_tokens,"
        " total_tokens, latency_ms, attempt, ok, error_type)"
        " VALUES (?, ?, ?, ?, 0, 0, 0, ?, 1, 0, ?)",
        (now - 60, "e2eoffline", "e2e-model", "chat", 1500.0, "provider_error"),
    )
    sandbox = bot.sandbox
    if sandbox is not None:
        # Seeded before sandbox.start() so CommitmentManager.restore() picks it up.
        await db.execute(
            "INSERT OR REPLACE INTO sandbox_commitments"
            " (commitment_id, character_id, person_id, kind, status, strength, description,"
            "  revision, priority, due_at, created_at, updated_at, metadata)"
            " VALUES ('e2e-commitment-1', ?, '', 'shared_activity', 'pending', 'explicit',"
            "  'E2E 约定：一起看猫图', 0, 0.5, ?, ?, ?, '{}')",
            (sandbox.character_id, float(now + 3600), float(now - 600), float(now - 600)),
        )


async def _sync_persona_from_bible(bot: Bot) -> None:
    """Same persona sync the production sandbox boot performs (v2.0 §2)."""
    sandbox = bot.sandbox
    if sandbox is None or not bot.config.sandbox.sync_persona_from_bible:
        return
    from app.character.persona import Persona
    from app.sandbox.persona import build_persona_payload

    payload = build_persona_payload(sandbox.bible)
    await bot.personas.save(Persona.model_validate(payload))
    await bot.character.personas.load()
    await sandbox.store.state_set("persona_synced_version", sandbox.bible.version)


async def _run(root: Path, port: int, password: str) -> int:
    config = build_config(root, port, password)
    # Deterministic bootstrap password: an inherited CATOOBOT_WEB_PASSWORD in
    # the outer shell must not silently override the E2E value.
    os.environ[config.web.password_env] = password

    setup_logging(
        config.logging.level,
        config.logging.log_dir,
        color=False,
        narrate=False,
        narrate_thinking=False,
    )

    adapter = OneBotV11Server(config.onebot)
    bot = Bot(config, adapter)
    if bot.web_auth is None:  # pragma: no cover - Bot always builds it
        raise RuntimeError("Bot.web_auth missing; cannot boot the WebUI")

    await bot.database.connect()
    # Bot.start() sets this; the WebUI runtime page reads it.
    bot.started_at = time.time()
    await bot.character.start()
    if bot.tools is not None:
        await bot.tools.start()
    await _seed_fixture_data(bot)
    if bot.sandbox is not None:
        await bot.sandbox.start()
        await _sync_persona_from_bible(bot)

    from app.web.server import WebServer
    from app.web.services.config_admin import ConfigAdminService

    server = WebServer(config.web, bot)
    # Isolation seam documented on ConfigAdminService: WebUI config writes go
    # to a temp overrides file, never to the repo's config/overrides.yaml.
    server._config_admin = ConfigAdminService(  # noqa: SLF001 - documented test seam
        bot, overrides_path=root / "overrides.yaml"
    )

    if not (REPO_ROOT / "webui" / "dist" / "index.html").is_file():
        raise RuntimeError("webui/dist/index.html is missing — run `npm run build` in webui/ first")

    await server.start()
    print(f"E2E server ready on http://127.0.0.1:{port}", flush=True)

    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        try:
            asyncio.get_event_loop().call_soon_threadsafe(stop.set)
        except RuntimeError:  # pragma: no cover - loop already gone
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass

    try:
        await stop.wait()
    finally:
        print("E2E server stopping…", flush=True)
        await server.stop()
        try:
            await bot.ai.close()
            await bot.tools.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        await bot.database.close()
    return 0


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="catoobot-e2e-"))
    port = _env_int("CATOOBOT_E2E_PORT", DEFAULT_PORT)
    password = os.environ.get("CATOOBOT_E2E_PASSWORD") or DEFAULT_PASSWORD
    try:
        return asyncio.run(_run(root, port, password))
    except KeyboardInterrupt:  # pragma: no cover - Playwright kills the process
        return 0


if __name__ == "__main__":
    sys.exit(main())
