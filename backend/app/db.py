"""SQLite 数据层：连接管理、schema 定义与初始化。

设计原则：
- 零外部依赖，使用 Python 内置 sqlite3。
- WAL 模式，兼顾读写并发。
- 每请求/每任务使用独立连接，避免线程安全问题。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("content_studio.db")

# ---------------------------------------------------------------- schema

SCHEMA = """
-- 内容主表：我的内容与对标内容统一存储，用 source 区分
-- provenance 只对 source='mine' 有意义：
--   human_original 用户完全自己写的
--   imported_self  用户导入的历史本人作品
--   ai_assisted    AI 生成初稿后用户编辑并确认的版本
-- analysis_state 是分析流程的唯一控制状态（status 保留以兼容旧逻辑）：
--   unprocessed 已入库、未分析（导入后的默认值，绝不自动调用 AI）
--   queued      用户已点击分析，等待 worker
--   processing  正在调用模型
--   analyzed    分析成功
--   failed      执行出错
--   cancelled   用户取消（剩余未执行部分回到 unprocessed）
CREATE TABLE IF NOT EXISTS contents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT    NOT NULL CHECK (source IN ('mine', 'benchmark')),
    provenance     TEXT    CHECK (provenance IN ('human_original', 'imported_self', 'ai_assisted')),
    raw_text       TEXT    NOT NULL,
    summary        TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'analyzing', 'analyzed', 'failed')),
    analysis_state TEXT CHECK (analysis_state IN
                   ('unprocessed', 'queued', 'processing', 'analyzed', 'failed', 'cancelled')),
    analysis_error TEXT,
    -- 来源元数据（如 Xporter 导出）：只做溯源/去重，绝不拼进 raw_text
    external_id    TEXT,
    source_url     TEXT,
    author_username TEXT,
    -- 正式作者关联；author_username 保留为兼容/便捷字段，两者由 repo 层同步维护
    author_id      INTEGER REFERENCES authors(id),
    posted_at      TEXT,
    post_type      TEXT,
    -- 同一次批量导入操作的标识（与 MiMo batch_id 无关）
    import_batch_id TEXT,
    -- 是否允许作为个人风格学习候选样本。
    -- 与 source 完全独立的第二个维度：
    --   source=mine      只是「这是我的内容」
    --   style_eligible=1 才是「允许 AI 学我的风格」
    -- 有效条件永远是 source='mine' AND style_eligible=1 AND 其它既有条件。
    style_eligible  INTEGER NOT NULL DEFAULT 0,
    -- 来源元数据 JSON（任务 04-A 新增）。
    -- 收容 Xporter 里**没有**专用列、但对下游有意义的字段：
    --   conversation_id / language / reply_to_username / hashtags / urls / media_urls
    -- 用一个 JSON 列而不是六套新表 —— 这些字段目前只用于过滤、标注与查看，
    -- 不参与任何等值连接，不需要索引。
    source_meta     TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contents_source     ON contents(source);
CREATE INDEX IF NOT EXISTS idx_contents_status     ON contents(status);
CREATE INDEX IF NOT EXISTS idx_contents_created    ON contents(created_at DESC);
-- 注意：idx_contents_provenance 在 _migrate 之后创建（旧库需要先补列）

-- 分析结果：一条内容对应一条分析。
--
-- 结构（任务 04-A / ANALYSIS v3）：
--   1. **扁平索引列**：真正用于检索、分组、跨样本计数的那几个标量。
--      它们全部来自 ANALYSIS_SPEC 的可空标量字段。
--   2. analysis_json   ：**完整规范 JSON**（通过严格校验后的原样对象）。
--      moves / markers / patterns / claims 等带 evidence 的细节只存在这里，
--      按路径读取 —— 不为每条细节建专用表。
--   3. 版本指纹：prompt_version / schema_version / schema_hash，
--      用于回答「这一行是哪版提示词、哪版合同产出的」。
--
-- 约束：**只有通过 analysis_spec.validate_analysis() 的结果才允许写入本表**。
-- 缺字段的半成品不得以 analyzed 身份落库。
CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id      INTEGER NOT NULL UNIQUE
                    REFERENCES contents(id) ON DELETE CASCADE,
    -- ---- 基础字段 ----
    topic           TEXT,
    subtopic        TEXT,
    content_type    TEXT,
    core_message    TEXT,
    -- ---- 写法字段 ----
    opening_type    TEXT,
    structure_type  TEXT,
    ending_type     TEXT,
    -- ---- 语言字段（只描述本篇）----
    tone            TEXT,
    register        TEXT,
    directness      TEXT,
    certainty       TEXT,
    rhythm          TEXT,
    -- ---- 本地计算，不由模型生成 ----
    length_chars    INTEGER,
    -- ---- 检索用数组 ----
    tags            TEXT,   -- JSON 数组
    keywords        TEXT,   -- JSON 数组
    -- ---- 完整规范 JSON ----
    analysis_json   TEXT,
    -- ---- 产物指纹 ----
    model           TEXT,
    prompt_version  TEXT,
    schema_version  TEXT,
    schema_hash     TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_topic ON analyses(topic);
