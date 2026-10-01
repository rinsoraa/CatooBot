"""SQLite persistence (WAL mode) with async-safe access.

Schema evolves through ordered, versioned migrations recorded in
``schema_migrations`` — existing databases are upgraded in place, never
dropped or recreated (see the migration list at the bottom of this module).
Blocking sqlite3 calls run in a worker thread via ``asyncio.to_thread``;
failures are logged by callers and never crash the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT, DatabaseConfig

_MIGRATIONS: list[tuple[int, str, str]] = [
    # (version, name, script)
    (
        1,
        "base schema",
        """
CREATE TABLE IF NOT EXISTS users (
    user_id   TEXT PRIMARY KEY,
    nickname  TEXT,
    last_seen INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   TEXT PRIMARY KEY,
    name       TEXT,
    last_seen  INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_session
    ON conversations(session_id, id);
""",
    ),
    (
        2,
        "character runtime: personas, states, profiles, relationships, memories",
        """
CREATE TABLE IF NOT EXISTS personas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    data       TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS character_states (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data       TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id           TEXT PRIMARY KEY,
    nickname_override TEXT,
    notes             TEXT DEFAULT '',
    tags              TEXT DEFAULT '[]',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    first_seen        INTEGER,
    last_seen         INTEGER
);

CREATE TABLE IF NOT EXISTS group_profiles (
    group_id          TEXT PRIMARY KEY,
    notes             TEXT DEFAULT '',
    tags              TEXT DEFAULT '[]',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    first_seen        INTEGER,
    last_seen         INTEGER
);

CREATE TABLE IF NOT EXISTS relationships (
    user_id           TEXT PRIMARY KEY,
    stage             TEXT NOT NULL DEFAULT 'new',
    preferred_tone    TEXT DEFAULT '',
    notes             TEXT DEFAULT '',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    first_seen        INTEGER,
    last_seen         INTEGER,
    updated_at        INTEGER
);

CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key    TEXT NOT NULL,
    user_id      TEXT,
    group_id     TEXT,
    category     TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    importance   REAL NOT NULL DEFAULT 0.5,
    confidence   REAL NOT NULL DEFAULT 0.7,
    use_count    INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    last_used_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_key, id);
CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
""",
    ),
    (
        3,
        "webui: admin users and runtime settings",
        """
CREATE TABLE IF NOT EXISTS web_users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
""",
    ),
    (
        4,
        "behavior engine: topics, behavior events, initiative state",
        """
CREATE TABLE IF NOT EXISTS topics (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key         TEXT NOT NULL,
    title             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    importance        REAL NOT NULL DEFAULT 0.5,
    participants      TEXT NOT NULL DEFAULT '[]',
    last_discussed_at INTEGER,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_scope ON topics(scope_key, status);

CREATE TABLE IF NOT EXISTS behavior_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    scope_key   TEXT,
    user_id     TEXT,
    group_id    TEXT,
    reason      TEXT DEFAULT '',
    detail      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'done',
    created_at  INTEGER NOT NULL,
    executed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_behavior_events_type
    ON behavior_events(type, created_at);

CREATE TABLE IF NOT EXISTS initiative_state (
    scope_key         TEXT PRIMARY KEY,
    last_sent_at      INTEGER,
    last_candidate_at INTEGER,
    unanswered_count  INTEGER NOT NULL DEFAULT 0,
    daily_count       INTEGER NOT NULL DEFAULT 0,
    daily_date        TEXT DEFAULT '',
    hourly_count      INTEGER NOT NULL DEFAULT 0,
    hourly_bucket     TEXT DEFAULT '',
    last_message      TEXT DEFAULT '',
    updated_at        INTEGER
);

ALTER TABLE user_profiles ADD COLUMN initiative_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE group_profiles ADD COLUMN participation_enabled INTEGER NOT NULL DEFAULT 1;
""",
    ),
    (
        5,
        "semantic memory: layers, status, embeddings, relations",
        """
ALTER TABLE memories ADD COLUMN layer TEXT NOT NULL DEFAULT 'semantic';
ALTER TABLE memories ADD COLUMN summary TEXT NOT NULL DEFAULT '';
ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'conversation';
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN supersedes_id INTEGER;
ALTER TABLE memories ADD COLUMN conflicts_with_id INTEGER;
ALTER TABLE memories ADD COLUMN valid_from INTEGER;
ALTER TABLE memories ADD COLUMN valid_until INTEGER;
ALTER TABLE memories ADD COLUMN event_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(scope_key, status);
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer, status);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id  INTEGER PRIMARY KEY,
    model      TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    vector     TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    dimensions   INTEGER NOT NULL,
    vector       TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (content_hash, model, dimensions)
);

