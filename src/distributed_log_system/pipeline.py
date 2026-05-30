from __future__ import annotations

import glob
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Iterable, Sequence

import ray

from .models import PartialAggregate
from .parser import parse_log_line
from .reporting import export_results


def _ensure_ray_initialized(num_cpus: int) -> bool:
    if ray.is_initialized():
        return False

    ray.init(
        num_cpus=max(1, num_cpus),
        include_dashboard=False,
        ignore_reinit_error=True,
        logging_level=logging.ERROR,
        log_to_driver=False,
    )
    return True


def resolve_input_files(input_paths: Sequence[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()

    for raw_path in input_paths:
        path = Path(raw_path)
        matched: list[Path]

        if path.exists():
            if path.is_dir():
                matched = sorted(file_path for file_path in path.rglob("*") if file_path.is_file())
            else:
                matched = [path]
        else:
            matched = sorted(Path(candidate) for candidate in glob.glob(raw_path))

        for file_path in matched:
            if file_path not in seen:
                resolved.append(file_path)
                seen.add(file_path)

    return resolved


def iter_log_chunks(files: Sequence[Path], chunk_size: int) -> Iterable[list[str]]:
    chunk: list[str] = []

    for file_path in files:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                chunk.append(line)
                if len(chunk) >= chunk_size:
                    yield chunk
                    chunk = []

    if chunk:
        yield chunk


def process_chunk(lines: list[str]) -> PartialAggregate:
    aggregate = PartialAggregate(total_lines=len(lines))

    for line in lines:
        record = parse_log_line(line)
        if record is None:
            aggregate.invalid_lines += 1
            continue

        aggregate.valid_lines += 1
        aggregate.total_response_bytes += record.response_bytes
        aggregate.total_response_time_ms += record.response_time_ms
        aggregate.method_counts[record.method] += 1
        aggregate.status_counts[record.status] += 1
        aggregate.endpoint_counts[record.endpoint] += 1
        aggregate.ip_counts[record.ip] += 1
        if record.status >= 400:
            aggregate.error_endpoint_counts[record.endpoint] += 1

        time_bucket = record.timestamp_utc.replace(second=0, microsecond=0).isoformat()
        aggregate.hourly_counts[time_bucket] += 1

        family = f"{record.status // 100}xx"
        if family in aggregate.status_family_hourly_counts:
            aggregate.status_family_hourly_counts[family][time_bucket] += 1

        if 500 <= record.status <= 599:
            minute_bucket = record.timestamp_utc.replace(second=0, microsecond=0).isoformat()
            aggregate.error_5xx_minute_counts[minute_bucket] += 1

    return aggregate


def execute_pipeline(
    input_paths: Sequence[str],
    output_dir: str,
    workers: int,
    chunk_size: int,
    top_n: int,
    anomaly_window: int,
    anomaly_sigma: float,
    benchmark_workers: Sequence[int] | None = None,
    export_dashboard: bool = True,
) -> dict:
    input_files = resolve_input_files(input_paths)
    if not input_files:
        raise FileNotFoundError("Nie znaleziono plikow logow dla podanych sciezek.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    worker_candidates = [workers, *(benchmark_workers or [])]
    max_ray_cpus = max(max(1, int(count)) for count in worker_candidates)
    started_ray = _ensure_ray_initialized(num_cpus=max_ray_cpus)

    try:
        aggregate, duration_seconds = run_map_reduce(input_files, workers=workers, chunk_size=chunk_size)

        summary = build_summary(
            aggregate=aggregate,
            input_files=input_files,
            workers=workers,
            chunk_size=chunk_size,
            duration_seconds=duration_seconds,
            top_n=top_n,
            anomaly_window=anomaly_window,
            anomaly_sigma=anomaly_sigma,
        )

        benchmark = []
        if benchmark_workers:
            benchmark = run_benchmark(input_files, chunk_size=chunk_size, worker_counts=benchmark_workers)

        export_results(
            output_dir=output_path,
            summary=summary,
            benchmark=benchmark,
            export_dashboard=export_dashboard,
        )

        summary["benchmark"] = benchmark
        return summary
    finally:
        if started_ray:
            ray.shutdown()


def run_map_reduce(files: Sequence[Path], workers: int, chunk_size: int) -> tuple[PartialAggregate, float]:
    max_workers = max(1, int(workers))
    aggregate = PartialAggregate()
    start = perf_counter()
    started_ray = _ensure_ray_initialized(num_cpus=max_workers)
    process_chunk_remote = ray.remote(process_chunk).options(num_cpus=1)
    pending = []

    try:
        for chunk in iter_log_chunks(files, chunk_size=chunk_size):
            pending.append(process_chunk_remote.remote(chunk))
            if len(pending) >= max_workers:
                ready, pending = ray.wait(pending, num_returns=1)
                for object_ref in ready:
                    aggregate.merge(ray.get(object_ref))

        while pending:
            ready, pending = ray.wait(pending, num_returns=1)
            for object_ref in ready:
                aggregate.merge(ray.get(object_ref))

        return aggregate, perf_counter() - start
    finally:
        if started_ray:
            ray.shutdown()


def build_summary(
    aggregate: PartialAggregate,
    input_files: Sequence[Path],
    workers: int,
    chunk_size: int,
    duration_seconds: float,
    top_n: int,
    anomaly_window: int,
    anomaly_sigma: float,
) -> dict:
    anomaly_detection = detect_error_anomalies(
        error_minute_counts=aggregate.error_5xx_minute_counts,
        all_minute_counts=aggregate.hourly_counts,
        window_size=anomaly_window,
        sigma=anomaly_sigma,
    )
    anomalies = anomaly_detection["anomalies"]

    top_ips = _top_items(aggregate.ip_counts, top_n)
    top_endpoints = _top_items(aggregate.endpoint_counts, top_n)
    top_error_endpoints = _top_items(aggregate.error_endpoint_counts, top_n)
    status_codes = _top_items(aggregate.status_counts, top_n=1000)
    methods = _top_items(aggregate.method_counts, top_n=1000)
    time_buckets = _build_trend_buckets(aggregate.hourly_counts)
    hourly_trends = [
        {"hour": time_bucket, "requests": aggregate.hourly_counts.get(time_bucket, 0)}
        for time_bucket in time_buckets
    ]
    status_family_trends = {
        family: [
            {"hour": time_bucket, "requests": counts.get(time_bucket, 0)}
            for time_bucket in time_buckets
        ]
        for family, counts in aggregate.status_family_hourly_counts.items()
    }

    return {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "input_files": [str(path) for path in input_files],
        "config": {
            "workers": workers,
            "chunk_size": chunk_size,
            "top_n": top_n,
            "anomaly_window": anomaly_window,
            "anomaly_sigma": anomaly_sigma,
        },
        "metrics": {
            "duration_seconds": round(duration_seconds, 4),
            "processed_lines": aggregate.total_lines,
            "valid_lines": aggregate.valid_lines,
            "invalid_lines": aggregate.invalid_lines,
            "valid_ratio": round(
                aggregate.valid_lines / aggregate.total_lines, 4
            )
            if aggregate.total_lines
            else 0.0,
            "average_response_time_ms": round(aggregate.average_response_time_ms, 2),
            "average_response_bytes": round(aggregate.average_response_bytes, 2),
        },
        "aggregations": {
            "top_ips": top_ips,
            "top_endpoints": top_endpoints,
            "top_error_endpoints": top_error_endpoints,
            "status_codes": status_codes,
            "methods": methods,
            "hourly_trends": hourly_trends,
            "status_family_trends": status_family_trends,
        },
        "anomalies": anomalies,
        "anomaly_detection": anomaly_detection,
    }


def detect_error_anomalies(
    error_minute_counts: dict[str, int],
    all_minute_counts: dict[str, int],
    window_size: int,
    sigma: float,
) -> dict:
    timestamps = _build_continuous_minute_buckets(all_minute_counts)
    values = [error_minute_counts.get(timestamp, 0) for timestamp in timestamps]
    normalized_window = _normalize_savgol_window(
        series_length=len(values),
        requested_window_size=window_size,
    )
    polyorder = min(2, max(1, normalized_window - 2)) if normalized_window else 0

    anomalies: list[dict] = []
    anomaly_indexes: set[int] = set()
    min_error_count = 3

    if normalized_window:
        baseline = [
            max(0.0, value)
            for value in _savitzky_golay_smooth(
                values=values,
                window_length=normalized_window,
                polyorder=polyorder,
            )
        ]
    else:
        baseline = [float(value) for value in values]

    residuals = [value - baseline_value for value, baseline_value in zip(values, baseline)]
    residual_scale = max(_robust_stddev(residuals), 1.0)
    thresholds = [
        max(baseline_value + sigma * residual_scale, float(min_error_count))
        for baseline_value in baseline
    ]

    for index, (timestamp, current, baseline_value, residual, threshold) in enumerate(
        zip(timestamps, values, baseline, residuals, thresholds)
    ):
        if current >= min_error_count and current >= threshold and residual > 0:
            score = residual / residual_scale if residual_scale else residual
            anomaly_indexes.add(index)
            anomalies.append(
                {
                    "timestamp": timestamp,
                    "error_count": current,
                    "savgol_baseline": round(baseline_value, 2),
                    "residual": round(residual, 2),
                    "residual_score": round(score, 2),
                    "threshold": round(threshold, 2),
                }
            )

    return {
        "method": "Savitzky-Golay",
        "metric": "5xx errors per minute",
        "window_length": normalized_window,
        "polyorder": polyorder,
        "sigma": sigma,
        "residual_scale": round(residual_scale, 4),
        "min_error_count": min_error_count,
        "anomalies": anomalies,
        "series": _sample_anomaly_series(
            timestamps=timestamps,
            values=values,
            baseline=baseline,
            thresholds=thresholds,
            anomaly_indexes=anomaly_indexes,
        ),
    }


def run_benchmark(files: Sequence[Path], chunk_size: int, worker_counts: Sequence[int]) -> list[dict]:
    results: list[dict] = []
    unique_counts = []
    seen = set()

    for count in worker_counts:
        normalized = max(1, int(count))
        if normalized not in seen:
            unique_counts.append(normalized)
            seen.add(normalized)

    baseline_duration = None
    for worker_count in unique_counts:
        aggregate, duration_seconds = run_map_reduce(
            files=files,
            workers=worker_count,
            chunk_size=chunk_size,
        )
        if baseline_duration is None:
            baseline_duration = duration_seconds

        speedup = baseline_duration / duration_seconds if duration_seconds else 1.0
        results.append(
            {
                "workers": worker_count,
                "duration_seconds": round(duration_seconds, 4),
                "processed_lines": aggregate.total_lines,
                "speedup_vs_first_run": round(speedup, 4),
            }
        )

    return results


def _build_trend_buckets(counter: dict[str, int]) -> list[str]:
    ordered_buckets = sorted(counter)
    if len(ordered_buckets) <= 1:
        return ordered_buckets

    buckets = set(ordered_buckets)
    previous = datetime.fromisoformat(ordered_buckets[0])

    for bucket in ordered_buckets[1:]:
        current = datetime.fromisoformat(bucket)
        first_missing = previous + timedelta(minutes=1)

        if first_missing < current:
            last_missing = current - timedelta(minutes=1)
            buckets.add(first_missing.isoformat())
            buckets.add(last_missing.isoformat())

        previous = current

    return sorted(buckets, key=datetime.fromisoformat)


def _build_continuous_minute_buckets(counter: dict[str, int]) -> list[str]:
    ordered_buckets = sorted(counter, key=datetime.fromisoformat)
    if not ordered_buckets:
        return []

    current = datetime.fromisoformat(ordered_buckets[0])
    end = datetime.fromisoformat(ordered_buckets[-1])
    buckets = []

    while current <= end:
        buckets.append(current.isoformat())
        current += timedelta(minutes=1)

    return buckets


def _normalize_savgol_window(series_length: int, requested_window_size: int) -> int:
    if series_length < 3:
        return 0

    window_length = max(5, int(requested_window_size))
    if window_length % 2 == 0:
        window_length += 1

    if window_length > series_length:
        window_length = series_length if series_length % 2 else series_length - 1

    return window_length if window_length >= 3 else 0


def _savitzky_golay_smooth(values: Sequence[int], window_length: int, polyorder: int) -> list[float]:
    coefficients = _savgol_coefficients(window_length=window_length, polyorder=polyorder)
    half_window = window_length // 2
    smoothed = []

    for index in range(len(values)):
        value = 0.0
        for offset, coefficient in enumerate(coefficients):
            source_index = index + offset - half_window
            value += coefficient * _reflected_value(values, source_index)
        smoothed.append(value)

    return smoothed


def _savgol_coefficients(window_length: int, polyorder: int) -> list[float]:
    half_window = window_length // 2
    positions = list(range(-half_window, half_window + 1))
    matrix_size = polyorder + 1
    normal_matrix = [
        [
            sum(float(position) ** (row + column) for position in positions)
            for column in range(matrix_size)
        ]
        for row in range(matrix_size)
    ]
    target = [1.0] + [0.0] * polyorder
    weights = _solve_linear_system(normal_matrix, target)

    return [
        sum((float(position) ** power) * weights[power] for power in range(matrix_size))
        for position in positions
    ]


def _solve_linear_system(matrix: list[list[float]], target: list[float]) -> list[float]:
    size = len(target)
    augmented = [row[:] + [target[index]] for index, row in enumerate(matrix)]

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot_row][column]) < 1e-12:
            raise ValueError("Nie mozna wyliczyc wspolczynnikow filtra Savitzky-Golay.")

        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        pivot = augmented[column][column]
        augmented[column] = [value / pivot for value in augmented[column]]

        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * augmented[column][position]
                for position, value in enumerate(augmented[row])
            ]

    return [row[-1] for row in augmented]


def _reflected_value(values: Sequence[int], index: int) -> int:
    if len(values) == 1:
        return values[0]

    last_index = len(values) - 1
    while index < 0 or index > last_index:
        if index < 0:
            index = -index
        elif index > last_index:
            index = 2 * last_index - index

    return values[index]


def _robust_stddev(values: Sequence[float]) -> float:
    if not values:
        return 0.0

    median = _median(values)
    median_absolute_deviation = _median([abs(value - median) for value in values])
    robust_scale = 1.4826 * median_absolute_deviation
    if robust_scale > 0:
        return robust_scale

    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) / 2


def _sample_anomaly_series(
    timestamps: Sequence[str],
    values: Sequence[int],
    baseline: Sequence[float],
    thresholds: Sequence[float],
    anomaly_indexes: set[int],
    max_points: int = 1200,
) -> list[dict]:
    if len(timestamps) <= max_points:
        selected_indexes = set(range(len(timestamps)))
    else:
        step = math.ceil(len(timestamps) / max_points)
        selected_indexes = set(range(0, len(timestamps), step))
        for index in anomaly_indexes:
            selected_indexes.update(
                nearby
                for nearby in range(index - 2, index + 3)
                if 0 <= nearby < len(timestamps)
            )

    return [
        {
            "timestamp": timestamps[index],
            "error_count": values[index],
            "savgol_baseline": round(baseline[index], 2),
            "threshold": round(thresholds[index], 2),
            "is_anomaly": index in anomaly_indexes,
        }
        for index in sorted(selected_indexes)
    ]


def _top_items(counter: dict, top_n: int) -> list[dict]:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{"key": str(key), "count": count} for key, count in items[:top_n]]