CREATE INDEX IF NOT EXISTS idx_analyses_type  ON analyses(content_type);

-- 帖子表现数据（Performance V1）：Xporter JSON 里已有的公开计数。
--
-- 语义：
--   1 content → 0 或 1 performance。**不是**快照、不是时序、没有历史版本。
--   没有 performance 的内容（TXT / MD / CSV / 手工输入）根本不会有这一行。
--   计数为 NULL 与 0 严格区分：NULL = 未知，0 = 已知为 0。
--
-- 重复导入同一个 external_id 时的更新规则（任务 04-A）：
--   内容本身仍然 skip（不重复导入、不触发 AI），但**表现数据会按采集时间更新**：
--     1. 新采集时间早于已有 captured_at → 整行不动（旧快照不得覆盖新快照）；
--     2. 否则逐列合并：新值为 NULL → 保留旧值（不覆盖已知计数）；
--        新值非 NULL（含 0）→ 覆盖。0 是「已知为 0」，有权覆盖旧的非零值。
--   不取最大值 —— 计数可能下降，取最大值会伪造一个从未发生过的峰值。
--
-- captured_at 是「工作台最后一次拿到这批数据的时间」，同一次 import batch 共用；
-- 不是帖子发布时间（后者在 contents.posted_at）。
CREATE TABLE IF NOT EXISTS content_performance (
    content_id     INTEGER PRIMARY KEY
                   REFERENCES contents(id) ON DELETE CASCADE,
    view_count     INTEGER,
    favorite_count INTEGER,
    reply_count    INTEGER,
    retweet_count  INTEGER,
    quote_count    INTEGER,
    bookmark_count INTEGER,
    is_pinned      INTEGER NOT NULL DEFAULT 0,
    media_type     TEXT,
    captured_at    TEXT    NOT NULL,
    updated_at     TEXT
);

-- 规范化标签表
CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL UNIQUE,
    kind  TEXT    NOT NULL DEFAULT 'tag',   -- tag / topic / content_type / keyword
    count INTEGER NOT NULL DEFAULT 0
);

-- 内容-标签关联
CREATE TABLE IF NOT EXISTS content_tags (
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    kind       TEXT    NOT NULL DEFAULT 'tag',
    PRIMARY KEY (content_id, tag_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_content_tags_tag ON content_tags(tag_id);

-- 风格档案：版本化（任务 04-B 起由"定期重算"改成"首次建档 + 增量"）
CREATE TABLE IF NOT EXISTS style_profile (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         INTEGER NOT NULL,
    based_on_count  INTEGER NOT NULL,
    profile_json    TEXT    NOT NULL,   -- 结构化档案
    summary_text    TEXT,               -- 自然语言总结（供改写 prompt 使用）
    sample_ids      TEXT,               -- JSON：本版档案**实际使用**的 content_id 列表
    prompt_version  TEXT,               -- 产出本版档案的 STYLE prompt 版本（D6）
    -- v8 → v9：区分档案是由哪套门槛/机制产出的，防止旧版冒充新版。
    --   legacy_min5    ：旧 `MIN_CONTENT=5` 门槛、重算式生成的档案（**只读保留**，不得用于 WRITE）
    --   initial_v20    ：第 20 篇有效已分析本人作品触发的首次集中建档
    --   incremental    ：第 21 篇起的单篇增量更新
    -- 旧行此列为 NULL → 按 legacy_min5 处理。
    profile_kind    TEXT,
    created_at      TEXT    NOT NULL
);

-- ================================================================
-- STYLE 增量学习台账（任务：STYLE 增量学习架构 v3 / A 方案）
-- ================================================================
-- 旧 style_profile 只存"一段汇总文本"，证据不可撤销、不可溯源。
-- 这三张表把 STYLE 从"定期重算"改成"可追溯的增量证据数据库"：
--   1. style_content_state      ：逐篇学习状态（与 ANALYSIS 状态分开）
--   2. style_patterns           ：规律（去重后的身份）
--   3. style_pattern_evidence   ：pattern ↔ content 的逐字证据

-- ① 逐篇 STYLE 学习状态。
-- state 与 contents.analysis_state **完全独立**：
-- ANALYSIS 完成 ≠ STYLE 已学习。
CREATE TABLE IF NOT EXISTS style_content_state (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id             INTEGER NOT NULL UNIQUE
                           REFERENCES contents(id) ON DELETE CASCADE,
    -- ── 幂等键四要素：内容版本 + ANALYSIS 版本 + STYLE 提取版本 ──
    text_hash              TEXT    NOT NULL,
    analysis_schema_version TEXT,
    analysis_prompt_version TEXT,
    extractor_version      TEXT    NOT NULL,
    -- 学习状态：
    --   pending      : 已计入学习池，等待首次建档
    --   queued       : 已入队，等待 worker
    --   processing   : 正在提取（失败不得留下半套证据）
    --   learned      : 已成功写入风格证据台账
    --   failed       : 提取失败，可重试
    --   invalidated  : 资格撤销 / 删除 / 转为对标 / 修稿旧版本，贡献已撤回
    state                  TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (state IN ('pending','queued','processing',
                                            'learned','failed','invalidated')),
    last_error             TEXT,
    -- 本篇局部观察（经 STYLE 观察合同校验后的 JSON）
    observation_json       TEXT,
    learned_at             TEXT,
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scs_state   ON style_content_state(state);
CREATE INDEX IF NOT EXISTS idx_scs_hash    ON style_content_state(text_hash);

-- ② 风格规律（去重后的身份）。
-- pattern_key 是规律的身份键（由 rule 归一化而来）：
-- 新观察命中已有规律 → 只增证据；否则新建候选规律。
CREATE TABLE IF NOT EXISTS style_patterns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key        TEXT    NOT NULL UNIQUE,
    rule               TEXT    NOT NULL,   -- 可执行的写法描述
    dimension          TEXT,               -- 维度：opening / structure / ending / wording / rhythm ...
    classification     TEXT    NOT NULL
                       CHECK (classification IN ('VOICE','X_REGISTER','TOPIC_BOUND','UNCERTAIN')),
    applies_when       TEXT,               -- 适用情境
    -- candidate  : 只有一篇支持（不足以写成强规则）
    -- supported  : 有跨篇有效证据
    -- retired    : 证据被撤回后降级（保留历史，不物理删除）
    status             TEXT    NOT NULL DEFAULT 'candidate'
                       CHECK (status IN ('candidate','supported','retired')),
    extractor_version  TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_class    ON style_patterns(classification);
CREATE INDEX IF NOT EXISTS idx_sp_status   ON style_patterns(status);

-- ③ 规律 ↔ 作品 的逐字证据。
-- **同一作品重复出现同一表达，也只计为该规律的一篇支持作品** ——
-- 因此 UNIQUE(pattern_id, content_id)：一篇作品对一条规律只有一条证据记录，
-- COUNT(DISTINCT content_id) 天然就是支持篇数（不是出现次数）。
CREATE TABLE IF NOT EXISTS style_pattern_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id         INTEGER NOT NULL
                       REFERENCES style_patterns(id) ON DELETE CASCADE,
    content_id         INTEGER NOT NULL
                       REFERENCES contents(id) ON DELETE CASCADE,
    quote              TEXT    NOT NULL,   -- 必须逐字连续存在于该作品原文
    -- 证据强度的两个独立维度（与 ANALYSIS/STYLE 的 authorship/preference 同源）
    source_kind        TEXT,               -- human_original / imported_self / ai_assisted
    authorship         TEXT,               -- high / medium / low
    preference         TEXT,               -- high / medium / low
    topic              TEXT,
    -- support   : 支持该规律
    -- counter   : 明确反例（真实人工修改证明的反向证据）
    -- uncertain : 证据不足，仅提示可能性
    evidence_type      TEXT    NOT NULL DEFAULT 'support'
                       CHECK (evidence_type IN ('support','counter','uncertain')),
    text_hash          TEXT    NOT NULL,   -- 内容版本（修稿时按此撤回旧贡献）
    extractor_version  TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    UNIQUE(pattern_id, content_id)
);
CREATE INDEX IF NOT EXISTS idx_spe_pattern ON style_pattern_evidence(pattern_id);
CREATE INDEX IF NOT EXISTS idx_spe_content ON style_pattern_evidence(content_id);

-- 改写 / 生成记录：同时保存 AI 初稿与用户终稿，及修改关系
-- benchmark_content_id 可为空：纯素材/想法生成没有对标来源
CREATE TABLE IF NOT EXISTS rewrites (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_content_id   INTEGER
                           REFERENCES contents(id) ON DELETE CASCADE,
    ai_draft               TEXT    NOT NULL,   -- AI 初稿
    final_text             TEXT,               -- 用户确认的最终稿
    instruction            TEXT,               -- 生成/迭代时使用的指令
    is_saved_as_mine       INTEGER NOT NULL DEFAULT 0,
    saved_content_id       INTEGER REFERENCES contents(id),
    examples_used          TEXT,               -- JSON：本次引用的 reference 内容 id
    style_profile_version  INTEGER,
    edit_distance          INTEGER,            -- 初稿→终稿 字符级编辑距离
    edit_ratio             REAL,               -- edit_distance / max(len(draft), len(final))
    prompt_version         TEXT,               -- 生成时使用的 WRITE prompt 版本
    personalization_state  TEXT,               -- 生成时的 personalization 状态
    model                  TEXT,
    created_at             TEXT    NOT NULL,
    updated_at             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rewrites_benchmark ON rewrites(benchmark_content_id);

-- 向量表：第一版预留，不使用；后续升级 embedding 检索时启用
CREATE TABLE IF NOT EXISTS embeddings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    model      TEXT    NOT NULL,
    vector     BLOB,
    dim        INTEGER,
    created_at TEXT    NOT NULL,
    UNIQUE (content_id, model)
);

-- 批量分析任务（Batch API）持久化：重启后可恢复查询，且禁止重复提交
CREATE TABLE IF NOT EXISTS batch_jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    provider       TEXT    NOT NULL DEFAULT 'openai_compat',
    model          TEXT    NOT NULL,
    batch_id       TEXT,
    input_file_id  TEXT,
    output_file_id TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending',
    custom_map     TEXT,               -- JSON: {"custom_id": content_id}
    total          INTEGER NOT NULL DEFAULT 0,
    succeeded      INTEGER NOT NULL DEFAULT 0,
    failed         INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT,
    error          TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_jobs(status);

-- 作者：正式数据实体。
-- 存在的意义是让「一个还没有任何帖子的作者」也能先被建立。
-- 唯一身份 = platform + normalized username（X 用户名大小写不敏感，统一小写）。
CREATE TABLE IF NOT EXISTS authors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL DEFAULT 'x',
    username     TEXT NOT NULL,          -- 已 normalize（无 @、小写）
    display_name TEXT,                   -- 可选；Xporter 没有可靠值时保持 NULL，不猜
    notes        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (platform, username)          -- 数据库层唯一保护，不依赖前端判断
);

-- 每一次用户批量导入操作（与 MiMo batch_id 无关）
CREATE TABLE IF NOT EXISTS import_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    import_batch_id TEXT    NOT NULL UNIQUE,
    filename        TEXT,
    source          TEXT    NOT NULL,
    item_count      INTEGER NOT NULL DEFAULT 0,
    -- 导入结果统计：让用户知道"为什么少了 N 条"
    input_count     INTEGER NOT NULL DEFAULT 0,
    inserted_count  INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    author_username TEXT,
    created_at      TEXT    NOT NULL
);

-- 一次分析运行（实时或 Batch）。用户可对正在运行的 run 发起取消。
CREATE TABLE IF NOT EXISTS analysis_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL CHECK (kind IN ('realtime', 'batch')),
    import_batch_id TEXT,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'cancelling', 'cancelled',
                                      'completed', 'failed')),
    total           INTEGER NOT NULL DEFAULT 0,
    done            INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    batch_job_id    INTEGER REFERENCES batch_jobs(id),
    model           TEXT,
    error           TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON analysis_runs(status);

-- ------------------------------------------------------------------
-- 「我的」V1：账号表现账本
-- ------------------------------------------------------------------
-- 全部 0 AI：这些表只承接用户导入的 CSV 与手动录入的快照，
-- 不参与分析、不进风格档案、不影响任何既有流程。

-- 账号日指标：Account Overview CSV 的日粒度行。
--
-- 语义（关键）：
--   1 天 → 0 或 1 行，按 metric_date 唯一。**不是**内容级数据，
--   与 content_performance 没有任何关联。
--   13 个指标列全部可空 —— 空白单元格 = NULL（未知），0 = 已知为 0，
--   这个区分与 content_performance 的既有约定一致，不得混为一谈。
--   再次导入同一天 = 整行覆盖（ON CONFLICT DO UPDATE），不是累加。
--
-- 列名用 metric_date 而不是 date：
--   SQLite 有内建 date() 函数，用 date 作列名会持续造成阅读歧义。
CREATE TABLE IF NOT EXISTS account_daily_metrics (
    metric_date    TEXT    NOT NULL UNIQUE,
    impressions    INTEGER,
    likes          INTEGER,
    engagements    INTEGER,
    bookmarks      INTEGER,
    shares         INTEGER,
    new_follows    INTEGER,
    unfollows      INTEGER,
    replies        INTEGER,
    reposts        INTEGER,
    profile_visits INTEGER,
    create_post    INTEGER,
    video_views    INTEGER,
    media_views    INTEGER,
    updated_at     TEXT    NOT NULL
);

