/**
 * 多文件导入的纯逻辑层。
 *
 * 刻意与 React 解耦：所有函数都是「输入 draft 数组 → 输出新数组」，
 * 不碰 DOM、不发请求，因此可以直接被 node --test 覆盖。
 */

import type { ImportBatchFile, ImportFileResult } from "./types.ts"

/** 预览中的单个文件。id 用于 React key，与后端返回的文件名无关（文件名可能重复）。 */
export interface ImportFileDraft {
  id: string
  filename: string
  ok: boolean
  error: string | null
  items: string[]
  metadatas: Record<string, unknown>[]
  /** 该文件出现过的作者（已规范化） */
  authors: string[]
  /** 缺作者标识的条数 */
  missingAuthorCount: number
  /** 预计重复（库里已有 + 本批次前面文件已占用） */
  duplicateCount: number
  /** 预计新增 */
  newCount: number
}

export type AuthorScope = "all" | "missing"

/**
 * 与后端 repository.normalize_author_username 完全一致的规范化：
 * trim → 去开头 @ → 小写。
 * 前端保留一份是为了让预览显示的值与最终入库值一致。
 */
export function normalizeAuthor(raw: string | null | undefined): string {
  if (!raw) return ""
  let value = String(raw).trim()
  if (value.startsWith("@")) value = value.slice(1)
  return value.trim().toLowerCase()
}

let _seq = 0

/** 生成 draft id。刻意不用 crypto.randomUUID（它要求安全上下文）。 */
export function nextDraftId(): string {
  _seq += 1
  return `f${_seq}`
}

/** 把后端返回的单文件结果转成前端 draft。 */
export function toDraft(file: ImportBatchFile): ImportFileDraft {
  return {
    id: nextDraftId(),
    filename: file.filename,
    ok: file.ok,
    error: file.error,
    items: [...file.items],
    metadatas: file.metadatas.map((m) => ({ ...m })),
    authors: [...file.authors],
    missingAuthorCount: file.missing_author_count,
    duplicateCount: file.duplicate_count,
    newCount: file.new_count,
  }
}

/** 从一个 draft 的 metadatas 重算作者集合与缺失计数（改过 metabata 之后必须重算）。 */
function recount(file: ImportFileDraft): ImportFileDraft {
  const authors = new Set<string>()
  let missing = 0
  for (const meta of file.metadatas) {
    const username = normalizeAuthor(meta.author_username as string | undefined)
    if (username) authors.add(username)
    else missing += 1
  }
  return { ...file, authors: [...authors].sort(), missingAuthorCount: missing }
}

/**
 * 给单个文件批量指定作者。
 *
 * scope = "all"     覆盖该文件的全部条目
 * scope = "missing" 只补当前没有 author_username 的条目
 *
 * 刻意**清掉** author_display_name：用户手填的是用户名，原文件的显示名
 * 不再对得上，留着会比没有更糟。
 */
export function assignAuthor(
  file: ImportFileDraft,
  rawUsername: string,
  scope: AuthorScope
): ImportFileDraft {
  const username = normalizeAuthor(rawUsername)
  if (!username) return file

  const metadatas = file.metadatas.map((meta) => {
    const has = Boolean(normalizeAuthor(meta.author_username as string | undefined))
    if (scope === "missing" && has) return meta
    const next: Record<string, unknown> = { ...meta, author_username: username }
    delete next.author_display_name
    return next
  })

  return recount({ ...file, metadatas })
}

/** 移除某文件里的第 index 条；顺带修正预计新增数（重复数无法本地重算，保持不变）。 */
export function removeItemAt(file: ImportFileDraft, index: number): ImportFileDraft {
  const wasNew = index < file.newCount
  const items = file.items.filter((_, i) => i !== index)
  const metadatas = file.metadatas.filter((_, i) => i !== index)
  return recount({
    ...file,
    items,
    metadatas,
    newCount: Math.max(0, file.newCount - (wasNew ? 1 : 0)),
    duplicateCount: Math.max(0, file.duplicateCount - (wasNew ? 0 : 1)),
  })
}

export interface DraftSummary {
  files: number
  okFiles: number
  failedFiles: number
  count: number
  newCount: number
  duplicateCount: number
  missingAuthorCount: number
}

export function summarizeDrafts(files: ImportFileDraft[]): DraftSummary {
  const ok = files.filter((f) => f.ok)
  return {
    files: files.length,
    okFiles: ok.length,
    failedFiles: files.length - ok.length,
    count: ok.reduce((n, f) => n + f.items.length, 0),
    newCount: ok.reduce((n, f) => n + Math.min(f.newCount, f.items.length), 0),
    duplicateCount: ok.reduce((n, f) => n + f.duplicateCount, 0),
    missingAuthorCount: ok.reduce((n, f) => n + f.missingAuthorCount, 0),
  }
}

/** 真正要提交的文件：解析成功且还有内容的。 */
export function committableFiles(files: ImportFileDraft[]): ImportFileDraft[] {
  return files.filter((f) => f.ok && f.items.length > 0)
}

export interface ImportOutcome {
  okFiles: number
  failedFiles: number
  created: number
  duplicates: number
  skipped: number
  /** 全部成功才 true —— 局部失败绝不能报成全部成功 */
  allOk: boolean
}

export function summarizeResults(results: ImportFileResult[]): ImportOutcome {
  const ok = results.filter((r) => r.ok)
  return {
    okFiles: ok.length,
    failedFiles: results.length - ok.length,
    created: results.reduce((n, r) => n + r.created, 0),
    duplicates: results.reduce((n, r) => n + r.duplicates, 0),
    skipped: results.reduce((n, r) => n + r.skipped, 0),
    allOk: results.length > 0 && ok.length === results.length,
  }
}

/** 可撤销/可分析的批次 id（只取成功的）。 */
export function batchIds(results: ImportFileResult[]): string[] {
  return results
    .filter((r) => r.ok && r.import_batch_id)
    .map((r) => r.import_batch_id as string)
}
