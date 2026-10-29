"""`.env` 写入隔离（W6 回归：测试不得碰操作者的真实 .env）。

历史事故：`tests/test_web_api_config.py` 里的一个用例把
``CATOOBOT_ONEBOT_ACCESS_TOKEN`` 写进了项目真实 `.env`，使 OneBot WS 服务器
开始强制校验 Token —— NapCat 的每次反向连接都被 401 拒绝。
本文件把护栏钉死。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import env_store
from app.config.settings import PROJECT_ROOT


class TestEnvStoreGuard:
    def test_writing_the_real_env_from_a_test_is_refused(self) -> None:
        """默认路径 = 操作者真实 .env → 在测试进程里必须拒绝。"""
        assert os.environ.get("PYTEST_CURRENT_TEST"), "该用例必须跑在 pytest 下"
        with pytest.raises(RuntimeError, match="拒绝在测试中写入真实"):
            env_store.write_env_secret("CATOOBOT_TEST_GUARD", "x")
        with pytest.raises(RuntimeError, match="拒绝在测试中写入真实"):
            env_store.remove_env_secret("CATOOBOT_TEST_GUARD")

    def test_an_explicit_temporary_path_works_and_preserves_other_lines(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / ".env"
        target.write_text("# 注释保留\nOTHER_KEY=keep-me\nCATOOBOT_X=old\n", encoding="utf-8")
        env_store.write_env_secret("CATOOBOT_X", "new", path=target)
        text = target.read_text(encoding="utf-8")
        assert "# 注释保留" in text
        assert "OTHER_KEY=keep-me" in text
        assert "CATOOBOT_X=new" in text
        assert "CATOOBOT_X=old" not in text
        # 原子替换：同目录不留临时文件
        assert [p.name for p in tmp_path.iterdir()] == [".env"]

        assert env_store.remove_env_secret("CATOOBOT_X", path=target) is True
        remaining = target.read_text(encoding="utf-8")
        assert "CATOOBOT_X" not in remaining
        assert "OTHER_KEY=keep-me" in remaining

    def test_a_monkeypatched_default_path_is_allowed(self, tmp_path: Path, monkeypatch) -> None:
        """测试把 env_path 指到临时文件后，正常写入路径必须照常工作。"""
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        env_store.write_env_secret("CATOOBOT_Y", "value")
        assert "CATOOBOT_Y=value" in (tmp_path / ".env").read_text(encoding="utf-8")


class TestConfigAdminWriter:
    def test_set_env_secret_uses_the_atomic_writer_and_preserves_lines(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.core.bot import Bot
        from app.web.services import config_admin as config_admin_module
        from tests.test_web_realtime import DummyAdapter

        target = tmp_path / ".env"
        target.write_text("KEEP=1\nCATOOBOT_ONEBOT_ACCESS_TOKEN=old\n", encoding="utf-8")
        monkeypatch.setattr(config_admin_module, "env_path", lambda: target)

        from app.config.settings import AppConfig

        bot = Bot(
            AppConfig(
                bot={"name": "T"},
                database={"url": "sqlite:///" + str(tmp_path / "iso.db")},
                logging={"log_dir": str(tmp_path / "logs")},
                web={"enabled": True},
            ),
            DummyAdapter(),
        )
        service = config_admin_module.ConfigAdminService(bot)
        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        assert service.set_env_secret("CATOOBOT_ONEBOT_ACCESS_TOKEN", "fresh") is True

        text = target.read_text(encoding="utf-8")
        assert "KEEP=1" in text
        assert "CATOOBOT_ONEBOT_ACCESS_TOKEN=fresh" in text
        assert os.environ["CATOOBOT_ONEBOT_ACCESS_TOKEN"] == "fresh"

    def test_set_env_secret_refuses_the_real_env(self, tmp_path: Path) -> None:
        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from app.web.services import config_admin as config_admin_module
        from tests.test_web_realtime import DummyAdapter

        bot = Bot(
            AppConfig(
                bot={"name": "T"},
                database={"url": "sqlite:///" + str(tmp_path / "iso2.db")},
                logging={"log_dir": str(tmp_path / "logs")},
                web={"enabled": True},
            ),
            DummyAdapter(),
        )
        service = config_admin_module.ConfigAdminService(bot)
        with pytest.raises(RuntimeError, match="拒绝在测试中写入真实"):
            service.set_env_secret("CATOOBOT_ONEBOT_ACCESS_TOKEN", "should-not-land")


class TestOperatorEnvIsHealthy:
    def test_real_env_has_no_stray_auto_token(self) -> None:
        """当前真实 .env 不应存在「测试写入」的 OneBot token（事故清理后的状态）。"""
        env_file = PROJECT_ROOT / ".env"
        if not env_file.exists():
            pytest.skip("no operator .env in this checkout")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith("CATOOBOT_ONEBOT_ACCESS_TOKEN="), (
                "真实 .env 里又出现了生效的 OneBot token —— 它会让 NapCat 全部 401；"
                "如需启用请与 NapCat 的 Token 保持一致"
            )
