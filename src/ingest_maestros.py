"""Ingesta de maestros (clientes con PII hostil, fronteras, limites).

B7: clientes.csv trae entradas hostiles (comillas anidadas, salto de linea
embebido en campo citado, cadenas larguisimas, texto que parece SQL). Nada
de eso se ejecuta: csv.reader para el formato, queries parametrizadas para
la base. Cada fila que no se pueda procesar va a cuarentena con su payload
cifrado (puede contener PII), nunca se pierde en silencio.

C2/C3: PII de columnas marcadas Restringido-PII en data_classification.yaml
se cifra con AES-256-GCM; observaciones se enmascara (pii.enmascarar) y el
conteo de clientes afectados va al dq_report.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass

from . import config, crypto, parse, pii
from .db import auditar
from .parse import ArchivoCorrupto, leer_bytes_texto


@dataclass
class ResultadoMaestro:
    leidas: int = 0
    cargadas: int = 0
    rechazadas: int = 0
    clientes_con_pii_texto_libre: int = 0


def _leer_csv_ruta(ruta) -> tuple[list[dict], str, int]:
    """Lee un maestro CSV con parseo robusto. Devuelve (filas, encoding, n).

    Para B7: csv.reader maneja comillas anidadas y newlines embebidos; el
    limite de campo esta ampliado para cadenas larguisimas.
    """
    raw = ruta.read_bytes()
    texto, enc = leer_bytes_texto(raw)
    sep = parse.detectar_delimitador(texto.splitlines()[0])
    rdr = csv.DictReader(io.StringIO(texto), delimiter=sep)
    filas = [dict(r) for r in rdr]
    return filas, enc, len(filas)


def _cuarentena(cur, corrida_id, fuente, fila_num, motivo, payload_dict,
                key, cifrar_payload=True):
    raw = json.dumps(payload_dict, ensure_ascii=False, default=str)
    if cifrar_payload and key:
        cur.execute(
            """INSERT INTO stage.cuarentena
                 (fuente, fila_num, motivo, payload, payload_cifrado, corrida_id)
               VALUES (%s,%s,%s,%s,true,%s)""",
            (fuente, fila_num, motivo,
             crypto.cifrar(raw, key), str(corrida_id)))
    else:
        cur.execute(
            """INSERT INTO stage.cuarentena
                 (fuente, fila_num, motivo, payload, payload_cifrado, corrida_id)
               VALUES (%s,%s,%s,%s,false,%s)""",
            (fuente, fila_num, motivo, raw, str(corrida_id)))


def cargar_clientes(cur, corrida_id: uuid.UUID, key: bytes,
                    log) -> ResultadoMaestro:
    res = ResultadoMaestro()
    ruta = config.DATA_DIR / "maestros" / "clientes.csv"
    try:
        filas, enc, n = _leer_csv_ruta(ruta)
    except (ArchivoCorrupto, csv.Error, UnicodeError) as e:
        log.error("clientes.csv no parsea", extra={"extra": {"error": str(e)[:200]}})
        _cuarentena(cur, corrida_id, "maestros/clientes.csv", None,
                    "archivo_corrupto", {"archivo": "clientes.csv",
                                        "error": str(e)[:500]}, key)
        res.rechazadas = 1
        return res
    res.leidas = n
    cols_pii = config.columnas_pii("dim_cliente")

    for i, r in enumerate(filas, start=2):
        # SAVEPOINT por fila: si la fila falla se revierte solo esa
        # unidad y la escritura a cuarentena sigue dentro de una
        # transaccion valida (evita InFailedSqlTransaction).
        cur.execute("SAVEPOINT sp_fila")
        try:
            cid = (r.get("cliente_id") or "").strip()
            if not cid:
                raise ValueError("cliente_id vacio")
            obs, n_pii = pii.enmascarar(r.get("observaciones"))
            if n_pii:
                res.clientes_con_pii_texto_libre += 1
            campos = {k: (r.get(k) or "").strip() or None for k in
                      ("razon_social", "nit", "email_contacto",
                       "celular_contacto", "direccion", "ciudad",
                       "departamento", "representante_legal")}
            # NOTA (menor privilegio): no se usa "ON CONFLICT DO UPDATE SET
            # col=EXCLUDED.col" porque Postgres exige SELECT sobre cada
            # columna referenciada por EXCLUDED — y rol_ingesta NO puede leer
            # las columnas cifradas. Patron upsert sin lectura de PII:
            # UPDATE primero (WHERE solo toca cliente_id, con SELECT ok);
            # si no toco filas, INSERT con DO NOTHING por si hay carrera.
            valores = (campos["razon_social"] or "(sin razon social)",
                       crypto.cifrar(campos["nit"], key)
                       if "nit" in cols_pii else None,
                       crypto.indice_ciego(campos["nit"], key),
                       crypto.cifrar(campos["email_contacto"], key),
                       crypto.cifrar(campos["celular_contacto"], key),
                       crypto.cifrar(campos["direccion"], key),
                       campos["ciudad"], campos["departamento"],
                       crypto.cifrar(campos["representante_legal"], key),
                       obs)
            cur.execute(
                """UPDATE core.dim_cliente SET
                     razon_social=%s, nit_cifrado=%s, nit_hash=%s,
                     email_cifrado=%s, celular_cifrado=%s,
                     direccion_cifrada=%s, ciudad=%s, departamento=%s,
                     representante_cifrado=%s,
                     observaciones_enmascaradas=%s
                   WHERE cliente_id=%s""",
                (*valores, cid))
            if cur.rowcount == 0:
                cur.execute(
                    """INSERT INTO core.dim_cliente
                         (cliente_id, razon_social, nit_cifrado, nit_hash,
                          email_cifrado, celular_cifrado, direccion_cifrada,
                          ciudad, departamento, representante_cifrado,
                          observaciones_enmascaradas)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (cliente_id) DO NOTHING""",
                    (cid, *valores))
            cur.execute("RELEASE SAVEPOINT sp_fila")
            res.cargadas += 1
        except Exception as e:  # noqa: BLE001 - nada se pierde en silencio
            cur.execute("ROLLBACK TO SAVEPOINT sp_fila")
            res.rechazadas += 1
            _cuarentena(cur, corrida_id, "maestros/clientes.csv", i,
                        f"fila_invalida:{type(e).__name__}",
                        {"fila": {k: v for k, v in r.items()
                                  if k != "observaciones"},
                         "observaciones_len":
                             len(r.get("observaciones") or "")},
                        key)
    log.info("maestro clientes cargado", extra={"extra": {
        "filas": res.leidas, "ok": res.cargadas, "rechazadas": res.rechazadas,
        "encoding": enc, "con_pii_texto": res.clientes_con_pii_texto_libre}})
    return res


def cargar_fronteras(cur, corrida_id: uuid.UUID, key: bytes,
                     log) -> ResultadoMaestro:
    res = ResultadoMaestro()
    filas, _, n = _leer_csv_ruta(config.DATA_DIR / "maestros" / "fronteras.csv")
    res.leidas = n
    for i, r in enumerate(filas, start=2):
        cur.execute("SAVEPOINT sp_fila")
        try:
            fid = (r.get("frontera_id") or "").strip()
            if not fid:
                raise ValueError("frontera_id vacio")
            cur.execute(
                """INSERT INTO core.dim_frontera
                     (frontera_id, codigo_sic, cliente_id, mercado,
                      operador_red, nivel_tension, fecha_registro, estado)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (frontera_id) DO UPDATE SET
                     codigo_sic=EXCLUDED.codigo_sic,
                     cliente_id=EXCLUDED.cliente_id,
                     mercado=EXCLUDED.mercado,
                     operador_red=EXCLUDED.operador_red,
                     nivel_tension=EXCLUDED.nivel_tension,
                     fecha_registro=EXCLUDED.fecha_registro,
                     estado=EXCLUDED.estado""",
                (fid, (r.get("codigo_sic") or "").strip() or None,
                 (r.get("cliente_id") or "").strip() or None,
                 (r.get("mercado") or "").strip() or None,
                 (r.get("operador_red") or "").strip() or None,
                 int(r["nivel_tension"]) if (r.get("nivel_tension") or "")
                 .strip().isdigit() else None,
                 (r.get("fecha_registro") or "").strip() or None,
                 (r.get("estado") or "").strip() or None))
            cur.execute("RELEASE SAVEPOINT sp_fila")
            res.cargadas += 1
        except Exception as e:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT sp_fila")
            res.rechazadas += 1
            _cuarentena(cur, corrida_id, "maestros/fronteras.csv", i,
                        f"fila_invalida:{type(e).__name__}", dict(r), key,
                        cifrar_payload=False)
    log.info("maestro fronteras cargado", extra={"extra": {
        "filas": res.leidas, "ok": res.cargadas, "rechazadas": res.rechazadas}})
    return res


def cargar_limites(cur, corrida_id: uuid.UUID, key: bytes,
                   log) -> ResultadoMaestro:
    res = ResultadoMaestro()
    filas, _, n = _leer_csv_ruta(config.DATA_DIR / "maestros"
                               / "limites_frontera.csv")
    res.leidas = n
    for i, r in enumerate(filas, start=2):
        cur.execute("SAVEPOINT sp_fila")
        try:
            fid = (r.get("frontera_id") or "").strip()
            if not fid:
                raise ValueError("frontera_id vacio")
            cur.execute(
                """INSERT INTO core.limite_frontera
                     (frontera_id, min_kwh, max_kwh)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (frontera_id) DO UPDATE SET
                     min_kwh=EXCLUDED.min_kwh, max_kwh=EXCLUDED.max_kwh""",
                (fid, parse.parse_decimal(r.get("min_kwh")),
                 parse.parse_decimal(r.get("max_kwh"))))
            cur.execute("RELEASE SAVEPOINT sp_fila")
            res.cargadas += 1
        except Exception as e:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT sp_fila")
            res.rechazadas += 1
            _cuarentena(cur, corrida_id, "maestros/limites_frontera.csv", i,
                        f"fila_invalida:{type(e).__name__}", dict(r), key,
                        cifrar_payload=False)
    log.info("maestro limites cargado", extra={"extra": {
        "filas": res.leidas, "ok": res.cargadas, "rechazadas": res.rechazadas}})
    return res


def cargar_maestros(cur, corrida_id: uuid.UUID, key: bytes, log) -> dict:
    """Corre los tres maestros; devuelve resumen para dq_report/metrics."""
    res_cli = cargar_clientes(cur, corrida_id, key, log)
    res_frt = cargar_fronteras(cur, corrida_id, key, log)
    res_lim = cargar_limites(cur, corrida_id, key, log)
    total = ResultadoMaestro(
        leidas=res_cli.leidas + res_frt.leidas + res_lim.leidas,
        cargadas=res_cli.cargadas + res_frt.cargadas + res_lim.cargadas,
        rechazadas=res_cli.rechazadas + res_frt.rechazadas + res_lim.rechazadas,
        clientes_con_pii_texto_libre=res_cli.clientes_con_pii_texto_libre)
    return {"clientes": res_cli, "fronteras": res_frt, "limites": res_lim,
            "total": total}
