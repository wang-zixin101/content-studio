/**
 * 「全选全部」分页收集器的回归测试。
 *
 * 背景：后端列表接口 limit 上限为 100，前端曾误传 200 导致
 * 「Input should be less than or equal to 100」。这里守住分页行为。
 *
 * 运行：node --test src/lib/collect-all-ids.test.ts
 */

import assert from "node:assert/strict"
import { test } from "node:test"

import { collectAllIds, MAX_PAGE_SIZE } from "./collect-all-ids.ts"

/** 假分页源：按 offset/limit 返回 {id} 列表，并记录每次请求参数。 */
function fakeSource(total: number) {
  const calls: { limit: number; offset: number }[] = []
  const fetcher = async ({ limit, offset }: { limit: number; offset: number }) => {
    calls.push({ limit, offset })
    const end = Math.min(offset + limit, total)
    const items: { id: number }[] = []
    for (let i = offset; i < end; i += 1) items.push({ id: 10000 + i })
    return { items, total }
  }
  return { fetcher, calls }
}

test("每次请求的 limit 都不超过后端上限 100", async () => {
  const { fetcher, calls } = fakeSource(500)
  await collectAllIds(fetcher, { total: 500 })

  assert.ok(calls.length > 0)
  for (const call of calls) {
    assert.ok(
      call.limit <= MAX_PAGE_SIZE,
      `limit=${call.limit} 超过了上限 ${MAX_PAGE_SIZE}`
    )
    assert.equal(call.limit, 100)
  }
})

test("total=30：得到 30 个，只请求一次", async () => {
  const { fetcher, calls } = fakeSource(30)
  const ids = await collectAllIds(fetcher, { total: 30 })

  assert.equal(ids.length, 30)
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0], { limit: 100, offset: 0 })
})

test("total=100：得到 100 个，只请求一次", async () => {
  const { fetcher, calls } = fakeSource(100)
  const ids = await collectAllIds(fetcher, { total: 100 })

  assert.equal(ids.length, 100)
  assert.equal(calls.length, 1, "刚好一页，不应多发一次空请求")
})

test("total=101：请求 2 次，得到 101 个", async () => {
  const { fetcher, calls } = fakeSource(101)
  const ids = await collectAllIds(fetcher, { total: 101 })

  assert.equal(ids.length, 101)
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1], { limit: 100, offset: 100 })
})

test("total=500：请求 5 次，得到 500 个", async () => {
  const { fetcher, calls } = fakeSource(500)
  const ids = await collectAllIds(fetcher, { total: 500 })

  assert.equal(ids.length, 500)
  assert.equal(calls.length, 5)
  assert.deepEqual(
    calls.map((c) => c.offset),
    [0, 100, 200, 300, 400]
  )
})

test("total=5000：不会因 limit 上限失败，请求 50 次", async () => {
  const { fetcher, calls } = fakeSource(5000)
  const ids = await collectAllIds(fetcher, { total: 5000 })

  assert.equal(ids.length, 5000)
  assert.equal(calls.length, 50)
  assert.ok(calls.every((c) => c.limit === 100))
})

test("ID 不重复且顺序稳定", async () => {
  const { fetcher } = fakeSource(250)
  const ids = await collectAllIds(fetcher, { total: 250 })

  assert.equal(new Set(ids).size, 250)
  assert.deepEqual(ids, [...ids].sort((a, b) => a - b))
})

test("传入过大的 pageSize 也会被钳制到 100", async () => {
  const { fetcher, calls } = fakeSource(500)
  const ids = await collectAllIds(fetcher, { total: 500, pageSize: 999 })

  assert.equal(ids.length, 500)
  assert.ok(
    calls.every((c) => c.limit === MAX_PAGE_SIZE),
    "pageSize 超限时必须钳制，而不是原样发给后端"
  )
})

test("中途请求失败：抛出错误，绝不返回半成品", async () => {
  let n = 0
  const fetcher = async ({ limit, offset }: { limit: number; offset: number }) => {
    n += 1
    if (n === 2) throw new Error("网络错误")
    return {
      items: Array.from({ length: limit }, (_, i) => ({ id: offset + i })),
      total: 500,
    }
  }

  await assert.rejects(
    () => collectAllIds(fetcher, { total: 500 }),
    /网络错误/,
    "失败必须传播给调用方，不能静默返回前 100 条"
  )
})

test("服务端提前返回空页时安全停止（不死循环）", async () => {
  let n = 0
  const fetcher = async ({ limit, offset }: { limit: number; offset: number }) => {
    n += 1
    if (n === 1) {
      return {
        items: Array.from({ length: limit }, (_, i) => ({ id: offset + i })),
        total: 500,
      }
    }
    return { items: [], total: 500 } // 数据被删掉了
  }

  const ids = await collectAllIds(fetcher, { total: 500 })
  assert.equal(ids.length, 100)
  assert.equal(n, 2)
})

test("total=0 时不发任何请求", async () => {
  const { fetcher, calls } = fakeSource(0)
  const ids = await collectAllIds(fetcher, { total: 0 })

  assert.deepEqual(ids, [])
  assert.equal(calls.length, 0)
})

test("onProgress 逐页上报进度", async () => {
  const { fetcher } = fakeSource(250)
  const progress: number[] = []
  await collectAllIds(fetcher, {
    total: 250,
    onProgress: (fetched) => progress.push(fetched),
  })

  assert.deepEqual(progress, [100, 200, 250])
})
