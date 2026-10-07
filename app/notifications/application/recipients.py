"""Who is told about an event: the workspace's Owners and Operators -- the roles that can act
on a halt or a stopped bot (an Operator can start or stop a bot, an Owner also releases an
emergency stop). Viewers are not notified, and neither are disabled accounts.

Each person gets an `in_app` notification (read in the application, no setup needed) and,
where the email channel is configured, an `email` one. `recipient_ref` is the user id for
`in_app` and the address for `email`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import OutboxEvent, SystemEvent
from app.models.workspace import AppUser, UserMembership

NOTIFIED_ROLES = ("owner", "operator")


def workspace_member_recipients(
    db: Session, _outbox_event: OutboxEvent, system_event: SystemEvent
) -> list[tuple[str, str]]:
    users = db.execute(
        select(AppUser.id, AppUser.email)
        .join(UserMembership, UserMembership.user_id == AppUser.id)
        .where(
            UserMembership.workspace_id == system_event.workspace_id,
            UserMembership.role.in_(NOTIFIED_ROLES),
            AppUser.status == "active",
        )
        .order_by(AppUser.email)
    ).all()
    recipients: list[tuple[str, str]] = []
    for user_id, email in users:
        recipients.append(("in_app", str(user_id)))
        recipients.append(("email", email))
    return recipients
