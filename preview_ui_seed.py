"""任务 07 UI 盘点用的**隔离预览库**。

为什么不用 `data/content_studio.db`：它是空库（0 authors / 0 benchmark），
每个页面点进去都是空状态，看不出真实布局问题。

本脚本造一份**独立**的预览库，覆盖全部页面的真实数据形态：
    · 4 位作者（含各自的 benchmark 素材 + 表现数据）
    · 30 条对标素材（含一条 600+ 字多段落长帖）
    · 24 条「我的内容」（已分析 + 可直接参与风格学习）
    · 一版可用的 STYLE 档案（让 /mine、/style、/insights 有真实内容）
    · 2 个导入批次（让 /imports、/import 有记录）

绝不写生产库、绝不写 Desktop V1。

用法：
    ./backend/.venv/Scripts/python.exe preview_ui_seed.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PREVIEW_DIR = Path(tempfile.gettempdir()) / "cs_ui_preview"
PREVIEW_DB = PREVIEW_DIR / "ui_preview.db"

TOPICS = ["AI工具", "效率方法", "副业", "内容运营", "投资心态"]
CONTENT_TYPES = ["观点表达", "经验分享"]

OPENINGS = ["直接陈述", "故事场景", "提问开场", "数据开场"]
STRUCTURES = ["观点—理由", "问题—方案", "并列论证"]
TONES = ["中性", "轻松", "克制"]
RHYTHMS = ["长短交替", "短句为主"]
ENDINGS = ["观点收束", "行动号召"]

BENCHMARK_POSTS: list[tuple[str, str]] = [
    ("AI工具", "选 AI 工具之前，先把你最近三次卡住的瞬间写下来。工具解决的是那三次，不是所有问题。"),
    ("AI工具", "我用 AI 写作一年，最大的收获不是写得快，是终于知道自己哪里不会写。"),
    ("AI工具", "别急着买订阅。先花 20 分钟，把最近三次写作卡住的地方写清楚。"),
    ("AI工具", "工具评测看的是功能表，你真正该看的是自己的工作流断在哪一段。"),
    ("效率方法", "很多人以为效率问题是时间不够。其实是你每天在十几个小决定上把注意力用光了。"),
    ("效率方法", "把决策提前固化，执行会变轻。这句话我贴在桌子上半年了。"),
    ("效率方法", "不要优化你本来就不该做的事。先砍掉，再提速。"),
    ("投资心态", "行情不好的时候，最好的操作通常是什么都不做。但这句话没人愿意听。"),
    ("投资心态", "止损难的不是设定比例，是承认自己一开始就看错了。"),
    ("投资心态", "仓位决定心态。仓位重了，再好的逻辑也拿不住。"),
    ("副业", "副业第一步不是找项目，是盘点你到底有什么是可交换的。"),
    ("副业", "大部分副业失败，是因为把顺序搞反了：先追风口，再补能力。"),
    ("副业", "副业的本质是交换。你拿能力换别人的钱。"),
    ("内容运营", "写一条没人看的帖子，比写十条有人看的更有价值——前提是你知道为什么没人看。"),
    ("内容运营", "X 上的中文内容，前三行决定一切。前面的铺垫基本等于没有。"),
    ("内容运营", "别急着追热点。热点会过去，你的判断力不会。"),
    ("内容运营", "没人转发，通常不是写得不好，是没给读者一个转发的理由。"),
    ("AI工具", "让 AI 一次写完整篇，是目前最浪费时间的用法。完整输出等于完整返工。"),
    ("效率方法", "一天只安排三件事。做完三件就是成功的一天。"),
    ("投资心态", "亏钱最贵的一课，是学会了在错误的仓位上坚持。"),
    ("副业", "先做能被验证的小事，再谈规模化。"),
    ("内容运营", "把你自己写作里最卡的那一段写下来，再去找工具。"),
    ("AI工具", "AI 放大的永远是你已有的东西。你脑子里只有三成清晰，它给你三成清晰的一百个版本。"),
    ("效率方法", "复盘不要写感受，写数字。感受会骗你，数字不会。"),
    ("投资心态", "不要因为便宜而买，要因为值得而买。"),
    ("副业", "能收钱的才叫能力，收不到钱的是爱好。"),
    ("内容运营", "一条帖子只讲一件事。想讲三件，就发三条。"),
    ("AI工具", "工具会换，判断力不会。花时间在后者上更划算。"),
    ("效率方法", "别用忙碌代替思考。忙是最容易的逃避方式。"),
    ("投资心态", "长期持有不是不动，是不因为短期波动而动。"),
]

LONG_POST = """先给结论：AI 工具选不对，90% 不是工具的问题，是你没定义清楚问题。

