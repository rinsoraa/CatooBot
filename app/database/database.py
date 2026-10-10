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
    (
        20,
        "drop the memory_embeddings JSON column (task 12 unfreeze)",
        """
-- The float64 blob + precomputed norm have been the read path since migration
-- 16, with the JSON column kept only as a fallback until the backfill ran on
-- real data. It has: every row now carries a blob (zero fallback reads), and
-- the blob path has been live without a retrieval regression. Drop the JSON
-- column — the blob is the single source of truth for vectors now.
ALTER TABLE memory_embeddings DROP COLUMN vector;
""",
    ),
    (
        21,
        "sticker asset scope: character-acquired vs global (v2.1 §44-§46)",
        """
-- The data-boundary refactor needs to tell "a sticker the character acquired
-- from chat" apart from platform-level assets, so a character reset can drop
-- the former and keep the latter. Existing user-message acquisitions are
-- character data; manual imports stay global.
ALTER TABLE sticker_assets ADD COLUMN scope TEXT NOT NULL DEFAULT 'global';
UPDATE sticker_assets SET scope = 'character' WHERE origin = 'user_message';
CREATE INDEX IF NOT EXISTS idx_sticker_assets_scope ON sticker_assets(scope);
""",
    ),
    (
        22,
        "sandbox experience → memory foundation (Phase 4)",
        """
-- Experiences: what the character actually lived through, derived from
-- sandbox events (never LLM-generated). Provenance root for sandbox memories.
CREATE TABLE IF NOT EXISTS sandbox_experiences (
    id                TEXT PRIMARY KEY,
    character_id      TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL DEFAULT 'world_note',
    summary           TEXT NOT NULL DEFAULT '',
    importance        REAL NOT NULL DEFAULT 0.3,
    location          TEXT NOT NULL DEFAULT '',
    actors            TEXT NOT NULL DEFAULT '[]',
    source_event_ids  TEXT NOT NULL DEFAULT '[]',
    causation_id      TEXT NOT NULL DEFAULT '',
    correlation_id    TEXT NOT NULL DEFAULT '',
    action_id         TEXT NOT NULL DEFAULT '',
    interaction_type  TEXT NOT NULL DEFAULT '',
    external_source   TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    created_at        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sandbox_experiences_char
    ON sandbox_experiences(character_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sandbox_experiences_corr
    ON sandbox_experiences(correlation_id);

-- Memory candidates: the pre-promotion form (§16). Only validated, deduped
-- candidates become long-term memories; the rest stay auditable here.
CREATE TABLE IF NOT EXISTS sandbox_memory_candidates (
    candidate_id         TEXT PRIMARY KEY,
    character_id         TEXT NOT NULL DEFAULT '',
    memory_type          TEXT NOT NULL DEFAULT 'episodic',
    summary              TEXT NOT NULL DEFAULT '',
    content              TEXT NOT NULL DEFAULT '',
    scope                TEXT NOT NULL DEFAULT 'self',
    importance           REAL NOT NULL DEFAULT 0.0,
    confidence           REAL NOT NULL DEFAULT 0.0,
    status               TEXT NOT NULL DEFAULT 'candidate',
    dedupe_key           TEXT NOT NULL DEFAULT '',
    source_experience_ids TEXT NOT NULL DEFAULT '[]',
    source_event_ids     TEXT NOT NULL DEFAULT '[]',
    memory_id            INTEGER,
    reason_code          TEXT NOT NULL DEFAULT '',
    created_at           REAL NOT NULL DEFAULT 0,
    observed_at          REAL NOT NULL DEFAULT 0,
    decided_at           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sandbox_candidates_char
    ON sandbox_memory_candidates(character_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sandbox_candidates_dedupe
    ON sandbox_memory_candidates(character_id, dedupe_key);

-- The existing memories table becomes character-aware (§11): old rows keep
-- an empty character_id (conversation-era memories, still valid), sandbox
-- memories carry the owner id so two characters sharing one store cannot
-- read each other's rows.
ALTER TABLE memories ADD COLUMN character_id TEXT NOT NULL DEFAULT '';
ALTER TABLE memories ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}';
ALTER TABLE memories ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_memories_character
    ON memories(character_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_dedupe
    ON memories(character_id, dedupe_key);
""",
    ),
    (
        23,
        "sandbox goals + goal steps (Phase 7)",
        """
-- Persistent intentions: why the character keeps doing something even when
-- nothing external happened. One row per goal; only the *current* step is
-- materialized (the layer re-plans instead of storing whole plan trees).
CREATE TABLE IF NOT EXISTS sandbox_goals (
    goal_id          TEXT PRIMARY KEY,
    character_id     TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'restock_resource',
    status           TEXT NOT NULL DEFAULT 'pending',
    priority         REAL NOT NULL DEFAULT 0.5,
    reason           TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'unfinished_task',
    source_event_id  TEXT NOT NULL DEFAULT '',
    causation_id     TEXT NOT NULL DEFAULT '',
    correlation_id   TEXT NOT NULL DEFAULT '',
    target_entity    TEXT NOT NULL DEFAULT '',
    target_space     TEXT NOT NULL DEFAULT '',
    target_item      TEXT NOT NULL DEFAULT '',
    target_project   TEXT NOT NULL DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}',
    progress         REAL NOT NULL DEFAULT 0.0,
    current_step     TEXT NOT NULL DEFAULT '{}',
    retry_count      INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  REAL NOT NULL DEFAULT 0,
    next_eligible_at REAL NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sandbox_goals_char_status
    ON sandbox_goals(character_id, status);
CREATE INDEX IF NOT EXISTS idx_sandbox_goals_char_kind_target
    ON sandbox_goals(character_id, kind, target_item);
CREATE INDEX IF NOT EXISTS idx_sandbox_goals_eligible
    ON sandbox_goals(character_id, next_eligible_at);

CREATE TABLE IF NOT EXISTS sandbox_goal_steps (
    step_id      TEXT PRIMARY KEY,
    goal_id      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'action',
    status       TEXT NOT NULL DEFAULT 'pending',
    action_id    TEXT NOT NULL DEFAULT '',
    target       TEXT NOT NULL DEFAULT '',
    step_order   INTEGER NOT NULL DEFAULT 0,
    requirements TEXT NOT NULL DEFAULT '[]',
    result       TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sandbox_goal_steps_goal
    ON sandbox_goal_steps(goal_id, step_order);
""",
    ),
    (
        24,
        "social & relationship dynamics (Phase 8)",
        """
-- Person identities: a person is not a QQ id (§7). external_ids maps platform
-- handles onto one stable person across QQ / future surfaces. Platform-level
-- data: shared by every character, never stores relationship opinions.
CREATE TABLE IF NOT EXISTS sandbox_persons (
    person_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    external_ids TEXT NOT NULL DEFAULT '{}',
    source       TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL DEFAULT 0
);

-- Dynamic relationship state, scoped per character (§6): the same person in
-- two worlds is two different relationships. Rows are only ever written by the
-- deterministic update engine from verified interaction facts (§10/§14).
CREATE TABLE IF NOT EXISTS sandbox_relationships (
    character_id TEXT NOT NULL,
    person_id    TEXT NOT NULL,
    data         TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (character_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_sandbox_relationships_char_updated
    ON sandbox_relationships(character_id, updated_at DESC);
""",
    ),
    (
        25,
        "social commitment & obligation (Phase 9)",
        """
-- What she told someone she would do (§5/§28). Character-scoped: the same
-- person in two worlds owes two different things (§6). Rows are only written
-- by the CommitmentManager from verified facts, never from memory or a model.
CREATE TABLE IF NOT EXISTS sandbox_commitments (
    commitment_id        TEXT PRIMARY KEY,
    character_id         TEXT NOT NULL DEFAULT '',
    person_id            TEXT NOT NULL DEFAULT '',
    kind                 TEXT NOT NULL DEFAULT 'shared_activity',
    status               TEXT NOT NULL DEFAULT 'pending',
    strength             TEXT NOT NULL DEFAULT 'explicit',
    description          TEXT NOT NULL DEFAULT '',
    revision             INTEGER NOT NULL DEFAULT 0,
    priority             REAL NOT NULL DEFAULT 0.5,
    target_action        TEXT NOT NULL DEFAULT '',
    target_activity      TEXT NOT NULL DEFAULT '',
    time_hint            TEXT NOT NULL DEFAULT '',
    earliest_at          REAL NOT NULL DEFAULT 0,
    latest_at            REAL NOT NULL DEFAULT 0,
    due_at               REAL NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL DEFAULT 0,
    updated_at           REAL NOT NULL DEFAULT 0,
    activated_at         REAL NOT NULL DEFAULT 0,
    resolved_at          REAL NOT NULL DEFAULT 0,
    source_interaction_id TEXT NOT NULL DEFAULT '',
    source_event_id      TEXT NOT NULL DEFAULT '',
    correlation_id       TEXT NOT NULL DEFAULT '',
    causation_id         TEXT NOT NULL DEFAULT '',
    metadata             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sandbox_commitments_char_status
    ON sandbox_commitments(character_id, status);
CREATE INDEX IF NOT EXISTS idx_sandbox_commitments_char_person
    ON sandbox_commitments(character_id, person_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_commitments_char_due
    ON sandbox_commitments(character_id, due_at);
CREATE INDEX IF NOT EXISTS idx_sandbox_commitments_char_status_person
    ON sandbox_commitments(character_id, status, person_id);
""",
    ),
    (
        26,
        "persistent episode identity (Phase 10.1)",
        """
-- One real behaviour = one persisted episode (§10.1 §2/§4). The key is derived
-- from the episode identity the runtime already uses (ActionInstance >
-- commitment > interaction fact) so a restart can no longer mint a second row
-- for the same act. Historical rows keep NULL — SQLite treats NULLs as
-- distinct, so they are never deduped against each other.
ALTER TABLE sandbox_experiences ADD COLUMN episode_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sandbox_experiences_episode
    ON sandbox_experiences(character_id, episode_key);
""",
    ),
    (
        27,
        "multi-step task runtime (Phase 5A)",
        """
-- 一个 Task = 一行（当前完整的 checkpoint）+ 一条 append-only 的转移日志。
-- 复用**同一个** SQLite 库：Phase 5A 不建第二套存储；跨进程重启时 task 仍然读得回来，
-- 但正在跑的 Minecraft action 早已失效（见 TaskRuntime 的 runtime_restart 处理）。
CREATE TABLE IF NOT EXISTS agent_task_runs (
    task_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    origin        TEXT NOT NULL,
    state         TEXT NOT NULL,
    objective     TEXT NOT NULL,
    plan_hash     TEXT NOT NULL,
    current_step  INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    expires_at    REAL NOT NULL DEFAULT 0,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_task_runs_session
    ON agent_task_runs(session_id, state, updated_at DESC);

-- 每一次会改变 Task state 的转移都追加一行（§九：Task 必须有 checkpoint）。
CREATE TABLE IF NOT EXISTS agent_task_checkpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    state       TEXT NOT NULL,
    step_id     TEXT NOT NULL DEFAULT '',
    event       TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_task_checkpoints_task
    ON agent_task_checkpoints(task_id, id);
""",
    ),
    (
        28,
        "minecraft identity bridge (Phase 5C)",
        """
-- QQ（或其它平台）↔ Minecraft 玩家 的显式绑定。**player_uuid 才是身份**，
-- username 只是"最后一次确认时的显示名"（改名不换人，重名不认亲）。
-- 解除绑定只把状态改成 REVOKED —— 历史行永远保留（Phase 5C §八）。
CREATE TABLE IF NOT EXISTS minecraft_identity_links (
    link_id     TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    server_id   TEXT NOT NULL,
    player_uuid TEXT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    verified_at REAL NOT NULL DEFAULT 0,
    revoked_at  REAL NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT ''
);
-- §三十五：同一个玩家 UUID 只能有一条 VERIFIED 绑定（别人想绑 → 冲突并拒绝）
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_identity_uuid_verified
    ON minecraft_identity_links(server_id, player_uuid) WHERE status = 'VERIFIED';
-- 同一个平台用户在**同一台服务器**上也只能有一条 VERIFIED 绑定
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_identity_user_verified
    ON minecraft_identity_links(platform, user_id, server_id) WHERE status = 'VERIFIED';
CREATE INDEX IF NOT EXISTS idx_mc_identity_lookup
    ON minecraft_identity_links(platform, user_id, status);
""",
    ),
    (
        29,
        "world activity: episodes + transitions (Phase 6A)",
        """
-- "她此刻正在做什么"的**唯一事实来源**（Phase 6A §一/§三）。
-- v1.x 也有过一张 activity_episodes，但那张在 migration 14 跟着 v0.8 persistent world
-- 整体删掉了；这一张按 6A §二十五 重建，并且**只**负责活动生命周期
-- （真实世界动作仍然只能由 TaskRuntime → Policy → Confirmation → ActionRuntime 产生）。
CREATE TABLE IF NOT EXISTS activity_episodes (
    episode_id          TEXT PRIMARY KEY,
    character_id        TEXT NOT NULL,
    activity_type       TEXT NOT NULL,
    activity_name       TEXT NOT NULL,
    location            TEXT NOT NULL DEFAULT '',
    social_state        TEXT NOT NULL DEFAULT 'alone',
    tags                TEXT NOT NULL DEFAULT '[]',
    started_at          REAL NOT NULL DEFAULT 0,
    planned_end_at      REAL NOT NULL DEFAULT 0,
    ended_at            REAL NOT NULL DEFAULT 0,
    min_duration        REAL NOT NULL DEFAULT 0,
    typical_duration    REAL NOT NULL DEFAULT 0,
    max_duration        REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    transition_reason   TEXT NOT NULL DEFAULT '',
    source              TEXT NOT NULL,
    parent_episode_id   TEXT NOT NULL DEFAULT '',
    related_task_id     TEXT NOT NULL DEFAULT '',
    extension_count     INTEGER NOT NULL DEFAULT 0,
    observation         TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);
-- §十二/§二十六：每个角色同一时间**最多一个**未结束的 primary Episode。
-- 数据库层兜底（运行态还有 ActivityRuntime 的串行化 + CAS）：
-- 两个进程同时想创建 ACTIVE Episode 时，第二个会被这个 partial unique index 拒绝。
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_episodes_primary
    ON activity_episodes(character_id)
    WHERE status IN ('SCHEDULED', 'ACTIVE', 'EXTENDED');
CREATE INDEX IF NOT EXISTS idx_activity_episodes_character
    ON activity_episodes(character_id, created_at DESC);

-- 转移审计日志（§三十一/§五十五）：回答了"为什么开始 / 为什么结束 / 是否被打断"。
CREATE TABLE IF NOT EXISTS activity_transitions (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  TEXT NOT NULL,
    transition  TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_transitions_episode
    ON activity_transitions(episode_id, seq);
-- §三十三：同一个 Episode 的**一次性**转移只允许落一行 —— 重启/重试都不会再发一次
-- completed/interrupted/cancelled/expired/started 事件（EXTENDED 可以重复，故不在其中）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_transitions_once
    ON activity_transitions(episode_id, transition)
    WHERE transition IN ('ACTIVE', 'COMPLETED', 'INTERRUPTED', 'CANCELLED', 'EXPIRED');
""",
    ),
    (
        30,
        "world activity: plans + plan items (Phase 6C)",
        """
-- Phase 6C §四十五：Rolling Horizon 的**计划**存储。先查过现有库：v1.x 的 agent_plans 是
-- 工具执行规划、sandbox_goals 是目标层，都**不是**这个 —— 日程计划此前没有存储，故新建。
-- 语义边界（§四十六）：计划只是 intent，**不是** reality；现实永远只有 activity_episodes。
CREATE TABLE IF NOT EXISTS activity_plans (
    plan_id         TEXT PRIMARY KEY,
    character_id    TEXT NOT NULL,
    plan_version    INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ACTIVE_PLAN',
    generated_at    REAL NOT NULL DEFAULT 0,
    horizon_start   REAL NOT NULL DEFAULT 0,
    horizon_end     REAL NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT '',
    trigger         TEXT NOT NULL DEFAULT '',
    content_hash    TEXT NOT NULL DEFAULT '',
    superseded_by   TEXT NOT NULL DEFAULT '',
    constraints     TEXT NOT NULL DEFAULT '{}',
    candidates      TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
-- §四十四 + §十二（同一个角色同时最多一份生效计划）：数据库层兜住"两份 ACTIVE_PLAN"。
-- 旧计划不删除，只把 status 改成 SUPERSEDED（历史与审计永远保留）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_plans_active
    ON activity_plans(character_id)
    WHERE status = 'ACTIVE_PLAN';
CREATE INDEX IF NOT EXISTS idx_activity_plans_character
    ON activity_plans(character_id, created_at DESC);

-- 计划条目：一条"打算做的事"（§四）。它**不是** Episode，没有状态机、没有 id。
CREATE TABLE IF NOT EXISTS activity_plan_items (
    plan_id        TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    activity       TEXT NOT NULL,
    planned_start  REAL NOT NULL DEFAULT 0,
    planned_end    REAL NOT NULL DEFAULT 0,
    reason         TEXT NOT NULL DEFAULT '',
    priority       REAL NOT NULL DEFAULT 0,
    anchor_id      TEXT NOT NULL DEFAULT '',
    goal_id        TEXT NOT NULL DEFAULT '',
    score          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (plan_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_activity_plan_items_plan
    ON activity_plan_items(plan_id, sequence);
""",
    ),
    (
        31,
        "world initiative: life intents (Phase 7A)",
        """
-- Phase 7A §二十三/§六十六：LifeIntent 的最小存储。先查过现有库：
--   initiative_state —— 是**聊天**主动性的按 scope 计数器（last_sent_at / 未回复数），
--                       语义与"生活意图"不同，塞进来会把两件事混成一件；
--   behavior_events  —— 已经是 append-only 审计表，7A **复用它**记录意图历史（§二十四），
--                       所以这里只有这一张新表：没有第二张历史表、没有候选表。
-- 幂等（§四十六）：fingerprint = character_id|type|goal|activity|semantic_key|time bucket，
-- 唯一索引保证"同一条想法"在一个时间桶里只会有一行 —— 重启 / 崩溃重放都不会多出一条。
-- 注意：状态只有 PROPOSED/SUPPRESSED/EXPIRED/CANCELLED/RESOLVED ——
-- **没有** EXECUTING / RUNNING（LifeIntent != Task，§四/§八）。
CREATE TABLE IF NOT EXISTS life_intents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id          TEXT NOT NULL UNIQUE,
    character_id       TEXT NOT NULL,
    intent_type        TEXT NOT NULL,
    title              TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT 'SYSTEM',
    origin             TEXT NOT NULL DEFAULT '',
    priority           REAL NOT NULL DEFAULT 0.5,
    confidence         REAL NOT NULL DEFAULT 0.5,
    created_at         REAL NOT NULL DEFAULT 0,
    expires_at         REAL NOT NULL DEFAULT 0,
    related_activity   TEXT NOT NULL DEFAULT '',
    related_goal       TEXT NOT NULL DEFAULT '',
    related_memory     TEXT NOT NULL DEFAULT '',
    related_player     TEXT NOT NULL DEFAULT '',
    related_task       TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'PROPOSED',
    suppression_reason TEXT NOT NULL DEFAULT '',
    resolution_reason  TEXT NOT NULL DEFAULT '',
    fingerprint        TEXT NOT NULL,
    execution_class    TEXT NOT NULL DEFAULT 'VIRTUAL_ONLY',
    tags               TEXT NOT NULL DEFAULT '[]',
    updated_at         REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_life_intents_fingerprint
    ON life_intents(fingerprint);
CREATE INDEX IF NOT EXISTS idx_life_intents_character
    ON life_intents(character_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_life_intents_status
    ON life_intents(character_id, status);
-- 意图历史复用 behavior_events（§二十四）：这条索引给"按 scope 取本阶段的审计"用
CREATE INDEX IF NOT EXISTS idx_behavior_events_scope_type
    ON behavior_events(scope_key, type, id);
""",
    ),
    (
        32,
        "task proposals (Phase 7C)",
        """
-- Phase 7C §十：TaskProposal 的最小存储。先查过现有库：
--   tasks            —— 是**已进入执行系统**的任务（5A），7C 的提案**绝不**写它（§五）；
--   life_intents     —— 是"为什么想做"（7A），提案是"准备怎样做"，语义不同，不能塞；
--   behavior_events  —— append-only 审计表，7C **复用它**记录提案历史（proposal.* 前缀），
--                       所以只有这一张新表：没有第二张历史表、没有候选表、没有第二套 TaskRuntime。
-- 幂等（§十）：fingerprint = 来源|目标|目标键|意图|时间桶，唯一索引保证
-- "同一份提案"在一个时间桶里只会有一行 —— 重启 / 崩溃重放都不会多出一条。
-- 状态只有 REJECTED/NEEDS_MORE_INFORMATION/NEEDS_USER_APPROVAL/READY_FOR_FUTURE_EXECUTION/
-- EXPIRED/CANCELLED —— **没有** RUNNING / EXECUTING / CONFIRMED（§一：本阶段执行层是 NONE）。
CREATE TABLE IF NOT EXISTS task_proposals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id           TEXT NOT NULL UNIQUE,
    source                TEXT NOT NULL DEFAULT 'SYSTEM',
    objective             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'NEEDS_MORE_INFORMATION',
    intent_id             TEXT NOT NULL DEFAULT '',
    initiator             TEXT NOT NULL DEFAULT '',
    target                TEXT NOT NULL DEFAULT '{}',
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    risk_summary          TEXT NOT NULL DEFAULT '{}',
    feasibility           TEXT NOT NULL DEFAULT 'UNKNOWN',
    suggestions           TEXT NOT NULL DEFAULT '[]',
    reason                TEXT NOT NULL DEFAULT '',
    fingerprint           TEXT NOT NULL,
    created_at            REAL NOT NULL DEFAULT 0,
    expires_at            REAL NOT NULL DEFAULT 0,
    updated_at            REAL NOT NULL DEFAULT 0,
    payload               TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_proposals_fingerprint
    ON task_proposals(fingerprint);
CREATE INDEX IF NOT EXISTS idx_task_proposals_created
    ON task_proposals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_proposals_status
    ON task_proposals(status, created_at DESC);
""",
    ),
    (
        33,
        "task agent plans (Phase 7D)",
        """
-- Phase 7D §二/修订 3：AgentPlan 的最小存储。先查过现有库：
--   tasks/agent_task_runs —— 是**执行系统**（5A），AgentPlan 只保存关联 task_id，
--                           步骤执行状态一律实时读 Task 的 checkpoint/事件，绝不在这里复制；
--   task_proposals        —— 是"知道需要什么能力"（7C），AgentPlan 是"准备怎样做"的结构化计划；
--   behavior_events       —— append-only 审计表，7D **复用它**记录计划历史（agentplan.* 前缀）。
-- 状态只有 PLANNING/READY_FOR_APPROVAL/NEEDS_MORE_INFORMATION/UNSUPPORTED/BLOCKED_BY_POLICY/
-- BLOCKED_BY_PRECONDITION/APPROVED/LINKED/REPLAN_REQUIRED/REJECTED/EXPIRED/CANCELLED ——
-- 表名用 task_agent_plans：库里已有 agent 子系统的 agent_plans 表，绝不混用。
-- **没有** EXECUTING / RUNNING / SUCCEEDED（修订 3：AgentPlan 不是第二套任务状态机）。
CREATE TABLE IF NOT EXISTS task_agent_plans (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id              TEXT NOT NULL UNIQUE,
    source               TEXT NOT NULL DEFAULT 'SYSTEM',
    objective            TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'PLANNING',
    proposal_id          TEXT NOT NULL DEFAULT '',
    intent_id            TEXT NOT NULL DEFAULT '',
    task_id              TEXT NOT NULL DEFAULT '',
    initiator            TEXT NOT NULL DEFAULT '',
    approver_user_id     TEXT NOT NULL DEFAULT '',
    approver_session_id  TEXT NOT NULL DEFAULT '',
    approved_at          REAL NOT NULL DEFAULT 0,
    target               TEXT NOT NULL DEFAULT '{}',
    plan                 TEXT NOT NULL DEFAULT '{}',
    plan_hash            TEXT NOT NULL DEFAULT '',
    version              INTEGER NOT NULL DEFAULT 0,
    history              TEXT NOT NULL DEFAULT '[]',
    checks               TEXT NOT NULL DEFAULT '[]',
    risk_summary         TEXT NOT NULL DEFAULT '{}',
    reason               TEXT NOT NULL DEFAULT '',
    replans              INTEGER NOT NULL DEFAULT 0,
    replan_budget        INTEGER NOT NULL DEFAULT 2,
    replan_reason        TEXT NOT NULL DEFAULT '',
    fingerprint          TEXT NOT NULL,
    created_at           REAL NOT NULL DEFAULT 0,
    expires_at           REAL NOT NULL DEFAULT 0,
    updated_at           REAL NOT NULL DEFAULT 0,
    payload              TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_agent_plans_fingerprint
    ON task_agent_plans(fingerprint);
CREATE INDEX IF NOT EXISTS idx_task_agent_plans_created
    ON task_agent_plans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_agent_plans_status
    ON task_agent_plans(status, created_at DESC);
""",
    ),
    (
        34,
        "procedural skills + learning ledger (Phase 7E)",
        """
-- Phase 7E §7.1/§9：程序性技能的最小存储（两张表）。先查过现有库：
--   memories           —— 通用记忆引擎：合并/压缩会改写正文、保留策略会过期删除、
--                         配额会裁剪、语义检索会把技能当聊天记忆注入 —— 与"结构化、带版本、
--                         带条件、终态不可复活"的技能语义相反，**不塞**（对比见 7E 文档 §2.1）；
--   task_agent_plans   —— 是"准备怎样做"的计划层（7D），技能是"已经验证过的方法"，
--                         且必须跨任务存活并带证据计数，语义不同；
--   sandbox_memory_candidates —— 沙盒"经历 → 记忆"的候选表（迁移 22），与程序性技能无关；
--   behavior_events    —— append-only 审计表，7E **复用它**记录 skill.* 历史，
--                         所以这里只有这两张表：没有第二套记忆引擎、没有第二套任务状态机。
-- 状态只有 CANDIDATE/ACTIVE/STALE/INVALIDATED/REJECTED（§7.1）—— 没有 RUNNING/EXECUTING，
-- 技能不是任务；也没有任何执行入口。
-- 幂等：fingerprint 唯一索引（同一方法只留一条）；证据表
-- UNIQUE(subject_key, task_id, plan_version, plan_hash) —— 重复终态事件 / 重复回调 /
-- 重启恢复都只落一条证据，成功/失败计数不会重复累加（§7.2 第 4 条）。
CREATE TABLE IF NOT EXISTS procedural_skills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL UNIQUE,
    schema_version      INTEGER NOT NULL DEFAULT 1,
    name                TEXT NOT NULL DEFAULT '',
    objective_pattern   TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'CANDIDATE',
    character_id        TEXT NOT NULL DEFAULT '',
    server_id           TEXT NOT NULL DEFAULT '',
    environment         TEXT NOT NULL DEFAULT '',
    tools_signature     TEXT NOT NULL DEFAULT '',
    max_risk            TEXT NOT NULL DEFAULT 'SAFE',
    version             INTEGER NOT NULL DEFAULT 1,
    supersedes_skill_id TEXT NOT NULL DEFAULT '',
    superseded_by       TEXT NOT NULL DEFAULT '',
    success_count       INTEGER NOT NULL DEFAULT 0,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    ambiguous_count     INTEGER NOT NULL DEFAULT 0,
    fingerprint         TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    created_at          REAL NOT NULL DEFAULT 0,
    updated_at          REAL NOT NULL DEFAULT 0,
    last_learned_at     REAL NOT NULL DEFAULT 0,
    last_verified_at    REAL NOT NULL DEFAULT 0,
    last_used_at        REAL NOT NULL DEFAULT 0,
    payload             TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_procedural_skills_fingerprint
    ON procedural_skills(fingerprint);
CREATE INDEX IF NOT EXISTS idx_procedural_skills_status
    ON procedural_skills(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_procedural_skills_scope
    ON procedural_skills(character_id, server_id, status);

CREATE TABLE IF NOT EXISTS procedural_skill_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id        TEXT NOT NULL UNIQUE,
    skill_id           TEXT NOT NULL DEFAULT '',
    subject_key        TEXT NOT NULL DEFAULT '',
    task_id            TEXT NOT NULL DEFAULT '',
    plan_version       INTEGER NOT NULL DEFAULT 0,
    plan_hash          TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL DEFAULT '',
    verdict            TEXT NOT NULL DEFAULT '',
    reason_code        TEXT NOT NULL DEFAULT '',
    step_ids           TEXT NOT NULL DEFAULT '[]',
    postcondition_kind TEXT NOT NULL DEFAULT '',
    postcondition_ok   INTEGER NOT NULL DEFAULT 0,
    detail             TEXT NOT NULL DEFAULT '{}',
    created_at         REAL NOT NULL DEFAULT 0,
    payload            TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_procedural_skill_evidence_once
    ON procedural_skill_evidence(subject_key, task_id, plan_version, plan_hash);
CREATE INDEX IF NOT EXISTS idx_procedural_skill_evidence_skill
    ON procedural_skill_evidence(skill_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_procedural_skill_evidence_recent
    ON procedural_skill_evidence(created_at DESC);

-- 技能使用链（§7.6 回流）：物化发生在"任务还不存在"之前，所以先按
-- (objective, plan_hash) 记账；任务收尾时用同两个值反查，就能把结果回流到正确的技能。
-- 不落 task_id：任务可能建失败（busy / 校验失败），那样这行就是"未被使用的记账"，无害。
CREATE TABLE IF NOT EXISTS procedural_skill_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL,
    usage_key   TEXT NOT NULL UNIQUE,
    objective   TEXT NOT NULL DEFAULT '',
    plan_hash   TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_procedural_skill_usage_skill
    ON procedural_skill_usage(skill_id, created_at DESC);
""",
    ),
    (
        35,
        "skill task bindings + subject key (Phase 7E.1)",
        """
-- Phase 7E.1 §2：把"技能被哪个**真实任务**用了"从派生键改成**每任务唯一**的持久绑定。
--   procedural_skill_usage（34，按 objective+plan_hash 派生键）已废弃：任务还没建立就登记，
--   之后同目标同计划哈希的普通任务会被误认成"用过技能" —— 这里直接删掉，绝不留兜底路径。
-- 先查过现有库：tasks / agent_task_runs 是执行系统，不承载"技能归因"；
-- behavior_events 是 append-only 审计，不适合做"一次性消费 + 撤销"的绑定；故新建一张最小表。
-- 语义：一个任务最多一条绑定（task_id 唯一）；只有**任务真正建立之后**才写；
-- 只按 task_id 精确查找（不再用派生键推断）；消费一次（consumed_at）；随任务 TTL 失效
-- （expires_at，仅用于作废/清理：task_id 唯一，未来的任务不可能撞上旧绑定）。
CREATE TABLE IF NOT EXISTS procedural_skill_bindings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id   TEXT NOT NULL UNIQUE,
    task_id      TEXT NOT NULL UNIQUE,
    skill_id     TEXT NOT NULL,
    subject_key  TEXT NOT NULL DEFAULT '',
    plan_version INTEGER NOT NULL DEFAULT 0,
    plan_hash    TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL DEFAULT 0,
    expires_at   REAL NOT NULL DEFAULT 0,
    consumed_at  REAL NOT NULL DEFAULT 0,
    outcome      TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_procedural_skill_bindings_skill
    ON procedural_skill_bindings(skill_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_procedural_skill_bindings_open
    ON procedural_skill_bindings(consumed_at, expires_at);

DROP TABLE IF EXISTS procedural_skill_usage;

-- 技能行上补 subject_key 列（"同一条方法主题"的精确索引；歧义/拒绝证据要能附着到它）。
-- ALTER 只在本迁移里出现一次，且放在所有幂等语句之后：迁移记录与结构变化同生共死。
ALTER TABLE procedural_skills ADD COLUMN subject_key TEXT NOT NULL DEFAULT '';
UPDATE procedural_skills
   SET subject_key = COALESCE(json_extract(payload, '$.extra.subject_key'), '')
 WHERE subject_key = '';
CREATE INDEX IF NOT EXISTS idx_procedural_skills_subject
    ON procedural_skills(character_id, server_id, subject_key);
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
