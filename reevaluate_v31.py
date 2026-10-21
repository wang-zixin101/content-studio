"""04-A 定点修正：只读重算逐项验收（不再调用任何 AI）。

`validate_v31.py` 跑完后，如果只是**判据**需要修正（例如把中文数字
「九个」「三个」算成实际数字），没必要再花一次模型调用 ——
直接对已经落库的 v3 / v3.1 结果重新判定即可。

用法：
    ./backend/.venv/Scripts/python.exe reevaluate_v31.py <source_db> <work_db>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "backend"))

from validate_v31 import (  # noqa: E402
    CHECKS,
    KNOWN_ISSUES,
    UNIVERSAL,
    DEFAULT_IDS,
)


def open_ro(db_path: str) -> sqlite3.Connection:
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load(db_path: str, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    conn = open_ro(db_path)
    try:
        for cid in ids:
            r = conn.execute(
                "SELECT analysis_json, prompt_version FROM analyses "
                " WHERE content_id = ?",
                (cid,),
            ).fetchone()
            if r is None:
                continue
            out[cid] = {
                "analysis": json.loads(r["analysis_json"]),
                "prompt_version": r["prompt_version"],
            }
    finally:
        conn.close()
    return out


def evaluate(label: str, data: dict[int, dict], ids: list[int]) -> tuple[list[dict], int, int]:
    print("=" * 100)
    print(f"{label}")
    print("=" * 100)
    verdicts = []
    total = fixed = 0
    for cid in ids:
        if cid not in data:
            verdicts.append({"content_id": cid, "status": "缺失", "items": []})
            continue
        checks = list(KNOWN_ISSUES.get(cid, [])) + [
            {"key": lbl, "check": k} for k, lbl in UNIVERSAL
        ]
        seen, items = set(), []
        for c in checks:
            if c["check"] in seen:
                continue
            seen.add(c["check"])
            ok, detail = CHECKS[c["check"]](data[cid]["analysis"])
            items.append({"issue": c["key"], "fixed": ok, "detail": detail})
        n_ok = sum(1 for i in items if i["fixed"])
        total += len(items)
        fixed += n_ok
        verdicts.append({"content_id": cid, "status": f"{n_ok}/{len(items)}",
                         "items": items})
        print(f"\ncontent {cid}：{n_ok}/{len(items)}  (prompt={data[cid]['prompt_version']})")
        for i in items:
            print(f"   {'✓' if i['fixed'] else '✗'} {i['issue']}")
            print(f"       {i['detail']}")
    print(f"\n【{label}】小计：{fixed}/{total}")
    return verdicts, fixed, total


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    source_db, work_db = sys.argv[1], sys.argv[2]
    ids = list(DEFAULT_IDS)

    old = load(source_db, ids)
    new = load(work_db, ids)

    _, b_fixed, b_total = evaluate("v3（基线，仅只读重算）", old, ids)
    _, n_fixed, n_total = evaluate("v3.1（修正后，仅只读重算）", new, ids)

    print("\n" + "=" * 100)
    print(f"v3 基线：{b_fixed}/{b_total}　→　v3.1：{n_fixed}/{n_total}")

    out = HERE / "task04a_deliverables" / "v3.1-只读重算验收.json"
    out.write_text(
        json.dumps(
            {
                "basis": "只读重算，不调用 AI；判据修正：中文数字计入实际数字",
                "source_db": source_db,
                "work_db": work_db,
                "baseline": {"fixed": b_fixed, "total": b_total},
                "v31": {"fixed": n_fixed, "total": n_total},
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已写出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
