from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tendermate_shared.observability.telemetry import (
    OpenInferenceOnly,
    Telemetry,
    init_obs_metrics_and_logs,
    init_profiling,
    obs_headers,
)

OBS = "http://run-tm-obs"


@pytest.fixture
def otel_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Mock exporters, global setters and the runtime instrumentor (globals are set once)."""
    mocks = {n: MagicMock() for n in ("metric_exporter", "log_exporter", "set_meter",
                                       "set_logger", "runtime")}  # fmt: skip
    prefix = "opentelemetry.exporter.otlp.proto.http"
    monkeypatch.setattr(f"{prefix}.metric_exporter.OTLPMetricExporter", mocks["metric_exporter"])
    monkeypatch.setattr(f"{prefix}._log_exporter.OTLPLogExporter", mocks["log_exporter"])
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", mocks["set_meter"])
    monkeypatch.setattr("opentelemetry._logs.set_logger_provider", mocks["set_logger"])
    monkeypatch.setattr(
        "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader",
        lambda *a, **k: MagicMock(),
    )
    monkeypatch.setattr(
        "opentelemetry.instrumentation.system_metrics.SystemMetricsInstrumentor",
        lambda config: mocks["runtime"],
    )
    return mocks


@pytest.fixture
def telemetry() -> Iterator[Telemetry]:
    t = Telemetry()
    yield t
    t.shutdown()  # stops the batch-export threads and detaches the root log handler


def _record(name: str) -> logging.LogRecord:
    return logging.LogRecord(name, logging.WARNING, __file__, 1, "msg", None, None)


def test_obs_headers() -> None:
    assert obs_headers("t") == {"authorization": "Bearer t"}
    assert obs_headers(None) is None
    assert obs_headers("") is None


def test_metrics_and_logs_go_to_obs(otel_mocks: dict[str, MagicMock], telemetry: Telemetry) -> None:
    headers = obs_headers("t")
    init_obs_metrics_and_logs(telemetry, Resource.create({}), OBS, headers)

    otel_mocks["metric_exporter"].assert_called_once_with(
        endpoint=f"{OBS}/v1/metrics", headers=headers
    )
    otel_mocks["log_exporter"].assert_called_once_with(endpoint=f"{OBS}/v1/logs", headers=headers)
    otel_mocks["set_meter"].assert_called_once_with(telemetry.meter_provider)
    otel_mocks["set_logger"].assert_called_once_with(telemetry.logger_provider)
    assert telemetry.log_handler in logging.getLogger().handlers
    assert telemetry.log_handler is not None and telemetry.log_handler.level == logging.INFO


def test_log_handler_filters(otel_mocks: dict[str, MagicMock], telemetry: Telemetry) -> None:
    init_obs_metrics_and_logs(
        telemetry, Resource.create({}), OBS, None, local_only_loggers=("sqlalchemy.engine",)
    )
    handler = telemetry.log_handler
    assert handler is not None

    assert not handler.filter(_record("opentelemetry.sdk._shared_internal"))
    assert not handler.filter(_record("sqlalchemy.engine.Engine"))
    app = _record("jobs.runner")
    assert handler.filter(app)
    assert hasattr(app, "job_id")  # JobIdFilter → Loki structured metadata


def test_runtime_metrics_registered_and_undone(
    otel_mocks: dict[str, MagicMock], telemetry: Telemetry
) -> None:
    init_obs_metrics_and_logs(telemetry, Resource.create({}), OBS, None)

    otel_mocks["runtime"].instrument.assert_called_once_with(
        meter_provider=telemetry.meter_provider
    )
    telemetry.shutdown()
    otel_mocks["runtime"].uninstrument.assert_called_once()


async def test_event_loop_lag_probe_inside_a_loop(
    otel_mocks: dict[str, MagicMock], telemetry: Telemetry
) -> None:
    init_obs_metrics_and_logs(telemetry, Resource.create({}), OBS, None)

    (probe,) = [t for t in asyncio.all_tasks() if t.get_name() == "event-loop-lag"]
    telemetry.shutdown()
    await asyncio.sleep(0)
    assert probe.cancelled()


def test_profiling(monkeypatch: pytest.MonkeyPatch, telemetry: Telemetry) -> None:
    pyroscope = MagicMock()
    monkeypatch.setitem(sys.modules, "pyroscope", pyroscope)

    init_profiling(
        telemetry, service="svc", endpoint=OBS, token="t", version="1.0", environment="prod"
    )

    kwargs = pyroscope.configure.call_args.kwargs
    assert kwargs["application_name"] == "svc"
    assert kwargs["server_address"] == OBS
    assert (kwargs["basic_auth_username"], kwargs["basic_auth_password"]) == ("obs", "t")
    telemetry.shutdown()
    pyroscope.shutdown.assert_called_once()


def test_shutdown_flushes_and_detaches_handler() -> None:
    handler = logging.NullHandler()
    logging.getLogger().addHandler(handler)
    providers: list[Any] = [MagicMock(), MagicMock(), MagicMock()]
    closer = MagicMock()
    telemetry = Telemetry(*providers, log_handler=handler, closers=[closer])

    telemetry.shutdown()

    closer.assert_called_once()
    for p in providers:
        p.shutdown.assert_called_once()
    assert handler not in logging.getLogger().handlers


def test_empty_telemetry_compares_equal_and_shuts_down() -> None:
    assert Telemetry() == Telemetry(closers=[lambda: None])  # closers are not compared
    Telemetry().shutdown()


def test_phoenix_gets_only_openinference_spans() -> None:
    phoenix, tempo = InMemorySpanExporter(), InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(OpenInferenceOnly(SimpleSpanProcessor(phoenix)))
    provider.add_span_processor(SimpleSpanProcessor(tempo))
    tracer = provider.get_tracer("test")

    with (
        tracer.start_as_current_span("SearchService/SearchProducts"),
        tracer.start_as_current_span(
            "search_product", attributes={"openinference.span.kind": "CHAIN"}
        ),
        tracer.start_as_current_span("etm.request"),
    ):
        pass

    assert [s.name for s in phoenix.get_finished_spans()] == ["search_product"]
    assert len(tempo.get_finished_spans()) == 3
    provider.shutdown()
