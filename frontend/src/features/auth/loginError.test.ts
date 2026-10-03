import { expect, it } from 'vitest'
import { loginErrorMessage } from './loginError'

it('explains an unreachable IdP, pointing at the local login server', () => {
  expect(loginErrorMessage('?login_error=idp_unreachable')).toContain('開発用ログインサーバー')
})

it('explains a missing OIDC configuration', () => {
  expect(loginErrorMessage('?login_error=not_configured')).toContain('OIDC_ISSUER')
})

it('falls back to a generic message for an unknown code', () => {
  expect(loginErrorMessage('?login_error=something_new')).toBe(
    'ログインを開始できませんでした。もう一度お試しください。',
  )
})

it('shows nothing without a login error', () => {
  expect(loginErrorMessage('')).toBeNull()
  expect(loginErrorMessage('?other=1')).toBeNull()
})
