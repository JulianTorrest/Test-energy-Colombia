"""Tests de enmascaramiento PII (C2) y cifrado AES-GCM + indice ciego (C3)."""
from __future__ import annotations

import os

from src import crypto, pii

KEY = os.urandom(32)


def test_enmascara_email_celular_cedula():
    txt = "Contacto: ana@mail.com tel 3001234567 c.c. 1234567890"
    out, n = pii.enmascarar(txt)
    assert "ana@mail.com" not in out
    assert "3001234567" not in out
    assert "1234567890" not in out
    assert n == 3


def test_enmascara_nit_puntuado():
    out, n = pii.enmascarar("NIT 846.661.846-9")
    assert "846.661.846-9" not in out
    assert n == 1


def test_sin_pii_no_toca():
    txt = "Cliente con contrato vigente. Sin novedades."
    out, n = pii.enmascarar(txt)
    assert out == txt and n == 0
    assert pii.contiene_pii(txt) is False
    assert pii.enmascarar(None) == (None, 0)


def test_cifrado_roundtrip():
    ct = crypto.cifrar("867.446.522-3", KEY)
    assert ct is not None and isinstance(ct, bytes)
    assert b"867" not in ct                    # no queda nada en claro
    assert crypto.descifrar(ct, KEY) == "867.446.522-3"


def test_nonce_aleatorio():
    a = crypto.cifrar("mismo", KEY)
    b = crypto.cifrar("mismo", KEY)
    assert a != b                               # mismo claro, distinto ct


def test_indice_ciego_estable_y_no_reversible():
    h1 = crypto.indice_ciego(" 867.446.522-3 ", KEY)
    h2 = crypto.indice_ciego("867.446.522-3", KEY)
    assert h1 == h2 and len(h1) == 64           # join por hash
    assert "867" not in h1


def test_none_no_explota():
    assert crypto.cifrar(None, KEY) is None
    assert crypto.descifrar(None, KEY) is None
    assert crypto.indice_ciego(None, KEY) is None
