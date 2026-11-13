# Decisiones de arquitectura (ADRs)

---

## ADR-001 — Modelo: staging inmutable + fact reconstruido, no MERGE incremental

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
Las correcciones tardías (`*_correccion.csv`) deben reemplazar lecturas ya
cargadas sin recargar todo, y el dedup B3 depende del orden lexicográfico de
archivo. Un MERGE incremental fila-a-fila es frágil ante re-procesos.

**Opciones consideradas**
1. `INSERT ... ON CONFLICT` por fila con lógica de versión en Python.
2. Staging append-only con **todas** las lecturas válidas + fact reconstruido
   con `ROW_NUMBER()` en cada corrida.
3. Tabla de snapshots por mes.

**Decisión**
Opción 2: `stage.consumo_lectura` guarda cada lectura válida con su
`orden_archivo` y `fila_num`; `core.fact_consumo_horario` se reconstruye con
`TRUNCATE + INSERT` del ranking `ORDER BY version_lectura DESC,
orden_archivo DESC, fila_num DESC`.

**Por qué**
Hace la corrección tardía trivial (es un archivo más, gana por versión), la
idempotencia gratis (mismo staging → mismo fact) y el dedup correcto sin
lógica de actualización. Resigno tiempo de cómputo: se recalcula el ranking
completo por corrida.

**Consecuencias**
Re-correr es O(total de lecturas) pero los archivos fuente no se releen
(sha256 en `control_archivos` decide omitir). Simple y auditable.

---

## ADR-002 — Cifrado a nivel de aplicación (AES-256-GCM) + índice ciego HMAC, no pgcrypto

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
C3 exige que leer la tabla cruda como superusuario no exponga PII, y aún así
hay que unir factura↔cliente por NIT.

**Opciones consideradas**
1. pgcrypto (`pgp_sym_encrypt`) — la llave viaja en cada query (queda en
   logs/`pg_stat_statements`) o vive en la base.
2. Cifrado en la app con llave de entorno + hash determinista para joins.

**Decisión**
Opción 2: AES-256-GCM en `src/crypto.py`, llave en `.env`
(`PII_ENCRYPTION_KEY`), `bytea = nonce‖ct‖tag`. `nit_hash` = HMAC-SHA256
para joins sin exponer.

**Por qué**
La llave nunca está en la base ni en queries: un dump es inútil. GCM da
integridad además de confidencialidad. Resigno no poder indexar/buscar por
el valor cifrado (por eso el índice ciego).

**Consecuencias**
El rol `ingesta` solo puede escribir `*_cifrado` pero no leerlos (revoke a
nivel columna). Rotar la llave exige re-cifrar en batch.

---

## ADR-003 — Cuarentena como tabla consultable, payload cifrado según la fuente

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
Lo rechazado no se borra y debe ser consultable, pero un payload crudo de
`clientes.csv` o de facturación trae PII en claro.

**Opciones consideradas**
1. Guardar el payload siempre en claro.
2. Guardar siempre cifrado.
3. Cifrar solo cuando la fuente puede traer PII.

**Decisión**
Opción 3: `stage.cuarentena` con `payload` (texto o cifrado) +
`payload_cifrado` booleano. Fuentes sin PII (consumos, maestros
fronteras/limites) quedan legibles para operar; clientes/facturación cifran.

**Por qué**
No expone PII en cuarentena pero mantiene operable el 95% de los rechazos
(lecturas de consumo, que no son PII). Resigno una columna extra y lógica
condicional.

**Consecuencias**
`rol_ingesta` puede leer cuarentena sin exponer PII. Auditar rechazos de
facturación exige la llave (fuera de la base).

---

## ADR-004 — Dedup B3 implementado en SQL (window), espejado en Python para tests

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
B3 es exacta: gana mayor `version_lectura`, empate → último en orden
lexicográfico de archivo, empate → última fila. Debe ser verificable.

**Opciones consideradas**
1. Dedup en Python al insertar (caro, propenso a race).
2. Un solo `INSERT ... SELECT` con `ROW_NUMBER() OVER (PARTITION BY
   frontera_id, ts ORDER BY ...)`.

**Decisión**
Opción 2 en `reconstruir_fact`. La misma regla se replica como función pura
`elegir_ganador` en `src/parse.py` para testear sin Docker.

**Por qué**
El motor resuelve el ranking de forma atómica y declarativa; el test cubre
la semántica. Resigno duplicar la regla en dos lenguajes (mitigado: el test
fija el comportamiento).

**Consecuencias**
`duplicados_exactos`/`conflictivos` se calculan en el mismo paso comparando
perdedores contra el ganador (`IS NOT DISTINCT FROM` para nulos).

---

## ADR-005 — `make stream` auto-suficiente: alimenta el tópico si está vacío y termina por idle-timeout

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
El evaluador corre `make stream` solo (timeout 300 s). El consumidor no
puede quedarse escuchando ni puede asumir que el tópico ya tiene datos.

**Opciones consideradas**
1. Asumir que el evaluador corre `producer.py` antes (frágil).
2. `make stream` detecta si el tópico está vacío → corre `producer.py`,
   consume hasta el fin y sale por inactividad con un tope duro de tiempo.

**Decisión**
Opción 2: `src/stream_runner.py` usa `assign()+seek_to_beginning` (sin
consumer-group, determinista), termina tras `_IDLE_S` sin mensajes, con
`_MAX_S` < 300 s como seguro.

**Por qué**
`make stream` funciona en cualquier estado del tópico y nunca se cuelga.
Resigno depender de un idle-timeout (riesgo de cortar un stream real lento,
aceptable en evaluación).

**Consecuencias**
Las alertas se deduplican por (tipo, frontera, ts) así un re-produce no
duplica `out/alertas.json`.
