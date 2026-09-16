export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/$/,
  '',
)

export const apiErrorMessage = async (response: Response, fallback: string) => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail ? `${fallback}: ${payload.detail}（HTTP ${response.status}）` : fallback
  } catch {
    return `${fallback}（HTTP ${response.status}）`
  }
}
