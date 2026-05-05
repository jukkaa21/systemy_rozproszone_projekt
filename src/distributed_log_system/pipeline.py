from __future__ import annotations

import csv
import glob
import math
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable, Sequence

from .models import PartialAggregate
from .parser import parse_log_line
from .reporting import export_results


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

        hour_bucket = record.timestamp_utc.replace(minute=0, second=0, microsecond=0).isoformat()
        aggregate.hourly_counts[hour_bucket] += 1

        family = f"{record.status // 100}xx"
        if family in aggregate.status_family_hourly_counts:
            aggregate.status_family_hourly_counts[family][hour_bucket] += 1

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


def run_map_reduce(files: Sequence[Path], workers: int, chunk_size: int) -> tuple[PartialAggregate, float]:
    max_workers = max(1, workers)
    aggregate = PartialAggregate()
    start = perf_counter()
    pending = set()

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for chunk in iter_log_chunks(files, chunk_size=chunk_size):
            pending.add(executor.submit(process_chunk, chunk))
            if len(pending) >= max_workers * 3:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    aggregate.merge(future.result())

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                aggregate.merge(future.result())

    return aggregate, perf_counter() - start


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
    anomalies = detect_error_anomalies(
        minute_counts=aggregate.error_5xx_minute_counts,
        window_size=anomaly_window,
        sigma=anomaly_sigma,
    )

    top_ips = _top_items(aggregate.ip_counts, top_n)
    top_endpoints = _top_items(aggregate.endpoint_counts, top_n)
    top_error_endpoints = _top_items(aggregate.error_endpoint_counts, top_n)
    status_codes = _top_items(aggregate.status_counts, top_n=1000)
    methods = _top_items(aggregate.method_counts, top_n=1000)
    hourly_trends = [
        {"hour": hour, "requests": count}
        for hour, count in sorted(aggregate.hourly_counts.items())
    ]
    status_family_trends = {
        family: [
            {"hour": hour, "requests": count}
            for hour, count in sorted(counts.items())
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
    }


def detect_error_anomalies(minute_counts: dict[str, int], window_size: int, sigma: float) -> list[dict]:
    ordered = sorted(minute_counts.items())
    if len(ordered) <= window_size:
        return []

    anomalies: list[dict] = []
    counts = [count for _, count in ordered]
    timestamps = [timestamp for timestamp, _ in ordered]
    min_threshold = 3

    for index in range(window_size, len(ordered)):
        history = counts[index - window_size : index]
        mean = sum(history) / len(history)
        variance = sum((value - mean) ** 2 for value in history) / len(history)
        stddev = math.sqrt(variance)
        current = counts[index]
        threshold = max(mean + sigma * stddev, min_threshold)

        if current > threshold and current > mean:
            anomalies.append(
                {
                    "timestamp": timestamps[index],
                    "error_count": current,
                    "baseline_mean": round(mean, 2),
                    "baseline_stddev": round(stddev, 2),
                    "threshold": round(threshold, 2),
                }
            )

    return anomalies


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


def _top_items(counter: dict, top_n: int) -> list[dict]:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [{"key": str(key), "count": count} for key, count in items[:top_n]]
