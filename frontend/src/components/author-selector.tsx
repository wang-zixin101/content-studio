import { useCallback, useEffect, useState } from "react"
import { Eraser, Plus, Search, User } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { authorLabel, authorSubLabel } from "@/lib/author-display"
import type { Author } from "@/lib/types"

interface AuthorSelectorProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (author: Author) => void
  excludeIds?: number[]
  /** 是否显示「清除作者」入口（批量改作者场景需要，写作页等场景不需要） */
  allowClear?: boolean
  onClear?: () => void
}

export function AuthorSelector({
  open,
  onOpenChange,
  onSelect,
  excludeIds,
  allowClear = false,
  onClear,
}: AuthorSelectorProps) {
  const [q, setQ] = useState("")
  const [authors, setAuthors] = useState<Author[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [newUsername, setNewUsername] = useState("")
  const [newDisplayName, setNewDisplayName] = useState("")
  const [newNotes, setNewNotes] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.listAuthors({ q })
      const filtered = (data.items || []).filter(
        (a) => !excludeIds?.includes(a.id)
      )
      setAuthors(filtered)
    } catch (err) {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [q, excludeIds])

  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

  const handleCreate = async () => {
    if (!newUsername.trim()) return
    setCreating(true)
    try {
      const author = await api.createAuthor({
        username: newUsername.trim(),
        display_name: newDisplayName.trim() || undefined,
        notes: newNotes.trim() || undefined,
      })
      setCreateOpen(false)
      setNewUsername("")
      setNewDisplayName("")
      setNewNotes("")
      onSelect(author)
      onOpenChange(false)
      void load()
    } catch (err) {
      alert((err as Error).message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-medium">选择作者</DialogTitle>
          </DialogHeader>

          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-text-secondary" />
            <Input
              placeholder="搜索用户名或显示名称…"
              className="pl-9"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>

          <div className="max-h-[320px] overflow-y-auto rounded-lg border border-border">
            {loading && (
              <div className="p-6 text-center text-sm text-text-secondary">
                搜索中…
              </div>
            )}

            {!loading && authors.length === 0 && (
              <div className="p-6 text-center text-sm text-text-secondary">
                未找到作者
              </div>
            )}

            {authors.map((author) => (
              <button
                key={author.id}
                type="button"
                onClick={() => {
                  onSelect(author)
                  onOpenChange(false)
                }}
                className="flex w-full items-center gap-3 border-b border-divider px-3 py-2.5 text-left transition-colors last:border-0 hover:bg-accent"
              >
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                  <User className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">
                    {authorLabel(author.display_name, author.username)}
                  </div>
                  <div className="truncate text-xs text-text-secondary">
                    {authorSubLabel(
                      author.display_name,
                      author.username,
                      author.content_count
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>

          <div className="flex gap-2">
            {allowClear && (
              <Button
                variant="ghost"
                className="flex-1 justify-start text-text-secondary hover:text-destructive"
                onClick={() => {
                  onClear?.()
                  onOpenChange(false)
                }}
              >
                <Eraser className="mr-2 h-4 w-4" />
                清除作者
              </Button>
            )}
            <Button
              variant="ghost"
              className="flex-1 justify-start text-text-secondary hover:text-foreground"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="mr-2 h-4 w-4" />
              新增作者
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-medium">新增作者</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                X 用户名 <span className="text-danger">*</span>
              </label>
              <Input
                placeholder="例如 wangchangfu88"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                显示名称（可选）
              </label>
              <Input
                placeholder="例如 王长富"
                value={newDisplayName}
                onChange={(e) => setNewDisplayName(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                备注（可选）
              </label>
              <Input
                placeholder="可选备注"
                value={newNotes}
                onChange={(e) => setNewNotes(e.target.value)}
              />
            </div>
            <Button
              className="w-full"
              disabled={!newUsername.trim() || creating}
              onClick={() => void handleCreate()}
            >
              {creating ? "创建中…" : "创建作者"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
