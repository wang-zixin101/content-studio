"""集中配置：全部来自环境变量 / .env，业务代码不读 os.environ。

运行根（Desktop V1 起拆分为两个，**不要**再混用）：

    RESOURCE_ROOT   只读资源根
        dev    = content-studio/
        frozen = sys._MEIPASS（onedir 下即 <exe 同级>/_internal）

    APP_DATA_ROOT   可写数据根
        dev    = content-studio/
        frozen = %LOCALAPPDATA%\\ContentStudio

冻结后 `__file__` 指向 bundle 内部，若还用单一 PROJECT_ROOT 推路径，
就会在安装目录（可能是 Program Files）里建 SQLite 并去找 dist —— 两者都会失败。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

APP_DIR_NAME = "ContentStudio"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结产物中。"""
    return bool(getattr(sys, "frozen", False))


def _dev_root() -> Path:
    # app/config.py -> app/ -> backend/ -> content-studio/
    return Path(__file__).resolve().parents[2]


def _frozen_resource_root() -> Path:
    # onedir: <exe 同级>/_internal ；onefile: 临时解包目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def _frozen_app_data_root() -> Path:
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / APP_DIR_NAME
    # 兜底：即使没有 LOCALAPPDATA，也绝不落到程序安装目录
    return Path.home() / f".{APP_DIR_NAME}"


FROZEN = is_frozen()

# 只读：打包进去的前端产物等
RESOURCE_ROOT = _frozen_resource_root() if FROZEN else _dev_root()
# 可写：SQLite / .env / logs
APP_DATA_ROOT = _frozen_app_data_root() if FROZEN else RESOURCE_ROOT

# 兼容别名：语义等于「资源根」。旧代码与测试仍引用这个名字。
PROJECT_ROOT = RESOURCE_ROOT

BACKEND_DIR = RESOURCE_ROOT / "backend"
DATA_DIR = APP_DATA_ROOT / "data"
LOG_DIR = APP_DATA_ROOT / "logs"

# 只加载唯一一个 .env：
#   dev    -> content-studio/backend/.env
#   frozen -> %LOCALAPPDATA%\ContentStudio\.env（backend/.env 绝不能打进 bundle）
ENV_FILE = (APP_DATA_ROOT / ".env") if FROZEN else (BACKEND_DIR / ".env")
load_dotenv(ENV_FILE)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


# ---------------------------------------------------------------- 产品常量

# Personalization 状态阈值。
# 注意：这是**产品默认值**，不是经过验证的科学结论，不要在界面或文案里当成定律宣传。
COLD_START_MAX = 4                        # 0 ~ 4 个有效风格样本 → COLD_START
LEARNING_MAX = 9                          # 5 ~ 9 → LEARNING
PERSONALIZED_MIN = 10                     # >= 10 → 可能 PERSONALIZED
MIN_TOPICS_FOR_PERSONALIZED = 3           # 进入 PERSONALIZED 还要求主题多样性
MIN_CONTENT_TYPES_FOR_PERSONALIZED = 2    # 以及内容类型多样性

# Prompt 版本：记录在生成结果里，便于回溯「是哪版提示词产出的」
# ANALYSIS 的版本轨迹：
#   v2 → v3    任务 04-A：换成新的四组字段合同
#   v3 → v3.1  04-A 定点修正：只增补**判别规则**（分类边界），
#              字段树、枚举、Schema 全部不变 ——
#              因此 ANALYSIS_SCHEMA_VERSION 与 ANALYSIS_SCHEMA_HASH 都**不动**。
#              这是提示词语义修订，不得伪装成 Schema 变动，也不重新迁移数据库。
#   v2 → v3    任务 04-C：WRITE 升级为面向中文 X 传播表现的创作引擎 ——
#               新增流量目标 / 重构自由度 / 输出纪律三段跨状态规则，
#               三候选从「保守程度三档」改为「三种传播机制」，
#               并新增五种创作输入路径（含参考作者自主选题）。
#               只改提示词与组装器，不新增 Schema、不迁移数据库。
WRITE_PROMPT_VERSION = "v3"
ANALYSIS_PROMPT_VERSION = "v3.1"
# 04-B：STYLE 升到 v3（分层证据 + supporting_samples 逐字引文）。
# 与 ANALYSIS_PROMPT_VERSION=v3.1 独立，不要因为 ANALYSIS 升版就顺手改这里。
STYLE_PROMPT_VERSION = "v3"