我去年陆续试了 17 个 AI 写作工具，从通用大模型到专门做长文的，最后只留下 2 个。
不是因为它们功能最强，而是因为它们刚好卡在我工作流里真正断掉的两段。

第一段：把散乱的长材料压成要点。
我原来要花两个小时读一份 40 页的材料，现在 15 分钟能拿到一页纸的骨架。
第二段：在我写完之后帮我找逻辑漏洞。
不是润色，是挑刺——它会直接问我「你这句话的证据在哪」。

中间这一段想多说一句：
工具评测看的是功能表，评分表上写满了「支持多少种格式」「能不能联网」「上下文窗口多大」。
但你真正该看的是自己的工作流里到底断在哪一段。断在哪，才需要什么。
我见过太多人把预算花在「最强的工具」上，结果那一段本来就不卡，卡的那一段反而没人管。

还有一个反直觉的点：留下来的那两个工具，功能都不是最多的。
其中一个甚至连插件生态都没有。但它解决的那两件事，恰好占了我写作卡点的 80%。

结尾给一个可执行的建议：
别急着买订阅。先花 20 分钟，把你最近三次写作卡住的瞬间写下来。
是没素材？是理不清结构？还是写完不敢发？
把这三个问题写清楚，你会发现要选的工具其实已经浮出来了。"""

MINE_POSTS: list[tuple[str, str, str]] = [
    ("AI工具", "human_original", "先定义问题，再选工具。我踩过的坑都在这一句里。"),
    ("AI工具", "human_original", "买工具之前先问一句：我到底卡在哪一步？"),
    ("效率方法", "human_original", "每天只做三件事，第四件一律推到明天。"),
    ("效率方法", "human_original", "把复杂的决定提前做完，剩下的就是执行。"),
    ("副业", "human_original", "副业要先算清成本，再谈收益。"),
    ("副业", "human_original", "能交换出去的能力，才叫能力。"),
    ("内容运营", "human_original", "前三行写不好，后面写得再好也没人看。"),
    ("内容运营", "human_original", "一条内容只讲一个判断。"),
    ("投资心态", "human_original", "仓位决定心态，这句话我信了三年。"),
    ("投资心态", "human_original", "看不懂就不做，这本身就是一个决策。"),
    ("AI工具", "imported_self", "我习惯先写结论，再补两条理由。"),
    ("效率方法", "imported_self", "复盘写数字，不写感受。"),
    ("副业", "imported_self", "先做能被验证的小事。"),
    ("内容运营", "imported_self", "别追热点，追你自己的判断。"),
    ("投资心态", "imported_self", "长期持有靠的不是意志力，是仓位轻。"),
    ("AI工具", "ai_assisted", "AI 给的是起点，不是答案。"),
    ("AI工具", "ai_assisted", "工具解决那三次，不解决所有问题。"),
    ("效率方法", "ai_assisted", "注意力比时间稀缺。"),
    ("副业", "ai_assisted", "顺序反了，努力就是白费。"),
    ("内容运营", "ai_assisted", "给读者一个转发的理由。"),
    ("投资心态", "ai_assisted", "便宜不是买入的理由。"),
    ("AI工具", "human_original", "我留下的两个工具，功能都不是最多的。"),
    ("效率方法", "human_original", "忙是最容易的逃避方式。"),
    ("副业", "human_original", "收不到钱的是爱好，不是能力。"),
]


def make_db() -> Path:
    if PREVIEW_DIR.exists():
        shutil.rmtree(PREVIEW_DIR, ignore_errors=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["DB_PATH"] = str(PREVIEW_DB)
    import app.config as cm

    cm.settings.db_path = PREVIEW_DB
    return PREVIEW_DB


def v3_analysis(
    text: str,
    *,
    topic: str,
    content_type: str = "观点表达",
    opening: str = "直接陈述",
    structure: str = "观点—理由",
    ending: str = "观点收束",
    tone: str = "中性",
    rhythm: str = "长短交替",
) -> dict:
    return {
        "topic": topic,
        "subtopic": None,
        "content_type": content_type,
        "core_message": text[:40],
        "tags": [topic],
        "keywords": [topic, "方法"],
        "content": {
            "claims": [
                {"kind": "作者判断", "text": text[:20], "evidence": text[:8]}
            ],
            "must_preserve": [],
            "context_limits": [],
        },
        "writing": {
            "opening_type": opening,
            "structure_type": structure,
            "ending_type": ending,
            "moves": [
                {"type": "对比", "evidence": text[:6], "mechanism": "用反差建立张力"}
            ],
        },
        "style": {
            "tone": tone,
            "register": "口语",
            "directness": "直接",
            "certainty": "明确肯定",
            "rhythm": rhythm,
            "markers": [{"type": "换行", "evidence": text[:4]}],
            "distinctive_phrases": [],
        },
        "reuse": {"patterns": [], "boundaries": []},
    }


def attach_perf(conn, content_id: int, *, views: int, likes: int, replies: int,
                retweets: int, bookmarks: int, days_ago: int) -> None:
    conn.execute(
        "UPDATE contents SET posted_at = datetime('now', ?) WHERE id = ?",
        (f"-{days_ago} days", content_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO content_performance("
        " content_id, view_count, favorite_count, reply_count, retweet_count,"
        " quote_count, bookmark_count, is_pinned, media_type, captured_at)"
        " VALUES(?,?,?,?,?,0,?,0,'',datetime('now'))",
        (content_id, views, likes, replies, retweets, bookmarks),
    )


def main() -> int:
    db_path = make_db()
    import sqlite3

    from app import repository as repo
    from app.db import get_conn, init_db

    init_db(db_path)

    with get_conn(db_path) as conn:
        authors = {}
        for username, display in (
            ("wangchangfu", "王长富"),
            ("laolei", "老雷"),
            ("suohage", "梭哈哥"),
            ("xingyu", "兴雨"),
        ):
            authors[username] = repo.create_author(
                conn, username=username, display_name=display
            )

        names = list(authors)
        # ---- 对标素材 + 表现数据 ----
        for i, (topic, text) in enumerate(BENCHMARK_POSTS):
            cid = repo.create_content(conn, "benchmark", text)
            repo.set_content_author(conn, [cid], authors[names[i % 4]])
            data = v3_analysis(
                text,
                topic=topic,
                content_type=CONTENT_TYPES[i % 2],
                opening=OPENINGS[i % 4],
                structure=STRUCTURES[i % 3],
                ending=ENDINGS[i % 2],
                tone=TONES[i % 3],
                rhythm=RHYTHMS[i % 2],
            )
            repo.save_analysis(conn, cid, data, "seed", raw=data)
            repo.set_analysis_state(conn, cid, "analyzed")
            high = i % 3 == 0
            attach_perf(
                conn, cid,
                views=(120_000 + i * 1_137) if high else (900 + i * 37),
                likes=(1_800 + i * 23) if high else (8 + i),
                replies=(120 + i) if high else (1 + i % 3),
                retweets=(260 + i) if high else (2 + i % 4),
                bookmarks=(420 + i * 3) if high else (3 + i % 2),
                days_ago=2 + i * 3,
            )

        # ---- 每位作者各补 5 条同主题素材 ----
        # 作用：让「跨作者洞察」有可比较的取值（该页要求某个取值在同一作者内部
        # 达到 5 条样本才形成判断），否则整页只能是空态、看不出真实布局。
        per_author_topic = {
            "wangchangfu": "AI工具",
            "laolei": "效率方法",
            "suohage": "投资心态",
            "xingyu": "内容运营",
        }
        fillers = [
            "这一条只是想把这个判断再说清楚一点，没有别的新东西。",
            "同一个判断换个说法：先看清楚，再动手。",
            "我反复验证过的一件事，写下来提醒自己。",
            "这类问题没有标准答案，但有一条底线。",
            "补一个具体例子，免得只有结论。",
        ]
        for username, topic in per_author_topic.items():
            for j, base in enumerate(fillers):
                text = f"[{topic}] {base}"
                cid = repo.create_content(conn, "benchmark", text)
                repo.set_content_author(conn, [cid], authors[username])
                d = v3_analysis(
                    text, topic=topic,
                    content_type=CONTENT_TYPES[j % 2],
                    opening=OPENINGS[j % 4],
                    structure=STRUCTURES[j % 3],
                )
                repo.save_analysis(conn, cid, d, "seed", raw=d)
                repo.set_analysis_state(conn, cid, "analyzed")
                attach_perf(
                    conn, cid,
                    views=80_000 + j * 997 if j < 2 else 1_500 + j * 61,
                    likes=1_200 + j * 11 if j < 2 else 12 + j * 2,
                    replies=90 + j if j < 2 else 2 + j % 3,
                    retweets=180 + j if j < 2 else 3 + j % 4,
                    bookmarks=300 + j if j < 2 else 4 + j % 2,
                    days_ago=5 + j * 4,
                )

        # ---- 一条长帖（验证完整阅读）----
        long_id = repo.create_content(conn, "benchmark", LONG_POST)
        repo.set_content_author(conn, [long_id], authors["wangchangfu"])
        data = v3_analysis(LONG_POST, topic="AI工具", opening="直接陈述")
        repo.save_analysis(conn, long_id, data, "seed", raw=data)
        repo.set_analysis_state(conn, long_id, "analyzed")
        attach_perf(conn, long_id, views=248_000, likes=3_120, replies=214,
                    retweets=402, bookmarks=688, days_ago=1)

        # ---- 无作者素材（空白作者池）----
        for text in (
            "（未标注来源的收藏）先做能被验证的小事。",
            "（未标注来源的收藏）把决策提前固化，执行会变轻。",
        ):
            cid = repo.create_content(conn, "benchmark", text)
            d = v3_analysis(text, topic="效率方法")
            repo.save_analysis(conn, cid, d, "seed", raw=d)
            repo.set_analysis_state(conn, cid, "analyzed")

        # ---- 我的内容 ----
        mine_ids: list[int] = []
        for i, (topic, prov, text) in enumerate(MINE_POSTS):
            cid = repo.create_content(conn, "mine", text, style_eligible=True)
            repo.set_provenance(conn, cid, prov)
            d = v3_analysis(
                text,
                topic=topic,
                content_type=CONTENT_TYPES[i % 2],
                opening=OPENINGS[i % 4],
                structure=STRUCTURES[i % 3],
            )
            repo.save_analysis(conn, cid, d, "seed", raw=d)
            repo.set_analysis_state(conn, cid, "analyzed")
            mine_ids.append(cid)

        # ---- 一版可用 STYLE 档案 ----
        repo.save_style_profile(
            conn,
            {
                "voice_summary": "偏短句，结论前置，几乎不用口头禅。",
                "evidence": {
                    "sample_count": len(mine_ids),
                    "text_sample_count": len(mine_ids),
                    "human_original_count": 14,
                    "imported_self_count": 5,
                    "ai_assisted_count": 5,
                    "rewrite_pair_count": 3,
                    "confidence": "medium",
                },
                "global_voice": {
                    "sentence_length": "偏短",
                    "opening": "结论前置",
                    "certainty": "明确肯定",
                    "person": "第一人称",
                },
                "signature_patterns": [
                    {
                        "rule": "第一句先给判断，第二句才补理由",
                        "classification": "VOICE",
                        "strength": "strong",
                        "frequency": "high",
                        "evidence_count": 12,
                        "applies_when": None,
                        "supporting_samples": [
                            {"content_id": mine_ids[0], "quote": MINE_POSTS[0][2][:14]}
                        ],
                    },
                    {
                        "rule": "用「不是A，是B」的对照句收尾",
                        "classification": "VOICE",
                        "strength": "medium",
                        "frequency": "medium",
                        "evidence_count": 5,
                        "applies_when": None,
                        "supporting_samples": [
                            {"content_id": mine_ids[6], "quote": MINE_POSTS[6][2][:12]}
                        ],
                    },
                ],
                "x_modes": [
                    {
                        "name": "单点判断",
                        "when_to_use": "只想说清一件事的时候",
                        "sample_count": 9,
                        "confidence": "high",
                    }
                ],
                "topic_bound_terms": ["副业起步"],
                "uncertain_patterns": ["偶发使用长句，样本不足"],
                "anti_style": {
                    "supported_by_user_edits": ["其实", "非常"],
                    "not_observed_do_not_force": ["emoji", "话题标签"],
                },
                "edit_preferences": {
                    "frequent_deletions": [
                        {"text": "其实", "count": 6},
                        {"text": "非常", "count": 3},
                    ],
                    "frequent_additions": [{"text": "先", "count": 4}],
                    "frequent_replacements": [],
                    "stable_corrections": [],
                },
            },
            "偏短句，结论前置，几乎不用口头禅。",
            based_on_count=len(mine_ids),
            sample_ids=mine_ids,
            prompt_version="v3",
            profile_kind="initial_v20",
        )

        # ---- 导入批次 ----
        for idx, (name, count) in enumerate(
            (("xporter-王长富-2026-09.json", 32), ("xporter-合集-2026-10.json", 48))
        ):
            conn.execute(
                "INSERT INTO import_batches(import_batch_id, filename, source,"
                " item_count, input_count, inserted_count, duplicate_count,"
                " skipped_count, author_username, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,datetime('now','-3 days'))",
                (f"seed-batch-{idx}", name, "xporter", count, count,
                 count, 0, 0, "wangchangfu" if idx == 0 else None),
            )

    restore(db_path)
    print(str(db_path))
    _ = sqlite3
    return 0


def restore(db_path: Path) -> None:
    import app.config as cm

    cm.settings.db_path = db_path
    os.environ["DB_PATH"] = str(db_path)


if __name__ == "__main__":
    raise SystemExit(main())
