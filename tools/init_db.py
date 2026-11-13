#!/usr/bin/env python3
"""Inicializa la base (lo corre `make up`): espera a Postgres, aplica los
sql/*.sql en orden, sustituye credenciales de roles desde .env y publica los
datos de conexion en entrega.yaml (lo que `make verificar` lee despues).

Idempotente: todos los SQL usan IF NOT EXISTS / CREATE OR REPLACE / GRANT.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg2
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config  # noqa: E402

SQLS = ("001_schemas_tables.sql", "002_roles_grants.sql",
        "003_audit_trigger.sql", "004_vista_analitica.sql")


def _cargar_env() -> dict:
    vals = {}
    for linea in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def _esperar_pg(env: dict, timeout_s: int = 120) -> None:
    t0 = time.monotonic()
    while True:
        try:
            conn = psycopg2.connect(
                host=env["PG_HOST"], port=env["PG_PORT"],
                dbname=env["PG_DATABASE"], user=env["PG_SUPERUSER"],
                password=env["PG_SUPERPASSWORD"], connect_timeout=5)
            conn.close()
            print("postgres listo")
            return
        except Exception as e:  # OperationalError, UnicodeDecodeError (mensajes
            # de libpq localizados en Windows llegan en cp1252 y rompen el
            # decode UTF-8 de psycopg2, enmascarando el error real)
            if time.monotonic() - t0 > timeout_s:
                raise RuntimeError(
                    f"postgres no respondio en {timeout_s}s: "
                    f"{type(e).__name__}: {e}")
            time.sleep(2)


def _aplicar_sql(conn, env: dict) -> None:
    conn.autocommit = True
    roles = {
        "__INGESTA_USER__": env["PG_ROL_INGESTA_USER"],
        "__ANALISTA_USER__": env["PG_ROL_ANALISTA_USER"],
        "__AUDITOR_USER__": env["PG_ROL_AUDITOR_USER"],
        "__SUPER_USER__": env["PG_SUPERUSER"],
    }
    for nombre in SQLS:
        sql = (ROOT / "sql" / nombre).read_text(encoding="utf-8")
        for k, v in roles.items():
            sql = sql.replace(k, v)
        # Variables estilo psql :'var' que no entiende psycopg2.
        sql = (sql
               .replace(":'ingesta_pass'", f"'{env['PG_ROL_INGESTA_PASSWORD']}'")
               .replace(":'analista_pass'", f"'{env['PG_ROL_ANALISTA_PASSWORD']}'")
               .replace(":'auditor_pass'", f"'{env['PG_ROL_AUDITOR_PASSWORD']}'"))
        with conn.cursor() as cur:
            cur.execute(sql)
        print(f"aplicado {nombre}")


def _publicar_entrega(env: dict) -> None:
    ruta = ROOT / "entrega.yaml"
    man = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    pg = man.setdefault("postgres", {})
    pg["host"] = env["PG_HOST"]
    pg["port"] = int(env["PG_PORT"])
    pg["database"] = env["PG_DATABASE"]
    pg["superusuario"] = {"usuario": env["PG_SUPERUSER"],
                          "password": env["PG_SUPERPASSWORD"]}
    pg["roles"] = {
        "ingesta": {"usuario": env["PG_ROL_INGESTA_USER"],
                    "password": env["PG_ROL_INGESTA_PASSWORD"]},
        "analista": {"usuario": env["PG_ROL_ANALISTA_USER"],
                     "password": env["PG_ROL_ANALISTA_PASSWORD"]},
        "auditor": {"usuario": env["PG_ROL_AUDITOR_USER"],
                    "password": env["PG_ROL_AUDITOR_PASSWORD"]},
    }
    man["objetos"] = {
        "tabla_consumos": "core.fact_consumo_horario",
        "tabla_clientes": "core.dim_cliente",
        "vista_analytics": "analytics.vw_consumo_analitico",
        "tabla_auditoria": "audit.bitacora",
    }
    ruta.write_text(yaml.safe_dump(man, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    print("entrega.yaml actualizado con credenciales y objetos")


def main() -> int:
    if not (ROOT / ".env").exists():
        print("falta .env — corre tools/ensure_env.py primero", file=sys.stderr)
        return 1
    env = _cargar_env()
    _esperar_pg(env)
    conn = psycopg2.connect(host=env["PG_HOST"], port=env["PG_PORT"],
                            dbname=env["PG_DATABASE"], user=env["PG_SUPERUSER"],
                            password=env["PG_SUPERPASSWORD"])
    try:
        _aplicar_sql(conn, env)
    finally:
        conn.close()
    _publicar_entrega(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
