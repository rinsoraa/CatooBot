"""The ``/config`` page: edit the running configuration from the browser.

Rendered server-side from :mod:`app.web.ui` components. Every save goes through
:class:`~app.web.services.config_admin.ConfigAdminService`, which validates by
building a real ``AppConfig`` *before* touching disk.
"""

from __future__ import annotations

from typing import Any

from app.web import ui

TABS: tuple[tuple[str, str, str], ...] = (
    ("basic", "基础与权限", "机器人名称、日志开关、超级管理员、记忆总开关"),
    ("ai", "AI 模型", "添加/修改 Provider 与 Model、冷却时间、默认温度"),
    ("onebot", "OneBot 接入", "监听地址与访问令牌（写入 .env，不落 YAML）"),
    ("web", "WebUI 账号", "登录用户名与密码（PBKDF2 哈希存库）"),
    ("raw", "高级 YAML", "直接编辑 WebUI 覆盖配置（保存前会校验）"),
)


def _tab_links(active: str) -> str:
    links = " ".join(
        f'<a href="/config?tab={key}" class="tab{" active" if key == active else ""}"'
        f"{ui.attr_tip(tip_text)}>{ui.esc(label)}</a>"
        for key, label, tip_text in TABS
    )
    return f"<nav class='tabs'>{links}</nav>"


def render(
    service: Any,
    *,
    tab: str = "basic",
    msg: str = "",
    kind: str = "ok",
    engine_report: dict[str, Any] | None = None,
) -> str:
    snapshot = service.snapshot()
    tab = tab if tab in {key for key, _label, _tip in TABS} else "basic"
    body = [
        ui.flash(kind, msg) if msg else "",
        _tab_links(tab),
        _restart_note(),
    ]
    if tab == "basic":
        body.append(_basic(service, snapshot))
    elif tab == "ai":
        body.append(_ai(service, snapshot, engine_report))
    elif tab == "onebot":
        body.append(_onebot(snapshot))
    elif tab == "web":
        body.append(_web(snapshot))
    else:
        body.append(_raw(service))
    return "".join(body)


def _restart_note() -> str:
    return (
        "<p class='hint'>"
        "这里保存的内容写入 <code>config/overrides.yaml</code>，"
        "会叠加在 <code>config.yaml</code> 之上（不会改动你手写的注释），"
        "大部分设置<strong>保存即生效</strong>；监听地址、端口、数据库等标了「重启后生效」的项需要重启进程。"
        "</p>"
    )


