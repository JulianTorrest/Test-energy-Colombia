"""Configuracion central: rutas, .env, pipeline.yaml, data_classification.yaml.

Cero secretos hardcodeados: todo credencial llega por variables de entorno
(cargadas desde .env, que genera tools/ensure_env.py y esta gitignored).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
LOG_DIR = ROOT / "logs"
SQL_DIR = ROOT / "sql"

_ENV_LOADED = False


def cargar_env() -> None:
    """Carga .env una sola vez (si existe). No pisa variables ya definidas."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover - dotenv siempre instalado
        pass


def env(nombre: str, default: str | None = None, requerido: bool = False) -> str:
    cargar_env()
    valor = os.environ.get(nombre, default)
    if requerido and not valor:
        raise RuntimeError(f"Falta la variable de entorno {nombre} "
                           "(corre `make up` para generar .env)")
    return valor  # type: ignore[return-value]


def cfg_pipeline() -> dict:
    with open(ROOT / "config" / "pipeline.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def clasificacion() -> dict:
    """C1: data_classification.yaml es un artefacto VIVO: el pipeline lo lee
    para decidir que columnas van cifradas (Restringido-PII) y cuales se
    enmascaran. Si el yaml dice que una columna es PII, se cifra."""
    with open(ROOT / "data_classification.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def columnas_pii(tabla: str) -> set[str]:
    """Columnas marcadas Restringido-PII para una tabla del modelo."""
    tablas = clasificacion().get("tablas", {})
    cols = tablas.get(tabla, {}).get("columnas", {})
    return {c for c, meta in cols.items()
            if isinstance(meta, dict) and
            meta.get("clasificacion") == "Restringido-PII"}


def dsn(usuario: str, password: str) -> str:
    cargar_env()
    return (f"host={env('PG_HOST', 'localhost')} "
            f"port={env('PG_PORT', '5432')} "
            f"dbname={env('PG_DATABASE', 'bia_datos')} "
            f"user={usuario} password={password} connect_timeout=10")


def credenciales_rol(nombre: str) -> tuple[str, str]:
    """('ingesta'|'analista'|'auditor') -> (usuario, password) desde .env."""
    pref = f"PG_ROL_{nombre.upper()}"
    return env(f"{pref}_USER", f"rol_{nombre}"), env(f"{pref}_PASSWORD",
                                                    requerido=True)


def pii_key() -> bytes:
    raw = env("PII_ENCRYPTION_KEY", requerido=True)
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise RuntimeError("PII_ENCRYPTION_KEY debe ser hex de 64 chars "
                           "(32 bytes)")
    return key