-- 合格浏览量快照：用户手动录入的**账本**。
--
-- 与 account_daily_metrics.impressions 完全独立，不做任何换算或预填。
-- 目标进度只认这张表。
CREATE TABLE IF NOT EXISTS qualified_impression_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at           TEXT    NOT NULL,
    qualified_impressions INTEGER NOT NULL CHECK (qualified_impressions >= 0),
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

-- 创作者目标：单实例（id 恒为 1，不允许第二条）。
--
-- baseline 是**建目标那一刻**的合格浏览量，用来把「目标」换算成
-- 可比较的增量，而不是让历史存量冒充进度。
CREATE TABLE IF NOT EXISTS creator_goal (
    id                             INTEGER PRIMARY KEY CHECK (id = 1),
    start_date                     TEXT    NOT NULL,
    baseline_qualified_impressions INTEGER NOT NULL
                                   CHECK (baseline_qualified_impressions >= 0),
    created_at                     TEXT    NOT NULL
);

-- 元信息（schema 版本等）
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA_VERSION = "9"


# ---------------------------------------------------------------- 迁移

# 旧库升级用：表名 -> [(列名, 列类型)]
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "contents": [
        ("provenance", "TEXT"),
        ("external_id", "TEXT"),
        ("source_url", "TEXT"),
        ("author_username", "TEXT"),
        ("posted_at", "TEXT"),
        ("post_type", "TEXT"),
        ("analysis_state", "TEXT"),
        ("import_batch_id", "TEXT"),
        # 注意：旧库回填由 _backfill_style_eligible 负责（不能只靠 DEFAULT，
        # 否则已有 mine 语料会全部失效）
        ("style_eligible", "INTEGER NOT NULL DEFAULT 0"),
        ("author_id", "INTEGER REFERENCES authors(id)"),
        # v7 → v8：来源元数据 JSON（conversation_id / language / 链接 / 话题标签…）
        ("source_meta", "TEXT"),
    ],
    "style_profile": [
        ("sample_ids", "TEXT"),
        # v7 → v8：记录本版档案由哪版 STYLE prompt 产出（D6）
        ("prompt_version", "TEXT"),
        # v8 → v9：区分旧 5 篇门槛档案与新版 20 篇增量档案，防止旧版冒充新版
        ("profile_kind", "TEXT"),
    ],
    "rewrites": [
        ("edit_distance", "INTEGER"),
        ("edit_ratio", "REAL"),
        ("prompt_version", "TEXT"),
        ("personalization_state", "TEXT"),
    ],
    "import_batches": [
        ("input_count", "INTEGER NOT NULL DEFAULT 0"),
        ("inserted_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duplicate_count", "INTEGER NOT NULL DEFAULT 0"),
        ("skipped_count", "INTEGER NOT NULL DEFAULT 0"),
        ("author_username", "TEXT"),
    ],
    "content_performance": [
        # v7 → v8：表现数据现在会随重复导入更新，需要记录最后更新时间
        ("updated_at", "TEXT"),
    ],
}

