"""Deteccion y enmascaramiento de PII en texto libre (C2).

El campo `observaciones` del maestro de clientes trae correos, celulares y
cedulas incrustados. Se enmascaran in-place manteniendo el resto del texto.

Orden de los patrones (el primero que matchea gana sobre el span):
  1. email          RFC-lite
  2. celular        3XXXXXXXXX (movil Colombia, 10 digitos)
  3. nit/cedula     NIT 846.661.846-9, CC 1.234.567.890, o 6-10 digitos con
                    o sin puntuacion de miles / guion de verificacion
"""
from __future__ import annotations

import re

_RE_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_CELULAR = re.compile(r"(?<!\d)3\d{9}(?!\d)")
_RE_NIT_PUNT = re.compile(
    r"(?<!\d)(?:NIT|N\.?I\.?T\.?|C\.?C\.?|CC)?\s*"
    r"\d{1,3}(?:\.\d{3}){1,3}(?:-\d)?(?!\d)", re.IGNORECASE)
_RE_CEDULA = re.compile(r"(?<![\d.,])\d{6,10}(?:-\d)?(?![\d.,])")

_PATRONES = [
    ("email", _RE_EMAIL),
    ("celular", _RE_CELULAR),
    ("nit_cedula", _RE_NIT_PUNT),
    ("nit_cedula", _RE_CEDULA),
]

_MASCARAS = {
    "email": "[email]",
    "celular": "[celular]",
    "nit_cedula": "[nit/cedula]",
}


def _spans(texto: str) -> list[tuple[int, int, str]]:
    """Spans no solapados de PII, resueltos en orden de patron."""
    ocupado = [False] * len(texto)
    hallados: list[tuple[int, int, str]] = []
    for tipo, patron in _PATRONES:
        for m in patron.finditer(texto):
            a, b = m.span()
            if a == b or any(ocupado[a:b]):
                continue
            for i in range(a, b):
                ocupado[i] = True
            hallados.append((a, b, tipo))
    return hallados


def enmascarar(texto: str | None) -> tuple[str | None, int]:
    """Devuelve (texto_enmascarado, num_hallazgos). None -> (None, 0)."""
    if texto is None:
        return None, 0
    hallados = _spans(texto)
    if not hallados:
        return texto, 0
    partes, pos = [], 0
    for a, b, tipo in sorted(hallados):
        partes.append(texto[pos:a])
        partes.append(_MASCARAS[tipo])
        pos = b
    partes.append(texto[pos:])
    return "".join(partes), len(hallados)


def contiene_pii(texto: str | None) -> bool:
    return texto is not None and bool(_spans(texto))
