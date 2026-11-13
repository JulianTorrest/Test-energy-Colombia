"""Normalizacion de los formatos de consumo (Parte A / reglas B1-B4).

Los archivos llegan en formatos distintos a proposito: cambia el encoding,
el separador, los nombres de columna, el formato de timestamp y la unidad.
NADA se asume por nombre de archivo: todo se detecta por contenido, porque
el evaluador suelta meses nuevos con cualquiera de estos formatos.

Formatos observados:
  * base:      sep=',' cols frontera_id,timestamp,consumo_kwh,version_lectura,
               calidad_lectura  | ts 'YYYY-MM-DD HH:MM:SS' local Bogota
  * febrero:   sep=';' col timestamp_utc ISO '...Z' (UTC => B1) y decimal ','
  * marzo:     sep=',' cols frontera_id,timestamp,kwh,unidad,... | unidad
               kWh|MWh|Wh (=> B2), ts local
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

TZ_LOCAL = ZoneInfo("America/Bogota")          # UTC-5 fijo, sin DST (B1)
OUTLIER_KWH = Decimal("1000000")               # B4

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_UNIDADES = {"kwh": Decimal("1"), "mwh": Decimal("1000"),
            "wh": Decimal("0.001")}
_MAX_CELDA = 1_000_000  # csv.field_size_limit: cadenas larguisimas del maestro

_ALIASES_TS = ("timestamp", "timestamp_utc", "fecha_hora", "fecha",
               "ts", "datetime")
_ALIASES_VALOR = ("consumo_kwh", "kwh", "consumo", "valor", "consumo_wh",
                  "lectura_kwh")
_ALIASES_FRONTERA = ("frontera_id", "frontera", "frt", "codigo_frontera")
_ALIASES_VERSION = ("version_lectura", "version", "ver")
_ALIASES_CALIDAD = ("calidad_lectura", "calidad", "quality")
_ALIASES_UNIDAD = ("unidad", "unit", "unidades")

csv.field_size_limit(_MAX_CELDA)


@dataclass
class FilaLectura:
    """Una fila normalizada; estado decide si va a staging o a cuarentena."""
    fila_num: int
    frontera_id: str | None = None
    ts: datetime | None = None            # timestamptz (aware)
    consumo_kwh: Decimal | None = None    # siempre kWh
    version_lectura: int = 1
    calidad_lectura: str | None = None
    unidad_original: str | None = None
    estado: str = "ok"                    # ok | invalida | error
    motivo: str | None = None             # nulo|negativo|outlier|...
    raw: dict = field(default_factory=dict)


class ArchivoCorrupto(Exception):
    """El archivo no se puede procesar de forma segura (truncado, sin
    encabezado reconocible, etc.). El archivo entero va a cuarentena."""


def leer_bytes_texto(raw: bytes) -> tuple[str, str]:
    """Detecta encoding. Devuelve (texto, encoding_usado).

    Estrategia: probar en orden; cp1252/latin-1 nunca fallan asi que siempre
    hay fallback. utf-8 estricto primero para no reinterpretar UTF-8 real.
    """
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def detectar_delimitador(primera_linea: str) -> str:
    """Separador por conteo en encabezado (Sniffer falla con comillas
    anidadas de las entradas hostiles)."""
    candidatos = (";", ",", "\t", "|")
    mejor = max(candidatos, key=primera_linea.count)
    if primera_linea.count(mejor) == 0:
        raise ArchivoCorrupto("encabezado sin separador reconocible")
    return mejor


def _norm_header(h: str | None) -> str:
    if h is None:
        return ""
    return re.sub(r"\s+", "_", h.strip().lower())


def _mapear_columnas(headers: list[str]) -> dict[str, int]:
    """Mapea encabezados a campos logicos por alias. Lanza ArchivoCorrupto
    si faltan los obligatorios (frontera, timestamp, valor)."""
    idx = {_norm_header(h): i for i, h in enumerate(headers)}

    def buscar(aliases: tuple[str, ...]) -> int | None:
        for a in aliases:
            if a in idx:
                return idx[a]
        return None

    cols = {
        "frontera": buscar(_ALIASES_FRONTERA),
        "ts": buscar(_ALIASES_TS),
        "valor": buscar(_ALIASES_VALOR),
        "version": buscar(_ALIASES_VERSION),
        "calidad": buscar(_ALIASES_CALIDAD),
        "unidad": buscar(_ALIASES_UNIDAD),
    }
    faltan = [k for k in ("frontera", "ts", "valor") if cols[k] is None]
    if faltan:
        raise ArchivoCorrupto(
            f"encabezado sin columnas obligatorias: {faltan}")
    return cols  # type: ignore[return-value]


def parse_timestamp(texto: str | None) -> datetime | None:
    """Acepta ISO con offset/Z (se convierte a Bogota) o naive local.

    Devuelve datetime aware (timestamptz). None si no parsea.
    """
    if texto is None:
        return None
    t = texto.strip()
    if not t:
        return None
    candidatos = (t, t.replace("Z", "+00:00"))
    for c in candidatos:
        try:
            dt = datetime.fromisoformat(c)
        except ValueError:
            continue
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_LOCAL)      # naive = hora local (B1)
        return dt.astimezone(TZ_LOCAL)               # aware => convertir (B1)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=TZ_LOCAL)
        except ValueError:
            continue
    return None


def parse_decimal(texto: str | None) -> Decimal | None:
    """'163,795' | '163.795' | '1.234,56' | '1,234.56' -> Decimal.

    Regla: si hay punto y coma, el separador de la derecha es el decimal.
    Si solo hay coma, es decimal (los miles no usan coma en estos archivos).
    """
    if texto is None:
        return None
    t = texto.strip().replace(" ", "")
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):   # 1.234,56 europeo
            t = t.replace(".", "").replace(",", ".")
        else:                            # 1,234.56 anglo
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def normalizar_unidad(unidad: str | None) -> Decimal | None:
    """Factor a kWh. None si la unidad es desconocida; ausente => kWh."""
    if unidad is None or not unidad.strip():
        return Decimal("1")
    return _UNIDADES.get(unidad.strip().lower())


def _campo(row: list[str], i: int | None) -> str | None:
    if i is None or i >= len(row):
        return None
    return row[i]


def procesar_archivo(raw: bytes) -> tuple[list[FilaLectura], dict]:
    """Parsea un archivo de consumo completo.

    Devuelve (filas, meta). Nunca lanza por filas malas: cada fila queda con
    estado/motivo. Lanza ArchivoCorrupto solo si el archivo entero no se
    puede interpretar (encabezado irreconocible, CSV roto global).
    """
    meta: dict = {}
    texto, meta["encoding"] = leer_bytes_texto(raw)
    primera = texto.splitlines()[0] if texto.splitlines() else ""
    if not primera.strip():
        raise ArchivoCorrupto("archivo vacio o solo espacios")
    sep = detectar_delimitador(primera)
    meta["delimitador"] = repr(sep)

    rdr = csv.reader(io.StringIO(texto), delimiter=sep)
    try:
        headers = next(rdr)
    except StopIteration:
        raise ArchivoCorrupto("sin encabezado")
    cols = _mapear_columnas(headers)
    meta["columnas"] = headers

    filas: list[FilaLectura] = []
    try:
        for fila_num, row in enumerate(rdr, start=2):  # fila 1 = header
            if not row or all(not (c or "").strip() for c in row):
                continue
            filas.append(_normalizar_fila(fila_num, row, cols, headers))
    except csv.Error as e:
        # CSV roto a nivel estructural: las filas ya parseadas se conservan
        # pero el archivo se marca para revision (el orquestador decide si lo
        # rechaza entero; aqui lo señalizamos en meta).
        meta["csv_error"] = str(e)
    return filas, meta


def _normalizar_fila(fila_num: int, row: list[str], cols: dict,
                     headers: list[str]) -> FilaLectura:
    f = FilaLectura(fila_num=fila_num,
                    raw={_norm_header(headers[i]): row[i]
                         for i in range(min(len(row), len(headers)))})
    f.frontera_id = (_campo(row, cols["frontera"]) or "").strip() or None
    f.version_lectura = _parse_version(_campo(row, cols["version"]))
    f.calidad_lectura = (_campo(row, cols["calidad"]) or "").strip() or None
    f.unidad_original = (_campo(row, cols["unidad"]) or "").strip() or None

    ts_txt = _campo(row, cols["ts"])
    f.ts = parse_timestamp(ts_txt)

    valor_txt = _campo(row, cols["valor"])
    valor = parse_decimal(valor_txt)

    factor = normalizar_unidad(f.unidad_original)
    if factor is None:
        f.estado, f.motivo = "invalida", "unidad_desconocida"
        return f
    if f.frontera_id is None:
        f.estado, f.motivo = "invalida", "frontera_vacia"
        return f
    if f.ts is None:
        f.estado, f.motivo = "invalida", "timestamp_invalido"
        return f
    # B4: nulos/vacios/no-numericos, negativos, outlier > 1.000.000 kWh
    if valor is None:
        f.estado, f.motivo = "invalida", ("valor_nulo" if not (valor_txt or "")
                                          .strip() else "valor_no_numerico")
        return f
    f.consumo_kwh = valor * factor
    if f.consumo_kwh < 0:
        f.estado, f.motivo = "invalida", "valor_negativo"
        return f
    if f.consumo_kwh > OUTLIER_KWH:
        f.estado, f.motivo = "invalida", "outlier"
        return f
    return f


def _parse_version(texto: str | None) -> int:
    try:
        return int(str(texto).strip())
    except (TypeError, ValueError):
        return 1


def clave_dedup(f: FilaLectura):
    """Orden B3 para elegir ganador: mayor version; empate => mayor orden de
    archivo (ultimo en orden lexicografico); empate => ultima fila del
    archivo."""
    return (f.version_lectura, getattr(f, "orden_archivo", 0), f.fila_num)


def elegir_ganador(filas: list[FilaLectura]) -> FilaLectura:
    return max(filas, key=clave_dedup)


def mes_de_archivo(nombre: str) -> str | None:
    """'consumos_2026-03.csv' -> '2026-03' (YYYY-MM)."""
    m = re.search(r"(20\d{2})-(\d{2})", nombre)
    return f"{m.group(1)}-{m.group(2)}" if m else None
