from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from tendermate_shared.observability import rpc

_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(rpc, "_tracer", provider.get_tracer("test"))
    yield exporter
    provider.shutdown()


# ── client ──────────────────────────────────────────────────────────────────────────────────


def test_client_call_carries_its_span_in_traceparent(spans: InMemorySpanExporter) -> None:
    call = rpc.start_client_call("SearchProducts")
    rpc.end_client_call(call)

    (span,) = spans.get_finished_spans()
    assert span.name == "tendermate.agent.v1.SearchService/SearchProducts"
    assert span.kind is SpanKind.CLIENT
    assert (span.attributes or {})["rpc.grpc.status_code"] == Status.OK.value
    assert call.metadata["traceparent"].split("-")[1] == f"{span.context.trace_id:032x}"


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (GRPCError(Status.UNAVAILABLE, "down"), Status.UNAVAILABLE),
        (asyncio.CancelledError(), Status.CANCELLED),
        (GeneratorExit(), Status.CANCELLED),
        (RuntimeError("boom"), Status.UNKNOWN),
    ],
)
def test_client_failure_status(
    spans: InMemorySpanExporter, exc: BaseException, status: Status
) -> None:
    rpc.end_client_call(rpc.start_client_call("SearchProducts"), exc)

    (span,) = spans.get_finished_spans()
    assert (span.attributes or {})["rpc.grpc.status_code"] == status.value
    assert span.status.status_code is StatusCode.ERROR


# ── server ──────────────────────────────────────────────────────────────────────────────────


async def _serve(handler: Any, metadata: dict[str, str] | None = None) -> SimpleNamespace:
    event = SimpleNamespace(
        method_name=f"/{rpc.SEARCH_SERVICE}/SearchProducts",
        method_func=handler,
        metadata=metadata or {},
    )
    await rpc.make_server_telemetry_listener()(event)
    return event


async def test_server_continues_client_trace(spans: InMemorySpanExporter) -> None:
    async def handler(stream: Any) -> None:
        pass

    event = await _serve(handler, {"traceparent": _TRACEPARENT})
    await event.method_func(object())

    (span,) = spans.get_finished_spans()
    assert span.name == f"{rpc.SEARCH_SERVICE}/SearchProducts"
    assert span.kind is SpanKind.SERVER
    assert f"{span.context.trace_id:032x}" == "0af7651916cd43dd8448eb211c80319c"
    assert span.parent is not None and f"{span.parent.span_id:016x}" == "b7ad6b7169203331"
    assert (span.attributes or {})["rpc.grpc.status_code"] == Status.OK.value


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (GRPCError(Status.INVALID_ARGUMENT, "bad"), Status.INVALID_ARGUMENT),
        (asyncio.CancelledError(), Status.CANCELLED),
        (RuntimeError("boom"), Status.UNKNOWN),
    ],
)
async def test_server_failure_status(
    spans: InMemorySpanExporter, exc: BaseException, status: Status
) -> None:
    async def handler(stream: Any) -> None:
        raise exc

    event = await _serve(handler)
    with pytest.raises(type(exc)):
        await event.method_func(object())

    (span,) = spans.get_finished_spans()
    assert (span.attributes or {})["rpc.grpc.status_code"] == status.value
    assert span.status.status_code is StatusCode.ERROR
