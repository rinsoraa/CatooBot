# CatooBot 运行镜像：OneBot 11 反向 WebSocket（8080，NapCat 连入）+ WebUI（8500）。
#
# 依赖来自 uv.lock（--frozen），与 CI、本地测试使用同一套版本。
# 密钥绝不进镜像：.env 由 docker-compose 以环境变量注入（见 docker-compose.yml）。
#
# 容器内注意：onebot.host / web.host 必须是 0.0.0.0（默认 127.0.0.1 只允许本机连入，
# NapCat 在容器外就连不进来）。在 WebUI 配置页或 config.yaml 里改。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 依赖单独一层（改代码不再重装依赖）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 代码与随包的只读配置（config.yaml / overrides.yaml 由挂载提供，不进镜像）
COPY app/ app/
COPY plugins/ plugins/
COPY run.py ./
COPY config/config.example.yaml config/character_bible.md config/
RUN uv sync --frozen --no-dev

# 运行期可变数据：由 docker-compose 挂载到宿主机
VOLUME ["/app/config", "/app/data", "/app/logs"]

# 8080 = OneBot 反向 WS（NapCat 主动连入）；8500 = WebUI
EXPOSE 8080 8500

# 直接使用项目虚拟环境的解释器（不在启动时再跑 uv sync）
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8500), 3).close()"]

CMD ["/app/.venv/bin/python", "run.py"]
