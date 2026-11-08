"""Custom metrics, sent to Sentry and to tm-obs (OpenTelemetry → Prometheus) at once.

A drop-in for ``sentry_sdk.metrics``: ``from tendermate_shared.observability import metrics`` and
keep calling ``count`` / ``distribution`` / ``gauge``. Each sink is a no-op while it is off (no
Sentry client / no MeterProvider set by the service's ``init_telemetry``); without ``sentry-sdk``
installed only the OpenTelemetry half runs.

In Prometheus, dots become underscores and the unit becomes a suffix: ``etm.request.duration``
(``unit="second"``) → ``etm_request_duration_seconds_{bucket,sum,count}``; counters get ``_total``.
Attributes become labels, so they must have low cardinality — never ids or URLs.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import Counter, Histogram, _Gauge

try:  # optional — the observability extra
    from sentry_sdk import metrics as sentry_metrics
except ImportError:  # pragma: no cover - exercised only where sentry-sdk is not installed
    sentry_metrics = None  # type: ignore[assignment]

# A proxy until init_telemetry() sets the global MeterProvider; instruments created through it
# start recording then.
_meter = otel_metrics.get_meter("tendermate")

# Sentry's unit names → UCUM, which Prometheus turns into the name suffix.
_OTEL_UNITS = {"second": "s", "ratio": "1"}
# OTel's default buckets are for milliseconds; ours are seconds (ETM requests, LLM calls, jobs).
SECONDS_BUCKETS = (
    0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600, 1800, 3600, 7200,
)  # fmt: skip
# Counts (tokens, candidates, attempts, products per job) and ratios.
_VALUE_BUCKETS = (
    0, 0.25, 0.5, 0.75, 1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 50000,
)  # fmt: skip

_counters: dict[str, Counter] = {}
_histograms: dict[str, Histogram] = {}
_gauges: dict[str, _Gauge] = {}


def count(
    name: str, value: float = 1, unit: str | None = None, attributes: dict[str, Any] | None = None
) -> None:
    if sentry_metrics is not None:
        sentry_metrics.count(name, value, unit=unit, attributes=attributes)
    counter = _counters.get(name)
    if counter is None:
        counter = _counters[name] = _meter.create_counter(name, unit=_otel_unit(unit))
    counter.add(value, attributes)


def distribution(
    name: str, value: float, unit: str | None = None, attributes: dict[str, Any] | None = None
) -> None:
    if sentry_metrics is not None:
        sentry_metrics.distribution(name, value, unit=unit, attributes=attributes)
    histogram = _histograms.get(name)
    if histogram is None:
        buckets = SECONDS_BUCKETS if unit == "second" else _VALUE_BUCKETS
        histogram = _histograms[name] = _meter.create_histogram(
            name, unit=_otel_unit(unit), explicit_bucket_boundaries_advisory=buckets
        )
    histogram.record(value, attributes)


def gauge(
    name: str, value: float, unit: str | None = None, attributes: dict[str, Any] | None = None
) -> None:
    if sentry_metrics is not None:
        sentry_metrics.gauge(name, value, unit=unit, attributes=attributes)
    instrument = _gauges.get(name)
    if instrument is None:
        instrument = _gauges[name] = _meter.create_gauge(name, unit=_otel_unit(unit))
    instrument.set(value, attributes)


def _otel_unit(unit: str | None) -> str:
    return _OTEL_UNITS.get(unit, unit) if unit else ""
