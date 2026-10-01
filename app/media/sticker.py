"""Sticker runtime (v1.1 §7-§36): the character's expression assets.

This is a *character asset* system, deliberately separate from Memory (§52).
It owns: the library (SQLite + files), acquisition (should I keep this?), the
selector (which one fits now?), the expression decision (text / +sticker /
sticker-only), and sending. Ordinary images never enter here (v1.x 规格，原文缺失：媒体类型边界).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.config.settings import project_path
from app.media.models import (
    AcquisitionDecision,
    ExpressionContext,
    ExpressionDecision,
    MediaContent,
    NativeFace,
    StickerAsset,
    VisionResult,
)
from app.memory.retrieval import bigrams

#: a small built-in QQ native-face map (face_id -> meaning). The operator may
#: extend this in the WebUI; we only ship a useful subset (§30).
DEFAULT_NATIVE_FACES: dict[int, NativeFace] = {
    14: NativeFace(
        face_id=14, display_name="微笑", emotion="开心", intent="友好", tags=["微笑", "友善"]
    ),
    66: NativeFace(
        face_id=66, display_name="害羞", emotion="害羞", intent="回应", tags=["害羞", "可爱"]
    ),
    12: NativeFace(
        face_id=12, display_name="发怒", emotion="生气", intent="吐槽", tags=["生气", "愤怒"]
    ),
    5: NativeFace(
        face_id=5, display_name="难过", emotion="难过", intent="安慰", tags=["难过", "哭"]
    ),
    6: NativeFace(
        face_id=6, display_name="惊讶", emotion="震惊", intent="回应", tags=["惊讶", "震惊"]
    ),
    4: NativeFace(
        face_id=4, display_name="尴尬", emotion="尴尬", intent="缓和", tags=["尴尬", "无语"]
    ),
    274: NativeFace(
        face_id=274,
        display_name="笑哭",
        emotion="开心",
        intent="好笑",
        tags=["笑哭", "开心", "爆笑"],
    ),
    34: NativeFace(
        face_id=34, display_name="亲亲", emotion="亲密", intent="亲昵", tags=["亲亲", "喜欢"]
    ),
}

#: emotion -> default intent when the analyzer can't infer one.
_EMOTION_INTENT = {
    "开心": "开心",
    "好笑": "好笑",
    "震惊": "惊讶",
    "难过": "安慰",
    "生气": "吐槽",
    "无语": "吐槽",
    "尴尬": "缓和",
    "疲惫": "无奈",
    "害羞": "回应",
}

_STICKER_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _lexical_similarity(left: str, right: str) -> float:
    left, right = (left or "").strip(), (right or "").strip()
    if not left or not right:
        return 0.0
    lu, ru = set(left), set(right)
    uni = len(lu & ru) / max(1, min(len(lu), len(ru)))
    lb, rb = bigrams(left), bigrams(right)
    bi = 0.0
    if lb and rb:
        bi = len(lb & rb) / max(1, min(len(lb), len(rb)))
    return min(1.0, max(uni, bi * 1.2))


class NativeFaceRegistry:
    def __init__(self, faces: dict[int, NativeFace] | None = None) -> None:
        self._faces = dict(DEFAULT_NATIVE_FACES)
        if faces:
            self._faces.update(faces)

    def get(self, face_id: int) -> NativeFace | None:
        return self._faces.get(face_id)

    def all(self) -> list[NativeFace]:
        return list(self._faces.values())


class StickerLibrary:
    """SQLite-backed sticker asset store (§8/§22). Not Memory."""

    def __init__(
        self,
        *,
        database: Any,
        sticker_dir: str = "data/stickers",
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Media")
        self._clock = clock
        self.dir = project_path(sticker_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------- read

    async def get(self, sticker_id: str) -> StickerAsset | None:
        row = await self._db.fetchone("SELECT * FROM sticker_assets WHERE id = ?", (sticker_id,))
        return self._from_row(row) if row else None

    async def by_sha256(self, sha: str) -> StickerAsset | None:
        row = await self._db.fetchone(
            "SELECT * FROM sticker_assets WHERE sha256 = ? LIMIT 1", (sha,)
        )
        return self._from_row(row) if row else None

    async def all(self, *, status: str = "active", limit: int = 200) -> list[StickerAsset]:
        rows = await self._db.fetchall(
            "SELECT * FROM sticker_assets WHERE status = ? ORDER BY usage_count DESC, created_at DESC LIMIT ?",
            (status, int(limit)),
        )
        return [self._from_row(row) for row in rows]

    async def count(self, status: str = "active") -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM sticker_assets WHERE status = ?", (status,)
        )
        return int(row["n"]) if row else 0

    async def search(
        self, *, query: str = "", emotion: str = "", intent: str = "", limit: int = 50
    ) -> list[StickerAsset]:
        assets = await self.all(status="active", limit=500)
        results: list[tuple[float, StickerAsset]] = []
        for asset in assets:
            text = asset.visual_summary + " " + " ".join(asset.all_tags)
            score = 0.0
            if query:
                score += _lexical_similarity(query, text)
            if emotion and emotion in asset.emotion_tags:
                score += 2.0
            if intent and intent in asset.intent_tags:
                score += 1.5
            if not query and not emotion and not intent:
                score = float(asset.usage_count) * 0.001 + asset.expressiveness
            results.append((score, asset))
        results.sort(key=lambda item: item[0], reverse=True)
        return [asset for _score, asset in results[:limit]]

    # ---------------------------------------------------------------- write

    async def insert(self, asset: StickerAsset) -> StickerAsset:
        now = int(self._clock())
        asset.id = asset.id or f"sticker_{uuid.uuid4().hex[:12]}"
        asset.created_at = asset.created_at or now
        asset.updated_at = now
        await self._save(asset)
        self._log.info("[Media.Sticker] saved %s (%s)", asset.id, asset.file_name or asset.emoji_id)
        return asset

    async def update(self, asset: StickerAsset) -> None:
        asset.updated_at = int(self._clock())
        await self._save(asset)

    async def set_status(self, sticker_id: str, status: str) -> bool:
        await self._db.execute(
            "UPDATE sticker_assets SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(self._clock()), sticker_id),
        )
        return True

    async def record_usage(
        self, sticker_id: str, *, scope_key: str = "", success: bool = True
    ) -> None:
        now = int(self._clock())
        await self._db.execute(
            "INSERT INTO sticker_usage (sticker_id, scope_key, success, created_at) VALUES (?, ?, ?, ?)",
            (sticker_id, scope_key, 1 if success else 0, now),
        )
        await self._db.execute(
            "UPDATE sticker_assets SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            (now, sticker_id),
        )

    async def _save(self, asset: StickerAsset) -> None:
        await self._db.execute(
            """INSERT INTO sticker_assets
               (id, file_path, file_name, mime_type, file_size, sha256, phash, width, height,
                is_animated, origin, origin_user_id, origin_group_id, origin_message_id,
                emoji_id, emoji_package_id, emoji_key, visual_summary, ocr_text,
                emotion_tags, intent_tags, scene_tags, style_tags, general_tags,
                intensity, humor, expressiveness, reusability, novelty, quality_score,
                safety_status, analysis_version, analysis_model, analyzed_at, embedding_status,
                usage_count, last_used_at, last_selected_at, created_at, updated_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 visual_summary=excluded.visual_summary, ocr_text=excluded.ocr_text,
                 emotion_tags=excluded.emotion_tags, intent_tags=excluded.intent_tags,
                 scene_tags=excluded.scene_tags, style_tags=excluded.style_tags, general_tags=excluded.general_tags,
                 intensity=excluded.intensity, humor=excluded.humor, expressiveness=excluded.expressiveness,
                 reusability=excluded.reusability, novelty=excluded.novelty, quality_score=excluded.quality_score,
                 safety_status=excluded.safety_status, analysis_version=excluded.analysis_version,
                 analysis_model=excluded.analysis_model, analyzed_at=excluded.analyzed_at,
                 usage_count=excluded.usage_count, last_used_at=excluded.last_used_at,
                 last_selected_at=excluded.last_selected_at, updated_at=excluded.updated_at, status=excluded.status""",
            self._tuple(asset),
        )

    def _tuple(self, asset: StickerAsset) -> tuple[Any, ...]:
        return (
            asset.id,
            asset.file_path,
            asset.file_name,
            asset.mime_type,
            asset.file_size,
            asset.sha256,
            asset.phash,
            asset.width,
            asset.height,
            1 if asset.is_animated else 0,
            asset.origin,
            asset.origin_user_id,
            asset.origin_group_id,
            asset.origin_message_id,
            asset.emoji_id,
            asset.emoji_package_id,
            asset.emoji_key,
            asset.visual_summary,
            asset.ocr_text,
            json.dumps(asset.emotion_tags, ensure_ascii=False),
            json.dumps(asset.intent_tags, ensure_ascii=False),
            json.dumps(asset.scene_tags, ensure_ascii=False),
            json.dumps(asset.style_tags, ensure_ascii=False),
            json.dumps(asset.general_tags, ensure_ascii=False),
            asset.intensity,
            asset.humor,
            asset.expressiveness,
            asset.reusability,
            asset.novelty,
            asset.quality_score,
            asset.safety_status,
            asset.analysis_version,
            asset.analysis_model,
            asset.analyzed_at,
            asset.embedding_status,
            asset.usage_count,
            asset.last_used_at,
            asset.last_selected_at,
            asset.created_at,
            asset.updated_at,
            asset.status,
        )

    @staticmethod
    def _from_row(row: dict[str, Any]) -> StickerAsset:
        return StickerAsset(
            id=row["id"],
            file_path=row.get("file_path") or "",
            file_name=row.get("file_name") or "",
            mime_type=row.get("mime_type") or "",
            file_size=int(row.get("file_size") or 0),
            sha256=row.get("sha256") or "",
            phash=row.get("phash") or "",
            width=int(row.get("width") or 0),
            height=int(row.get("height") or 0),
            is_animated=bool(row.get("is_animated")),
            origin=row.get("origin") or "manual_import",
            origin_user_id=row.get("origin_user_id") or "",
            origin_group_id=row.get("origin_group_id") or "",
            origin_message_id=row.get("origin_message_id") or "",
            emoji_id=row.get("emoji_id") or "",
            emoji_package_id=row.get("emoji_package_id") or "",
            emoji_key=row.get("emoji_key") or "",
            visual_summary=row.get("visual_summary") or "",
            ocr_text=row.get("ocr_text") or "",
            emotion_tags=_json_list(_maybe_json(row.get("emotion_tags"))),
            intent_tags=_json_list(_maybe_json(row.get("intent_tags"))),
            scene_tags=_json_list(_maybe_json(row.get("scene_tags"))),
            style_tags=_json_list(_maybe_json(row.get("style_tags"))),
            general_tags=_json_list(_maybe_json(row.get("general_tags"))),
            intensity=float(row.get("intensity") or 0.0),
            humor=float(row.get("humor") or 0.0),
            expressiveness=float(row.get("expressiveness") or 0.0),
            reusability=float(row.get("reusability") or 0.0),
            novelty=float(row.get("novelty") or 0.0),
            quality_score=float(row.get("quality_score") or 0.0),
            safety_status=row.get("safety_status") or "ok",
            analysis_version=row.get("analysis_version") or "",
            analysis_model=row.get("analysis_model") or "",
            analyzed_at=int(row.get("analyzed_at") or 0),
            embedding_status=row.get("embedding_status") or "none",
            usage_count=int(row.get("usage_count") or 0),
            last_used_at=int(row.get("last_used_at") or 0),
            last_selected_at=int(row.get("last_selected_at") or 0),
            created_at=int(row.get("created_at") or 0),
            updated_at=int(row.get("updated_at") or 0),
            status=row.get("status") or "active",
        )


def _maybe_json(raw: Any) -> Any:
    if raw is None:
        return []
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


class StickerAnalyzer:
    """Derives tags + quality from a vision result / mface summary (§12)."""

    def __init__(self, *, analysis_version: str = "v1") -> None:
        self.version = analysis_version

    def analyze(self, media: MediaContent, vision: VisionResult | None) -> StickerAsset:
        summary = vision.summary if vision else ""
        if not summary and media.emoji_summary:
            summary = media.emoji_summary
        emotion = _infer_emotion(summary + " " + " ".join(vision.visual_tags if vision else []))
        intent = _EMOTION_INTENT.get(emotion, "")
        tags = list(vision.visual_tags) if vision else []
        general = [tag for tag in tags if tag not in (emotion, intent)]
        expressiveness = vision.confidence if vision else (0.5 if summary else 0.0)
        return StickerAsset(
            file_path=media.file,
            file_name=media.file.split("/")[-1] if media.file else "",
            mime_type=media.mime_type,
            file_size=media.file_size,
            sha256=media.sha256,
            width=media.width,
            height=media.height,
            is_animated=media.is_animated,
            origin="user_message" if media.source_type.startswith("qq_") else "manual_import",
            origin_user_id=media.source_user_id,
            origin_group_id=media.source_group_id,
            origin_message_id=media.source_message_id,
            emoji_id=media.emoji_id,
            emoji_package_id=media.emoji_package_id,
            emoji_key=media.emoji_key,
            visual_summary=summary,
            ocr_text=" ".join(vision.ocr_text) if vision else "",
            emotion_tags=[emotion] if emotion else [],
            intent_tags=[intent] if intent else [],
            scene_tags=[vision.scene] if vision and vision.scene else [],
            general_tags=general[:12],
            expressiveness=round(expressiveness, 3),
            reusability=round(0.4 + 0.5 * expressiveness, 3),
            novelty=1.0,
            quality_score=round(_quality(media, summary), 3),
            safety_status="ok",
            analysis_version=self.version,
        )


def _infer_emotion(text: str) -> str:
    """Infer an emotion from a *short, expressive* utterance.

    Conservative on purpose: single ambiguous characters ("气" in "天气",
    "笑" in "笑话") must not trigger a sticker. Only strong cues count.
    """
    text = (text or "").strip()
    if not text or len(text) > 16:
        return ""
    cues = [
        ("笑死", "开心"),
        ("笑哭", "开心"),
        ("哈哈哈", "开心"),
        ("哈哈", "开心"),
        ("嘿嘿", "开心"),
        ("呜呜", "难过"),
        ("难过", "难过"),
        ("伤心", "难过"),
        ("哭", "难过"),
        ("生气", "生气"),
        ("气死", "生气"),
        ("气人", "生气"),
        ("愤怒", "生气"),
        ("震惊", "震惊"),
        ("惊讶", "震惊"),
        ("无语", "无语"),
        ("尴尬", "尴尬"),
        ("好累", "疲惫"),
        ("困死", "疲惫"),
    ]
    for cue, emotion in cues:
        if cue in text:
            return emotion
    return ""


def _quality(media: MediaContent, summary: str) -> float:
    score = 0.4
    if media.file_size > 0:
        score += 0.2
    if summary:
        score += 0.3
    if media.emoji_id:
        score += 0.1
    return max(0.0, min(1.0, score))


class StickerAcquisitionEvaluator:
    """Should the character keep this sticker? (rule-based save/reject/defer)."""

    async def evaluate(
        self,
        asset: StickerAsset,
        library: StickerLibrary,
    ) -> AcquisitionDecision:
        if asset.sha256:
            existing = await library.by_sha256(asset.sha256)
            if existing is not None:
                return AcquisitionDecision(
                    decision="reject",
                    confidence=1.0,
                    novelty=0.0,
                    reason_codes=["duplicate_sha256"],
                )
        # semantic/lexical similarity to existing assets -> low novelty
        similarity = 0.0
        for existing in await library.all(status="active", limit=200):
            sim = _lexical_similarity(
                asset.visual_summary + " " + " ".join(asset.all_tags),
                existing.visual_summary + " " + " ".join(existing.all_tags),
            )
            similarity = max(similarity, sim)
            if similarity >= 0.85:
                break
        novelty = round(1.0 - similarity, 3)

        if not asset.visual_summary and not asset.emoji_id:
            return AcquisitionDecision(
                decision="defer",
                confidence=0.4,
                novelty=novelty,
                reason_codes=["insufficient_info"],
            )
        if similarity >= 0.85:
            return AcquisitionDecision(
                decision="reject",
                confidence=0.9,
                novelty=novelty,
                reason_codes=["low_novelty", "semantic_duplicate"],
            )
        if novelty >= 0.6 and asset.expressiveness >= 0.4:
            return AcquisitionDecision(
                decision="save",
                confidence=0.8 + 0.2 * novelty,
                novelty=novelty,
                reusability=asset.reusability,
                expressiveness=asset.expressiveness,
                reason_codes=["novel_expression", "fits_character_style"],
            )
        return AcquisitionDecision(
            decision="defer", confidence=0.5, novelty=novelty, reason_codes=["uncertain_value"]
        )


class ExpressionDecisionEngine:
    """Should the character attach a sticker now? (structured, no random)."""

    def __init__(
        self, *, cooldown_seconds: int = 60, max_per_turn: int = 1, clock: Any = time.time
    ) -> None:
        self._cooldown = cooldown_seconds
        self._max_per_turn = max_per_turn
        self._clock = clock
        self._last_sent: dict[str, float] = {}

    def decide(
        self, context: ExpressionContext, *, user_text: str, scope_key: str = ""
    ) -> ExpressionDecision:
        media_type = context.incoming_media_type
        last = self._last_sent.get(scope_key)
        if last is not None and self._clock() - last < self._cooldown:
            return ExpressionDecision(response_mode="text")
        # v1.2: a strong turn-level affect can open the expression door on
        # emotionally flat text (amusement from a shared joke, warmth from a
        # close relationship) — still structured, never a dice (§122).
        hint = (getattr(context, "emotion_hint", "") or "").strip()
        if not media_type and not _infer_emotion(user_text) and hint in ("开心", "好笑"):
            return ExpressionDecision(
                response_mode="text_and_sticker",
                sticker_intent="回应",
                emotion=hint,
                intensity=0.4,
                selection_required=True,
            )
        # A sticker in is a strong cue that a sticker out is natural.
        if media_type in ("sticker", "native_face"):
            return ExpressionDecision(
                response_mode="text_and_sticker",
                sticker_intent=context.incoming_sticker_semantics or "回应",
                emotion=_infer_emotion(user_text) or "开心",
                intensity=0.6,
                selection_required=True,
            )
        # Text with clear emotional markers (short expressive text only).
        emotion = _infer_emotion(user_text)
        if emotion and emotion in ("开心", "好笑", "难过", "生气", "震惊", "无语"):
            return ExpressionDecision(
                response_mode="text_and_sticker",
                sticker_intent="回应",
                emotion=emotion,
                intensity=0.5,
                selection_required=True,
            )
        return ExpressionDecision(response_mode="text")

    def note_sent(self, scope_key: str) -> None:
        self._last_sent[scope_key] = self._clock()


class StickerSelector:
    """Chooses one sticker for a given expression intent (§28)."""

    def __init__(
        self,
        *,
        library: StickerLibrary,
        faces: NativeFaceRegistry,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._library = library
        self._faces = faces
        self._log = logger or logging.getLogger("CatooBot.Media")
        self._clock = clock
        self._recent: list[str] = []

    async def select(
        self, decision: ExpressionDecision, *, scope_key: str = "", limit: int = 20
    ) -> StickerAsset | NativeFace | None:
        # Native face is the cheapest, natural option when one matches.
        if decision.emotion:
            for face in self._faces.all():
                if face.emotion == decision.emotion:
                    return face
        candidates = await self._library.search(
            emotion=decision.emotion, intent=decision.sticker_intent, limit=limit
        )
        for asset in candidates:
            if asset.id in self._recent:
                continue
            return asset
        return candidates[0] if candidates else None

    def note_selected(self, sticker_id: str) -> None:
        self._recent.append(sticker_id)
        self._recent = self._recent[-8:]


class StickerSender:
    """Converts a sticker / face / mface into a OneBot-ready Message (§32)."""

    def build_message(self, item: StickerAsset | NativeFace) -> Any:
        from app.message.message import Face, Message, Mface
        from app.message.segment import ImageSegment

        if isinstance(item, NativeFace):
            return Message([Face(item.face_id)])
        if item.emoji_id and item.emoji_package_id:
            return Message(
                [Mface(item.emoji_id, item.emoji_package_id, item.emoji_key, item.visual_summary)]
            )
        if item.file_path:
            return Message([ImageSegment(type="image", data={"file": item.file_path})])
        if item.emoji_id:
            return Message(
                [Mface(item.emoji_id, item.emoji_package_id, item.emoji_key, item.visual_summary)]
            )
        return Message([])
