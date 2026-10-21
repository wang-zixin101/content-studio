/**
 * 内容库列表行的**主文本**（Stage 12 走查修复）。
 *
 * ------------------------------------------------------------------
 * 规则：永远显示原文
 * ------------------------------------------------------------------
 * 以前行主文本是 `summary || excerpt`，于是同一个内容库里：
 *   有 AI 摘要的 → 显示总结句
 *   没有摘要的   → 显示原帖正文
 * 两种文本混排，用户无法快速识别原帖。
 *
 * 现在统一走 `excerpt`：它由后端**只从 raw_text 派生**
 * （去换行、截断 160 字），与 summary 无关。
 *
 * 刻意说明：
 *   - summary **数据不删除**，生成 / 保存 / API / DB schema 全都不动
 *   - 内容详情页仍可正常使用 summary / analysis
 *   - 搜索、筛选、标签、作者、状态等一律不受影响
 *   - 本函数**不接受也不返回** summary，从签名上就不可能让摘要重新混进列表
 */

export interface RowTextSource {
  /** 后端从 raw_text 派生的列表摘要（去换行、≤160 字） */
  excerpt?: string | null
  /** 仅作为 excerpt 缺失时的兜底 */
  raw_text?: string | null
}

export function contentRowMainText(item: RowTextSource): string {
  const excerpt = (item.excerpt ?? "").trim()
  if (excerpt) return excerpt
  return (item.raw_text ?? "").trim()
}
