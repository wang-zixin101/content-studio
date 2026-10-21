# Content Studio V2 · 任务 04-C WRITE 升级报告

> 交付时间：2026-10-10
> 范围：WRITE 提示词与组装器升级为「面向中文 X 传播表现」的创作引擎 + 五种创作输入路径
> 不含：ANALYSIS / STYLE 业务规则、任务 05 / 06 / 07、数据库迁移、EXE 构建

---

## 0. 测试结果总览

| 项目 | 结果 |
|---|---|
| 后端全套回归 | **899 / 899 passed**（原 848 + 04-C 新增 51） |
| 前端单测 | **163 / 163 passed**（原 150 + 新增 13） |
| 前端类型检查 | `tsc --noEmit` 通过 |
| 真实模型样本 | 3 次调用，全部只输出正文（三候选 / 单稿 / 自主选题各 1 次） |
| 生产库 `data/content_studio.db` | `b867f12e68afb9e5…` mtime `2026-10-07 19:38:42` — **未改动** |
| Desktop V1 `%LOCALAPPDATA%/ContentStudio/data/content_studio.db` | `ec4f0eebde620654…` mtime `2026-10-09 17:03:28` — **未改动** |
| EXE | 未构建 |

---

## 1. 修改文件清单（含 sha256 前 16 位）

| 文件 | sha256(16) | 改动 |
|---|---|---|
| `backend/app/services/creation_path.py` | `b4729d89d7762163` | **新增**：五种创作路径的确定性解析 |
| `backend/app/prompts.py` | `4fc5a515f4a7e153` | WRITE v3：流量目标 / 重构自由度 / 输出纪律；新三候选角色；路径输入块 |
| `backend/app/services/rewriter.py` | `9a1f911faa538993` | 支持 creation_path；author_auto 无素材生成；只传 content_id 时服务端自取原文 |
| `backend/app/services/rewrite_candidates.py` | `c4d46f2d855e6144` | 同上（三候选出口） |
| `backend/app/services/rewrite_strategy.py` | `d925bc70e53743e7` | `ROLE_PRIORITY_DIMENSIONS` 按新角色重排 |
| `backend/app/schemas.py` | `515fb0f15385b0ec` | `CreationPath` 字面量 + `RewriteIn` / `RewriteCandidateIn` / 两个 Out |
| `backend/app/config.py` | `9abd358032e30a19` | `WRITE_PROMPT_VERSION` v2 → **v3** |
| `backend/app/api/rewrites.py` | `74940e927f8558d0` | 透传 creation_path；author_auto 放开空素材 |
| `backend/tests/test_regression.py` | `1af35706301e64c1` | 3 处旧断言更新 + 新增 51 条 04-C 测试 |
| `frontend/src/lib/rewrite-flow.ts` | `704fdfbb8dea881c` | `CreationPath` / `resolveCreationPath` / `canGenerate` 纯函数 |
| `frontend/src/lib/api.ts` | `3a9787e0e17183d7` | `creation_path` 与 `use_reference_profile` 入参 |
| `frontend/src/pages/write.tsx` | `3ca60bc89020ac48` | 新增「作者起稿」模式；参考作者选择扩展到全部生成型模式；画像开关 |
| `frontend/src/lib/rewrite-flow.test.ts` | — | 新增 13 条路径测试 |
| `validate_write_04c.py` | `e772bacdde4df126` | **新增**：路径形态 dry 校验 + 真实样本（每种 1 次调用） |

未改动（明确核对过，避免覆盖 04-A / 04-B）：`services/analysis_spec.py`、`prompts.ANALYSIS_SYSTEM`、`prompts.STYLE_SYSTEM`、`services/style_profile.py`、`services/style_contract.py`、`services/style_learning.py`、`services/write_context.py`、`db.py`（`SCHEMA_VERSION` 仍为 9）。

---

## 2. 七项要求的逐项落地

### 要求 1 — 三候选全部追求流量，体现不同切入角度与表达机制

旧角色是「保守程度三档」（稳妥 / 强钩子 / 重构），其中**"稳妥版"的定义本身就允许平淡**——"不追求极端 hook""适合直接照发"，与"全部追求流量"直接冲突。

新角色改为**三种传播机制**，差别在切入角度与表达机制，不在敢不敢冒险：

