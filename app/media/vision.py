"""Image understanding runtime (v1.1 §5/§6/§42/§45/§68).

Reuses the existing :class:`~app.ai.engine.AIEngine` — no new provider. A
multimodal :class:`ChatMessage` carries the image; the provider serializes it.
Results are cached by sha256 so the same image is never analysed twice, and a
failed analysis degrades to an empty structured result instead of a guess
(spec §68).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.media.models import VisionResult

VISION_PROMPT = """你是一个图片理解工具。请用中文简洁描述这张图片，只输出 JSON，不要解释：
{"summary":"一句话描述","objects":["物体1","物体2"],"scene":"场景","ocr_text":["识别到的文字"],"people_count":0,"actions":["动作"],"mood":"氛围","visual_tags":["标签"],"confidence":0.0}
不确定的项留空或空数组，不要编造。"""


def data_url_from_bytes(data: bytes, mime_type: str = "image/png") -> str:
    """Encode raw image bytes as a base64 data URL for the vision model."""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class ImageUnderstandingRuntime:
    def __init__(
        self,
        *,
        engine: Any,
        database: Any = None,
        vision_model: str = "",
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._engine = engine
        self._db = database
        self._vision_model = vision_model
        self._log = logger or logging.getLogger("CatooBot.Media")
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self._engine is not None and getattr(self._engine, "enabled", False))

    async def analyze(self, image: str, *, sha256_hash: str = "") -> VisionResult:
        """Analyze one image URL / data-URL; returns a structured result."""
        if not self.enabled:
            self._log.warning(
                "[Media.Vision] vision unavailable (AI off or no model) — image skipped"
            )
            return VisionResult()
        if sha256_hash:
            cached = await self._cache_get(sha256_hash)
            if cached is not None:
                return cached

        request = AIRequest(
            messages=[ChatMessage.user_with_images(VISION_PROMPT, [image])],
            temperature=0.1,
            # reasoning-style vision models spend tokens thinking first; a tight
            # cap truncates the JSON before it ever closes (seen in logs as
            # finish=length). 800 leaves room for thought + the small payload.
            max_tokens=800,
        )
        if self._vision_model:
            request = request.with_model(self._vision_model)
        try:
            response = await self._engine.chat(request)
        except AIError as exc:
            self._log.warning("[Media.Vision] analysis failed: %s", exc)
            result = VisionResult()
            await self._cache_put(sha256_hash, result, failed=True)
            return result
        result = self._parse(response.content)
        if sha256_hash:
            await self._cache_put(sha256_hash, result)
        return result

    @staticmethod
    def _parse(content: str) -> VisionResult:
        text = (content or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return VisionResult()
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return VisionResult()
        if not isinstance(data, dict):
            return VisionResult()

        def as_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item) for item in value][:12]
            return []

        def as_int(value: Any) -> int:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        def as_float(value: Any) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return 0.0

        return VisionResult(
            summary=str(data.get("summary", ""))[:300],
            objects=as_list(data.get("objects")),
            scene=str(data.get("scene", ""))[:80],
            ocr_text=as_list(data.get("ocr_text")),
            people_count=as_int(data.get("people_count")),
            actions=as_list(data.get("actions")),
            mood=str(data.get("mood", ""))[:40],
            visual_tags=as_list(data.get("visual_tags")),
            confidence=as_float(data.get("confidence")),
        )

    # ---------------------------------------------------------------- cache

    async def _cache_get(self, sha256_hash: str) -> VisionResult | None:
        if self._db is None or not sha256_hash:
            return None
        try:
            row = await self._db.fetchone(
                "SELECT data FROM image_analysis WHERE sha256 = ?", (sha256_hash,)
            )
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        try:
            return VisionResult.model_validate(json.loads(row["data"]))
        except (TypeError, ValueError):
            return None

    async def _cache_put(
        self, sha256_hash: str, result: VisionResult, *, failed: bool = False
    ) -> None:
        if self._db is None or not sha256_hash:
            return
        try:
            await self._db.execute(
                """INSERT INTO image_analysis (sha256, data, status, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(sha256) DO UPDATE SET data=excluded.data, status=excluded.status""",
                (
                    sha256_hash,
                    json.dumps(result.model_dump(), ensure_ascii=False),
                    "failed" if failed else "ok",
                    int(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Media.Vision] cache write failed")
