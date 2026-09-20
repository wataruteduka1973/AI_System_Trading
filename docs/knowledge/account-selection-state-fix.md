# Account selection state fix

## Objective

Prevent an active external account from appearing selectable when its exchange connection is not
currently verified, and ensure unexpected verification failures do not leave a connection in the
`verifying` state.

## Changes

- Include the parent connection status in the workspace-account API response.
- Disable account selection in the UI unless the account is active and the connection is verified.
- Convert unexpected adapter verification failures into a sanitized communication failure state.
- Add regression coverage and run backend/frontend validation.

## Security

The verified-connection requirement remains unchanged. Credential values and exception details are
not returned by the new failure path.
