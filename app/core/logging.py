"""Structured logging setup for the backend API and worker process.

Uses structlog on top of the standard library ``logging`` module, writing
human-readable lines to both stdout and a rotating log file
(``settings.log_dir / "backend.log"`` or ``worker.log``), so a failure
seen during manual testing can be traced back later.

Safety rule (see docs/plans/observability-logging.md and
topics/tech-stack.md "never log credentials"): nothing here inspects or
logs request/response bodies. The ``redact_sensitive_fields`` processor
below is defense in depth on top of that -- callers must still never pass
a raw secret value as a structlog field in the first place.
"""

from __future__ import annotations

import logging
import logging.handlers

import structlog

from app.core.config import Settings

# Any structlog event-dict key containing one of these substrings
# (case-insensitive) has its value replaced with "***", at any nesting
# depth. Extend this list if a new credential-bearing field name is
# introduced elsewhere in the codebase.
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "authorization",
    "encryption_key",
    "private_key",
)

# secret_ref is a pointer into LocalEncryptedSecretStore (e.g.
# "local-encrypted://<uuid>"), never the secret value itself -- keep it
# visible, since it is exactly what you need to correlate a log line with
# a specific stored connection while debugging.
_SENSITIVE_KEY_EXCEPTIONS = frozenset({"secret_ref", "secret_refs"})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_KEY_EXCEPTIONS:
        return False
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "***"
                if _is_sensitive_key(str(key)) and not isinstance(item, (dict, list, tuple))
                else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def redact_sensitive_fields(
    logger: object, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: mask any event-dict key (at any nesting depth)
    whose name matches a known sensitive-field marker.

    A sensitive key whose value is itself a dict/list is not masked
    wholesale -- it is recursed into instead, so that non-sensitive sibling
    fields inside it (e.g. "username" next to "password" under a
    "credentials" key) stay visible instead of being hidden along with the
    actually-sensitive field.
    """
    for key in list(event_dict.keys()):
        value = event_dict[key]
        if _is_sensitive_key(key) and not isinstance(value, (dict, list, tuple)):
            event_dict[key] = "***"
        else:
            event_dict[key] = _redact_value(value)
    return event_dict


def configure_logging(settings: Settings, *, log_filename: str = "backend.log") -> None:
    """Configure stdlib logging + structlog to write to stdout and a
    rotating file under ``settings.log_dir``.

    Safe to call more than once (e.g. once at process startup, and again
    in a test with a ``tmp_path``-based ``log_dir``): each call replaces
    the root logger's handlers, so the most recent call wins.
    """
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.log_dir / log_filename

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_sensitive_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Do NOT cache: several call sites hold onto a module-level logger
        # created once at import time, and tests reconfigure logging with
        # a different (tmp_path-based) log_dir. Caching would freeze the
        # first configuration in place for the lifetime of the process.
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=settings.log_rotation_max_bytes,
        backupCount=settings.log_rotation_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    root_logger.handlers = [file_handler, stream_handler]


def configure_named_log_file(settings: Settings, *, logger_name: str, log_filename: str) -> None:
    """Give one named logger its own dedicated rotating file, separate
    from the root logger's file (e.g. frontend-reported errors go to
    logs/frontend.log instead of logs/backend.log). The named logger does
    not propagate to root, so nothing is written twice.

    Call configure_logging() first -- this reuses its formatter and
    assumes the log directory already exists.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / log_filename,
        maxBytes=settings.log_rotation_max_bytes,
        backupCount=settings.log_rotation_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    named_logger = logging.getLogger(logger_name)
    named_logger.setLevel(settings.log_level)
    named_logger.handlers = [file_handler]
    named_logger.propagate = False
