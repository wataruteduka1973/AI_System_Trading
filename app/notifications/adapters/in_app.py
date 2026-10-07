class InAppNotificationAdapter:
    """The `in_app` channel needs no delivery: the `notification` row itself is what the
    application shows (and what a person acknowledges). `send` has nothing to do, so a
    notification on this channel is `sent` as soon as it is recorded."""

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        return None
