/** "212秒前" / "3分前" / "2時間前"; `suffix` false gives the bare span ("3分"). */
export function formatAge(seconds: number | null, suffix = true): string {
  if (seconds === null) return '記録なし'
  const tail = suffix ? '前' : ''
  if (seconds < 120) return `${seconds}秒${tail}`
  if (seconds < 7200) return `${Math.floor(seconds / 60)}分${tail}`
  return `${Math.floor(seconds / 3600)}時間${tail}`
}