def _basic(service: Any, snapshot: dict[str, Any]) -> str:
    cfg = snapshot
    bot = cfg["bot"]
    logging_cfg = cfg["logging"]
    permissions = cfg["permissions"]
    memory = cfg["memory"]
    content = f"""
<form method="post" action="/config/basic">
  {
        ui.field(
            "机器人名称",
            "bot_name",
            bot["name"],
            tip_text="显示在 WebUI 与日志里的名字，不影响 QQ 昵称",
        )
    }
  {
        ui.field(
            "命令前缀",
            "command_prefix",
            bot["command_prefix"],
            tip_text="内部命令系统的前缀；QQ 端自 v0.3 起没有任何命令，这一项只影响插件/内部调用",
        )
    }
  {
        ui.switch(
            "bot_debug",
            bot["debug"],
            "调试模式",
            tip_text="打开后日志会输出原始 OneBot JSON，排查连接问题时很有用（信息量大）",
        )
    }
  <div class="section-title">日志与终端</div>
  {
        ui.select(
            "日志级别",
            "log_level",
            [
                ("DEBUG", "DEBUG（最详细）"),
                ("INFO", "INFO（推荐）"),
                ("WARNING", "WARNING"),
                ("ERROR", "ERROR"),
            ],
            logging_cfg["level"],
            tip_text="控制台与文件同时生效；调高之后播报也会安静下来",
        )
    }
  <div>
    {
        ui.switch(
            "log_color",
            logging_cfg["color"],
            "彩色终端输出",
            tip_text="管道/重定向到文件时自动变纯文本；NO_COLOR 环境变量优先级最高",
        )
    }
    {
        ui.switch(
            "log_narrate",
            logging_cfg["narrate"],
            "内心播报",
            tip_text="在控制台打印她的心理历程 / 思考 / 心流 / 碎碎念 / 沙盒状态（只给运营者看，不进 QQ）",
        )
    }
    {
        ui.switch(
            "log_narrate_ticks",
            logging_cfg["narrate_world_ticks"],
            "每次沙盒心跳都播报",
            tip_text="默认只在状态变化时播报；打开后每 tick 都会打印一行（信息较密）",
        )
    }
  </div>
  <div class="section-title">权限</div>
  {
        ui.field(
            "超级管理员 QQ",
            "superusers",
            "、".join(permissions["superusers"]),
            tip_text="拥有全部权限；多个用逗号/空格分隔",
        )
    }
  {
        ui.field(
            "管理员 QQ",
            "admins",
            "、".join(permissions["admins"]),
            tip_text="比普通用户高一级，不能改配置",
        )
    }
  <div class="section-title">记忆</div>
  {
        ui.switch(
            "memory_enabled",
            memory["enabled"],
            "启用长期记忆",
            tip_text="关闭后不再提取与检索记忆（已有数据保留，可在记忆页查看）",
        )
    }
  {
        ui.switch(
            "memory_extraction",
            memory["extraction"]["enabled"],
            "回复后自动提取记忆",
            tip_text="后台异步执行，不阻塞聊天；关掉后只能靠「记忆修正」手工写入",
        )
    }
  {
        ui.field(
            "提取使用的模型",
            "memory_extract_model",
            memory["extraction"]["model"],
            tip_text="填 ai.models 里的名字；留空使用主模型",
        )
    }
  {
        ui.field(
            "提取超时（秒）",
            "memory_extract_timeout",
            memory["extraction"]["timeout"],
            tip_text="超时后放弃这次提取，不会重试",
        )
    }
  <p><button class="btn btn-primary" type="submit"
      data-tip="保存这一页的改动并立即生效">保存基础设置</button></p>
</form>
<p class="hint">回复节奏 / 作息 / 主动性在 <a href="/behavior">行为</a> 页，
她此刻的生活在 <a href="/sandbox">沙盒</a> 页</p>
"""
    return ui.card("基础设置", content, tip_text="这些开关都会立刻作用到运行中的进程")


