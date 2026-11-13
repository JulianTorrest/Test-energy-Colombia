"""Orquestador del pipeline batch (make run).

Etapas: maestros -> consumos -> dedup/fact -> facturacion -> reportes.
Corre como rol_ingesta. Cada etapa audita en audit.bitacora. La transaccion
es por etapa (un archivo malo no tira lo ya cargado; los meses previos no
cambian ante un archivo corrupto nuevo).

Idempotencia: archivos identicos ya procesados se omiten; el fact se
reconstruye desde staging => segunda corrida = mismo estado y mismos
reportes.
"""
from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone

from . import config
from .db import auditar, conexion_ingesta
from .ingest_consumos import cargar_consumos, reconstruir_fact
from .ingest_facturacion import cargar_facturacion
from .ingest_maestros import cargar_maestros
from .logging_setup import get_logger
from .reports import consumo_mensual, dq_report, metrics_json


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def main() -> int:
    config.cargar_env()
    config.OUT_DIR.mkdir(exist_ok=True)
    config.LOG_DIR.mkdir(exist_ok=True)
    log = get_logger("pipeline", config.LOG_DIR / "pipeline.log")
    corrida_id = uuid.uuid4()
    inicio = datetime.now(timezone.utc)
    resumen = {"corrida_id": str(corrida_id), "inicio": inicio,
               "etapas": {}, "rechazos_por_motivo": {}}
    key = config.pii_key()

    log.info("corrida iniciada", extra={"extra": {"corrida_id": str(corrida_id)}})

    with conexion_ingesta() as conn:
        with conn.cursor() as cur:
            # --- Etapa: maestros -------------------------------------------
            t0 = time.perf_counter()
            res_m = cargar_maestros(cur, corrida_id, key, log)
            d = _ms(t0)
            resumen["etapas"]["maestros"] = {
                "leidas": res_m["total"].leidas,
                "cargadas": res_m["total"].cargadas,
                "rechazadas": res_m["total"].rechazadas,
                "duracion_ms": d}
            auditar(cur, corrida_id, "maestros", "core.dim_cliente+dim_frontera"
                    "+limite_frontera", res_m["total"].leidas,
                    res_m["total"].cargadas, res_m["total"].rechazadas,
                    "ok", d)
            conn.commit()

            # --- Etapa: consumos -------------------------------------------
            t0 = time.perf_counter()
            res_c = cargar_consumos(cur, corrida_id, key, log)
            conn.commit()
            conteos = reconstruir_fact(cur)
            d = _ms(t0)
            resumen["etapas"]["consumos"] = {
                "archivos": len(res_c.archivos),
                "procesados": res_c.procesados, "omitidos": res_c.omitidos,
                "rechazados": res_c.rechazados, "leidas": res_c.leidas,
                "cargadas": res_c.cargadas_staging,
                "rechazadas": res_c.invalidas, "duracion_ms": d}
            auditar(cur, corrida_id, "consumos",
                    "core.fact_consumo_horario", res_c.leidas,
                    res_c.cargadas_staging, res_c.invalidas, "ok", d,
                    {"archivos": res_c.archivos,
                     "procesados": res_c.procesados,
                     "omitidos": res_c.omitidos,
                     "rechazados": res_c.rechazados})
            conn.commit()

            # --- Etapa: facturacion -----------------------------------------
            t0 = time.perf_counter()
            res_f = cargar_facturacion(cur, corrida_id, key, log)
            d = _ms(t0)
            resumen["etapas"]["facturacion"] = {
                "archivos": res_f.archivos, "leidas": res_f.leidas,
                "cargadas": res_f.cargadas,
                "rechazadas": res_f.huerfanas + res_f.invalidas,
                "duracion_ms": d}
            auditar(cur, corrida_id, "facturacion", "core.fact_factura",
                    res_f.leidas, res_f.cargadas,
                    res_f.huerfanas + res_f.invalidas, "ok", d,
                    {"huerfanas": res_f.huerfanas})
            conn.commit()

            # --- Etapa: reportes --------------------------------------------
            t0 = time.perf_counter()
            n_filas_csv = consumo_mensual(cur, config.OUT_DIR)
            meses = sorted(res_c.meses)
            rep = dq_report(cur, meses,
                            res_m["total"].clientes_con_pii_texto_libre,
                            conteos, len(res_f.fronteras_huerfanas))
            d = _ms(t0)
            resumen["etapas"]["reportes"] = {"duracion_ms": d,
                                             "filas_consumo_mensual": n_filas_csv}
            auditar(cur, corrida_id, "reportes",
                    "out/consumo_mensual.csv+dq_report.json",
                    None, n_filas_csv, None, "ok", d)
            conn.commit()

            # rechazos por motivo para metrics.json
            cur.execute("""SELECT motivo, count(*) FROM stage.cuarentena
                           GROUP BY motivo""")
            resumen["rechazos_por_motivo"] = {m: int(c)
                                            for m, c in cur.fetchall()}

    metrics_json(resumen)
    auditar_final_ok = rep is not None
    log.info("corrida finalizada",
             extra={"extra": {"corrida_id": str(corrida_id),
                              "filas_consumo_mensual": n_filas_csv,
                              "dq": {k: rep[k] for k in
                                     ("filas_leidas", "duplicados_exactos",
                                      "duplicados_conflictivos",
                                      "correcciones_tardias_aplicadas")}}})
    return 0 if auditar_final_ok else 1


if __name__ == "__main__":
    sys.exit(main())