# v7 → v8：analyses 换用新的字段合同。
#
# 旧列（core_viewpoint / structure / emotion / sentence_style / raw_json）与新列
# （core_message / structure_type / register / …）不是改名关系，字段语义也不同，
# 无法做保真映射 —— 硬映射会在画像里伪造出一批看似合法的统计。
#
# 因此：
#   - 表内 0 行 → 直接重建（开发阶段常态）；
#   - 表内 >0 行 → **改名封存**为 analyses_legacy_v2（绝不 DELETE），
#     新建空表，并把那些内容退回 unprocessed，让 UI 如实显示「未分析」，
#     用户可以按新合同重新分析。封存行数会写进日志。
_ANALYSES_LEGACY_TABLE = "analyses_legacy_v2"


def _analyses_needs_rebuild(conn: sqlite3.Connection) -> bool:
    """现有 analyses 表是否还是旧合同（靠列名判断）。"""
    rows = conn.execute("PRAGMA table_info(analyses)").fetchall()
    names = {row["name"] for row in rows}
    if not names:
        return False                    # 表不存在 → executescript 已建新表
    return "raw_json" in names or "core_message" not in names


def _rebuild_analyses_for_v8(conn: sqlite3.Connection) -> dict[str, int]:
    """把 analyses 迁移到新合同。返回统计，供日志与测试断言。"""
    if not _analyses_needs_rebuild(conn):
        return {"rebuilt": 0, "archived": 0, "reverted_contents": 0}

    total = int(conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"])

    # 旧表无论是否有行都必须让位：新表结构由 SCHEMA 里的 CREATE 语句定义，
    # 因此先改名（或直接删空表），再让 init_db 的 executescript 建新表。
    if total == 0:
        conn.execute("DROP TABLE analyses")
        return {"rebuilt": 1, "archived": 0, "reverted_contents": 0}

    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (_ANALYSES_LEGACY_TABLE,),
    ).fetchone():
        # 已经封存过一次：本次直接丢弃旧表（数据已在 legacy 表里）
        conn.execute("DROP TABLE analyses")
    else:
        conn.execute(f"ALTER TABLE analyses RENAME TO {_ANALYSES_LEGACY_TABLE}")

    reverted = conn.execute(
        "UPDATE contents SET analysis_state='unprocessed', status='pending', "
        "                     analysis_error=NULL, updated_at=? "
        " WHERE id IN (SELECT content_id FROM analyses_legacy_v2)",
        (_now_iso(),),
    ).rowcount

    logger.warning(
        "analyses 迁移到新合同：%s 行旧结果已封存到 %s（未删除）；"
        "%s 条内容退回未分析，需按新合同重跑",
        total,
        _ANALYSES_LEGACY_TABLE,
        int(reverted or 0),
    )
    return {"rebuilt": 1, "archived": total, "reverted_contents": int(reverted or 0)}


def _now_iso() -> str:
    """本地时区的 ISO 时间戳（与 repository.now_iso 口径一致）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """幂等补齐旧库缺失的列（SQLite 不支持 ADD COLUMN IF NOT EXISTS）。"""
    for table, columns in _COLUMN_MIGRATIONS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        if not existing:
            continue  # 表不存在（理论上不会发生）
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# 依赖迁移后新列的索引：必须在 _migrate 之后创建，否则旧库会报 "no such column"
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_contents_provenance ON contents(provenance)",
    "CREATE INDEX IF NOT EXISTS idx_contents_import_batch ON contents(import_batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_contents_analysis_state ON contents(analysis_state)",
    "CREATE INDEX IF NOT EXISTS idx_contents_author ON contents(author_username)",
    "CREATE INDEX IF NOT EXISTS idx_contents_author_id ON contents(author_id)",
    "CREATE INDEX IF NOT EXISTS idx_contents_style_eligible ON contents(style_eligible)",
    # external_id 去重：用「部分唯一索引」，只约束真正有值的行。
    # 无 external_id 的 TXT/MD 手工内容不受影响。
    "DROP INDEX IF EXISTS idx_contents_external_id",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_contents_external_id_unique
       ON contents(external_id)
       WHERE external_id IS NOT NULL AND external_id != ''""",
]


