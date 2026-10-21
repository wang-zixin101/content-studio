"""「我的」V1：目标 / 合格浏览量快照 / 账号日指标。

------------------------------------------------------------------
0 AI
------------------------------------------------------------------
本模块**没有任何 provider 调用**。
目标计算是纯日期/整数运算，CSV 导入是纯字符串解析 + SQL 写入。

------------------------------------------------------------------
与既有体系的边界
------------------------------------------------------------------
- 不写 import_batches、不进「导入记录」、没有批次撤销。
  （理由与 services/external_analysis.py 一致：这里搬的是**账号指标**，
  不是内容，塞进导入体系会污染撤销语义。）
- 合格浏览量快照与 account_daily_metrics.impressions **完全独立**：
  不做换算、不自动预填、不互相推算。这是刻意的产品边界。
- 不参与内容分析、风格档案、personalization。

------------------------------------------------------------------
目标语义（已锁定）
------------------------------------------------------------------
目标是**绝对值**：在 90 天内让合格浏览量总量达到 500,000。
    progress     = current / TARGET          （不是「相对基线的增量」）
    status       = achieved / expired / active
    achieved     优先于 expired —— 达标就是达标，不看是否超期。
    elapsed_days = (today - start_date).days
    elapsed <= 90 → active（第 90 天当天仍算 active，remaining_days = 0）
    elapsed  > 90 且未达标 → expired

baseline 只作为**图表起点**，不进入 progress 的分母，
也不代表「已完成的工作量」。

------------------------------------------------------------------
baseline >= TARGET 的边界（已锁定）
------------------------------------------------------------------
建目标时基线就已经达到/超过 500,000，说明这个目标**在建立时即达成**：
    status                  = achieved
    remaining               = 0
    original_daily_required = 0     （一开始就没有缺口，无从摊派）
    current_daily_required  = None
    planned_today           = TARGET
    ahead_behind            = current - TARGET
并且**目标图的目标线固定为 TARGET=500000**：
    绝不能画一条从 520,000 向下走到 500,000 的「基准线」——
    那在语义上是「要求用户把合格浏览量做回去」，是错的。

------------------------------------------------------------------
可编辑的计划参数（已锁定）
------------------------------------------------------------------
TARGET = 500,000 与 DURATION = 90 天是**固定规则**，用户改不了，
它们也只是 config 里的常量，根本不在 creator_goal 表里。

start_date 与 baseline_qualified_impressions 是**用户自己的计划参数**，
建完可以反悔，走 PATCH /api/mine/goal 就地改 —— 单例不变、不留历史、
不新建目标。改完所有派生量（deadline / elapsed / remaining /
original_daily_required / planned_today / ahead_behind /
current_daily_required / 90 天目标线）立刻按新起点重算。

current 的取值口径（编辑 goal 后依然成立）：
    只认 recorded_at >= 当前 start_date 的最新快照；
    窗口内没有快照 → fallback 到 baseline。
编辑 goal **绝不**删除、修改或补写任何快照。
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta
from typing import Any

from app import repository as repo
from app.config import (
    MINE_GOAL_DURATION_DAYS,
    MINE_TARGET_QUALIFIED_IMPRESSIONS,
    settings,
)
from app.db import get_conn
from app.services.errors import ServiceError
from app.services.importer import _decode  # 复用 BOM / gb18030 兼容

# ------------------------------------------------------------------
# Account Overview CSV 契约
# ------------------------------------------------------------------
# 真实导出表头（用户实测提供，逐字固定）：
#   Date,Impressions,Likes,Engagements,Bookmarks,Shares,New follows,
#   Unfollows,Replies,Reposts,Profile visits,Create Post,
#   Video views,Media views
#
# 14 列 = 1 个日期列 + 13 个指标列。
# 表头**逐字**匹配（区分大小写），因为这是外部文件的硬契约；
# 一旦放宽成模糊匹配，未知列就会静默进入或被静默丢弃。

CSV_DATE_HEADER = "Date"

# CSV 表头 → 数据库列名（顺序与 CSV 列顺序一致）
CSV_HEADER_TO_COLUMN: dict[str, str] = {
    "Impressions": "impressions",
    "Likes": "likes",
    "Engagements": "engagements",
    "Bookmarks": "bookmarks",
    "Shares": "shares",
    "New follows": "new_follows",
    "Unfollows": "unfollows",
    "Replies": "replies",
    "Reposts": "reposts",
    "Profile visits": "profile_visits",
    "Create Post": "create_post",
    "Video views": "video_views",
    "Media views": "media_views",
}

# 必填表头 = 日期列 + 全部 13 个指标列（共 14）
REQUIRED_CSV_HEADERS: tuple[str, ...] = (CSV_DATE_HEADER, *CSV_HEADER_TO_COLUMN)


class AccountMetricsCsvError(ServiceError):
    """CSV 导入失败：携带行号与列名，整文件回滚，不写库。"""

    def __init__(self, message: str, row: int | None = None,
                 column: str | None = None) -> None:
        super().__init__(message)
        self.row = row
        self.column = column


# ------------------------------------------------------------------
# 解析（纯函数，不写库）
# ------------------------------------------------------------------

def _normalize_date_cell(raw: str) -> str:
    """把 CSV 日期单元格规范成 YYYY-MM-DD。

    接受：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD，
    以及带时间部分的形式（取空格或 T 之前的一段）。
    其它一律拒绝 —— 宁可明确报错，也不做会导致错位的主观猜测。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("日期为空")
    head = text.replace("T", " ").split(" ")[0]
    head = head.replace("/", "-").replace(".", "-")
    # date.fromisoformat 在 3.11+ 也接受 YYYYMMDD，这里先兜住长度避免误判
    if len(head) != 10:
        raise ValueError(f"无法识别的日期「{text}」（需要 YYYY-MM-DD）")
    try:
        return date.fromisoformat(head).isoformat()
    except ValueError as err:
        raise ValueError(f"无法识别的日期「{text}」（需要 YYYY-MM-DD）") from err


