"""Logging estructurado JSON -> logs/pipeline.log (Parte E).

REGLA DURA: ningun evento puede contener PII. Solo se loguean ids tecnicos
(frontera_id, cliente_id, archivos), conteos y estados. Las funciones de
ingesta nunca reciben el logger con valores de campos personales.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "nivel": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Campos extra estructurados (solo valores no-PII por contrato).
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            evento.update(extra)
        if record.exc_info:
            evento["error"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(evento, ensure_ascii=False, default=str)


def get_logger(nombre: str, log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(nombre)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fmt = _JsonFormatter()
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger
