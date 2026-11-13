#!/usr/bin/env python3
"""Genera .env con credenciales aleatorias si no existe (lo corre `make up`).

C6: cero secretos en el codigo. Las credenciales efimeras de evaluacion se
generan aqui, viven en .env (gitignored) y las publica tools/init_db.py en
entrega.yaml para que el evaluador se pueda conectar.
"""
from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def _rand(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def main() -> None:
    if ENV.exists():
        print(".env ya existe, no se toca")
        return
    contenido = f"""# Generado por tools/ensure_env.py — credenciales efimeras locales.
# Gitignored: nunca versionar.
PG_HOST=127.0.0.1
PG_PORT=5433
PG_DATABASE=bia_datos
PG_SUPERUSER=postgres
PG_SUPERPASSWORD={_rand()}

PG_ROL_INGESTA_USER=rol_ingesta
PG_ROL_INGESTA_PASSWORD={_rand()}
PG_ROL_ANALISTA_USER=rol_analista
PG_ROL_ANALISTA_PASSWORD={_rand()}
PG_ROL_AUDITOR_USER=rol_auditor
PG_ROL_AUDITOR_PASSWORD={_rand()}

# Llave AES-256-GCM para PII en reposo (hex 64 chars = 32 bytes).
PII_ENCRYPTION_KEY={secrets.token_hex(32)}

MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY={_rand(12)}
MINIO_SECRET_KEY={_rand()}

KAFKA_BOOTSTRAP=localhost:9092
KAFKA_TOPIC=mediciones.raw
"""
    ENV.write_text(contenido, encoding="utf-8")
    print(".env generado con credenciales aleatorias")


if __name__ == "__main__":
    main()
