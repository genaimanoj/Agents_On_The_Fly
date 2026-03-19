"""OTEL-compliant structured logging, configurable from config.toml.

Log format follows the OpenTelemetry Log Data Model:
  https://opentelemetry.io/docs/specs/otel/logs/data-model/

Fields emitted:
  Timestamp, SeverityText, SeverityNumber, Body, Resource,
  Attributes, TraceId, SpanId, research_id
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Context variables for trace propagation ──────────────────────
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
research_id_var: ContextVar[str] = ContextVar("research_id", default="")

# ── OTEL severity mapping ───────────────────────────────────────
_SEVERITY = {
    "DEBUG": 5,
    "INFO": 9,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
}


class OTELJsonFormatter(logging.Formatter):
    """Formats log records as OTEL-compliant JSON lines."""

    def __init__(self, service_name: str = "flyagent", service_version: str = "0.1.0"):
        super().__init__()
        self.service_name = service_name
        self.service_version = service_version

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "Timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "SeverityText": record.levelname,
            "SeverityNumber": _SEVERITY.get(record.levelname, 0),
            "Body": record.getMessage(),
            "Resource": {
                "service.name": self.service_name,
                "service.version": self.service_version,
            },
            "Attributes": {
                "logger.name": record.name,
                "code.filepath": record.pathname,
                "code.lineno": record.lineno,
                "code.function": record.funcName,
            },
            "TraceId": getattr(record, "trace_id", "") or trace_id_var.get(""),
            "SpanId": getattr(record, "span_id", ""),
            "research_id": getattr(record, "research_id", "") or research_id_var.get(""),
        }
        # Extra attributes from log call
        for key in ("agent_type", "agent_id", "action", "tool", "step"):
            val = getattr(record, key, None)
            if val is not None:
                entry["Attributes"][key] = val
        return json.dumps(entry, ensure_ascii=False)


class OTELTextFormatter(logging.Formatter):
    """Human-readable format with OTEL trace context."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        rid = getattr(record, "research_id", "") or research_id_var.get("")
        tid = getattr(record, "trace_id", "") or trace_id_var.get("")
        prefix = f"{ts} [{record.levelname:>7}]"
        if rid:
            prefix += f" [rid={rid[:8]}]"
        if tid:
            prefix += f" [tid={tid[:8]}]"
        return f"{prefix} {record.name}: {record.getMessage()}"


def setup_logging(
    level: str = "INFO",
    fmt: str = "json",
    log_to_console: bool = True,
    log_to_file: bool = True,
    log_file: str = "./workspace/logs/flyagent.log",
    service_name: str = "flyagent",
) -> None:
    """Configure root logger with OTEL-compliant formatting.

    Called once at startup with values from config.toml [logging].
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    if fmt == "json":
        formatter = OTELJsonFormatter(service_name=service_name)
    else:
        formatter = OTELTextFormatter()

    if log_to_console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    if log_to_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # Quieten noisy libs
    for name in ("httpx", "httpcore", "urllib3", "google", "grpc"):
        logging.getLogger(name).setLevel(logging.WARNING)
