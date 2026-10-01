"""Media / sticker data models (v1.1).

The central distinction (spec §2.1/§7/§52): an ordinary ``image`` is *never* a
sticker. Media is classified into ``image / sticker / native_face / unknown``
and only ``sticker`` sources may ever enter the Sticker Library.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MediaType = Literal["image", "sticker", "native_face", "unknown"]
SourceType = Literal["qq_image", "qq_mface", "qq_face", "manual_import"]
StickerStatus = Literal["active", "disabled", "duplicate", "archived", "invalid", "analyzing"]
AcquisitionDecisionKind = Literal["save", "reject", "defer"]
ResponseMode = Literal["text", "text_and_sticker", "sticker_only"]


class MediaContent(BaseModel):
    """Normalized media extracted from an incoming message (spec §4)."""

    media_id: str = ""
    media_type: MediaType = "unknown"
    source_type: SourceType = "qq_image"
    file: str = ""
    url: str = ""
    mime_type: str = ""
    file_size: int = 0
    sha256: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    is_animated: bool = False
    # QQ market-emoji metadata (kept for mface reuse, spec §31)
    emoji_id: str = ""
    emoji_package_id: str = ""
    emoji_key: str = ""
    emoji_summary: str = ""
    # face id (native QQ emoji)
    face_id: int = 0
    # provenance (audit only, never exposed to other users)
    source_message_id: str = ""
    source_user_id: str = ""
    source_group_id: str = ""
    created_at: int = 0

    @property
    def is_sticker_source(self) -> bool:
        """Whether this media may legitimately enter the Sticker Library."""
        return self.media_type == "sticker" or self.source_type == "manual_import"


class VisionResult(BaseModel):
    """Structured image understanding (spec §5.1) — no hidden chain-of-thought."""

    image_id: str = ""
    summary: str = ""
    objects: list[str] = Field(default_factory=list)
    scene: str = ""
    ocr_text: list[str] = Field(default_factory=list)
    people_count: int = 0
    actions: list[str] = Field(default_factory=list)
    mood: str = ""
    visual_tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    def as_text(self) -> str:
        """A compact human-readable description for the character context."""
        bits: list[str] = []
        if self.summary:
            bits.append(self.summary)
        if self.scene:
            bits.append(f"场景：{self.scene}")
        if self.objects:
            bits.append("包含：" + "、".join(self.objects[:6]))
        if self.ocr_text:
            bits.append("文字：" + " ".join(self.ocr_text[:4]))
        if self.people_count:
            bits.append(f"约 {self.people_count} 人")
        if self.actions:
            bits.append("动作：" + "、".join(self.actions[:4]))
        if self.mood:
            bits.append(f"氛围：{self.mood}")
        return "；".join(bits) or "（未识别出内容）"


class StickerAsset(BaseModel):
    """One sticker in the character's library (spec §8/§52: an asset, not Memory)."""

    id: str = ""
    file_path: str = ""
    file_name: str = ""
    mime_type: str = ""
    file_size: int = 0
    sha256: str = ""
    phash: str = ""
    width: int = 0
    height: int = 0
    is_animated: bool = False

    origin: str = "manual_import"
    origin_user_id: str = ""
    origin_group_id: str = ""
    origin_message_id: str = ""

    emoji_id: str = ""
    emoji_package_id: str = ""
    emoji_key: str = ""

    visual_summary: str = ""
    ocr_text: str = ""
    emotion_tags: list[str] = Field(default_factory=list)
    intent_tags: list[str] = Field(default_factory=list)
    scene_tags: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    general_tags: list[str] = Field(default_factory=list)

    intensity: float = 0.0
    humor: float = 0.0
    expressiveness: float = 0.0
    reusability: float = 0.0
    novelty: float = 0.0
    quality_score: float = 0.0
    safety_status: str = "ok"

    analysis_version: str = ""
    analysis_model: str = ""
    analyzed_at: int = 0
    embedding_status: str = "none"

    usage_count: int = 0
    last_used_at: int = 0
    last_selected_at: int = 0

    created_at: int = 0
    updated_at: int = 0
    status: StickerStatus = "active"

    @property
    def all_tags(self) -> list[str]:
        tags: list[str] = []
        for group in (
            self.emotion_tags,
            self.intent_tags,
            self.scene_tags,
            self.style_tags,
            self.general_tags,
        ):
            tags.extend(group)
        # de-dupe, preserve order
        seen: set[str] = set()
        out: list[str] = []
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
        return out


class AcquisitionDecision(BaseModel):
    """Should the character save this sticker? (spec §10/§70, independent of use)"""

    decision: AcquisitionDecisionKind = "reject"
    confidence: float = 0.0
    novelty: float = 0.0
    reusability: float = 0.0
    expressiveness: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)


class ExpressionDecision(BaseModel):
    """Should the character use a sticker right now? (spec §24/§70, independent of save)"""

    response_mode: ResponseMode = "text"
    sticker_intent: str = ""
    emotion: str = ""
    intensity: float = 0.0
    selection_required: bool = False


class NativeFace(BaseModel):
    """A QQ native emoji the character may use (spec §30)."""

    face_id: int
    display_name: str = ""
    emotion: str = ""
    intent: str = ""
    tags: list[str] = Field(default_factory=list)


class ExpressionContext(BaseModel):
    """Expression-relevant context exposed to Social/Character layers (spec §27/§54)."""

    incoming_media_type: str = ""
    incoming_sticker_semantics: str = ""
    image_context: str = ""
    expression_opportunity: float = 0.0
    # v1.2 §90: character affect feeds the expression choice (e.g. high
    # turn-level amusement suggests a cheerful sticker even for flat text).
    emotion_hint: str = ""
