"""Consumidor del topico mediciones.raw (make stream, Parte D).

Flujo: asegura que el topico tenga datos (corre tools/producer.py si esta
vacio), consume desde el inicio, evalua las tres reglas y escribe
out/alertas.json. TERMINA SOLO: sale cuando agota el fin del topico tras
un tiempo ocioso configurable — nunca se queda escuchando para siempre.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from kafka import KafkaConsumer, TopicPartition

from . import alerts, config
from .logging_setup import get_logger

_IDLE_S = 8.0        # segundos sin mensajes tras el 1ro => se acabo el stream
_MAX_S = 240.0       # tope duro de seguridad (< timeout 300 s del evaluador)
_POLL_MS = 1000


def _limites() -> dict[str, float]:
    """max_kwh por frontera desde el maestro (mismo origen que el batch)."""
    ruta = config.DATA_DIR / "maestros" / "limites_frontera.csv"
    out: dict[str, float] = {}
    with open(ruta, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r["frontera_id"].strip()] = float(r["max_kwh"])
            except (KeyError, ValueError):
                continue
    return out


def _topic_tiene_datos(bootstrap: str, topic: str) -> bool:
    c = KafkaConsumer(bootstrap_servers=bootstrap,
                      consumer_timeout_ms=3000,
                      enable_auto_commit=False)
    try:
        partes = c.partitions_for_topic(topic)
        if not partes:
            return False
        tps = [TopicPartition(topic, p) for p in partes]
        c.assign(tps)
        fin = c.end_offsets(tps)
        return any(o > 0 for o in fin.values())
    finally:
        c.close()


def _alimentar(bootstrap: str, topic: str, log) -> None:
    producer = config.ROOT / "tools" / "producer.py"
    log.info("topico vacio: corriendo producer", extra={"extra": {
        "topic": topic}})
    subprocess.run([sys.executable, str(producer), "--topic", topic,
                    "--bootstrap", bootstrap, "--rate", "0"],
                   check=True, timeout=180)


def _consumir(bootstrap: str, topic: str, log) -> list[dict]:
    c = KafkaConsumer(bootstrap_servers=bootstrap,
                      enable_auto_commit=False,
                      consumer_timeout_ms=_POLL_MS * 5,
                      value_deserializer=lambda b: json.loads(
                          b.decode("utf-8")))
    try:
        partes = c.partitions_for_topic(topic)
        if not partes:
            return []
        tps = [TopicPartition(topic, p) for p in partes]
        c.assign(tps)
        c.seek_to_beginning(*tps)
        eventos: list[dict] = []
        t_ini = time.monotonic()
        ultimo_msg = None
        while True:
            msg = c.poll(timeout_ms=_POLL_MS)
            ahora = time.monotonic()
            if msg:
                ultimo_msg = ahora
                for recs in msg.values():
                    for r in recs:
                        if isinstance(r.value, dict):
                            eventos.append(r.value)
                continue
            # Sin mensajes: ¿fin del stream?
            if eventos and ultimo_msg and ahora - ultimo_msg >= _IDLE_S:
                break
            if not eventos and ahora - t_ini >= _IDLE_S:
                break
            if ahora - t_ini >= _MAX_S:
                log.warning("tope de tiempo alcanzado en consumo",
                            extra={"extra": {"max_s": _MAX_S}})
                break
        log.info("consumo finalizado", extra={"extra": {
            "eventos": len(eventos)}})
        return eventos
    finally:
        c.close()


def main() -> int:
    config.cargar_env()
    config.OUT_DIR.mkdir(exist_ok=True)
    config.LOG_DIR.mkdir(exist_ok=True)
    log = get_logger("stream", config.LOG_DIR / "pipeline.log")

    bootstrap = config.env("KAFKA_BOOTSTRAP", "localhost:9092")
    topic = config.env("KAFKA_TOPIC", "mediciones.raw")

    # Esperar a que el broker responda (redpanda tarda en levantar).
    t_ini = time.monotonic()
    while True:
        try:
            if _topic_tiene_datos(bootstrap, topic):
                break
            _alimentar(bootstrap, topic, log)
            break
        except Exception as e:  # noqa: BLE001
            if time.monotonic() - t_ini > 120:
                log.error("broker inalcanzable", extra={"extra": {
                    "error": str(e)[:200]}})
                return 1
            time.sleep(3)

    eventos = _consumir(bootstrap, topic, log)
    max_kwh = _limites()
    fronteras = sorted(set(max_kwh) |
                       {e.get("frontera_id") for e in eventos
                        if e.get("frontera_id")})
    salida = alerts.consolidar(eventos, max_kwh, fronteras)

    ruta = config.OUT_DIR / "alertas.json"
    doc = [{"tipo": a.tipo, "frontera_id": a.frontera_id,
            "ts": a.ts.isoformat()} for a in salida]
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    log.info("alertas escritas", extra={"extra": {
        "alertas": len(doc), "eventos": len(eventos),
        "por_tipo": {t: sum(1 for a in doc if a["tipo"] == t)
                     for t in {a["tipo"] for a in doc}}}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
