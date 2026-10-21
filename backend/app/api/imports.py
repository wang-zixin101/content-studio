"""批量导入：解析上传文件 → 预览 → 提交入库。

**核心原则：导入与分析彻底解耦。**

导入只做一件事：把内容写进数据库，状态为 unprocessed。
无论 1 条、10 条还是 500 条，导入本身都不产生任何 AI 调用。
是否分析、何时分析、用实时还是 Batch，全部由用户随后显式点击决定。
"""

from __future__ import annotations

import logging
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app import repository as repo
from app.config import BATCH_THRESHOLD, settings
from app.db import get_conn
from app.schemas import ImportCommitIn, ImportResult
from app.services import batch
from app.services.importer import PERFORMANCE_KEY, ImporterError, parse_upload

logger = logging.getLogger("content_studio.imports")

router = APIRouter(prefix="/api", tags=["imports"])

MIN_LEN = 5
MAX_PREVIEW = 100


@router.post("/imports/parse")
async def parse_file(file: UploadFile = File(...)) -> dict:
    """解析上传的 JSON / TXT / Markdown / CSV，返回识别出的条目供预览。

    解析失败时给出明确原因，绝不把整份文件降级成一条正文。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")

    try:
        parsed = parse_upload(file.filename or "", data)
    except ImporterError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    texts = [item.text for item in parsed]
    return {
        "filename": file.filename,
        "count": len(parsed),
        "items": texts,
        "metadatas": [item.metadata for item in parsed],
        "preview": texts[:MAX_PREVIEW],
        "suggest_batch": batch.should_use_batch(len(parsed)),
        "batch_available": batch.available(),
        "batch_threshold": BATCH_THRESHOLD,
    }


def _failed_group(filename: str, error: str) -> dict:
    """一个文件解析失败时的占位分组：字段齐全，绝不让前端拿到残缺结构。"""
    return {
        "filename": filename,
        "ok": False,
        "error": error,
        "count": 0,
        "items": [],
        "metadatas": [],
        "authors": [],
        "missing_author_count": 0,
        "duplicate_count": 0,
        "new_count": 0,
    }


@router.post("/imports/parse-batch")
async def parse_batch(files: list[UploadFile] = File(...)) -> dict:
    """一次解析多个文件，按文件分组返回，并追加一次**只读**重复预检。

    与 /imports/parse 的关系：
    - parse_upload 仍是纯函数，本端点只做编排 —— 不把 DB 检查塞进解析器
    - 单个文件失败只影响它自己，其它文件照常返回
    - 预检结果只是**预计值**；真正以 commit 的实际去重结果为准

    重复判定与 commit 保持同一口径：先看库里已存在的 external_id，
    再看本批次**前面的文件**已经占用过的 external_id（跨文件重复）。
    没有 external_id 的内容（TXT / MD）不参与去重，与 commit 一致。
    """
    if not files:
        raise HTTPException(status_code=400, detail="没有收到任何文件")

    groups: list[dict] = []
    all_external_ids: list[str] = []

    for upload in files:
        filename = upload.filename or ""
        data = await upload.read()
        if not data:
            groups.append(_failed_group(filename, "文件为空"))
            continue

        try:
            parsed = parse_upload(filename, data)
        except ImporterError as err:
            groups.append(_failed_group(filename, str(err)))
            continue

        metadatas = [item.metadata or {} for item in parsed]
        all_external_ids.extend(
            str(meta["external_id"]).strip()
            for meta in metadatas
            if meta.get("external_id")
        )

        # 作者集合与缺失计数一律按**规范化后**的口径统计 ——
        # 预览里显示的用户名必须与最终入库的作者行一致，
        # 且 "@" / 空白这类规范化后为空的值要算作「缺作者」，
        # 否则预览说「有作者」、入库却是 NULL。
        author_set: set[str] = set()
        missing_author = 0
        for meta in metadatas:
            username = repo.normalize_author_username(
                str(meta.get("author_username") or "")
            )
            if username:
                author_set.add(username)
            else:
                missing_author += 1

        groups.append({
            "filename": filename,
            "ok": True,
            "error": None,
            "count": len(parsed),
            "items": [item.text for item in parsed],
            "metadatas": metadatas,
            "authors": sorted(author_set),
            "missing_author_count": missing_author,
            "duplicate_count": 0,
            "new_count": len(parsed),
        })

    # ---- 只读预检：一次性查出库中已有的 external_id（不写任何东西）----
    existing: set[str] = set()
    if all_external_ids:
        with get_conn(settings.db_path) as conn:
            existing = repo.find_existing_external_ids(conn, all_external_ids)

    claimed: set[str] = set()  # 本批次前面文件已经占用的 id
    for group in groups:
        if not group["ok"]:
            continue
        duplicates = 0
        new = 0
        for meta in group["metadatas"]:
            eid = str(meta.get("external_id") or "").strip()
            if not eid:
                new += 1
                continue
            if eid in existing or eid in claimed:
                duplicates += 1
                continue
            claimed.add(eid)
            new += 1
        group["duplicate_count"] = duplicates
        group["new_count"] = new

    ok_groups = [g for g in groups if g["ok"]]
    totals = {
        "files": len(groups),
        "ok_files": len(ok_groups),
        "failed_files": len(groups) - len(ok_groups),
        "count": sum(g["count"] for g in ok_groups),
        "duplicate_count": sum(g["duplicate_count"] for g in groups),
        "new_count": sum(g["new_count"] for g in groups),
        "missing_author_count": sum(g["missing_author_count"] for g in groups),
    }

    return {
        "files": groups,
        "totals": totals,
        "batch_available": batch.available(),
        "batch_threshold": BATCH_THRESHOLD,
    }


def _common_author(items) -> str | None:
    """若本批内容作者一致，返回该作者，否则 None。"""
    authors = {
        str((item.metadata or {}).get("author_username")).strip()
        for item in items
        if (item.metadata or {}).get("author_username")
    }
    return authors.pop() if len(authors) == 1 else None


@router.post("/imports/commit", response_model=ImportResult)
async def commit(payload: ImportCommitIn) -> ImportResult:
    """把预览确认后的条目批量入库。

    **不触发任何分析**：全部以 unprocessed 入库后即停止。

    去重规则：有可靠 external_id 的内容以 external_id 为准（跨批次 + 批内双重去重）；
    没有 external_id 的 TXT/MD 手工内容不做去重，照常导入。
    重复项**不重复导入内容**（绝不覆盖已有的 source / author / analysis / 正文），
    但**表现数据会按采集时间更新** —— 见下方 performance_refreshed。

    Xporter 文件带表现数据时一并入库（0 与 NULL 严格区分），
    整个导入过程 0 AI。
    """
    import_batch_id = uuid4().hex
    # 本批统一的采集时间代理：Xporter 文件没有 exported_at / captured_at，
    # 因此在**一次导入开始时**生成一次，全批共用 —— 绝不每条 now()。
    # 语义是「工作台拿到这批数据的时间」，不是帖子发布时间（后者在 posted_at）。
    captured_at = repo.now_iso()
    created_ids: list[int] = []
    input_count = len(payload.items)
    duplicate_count = 0
    skipped = 0
    performance_refreshed = 0
    seen_in_file: set[str] = set()

    with get_conn(settings.db_path) as conn:
        # 一次性查出本批涉及的 external_id 中，库里已经存在的（id 也要，
        # 重复项要靠它去更新表现数据）
        incoming = [
            str((item.metadata or {}).get("external_id")).strip()
            for item in payload.items
            if (item.metadata or {}).get("external_id")
        ]
        existing = repo.map_existing_external_ids(conn, incoming)

        for item in payload.items:
            text = (item.text or "").strip()
            source = item.source or payload.source
            if len(text) < MIN_LEN:
                skipped += 1
                continue

            external_id = (item.metadata or {}).get("external_id")
            if external_id:
                eid = str(external_id).strip()
                # 跨批次重复，或同一文件内重复
                if eid in existing or eid in seen_in_file:
                    duplicate_count += 1
                    # 内容不重复导入；但已知帖子的表现数据可以按采集时间更新。
                    # 同一文件内重复的那条不入库，因此没有可更新的目标。
                    known_id = existing.get(eid)
                    if known_id is not None and item.metadata:
                        if repo.save_content_performance(
                            conn,
                            known_id,
                            item.metadata.get(PERFORMANCE_KEY),
                            captured_at=captured_at,
                        ):
                            performance_refreshed += 1
                        # 扩展来源字段走**只合并 extras** 的入口：
                        # 绝不在重复导入时重新 resolve 作者或改写任何正式列
                        repo.merge_content_source_meta_extras(
                            conn, known_id, item.metadata
                        )
                    continue
                seen_in_file.add(eid)

            try:
                cid = repo.create_content(
                    conn,
                    source,
                    text,
                    import_batch_id=import_batch_id,
                    # 导入「我的」历史作品 → 默认允许参与风格学习；
                    # 对标内容永远不允许（create_content 内部也会二次校验）
                    style_eligible=(source == "mine"),
                )
            except sqlite3.IntegrityError:
                # 并发导入撞上唯一约束：计入重复，而不是让整批失败
                duplicate_count += 1
                continue

            if source == "mine":
                # 导入的本人历史作品 = imported_self
                repo.set_provenance(conn, cid, "imported_self")
            if item.metadata:
                repo.set_content_source_meta(conn, cid, item.metadata)
                repo.save_content_performance(
                    conn,
                    cid,
                    item.metadata.get(PERFORMANCE_KEY),
                    captured_at=captured_at,
                )
            created_ids.append(cid)

        repo.create_import_batch(
            conn,
            import_batch_id=import_batch_id,
            filename=payload.filename,
            source=payload.source,
            item_count=len(created_ids),
            input_count=input_count,
            inserted_count=len(created_ids),
            duplicate_count=duplicate_count,
            skipped_count=skipped,
            author_username=_common_author(payload.items),
        )

    logger.info(
        "导入完成：batch=%s 输入 %s，新增 %s，重复跳过 %s，表现数据刷新 %s，无效跳过 %s",
        import_batch_id,
        input_count,
        len(created_ids),
        duplicate_count,
        performance_refreshed,
        skipped,
    )

    return ImportResult(
        created=len(created_ids),
        content_ids=created_ids,
        skipped=skipped,
        duplicates=duplicate_count,
        input_count=input_count,
        mode="none",
        import_batch_id=import_batch_id,
        performance_refreshed=performance_refreshed,
    )


@router.get("/imports")
def list_batches(limit: int = 50) -> dict:
    """导入记录：即使离开导入完成页，也能回来看批次与处理。"""
    with get_conn(settings.db_path) as conn:
        rows = repo.list_import_batches(conn, limit=limit)
        items = []
        for row in rows:
            bid = row["import_batch_id"]
            breakdown = repo.batch_analysis_breakdown(conn, bid)
            analyzed = breakdown.get("analyzed", 0)
            unanalyzed = sum(n for s, n in breakdown.items() if s != "analyzed")
            active = repo.find_active_run(conn, bid)
            items.append({
                "import_batch_id": bid,
                "filename": row["filename"],
                "source": row["source"],
                "author_username": row["author_username"],
                "created_at": row["created_at"],
                "input_count": row["input_count"] or row["item_count"],
                "inserted_count": row["inserted_count"] or row["item_count"],
                "duplicate_count": row["duplicate_count"] or 0,
                "skipped_count": row["skipped_count"] or 0,
                # 当前实际还在库里的内容数（可能被删除）
                "remaining": sum(breakdown.values()),
                "analyzed": analyzed,
                "unanalyzed": unanalyzed,
                "breakdown": breakdown,
                # 运行状态：供 UI 决定显示「暂停分析」还是「继续分析」
                "is_running": active is not None,
                "run_kind": active["kind"] if active else None,
                "run_status": active["status"] if active else None,
            })
    return {"items": items, "total": len(items)}