def count_duplicate_external_ids(conn: sqlite3.Connection) -> int:
    """统计存在重复的 external_id 组数（只读，用于迁移前安全检查）。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT external_id FROM contents
             WHERE external_id IS NOT NULL AND external_id != ''
             GROUP BY external_id HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    return int(row["n"])


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _backfill_analysis_state(conn: sqlite3.Connection) -> None:
    """旧数据回填 analysis_state（旧 pending 归为 unprocessed）。"""
    conn.execute(_BACKFILL_ANALYSIS_STATE)


def normalize_username(raw: str | None) -> str | None:
    """X 用户名的统一规范。

    规则：trim → 去掉开头 @ → 统一小写。

    这样 @WangChangFu88 / WangChangFu88 / wangchangfu88
    会被识别为同一个作者，不会创建三个。
    """
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):
        value = value[1:]
    value = value.strip().lower()
    return value or None


def _backfill_authors(conn: sqlite3.Connection) -> int:
    """把历史 contents.author_username 归并为正式 authors 并回填 author_id。

    只动 author_id，**不修改** source / provenance / analysis /
    external_id / style_eligible 等任何业务字段。
    """
    rows = conn.execute(
        "SELECT DISTINCT author_username FROM contents "
        " WHERE author_username IS NOT NULL AND TRIM(author_username) != ''"
    ).fetchall()

    # normalize 后归并（@abc / ABC / abc → abc）
    normalized: dict[str, str] = {}
    for r in rows:
        raw = r["author_username"]
        norm = normalize_username(raw)
        if norm and norm not in normalized:
            normalized[norm] = str(raw).strip()

    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    created = 0
    for norm, original in normalized.items():
        existing = conn.execute(
            "SELECT id FROM authors WHERE platform = 'x' AND username = ?", (norm,)
        ).fetchone()
        if existing:
            author_id = int(existing["id"])
        else:
            cur = conn.execute(
                "INSERT INTO authors(platform, username, display_name, notes, "
                "                    created_at, updated_at) "
                "VALUES('x', ?, NULL, NULL, ?, ?)",
                (norm, ts, ts),
            )
            author_id = int(cur.lastrowid)
            created += 1
        # 该规范名下所有写法归并到同一作者，并同步 author_username 避免漂移
        conn.execute(
            "UPDATE contents SET author_id = ?, author_username = ? "
            " WHERE author_username IS NOT NULL AND LOWER(TRIM("
            "   CASE WHEN TRIM(author_username) LIKE '@%' "
            "        THEN SUBSTR(TRIM(author_username), 2) "
            "        ELSE TRIM(author_username) END)) = ?",
            (author_id, norm, norm),
        )

    filled = conn.execute(
        "SELECT COUNT(*) AS n FROM contents WHERE author_id IS NOT NULL"
    ).fetchone()["n"]
    if created or filled:
        logger.info(
            "authors 回填：新建 %s 位作者，回填 %s 条内容的 author_id", created, filled
        )
    return created


def _backfill_style_eligible(conn: sqlite3.Connection) -> int:
    """把历史数据回填为合理的 style_eligible（只在 v3 → v4 升级时执行一次）。

    规则：
        source='mine' AND provenance IN (human_original, imported_self, ai_assisted) → 1
        其它（benchmark / provenance 为空）→ 0

    既不让 benchmark 历史数据获得学习资格，
    也不让真实已有 mine 语料全部失效。
    """
    cur = conn.execute(
        """
        UPDATE contents SET style_eligible = 1
         WHERE source = 'mine'
           AND provenance IN ('human_original', 'imported_self', 'ai_assisted')
        """
    )
    affected = int(cur.rowcount or 0)
    if affected:
        logger.info(
            "style_eligible 回填：%s 条历史 mine 内容被标记为可参与风格学习", affected
        )
    return affected

# 旧数据回填：把既有 status 映射到 analysis_state。
# 刻意把旧的 pending 归为 unprocessed —— 它们本就不该被自动分析。
_BACKFILL_ANALYSIS_STATE = """
UPDATE contents SET analysis_state = CASE status
    WHEN 'analyzed'  THEN 'analyzed'
    WHEN 'failed'    THEN 'failed'
    WHEN 'analyzing' THEN 'processing'
    ELSE 'unprocessed'
