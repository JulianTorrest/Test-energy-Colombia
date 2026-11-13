"""Cifrado en reposo a nivel de aplicacion (C3).

- AES-256-GCM: confidencialidad + integridad; nonce aleatorio por valor,
  formato bytea = nonce(12B) || ciphertext||tag. Incluso leyendo la tabla
  cruda como superusuario solo se ve cifrado.
- Indice ciego HMAC-SHA256: permite JOIN por NIT sin exponerlo ni usar el
  valor en claro (fact_factura.cliente_nit_hash = dim_cliente.nit_hash).

La llave viene de PII_ENCRYPTION_KEY (.env). Nunca se persiste ni se loguea.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12


def cifrar(valor: str | None, key: bytes) -> bytes | None:
    """str -> bytea (nonce || AES-256-GCM(ct)). None -> None."""
    if valor is None or valor == "":
        return None
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, valor.encode("utf-8"), None)
    return nonce + ct


def descifrar(dato: bytes | None, key: bytes) -> str | None:
    """bytea -> str. None -> None. Solo para uso interno autorizado."""
    if dato is None:
        return None
    dato = bytes(dato)
    nonce, ct = dato[:_NONCE_LEN], dato[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def indice_ciego(valor: str | None, key: bytes) -> str | None:
    """HMAC-SHA256 hex del valor normalizado (trim+lower): join sin exponer."""
    if valor is None or valor == "":
        return None
    normalizado = valor.strip().lower().encode("utf-8")
    return hmac.new(key, normalizado, hashlib.sha256).hexdigest()