# ------------------------------------------------------------------
# 分析相关的**四个**独立版本标识（Stage 12 起明确分开，不再混用）
# ------------------------------------------------------------------
#   ANALYSIS_PROMPT_VERSION          Prompt 措辞版本（上面的 "v3.1"）
#   ANALYSIS_SCHEMA_VERSION          字段结构版本 —— 只要字段树变了就必须改
#   ANALYSIS_SCHEMA_HASH             ANALYSIS_SPEC 的 canonical JSON 指纹
#   EXTERNAL_ANALYSIS_FORMAT_VERSION 外部交换文件的**信封**格式版本
#
# Schema hash 由 services/analysis_spec.py 计算，这里不重复定义。
# 注意 hash 只覆盖 version + fields + enums，**不含 Prompt 版本** ——
# 提示词修订不会让外部交换文件的指纹失效，这是刻意的。
ANALYSIS_SCHEMA_VERSION = "analysis-v3"

# 外部信封结构本身（content_id/content_hash/_meta 的位置与含义）没有变化，
# 变的只是它承载的 analysis 合同，因此 format_version 保持 1。
EXTERNAL_ANALYSIS_FORMAT_VERSION = 1

# ------------------------------------------------------------------
# 完成 Token：**不设人为上限**
# ------------------------------------------------------------------
# 纪律：本项目的四条 AI 调用链都**不主动传入** max_tokens /
# max_completion_tokens，一律使用供应商默认值。
#
# 依据（小米 MiMo 开放平台官方 OpenAI 兼容文档）：
#   max_completion_tokens  integer | null  可选
#     "An upper bound for the number of tokens that can be generated for a
#      completion, including visible output tokens and reasoning tokens."
#     mimo-v2.6-flash:            default 131072
#     mimo-v2.6-pro:              default 131072   ← 本项目使用
#     mimo-v2.6-pro-ultraspeed:   default 131072
#     mimo-v2.5-pro:              default 131072
#     mimo-v2.5:                  default 32768
#     Required range: [1, 131072]
#   来源：https://platform.xiaomimimo.com/docs/en-US/api/chat/openai-api
#
# 为什么不自己写一个「够大的数」：
#   1. 我们不知道模型需要多少 —— 推理 token 逐次浮动，写死任何值都可能
#      比供应商默认更早截断（旧版 1500/2000/3000/4000 就是这样栽的）；
#   2. 供应商默认已经是该模型的最大输出上限，自己写只会更小或持平；
#   3. 一旦上游调整默认值，不写死的一方自动跟着受益。
#
# 截断仍然被严密监控（不依赖上限值本身）：
#   ANALYSIS_DETECT_TRUNCATION 打开时，finish_reason 命中
#   length / max_tokens / max_output_tokens 即判定截断失败并报出
#   completion_tokens 与 reasoning_tokens —— 上限是被谁截的、截了多少，
#   日志里看得一清二楚。

# 截断检测开关：finish_reason 为 length / max_tokens 时判定为截断失败
ANALYSIS_DETECT_TRUNCATION = True

# 单条分析的**最大请求次数**（含首次）。
#   1 → 一次请求定生死（最省费用，但结构问题无法自愈）
#   2 → 首次失败时带着「具体错在哪」再问一次（默认）
# 只对「解析失败 / 合同不符」生效；**截断不在此列** —— 同参数重发只会再截断。
ANALYSIS_MAX_ATTEMPTS = 2

# 外部交换信封标识（写进 task / result JSONL 的 _meta）
EXTERNAL_TASK_FORMAT = "content-studio-analysis-task"
EXTERNAL_RESULT_FORMAT = "content-studio-analysis-result"

