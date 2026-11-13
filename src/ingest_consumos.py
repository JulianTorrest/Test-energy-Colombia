"""Ingesta incremental de consumos (Parte A).

- Descubrimiento: glob de data/consumos/*.csv, orden lexicografico (B3).
  No hay lista fija de meses: lo que aparezca se procesa con el parser
  generico (formato detectado por contenido).
- Idempotencia: stage.control_archivos guarda sha256+estado. Un archivo ya
  procesado e identico se omite; si cambio de contenido se re-procesa (sus
  filas viejas se reemplazan en staging); si esta corrupto queda 'rechazado'
  y NO se reintenta (los meses ya cargados no cambian).
- Dedup B3: al final se reconstruye core.fact_consumo_horario desde
  stage.consumo_lectura con ROW_NUMBER (version DESC, orden_archivo DESC,
  fila_num DESC). Las correcciones tardias (*_correccion.csv) entran como
  un archivo mas y ganan por version => el fact se actualiza sin releer nada.
- Transaccionalidad por archivo: un archivo falla => rollback de ESE archivo
  solamente.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from . import config, parse
from .parse import ArchivoCorrupto, FilaLectura

_INS_STAGE = """INSERT INTO stage.consumo_lectura
    (archivo, orden_archivo, fila_num, frontera_id, ts, consumo_kwh,
     version_lectura, calidad_lectura, unidad_original)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


