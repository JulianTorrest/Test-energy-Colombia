"""Salidas exactas del contrato (Parte B/E):

  out/consumo_mensual.csv  frontera_id,mes,consumo_kwh,horas_con_dato
  out/dq_report.json       todas las llaves obligatorias
  out/metrics.json         filas, rechazos por motivo, duracion por etapa

Todo se calcula DESDE LA BASE (estado final), no desde variables de la
corrida: asi una segunda corrida que no procesa nada produce identico
reporte (idempotencia verificable).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def consumo_mensual(cur, out_dir: Path) -> int:
    """frontera_id,mes(YYYY-MM hora local),consumo_kwh(3 dec),horas_con_dato.
    Una fila por combinacion frontera-mes con >=1 lectura valida."""
    cur.execute(
        """SELECT frontera_id,
                  to_char(ts AT TIME ZONE 'America/Bogota', 'YYYY-MM') AS mes,
                  round(sum(consumo_kwh), 3) AS consumo_kwh,
                  count(*) AS horas_con_dato
           FROM core.fact_consumo_horario
           GROUP BY frontera_id, mes
           ORDER BY frontera_id, mes""")
    filas = cur.fetchall()
    ruta = out_dir / "consumo_mensual.csv"
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frontera_id", "mes", "consumo_kwh", "horas_con_dato"])
        for fid, mes, kwh, horas in filas:
            w.writerow([fid, mes, f"{kwh:.3f}", horas])
    return len(filas)


def _huecos(cur, meses: list[str]) -> tuple[int, dict]:
    """B5: hueco = hora sin lectura valida en un mes con archivo procesado.

    Rejilla esperada: todas las fronteras del maestro x todos los meses que
    tienen archivo procesado; horas esperadas = dias del mes * 24.
    """
    if not meses:
        return 0, {}
    cur.execute(
        """WITH meses(mes_ini) AS (
             SELECT (m || '-01')::date FROM unnest(%s::text[]) m),
           horas_esperadas AS (
             SELECT f.frontera_id,
                    to_char(mes_ini, 'YYYY-MM') AS mes,
                    (extract(days FROM (mes_ini + interval '1 month'
                                        - mes_ini)) * 24)::int AS esperadas
             FROM core.dim_frontera f CROSS JOIN meses),
           con_dato AS (
             SELECT frontera_id,
                    to_char(ts AT TIME ZONE 'America/Bogota','YYYY-MM') AS mes,
                    count(*) AS validas
             FROM core.fact_consumo_horario GROUP BY 1, 2)
           SELECT h.frontera_id,
                  sum(h.esperadas - coalesce(d.validas,0))::int AS huecos
           FROM horas_esperadas h
           LEFT JOIN con_dato d ON d.frontera_id = h.frontera_id
                                AND d.mes = h.mes
           GROUP BY h.frontera_id
           HAVING sum(h.esperadas - coalesce(d.validas,0)) > 0
           ORDER BY h.frontera_id""", (meses,))
    por_frontera = {fid: int(h) for fid, h in cur.fetchall()}
    return sum(por_frontera.values()), por_frontera


def dq_report(cur, meses: list[str], clientes_con_pii: int,
              conteos_dedup: dict, facturacion) -> dict:
    cur.execute("SELECT coalesce(sum(filas_leidas),0) FROM stage.control_archivos")
    filas_leidas = int(cur.fetchone()[0])

    cur.execute("""SELECT motivo, count(*) FROM stage.cuarentena
                   GROUP BY motivo""")
    rechazos = {m: int(c) for m, c in cur.fetchall()}
    cur.execute("""SELECT count(*) FROM stage.consumo_lectura
                   WHERE lower(coalesce(unidad_original,'')) = 'mwh'""")
    mwh = int(cur.fetchone()[0])

    total_huecos, huecos_frt = _huecos(cur, meses)

    reporte = {
        "filas_leidas": filas_leidas,
        "duplicados_exactos": conteos_dedup["duplicados_exactos"],
        "duplicados_conflictivos": conteos_dedup["duplicados_conflictivos"],
        "registros_negativos": rechazos.get("valor_negativo", 0),
        "registros_nulos": rechazos.get("valor_nulo", 0)
                           + rechazos.get("valor_no_numerico", 0),
        "registros_outlier": rechazos.get("outlier", 0),
        "registros_mwh_convertidos": mwh,
        "correcciones_tardias_aplicadas":
            conteos_dedup["correcciones_tardias_aplicadas"],
        "horas_sin_lectura_valida_total": total_huecos,
        "horas_sin_lectura_valida_por_frontera": huecos_frt,
        "fronteras_huerfanas_facturacion": facturacion,
        "clientes_con_pii_en_texto_libre": clientes_con_pii,
    }
    with open(config.OUT_DIR / "dq_report.json", "w", encoding="utf-8") as fh:
        json.dump(reporte, fh, ensure_ascii=False, indent=2)
    return reporte


def metrics_json(resumen: dict) -> dict:
    """resumen: {etapas:{nombre:{leidas,cargadas,rechazadas,duracion_ms}},
                 rechazos_por_motivo:{}, corrida_id, inicio, fin}."""
    doc = {
        "corrida_id": resumen["corrida_id"],
        "inicio": resumen["inicio"].isoformat(),
        "fin": datetime.now(timezone.utc).isoformat(),
        "duracion_total_ms": int((datetime.now(timezone.utc)
                                 - resumen["inicio"]).total_seconds() * 1000),
        "filas_leidas": sum(e.get("leidas", 0)
                            for e in resumen["etapas"].values()),
        "filas_cargadas": sum(e.get("cargadas", 0)
                             for e in resumen["etapas"].values()),
        "filas_rechazadas_por_motivo": resumen["rechazos_por_motivo"],
        "etapas": resumen["etapas"],
    }
    with open(config.OUT_DIR / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, default=str)
    return doc
