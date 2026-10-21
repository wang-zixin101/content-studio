/** 作者的显示文本。这里是唯一真实来源，组件与测试都必须复用。 */

/**
 * 主显示名：有 display_name 就用它，否则用 @username。
 */
export function authorLabel(displayName: string | null, username: string): string {
  return displayName || `@${username}`
}

/**
 * 副显示行：有 display_name 时补 @username，再拼帖数。
 */
export function authorSubLabel(
  displayName: string | null,
  username: string,
  contentCount: number
): string {
  const handle = displayName ? `@${username}` : ""
  return `${handle} ${contentCount} 帖`.trim()
}
