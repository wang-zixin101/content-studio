"""ANALYSIS V3 候选的兼容性核查（0 AI / 0 网络）。

核查项：
    1. 候选文件可被原样导入（不改动一个字符）
    2. V3 的 JSON 输出模板是否**真的可被 json.loads 解析**（V2 的 Hint 是伪 JSON）
    3. V3 模板的字段路径集合与 ANALYSIS_V2_SPEC 是否**逐字段一致**（59/59）
    4. V3 模板的标量/列表默认值是否符合校验器要求（null / []）
    5. 两条 builder 的消息结构（system / user）与体积对比
    6. V3 是否触碰了任何禁止项（schema hash / schema version / 字段名）

只读：不修改 app/ 下任何文件。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

CANDIDATE = HERE / "candidate" / "analysis_v3_candidate.py"

from app.prompts import (  # noqa: E402
    ANALYSIS_SCHEMA_HINT as V2_HINT,
    ANALYSIS_SYSTEM as V2_SYSTEM,
    build_analysis_messages as v2_build,
)
from app.services.analysis_spec import (  # noqa: E402
    ANALYSIS_SCHEMA_HASH,
    spec_field_paths,
)
from app.config import ANALYSIS_SCHEMA_VERSION  # noqa: E402

spec = importlib.util.spec_from_file_location("v3cand", CANDIDATE)
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)

ok_count = 0
fail_count = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if ok:
        ok_count += 1
    else:
        fail_count += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail else ""))


print("=" * 78)
print("ANALYSIS V3 候选兼容性核查")
print("=" * 78)

# 1) 导入与常量
check("候选文件可原样导入", hasattr(v3, "ANALYSIS_SYSTEM") and hasattr(v3, "ANALYSIS_SCHEMA_HINT"))
check(
    "候选 prompt 版本标识 = v3-rc1",
    v3.ANALYSIS_PROMPT_VERSION_CANDIDATE == "v3-rc1",
    v3.ANALYSIS_PROMPT_VERSION_CANDIDATE,
)
print(f"      V3 SYSTEM 字符数 = {len(v3.ANALYSIS_SYSTEM)}")
print(f"      V3 HINT   字符数 = {len(v3.ANALYSIS_SCHEMA_HINT)}")
print(f"      V2 SYSTEM 字符数 = {len(V2_SYSTEM)}")
print(f"      V2 HINT   字符数 = {len(V2_HINT)}")

# 2) V3 模板是否真 JSON
start = v3.ANALYSIS_SCHEMA_HINT.find("{")
end = v3.ANALYSIS_SCHEMA_HINT.rfind("}")
template_text = v3.ANALYSIS_SCHEMA_HINT[start : end + 1] if start >= 0 else ""
try:
    v3_template = json.loads(template_text)
    v3_json_ok = isinstance(v3_template, dict)
    v3_json_err = ""
except Exception as err:  # noqa: BLE001
    v3_template = None
    v3_json_ok = False
    v3_json_err = str(err)
check("V3 JSON 模板可被 json.loads 解析", v3_json_ok, v3_json_err)

# 对照：V2 Hint 是伪 JSON
try:
    json.loads(V2_HINT)
    v2_json_ok = True
except Exception:  # noqa: BLE001
    v2_json_ok = False
check("（对照）V2 Hint 是伪 JSON，不可解析", not v2_json_ok)


# 3) 字段路径一致性
def paths_of(node: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for k, v in node.items():
        p = f"{prefix}{k}"
        out.append(p)
        if isinstance(v, dict):
            out.extend(paths_of(v, p + "."))
    return sorted(out)


spec_paths = set(spec_field_paths())
v3_paths = set(paths_of(v3_template)) if v3_template else set()
missing = sorted(spec_paths - v3_paths)
extra = sorted(v3_paths - spec_paths)

check(
    "V3 模板字段路径 = SPEC 字段路径（59/59）",
    spec_paths == v3_paths,
    f"spec={len(spec_paths)} v3={len(v3_paths)} 缺={missing} 多={extra}",
)

# 4) 默认值形态：标量 null / 列表 []
bad_default: list[str] = []
OBJ_KEYS = {
    "content_invariants",
    "rhetorical",
    "stance_engagement",
    "microstyle",
    "style_boundary",
}
for p in sorted(spec_paths):
    if p in OBJ_KEYS:
        continue
    node = v3_template
    cur = p.split(".")
    for c in cur:
        node = node[c]
    expected_null = p != "length_chars" or True  # 模板里 length_chars 也是 null
    if isinstance(node, list):
        continue  # [] 合法
    if node is not None:
        bad_default.append(f"{p}={node!r}")
check("V3 模板标量默认均为 null、列表默认均为 []", not bad_default, str(bad_default[:5]))

# 5) 消息结构
sample = "有没有人跟我一样\n\n两眼一睁\n\n就是 X"
m2 = v2_build(sample)
m3 = v3.build_analysis_messages(sample)
check("V2 消息结构 = [system, user]", [m["role"] for m in m2] == ["system", "user"])
check("V3 消息结构 = [system, user]", [m["role"] for m in m3] == ["system", "user"])
print(f"      V2 输入字符数 = {len(m2[0]['content']) + len(m2[1]['content'])}")
print(f"      V3 输入字符数 = {len(m3[0]['content']) + len(m3[1]['content'])}")

# 6) 禁止项
check("未修改 ANALYSIS_SCHEMA_VERSION", ANALYSIS_SCHEMA_VERSION == "analysis-v2", ANALYSIS_SCHEMA_VERSION)
check("未修改 ANALYSIS_SCHEMA_HASH", ANALYSIS_SCHEMA_HASH.startswith("d1bec6d5"), ANALYSIS_SCHEMA_HASH[:16] + "…")
forbidden = ["analysis-v3", "schema_version_v3"]
hit = [f for f in forbidden if f in json.dumps(v3_template, ensure_ascii=False)]
check("V3 模板内不含新版 schema 标识", not hit, str(hit))

print()
print(f"核查项 {ok_count + fail_count} 项：PASS {ok_count} / FAIL {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
