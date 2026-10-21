import { describe, it } from "node:test"
import assert from "node:assert"

import {
  assignAuthor,
  batchIds,
  committableFiles,
  normalizeAuthor,
  removeItemAt,
  summarizeDrafts,
  summarizeResults,
  toDraft,
  type ImportFileDraft,
} from "./import-batch.ts"
import type { ImportBatchFile, ImportFileResult } from "./types.ts"

function batchFile(over: Partial<ImportBatchFile> = {}): ImportBatchFile {
  return {
    filename: "a.json",
    ok: true,
    error: null,
    count: 2,
    items: ["第一条正文内容", "第二条正文内容"],
    metadatas: [
      { external_id: "1", author_username: "H0peYin" },
      { external_id: "2", author_username: "H0peYin" },
    ],
    authors: ["h0peyin"],
    missing_author_count: 0,
    duplicate_count: 0,
    new_count: 2,
    ...over,
  }
}

function draft(over: Partial<ImportFileDraft> = {}): ImportFileDraft {
  return { ...toDraft(batchFile()), ...over }
}

describe("normalizeAuthor", () => {
  it("trim / 去开头 @ / 小写，与后端 normalize_author_username 一致", () => {
    assert.equal(normalizeAuthor("H0peYin"), "h0peyin")
    assert.equal(normalizeAuthor("@H0peYin"), "h0peyin")
    assert.equal(normalizeAuthor("  @H0peYin  "), "h0peyin")
    // 与后端一致：只去掉**一个**开头的 @
    assert.equal(normalizeAuthor("@@x"), "@x")
  })

  it("空值一律归一为空串", () => {
    assert.equal(normalizeAuthor(null), "")
    assert.equal(normalizeAuthor(undefined), "")
    assert.equal(normalizeAuthor(""), "")
    assert.equal(normalizeAuthor("@"), "")
    assert.equal(normalizeAuthor("   "), "")
  })
})

describe("toDraft", () => {
  it("复制 items / metadatas，不共享引用", () => {
    const src = batchFile()
    const d = toDraft(src)
    assert.equal(d.items.length, 2)
    assert.notEqual(d.items, src.items)
    assert.notEqual(d.metadatas[0], src.metadatas[0])
    // 改 draft 不影响源
    d.metadatas[0].author_username = "changed"
    assert.equal(src.metadatas[0].author_username, "H0peYin")
  })

  it("保留后端给出的作者集合与缺失计数", () => {
    const d = toDraft(batchFile({ authors: ["a", "b"], missing_author_count: 3 }))
    assert.deepEqual(d.authors, ["a", "b"])
    assert.equal(d.missingAuthorCount, 3)
  })
})

describe("assignAuthor", () => {
  const noAuthor = draft({
    metadatas: [
      { external_id: "1" },
      { external_id: "2", author_username: "someone" },
      { external_id: "3" },
    ],
    authors: ["someone"],
    missingAuthorCount: 2,
  })

  it("scope=missing 只补没有作者的条目，已有作者不动", () => {
    const out = assignAuthor(noAuthor, "@NewOne", "missing")
    assert.equal(out.metadatas[0].author_username, "newone")
    assert.equal(out.metadatas[1].author_username, "someone")
    assert.equal(out.metadatas[2].author_username, "newone")
    assert.equal(out.missingAuthorCount, 0)
    assert.deepEqual(out.authors, ["newone", "someone"])
  })

  it("scope=all 覆盖全部条目", () => {
    const out = assignAuthor(noAuthor, "Owner", "all")
    assert.deepEqual(
      out.metadatas.map((m) => m.author_username),
      ["owner", "owner", "owner"]
    )
    assert.deepEqual(out.authors, ["owner"])
    assert.equal(out.missingAuthorCount, 0)
  })

  it("指定作者时清掉旧的 author_display_name（避免显示名与新用户名对不上）", () => {
    const withName = draft({
      metadatas: [{ external_id: "1", author_username: "old", author_display_name: "旧名字" }],
    })
    const out = assignAuthor(withName, "new", "all")
    assert.equal(out.metadatas[0].author_username, "new")
    assert.equal("author_display_name" in out.metadatas[0], false)
  })

  it("空用户名不做任何改动", () => {
    const out = assignAuthor(noAuthor, "   ", "all")
    assert.deepEqual(out.metadatas, noAuthor.metadatas)
    assert.equal(out.missingAuthorCount, 2)
  })

  it("不修改原对象", () => {
    const before = JSON.stringify(noAuthor)
    assignAuthor(noAuthor, "x", "all")
    assert.equal(JSON.stringify(noAuthor), before)
  })
})

