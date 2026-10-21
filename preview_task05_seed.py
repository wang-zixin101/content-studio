"""任务 05 截图预览库：造一份**与生产库隔离**的演示数据。

为什么不用生产库截图：`data/content_studio.db` 当前是 0 authors / 0 benchmark
素材（空库），截出来什么都看不到。本脚本造一个独立的预览库，
**绝不写生产库、绝不写 Desktop V1**。

数据刻意包含：
  - 3 位作者 + 空白作者池（验证「全部作者 / 指定作者 / 空白作者」三个范围）
  - 一条**多段落长帖**（验证完整阅读：开头 / 中间 / 结尾都能读到）

用法：
    ./backend/.venv/Scripts/python.exe preview_task05_seed.py
    # 打印预览库绝对路径
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

#: 固定目录，方便后端与浏览器脚本共用同一个库
PREVIEW_DIR = Path(tempfile.gettempdir()) / "cs_task05_preview"
PREVIEW_DB = PREVIEW_DIR / "preview.db"

#: 多段落长帖 —— 截图里要验证「开头 / 中间 / 结尾」全部可读。
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

POSTS: list[tuple[str, str]] = [
    ("AI工具", "选 AI 工具之前，先把你最近三次卡住的瞬间写下来。工具解决的是那三次，不是所有问题。"),
    ("AI工具", "我用 AI 写作一年，最大的收获不是写得快，是终于知道自己哪里不会写。"),
    ("效率", "很多人以为效率问题是时间不够。其实是你每天在十几个小决定上把注意力用光了。"),
    ("效率", "把决策提前固化，执行会变轻。这句话我贴在桌子上半年了。"),
    ("投资", "行情不好的时候，最好的操作通常是什么都不做。但这句话没人愿意听。"),
    ("投资", "止损这件事，难的不是设定比例，是承认自己一开始就看错了。"),
    ("副业", "副业第一步不是找项目，是盘点你到底有什么是可交换的。"),
    ("副业", "大部分副业失败，是因为把顺序搞反了：先追风口，再补能力。"),
    ("内容运营", "写一条没人看的帖子，比写十条有人看的更有价值——前提是你知道为什么没人看。"),
    ("内容运营", "X 上的中文内容，前三行决定一切。前面的铺垫基本等于没有。"),
    ("内容运营", "别急着追热点。热点会过去，你的判断力不会。"),
    ("AI工具", "我试过让 AI 直接写完整篇。结果改到半夜。后来只让它写我能当场验收的那一小块。"),
]

BLANK_POSTS = [
    "（未标注来源的对标素材）这条是从别处收藏的，作者信息已经丢失了。",
    "（未标注来源的对标素材）记录一个判断：先做能被验证的小事。",
]


def make_db(force: bool = True) -> Path:
    if force and PREVIEW_DIR.exists():
        shutil.rmtree(PREVIEW_DIR, ignore_errors=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["DB_PATH"] = str(PREVIEW_DB)
    import app.config as cm

    cm.settings.db_path = PREVIEW_DB
    return PREVIEW_DB


def seed(db_path: Path) -> None:
    from app import repository as repo
    from app.db import get_conn, init_db

    init_db(db_path)

    with get_conn(db_path) as conn:
        authors = {}
        for username, display in (
            ("wangchangfu", "王长富"),
            ("laolei", "老雷"),
            ("suohage", "梭哈哥"),
        ):
            authors[username] = repo.create_author(
                conn, username=username, display_name=display
            )

        # 长帖挂在「王长富」名下
        long_id = repo.create_content(conn, "benchmark", LONG_POST)
        repo.set_content_author(conn, [long_id], authors["wangchangfu"])

        order = ["wangchangfu", "laolei", "suohage"]
        for i, (topic, text) in enumerate(POSTS):
            cid = repo.create_content(conn, "benchmark", text)
            repo.set_content_author(conn, [cid], authors[order[i % 3]])

        for text in BLANK_POSTS:
            repo.create_content(conn, "benchmark", text)

    _ = repo


def main() -> int:
    db_path = make_db()
    seed(db_path)
    print(str(db_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
