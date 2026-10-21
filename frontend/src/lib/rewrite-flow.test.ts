import { test } from "node:test"
import assert from "node:assert/strict"

import type { ReferenceMode, SourceSlice } from "./rewrite-flow.ts"
import {
  addSeen,
  canGenerate,
  CREATION_PATHS,
  defaultReferenceMode,
  hasSourceInput,
  initialSourceSlice,
  isFormalAuthor,
  referenceModeOnSourceChange,
  resetSeen,
  resetSourceOnModeSwitch,
  resolveCreationPath,
  resolveReferenceAuthor,
  selectReferenceAuthor,
  sourceFieldsFor,
} from "./rewrite-flow.ts"

// ---------------- 参考作者解析（spec §41：11–19）----------------

test("11/12 默认策略：有素材作者 → follow_source，无 → none", () => {
  assert.equal(defaultReferenceMode(12), "follow_source")
  assert.equal(defaultReferenceMode(null), "none")
})

test("13 follow_source 使用正确的素材 author_id", () => {
  assert.equal(resolveReferenceAuthor("follow_source", 12, 99), 12)
})

test("14 none：不加载任何单作者画像", () => {
  assert.equal(resolveReferenceAuthor("none", 12, 99), null)
})

test("15 fixed author：可以与素材作者不同", () => {
  assert.equal(resolveReferenceAuthor("fixed", 12, 99), 99)
})

test("16 follow_source：换素材后跟随新素材作者", () => {
  const next = referenceModeOnSourceChange("follow_source", 77)
  assert.equal(next.mode, "follow_source")
  assert.equal(resolveReferenceAuthor(next.mode, 77, next.fixedAuthorId), 77)
})

test("17 fixed author：换素材后仍是同一个作者", () => {
  const next = referenceModeOnSourceChange("fixed", 77, 99)
  assert.equal(next.mode, "fixed")
  assert.equal(next.fixedAuthorId, 99)
  assert.equal(resolveReferenceAuthor(next.mode, 77, next.fixedAuthorId), 99)
})

test("18 none：换素材后仍然是 none", () => {
  const next = referenceModeOnSourceChange("none", 77, 99)
  assert.equal(next.mode, "none")
  assert.equal(resolveReferenceAuthor(next.mode, 77, next.fixedAuthorId), null)
})

test("19 素材无作者时 follow_source 退化为 none（绝不猜作者）", () => {
  const next = referenceModeOnSourceChange("follow_source", null)
  assert.equal(next.mode, "none")
  assert.equal(resolveReferenceAuthor(next.mode, null, next.fixedAuthorId), null)
})

test("19b blank / null 绝不是正式作者", () => {
  assert.equal(isFormalAuthor(null), false)
  assert.equal(isFormalAuthor(undefined), false)
  assert.equal(isFormalAuthor(0), false)
  assert.equal(isFormalAuthor(12), true)
})

// ---------------- Stage 11 P0 回归：切参考作者不得动素材状态 ----------------

/** 一次随机素材选择后的完整素材状态。 */
const SOURCE_STATE = {
  sourceEntry: "random",
  sourceContentId: 123,
  sourceText: "A",
  sourceAuthorId: 7,
  sourceAuthorLabel: "@lyulog",
  pool: "all",
  seen: [123, 456],
}

/**
 * 模拟 Write 页里 `chooseReference` 的效果：
 * 只允许写入 refMode / fixedAuthorId，其余字段原样返回。
 */
function applyChooseReference(
  state: typeof SOURCE_STATE & {
    refMode: ReferenceMode
    fixedAuthorId: number | null
  },
  choice:
    | { kind: "none" }
    | { kind: "follow_source" }
    | { kind: "fixed"; authorId: number }
) {
  const next = selectReferenceAuthor(
    { mode: state.refMode, fixedAuthorId: state.fixedAuthorId },
    choice
  )
  return {
    ...state,
    refMode: next.mode,
    fixedAuthorId: next.fixedAuthorId,
  }
}

