import { describe, it } from "node:test"
import assert from "node:assert"

// 直接 import 生产实现，不复制函数副本
// （node --test 的类型剥离要求写完整扩展名，与 collect-all-ids.test.ts 一致）
import { authorLabel, authorSubLabel } from "./author-display.ts"

describe("作者显示", () => {
  it("有 display_name 时显示名称，副标题显示 username", () => {
    assert.strictEqual(authorLabel("王长富", "wangchangfu88"), "王长富")
    assert.strictEqual(
      authorSubLabel("王长富", "wangchangfu88", 500),
      "@wangchangfu88 500 帖"
    )
  })

  it("无 display_name 时显示 @username，副标题只显示帖数", () => {
    assert.strictEqual(authorLabel(null, "btcmos"), "@btcmos")
    assert.strictEqual(authorSubLabel(null, "btcmos", 480), "480 帖")
  })
})