# 外部模型名落库前缀：外部结果绝不能被误认为内部模型产出
EXTERNAL_MODEL_PREFIX = "external:"
EXTERNAL_MODEL_UNKNOWN = "external:unknown"
EXTERNAL_MODEL_MAX_LEN = 64

# 批量分析：达到该条数时，导入默认走 Batch Pro（用户仍可强制实时）
BATCH_THRESHOLD = 20

# ------------------------------------------------------------------
# 「我的」V1：目标与账本体系（0 AI，纯本地记录）
# ------------------------------------------------------------------
# 合格浏览量（qualified impressions）= 用户**手动录入**的快照值。
# 它与 X 后台 CSV 里的 impressions **不是同一个指标**，两者不互相推算、
# 不自动同步 —— 这是刻意的产品边界，不要让界面暗示它们等价。
MINE_TARGET_QUALIFIED_IMPRESSIONS = 500000
MINE_GOAL_DURATION_DAYS = 90


@dataclass
class Settings:
    """运行时配置。所有字段均可通过环境变量覆盖。

    非 frozen：允许测试注入独立数据库路径，避免污染真实库。
    """

    # ---- AI ----
    ai_provider: str = "openai_compat"
    ai_base_url: str = "https://api.xiaomimimo.com/v1"
    ai_api_key: str = ""
    model_analysis: str = "mimo-v2.6-pro"
    model_rewrite: str = "mimo-v2.6-pro"
    model_style: str = "mimo-v2.6-pro"
    model_batch: str = "mimo-v2.6-pro"
    ai_timeout: int = 180

    # ---- Batch（独立端点，不得默认复用实时 Base URL）----
    # MiMo Batch 使用独立 Base URL，例如 https://batch-api-<region>.xiaomimimo.com/v1
    ai_batch_base_url: str = ""
    ai_batch_api_key: str = ""

    # ---- 存储 ----
    db_path: Path = DATA_DIR / "content_studio.db"

    # ---- 服务 ----
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    @property
    def ai_configured(self) -> bool:
        """是否已配置可用的 AI 密钥。未配置时分析任务会明确失败而非静默。"""
        return bool(self.ai_api_key and self.ai_api_key.strip())

    @property
    def batch_key(self) -> str:
        """Batch 密钥：未单独配置时复用实时密钥（普通 sk- 密钥）。"""
        return (self.ai_batch_api_key or "").strip() or self.ai_api_key

    @property
    def batch_configured(self) -> bool:
        """Batch 是否可用。

        必须**显式**配置独立的 Batch Base URL——不允许默认复用实时 AI_BASE_URL，
        因为 MiMo 的 Batch 端点是独立域名。
        """
        return bool((self.ai_batch_base_url or "").strip()) and bool(self.batch_key)


def load_settings() -> Settings:
    db_env = os.getenv("DB_PATH", "").strip()
    db_path = Path(db_env) if db_env else (DATA_DIR / "content_studio.db")

    return Settings(
        ai_provider=os.getenv("AI_PROVIDER", "openai_compat").strip(),
        ai_base_url=os.getenv("AI_BASE_URL", "https://api.xiaomimimo.com/v1").strip(),
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        model_analysis=os.getenv("AI_MODEL_ANALYSIS", "mimo-v2.6-pro").strip(),
        model_rewrite=os.getenv("AI_MODEL_REWRITE", "mimo-v2.6-pro").strip(),
        model_style=os.getenv("AI_MODEL_STYLE", "mimo-v2.6-pro").strip(),
        model_batch=os.getenv("AI_MODEL_BATCH", "mimo-v2.6-pro").strip(),
        ai_batch_base_url=os.getenv("AI_BATCH_BASE_URL", "").strip(),
        ai_batch_api_key=os.getenv("AI_BATCH_API_KEY", "").strip(),
        ai_timeout=int(os.getenv("AI_TIMEOUT", "180")),
        db_path=db_path,
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=int(os.getenv("PORT", "8000")),
        cors_origins=_split_csv(
            os.getenv(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            )
        ),
    )


settings = load_settings()
