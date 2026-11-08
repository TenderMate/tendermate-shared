"""OpenTelemetry for grpclib, both ends of the backend → searching-agent ``SearchService``.

grpclib has no OpenTelemetry instrumentation, so this is a minimal one. Both halves live here so
span names, metric names and attributes always match:

- client (backend): :func:`start_client_call` opens a CLIENT span — *not* made current, a span
  held open across an async generator's yields would leak into the consumer — and returns the
  W3C ``traceparent`` metadata to send; :func:`end_client_call` closes it and records
  ``rpc.client.duration``;
- server (agent): :func:`make_server_telemetry_listener` wraps each call in a SERVER span that
  continues the client's trace and records ``rpc.server.duration``.

Metric attributes: ``rpc.system``, ``rpc.service``, ``rpc.method``, ``rpc.grpc.status_code``.
Needs ``opentelemetry-api`` only; without a provider set everything is a no-op.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from grpclib.const import Status
from grpclib.exceptions import GRPCError
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import Span, SpanKind, StatusCode
from tendermate_shared.observability.metrics import SECONDS_BUCKETS

# gRPC metadata key carrying the backend ↔ agent shared secret (agent: AGENT_GRPC_KEY,
# backend: SEARCHING_AGENT_KEY).
AUTH_METADATA_KEY = "x-internal-key"
SEARCH_SERVICE = "tendermate.agent.v1.SearchService"

_tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
_client_duration = _meter.create_histogram(
    "rpc.client.duration", unit="s", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS
)
_server_duration = _meter.create_histogram(
    "rpc.server.duration", unit="s", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS
)


def _attributes(service: str, method: str) -> dict[str, str]:
    return {"rpc.system": "grpc", "rpc.service": service, "rpc.method": method}


def _status_of(exc: BaseException | None) -> Status:
    if exc is None:
        return Status.OK
    if isinstance(exc, GRPCError):
        return exc.status
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return Status.CANCELLED
    return Status.UNKNOWN


@dataclass
class ClientCall:
    span: Span
    service: str
    method: str
    started: float
    metadata: dict[str, str]


def start_client_call(method: str, *, service: str = SEARCH_SERVICE) -> ClientCall:
    """Start a CLIENT span; send ``call.metadata`` with the request (merged with auth)."""
    span = _tracer.start_span(
        f"{service}/{method}", kind=SpanKind.CLIENT, attributes=_attributes(service, method)
    )
    carrier: dict[str, str] = {}
    propagate.inject(carrier, context=trace.set_span_in_context(span))
    return ClientCall(span, service, method, time.monotonic(), carrier)


def end_client_call(call: ClientCall, exc: BaseException | None = None) -> None:
    """End the span with the call's gRPC status (from *exc*) and record its duration."""
    code = _status_of(exc)
    call.span.set_attribute("rpc.grpc.status_code", code.value)
    if code is not Status.OK:
        if exc is not None and not isinstance(exc, GeneratorExit):
            call.span.record_exception(exc)
        call.span.set_status(StatusCode.ERROR, code.name)
    call.span.end()
    _client_duration.record(
        time.monotonic() - call.started,
        {**_attributes(call.service, call.method), "rpc.grpc.status_code": code.value},
    )


def make_server_telemetry_listener() -> Callable[[Any], Awaitable[None]]:
    """grpclib ``RecvRequest`` hook: a SERVER span + ``rpc.server.duration`` around each call.

    Register it with ``grpclib.events.listen(server, RecvRequest, ...)`` *after* the auth hook,
    so rejected calls are neither traced nor measured.
    """

    async def _wrap(event: Any) -> None:
        handler = event.method_func
        service, _, method = event.method_name.strip("/").rpartition("/")
        attributes = _attributes(service, method)
        parent = propagate.extract(event.metadata)

        async def traced(stream: Any) -> None:
            started = time.monotonic()
            exc: BaseException | None = None
            with _tracer.start_as_current_span(
                f"{service}/{method}", context=parent, kind=SpanKind.SERVER, attributes=attributes
            ) as span:
                try:
                    await handler(stream)
                except BaseException as error:
                    exc = error
                    raise
                finally:
                    code = _status_of(exc)
                    span.set_attribute("rpc.grpc.status_code", code.value)
                    if code is not Status.OK:
                        span.set_status(StatusCode.ERROR, code.name)
                    _server_duration.record(
                        time.monotonic() - started,
                        {**attributes, "rpc.grpc.status_code": code.value},
                    )

        event.method_func = traced

    return _wrap
