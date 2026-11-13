"""Tests del parser de consumos: encoding, delimitador, timestamps (B1),
unidades (B2) e invalidos (B4). No requieren Docker."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src import parse
from src.parse import ArchivoCorrupto, FilaLectura, elegir_ganador

BOG = ZoneInfo("America/Bogota")

CSV_BASE = (b"frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
            b"CO1,2026-01-01 00:00:00,275.283,1,OK\n"
            b"CO1,2026-01-01 01:00:00,-5,1,OK\n"
            b"CO1,2026-01-01 02:00:00,,1,OK\n"
            b"CO1,2026-01-01 03:00:00,2000000.5,1,OK\n")

CSV_UTC = ("frontera_id;timestamp_utc;consumo_kwh;version_lectura;calidad_lectura\n"
           "CO1;2026-02-01T05:00:00Z;163,795;1;OK\n").encode("utf-8")

CSV_UNIDAD = (b"frontera_id,timestamp,kwh,unidad,version_lectura,calidad_lectura\n"
              b"CO1,2026-03-01 00:00:00,126.985,kWh,1,OK\n"
              b"CO1,2026-03-01 01:00:00,1.5,MWh,1,OK\n")


def test_formato_base_ok():
    filas, meta = parse.procesar_archivo(CSV_BASE)
    assert filas[0].estado == "ok"
    assert filas[0].consumo_kwh == Decimal("275.283")
    assert filas[0].ts == datetime(2026, 1, 1, 0, 0, tzinfo=BOG)


def test_b4_negativo_nulo_outlier():
    filas, _ = parse.procesar_archivo(CSV_BASE)
    assert filas[1].motivo == "valor_negativo"
    assert filas[2].motivo == "valor_nulo"
    assert filas[3].motivo == "outlier"


def test_b1_utc_a_bogota():
    """05:00Z == 00:00 en Bogota (UTC-5 fijo)."""
    filas, meta = parse.procesar_archivo(CSV_UTC)
    assert meta["delimitador"] == "';'"
    assert filas[0].ts == datetime(2026, 2, 1, 0, 0, tzinfo=BOG)
    assert filas[0].consumo_kwh == Decimal("163.795")  # decimal coma


def test_b2_unidad_mwh():
    filas, _ = parse.procesar_archivo(CSV_UNIDAD)
    assert filas[0].consumo_kwh == Decimal("126.985")
    assert filas[1].consumo_kwh == Decimal("1500.000")  # 1.5 MWh -> 1500 kWh


def test_decimal_mixto():
    assert parse.parse_decimal("1.234,56") == Decimal("1234.56")
    assert parse.parse_decimal("1,234.56") == Decimal("1234.56")
    assert parse.parse_decimal("basura") is None
    assert parse.parse_decimal("") is None


def test_timestamp_naive_es_local():
    dt = parse.parse_timestamp("2026-04-01 20:00:00")
    assert dt == datetime(2026, 4, 1, 20, 0, tzinfo=BOG)
    assert parse.parse_timestamp("no-fecha") is None


def test_archivo_corrupto():
    with pytest.raises(ArchivoCorrupto):
        parse.procesar_archivo(b"")
    with pytest.raises(ArchivoCorrupto):
        parse.procesar_archivo(b"esto no tiene separadores ni columnas\n")


def test_encoding_latin1():
    data = "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n" \
           "CO1,2026-01-01 00:00:00,1.0,1,VÁLIDA\n".encode("latin-1")
    filas, meta = parse.procesar_archivo(data)
    assert meta["encoding"] in ("cp1252", "latin-1")
    assert filas[0].estado == "ok"


def test_dedup_b3():
    """Gana mayor version; empate -> ultimo archivo (mayor orden);
    empate -> ultima fila."""
    a = FilaLectura(fila_num=1, version_lectura=1,
                    consumo_kwh=Decimal("10"))
    b = FilaLectura(fila_num=2, version_lectura=2,
                    consumo_kwh=Decimal("99"))
    a.orden_archivo = b.orden_archivo = 0
    assert elegir_ganador([a, b]) is b            # mayor version gana

    c = FilaLectura(fila_num=1, version_lectura=2,
                    consumo_kwh=Decimal("50"))
    c.orden_archivo = 1                            # archivo mas reciente
    assert elegir_ganador([b, c]) is c             # empate -> ultimo archivo