CREATE TABLE IF NOT EXISTS memory_relations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL,
    to_id      INTEGER NOT NULL,
    relation   TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_relations_from
    ON memory_relations(from_id, relation);
""",
    ),
    (
        6,
        "tool runtime: configs, executions, cache, permissions",
        """
CREATE TABLE IF NOT EXISTS tool_configs (
    name       TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    timeout    REAL,
    config     TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id          TEXT NOT NULL,
    tool_name         TEXT NOT NULL,
    session_id        TEXT DEFAULT '',
    user_id           TEXT,
    group_id          TEXT,
    reason            TEXT DEFAULT '',
    arguments_hash    TEXT DEFAULT '',
    arguments_preview TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'ok',
    error_type        TEXT DEFAULT '',
    cache_hit         INTEGER NOT NULL DEFAULT 0,
    duration_ms       REAL NOT NULL DEFAULT 0,
    result_summary    TEXT DEFAULT '',
    created_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_executions_tool
    ON tool_executions(tool_name, created_at);

CREATE TABLE IF NOT EXISTS tool_cache (
    cache_key  TEXT PRIMARY KEY,
    tool_name  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_permissions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    ref        TEXT NOT NULL,
    tool_name  TEXT NOT NULL,
    allowed    INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_permissions_unique
    ON tool_permissions(scope, ref, tool_name);
""",
    ),
    (
        7,
        "agent runtime: goals, tasks, plans, steps, observations, traces",
        """
CREATE TABLE IF NOT EXISTS agent_goals (
    goal_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    user_id     TEXT,
    group_id    TEXT,
    description TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'multi_step',
    priority    INTEGER NOT NULL DEFAULT 5,
    constraints TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  INTEGER NOT NULL,
    deadline    INTEGER,
    completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_agent_goals_session
    ON agent_goals(session_id, status);

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id         TEXT PRIMARY KEY,
    goal_id         TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    user_id         TEXT,
    group_id        TEXT,
    classification  TEXT NOT NULL DEFAULT 'multi_step',
    status          TEXT NOT NULL DEFAULT 'created',
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    started_at      INTEGER,
    finished_at     INTEGER,
    duration_ms     REAL NOT NULL DEFAULT 0,
    step_count      INTEGER NOT NULL DEFAULT 0,
    completed_steps INTEGER NOT NULL DEFAULT 0,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    replans         INTEGER NOT NULL DEFAULT 0,
    result_status   TEXT DEFAULT '',
    result_summary  TEXT DEFAULT '',
    facts           TEXT NOT NULL DEFAULT '[]',
    sources         TEXT NOT NULL DEFAULT '[]',
    unresolved      TEXT NOT NULL DEFAULT '[]',
    error_type      TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
    ON agent_tasks(status, updated_at);

CREATE TABLE IF NOT EXISTS agent_plans (
    plan_id    TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    status     TEXT NOT NULL DEFAULT 'active',
    reason     TEXT DEFAULT '',
    summary    TEXT DEFAULT '',
    criteria   TEXT NOT NULL DEFAULT '[]',
    steps      TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_plans_task ON agent_plans(task_id, version);

CREATE TABLE IF NOT EXISTS agent_steps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    plan_id             TEXT NOT NULL,
    step_id             TEXT NOT NULL,
    description         TEXT DEFAULT '',
    tool_name           TEXT DEFAULT '',
    arguments_hash      TEXT DEFAULT '',
    depends_on          TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'pending',
    started_at          INTEGER,
    finished_at         INTEGER,
    duration_ms         REAL NOT NULL DEFAULT 0,
    observation_summary TEXT DEFAULT '',
    error_type          TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_task ON agent_steps(task_id, step_id);

CREATE TABLE IF NOT EXISTS agent_observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    step_id    TEXT NOT NULL,
    success    INTEGER NOT NULL DEFAULT 1,
    summary    TEXT DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}',
    source     TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_observations_task
    ON agent_observations(task_id, id);

CREATE TABLE IF NOT EXISTS agent_traces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'INFO',
    event      TEXT NOT NULL,
    detail     TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_task ON agent_traces(task_id, id);
""",
    ),
    (
        8,
        "persistent world: events/timeline, goals, snapshots",
        """
CREATE TABLE IF NOT EXISTS world_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    summary       TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '{}',
    source        TEXT NOT NULL DEFAULT 'world',
    importance    REAL NOT NULL DEFAULT 0.5,
    related_goal  TEXT,
    related_topic TEXT,
    session_id    TEXT,
    user_id       TEXT,
    surfaced_at   INTEGER,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_world_events_type
    ON world_events(type, created_at);

CREATE TABLE IF NOT EXISTS persistent_goals (
    goal_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'active',
    priority          INTEGER NOT NULL DEFAULT 5,
    progress          REAL NOT NULL DEFAULT 0.0,
    next_action       TEXT DEFAULT '',
    milestones        TEXT NOT NULL DEFAULT '[]',
    project_id        TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    last_progress_at  INTEGER,
    completed_at      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_persistent_goals_status
    ON persistent_goals(status, priority);

CREATE TABLE IF NOT EXISTS character_projects (
    project_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'idea',
    progress    REAL NOT NULL DEFAULT 0.0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS world_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at INTEGER NOT NULL,
    data        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_world_snapshots_at ON world_snapshots(snapshot_at DESC);
""",
    ),
    (
        9,
        "social cognition: observations",
        """
CREATE TABLE IF NOT EXISTS social_observations (
    observation_id TEXT PRIMARY KEY,
    group_id      TEXT NOT NULL,
    message_range TEXT NOT NULL DEFAULT '',
    topic         TEXT NOT NULL DEFAULT '',
    decision      TEXT NOT NULL DEFAULT 'ignore',
    reason_code   TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.0,
    created_at    INTEGER NOT NULL,
    detail        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_social_observations_group
    ON social_observations(group_id, created_at DESC);
""",
    ),
    (
        10,
        "world activity: episodes",
        """
CREATE TABLE IF NOT EXISTS activity_episodes (
    id                 TEXT PRIMARY KEY,
    activity_key       TEXT NOT NULL,
    activity_label     TEXT NOT NULL,
    activity_detail    TEXT NOT NULL DEFAULT '',
    location           TEXT NOT NULL DEFAULT '',
    social_state       TEXT NOT NULL DEFAULT 'alone',
    tags               TEXT NOT NULL DEFAULT '[]',
    started_at         REAL NOT NULL,
    planned_end_at     REAL NOT NULL,
    ended_at           REAL,
    status             TEXT NOT NULL DEFAULT 'active',
    source             TEXT NOT NULL DEFAULT 'routine',
    transition_reason  TEXT NOT NULL DEFAULT '',
    parent_episode_id  TEXT NOT NULL DEFAULT '',
    extension_count    INTEGER NOT NULL DEFAULT 0,
    ambient_count      INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_episodes_time
    ON activity_episodes(started_at DESC);
""",
    ),
    (
        11,
        "media + sticker runtime",
        """
CREATE TABLE IF NOT EXISTS image_analysis (
    sha256     TEXT PRIMARY KEY,
    data       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'ok',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sticker_assets (
    id                 TEXT PRIMARY KEY,
    file_path          TEXT NOT NULL DEFAULT '',
    file_name          TEXT NOT NULL DEFAULT '',
    mime_type          TEXT NOT NULL DEFAULT '',
    file_size          INTEGER NOT NULL DEFAULT 0,
    sha256             TEXT NOT NULL DEFAULT '',
    phash              TEXT NOT NULL DEFAULT '',
    width              INTEGER NOT NULL DEFAULT 0,
    height             INTEGER NOT NULL DEFAULT 0,
    is_animated        INTEGER NOT NULL DEFAULT 0,
    origin             TEXT NOT NULL DEFAULT 'manual_import',
    origin_user_id     TEXT NOT NULL DEFAULT '',
    origin_group_id    TEXT NOT NULL DEFAULT '',
    origin_message_id  TEXT NOT NULL DEFAULT '',
    emoji_id           TEXT NOT NULL DEFAULT '',
    emoji_package_id   TEXT NOT NULL DEFAULT '',
    emoji_key          TEXT NOT NULL DEFAULT '',
    visual_summary     TEXT NOT NULL DEFAULT '',
    ocr_text           TEXT NOT NULL DEFAULT '',
    emotion_tags       TEXT NOT NULL DEFAULT '[]',
    intent_tags        TEXT NOT NULL DEFAULT '[]',
    scene_tags         TEXT NOT NULL DEFAULT '[]',
    style_tags         TEXT NOT NULL DEFAULT '[]',
    general_tags       TEXT NOT NULL DEFAULT '[]',
    intensity          REAL NOT NULL DEFAULT 0.0,
    humor              REAL NOT NULL DEFAULT 0.0,
    expressiveness     REAL NOT NULL DEFAULT 0.0,
    reusability        REAL NOT NULL DEFAULT 0.0,
    novelty            REAL NOT NULL DEFAULT 0.0,
    quality_score      REAL NOT NULL DEFAULT 0.0,
    safety_status      TEXT NOT NULL DEFAULT 'ok',
    analysis_version   TEXT NOT NULL DEFAULT '',
    analysis_model     TEXT NOT NULL DEFAULT '',
    analyzed_at        INTEGER NOT NULL DEFAULT 0,
    embedding_status   TEXT NOT NULL DEFAULT 'none',
    usage_count        INTEGER NOT NULL DEFAULT 0,
    last_used_at       INTEGER NOT NULL DEFAULT 0,
    last_selected_at   INTEGER NOT NULL DEFAULT 0,
    created_at         INTEGER NOT NULL DEFAULT 0,
    updated_at         INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_sticker_assets_sha256 ON sticker_assets(sha256);
CREATE INDEX IF NOT EXISTS idx_sticker_assets_status ON sticker_assets(status);

CREATE TABLE IF NOT EXISTS sticker_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sticker_id    TEXT NOT NULL,
    scope_key     TEXT NOT NULL DEFAULT '',
    success       INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sticker_usage_sticker ON sticker_usage(sticker_id, created_at DESC);

CREATE TABLE IF NOT EXISTS native_faces (
    face_id      INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    emotion      TEXT NOT NULL DEFAULT '',
    intent       TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]'
);
""",
    ),
    (
        12,
        "conversation turns + character continuity",
        """
CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id         TEXT PRIMARY KEY,
    generation_id   INTEGER NOT NULL DEFAULT 0,
    session_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    group_id        TEXT NOT NULL DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'single',
    status          TEXT NOT NULL DEFAULT 'open',
    text            TEXT NOT NULL DEFAULT '',
    silence_reason  TEXT NOT NULL DEFAULT '',
    message_ids     TEXT NOT NULL DEFAULT '[]',
    started_at      REAL NOT NULL,
    ended_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_session
    ON conversation_turns(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS open_loops (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'plan',
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    scope_key   TEXT NOT NULL DEFAULT '',
    progress    REAL NOT NULL DEFAULT 0.0,
    source      TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 0.5,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    expires_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_open_loops_status ON open_loops(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS shared_experiences (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    type              TEXT NOT NULL DEFAULT 'shared_event',
    summary           TEXT NOT NULL,
    detail            TEXT NOT NULL DEFAULT '',
    keywords          TEXT NOT NULL DEFAULT '[]',
    times_referenced  INTEGER NOT NULL DEFAULT 0,
    confidence        REAL NOT NULL DEFAULT 0.5,
    source            TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shared_experiences_user
    ON shared_experiences(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS interaction_profiles (
    user_id          TEXT PRIMARY KEY,
    patterns         TEXT NOT NULL DEFAULT '{}',
    favorite_topics  TEXT NOT NULL DEFAULT '[]',
    updated_at       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS micro_events (
    id                TEXT PRIMARY KEY,
    summary           TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'ambient',
    related_activity  TEXT NOT NULL DEFAULT '',
    reason_code       TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_micro_events_time ON micro_events(created_at DESC);

CREATE TABLE IF NOT EXISTS affective_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension    TEXT NOT NULL,
    delta        REAL NOT NULL DEFAULT 0.0,
    reason_code  TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_affective_events_time ON affective_events(created_at DESC);
""",
    ),
    (
        13,
        "character life sandbox",
        """
CREATE TABLE IF NOT EXISTS sandbox_entities (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'object',
    name        TEXT NOT NULL DEFAULT '',
    space_id    TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_spaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    parent_id   TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'room',
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_objects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    space_id    TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'object',
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_inventories (
    key         TEXT PRIMARY KEY,
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_actions (
    id            TEXT PRIMARY KEY,
    definition_id TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    started_at    REAL NOT NULL DEFAULT 0,
    ended_at      REAL NOT NULL DEFAULT 0,
    data          TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sandbox_actions_status
    ON sandbox_actions(status, started_at DESC);

CREATE TABLE IF NOT EXISTS sandbox_events (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'micro',
    priority     TEXT NOT NULL DEFAULT 'normal',
    source       TEXT NOT NULL DEFAULT 'system',
    summary      TEXT NOT NULL DEFAULT '',
    reason_code  TEXT NOT NULL DEFAULT '',
    data         TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sandbox_events_time ON sandbox_events(created_at DESC);

CREATE TABLE IF NOT EXISTS sandbox_traces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'tick',
    summary      TEXT NOT NULL DEFAULT '',
    factors      TEXT NOT NULL DEFAULT '[]',
    reason_code  TEXT NOT NULL DEFAULT '',
    data         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sandbox_traces_time ON sandbox_traces(ts DESC);

CREATE TABLE IF NOT EXISTS sandbox_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   REAL NOT NULL,
    elapsed_min  REAL NOT NULL DEFAULT 0,
    data         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sandbox_commissions (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'mc_build',
    status       TEXT NOT NULL DEFAULT 'open',
    progress     REAL NOT NULL DEFAULT 0,
    deadline     REAL,
    reward       REAL NOT NULL DEFAULT 0,
    data         TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_social_spaces (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'simulated',
    data         TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_knowledge (
    key          TEXT PRIMARY KEY,
    known        INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT '',
    learned_at   REAL NOT NULL DEFAULT 0,
    data         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sandbox_state (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL DEFAULT '',
    updated_at   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS character_bible (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    version      TEXT NOT NULL DEFAULT '',
    source_hash  TEXT NOT NULL DEFAULT '',
    compiled     TEXT NOT NULL DEFAULT '{}',
    coverage     TEXT NOT NULL DEFAULT '{}',
    report       TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL
);
""",
    ),
    (
        14,
        "drop v1.x world tables",
        """
-- The persistent world (v0.8) was deleted wholesale in v2.0; the character
-- life sandbox replaced it. These tables lost their only writer back then and
-- nothing has read them since, so they are dropped instead of lingering as
-- empty schema. Fresh databases still create them in migration 10 and drop
-- them here — historical migrations are never rewritten.
DROP TABLE IF EXISTS world_events;
DROP TABLE IF EXISTS persistent_goals;
DROP TABLE IF EXISTS character_projects;
DROP TABLE IF EXISTS world_snapshots;
DROP TABLE IF EXISTS activity_episodes;
""",
    ),
    (
        15,
        "memory keyword index (fts5)",
        """
-- Keyword candidates used to be picked by scanning the (importance-capped)
-- candidate list in Python, so a perfect match could be invisible. The index
-- searches the whole scope. search_text holds the same tokens
-- app.memory.keyword_index.bigrams() produces (CJK chars + CJK bigrams + ascii
-- words), which is what makes short Chinese queries match; legacy rows are
-- backfilled by KeywordIndex.ensure_ready() at startup.
ALTER TABLE memories ADD COLUMN search_text TEXT NOT NULL DEFAULT '';

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    search_text,
    content='memories',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, search_text)
    VALUES ('delete', old.id, old.search_text);
END;

-- Only reindex when the indexed text changes (use_count/status writes are hot).
CREATE TRIGGER IF NOT EXISTS memories_fts_au
AFTER UPDATE OF content, summary, search_text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, search_text)
    VALUES ('delete', old.id, old.search_text);
    INSERT INTO memories_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;
""",
    ),
    (
        16,
        "vector blobs + precomputed norms",
        """
-- Vectors lived as JSON text and every search parsed them and recomputed two
-- norms in Python. The blob holds the same float64 values (bit-identical to
-- json.loads) and `norm` makes scoring a single dot product.
-- Additive on purpose: the read path prefers the blob and falls back to the
-- JSON column, which is only dropped in a follow-up migration once the
-- backfill has run on real data.
ALTER TABLE memory_embeddings ADD COLUMN vector_blob BLOB;
ALTER TABLE memory_embeddings ADD COLUMN norm REAL;
""",
    ),
    (
        17,
        "model usage log",
        """
-- One row per provider attempt (the router records failures too), so the model
-- page can show where tokens go, which model got slower and what is failing.
-- Pruned by ai.usage.retention_days on the shared scheduler.
CREATE TABLE IF NOT EXISTS ai_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                INTEGER NOT NULL,
    provider          TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    purpose           TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    latency_ms        REAL NOT NULL DEFAULT 0,
    attempt           INTEGER NOT NULL DEFAULT 1,
    ok                INTEGER NOT NULL DEFAULT 1,
    error_type        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_ts ON ai_usage(ts DESC);
CREATE INDEX IF NOT EXISTS idx_ai_usage_model ON ai_usage(model, ts DESC);
""",
    ),
    (
        18,
        "reply outcomes (task 20)",
        """
-- What happened *after* she spoke: one row per turn (not per bubble; a turn is
-- 2-3 bubbles and MessageDelivery keeps only the last id per scope), settled by
-- a scheduled sweep once the observation window has passed. verdict stays
-- 'pending' until then; 'unknown' is a first-class answer (restart, private
-- chat, too few samples) so silence is never assumed.
CREATE TABLE IF NOT EXISTS reply_outcomes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id           TEXT NOT NULL,
    scope_key         TEXT NOT NULL,
    is_group          INTEGER NOT NULL DEFAULT 1,
    reason_code       TEXT NOT NULL DEFAULT '',
    self_initiated    INTEGER NOT NULL DEFAULT 0,
    sent_at           REAL NOT NULL,
    window_seconds    REAL NOT NULL DEFAULT 90,
    replies           INTEGER NOT NULL DEFAULT 0,
    first_reply_after REAL,
    addressed_back    INTEGER,
    polarity          INTEGER,
    baseline          INTEGER,
    verdict           TEXT NOT NULL DEFAULT 'pending',
    settled_at        REAL,
    note              TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_outcomes_turn ON reply_outcomes(turn_id);
CREATE INDEX IF NOT EXISTS idx_reply_outcomes_scope ON reply_outcomes(scope_key, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_reply_outcomes_verdict ON reply_outcomes(verdict, sent_at DESC);
""",
    ),
    (
        19,
        "expression / 口癖 learning (task 22)",
        """
-- Patterns she learned from how a group talks: one row per (group, pattern).
-- status: active (injected) | disabled (user paused) | archived (evicted).
CREATE TABLE IF NOT EXISTS expression_patterns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key     TEXT NOT NULL,                -- group:<gid>
    pattern       TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'word', -- word | pattern
    sample_count  INTEGER NOT NULL DEFAULT 0,
    speaker_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at INTEGER,
    last_seen_at  INTEGER,
    last_used_at  INTEGER,
    use_count     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_expr_pattern_scope
    ON expression_patterns(scope_key, pattern);
CREATE INDEX IF NOT EXISTS idx_expr_pattern_scope_status
    ON expression_patterns(scope_key, status);

-- Provenance: the raw (redacted) messages a pattern came from, for the WebUI.
CREATE TABLE IF NOT EXISTS expression_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    text       TEXT NOT NULL,
    seen_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_expr_sample_unique
    ON expression_samples(pattern_id, message_id);
CREATE INDEX IF NOT EXISTS idx_expr_sample_pattern
    ON expression_samples(pattern_id);

-- Vector search, same shape as memory_embeddings (blob + norm, no JSON column).
CREATE TABLE IF NOT EXISTS expression_vectors (
    pattern_id  INTEGER PRIMARY KEY,
    model       TEXT NOT NULL,
    dimensions  INTEGER NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    vector_blob BLOB,
    norm        REAL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
""",
    ),
]


class Database:
    """Small async facade over a single SQLite connection."""

    def __init__(self, config: DatabaseConfig, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._log = logger or logging.getLogger("CatooBot.Database")
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        path = self._config.sqlite_path
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open, path)
        applied = await asyncio.to_thread(self._migrate, self._conn)
        if applied:
            self._log.info("Applied %d database migration(s): %s", len(applied), ", ".join(applied))
        self._log.info("SQLite connected: %s", path)

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> list[str]:
        """Apply pending migrations in order, recording versions applied."""
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT, applied_at INTEGER)"
        )
        applied: list[str] = []
        existing = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, script in _MIGRATIONS:
            if version in existing:
                continue
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations"
                " (version, name, applied_at) VALUES (?, ?, strftime('%s','now'))",
                (version, name),
            )
            conn.commit()
            applied.append(f"v{version}:{name}")
        return applied

    async def close(self) -> None:
        # Serialise with in-flight queries: closing a connection that another
        # to_thread call is still using segfaults sqlite3 on Windows.
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None
                self._log.info("SQLite connection closed")

    # --------------------------------------------------------------- helpers

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: tuple[Any, ...]) -> None:
        conn = self._require_conn()
        conn.execute(sql, params)
        conn.commit()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self._lock:
            row = await asyncio.to_thread(self._fetchone_sync, sql, params)
        return dict(row) if row is not None else None

    def _fetchone_sync(self, sql: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        return self._require_conn().execute(sql, params).fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await asyncio.to_thread(self._fetchall_sync, sql, params)
        return [dict(row) for row in rows]

    def _fetchall_sync(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        return self._require_conn().execute(sql, params).fetchall()

    async def run_in_transaction(self, fn: Any) -> Any:
        """Run ``fn(conn)`` as one SQLite transaction (rollback on error).

        ``fn`` receives the raw ``sqlite3.Connection`` and runs synchronously in
        a worker thread; the whole body commits once, or rolls back entirely if
        it raises. The lock is held for the duration so no other write can
        interleave into the transaction.
        """
        async with self._lock:

            def _run() -> Any:
                conn = self._require_conn()
                conn.execute("BEGIN")
                try:
                    result = fn(conn)
                    conn.commit()
                    return result
                except BaseException:
                    conn.rollback()
                    raise

            return await asyncio.to_thread(_run)

    # ------------------------------------------------------------ high level

    async def upsert_user(self, user_id: int | str, nickname: str | None, last_seen: int) -> None:
        await self.execute(
            """INSERT INTO users (user_id, nickname, last_seen, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   nickname=excluded.nickname, last_seen=excluded.last_seen,
                   updated_at=excluded.updated_at""",
            (str(user_id), nickname, last_seen, last_seen),
        )

    async def upsert_group(self, group_id: int | str, name: str | None, last_seen: int) -> None:
        await self.execute(
            """INSERT INTO groups (group_id, name, last_seen, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(group_id) DO UPDATE SET
                   name=COALESCE(excluded.name, groups.name), last_seen=excluded.last_seen,
                   updated_at=excluded.updated_at""",
            (str(group_id), name, last_seen, last_seen),
        )

    async def get_state(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM bot_state WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_state(self, key: str, value: str, updated_at: int) -> None:
        await self.execute(
            """INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, updated_at),
        )

    async def get_setting_json(self, key: str) -> Any | None:
        """Read a JSON-serialized runtime setting; None when absent/invalid."""
        import json

        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except ValueError:
            self._log.warning("Setting '%s' holds invalid JSON, ignored", key)
            return None

    async def set_setting_json(self, key: str, value: Any, updated_at: int) -> None:
        import json

        await self.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value, ensure_ascii=False), updated_at),
        )
