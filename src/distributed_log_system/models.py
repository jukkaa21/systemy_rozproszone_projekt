from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ParsedLogRecord:
    ip: str
    remote_log_name: str
    user_id: str
    timestamp_utc: datetime
    method: str
    endpoint: str
    protocol: str
    status: int
    response_bytes: int
    referrer: str
    user_agent: str
    response_time_ms: int


@dataclass
class PartialAggregate:
    total_lines: int = 0
    valid_lines: int = 0
    invalid_lines: int = 0
    total_response_bytes: int = 0
    total_response_time_ms: int = 0
    method_counts: Counter[str] = field(default_factory=Counter)
    status_counts: Counter[int] = field(default_factory=Counter)
    endpoint_counts: Counter[str] = field(default_factory=Counter)
    error_endpoint_counts: Counter[str] = field(default_factory=Counter)
    ip_counts: Counter[str] = field(default_factory=Counter)
    hourly_counts: Counter[str] = field(default_factory=Counter)
    error_5xx_minute_counts: Counter[str] = field(default_factory=Counter)
    status_family_hourly_counts: dict[str, Counter[str]] = field(
        default_factory=lambda: {
            "2xx": Counter(),
            "3xx": Counter(),
            "4xx": Counter(),
            "5xx": Counter(),
        }
    )

    def merge(self, other: "PartialAggregate") -> None:
        self.total_lines += other.total_lines
        self.valid_lines += other.valid_lines
        self.invalid_lines += other.invalid_lines
        self.total_response_bytes += other.total_response_bytes
        self.total_response_time_ms += other.total_response_time_ms
        self.method_counts.update(other.method_counts)
        self.status_counts.update(other.status_counts)
        self.endpoint_counts.update(other.endpoint_counts)
        self.error_endpoint_counts.update(other.error_endpoint_counts)
        self.ip_counts.update(other.ip_counts)
        self.hourly_counts.update(other.hourly_counts)
        self.error_5xx_minute_counts.update(other.error_5xx_minute_counts)
        for family, counts in other.status_family_hourly_counts.items():
            self.status_family_hourly_counts[family].update(counts)

    @property
    def average_response_time_ms(self) -> float:
        if self.valid_lines == 0:
            return 0.0
        return self.total_response_time_ms / self.valid_lines

    @property
    def average_response_bytes(self) -> float:
        if self.valid_lines == 0:
            return 0.0
        return self.total_response_bytes / self.valid_lines