describe("removeItemAt", () => {
  it("删除条目并同步移除对应 metadata", () => {
    const d = draft()
    const out = removeItemAt(d, 0)
    assert.equal(out.items.length, 1)
    assert.equal(out.items[0], "第二条正文内容")
    assert.equal(out.metadatas.length, 1)
    assert.equal(out.metadatas[0].external_id, "2")
  })

  it("删掉预计新增的条目时 newCount 减 1", () => {
    const d = draft({ newCount: 2, duplicateCount: 0 })
    assert.equal(removeItemAt(d, 0).newCount, 1)
  })

  it("删掉预计重复的条目时 duplicateCount 减 1、newCount 不变", () => {
    const d = draft({ newCount: 1, duplicateCount: 1 })
    assert.equal(removeItemAt(d, 1).newCount, 1)
    assert.equal(removeItemAt(d, 1).duplicateCount, 0)
  })

  it("计数不会变成负数", () => {
    const d = draft({ newCount: 0, duplicateCount: 0 })
    const out = removeItemAt(d, 0)
    assert.equal(out.newCount, 0)
    assert.equal(out.duplicateCount, 0)
  })

  it("删除后重算作者集合与缺失计数", () => {
    const d = draft({
      metadatas: [{ external_id: "1", author_username: "a" }, { external_id: "2" }],
      authors: ["a"],
      missingAuthorCount: 1,
    })
    const out = removeItemAt(d, 1)
    assert.deepEqual(out.authors, ["a"])
    assert.equal(out.missingAuthorCount, 0)
  })
})

describe("summarizeDrafts", () => {
  it("统计文件数、条数、预计新增、重复、缺作者", () => {
    const s = summarizeDrafts([
      draft({ newCount: 2, duplicateCount: 1, missingAuthorCount: 1 }),
      draft({ newCount: 1, duplicateCount: 0 }),
    ])
    assert.equal(s.files, 2)
    assert.equal(s.okFiles, 2)
    assert.equal(s.failedFiles, 0)
    assert.equal(s.count, 4)
    assert.equal(s.newCount, 3)
    assert.equal(s.duplicateCount, 1)
    assert.equal(s.missingAuthorCount, 1)
  })

  it("失败文件不计入条数，但计入 failedFiles", () => {
    const s = summarizeDrafts([
      draft(),
      toDraft(batchFile({ filename: "bad.json", ok: false, error: "JSON 文件无法解析", count: 0, items: [], metadatas: [], authors: [], new_count: 0 })),
    ])
    assert.equal(s.files, 2)
    assert.equal(s.okFiles, 1)
    assert.equal(s.failedFiles, 1)
    assert.equal(s.count, 2)
  })

  it("newCount 不会被算得超过实际条数", () => {
    const s = summarizeDrafts([draft({ newCount: 99 })])
    assert.equal(s.newCount, 2)
  })

  it("空数组返回全 0", () => {
    const s = summarizeDrafts([])
    assert.deepEqual(s, {
      files: 0, okFiles: 0, failedFiles: 0,
      count: 0, newCount: 0, duplicateCount: 0, missingAuthorCount: 0,
    })
  })
})

describe("committableFiles", () => {
  it("只提交解析成功且还有内容的文件", () => {
    const ok = draft()
    const failed = toDraft(batchFile({ ok: false, error: "boom" }))
    const emptied = draft({ items: [], metadatas: [] })
    const out = committableFiles([ok, failed, emptied])
    assert.equal(out.length, 1)
    assert.equal(out[0].id, ok.id)
  })
})

describe("summarizeResults", () => {
  const ok = (filename: string, created: number, duplicates = 0): ImportFileResult => ({
    filename, ok: true, created, duplicates, skipped: 0, error: null,
    import_batch_id: `b-${filename}`,
  })
  const bad = (filename: string): ImportFileResult => ({
    filename, ok: false, created: 0, duplicates: 0, skipped: 0,
    error: "导入失败", import_batch_id: null,
  })

  it("全部成功时 allOk 为 true", () => {
    const s = summarizeResults([ok("a.json", 10), ok("b.json", 5, 2)])
    assert.equal(s.allOk, true)
    assert.equal(s.okFiles, 2)
    assert.equal(s.failedFiles, 0)
    assert.equal(s.created, 15)
    assert.equal(s.duplicates, 2)
  })

  it("局部失败时 allOk 必须为 false —— 不能误报全部成功", () => {
    const s = summarizeResults([ok("a.json", 10), bad("b.json")])
    assert.equal(s.allOk, false)
    assert.equal(s.okFiles, 1)
    assert.equal(s.failedFiles, 1)
    assert.equal(s.created, 10)
  })

  it("全部失败时 allOk 为 false", () => {
    const s = summarizeResults([bad("a.json")])
    assert.equal(s.allOk, false)
    assert.equal(s.failedFiles, 1)
    assert.equal(s.created, 0)
  })

  it("空结果 allOk 为 false（没提交过就不算成功）", () => {
    assert.equal(summarizeResults([]).allOk, false)
  })
})

describe("batchIds", () => {
  it("只返回成功且有 id 的批次", () => {
    const ids = batchIds([
      { filename: "a", ok: true, created: 1, duplicates: 0, skipped: 0, error: null, import_batch_id: "x" },
      { filename: "b", ok: false, created: 0, duplicates: 0, skipped: 0, error: "e", import_batch_id: null },
      { filename: "c", ok: true, created: 0, duplicates: 3, skipped: 0, error: null, import_batch_id: "y" },
    ])
    assert.deepEqual(ids, ["x", "y"])
  })
})