def _ai(service: Any, snapshot: dict[str, Any], engine_report: dict[str, Any] | None) -> str:
    ai = snapshot["ai"]
    providers = ai["providers"]
    models = ai["models"]
    report_html = ""
    if engine_report:
        report_html = ui.flash(
            "ok",
            "AI 已重载 · 模型："
            + ("、".join(engine_report.get("models") or []) or "无")
            + " · Provider："
            + ("、".join(engine_report.get("providers") or []) or "无"),
        )

    provider_rows = []
    for index, (name, data) in enumerate(providers.items()):
        key_state = (
            ui.badge("Key 已就绪", "success")
            if data["has_key"]
            else ui.badge("缺少 Key", "warning")
        )
        provider_rows.append(
            f"<tr><td><input name='p_name_{index}' value='{ui.esc(name)}'"
            f"{ui.attr_tip('Provider 名称，模型里的 provider 字段要与之对应')}></td>"
            f"<td><select name='p_type_{index}'>"
            f"<option value='openai_compatible'"
            f"{' selected' if data['type'] == 'openai_compatible' else ''}>openai_compatible</option>"
            f"</select></td>"
            f"<td><input name='p_base_{index}' value='{ui.esc(data['base_url'])}'"
            f"{ui.attr_tip('兼容 OpenAI 的接口地址，例如 http://127.0.0.1:7864/v1')}></td>"
            f"<td><input name='p_env_{index}' value='{ui.esc(data['api_key_env'])}'"
            f"{ui.attr_tip('读取 API Key 的环境变量名（Key 本身不写进 YAML）')}></td>"
            f"<td>{key_state}</td>"
            f"<td><label class='switch'{ui.attr_tip('勾选后保存即删除该 Provider')}>"
            f"<input type='checkbox' name='p_del_{index}' value='1'>"
            f"<span class='switch-track'><span class='switch-knob'></span></span>"
            f"<span class='switch-label'>删除</span></label></td></tr>"
        )
    provider_table = ui.table(
        ["名称", "类型", "Base URL", "API Key 环境变量", "状态", "删除"],
        provider_rows,
        tips=[
            "Provider 名称",
            "接口类型（目前支持 openai_compatible）",
            "接口地址",
            "API Key 所在的环境变量名（也可在下方凭据区写入 .env）",
            "环境变量里是否已有 Key",
            "勾选并保存即移除",
        ],
        empty="还没有 Provider，下面新增一个吧",
    )

    model_rows = []
    provider_names = list(providers) or [""]
    for index, model in enumerate(models):
        options = "".join(
            f"<option value='{ui.esc(name)}'"
            f"{' selected' if name == model['provider'] else ''}>{ui.esc(name)}</option>"
            for name in provider_names
        )
        live = ui.badge("已生效", "success") if model["live"] else ui.badge("未生效", "warning")
        model_rows.append(
            f"<tr><td><input name='m_name_{index}' value='{ui.esc(model['name'])}'"
            f"{ui.attr_tip('模型别名，会显示在日志与 WebUI 中')}></td>"
            f"<td><select name='m_provider_{index}'>{options}</select></td>"
            f"<td><input name='m_model_{index}' value='{ui.esc(model['model'])}'"
            f"{ui.attr_tip('服务商真实的模型 ID，例如 cn:deepseek-v4.1-flash')}></td>"
            f"<td>{live}</td>"
            f"<td><label class='switch'{ui.attr_tip('勾选后该模型不参与调度（列表顺序即优先级）')}>"
            f"<input type='checkbox' name='m_off_{index}' value='1'"
            f"{'' if model['enabled'] else ' checked'}>"
            f"<span class='switch-track'><span class='switch-knob'></span></span>"
            f"<span class='switch-label'>停用</span></label></td>"
            f"<td><label class='switch'{ui.attr_tip('勾选后保存即删除该模型')}>"
            f"<input type='checkbox' name='m_del_{index}' value='1'>"
            f"<span class='switch-track'><span class='switch-knob'></span></span>"
            f"<span class='switch-label'>删除</span></label></td></tr>"
        )
    model_table = ui.table(
        ["别名", "Provider", "模型 ID", "状态", "停用", "删除"],
        model_rows,
        tips=[
            "模型别名",
            "使用哪个 Provider",
            "服务商模型 ID",
            "是否已装载到运行时",
            "临时停用（不删除）",
            "勾选并保存即移除",
        ],
        empty="还没有模型",
    )

    content = f"""
{report_html}
<form method="post" action="/config/ai">
  <div class="section-title">全局</div>
  {
        ui.switch(
            "ai_enabled",
            ai["enabled"],
            "启用 AI",
            tip_text="总开关；关闭后 QQ 里只会收到固定的忙碌回复",
        )
    }
  {
        ui.field(
            "默认温度",
            "ai_temperature",
            ai["default_temperature"],
            tip_text="0~2，越高越活泼；角色聊天一般 0.7~0.9",
        )
    }
  {ui.field("请求超时（秒）", "ai_timeout", ai["timeout"], tip_text="超过这个时间就换下一个模型")}
  {
        ui.field(
            "限流冷却（秒）",
            "ai_rate_cooldown",
            ai["cooldown"]["rate_limit_seconds"],
            tip_text="遇到 429 时该模型冷却多久，期间自动走备用模型",
        )
    }
  {
        ui.field(
            "服务错误冷却（秒）",
            "ai_server_cooldown",
            ai["cooldown"]["server_error_seconds"],
            tip_text="5xx/超时后的冷却时间",
        )
    }

  <div class="section-title">Provider（模型服务商）</div>
  {provider_table}
  <div class="section-title">新增 Provider</div>
  <div class="grid">
    {ui.field("名称", "p_new_name", "", tip_text="例如 workbuddy / siliconflow")}
    {ui.field("Base URL", "p_new_base", "", tip_text="例如 http://127.0.0.1:7864/v1")}
    {ui.field("API Key 环境变量", "p_new_env", "", tip_text="例如 WORKBUDDY_API_KEY")}
  </div>

  <div class="section-title">Model（模型列表，顺序即优先级）</div>
  {model_table}
  <div class="section-title">新增模型</div>
  <div class="grid">
    {ui.field("别名", "m_new_name", "", tip_text="例如 primary / secondary")}
    {ui.field("模型 ID", "m_new_model", "", tip_text="例如 cn:deepseek-v4.1-flash")}
    {ui.field("Provider", "m_new_provider", "", tip_text="填上面某个 Provider 名称")}
  </div>
  <p>
    <button class="btn btn-primary" type="submit"
      data-tip="保存后立即重建 Provider 与模型路由，无需重启">保存并热重载</button>
  </p>
</form>
<p class="hint">模型的启用/停用与优先级也可以在 <a href="/models">模型</a> 页面即时调整；
API Key 请写入 <code>.env</code>（下方凭据区或服务器上手工设置）。</p>
"""
    return ui.card("AI 模型与 Provider", content, tip_text="改完保存即生效，无需重启进程")


