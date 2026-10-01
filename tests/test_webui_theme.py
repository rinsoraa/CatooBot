"""WebUI theming + component tests (v0.9 UI work).

Spec coverage: light/dark modes and accent themes render as data attributes,
tooltips become accessible notes, the login page uses a minimal shell (no nav
links for an unauthenticated visitor), and the component helpers escape input.
"""

from __future__ import annotations

from app.web import ui


class TestThemeAndShell:
    def test_page_carries_mode_and_theme(self) -> None:
        page = ui.page("x", "/", "", prefs={"mode": "dark", "theme": "sakura"})
        assert 'data-mode="dark"' in page
        assert 'data-theme="sakura"' in page
        assert "cbToggleMode" in page and "cbSetTheme" in page

    def test_invalid_prefs_normalize(self) -> None:
        page = ui.page("x", "/", "", prefs={"mode": "neon", "theme": "garbage"})
        assert 'data-mode="light"' in page
        assert 'data-theme="blue"' in page

    def test_login_uses_minimal_shell_without_nav(self) -> None:
        page = ui.page("登录", "", "hello", minimal=True)
        assert "<aside" not in page  # no sidebar shell on the login screen
        assert "cbToggleMode" in page  # theme/mode still work
        assert "hello" in page

    def test_regular_page_has_sidebar_and_signature(self) -> None:
        page = ui.page("仪表盘", "/", "body")
        assert "nav-item" in page
        assert "Rinsora" in page
        assert "v0.8" in page

    def test_preferences_read_from_cookies(self) -> None:
        class Cookies:
            def get(self, key: str, default=None):  # noqa: ANN001
                return {"catoobot_mode": "dark", "catoobot_theme": "violet"}.get(key, default)

        prefs = ui.preferences_from(Cookies())
        assert prefs == {"mode": "dark", "theme": "violet", "nav": "open", "lang": "zh"}


class TestTooltips:
    def test_tip_renders_an_accessible_note(self) -> None:
        html = ui.tip("这是说明")
        assert "这是说明" in html
        assert 'data-tip="这是说明"' in html
        assert 'role="note"' in html

    def test_attr_tip_escapes(self) -> None:
        markup = ui.attr_tip('他说 "你好" <脚本>')
        assert "&quot;" in markup and "&lt;" in markup

    def test_components_carry_tips(self) -> None:
        field = ui.field("名称", "name", "x", tip_text="给模型起个名字")
        assert "给模型起个名字" in field
        switch = ui.switch("on", True, "开", tip_text="开关说明")
        assert "开关说明" in switch
        btn = ui.button("保存", tip_text="保存后生效")
        assert "保存后生效" in btn

    def test_all_values_escaped(self) -> None:
        # a user name must never break out of the markup
        field = ui.field("名字", "name", '"><script>alert(1)</script>')
        assert "<script>" not in field
        assert "&lt;script&gt;" in field


class TestComponents:
    def test_stats_grid(self) -> None:
        html = ui.stats_grid([("在线", "是", "NapCat 是否连接"), ("用户", 3, "用户数")])
        assert "NapCat 是否连接" in html and "用户数" in html

    def test_table_filters_and_tips(self) -> None:
        html = ui.table(
            ["ID", "内容"],
            ["<tr><td>1</td><td>记忆A</td></tr>"],
            tips=["主键", "正文"],
            table_id="t1",
        )
        assert "id='t1'" in html
        assert "table-filter" in html
        assert "主键" in html

    def test_table_empty_state(self) -> None:
        html = ui.table(["A"], [], empty="啥也没有", table_id="t")
        assert "啥也没有" in html

    def test_badge_and_progress(self) -> None:
        assert "badge-success" in ui.badge("开启", "success")
        assert "progress-bar" in ui.progress(0.5)

    def test_flash_and_tabs(self) -> None:
        assert "flash-ok" in ui.flash("ok", "成功")
        tabs = ui.tabs([("/a", "甲"), ("/b", "乙")], "/b")
        assert "active" in tabs and "乙" in tabs


class TestThemes:
    def test_every_theme_is_known(self) -> None:
        for key, (_label, primary, fg) in ui.THEMES.items():
            assert ui.normalize_theme(key) == key
            assert " " in primary and " " in fg

    def test_normalize_unknown_theme(self) -> None:
        assert ui.normalize_theme("lime") == "blue"
        assert ui.normalize_mode("sepia") == "light"