def _parse_metric_cell(raw: str) -> int | None:
    """把指标单元格解析成非负整数。

    空白 → None（未知，不是 0）
    "0"  → 0
    "1,234" → 1234（CSV 导出常见的千分位写法）
    其它 / 负数 → ValueError
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        value = int(text)
    except ValueError as err:
        raise ValueError(f"不是整数「{raw.strip()}」") from err
    if value < 0:
        raise ValueError(f"不能为负数「{raw.strip()}」")
    return value


def parse_account_metrics_csv(data: bytes) -> list[dict[str, Any]]:
    """严格解析 Account Overview CSV。**不写库**，任何错误都抛异常。

    表头必须逐字命中 14 列；多一列、少一列、重复列都直接拒绝 ——
    因为缺列会导致整列数据被静默当成"未知"。
    """
    text = _decode(data)
    if not text.strip():
        raise AccountMetricsCsvError("文件为空")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration as err:
        raise AccountMetricsCsvError("文件为空") from err

    header = [h.strip() for h in header]

    # 校验顺序 = 从最具体的结构异常到最泛的缺失：
    # 重复列 → 不支持的列 → 缺少必需的列。
    # 这样「多了一列」不会被误报成「少了 12 列」。

    # 重复表头：csv 模块会静默保留最后一列的值，等于悄悄丢数据。
    seen: set[str] = set()
    duplicated = [h for h in header if h in seen or seen.add(h)]
    if duplicated:
        raise AccountMetricsCsvError(
            f"存在重复的列：{'、'.join(dict.fromkeys(duplicated))}"
        )

    unsupported = [h for h in header if h not in REQUIRED_CSV_HEADERS]
    if unsupported:
        raise AccountMetricsCsvError(
            f"存在不支持的列：{'、'.join(unsupported)}"
        )

    missing = [h for h in REQUIRED_CSV_HEADERS if h not in header]
    if missing:
        raise AccountMetricsCsvError(
            f"缺少必需的列：{'、'.join(missing)}"
        )

    rows: list[dict[str, Any]] = []
    for line_no, cells in enumerate(reader, start=2):   # 第 1 行是表头
        if not any((c or "").strip() for c in cells):
            continue                                    # 忽略纯空行
        if len(cells) != len(header):
            raise AccountMetricsCsvError(
                f"第 {line_no} 行有 {len(cells)} 个字段，表头有 {len(header)} 个",
                row=line_no,
            )

        record = dict(zip(header, cells))
        row: dict[str, Any] = {}
        try:
            row["metric_date"] = _normalize_date_cell(record[CSV_DATE_HEADER])
        except ValueError as err:
            raise AccountMetricsCsvError(
                str(err), row=line_no, column=CSV_DATE_HEADER
            ) from err

        for csv_header, column in CSV_HEADER_TO_COLUMN.items():
            try:
                row[column] = _parse_metric_cell(record[csv_header])
            except ValueError as err:
                raise AccountMetricsCsvError(
                    str(err), row=line_no, column=csv_header
                ) from err

        rows.append(row)

    if not rows:
        raise AccountMetricsCsvError("没有数据行")
    return rows


# ------------------------------------------------------------------
# 账号日指标：导入 / 查询
# ------------------------------------------------------------------

def import_account_metrics(data: bytes) -> dict[str, Any]:
    """导入 Account Overview CSV。

    **整文件一个事务**：任何一行解析失败 → 全部回滚，库里不留半成品。
    已存在的日期 → 整行覆盖（不是合并、不是累加）。
    """
    rows = parse_account_metrics_csv(data)          # 解析失败即抛出

    with get_conn(settings.db_path) as conn:
        inserted = 0
        updated = 0
        for row in rows:
            if repo.upsert_account_daily_metric(conn, row):
                updated += 1
            else:
                inserted += 1

    dates = [r["metric_date"] for r in rows]
    return {
        "input_rows": len(rows),
        "inserted": inserted,
        "updated": updated,
        "date_min": min(dates),
        "date_max": max(dates),
    }


def query_account_metric(metric: str) -> dict[str, Any]:
    """某指标的完整序列 + 最新 / 上一次 / 差值。

    无分页 —— 账号日指标最多几百行，一次给前端画图即可。
    """
    if not repo.mine_metric_supported(metric):
        raise ServiceError(f"不支持的指标「{metric}」")

    with get_conn(settings.db_path) as conn:
        items = repo.list_mine_metric(conn, metric)

    latest = items[-1] if items else None
    previous = items[-2] if len(items) >= 2 else None

    difference: int | None = None
    if latest and previous:
        difference = latest["value"] - previous["value"]

    return {
        "metric": metric,
        "items": items,
        "latest": latest,
        "previous": previous,
        "difference": difference,
    }


# ------------------------------------------------------------------
# 合格浏览量快照 CRUD
# ------------------------------------------------------------------

def list_snapshots() -> list[dict[str, Any]]:
    with get_conn(settings.db_path) as conn:
        return repo.list_qualified_snapshots(conn)


def add_snapshot(recorded_at: str, qualified_impressions: int) -> dict[str, Any]:
    _require_date(recorded_at)
    _require_non_negative(qualified_impressions)
    with get_conn(settings.db_path) as conn:
        return repo.insert_qualified_snapshot(
            conn, recorded_at, qualified_impressions
        )


def edit_snapshot(
    snapshot_id: int,
    recorded_at: str | None,
    qualified_impressions: int | None,
) -> dict[str, Any]:
    if recorded_at is not None:
        _require_date(recorded_at)
    if qualified_impressions is not None:
        _require_non_negative(qualified_impressions)

    with get_conn(settings.db_path) as conn:
        updated = repo.update_qualified_snapshot(
            conn, snapshot_id, recorded_at, qualified_impressions
        )
    if not updated:
        raise ServiceError(f"快照 {snapshot_id} 不存在", status_code=404)
    return updated


def remove_snapshot(snapshot_id: int) -> bool:
    with get_conn(settings.db_path) as conn:
        deleted = repo.delete_qualified_snapshot(conn, snapshot_id)
    if not deleted:
        raise ServiceError(f"快照 {snapshot_id} 不存在", status_code=404)
    return True


def _require_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise ServiceError("日期格式需要 YYYY-MM-DD") from err


def _require_non_negative(value: int) -> None:
    if value < 0:
        raise ServiceError("合格浏览量不能为负数")


# ------------------------------------------------------------------
# 目标
# ------------------------------------------------------------------

def create_goal(start_date: str, baseline: int) -> dict[str, Any]:
    """建立目标。已存在 → 409（目标只有一个，不静默覆盖）。"""
    _require_date(start_date)
    _require_non_negative(baseline)

    with get_conn(settings.db_path) as conn:
        if repo.get_creator_goal(conn):
            raise ServiceError("目标已存在", status_code=409)
        repo.insert_creator_goal(conn, start_date, baseline)
        return read_goal(conn)


def get_goal() -> dict[str, Any]:
    with get_conn(settings.db_path) as conn:
        return read_goal(conn)


def update_goal(start_date: str, baseline: int) -> dict[str, Any]:
    """就地修改目标的两个计划参数（单例，永远是同一条）。

    刻意**不做**的事：
      - 不新建第二条目标、不写历史表 —— 单例就是单例；
      - 不碰 qualified_impression_snapshots（不删、不改、不自动补一条）；
      - 不碰 account_daily_metrics；
      - 不把 baseline 写成一条快照（baseline 是计划参数，不是账本记录）。

    TARGET / DURATION 依然是常量，改不了。
    返回按新起点**立刻重算**过的完整目标状态。
    """
    _require_date(start_date)
    _require_non_negative(baseline)

    with get_conn(settings.db_path) as conn:
        if not repo.update_creator_goal(conn, start_date, baseline):
            raise ServiceError("目标不存在，无法修改", status_code=404)
        return read_goal(conn)


def read_goal(conn: sqlite3.Connection) -> dict[str, Any]:
    """读目标 + 现算进度。**不缓存** —— 快照随时可能改。"""
    goal = repo.get_creator_goal(conn)
    if not goal:
        return {
            "exists": False,
            "goal": None,
            "target": MINE_TARGET_QUALIFIED_IMPRESSIONS,
            "duration_days": MINE_GOAL_DURATION_DAYS,
            "baseline": None,
            "current": None,
            "gained": None,
            "remaining": None,
            "progress": None,
            "status": None,
            "elapsed_days": None,
            "remaining_days": None,
            "current_daily_required": None,
            "original_daily_required": None,
            "planned_today": None,
            "ahead_behind": None,
            "snapshots": [],
            "chart": {"line": [], "snapshots": []},
        }

    snapshots = repo.list_qualified_snapshots(conn)
    return compute_goal_state(goal, snapshots)


def compute_goal_state(
    goal: dict[str, Any], snapshots: list[dict[str, Any]]
) -> dict[str, Any]:
    """纯计算：目标 + 快照 → 进度。**不读库、不写库**，便于直接测试。"""
    target = MINE_TARGET_QUALIFIED_IMPRESSIONS
    duration = MINE_GOAL_DURATION_DAYS

    start = date.fromisoformat(goal["start_date"])
    today = date.today()

    elapsed_raw = (today - start).days
    elapsed_days = max(0, elapsed_raw)
    # 未来才开始的目标：期限仍是完整的 duration，不该显示成「已过期」。
    remaining_days = max(0, min(duration, duration - elapsed_raw))

    baseline = int(goal["baseline_qualified_impressions"])
    # 只有 recorded_at >= start_date 的快照才算「本期的进度」。
    # 起点之前的旧记录一律**不删不改**，但不再参与 current ——
    # 所以把 start_date 往后改，进度会立刻退回 baseline 重新起算。
    # 窗口内一条都没有 → fallback 到 baseline。
    window = snapshots_in_window(snapshots, start)
    current = int(window[-1]["qualified_impressions"]) if window else baseline

    progress = 0.0 if target <= 0 else min(1.0, max(0.0, current / target))

    # 基线在建立目标时就已经达到/超过目标值：这个目标**建立即达成**。
    baseline_reached = baseline >= target

    if baseline_reached or current >= target:
        status = "achieved"
    elif elapsed_raw > duration:
        status = "expired"
    else:
        status = "active"

    # 已达成就没有缺口可追；未达成时才算剩余。
    remaining = 0 if status == "achieved" else max(0, target - current)

    # 建目标时按 90 天匀速摊派的日均要求。
    # baseline >= TARGET 时初始缺口为 0 → 日均要求也是 0（不是"每天要做 0"，
    # 而是**本来就没有需要摊派的工作量**）。
    gap_at_start = max(0, target - baseline)
    original_daily_required = -(-gap_at_start // duration) if duration > 0 else 0

    # 目标线在今天这一点上的计划值。
    # baseline >= TARGET 时目标线是常数 TARGET，所以 planned_today = TARGET。
    plan_line = build_plan_line(baseline, target, duration)
    planned_today = plan_line[min(max(elapsed_days, 0), duration)]

    # 剩余天数为 0（今天截止）时**不给日均要求** ——
    # 那会算成除零或"今天要补 50 万"这种没有意义、只会制造焦虑的数字。
    current_daily_required: int | None = None
    if status == "active" and remaining_days > 0 and remaining > 0:
        current_daily_required = -(-remaining // remaining_days)  # ceil

    return {
        "exists": True,
        "goal": goal,
        "target": target,
        "duration_days": duration,
        "baseline": baseline,
        "current": current,
        "gained": current - baseline,
        "remaining": remaining,
        "progress": progress,
        "status": status,
        "elapsed_days": elapsed_days,
        "remaining_days": remaining_days,
        "current_daily_required": current_daily_required,
        "original_daily_required": original_daily_required,
        "planned_today": planned_today,
        "ahead_behind": current - planned_today,
        "snapshots": snapshots,
        "chart": build_goal_chart(goal, snapshots),
    }


def snapshots_in_window(
    snapshots: list[dict[str, Any]], start: date
) -> list[dict[str, Any]]:
    """只留 recorded_at >= start 的快照（保持输入的顺序）。

    这是「current 只认本期记录」的唯一判定入口，图表也复用同一把尺子 ——
    否则会出现「头部数字说 4700、图上最后一个点却是 10000」的自相矛盾。
    """
    return [s for s in snapshots if _snapshot_date(s) >= start]


def _snapshot_date(snap: dict[str, Any]) -> date:
    return date.fromisoformat(str(snap["recorded_at"])[:10])


def build_plan_line(baseline: int, target: int, duration: int) -> list[int]:
    """目标线的逐日计划值（duration + 1 个点，day 0 ~ day duration）。

    baseline <  target → 从 baseline 线性走到 target（day 0 = baseline）。
    baseline >= target → **常数 target**，全程不下降。

    第二条是关键：如果基线已经 520,000，还画一条从 520,000 降到 500,000
    的线，语义就变成了「要求用户把合格浏览量做回去」，是错的。
    """
    if duration <= 0:
        return [target]
    if baseline >= target:
        return [target] * (duration + 1)
    return [
        round(baseline + (target - baseline) * day / duration)
        for day in range(duration + 1)
    ]


def build_goal_chart(
    goal: dict[str, Any], snapshots: list[dict[str, Any]]
) -> dict[str, Any]:
    """目标图数据：一条 90 天**目标线** + 本期真实快照点。

    目标线由 build_plan_line 决定：baseline < target 时线性上升，
    baseline >= target 时恒为 target（**不下降**）。

    快照点与 current 用同一把尺子（start_date 之后的才算本期），
    day 仍是「相对 start_date 的真实天数」——**不裁剪**，
    落在 0~90 之外的日子照样按真实 day 画出来。

    刻意**不做**：
      - 不插值：没有快照的日子不补点；
      - 不预测：不根据已有斜率外推未来；
      - 不平滑：快照点就是用户录入的原始值。
    """
    target = MINE_TARGET_QUALIFIED_IMPRESSIONS
    duration = MINE_GOAL_DURATION_DAYS
    start = date.fromisoformat(goal["start_date"])
    baseline = int(goal["baseline_qualified_impressions"])

    values = build_plan_line(baseline, target, duration)
    line: list[dict[str, Any]] = [
        {
            "day": day,
            "date": (start + timedelta(days=day)).isoformat(),
            "value": values[day],
        }
        for day in range(len(values))
    ]

    # 与 current 同一把尺子：起点之前的旧记录不进图，
    # 避免「头部数字 4700、图上最后一点 10000」这种自相矛盾。
    points: list[dict[str, Any]] = []
    for snap in snapshots_in_window(snapshots, start):
        recorded = _snapshot_date(snap)
        points.append(
            {
                "day": (recorded - start).days,
                "date": recorded.isoformat(),
                "value": int(snap["qualified_impressions"]),
                "id": snap["id"],
            }
        )

    return {"line": line, "snapshots": points}
