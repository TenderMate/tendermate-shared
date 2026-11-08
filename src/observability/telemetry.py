"""OpenTelemetry bootstrap shared by the TenderMate services: export to tm-obs + runtime signals.

Each service's ``core/observability.py::init_telemetry`` builds its own pieces (resource, the
Phoenix sink of the agents, SQL/httpx instrumentation of the backend) and calls these helpers for
the common ones:

- :func:`init_obs_metrics_and_logs` — MeterProvider + LoggerProvider exporting to tm-obs
  (OTLP/HTTP, ``<endpoint>/v1/<signal>``), the root log handler, and :func:`init_runtime_metrics`;
- :func:`obs_span_processor` — the tm-obs trace exporter for the service's TracerProvider;
- :func:`init_profiling` — continuous CPU profiles to tm-obs Pyroscope;
- :class:`OpenInferenceOnly` — wraps the Phoenix exporter so it gets only LLM spans.

Everything imports its OpenTelemetry / psutil / pyroscope modules lazily, so a service with
telemetry off pays nothing. Needs the ``observability`` extra.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry.sdk.trace import SpanProcessor

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.metrics import Histogram
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, Span, TracerProvider

logger = logging.getLogger(__name__)

METRIC_EXPORT_INTERVAL_MS = 15_000
# Logs below INFO never leave the container (DEBUG is for the local console / file only).
OBS_LOG_LEVEL = logging.INFO
# How often the event-loop lag probe wakes up; lag = how late the wake-up was.
_LOOP_LAG_INTERVAL_SEC = 1.0
# Process-level runtime metrics only: inside a container the system.* ones describe the host.
RUNTIME_METRICS: dict[str, list[str] | None] = {
    "process.cpu.time": ["user", "system"],
    "process.cpu.utilization": None,
    "process.memory.usage": None,
    "process.memory.virtual": None,
    "process.open_file_descriptor.count": None,
    "process.thread.count": None,
    "process.context_switches": ["involuntary", "voluntary"],
    "cpython.gc.collections": None,
}
# The span attribute every OpenInference (LangChain/LLM) span carries.
_OPENINFERENCE_KIND = "openinference.span.kind"


@dataclass
class Telemetry:
    """The providers ``init_telemetry`` installed; ``None`` for each sink that is off."""

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None
    log_handler: logging.Handler | None = None
    # Undo hooks for the rest (runtime metrics, the loop-lag probe, the profiler), run first.
    closers: list[Callable[[], object]] = field(default_factory=list, compare=False)

    def shutdown(self) -> None:
        """Flush buffered spans, metrics and logs on shutdown (they are only sent on flush)."""
        for close in self.closers:
            close()
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is not None:
                provider.shutdown()


def obs_headers(token: str | None) -> dict[str, str] | None:
    """``Authorization: Bearer <OBS_INGEST_TOKEN>``, the header tm-obs' nginx checks."""
    return {"authorization": f"Bearer {token}"} if token else None


def obs_span_processor(endpoint: str, headers: dict[str, str] | None) -> SpanProcessor:
    """Batch exporter of every span to tm-obs Tempo (*endpoint* is the base URL, no path)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers))


class _SkipLocalOnly(logging.Filter):
    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(self._prefixes)


def init_obs_metrics_and_logs(
    telemetry: Telemetry,
    resource: Resource,
    endpoint: str,
    headers: dict[str, str] | None,
    *,
    local_only_loggers: Iterable[str] = (),
) -> None:
    """Global MeterProvider + LoggerProvider to tm-obs, the root log handler and runtime metrics.

    INFO+ records go to Loki with ``trace_id`` / ``span_id`` and ``job_id`` (structured metadata).
    Never shipped: the OpenTelemetry SDK's own logs (export failures would feed back into the
    exporter) and the loggers in *local_only_loggers*.
    """
    from opentelemetry import metrics
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from tendermate_shared.observability.sentry import JobIdFilter

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers),
                export_interval_millis=METRIC_EXPORT_INTERVAL_MS,
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)  # FastAPI's HTTP metrics and core.metrics use it

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers))
    )
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=OBS_LOG_LEVEL, logger_provider=logger_provider)
    handler.addFilter(JobIdFilter())
    handler.addFilter(_SkipLocalOnly(("opentelemetry", *local_only_loggers)))
    logging.getLogger().addHandler(handler)

    telemetry.meter_provider = meter_provider
    telemetry.logger_provider = logger_provider
    telemetry.log_handler = handler
    init_runtime_metrics(telemetry, meter_provider)


def init_runtime_metrics(telemetry: Telemetry, meter_provider: MeterProvider) -> None:
    """Process CPU / memory / GC (psutil) and, inside a running loop, the event-loop lag."""
    from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

    instrumentor = SystemMetricsInstrumentor(config=RUNTIME_METRICS)
    instrumentor.instrument(meter_provider=meter_provider)
    telemetry.closers.append(instrumentor.uninstrument)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (tests, scripts): nothing to measure
    lag = meter_provider.get_meter(__name__).create_histogram(
        "process.event_loop.lag",
        unit="s",
        explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    )
    task = loop.create_task(_watch_event_loop_lag(lag), name="event-loop-lag")
    telemetry.closers.append(task.cancel)


async def _watch_event_loop_lag(histogram: Histogram) -> None:
    """A blocked loop (sync code on it) wakes this sleep late: the delay is the lag."""
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(_LOOP_LAG_INTERVAL_SEC)
        histogram.record(max(0.0, loop.time() - started - _LOOP_LAG_INTERVAL_SEC))


def init_profiling(
    telemetry: Telemetry,
    *,
    service: str,
    endpoint: str,
    token: str | None,
    version: str,
    environment: str,
) -> None:
    """Continuous CPU profiles to tm-obs Pyroscope (Basic auth ``obs:<OBS_INGEST_TOKEN>``)."""
    import pyroscope

    pyroscope.configure(
        application_name=service,
        server_address=endpoint,
        basic_auth_username="obs",
        basic_auth_password=token or "",
        tags={"service_version": version, "environment": environment},
    )
    telemetry.closers.append(pyroscope.shutdown)


class OpenInferenceOnly(SpanProcessor):
    """Passes only OpenInference spans to *processor*: Phoenix is for LLM traces.

    Dropped spans are infrastructure (HTTP, gRPC, outgoing requests). The LLM spans under them
    keep a whole trace in Phoenix only if their root is an OpenInference span too, so the service
    must start that root (``search_product``, ``parse_document``) as a new trace, linked to its
    caller, rather than as a child of the HTTP / gRPC span.
    """

    def __init__(self, processor: SpanProcessor) -> None:
        self._processor = processor

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self._processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if span.attributes and _OPENINFERENCE_KIND in span.attributes:
            self._processor.on_end(span)

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._processor.force_flush(timeout_millis)