def _onebot(snapshot: dict[str, Any]) -> str:
    onebot = snapshot["onebot"]
    content = f"""
<form method="post" action="/config/onebot">
  {
        ui.field(
            "监听地址",
            "onebot_host",
            onebot["host"],
            tip_text="NapCat 反向 WebSocket 要连接到这里；改动需要重启",
        )
    }
  {
        ui.field(
            "监听端口",
            "onebot_port",
            onebot["port"],
            tip_text="默认 8080，改完记得同步修改 NapCat 的 URL",
        )
    }
  {
        ui.field(
            "WebSocket 路径",
            "onebot_path",
            onebot["path"],
            tip_text="默认 /onebot/v11/ws，必须与 NapCat 里填的完全一致",
        )
    }
  {
        ui.field(
            "API 调用超时（秒）",
            "onebot_api_timeout",
            onebot["api_timeout"],
            tip_text="调用 send_* 等接口的等待上限",
        )
    }
  {
        ui.field(
            "访问令牌",
            "onebot_token",
            "",
            type="password",
            tip_text="写入 .env 的 CATOOBOT_ONEBOT_ACCESS_TOKEN；留空表示不修改（YAML 里永远是空值）",
        )
    }
  <p><button class="btn btn-primary" type="submit"
      data-tip="保存；地址/端口/路径需要重启进程才会生效">保存 OneBot 设置</button></p>
</form>
<p class="hint">当前生效地址：<code>{ui.esc(snapshot["onebot"]["host"])}:{
        snapshot["onebot"]["port"]
    }{ui.esc(snapshot["onebot"]["path"])}</code></p>
"""
    return ui.card("OneBot 接入", content, tip_text="CatooBot 在这里监听 NapCat 的连接")


def _web(snapshot: dict[str, Any]) -> str:
    web = snapshot["web"]
    content = f"""
<form method="post" action="/config/web">
  {ui.field("用户名", "web_username", web["username"], tip_text="WebUI 登录账号，可改名")}
  {
        ui.field(
            "新密码",
            "web_password",
            "",
            type="password",
            tip_text="至少 6 位；保存后所有已登录会话会失效，需要重新登录",
        )
    }
  {
        ui.field(
            "监听地址",
            "web_host",
            web["host"],
            tip_text="默认 127.0.0.1（只允许本机访问），改动需要重启",
        )
    }
  {ui.field("监听端口", "web_port", web["port"], tip_text="默认 8500，改动需要重启")}
  <p><button class="btn btn-primary" type="submit"
      data-tip="保存账号设置；端口改动重启后生效">保存 WebUI 设置</button></p>
</form>
"""
    return ui.card("WebUI 账号", content, tip_text="密码只保存 PBKDF2 哈希，明文不会落盘")


def _raw(service: Any) -> str:
    text = service.overrides_yaml() or "# 目前没有任何覆盖项（全部使用 config.yaml 的值）\n"
    content = f"""
<form method="post" action="/config/raw">
  <label class="field">
    <span class="field-label">config/overrides.yaml{ui.tip("WebUI 写入的覆盖配置；删掉本文件就等于回到 config.yaml 原样")}</span>
    <textarea name="raw" rows="18" class="mono">{ui.esc(text)}</textarea>
  </label>
  <p>
    <button class="btn btn-primary" type="submit" data-tip="先校验再写入，出错会保持原样">保存覆盖配置</button>
  </p>
</form>
<form method="post" action="/config/reset" data-confirm="确定要清空所有覆盖项，恢复 config.yaml 的原样吗？">
  <button class="btn btn-danger" type="submit"
    data-tip="删除 overrides.yaml 并重新读取 config.yaml">清空全部覆盖</button>
</form>
"""
    return ui.card("高级：覆盖配置", content, tip_text="给熟悉 YAML 的运营者用的兜底入口")
