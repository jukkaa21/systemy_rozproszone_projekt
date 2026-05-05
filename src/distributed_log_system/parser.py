from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import ParsedLogRecord

LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+) (?P<logname>\S+) (?P<userid>\S+) \[(?P<timestamp>[^\]]+)\] '
    r'"(?P<method>[A-Za-z]+) (?P<endpoint>\S+) (?P<protocol>[^"]+)" '
    r"(?P<status>\d{3}) (?P<bytes>\S+) "
    r'"(?P<referrer>[^"]*)" "(?P<user_agent>[^"]*)" (?P<response_time>\d+)$'
)
TIMESTAMP_FORMATS = ("%d/%b/%Y:%H:%M:%S %z", "%d/%b/%Y:%I:%M:%S %z")
SUPPORTED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def parse_log_line(raw_line: str) -> ParsedLogRecord | None:
    line = raw_line.strip()
    if not line:
        return None

    match = LOG_PATTERN.match(line)
    if match is None:
        return None

    groups = match.groupdict()
    timestamp_utc = _parse_timestamp(groups["timestamp"])
    if timestamp_utc is None:
        return None

    method = groups["method"].upper()
    if method not in SUPPORTED_METHODS:
        return None

    endpoint = groups["endpoint"].strip()
    if not endpoint.startswith("/"):
        return None

    try:
        status = int(groups["status"])
        response_time_ms = int(groups["response_time"])
        response_bytes = 0 if groups["bytes"] == "-" else int(groups["bytes"])
    except ValueError:
        return None

    if status < 100 or status > 599 or response_time_ms < 0 or response_bytes < 0:
        return None

    return ParsedLogRecord(
        ip=groups["ip"],
        remote_log_name="" if groups["logname"] == "-" else groups["logname"],
        user_id="" if groups["userid"] == "-" else groups["userid"],
        timestamp_utc=timestamp_utc,
        method=method,
        endpoint=endpoint,
        protocol=groups["protocol"].upper(),
        status=status,
        response_bytes=response_bytes,
        referrer="" if groups["referrer"] == "-" else groups["referrer"],
        user_agent=groups["user_agent"],
        response_time_ms=response_time_ms,
    )


def _parse_timestamp(value: str) -> datetime | None:
    for timestamp_format in TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(value, timestamp_format)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None
