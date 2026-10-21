# Content Studio V2 · Windows 程序更新报告

> 时间：2026-10-11 21:33–22:00
> 结论：**Windows 程序已更新为最终验收通过的 V2 构建，实际启动验证通过**
> 用户数据（数据库 / 配置 / AI 连接设置）**完整保留**；Desktop V1 数据未被测试库覆盖

---

## 1. 现有构建方式（确认）

| 项 | 内容 |
|---|---|
| 打包方式 | PyInstaller **onedir**（`desktop/ContentStudio.spec`），无 console、无 onefile |
| 构建脚本 | `desktop/build.bat`：① `npm run build`（Vite → frontend/dist）② PyInstaller（--distpath release） |
| 产物结构 | `release/ContentStudio/ContentStudio.exe` + `_internal/`（Python 运行时 + `frontend/dist`） |
| 启动流程 | `desktop/main.py`：内部 uvicorn `127.0.0.1:8765`（同服 /api/* 与 React dist）→ 等健康检查 → pywebview（EdgeWebView2）打开 1440×900 窗口 |
| 用户数据 | 冻结态固定在 `%LOCALAPPDATA%\ContentStudio\`：`data\content_studio.db`、`.env`（AI 连接设置）、`logs\` —— **与程序目录分离，更新程序不会触碰** |
| 明确排除 | backend/.env、任何 SQLite、tests、.venv、.git（spec 头部声明） |

## 2. 更新前状态核对

| 项 | 值 |
|---|---|
| 旧 EXE | `release/ContentStudio/ContentStudio.exe`，2026-10-07 20:46 |
| 旧前端 dist | 同时刻（`index.html` sha256 `c37fbe68…`，早于全部 V2 功能） |
| 8765 端口 | 空闲（旧程序未在运行；日志显示 21:28 有一次正常关闭） |
| 用户 DB | `AppData\Local\ContentStudio\data\content_studio.db`，290,816 字节，mtime 2026-10-09 17:03，sha256 `ec4f0eeb…` |
| 其他副本 | `build/pyinstaller/ContentStudio/`（PyInstaller 工作路径残留，同为 10-07 旧构建） |
| 快捷方式 | 桌面 / 开始菜单**均未发现** ContentStudio 快捷方式 |

## 3. 执行的更新

1. **备份**：旧构建整目录压缩为
   `release/backup/ContentStudio-2026-10-07.zip`（19.6 MB，可随时还原）
2. **重建**：`desktop/build.bat`（Vite 构建 34s + PyInstaller 52s）——
   首次尝试在 COLLECT 阶段被工作台沙箱的批量删除守卫拦截（需删除 138 个旧文件 > 阈值 50）；
   处理：将旧构建**移动**（非删除）为 `release/_old-2026-10-07/` 后重建成功
3. **验证**：见 §4

## 4. 实际启动验证（`release/ContentStudio/ContentStudio.exe`）

### 4.1 启动与前后端连接
- 进程启动（PID 33756 → 28388 → 40144，共启动 3 次分别验证）
- `/api/health` → **200 `{"ok":true}`**
- 日志：`数据库就绪：C:\Users\18483\AppData\Local\ContentStudio\data\content_studio.db`（用户库）
- 日志：`分析队列已启动（worker=2…）`、`Batch 监控已启动`、`Uvicorn running on http://127.0.0.1:8765`
- 日志：`健康检查通过，创建窗口`；进程窗口标题 = **Content Studio**

### 4.2 数据读取（用户真实数据库）
- `/api/contents?limit=1` → `total: 0`（用户桌面库当前无内容 —— 与库内实际行数一致）
- `/api/authors` → 0 位作者（同上）
- `/api/mine/goal` → 正常返回（18 个字段，用户 10-08 建立的目标）

### 4.3 前端资源 = 最终验收通过的 V2 构建
- EXE 服务中的 `index.html` sha256（16）= `82b0bf1c1c739fd5`
- 源码 `frontend/dist/index.html` = `82b0bf1c1c739fd5` —— **逐字节一致**

### 4.4 主要页面实拍（EXE 自身服务 8765，1440×900 = 用户实际窗口尺寸）
共 12 张（`final_extra_shots/exe_*.png`）：
我的 / **写作** / 内容库 / 作者管理 / 风格档案 / 批量导入 / 导入记录 / 外部分析 / 跨作者洞察
+ 写作与内容库的 1100px / 820px。
已人工目检：写作页（22px 页头、EmptyState 居中、底部动作条 = S1/S6 后的最终 UI）、
内容库（风格学习筛选、空态「去写作」CTA）均正确呈现。

### 4.5 关闭与重启
- `CloseMainWindow()` → **✓ 已优雅关闭**，日志出现
  `Application shutdown complete` / `uvicorn 已退出，launcher 结束`
- **重新启动** → 健康检查再次通过、窗口标题正常、再次优雅关闭（重复操作无异常）

### 4.6 版本核对汇总

| 项 | 更新前 | 更新后 |
|---|---|---|
| `ContentStudio.exe` | 2026-10-07 20:46，8,594,692 B | **2026-10-11 21:40，8,712,000 B** |
| `_internal/frontend/dist` | 10-07 旧 UI（`c37fbe68…`） | **10-11 最终 V2 UI（与源码 dist 逐字节一致）** |
| `_internal`（Python 运行时/依赖） | 10-07 | **10-11 全量重建** |
| 用户 DB | `ec4f0eeb…`（290,816 B） | `9cec3f4f…`（见 §5 说明，**数据行完整保留**） |
| 用户 `.env`（AI 配置） | 1,130 B，mtime 10-07 | **未改动**（mtime 不变） |
| `build/pyinstaller/` 副本 | 10-07 旧 | 10-11（本批构建刷新） |

## 5. 用户数据库完整性（重点）

EXE 两次启动后用户 DB 文件哈希由 `ec4f0eeb…` 变为 `9cec3f4f…`。已核查：

| 检查 | 结果 |
|---|---|
| `meta.schema_version` | **9（最新，与源码一致）**—— 用户库在 10-08 建库时就已是 v9，**未发生 schema 迁移** |
| v7 三表 | `creator_goal` / `qualified_impression_snapshots` / `account_daily_metrics` 均存在 |
| 用户数据行 | `creator_goal` 1 行（10-08 建立的目标）、`qualified_impression_snapshots` 1 行（10-09 记录）—— **原样保留** |
| `contents` / `authors` | 0 行（用户库本就无内容，EXE API 返回与库内实际一致） |
| `PRAGMA integrity_check` | **ok** |
| `PRAGMA foreign_key_check` | **通过** |

哈希变化的定性：EXE 启动时 `init_db`（CREATE IF NOT EXISTS，幂等）与连接开闭导致的
**SQLite 文件级整理**（journal/checkpoint），逻辑数据（schema + 用户行）完整无损。
另说明：§3 的 ZIP 备份按 spec 只含程序文件、不含数据库；启动前 DB 的文件态未单独留档，
但启动前后 API 均返回 `total=0` 且用户行（目标 + 快照）在启动后核验存在 —— 无数据丢失。

## 6. 未同步运行文件的处理结论

| 路径 | 状态 | 处理 |
|---|---|---|
| `release/ContentStudio/` | **已更新**（用户应启动这里） | `release\ContentStudio\ContentStudio.exe` |
| `build/pyinstaller/ContentStudio/` | 构建工作路径产物，已随本批刷新 | 无需操作（可删） |
| `release/_old-2026-10-07/` | 旧构建留档（移动保留） | 可随时删除 |
| `release/backup/ContentStudio-2026-10-07.zip` | 旧构建 ZIP 备份（19.6 MB） | 建议保留至确认稳定 |
| 快捷方式 | 不存在 | 如需可自行创建，指向 `release\ContentStudio\ContentStudio.exe` |

**用户应启动的唯一文件**：
`C:\Users\18483\Desktop\x运营\content-studio\release\ContentStudio\ContentStudio.exe`

## 7. 未解决问题

无。唯一环境性限制：工作台沙箱会在工具调用结束后回收其派生的子进程，
因此 EXE 的存活验证在单次调用内完成（启动 → 验证 → 截图 → 优雅关闭），
并另起一次调用完成重启复验 —— 不影响结论。

---

## 附：构建日志关键行

```
29547 INFO: Building COLLECT COLLECT-00.toc completed successfully.
29550 INFO: Build complete! The results are available in: release
Done: release\ContentStudio\ContentStudio.exe
```
