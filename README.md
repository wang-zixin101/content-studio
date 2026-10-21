# X 内容工作台（Content Studio）

本地运行的个人内容系统：输入内容 → 自动分析分类 → 检索 → 用你的风格改写。

严格区分「我的内容」与「对标内容」，并随「我的内容」积累逐步建立你的表达风格档案。

---

## 一、快速开始

### 1. 配置 AI 密钥

```bash
cd content-studio/backend
cp .env.example .env
```

打开 `backend/.env`，填入你的 MiMo API Key：

```
AI_API_KEY=sk-你的密钥
```

> MiMo 按量付费 Base URL：`https://api.xiaomimimo.com/v1`（key 形如 `sk-xxxxx`）
> MiMo Token Plan Base URL：`https://token-plan-cn.xiaomimimo.com/v1`（key 形如 `tp-xxxxx`）
> 两者选其一，改 `AI_BASE_URL` 与 `AI_API_KEY` 即可。

### 2. 启动

双击项目根目录的 `start.bat`（自动开两个窗口：后端 + 前端）。

或手动分别启动：

```bash
# 后端
cd content-studio/backend
"C:\Users\18483\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn app.main:app --reload

# 前端
cd content-studio/frontend
npm run dev
```

打开 http://localhost:5173

---

## 二、核心闭环（验收路径）

1. 打开工作台 → 「快速输入」
2. 粘贴一段文字 → 选择「我的」或「对标」→ `Ctrl + Enter` 保存
3. 系统在后台自动分析（13 个结构化字段）+ 打标签
4. 到「内容库」搜索 / 按标签、主题、类型筛选，能找到它
5. 点击内容 → 右侧详情面板查看 AI 拆解
6. 「我的内容」积累 ≥3 条后，「风格档案」可生成（建议 ≥10 条）
7. 打开任意「对标内容」详情 → 点「用我的口吻改写」
8. 系统检索你历史中最相似的内容 + 读取风格档案 → 生成「我的版本」
9. 你继续编辑 → 「保存为我的」→ 终稿作为「我的内容」入库并自动分析

---

## 三、快捷键

| 快捷键 | 作用 |
|---|---|
| `Ctrl + Enter` | 保存并分析 |
| `Ctrl + 1` | 标记为「我的」 |
| `Ctrl + 2` | 标记为「对标」 |
| `Tab`（输入框内） | 切换来源 |

---

## 四、批量导入

「批量导入」支持 TXT / Markdown / CSV：

- **TXT / Markdown**：按空行分段（单独一行 `---` 也作为分隔）
- **CSV**：每行一条；首行为表头时自动跳过

导入时选择来源，导入后与手动输入走**完全相同**的分析流水线。

---

## 五、技术架构

```
content-studio/
├── backend/                 Python + FastAPI
│   └── app/
│       ├── ai/              AI 封装层（Provider 抽象 + OpenAI 兼容实现 + 工厂）
│       ├── services/        分析 / 检索 / 风格档案 / 改写 / 导入
│       ├── api/             HTTP 路由
│       ├── repository.py    数据访问（全部 SQL 集中于此）
│       ├── db.py            SQLite schema 与连接
│       ├── jobs.py          后台分析队列
│       └── prompts.py       提示词模板
├── frontend/                React + Vite + TypeScript + shadcn/ui
│   └── src/
│       ├── pages/           快速输入 / 内容库 / 批量导入 / 风格档案 / 改写
│       ├── components/      UI 组件与业务组件
│       └── lib/             API 客户端与类型
└── data/                    SQLite 数据库（本地，不入库）
```

### 数据表

`contents` `analyses` `tags` `content_tags` `style_profile` `rewrites` `embeddings`(预留)

### 换模型 / 换厂商

模型调用全部封装在 `backend/app/ai/`，业务代码不出现任何模型名。
换厂商只需改 `.env`；换协议只需新增一个 `AIProvider` 子类并在 `factory.py` 注册。

---

## 六、设计取舍（重要）

- **相似检索用字段加权匹配，不用向量**：本系统中「相似」本质是主题/类型/标签的语义相似，
  字段匹配比文本重合更精准，且零依赖、可解释、可调权。内容量变大后可在 `embeddings` 表上叠加向量检索。
- **风格改写不只依赖抽象档案**：始终同时喂入真实历史样例（检索出的最相似「我的内容」），
  这是「像不像你」的关键。
- **改写同时保留 AI 原稿与用户终稿**：`rewrites` 表记录修改关系，为后续分析「你在删什么、改什么」积累数据。
- **删除按精确 ID，界面里无「清空全部」这类危险操作**。

---

## 七、第一版不做

自动发布、多账号、热点系统、数据看板、粉丝分析、排程、Agent 工作流、
团队协作、权限、登录、云同步、知识图谱、花哨可视化。
