from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from tendermate_shared.observability import metrics


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(metrics, "_meter", provider.get_meter("test"))
    # Instruments are cached per name; drop the ones bound to the global (proxy) meter.
    for cache in (metrics._counters, metrics._histograms, metrics._gauges):
        cache.clear()
    yield reader
    provider.shutdown()
    for cache in (metrics._counters, metrics._histograms, metrics._gauges):
        cache.clear()


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(metrics, "sentry_metrics", mock)
    return mock


def _points(reader: InMemoryMetricReader) -> dict[str, tuple[str, list]]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        m.name: (m.unit, list(m.data.data_points))
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }


def test_count_goes_to_both_sinks(otel: InMemoryMetricReader, sentry: MagicMock) -> None:
    metrics.count("etm.request", 1, attributes={"kind": "search"})
    metrics.count("etm.request", 2, attributes={"kind": "search"})

    sentry.count.assert_called_with("etm.request", 2, unit=None, attributes={"kind": "search"})
    _, (point,) = _points(otel)["etm.request"]
    assert point.value == 3
    assert dict(point.attributes) == {"kind": "search"}


def test_distribution_in_seconds_uses_second_buckets(
    otel: InMemoryMetricReader, sentry: MagicMock
) -> None:
    metrics.distribution("llm.call.duration", 12.0, unit="second", attributes={"agent": "a"})

    sentry.distribution.assert_called_once()
    unit, (point,) = _points(otel)["llm.call.duration"]
    assert unit == "s"  # → llm_call_duration_seconds in Prometheus
    assert tuple(point.explicit_bounds) == metrics.SECONDS_BUCKETS
    assert point.count == 1


def test_gauge_keeps_last_value(otel: InMemoryMetricReader, sentry: MagicMock) -> None:
    metrics.gauge("search_job.in_flight", 3)
    metrics.gauge("search_job.in_flight", 1)

    sentry.gauge.assert_called_with("search_job.in_flight", 1, unit=None, attributes=None)
    _, (point,) = _points(otel)["search_job.in_flight"]
    assert point.value == 1


def test_ratio_unit_maps_to_ucum(otel: InMemoryMetricReader, sentry: MagicMock) -> None:
    metrics.distribution("search_job.completion_ratio", 0.5, unit="ratio")

    unit, _ = _points(otel)["search_job.completion_ratio"]
    assert unit == "1"


def test_without_sentry_only_otel(
    otel: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metrics, "sentry_metrics", None)

    metrics.count("x.y", 2)

    _, (point,) = _points(otel)["x.y"]
    assert point.value == 2
