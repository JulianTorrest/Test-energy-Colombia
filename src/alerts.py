"""Reglas de alerta del stream (Parte D). Funciones puras, testeables sin Kafka.

Rejilla: 96 intervalos de 15 min desde 2026-06-01T00:00:00-05:00.

  FRONTERA_SIN_REPORTE   3 intervalos consecutivos sin medicion; una sola
                         alerta por caida; ts = el del 3er intervalo ausente.
  CONSUMO_FUERA_DE_BANDA kwh > max_kwh de la frontera (estricto); ts = medicion.
  LECTURA_RETROACTIVA    ts_medicion anterior a la ultima ya procesada de esa
                         frontera (marca de agua); ts = la medicion tardia.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Bogota")
INICIO_REJILLA = datetime(2026, 6, 1, 0, 0, 0, tzinfo=TZ)
INTERVALOS = 96
PASO = timedelta(minutes=15)


@dataclass(frozen=True)
class Alerta:
    tipo: str
    frontera_id: str
    ts: datetime

    def clave(self):
        return (self.tipo, self.frontera_id, self.ts)


def _indice_intervalo(ts: datetime) -> int | None:
    """Intervalo de la rejilla al que pertenece ts. None si cae fuera o no
    esta alineado a 15 min (la medicion igual cuenta para fuera-de-banda)."""
    ts = ts.astimezone(TZ)
    if ts < INICIO_REJILLA:
        return None
    delta = ts - INICIO_REJILLA
    if delta.total_seconds() % 900 != 0:
        return None
    idx = int(delta.total_seconds() // 900)
    return idx if 0 <= idx < INTERVALOS else None


def alertas_llegada(eventos: list[dict],
                    max_kwh: dict[str, float]) -> list[Alerta]:
    """Reglas evaluadas en ORDEN DE LLEGADA (solo LECTURA_RETROACTIVA y
    CONSUMO_FUERA_DE_BANDA).

    eventos: [{'frontera_id','ts_medicion','kwh',...}] en el orden en que
    llegaron al topico. max_kwh: frontera_id -> limite superior.
    """
    alertas: list[Alerta] = []
    watermark: dict[str, datetime] = {}
    for ev in eventos:
        fid = ev.get("frontera_id")
        if not fid:
            continue
        ts = ev.get("ts_medicion")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts is None:
            continue

        limite = max_kwh.get(fid)
        if limite is not None and ev.get("kwh") is not None \
                and ev["kwh"] > limite:                     # estricto
            alertas.append(Alerta("CONSUMO_FUERA_DE_BANDA", fid, ts))

        prev = watermark.get(fid)
        if prev is not None and ts < prev:                  # watermark
            alertas.append(Alerta("LECTURA_RETROACTIVA", fid, ts))
        if prev is None or ts > prev:
            watermark[fid] = ts
    return alertas


def alertas_sin_reporte(eventos: list[dict],
                        fronteras: list[str]) -> list[Alerta]:
    """FRONTERA_SIN_REPORTE sobre la rejilla completa del dia.

    Se evalua con los eventos agrupados por frontera (orden irrelevante:
    lo que importa es que intervalos quedaron cubiertos). Una frontera sin
    ningun evento = caida total: una alerta en el 3er intervalo (00:45).
    Una sola alerta por tramo consecutivo de ausencia.
    """
    cubiertos: dict[str, set[int]] = {f: set() for f in fronteras}
    for ev in eventos:
        fid = ev.get("frontera_id")
        if fid is None:
            continue
        ts = ev.get("ts_medicion")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts is None:
            continue
        idx = _indice_intervalo(ts)
        if idx is not None:
            cubiertos.setdefault(fid, set()).add(idx)

    alertas: list[Alerta] = []
    for fid, idxs in cubiertos.items():
        racha = 0
        for i in range(INTERVALOS):
            if i in idxs:
                racha = 0
                continue
            racha += 1
            if racha == 3:  # 3er intervalo ausente de la caida
                alertas.append(Alerta(
                    "FRONTERA_SIN_REPORTE", fid,
                    INICIO_REJILLA + i * PASO))
    return alertas


def consolidar(eventos: list[dict], max_kwh: dict[str, float],
               fronteras: list[str]) -> list[Alerta]:
    """Todas las alertas, deduplicadas por (tipo, frontera, ts)."""
    vistos: set = set()
    salida: list[Alerta] = []
    for a in alertas_llegada(eventos, max_kwh) + \
            alertas_sin_reporte(eventos, fronteras):
        if a.clave() in vistos:
            continue
        vistos.add(a.clave())
        salida.append(a)
    return sorted(salida, key=lambda a: (a.tipo, a.frontera_id, a.ts))