END
WHERE analysis_state IS NULL
"""


# ---------------------------------------------------------------- 连接

def connect(db_path: Path) -> sqlite3.Connection:
    """创建一个已配置好的 SQLite 连接。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def get_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """上下文管理器：自动提交 / 回滚 / 关闭。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """初始化数据库：建表 → 补列 → 建索引 → 回填 → 写入 schema 版本。幂等。

    安全设计：external_id 的唯一约束只有在**确认无重复**时才会创建。
    真实库若已存在重复，这里会跳过约束并告警，而不是抛错或擅自删数据。

    v7 → v8（任务 04-A）：analyses 换成新字段合同。旧行**永不删除**，
    按行数决定「直接重建」还是「改名封存」，详见 _rebuild_analyses_for_v8。
    """
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)      # 先建表（含 meta），才能读版本号
        previous_version = _current_schema_version(conn)

        # analyses 换合同：必须在补列与建索引**之前**做，
        # 因为旧表的列集合与新表不兼容，补列会补出一张四不像的表。
        rebuild = _rebuild_analyses_for_v8(conn)
        if rebuild["rebuilt"]:
            # 旧表已让位（DROP 或 RENAME）—— 重新执行建表脚本，
            # 把空的、新合同版本的 analyses 建出来。
            conn.executescript(SCHEMA)

        _migrate(conn)

        duplicate_groups = count_duplicate_external_ids(conn)
        for statement in _POST_MIGRATION_INDEXES:
            if "UNIQUE INDEX" in statement and duplicate_groups:
                logger.warning(
                    "发现 %s 组重复 external_id，跳过唯一约束创建（需人工处理，"
                    "不会自动删除任何数据）",
                    duplicate_groups,
                )
                continue
            conn.execute(statement)

        conn.execute(_BACKFILL_ANALYSIS_STATE)

        # style_eligible 只在 v3 → v4 升级时回填一次，
        # 之后用户手动关闭的资格不会被重新打开。
        if previous_version < 4:
            _backfill_style_eligible(conn)

        # authors 回填：把历史 contents.author_username 归并成正式作者
        # （v4 → v5，只跑一次）
        if previous_version < 5:
            _backfill_authors(conn)

        # v5 → v6 = content_performance 表。
        # 该表由上面的 executescript(SCHEMA) 自动建立，无需 ALTER，
        # 也**不存在**可回填的历史数据（表现数据只随 Xporter 导入写入），
        # 因此这里不需要任何回填动作。

        # v6 → v7 = 「我的」V1 的 3 张表（account_daily_metrics /
        # qualified_impression_snapshots / creator_goal）。
        # 同样由 executescript(SCHEMA) 建立，全部是**新账本**：
        # 没有任何历史数据可迁移，也不需要回填。

        # v7 → v8 = ANALYSIS 换合同。
        #   - analyses 重建/封存：见上面的 _rebuild_analyses_for_v8
        #   - contents.source_meta / content_performance.updated_at /
        #     style_profile.prompt_version：由 _migrate 补列，无需回填
        #     （旧行这些字段为 NULL，语义就是「这条记录产生时还没有这个字段」）

        # v8 → v9 = STYLE 增量学习台账。
        #   - 三张新表（style_content_state / style_patterns /
        #     style_pattern_evidence）由上面的 executescript(SCHEMA) 建立；
        #     全部是**新账本**，没有历史数据可迁移。
        #   - style_profile.profile_kind 由 _migrate 补列。
        #     已有的档案一律显式标记为 legacy_min5 ——
        #     它们是旧 `MIN_CONTENT=5` 门槛 + 重算式机制产出的，
        #     **不得**冒充新版 20 篇有效档案。这里只打标签，不删任何历史。
        if previous_version < 9:
            conn.execute(
                "UPDATE style_profile SET profile_kind = 'legacy_min5' "
                " WHERE profile_kind IS NULL"
            )

        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