| | 角色 | 传播机制 | 关键约束 |
|---|---|---|---|
| A | 反差切入 | 认知冲突 | 前三行必须完成「推翻一个默认认知」 |
| B | 结论前置 | 信息密度 + 确定性 | 前三行必须包含最值得转发的那一点本身；不做铺垫 |
| C | 场景重构 | 代入感 + 信息增量 | 换切入角度；可改信息顺序与推进结构 |

system 里新增 `_WRITE_TRAFFIC`，横跨三个个性化状态（有测试锁：COLD_START / LEARNING / PERSONALIZED 都必须带这一段）：
- 每版必须有至少一个**传播点**（值得转发的判断 / 反常识信息 / 可引用细节 / 能站队的立场）；
- 明确列出禁用结果：平淡复述、学术腔、报告腔、客服腔、公文腔、空洞升华、总结式收尾、强行排比、机械三段式、"首先其次最后"、机械"不是X而是Y"、固定 rule-of-three、凑长度的铺垫复述套话、无材料支撑的宏大判断、把"全面"当优点；
- 前 1～3 行必须能**独立成立**。

`_WRITE_CANDIDATE_TASK` 新增硬规则："三个候选全部追求流量……写出任何一个平淡版本都算失败"，并要求"切入角度不同、开头机制不同、推进方式不同"。

`ROLE_PRIORITY_DIMENSIONS` 同步重排（A 看开头机制/修辞动作/直接程度，B 看直接程度/确定性/节奏，C 看结构/修辞动作/结尾/语气），保证候选卡上的「参考：」三个不完全一样（Stage 10 验收项 33c）。

### 要求 2 — 允许大规模重构，当前用户指令优先

新增 `_WRITE_RESTRUCTURE`：
- 素材 / 主题只是**原料**，不是结构模板；
- 明确允许：换切入角度、换推进结构、换开头与收尾机制、换信息密度与节奏、提出比原材料更锋利的观点、自由补充数据案例素材；
- **用户对本次生成的明确要求优先级最高**，高于素材原有的角度、结构与观点；用户说"换个角度""更狠一点""不要这个结论"时必须照做，不得因为"素材原文不是这么写的"而拒绝或打折扣；
- 唯一不能擅自改变的是**可核查硬事实**（数字、时间、实体、引用归属、明确标注的不确定性）；用户明确要求改变某一项时以用户要求为准；
- 不得凭空补写素材里看不到的信息（配图 / 视频 / 链接 / 上文 / 会话语境）。

### 要求 3 — 复用 ANALYSIS / 个人 STYLE / 参考作者画像 / 历史流量数据

全部走既有接线，一行未重写：
- `source_analysis` 仍是 **完整** ANALYSIS 合同对象（04-A D1 的成果）；
- `_style_write_context()` 仍是 STYLE → WRITE 的**唯一**确定性投影（04-B B2 的成果）；
- `build_reference_profile_context()` 仍是参考作者画像的唯一出口；
- `strategy_context` 仍是 `services/write_context.build_write_context()`，单稿与三候选共用。

新增测试 `test_36_style_and_data_blocks_unchanged_by_path` 锁死：**换路径不得丢掉**「用户确认过的真实作品 / 全局风格档案 / 素材分析 / 可参考的历史数据规律」四块中的任何一块（五条路径逐条断言）。

### 要求 4 — 五种创作输入路径

新增 `services/creation_path.py`，两个出口（单稿 / 三候选）**共用同一个解析函数**——路径判定散落两处正是 04-A 的 D1/D4 成因。

| 路径 | 输入 | 判定依据 |
|---|---|---|
| `rewrite` 原帖改写 | 有素材，无参考作者 | 默认推导 |
| `rewrite_author` 原帖 + 参考作者 | 有素材 + 选中参考作者 | `reference_author_id` |
| `topic` 仅主题创作 | 有主题，无参考作者 | **必须由调用方声明**（后端无法从文本区分原帖与主题） |
| `topic_author` 主题 + 作者 | 有主题 + 选中参考作者 | 声明 + `reference_author_id` |
| `author_auto` 仅参考作者，AI 自主选题 | 无素材无主题 + 选中参考作者 | 空素材时的唯一合法出路 |

三条纪律：
1. **路径依据是"有没有选参考作者"，不是"有没有勾选画像开关"** —— 作者一旦选中，其流量画像与跨账号数据就已进入本次生成；`use_reference_profile` 只是"要不要再注入写作画像"的更细开关（阶段 7 纪律保留：选作者 ≠ 自动启用画像）。
2. **空素材只有一条合法出路** —— 无素材且无作者直接 `ServiceError`，绝不退化成"拿空文本生成"。
3. **声明与输入冲突报错，绝不静默改判** —— 否则"自主选题"会悄悄变成"拿空文本生成"。

