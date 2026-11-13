"""Acceso a Postgres. El pipeline corre como rol_ingesta (menor privilegio):
solo psycopg2, queries parametrizadas SIEMPRE — el "algo que parece SQL" del
maestro hostil nunca toca una consulta (B7 / eliminatorio 3).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from . import config


@contextmanager
def conexion_ingesta():
    user, pw = config.credenciales_rol("ingesta")
    conn = psycopg2.connect(config.dsn(user, pw))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def auditar(cur, corrida_id: uuid.UUID, etapa: str, objeto: str | None,
            leidas: int | None, cargadas: int | None, rechazadas: int | None,
            resultado: str, duracion_ms: int | None,
            detalle: dict | None = None) -> None:
    """INSERT en audit.bitacora (append-only). detalle NUNCA contiene PII."""
    cur.execute(
        """INSERT INTO audit.bitacora
             (corrida_id, usuario_db, etapa, objeto, filas_leidas,
              filas_cargadas, filas_rechazadas, resultado, duracion_ms, detalle)
           VALUES (%s, current_user, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (str(corrida_id), etapa, objeto, leidas, cargadas, rechazadas,
         resultado, duracion_ms,
         psycopg2.extras.Json(detalle) if detalle else None))
