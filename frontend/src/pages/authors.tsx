import { useCallback, useEffect, useState } from "react"
import { Pencil, Plus, Search, Trash2, User } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { PageHeader } from "@/components/common"
import { api } from "@/lib/api"
import { authorLabel } from "@/lib/author-display"
import type { Author } from "@/lib/types"

export default function AuthorsPage() {
  const [authors, setAuthors] = useState<Author[]>([])
  const [q, setQ] = useState("")
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editAuthor, setEditAuthor] = useState<Author | null>(null)

  const [formUsername, setFormUsername] = useState("")
  const [formDisplayName, setFormDisplayName] = useState("")
  const [formNotes, setFormNotes] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // 管理页口径：全部正式作者，包括 0 内容作者
      const data = await api.listAuthors({ q })
      setAuthors(data.items || [])
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [q])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setFormUsername("")
    setFormDisplayName("")
    setFormNotes("")
    setEditAuthor(null)
    setCreateOpen(true)
  }

  const openEdit = (author: Author) => {
    setFormUsername(author.username)
    setFormDisplayName(author.display_name || "")
    setFormNotes(author.notes || "")
    setEditAuthor(author)
    setCreateOpen(true)
  }

  const handleSubmit = async () => {
    if (!formUsername.trim()) return
    setSubmitting(true)
    try {
      if (editAuthor) {
        await api.updateAuthor(editAuthor.id, {
          username: formUsername.trim(),
          display_name: formDisplayName.trim() || null,
          notes: formNotes.trim() || null,
        })
        toast.success("作者已更新")
      } else {
        await api.createAuthor({
          username: formUsername.trim(),
          display_name: formDisplayName.trim() || undefined,
          notes: formNotes.trim() || undefined,
        })
        toast.success("作者已创建")
      }
      setCreateOpen(false)
      void load()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (author: Author) => {
    if (author.content_count > 0) {
      toast.error("该作者仍有关联内容，无法删除")
      return
    }
    if (!window.confirm(`确定删除作者 @${author.username}？`)) return
    try {
      await api.deleteAuthor(author.id)
      toast.success("作者已删除")
      void load()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {/*
        任务 07 S3：页头走公共 PageHeader（与「我的」同一套 22px/600 + 顶部间距）。
        原手写页头没有说明文字 —— 为不擅自新增文案，这里也未传 description；
        若要补一句说明，加 description 属性即可。
      */}
      <PageHeader
        title="作者"
        className="mb-6"
        actions={
          <Button size="sm" onClick={openCreate}>
            <Plus className="mr-1.5 h-4 w-4" />
            新增作者
          </Button>
        }
      />

      <div className="relative mb-4">
        <Search className="absolute left-3 top-2.5 h-4 w-4 text-text-secondary" />
        <Input
          placeholder="搜索用户名或显示名称…"
          className="pl-10"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {loading && (
        <div className="py-12 text-center text-sm text-text-secondary">加载中…</div>
      )}

      {!loading && authors.length === 0 && (
        <div className="py-12 text-center text-sm text-text-secondary">
          暂无作者
        </div>
      )}

      {!loading && authors.length > 0 && (
        <div className="rounded-lg border border-border">
          {authors.map((author) => (
            <div
              key={author.id}
              className="flex items-center gap-3 border-b border-divider px-4 py-3 last:border-0"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                <User className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">
                    {authorLabel(author.display_name, author.username)}
                  </span>
                  {author.display_name && (
                    <span className="text-xs text-text-secondary">
                      @{author.username}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex gap-3 text-xs text-text-secondary">
                  <span className="tabular-nums">{author.content_count} 帖</span>
                  <span className="tabular-nums">{author.analyzed_count} 已分析</span>
                  {author.notes && <span className="truncate">{author.notes}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-text-secondary hover:text-foreground"
                  onClick={() => openEdit(author)}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-text-secondary hover:text-destructive"
                  onClick={() => handleDelete(author)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-medium">
              {editAuthor ? "编辑作者" : "新增作者"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                X 用户名 <span className="text-danger">*</span>
              </label>
              <Input
                placeholder="例如 wangchangfu88"
                value={formUsername}
                onChange={(e) => setFormUsername(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                显示名称（可选）
              </label>
              <Input
                placeholder="例如 王长富"
                value={formDisplayName}
                onChange={(e) => setFormDisplayName(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                备注（可选）
              </label>
              <Input
                placeholder="可选备注"
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
              />
            </div>
            <Button
              className="w-full"
              disabled={!formUsername.trim() || submitting}
              onClick={() => void handleSubmit()}
            >
              {submitting ? "保存中…" : editAuthor ? "更新作者" : "创建作者"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
