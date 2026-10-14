"""Shared Sentry reporting for handled (swallowed) failures.

Only the reusable *reporting* half lives here — ``report_handled_issue`` plus the ``job_id``
context plumbing. The ``init_sentry`` bootstrap stays in each service (it depends on that service's
``Settings`` and web-framework integrations).

``sentry_sdk`` is an OPTIONAL dependency: when it is not installed (e.g. ``tendermate-import-agent``,
which does not ship it) reporting degrades to a plain log line, so this module imports and runs
everywhere. Install the ``observability`` extra to enable Sentry capture.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

try:  # optional — absent in services that don't ship Sentry
    import sentry_sdk
except ImportError:  # pragma: no cover - exercised only where sentry-sdk is not installed
    sentry_sdk = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Carries job_id across async tasks. Set once per job in the service's runner; automatically
# inherited by asyncio.create_task children.
job_id_var: ContextVar[str] = ContextVar("job_id", default="")

_LOG_LEVELS = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO}


def report_handled_issue(
    operation: str,
    message: str,
    *,
    exc: BaseException | None = None,
    level: str = "warning",
    **context: object,
) -> None:
    """Log a handled (swallowed) failure and forward it to Sentry with context.

    These failures are caught locally — a parse error falls back to an empty result, a candidate
    that cannot be fetched or verified is recorded with an error ``reason`` — so they never bubble
    up to the job-level capture. Report them explicitly here so parsing/verification problems stay
    visible as Sentry events (the logging integration only promotes ERROR logs to events) with the
    raw payload / URL attached.

    ``exc`` sends an exception event; otherwise a message event is sent at ``level``. All Sentry
    calls are no-ops when no DSN is configured, and skipped entirely when ``sentry_sdk`` is absent.
    """
    logger.log(_LOG_LEVELS.get(level, logging.WARNING), "%s: %s | %s", operation, message, context)
    if sentry_sdk is None:
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_level(level)
        scope.set_tag("operation", operation)
        if context:
            scope.set_context("details", dict(context))
        if exc is not None:
            scope.set_tag("error_type", type(exc).__name__)
            sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_message(f"{operation}: {message}", level=level)


class JobIdFilter(logging.Filter):
    """Inject the current job_id into every log record.

    Install on the root handler so all loggers pick it up automatically. The format string should
    reference ``%(job_id)s`` to surface it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = job_id_var.get()  # type: ignore[attr-defined]
        return True
