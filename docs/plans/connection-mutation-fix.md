# Connection mutation failure fix

## Root cause

Audit writes caused SQLAlchemy mapper resolution to fail because referenced tables (`app_user` and
`market_data_gap`) existed in PostgreSQL but were absent from ORM metadata. The unhandled backend
500 then appeared in the browser as a missing CORS header. Deletion stayed disabled because the
connection never reached the required `disabled` state.

A second runtime check found that `audit_log.ip_address` was mapped as `VARCHAR` while PostgreSQL
stores it as `inet`, causing verification and disable audit inserts to fail even after mapper
resolution was corrected.

## Changes

- Add the missing ORM table definitions and a mapper-configuration regression test.
- Map the audit IP address with PostgreSQL's native `INET` type.
- Roll back connection disable/delete transactions and return sanitized API errors on database
  failures.
- Catch frontend request failures and explain the required disable-then-delete sequence.

## Security

Secrets and raw database exception messages are not returned to the browser.
