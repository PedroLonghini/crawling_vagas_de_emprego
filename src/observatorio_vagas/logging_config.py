"""Logging estruturado com mascaramento básico de dados sensíveis."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|password|secret|token)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _redact_text(value: str) -> str:
    """Mascara padrões comuns de credenciais presentes em texto."""

    redacted = value
    redacted = _SECRET_PATTERNS[0].sub(r"\1 [REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[1].sub(r"\1=[REDACTED]", redacted)
    return redacted


def _sanitize(value: Any, key: str | None = None) -> Any:
    """Remove segredos de estruturas usadas como contexto do log."""

    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return "[REDACTED]"

    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    """Formata logs como uma linha JSON para facilitar busca e alertas."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }

        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = _sanitize(context)

        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", stream: Any = None) -> None:
    """Configura o logger raiz de forma idempotente."""

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger nomeado para o módulo chamador."""

    return logging.getLogger(name)
