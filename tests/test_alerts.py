"""Tests de las tres reglas de alerta del stream (Parte D)."""
from __future__ import annotations

from datetime import datetime, timedelta

from src import alerts

I = alerts.INICIO_REJILLA


def ev(fid, idx=None, kwh=10.0, ts=None):
    t = ts if ts is not None else I + idx * alerts.PASO
    return {"frontera_id": fid, "ts_medicion": t, "kwh": kwh}


def test_fuera_de_banda_estricto():
    eventos = [ev("F1", 0, kwh=200.0), ev("F1", 1, kwh=200.0),
               ev("F1", 2, kwh=150.0)]
    out = alerts.alertas_llegada(eventos, {"F1": 200.0})
    # mayor estricto: kwh == max NO alerta; 150 no; ninguno > 200
    assert all(a.tipo != "CONSUMO_FUERA_DE_BANDA" for a in out)
    eventos.append(ev("F1", 3, kwh=200.001))
    out = alerts.alertas_llegada(eventos, {"F1": 200.0})
    banda = [a for a in out if a.tipo == "CONSUMO_FUERA_DE_BANDA"]
    assert len(banda) == 1 and banda[0].ts == I + 3 * alerts.PASO


def test_retroactiva_watermark():
    eventos = [ev("F1", 5), ev("F1", 2)]        # llega tarde el intervalo 2
    out = alerts.alertas_llegada(eventos, {})
    retro = [a for a in out if a.tipo == "LECTURA_RETROACTIVA"]
    assert len(retro) == 1
    assert retro[0].ts == I + 2 * alerts.PASO   # ts de la medicion tardia


def test_retroactiva_no_falsa_alarma():
    eventos = [ev("F1", i) for i in range(10)]
    out = alerts.alertas_llegada(eventos, {})
    assert not [a for a in out if a.tipo == "LECTURA_RETROACTIVA"]


def test_sin_reporte_3_consecutivos():
    # frontera reporta 0,1 y del 5 en adelante: hueco 2,3,4 -> alerta en 4
    eventos = [ev("F1", 0), ev("F1", 1)] + [ev("F1", i) for i in range(5, 96)]
    out = alerts.alertas_sin_reporte(eventos, ["F1"])
    sinrep = [a for a in out if a.tipo == "FRONTERA_SIN_REPORTE"]
    assert len(sinrep) == 1                      # una sola alerta por caida
    assert sinrep[0].ts == I + 4 * alerts.PASO   # ts del 3er ausente


def test_sin_reporte_caida_total_una_alerta():
    out = alerts.alertas_sin_reporte([], ["F1"])
    sinrep = [a for a in out if a.tipo == "FRONTERA_SIN_REPORTE"]
    assert len(sinrep) == 1
    assert sinrep[0].ts == I + 2 * alerts.PASO   # 3er intervalo = 00:45


def test_sin_reporte_dos_caidas_dos_alertas():
    # hueco 2-4 (alerta en 4) y hueco 10-13 (alerta en 12)
    idxs = [0, 1] + list(range(5, 10)) + list(range(14, 96))
    eventos = [ev("F1", i) for i in idxs]
    out = alerts.alertas_sin_reporte(eventos, ["F1"])
    sinrep = sorted(a.ts for a in out if a.tipo == "FRONTERA_SIN_REPORTE")
    assert sinrep == [I + 4 * alerts.PASO, I + 12 * alerts.PASO]


def test_consolidar_dedup():
    eventos = [ev("F1", 3, kwh=999.0), ev("F1", 3, kwh=999.0)]
    out = alerts.consolidar(eventos, {"F1": 100.0}, ["F1"])
    claves = [a.clave() for a in out]
    assert len(claves) == len(set(claves))       # sin duplicados