prompt 侧三种输入块：`## 当前素材 / 想法 / 对标内容`（原帖）/ `## 本次主题 / 灵感`（主题，附"不是待改写的原帖"说明）/ `## 本次没有提供素材或主题`（自主选题，含选题优先级与"选题理由不要写出来"）。

**未分析素材必须能直接生成**：`source_analysis` 为 `None` 时该段直接跳过，不阻塞、不自动补分析。补了两条回归：
- 新粘贴文本 → 生成成功，prompt 里**没有** `## 素材分析`，`analyses` 表仍为 0 行；
- 库里已有但未分析的 content → 生成成功，`analysis_state` 仍为 `unprocessed`，`analyses` 表仍为 0 行。

### 要求 5 — 不额外核查，初稿可自由补充数据素材

未新增任何事实核查链路或联网步骤。`_WRITE_RESTRUCTURE` 明确写"自由补充数据、案例、对照、背景素材来支撑传播点"。新增测试 `test_19_no_fact_check_stage_in_prompt` 锁死 prompt 中不得出现「请先核查 / 务必核实 / 联网核查 / 先验证真实性」。

唯一保留的边界是**不得篡改素材自带的可核查硬事实**与**不得凭空补写素材里看不到的信息**——这不是核查流程，是内容保真的既有底线。

### 要求 6 — 严格控制输出

新增 `_WRITE_OUTPUT_DISCIPLINE`（硬性段），逐类点名禁止：
创作说明 / 思路说明 / 策略说明 · 事实核查提醒 / 数据可靠性提示 / 免责声明 · 模型生成声明 / AI 身份声明 / "仅供参考" · 对本文档与档案的任何引用说明 · Markdown 代码块围栏 / 标题符号 / 序号前缀 / 角色名 / 小标题 · "以下是初稿""供你参考""你可以再调整"类过渡语 · 三候选标签之外的任何文字。
收尾一句："正文之外多一个字，本次生成就算失败。"

两个出口各自收尾：单稿"只输出正文，不要输出任何说明、提醒、免责声明或生成声明"；三候选"只输出三段正文……"且规则新增"标签之内也不要写任何说明文字"。

真实样本验证见 §4：三个候选 + 单稿全部只输出正文，脚本里的 `check_output_discipline()` 逐条扫描无命中。

### 要求 7 — 模型、参数、调用次数不变

- 模型仍是 `mimo-v2.6-pro`（`test_50` 锁 `settings.model_rewrite`）；
- 调用链**不传** `temperature` / `max_tokens`，provider 只在调用方显式传值时才写入 payload，且用 MiMo 官方的 `max_completion_tokens`（`test_48` / `test_49` / `test_51`）；
- 三候选仍是**恰好 1 次** `provider.chat()`，解析失败明确报错、绝不偷偷重试（既有纪律未动）；
- 新增 `test_43_all_five_paths_exactly_one_ai_call`：**五条路径逐条**断言 1 次 AI 调用。

---

## 3. 前端五条路径

| 模式 | 可走的路径 | 出口 |
|---|---|---|
| 灵感起稿 `idea` | 仅主题创作 / 主题 + 作者 | 单稿 |
| 成稿收录 `final` | 无（不调用生成服务） | 收录 |
| 对标改写 `benchmark` | 原帖改写 / 原帖 + 参考作者 | 三候选 |
| **作者起稿 `author`（新增）** | 仅参考作者 → AI 自主选题 | 三候选 |

同时修掉两个 UI 缺口：
1. 参考作者选择器**原本只在「对标模式 + 已选随机素材」时出现**——粘贴素材、主题创作都选不了参考作者，"主题 + 作者"路径在 UI 上根本不存在。现在对所有生成型模式开放（抽素材阶段与已采用阶段仍收起，保持 B3 #4 / #15）。
2. 「使用作者写作画像」开关在旧版 UI 里**没有控件**，`useProfile` 恒为 `false`，导致参考作者画像从未真正注入。现在补上可见的勾选项，并保留"选作者 ≠ 自动启用"的语义。

路径推导是 `rewrite-flow.ts` 里的纯函数 `resolveCreationPath()`，13 条前端单测覆盖五种路径 + 三个 null 边界（没选作者 / author 模式却有素材 / 其它模式无素材）。

