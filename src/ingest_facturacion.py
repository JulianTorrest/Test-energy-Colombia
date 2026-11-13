"""Ingesta de facturacion (JSON anidado, Parte A + B6).

Cada facturacion_YYYY-MM.json trae un array `facturas`. B6: una factura cuya
frontera_id no existe en core.dim_frontera NO se carga: va a cuarentena con
payload cifrado (contiene email/nit del cliente => PII).

El join factura->cliente se hace por nit_hash (indice ciego HMAC): nunca se
compara el NIT en claro.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from . import config, crypto
from .db import auditar


@dataclass
class ResultadoFacturacion:
    archivos: int = 0
    leidas: int = 0
    cargadas: int = 0
    huerfanas: int = 0
    invalidas: int = 0
    fronteras_huerfanas: set = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.fronteras_huerfanas is None:
            self.fronteras_huerfanas = set()


def _fronteras_validas(cur) -> set[str]:
    cur.execute("SELECT frontera_id FROM core.dim_frontera")
    return {r[0] for r in cur.fetchall()}


def _a_cuarentena(cur, corrida_id, fuente, motivo, factura: dict, key):
    cur.execute(
        """INSERT INTO stage.cuarentena
             (fuente, motivo, payload, payload_cifrado, corrida_id)
           VALUES (%s,%s,%s,true,%s)""",
        (fuente, motivo,
         crypto.cifrar(json.dumps(factura, ensure_ascii=False,
                                  default=str), key),
         str(corrida_id)))


def _procesar_un_json(cur, corrida_id, ruta, validas: set[str], key, log,
                      res: ResultadoFacturacion) -> None:
    nombre = ruta.name
    try:
        doc = json.loads(ruta.read_text(encoding="utf-8"))
        facturas = doc.get("facturas", [])
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("facturacion corrupta a cuarentena",
                    extra={"extra": {"archivo": nombre, "error": str(e)[:200]}})
        cur.execute(
            """INSERT INTO stage.cuarentena
                 (fuente, motivo, payload, payload_cifrado, corrida_id)
               VALUES (%s,%s,%s,false,%s)""",
            (f"facturacion/{nombre}", "archivo_corrupto",
             json.dumps({"error": str(e)[:500]}), str(corrida_id)))
        res.invalidas += 1
        return

    # Idempotente por archivo: re-correr no duplica ni en fact ni en
    # cuarentena (cuarentena = rechazos vigentes; historia en bitacora).
    cur.execute("DELETE FROM core.fact_factura WHERE archivo=%s", (nombre,))
    cur.execute("DELETE FROM stage.cuarentena WHERE fuente=%s",
                (f"facturacion/{nombre}",))
    res.archivos += 1
    for fac in facturas:
        res.leidas += 1
        fid = (fac.get("frontera_id") or "").strip()
        if not fid or fid not in validas:  # B6
            res.huerfanas += 1
            res.fronteras_huerfanas.add(fid or "(vacio)")
            _a_cuarentena(cur, corrida_id, f"facturacion/{nombre}",
                          "frontera_huerfana", fac, key)
            continue
        cli = fac.get("cliente") or {}
        nit = cli.get("nit")
        cur.execute("SAVEPOINT sp_factura")
        try:
            cur.execute(
                """INSERT INTO core.fact_factura
                     (factura_id, periodo, frontera_id, cliente_nit_cifrado,
                      cliente_nit_hash, cliente_razon_social,
                      cliente_email_cifrado, consumo_facturado_kwh,
                      tarifa_cop_kwh, valor_total_cop, archivo)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (fac.get("factura_id"), doc.get("periodo"), fid,
                 crypto.cifrar(nit, key), crypto.indice_ciego(nit, key),
                 cli.get("razon_social"),
                 crypto.cifrar(cli.get("email_facturacion"), key),
                 fac.get("consumo_facturado_kwh"),
                 fac.get("tarifa_cop_kwh"), fac.get("valor_total_cop"),
                 nombre))
            cur.execute("RELEASE SAVEPOINT sp_factura")
            res.cargadas += 1
        except Exception as e:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT sp_factura")
            res.invalidas += 1
            _a_cuarentena(cur, corrida_id, f"facturacion/{nombre}",
                          f"factura_invalida:{type(e).__name__}", fac, key)
    log.info("facturacion procesada", extra={"extra": {
        "archivo": nombre, "facturas": len(facturas)}})


def cargar_facturacion(cur, corrida_id: uuid.UUID, key: bytes,
                       log) -> ResultadoFacturacion:
    res = ResultadoFacturacion()
    validas = _fronteras_validas(cur)
    for ruta in sorted((config.DATA_DIR / "facturacion").glob("*.json")):
        try:
            _procesar_un_json(cur, corrida_id, ruta, validas, key, log, res)
        except Exception as e:  # noqa: BLE001
            res.invalidas += 1
            log.error("fallo inesperado en facturacion",
                      extra={"extra": {"archivo": ruta.name,
                                       "error": str(e)[:300]}},
                      exc_info=True)
    return res
