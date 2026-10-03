/** Reasons `GET /api/v1/auth/login` sends the browser back with (`?login_error=`), since a
 * failed login start must land on this screen rather than a raw JSON error page. */
const messages: Record<string, string> = {
  idp_unreachable:
    'ログイン先(IdP)に接続できませんでした。ローカル環境では、起動ウィンドウで開発用ログインサーバーが起動しているか確認してください。',
  idp_error: 'ログイン先(IdP)から正しい応答がありませんでした。.env の OIDC_ISSUER を確認してください。',
  not_configured: 'ログインが設定されていません。.env の OIDC_ISSUER などを設定してください。',
}

export function loginErrorMessage(search: string): string | null {
  const code = new URLSearchParams(search).get('login_error')
  if (code === null) return null
  return messages[code] ?? 'ログインを開始できませんでした。もう一度お試しください。'
}