const BASE = {
  ...SOURCE_STATE,
  refMode: "follow_source" as ReferenceMode,
  fixedAuthorId: null as number | null,
}

function assertSourceUntouched(after: typeof BASE) {
  assert.deepEqual(
    {
      sourceEntry: after.sourceEntry,
      sourceContentId: after.sourceContentId,
      sourceText: after.sourceText,
      sourceAuthorId: after.sourceAuthorId,
      sourceAuthorLabel: after.sourceAuthorLabel,
      pool: after.pool,
      seen: after.seen,
    },
    SOURCE_STATE
  )
}

test("随机素材已选 → 切「不指定」，素材状态全部不变", () => {
  const after = applyChooseReference(BASE, { kind: "none" })
  assert.equal(after.refMode, "none")
  assert.equal(after.fixedAuthorId, null)
  assertSourceUntouched(after)
})

test("随机素材已选 → 切「跟随素材作者」，素材状态全部不变", () => {
  const after = applyChooseReference({ ...BASE, refMode: "none" }, { kind: "follow_source" })
  assert.equal(after.refMode, "follow_source")
  assertSourceUntouched(after)
})

test("随机素材已选 → 切 fixed「王长富」，素材状态全部不变（重点）", () => {
  const after = applyChooseReference(BASE, { kind: "fixed", authorId: 2 })
  assert.equal(after.refMode, "fixed")
  assert.equal(after.fixedAuthorId, 2)
  // 素材作者必须仍是 @lyulog，绝不能被参考作者覆盖
  assert.equal(after.sourceAuthorId, 7)
  assert.equal(after.sourceAuthorLabel, "@lyulog")
  assert.equal(after.sourceContentId, 123)
  assert.equal(after.sourceText, "A")
  // 参考作者解析结果 = 王长富，与素材作者独立
  assert.equal(resolveReferenceAuthor(after.refMode, after.sourceAuthorId, after.fixedAuthorId), 2)
})

test("fixed 参考作者换素材后仍保留（Stage 10 #17 / #37）", () => {
  const after = applyChooseReference(BASE, { kind: "fixed", authorId: 2 })
  const nextMaterial = referenceModeOnSourceChange(after.refMode, null, after.fixedAuthorId)
  assert.equal(nextMaterial.mode, "fixed")
  assert.equal(nextMaterial.fixedAuthorId, 2, "继续下一条后 fixed 参考作者必须保留")
  assert.equal(resolveReferenceAuthor(nextMaterial.mode, null, nextMaterial.fixedAuthorId), 2)
})

test("none / follow 换素材后不会退化成 fixed", () => {
  assert.equal(referenceModeOnSourceChange("none", 9, null).mode, "none")
  assert.equal(referenceModeOnSourceChange("follow_source", 9, null).mode, "follow_source")
})

// ---------------- seen（spec §40：7/8）----------------

test("7 当前 session 不重复展示已抽过的素材", () => {
  let seen: number[] = []
  seen = addSeen(seen, 81)
  seen = addSeen(seen, 263)
  seen = addSeen(seen, 81)
  assert.deepEqual(seen, [81, 263])
})

test("8 切换素材池 → seen 清空", () => {
  let seen = addSeen(addSeen([], 1), 2)
  seen = resetSeen()
  assert.deepEqual(seen, [])
})

// ---------------- 04-C：五种创作输入路径 ----------------

test("04C-1 原帖改写：有素材 + 不指定参考作者 → rewrite", () => {
  assert.equal(
    resolveCreationPath({
      mode: "benchmark",
      hasSource: true,
      referenceAuthorId: null,
      useProfile: false,
    }),
    "rewrite"
  )
})