@dataclass
class ResultadoConsumos:
    archivos: list[str] = field(default_factory=list)
    procesados: int = 0
    omitidos: int = 0
    rechazados: int = 0
    leidas: int = 0
    cargadas_staging: int = 0
    invalidas: int = 0
    meses: set[str] = field(default_factory=set)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _estado_previo(cur, archivo: str) -> tuple[str | None, str | None]:
    cur.execute("SELECT sha256, estado FROM stage.control_archivos "
                "WHERE archivo=%s", (archivo,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _a_cuarentena(cur, corrida_id, fuente, fila_num, motivo, fila: FilaLectura):
    cur.execute(
        """INSERT INTO stage.cuarentena
             (fuente, fila_num, motivo, payload, payload_cifrado, corrida_id)
           VALUES (%s,%s,%s,%s,false,%s)""",
        (fuente, fila_num, motivo,
         json.dumps(fila.raw, ensure_ascii=False, default=str),
         str(corrida_id)))


def _registrar_archivo(cur, corrida_id, archivo, sha, leidas, ok, rech,
                       estado, motivo=None):
    cur.execute(
        """INSERT INTO stage.control_archivos
             (archivo, sha256, filas_leidas, filas_cargadas, filas_rechazadas,
              estado, motivo, corrida_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (archivo) DO UPDATE SET
             sha256=EXCLUDED.sha256, filas_leidas=EXCLUDED.filas_leidas,
             filas_cargadas=EXCLUDED.filas_cargadas,
             filas_rechazadas=EXCLUDED.filas_rechazadas,
             estado=EXCLUDED.estado, motivo=EXCLUDED.motivo,
             procesado_en=now(), corrida_id=EXCLUDED.corrida_id""",
        (archivo, sha, leidas, ok, rech, estado, motivo, str(corrida_id)))


def procesar_un_archivo(cur, corrida_id, ruta, orden_archivo, key, log,
                        res: ResultadoConsumos) -> None:
    nombre = ruta.name
    raw = ruta.read_bytes()
    sha = _sha256(raw)
    sha_prev, estado_prev = _estado_previo(cur, nombre)

    if sha_prev == sha and estado_prev == "procesado":
        res.omitidos += 1
        log.info("archivo sin cambios, se omite",
                 extra={"extra": {"archivo": nombre}})
        return
    if sha_prev == sha and estado_prev == "rechazado":
        res.rechazados += 1
        log.info("archivo ya rechazado antes, no se reintenta",
                 extra={"extra": {"archivo": nombre}})
        return

    # Contenido nuevo o cambiado: si ya tenia staging/rechazos, se
    # reemplazan enteros (la cuarentena refleja estado actual; la
    # historia de corridas esta en audit.bitacora).
    if sha_prev is not None:
        cur.execute("DELETE FROM stage.consumo_lectura WHERE archivo=%s",
                    (nombre,))
    cur.execute("DELETE FROM stage.cuarentena WHERE fuente=%s",
                (f"consumos/{nombre}",))

    try:
        filas, meta = parse.procesar_archivo(raw)
    except ArchivoCorrupto as e:
        res.rechazados += 1
        _registrar_archivo(cur, corrida_id, nombre, sha, 0, 0, 0,
                           "rechazado", f"archivo_corrupto:{e}")
        cur.execute(
            """INSERT INTO stage.cuarentena
                 (fuente, motivo, payload, payload_cifrado, corrida_id)
               VALUES (%s,%s,%s,false,%s)""",
            (f"consumos/{nombre}", "archivo_corrupto",
             json.dumps({"error": str(e)[:500], "bytes": len(raw)}),
             str(corrida_id)))
        log.warning("archivo corrupto a cuarentena",
                    extra={"extra": {"archivo": nombre, "motivo": str(e)[:200]}})
        return

    # Si hubo un csv.Error global, el archivo va rechazado entero (mas seguro
    # que cargar parcial un archivo truncado/corrupto).
    if "csv_error" in meta:
        res.rechazados += 1
        _registrar_archivo(cur, corrida_id, nombre, sha, len(filas), 0,
                           len(filas), "rechazado",
                           f"csv_error:{meta['csv_error'][:200]}")
        cur.execute(
            """INSERT INTO stage.cuarentena
                 (fuente, motivo, payload, payload_cifrado, corrida_id)
               VALUES (%s,%s,%s,false,%s)""",
            (f"consumos/{nombre}", "archivo_corrupto",
             json.dumps({"error": meta["csv_error"], "bytes": len(raw)}),
             str(corrida_id)))
        log.warning("archivo con csv_error a cuarentena",
                    extra={"extra": {"archivo": nombre,
                                     "error": meta["csv_error"][:200]}})
        return

    leidas = ok = rech = 0
    for f in filas:
        leidas += 1
        if f.estado == "ok":
            cur.execute(_INS_STAGE,
                        (nombre, orden_archivo, f.fila_num, f.frontera_id,
                         f.ts, f.consumo_kwh, f.version_lectura,
                         f.calidad_lectura, f.unidad_original))
            ok += 1
        else:
            rech += 1
            _a_cuarentena(cur, corrida_id, f"consumos/{nombre}", f.fila_num,
                          f.motivo, f)
    _registrar_archivo(cur, corrida_id, nombre, sha, leidas, ok, rech,
                       "procesado")
    res.procesados += 1
    res.leidas += leidas
    res.cargadas_staging += ok
    res.invalidas += rech
    mes = parse.mes_de_archivo(nombre)
    if mes:
        res.meses.add(mes)
    log.info("archivo procesado", extra={"extra": {
        "archivo": nombre, "encoding": meta.get("encoding"),
        "delimitador": meta.get("delimitador"), "leidas": leidas,
        "ok": ok, "rechazadas": rech}})


def reconstruir_fact(cur) -> dict:
    """Reconstruye fact_consumo_horario desde staging aplicando B3.

    Devuelve conteos de duplicados para dq_report:
      exactos       = perdedores identicos al ganador en (version, kwh, calidad)
      conflictivos  = perdedores que difieren
      correcciones  = ganadores que provienen de archivos *correccion*
    En una sola transaccion: DELETE + INSERT del ranking (atomico).
    (rol_ingesta no tiene TRUNCATE — ni lo necesita: DELETE FROM basta
    con el grant DELETE ya otorgado, manteniendo menor privilegio.)
    """
    cur.execute("DELETE FROM core.fact_consumo_horario")
    cur.execute(
        """WITH ranked AS (
             SELECT *, ROW_NUMBER() OVER (
                 PARTITION BY frontera_id, ts
                 ORDER BY version_lectura DESC, orden_archivo DESC,
                          fila_num DESC) AS rn
             FROM stage.consumo_lectura)
           INSERT INTO core.fact_consumo_horario
             (frontera_id, ts, consumo_kwh, version_lectura,
              calidad_lectura, archivo_origen)
           SELECT frontera_id, ts, consumo_kwh, version_lectura,
                  calidad_lectura, archivo
           FROM ranked WHERE rn = 1""")

    cur.execute(
        """WITH ranked AS (
             SELECT *, ROW_NUMBER() OVER (
                 PARTITION BY frontera_id, ts
                 ORDER BY version_lectura DESC, orden_archivo DESC,
                          fila_num DESC) AS rn
             FROM stage.consumo_lectura),
           w AS (SELECT * FROM ranked WHERE rn = 1),
           l AS (SELECT r.* FROM ranked r WHERE r.rn > 1)
           SELECT
             count(*) FILTER (
               WHERE l.version_lectura = w.version_lectura
                 AND l.consumo_kwh     = w.consumo_kwh
                 AND l.calidad_lectura IS NOT DISTINCT FROM w.calidad_lectura
             ) AS exactos,
             count(*) FILTER (
               WHERE NOT (l.version_lectura = w.version_lectura
                 AND l.consumo_kwh     = w.consumo_kwh
                 AND l.calidad_lectura IS NOT DISTINCT FROM w.calidad_lectura)
             ) AS conflictivos
           FROM l JOIN w USING (frontera_id, ts)""")
    exactos, conflictivos = cur.fetchone()

    cur.execute(
        """SELECT count(*) FROM core.fact_consumo_horario
           WHERE archivo_origen ILIKE '%correccion%'""")
    correcciones = cur.fetchone()[0]
    return {"duplicados_exactos": exactos or 0,
            "duplicados_conflictivos": conflictivos or 0,
            "correcciones_tardias_aplicadas": correcciones or 0}


def cargar_consumos(cur, corrida_id: uuid.UUID, key: bytes,
                    log) -> ResultadoConsumos:
    res = ResultadoConsumos()
    ruta_dir = config.DATA_DIR / "consumos"
    archivos = sorted(p for p in ruta_dir.glob("*.csv") if p.is_file())
    for orden, ruta in enumerate(archivos):
        res.archivos.append(ruta.name)
        # SAVEPOINT por archivo: un fallo a mitad del archivo (insert de
        # staging, cuarentena, control) revierte solo esa unidad y deja la
        # transaccion valida para registrar el rechazo y seguir con el
        # siguiente archivo (evita InFailedSqlTransaction).
        cur.execute("SAVEPOINT sp_archivo")
        try:
            procesar_un_archivo(cur, corrida_id, ruta, orden, key, log, res)
            cur.execute("RELEASE SAVEPOINT sp_archivo")
        except Exception as e:  # noqa: BLE001 - un archivo no tumba el resto
            cur.execute("ROLLBACK TO SAVEPOINT sp_archivo")
            res.rechazados += 1
            log.error("fallo inesperado procesando archivo",
                      extra={"extra": {"archivo": ruta.name,
                                       "error": str(e)[:300]}},
                      exc_info=True)
            _registrar_archivo(cur, corrida_id, ruta.name,
                               _sha256(ruta.read_bytes()), 0, 0, 0,
                               "rechazado", f"error_interno:{e}"[:500])
    return res