---

## 4. 真实样本（`mimo-v2.6-pro`，各 1 次调用）

> 说明：参考作者「老雷」的分析与表现数据是脚本构造的夹具，**不是用户真实数据**；模型调用与输出是真实的。个性化状态为 `COLD_START`（与用户当前账号的真实状态一致）。
> 证据：`task04c_deliverables/WRITE-v3-真实样本汇总.json`

### 4.1 三候选 · 原帖 + 参考作者（261 / 222 / 302 字）

- **A 反差切入**：以"别信'功能最强'的 AI 工具推荐"开局，反打"最强的那几个，反而是最早被我删掉的"。
- **B 结论前置**：首行直接给硬信息"去年试了 17 个 AI 写作工具，最后只留 2 个"，无铺垫。
- **C 场景重构**：改成一段对话场景（"前几天又有人问我…我没推荐，只反问了一句…"），靠过程与代入感推进。

三者切入角度、开头机制、推进方式均不同；均无非正文文字。

### 4.2 单稿 · 主题 + 作者（296 字）

只输出正文。以"用 AI 没效果的人，问题基本出在同一个地方"开局，含个人踩坑案例与可执行收尾，无说明 / 提醒 / 声明。

### 4.3 三候选 · 仅参考作者自主选题（无素材、无主题）

AI 自行选定主题「让 AI 一次写完整篇是目前最浪费时间的用法」，并据此产出三个机制不同的候选（A 反差 / B 结论前置 / C 场景重构），均只输出正文。

---

## 5. 新旧写作行为差异

| 维度 | 旧（WRITE v2） | 新（WRITE v3） |
|---|---|---|
| 目标 | 通顺、完整、像样、可编辑 | **传播表现**；无传播点即失败品 |
| 三候选差别 | 保守程度三档（稳妥版允许平淡） | **三种传播机制**；三者都必须有传播点 |
| A 版定位 | 稳妥、不追求极端 hook | 反差切入，靠认知冲突 |
| 素材地位 | 结构模板（近似原文改写） | **原料**；可换角度 / 换结构 / 换观点 |
| 用户指令 | 与素材结构冲突时无明确优先级 | **明确要求优先级最高** |
| 补充数据 | 未明确 | 明确允许自由补充数据 / 案例 / 素材 |
| 输出 | "最终只输出正文"（一句） | 逐类点名禁止的**输出纪律段** |
| 输入路径 | 1 个隐含路径（有素材→生成） | **5 条显式路径**，含 AI 自主选题 |
| 空素材 | 直接报错 | `author_auto` 合法；其余仍报错 |
| 参考作者（UI） | 仅随机素材可选，画像开关无控件 | 全部生成型模式可选 + 可见开关 |
| 模型 / 参数 / 调用次数 | mimo-v2.6-pro / 无 temperature 无 max_tokens / 三候选 1 次 | **完全不变** |

---

## 6. 已知限制（如实上报，未做范围蔓延）

1. **创作路径不落库**：`rewrites` 表没有 `creation_path` 列。需求未要求，加列属于 DB 迁移，本轮不做；路径只在 API 响应里回传（`creation_path` 字段）便于排查。
2. **初始 20 篇仍是逐篇调用**：这是 STYLE 增量学习的既有限制（设计允许"分批提取、统一归并"，但需一份批量观察合同，设计未规定），与 04-C 无关，未动。
3. **真实样本用的是夹具数据**：参考作者的分析/表现是脚本构造的，且个性化状态为 `COLD_START`（无用户真实风格证据）。用户真实数据下的效果需实际使用后再看。
4. **主题路径依赖前端声明**：后端无法从文本区分"原帖"与"主题"，未声明时按 `rewrite` 处理（与旧行为一致）。若第三方直接调 API 且不传 `creation_path`，主题类输入会被当成原帖。

---

## 7. 复核方式

```bash
cd backend && ./.venv/Scripts/python.exe -m unittest tests.test_regression
# → Ran 899 tests / OK

cd frontend && ./node_modules/.bin/tsc --noEmit && node --test "src/**/*.test.ts"
# → pass 163 / fail 0

./backend/.venv/Scripts/python.exe validate_write_04c.py            # dry：五条路径 prompt 形态
./backend/.venv/Scripts/python.exe validate_write_04c.py --real --sample auto_topic
# → 每种样本恰好 1 次真实模型调用
```