test("04C-2 原帖 + 参考作者：启用画像 → rewrite_author", () => {
  assert.equal(
    resolveCreationPath({
      mode: "benchmark",
      hasSource: true,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    "rewrite_author"
  )
})

test("04C-3 路径依据是「选了作者」，不是「勾选了画像开关」", () => {
  // 作者的流量画像与跨账号数据只要选中就已进入生成；
  // useProfile 只是「要不要再注入写作画像」的更细开关。
  assert.equal(
    resolveCreationPath({
      mode: "benchmark",
      hasSource: true,
      referenceAuthorId: 12,
      useProfile: false,
    }),
    "rewrite_author"
  )
})

test("04C-3b 没选作者 → 绝不可能是 *_author 路径", () => {
  assert.equal(
    resolveCreationPath({
      mode: "benchmark",
      hasSource: true,
      referenceAuthorId: null,
      useProfile: true,
    }),
    "rewrite"
  )
})

test("04C-4 仅主题创作：idea 模式 + 无作者 → topic", () => {
  assert.equal(
    resolveCreationPath({
      mode: "idea",
      hasSource: true,
      referenceAuthorId: null,
      useProfile: false,
    }),
    "topic"
  )
})

test("04C-5 主题 + 作者：idea 模式 + 启用画像 → topic_author", () => {
  assert.equal(
    resolveCreationPath({
      mode: "idea",
      hasSource: true,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    "topic_author"
  )
})

test("04C-6 仅参考作者自主选题：author 模式 + 无素材 + 启用画像 → author_auto", () => {
  assert.equal(
    resolveCreationPath({
      mode: "author",
      hasSource: false,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    "author_auto"
  )
})

test("04C-7 author 模式还没选作者 → null（不猜路径）", () => {
  assert.equal(
    resolveCreationPath({
      mode: "author",
      hasSource: false,
      referenceAuthorId: null,
      useProfile: false,
    }),
    null
  )
  assert.equal(
    resolveCreationPath({
      mode: "author",
      hasSource: false,
      referenceAuthorId: null,
      useProfile: true,
    }),
    null
  )
})

test("04C-7b author 模式只选了作者、没勾画像开关 → author_auto", () => {
  assert.equal(
    resolveCreationPath({
      mode: "author",
      hasSource: false,
      referenceAuthorId: 12,
      useProfile: false,
    }),
    "author_auto"
  )
})

test("04C-8 author 模式竟然有素材 → null（冲突状态，拦住而不是猜）", () => {
  assert.equal(
    resolveCreationPath({
      mode: "author",
      hasSource: true,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    null
  )
})

test("04C-9 没有素材的其它模式 → null", () => {
  assert.equal(
    resolveCreationPath({
      mode: "benchmark",
      hasSource: false,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    null
  )
  assert.equal(
    resolveCreationPath({
      mode: "idea",
      hasSource: false,
      referenceAuthorId: 12,
      useProfile: true,
    }),
    null
  )
})

test("04C-10 五种路径全部可枚举且互不重复", () => {
  assert.equal(CREATION_PATHS.length, 5)
  assert.equal(new Set(CREATION_PATHS).size, 5)
})

test("04C-11 canGenerate 只在路径非空时为真", () => {
  assert.equal(canGenerate("author_auto"), true)
  assert.equal(canGenerate("topic"), true)
  assert.equal(canGenerate(null), false)
})

// ---------------- 04-C 第二轮：切换模式必须清除旧素材 id ----------------

test("04C-R2-1 切换模式后的素材态是干净的（含 contentId 与 entry）", () => {
  const clean = resetSourceOnModeSwitch()
  assert.equal(clean.sourceText, "")
  assert.equal(clean.sourceContentId, null, "旧素材 id 必须被清除")
  assert.equal(clean.sourceEntry, "specified", "素材入口必须回到默认")
  assert.equal(clean.sourceAuthorId, null)
  assert.equal(clean.sourceAuthorLabel, null)
})

test("04C-R2-2 initialSourceSlice 与切换重置一致", () => {
  assert.deepEqual(initialSourceSlice(), resetSourceOnModeSwitch())
})

test("04C-R2-3 hasSourceInput 把 content_id 也算作素材", () => {
  assert.equal(hasSourceInput({ sourceText: "有文本", sourceContentId: null }), true)
  assert.equal(
    hasSourceInput({ sourceText: "", sourceContentId: 123 }),
    true,
    "只给 content_id 时服务端同样会取回原文，必须算有素材"
  )
  assert.equal(hasSourceInput({ sourceText: "   ", sourceContentId: null }), false)
  assert.equal(hasSourceInput({ sourceText: "", sourceContentId: null }), false)
})

test("04C-R2-4 复现场景①：对标改写选素材 → 切到作者起稿，请求不得携带旧 id", () => {
  // 步骤 1：对标改写模式下从内容库选了一条素材
  const picked: SourceSlice = {
    sourceText: "内容库里的一条对标素材。",
    sourceContentId: 123,
    sourceEntry: "random",
    sourceAuthorId: 7,
    sourceAuthorLabel: "@lyulog",
  }
  assert.equal(hasSourceInput(picked), true)
  assert.equal(resolveCreationPath({
    mode: "benchmark", hasSource: hasSourceInput(picked),
    referenceAuthorId: null, useProfile: false,
  }), "rewrite")

  // 步骤 2：切到「作者起稿」—— 素材态整片重置
  const after = resetSourceOnModeSwitch()

  // 步骤 3：新请求不得携带旧素材 id
  const fields = sourceFieldsFor(after)
  assert.equal(fields.source_content_id, null, "请求仍携带了旧素材 id")
  assert.equal("source_text" in fields, false, "作者起稿不该带素材文本")
  // 路径必须是自主选题，而不是冲突态
  assert.equal(resolveCreationPath({
    mode: "author", hasSource: hasSourceInput(after),
    referenceAuthorId: 12, useProfile: true,
  }), "author_auto")
})

test("04C-R2-5 复现场景②：切到灵感起稿后新主题不得关联旧素材", () => {
  const after = resetSourceOnModeSwitch()
  // 用户输入新主题
  const withTopic = { ...after, sourceText: "我想写一条关于 AI 工具的新内容" }

  const fields = sourceFieldsFor(withTopic)
  assert.equal(fields.source_content_id, null, "新主题被错误关联到旧素材 id")
  assert.equal(fields.source_text, "我想写一条关于 AI 工具的新内容")
  assert.equal(resolveCreationPath({
    mode: "idea", hasSource: hasSourceInput(withTopic),
    referenceAuthorId: null, useProfile: false,
  }), "topic")
})

test("04C-R2-6 若旧 id 真的残留，hasSourceInput 会把它拦住（纵深防御）", () => {
  // 即使重置函数将来被改坏，只要 id 还在，作者起稿就判定为冲突态而非 author_auto
  const stale = { sourceText: "", sourceContentId: 123 }
  assert.equal(hasSourceInput(stale), true)
  assert.equal(resolveCreationPath({
    mode: "author", hasSource: true,
    referenceAuthorId: 12, useProfile: true,
  }), null, "作者起稿 + 有素材 = 冲突，必须拦住而不是猜路径")
})

test("04C-R2-7 同一模式内选素材 / 换素材不受影响", () => {
  // 对标改写模式内正常选素材：content_id 必须照常带出去
  const picked: SourceSlice = {
    sourceText: "素材正文。",
    sourceContentId: 123,
    sourceEntry: "random",
    sourceAuthorId: 7,
    sourceAuthorLabel: "@lyulog",
  }
  const fields = sourceFieldsFor(picked)
  assert.equal(fields.source_content_id, 123)
  assert.equal(fields.source_text, "素材正文。")
  assert.equal(resolveCreationPath({
    mode: "benchmark", hasSource: hasSourceInput(picked),
    referenceAuthorId: 7, useProfile: true,
  }), "rewrite_author")
})

test("04C-R2-8 sourceFieldsFor 只在文本非空时带 source_text", () => {
  assert.deepEqual(
    sourceFieldsFor({ sourceText: "", sourceContentId: null }),
    { source_content_id: null }
  )
  assert.deepEqual(
    sourceFieldsFor({ sourceText: "  ", sourceContentId: 5 }),
    { source_content_id: 5 }
  )
})
