"""Unit tests for the redaction processor in app/core/logging.py.

This is defense in depth, not the primary protection: no code path should
log a request/response body or a raw credential value in the first
place. These tests only confirm that *if* a sensitive-looking key ever
reaches a log call, its value is masked rather than written out.
"""

from app.core.logging import redact_sensitive_fields


def test_redacts_known_sensitive_key() -> None:
    event = {"event": "login_attempt", "api_key": "sk_live_abc123", "user": "alice"}

    result = redact_sensitive_fields(None, "info", event)

    assert result["api_key"] == "***"
    assert result["user"] == "alice"


def test_redacts_case_insensitively_and_by_substring() -> None:
    event = {"event": "x", "Authorization": "Bearer abc", "DEV_OWNER_TOKEN": "xyz"}

    result = redact_sensitive_fields(None, "info", event)

    assert result["Authorization"] == "***"
    assert result["DEV_OWNER_TOKEN"] == "***"


def test_redacts_nested_dict_values() -> None:
    event = {"event": "x", "credentials": {"password": "hunter2", "username": "bob"}}

    result = redact_sensitive_fields(None, "info", event)

    assert result["credentials"] == {"password": "***", "username": "bob"}


def test_does_not_redact_secret_ref_since_it_is_not_the_secret_itself() -> None:
    # secret_ref points at LocalEncryptedSecretStore (e.g. "local-encrypted://<uuid>");
    # it is not the secret value, and stays visible so logs remain useful for
    # correlating a request with a specific stored connection.
    event = {"event": "x", "secret_ref": "local-encrypted://0123456789abcdef", "token": "abc"}

    result = redact_sensitive_fields(None, "info", event)

    assert result["secret_ref"] == "local-encrypted://0123456789abcdef"
    assert result["token"] == "***"


def test_redacts_sensitive_values_inside_lists() -> None:
    event = {"event": "x", "connections": [{"secret_ref": "irrelevant", "token": "abc"}]}

    result = redact_sensitive_fields(None, "info", event)

    assert result["connections"] == [{"secret_ref": "irrelevant", "token": "***"}]


def test_leaves_non_sensitive_fields_untouched() -> None:
    event = {"event": "request_completed", "method": "GET", "path": "/health", "status_code": 200}

    result = redact_sensitive_fields(None, "info", dict(event))

    assert result == event
