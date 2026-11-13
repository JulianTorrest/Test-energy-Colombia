# Modelo de amenazas — Pipeline Bia Data Platform

## 1. Alcance y diagrama de flujo de datos

```
[data/ maestros+consumos+facturacion]   (hostil: B7, PII en claro)
            |                                  ^
            v (archivos del FS)                |
   +----------------------+                    |
   | pipeline (rol_ingesta)|--- cuarentena --->| stage.cuarentena
   |  parse/mask/encrypt   |                   | (payload cifrado)
   +----------+-----------+
              | INSERT/UPDATE                        ┌─ FRONTERA DE CONFIANZA
              v                                      │  1: datos no confiables
   +---------------------+                           │  2: credenciales/llave
   | Postgres            |  PII cifrada AES-GCM      │  3: roles por necesidad
   | core.* stage.*      |<---rol_analista (vista) ──┘
   | audit.bitacora      |<---rol_auditor (solo audit)
   +---------------------+
              ^
   Kafka (redpanda) -> src/stream_runner -> out/alertas.json
```

Activos: PII de clientes, datos de consumo/facturación, credenciales de
roles, llave de cifrado, integridad del modelo, bitácora.

## 2. Activos y clasificación

| Activo | Dónde vive | Clasificación | Impacto si se compromete |
|---|---|---|---|
| PII cliente (nit/email/cel/dirección) | `dim_cliente.*_cifrado`, `.env` | Restringido-PII | Ley 1581, demanda |
| Consumo/facturación | `core.fact_*` | Confidencial | Factura mal, cliente se va |
| Llave `PII_ENCRYPTION_KEY` | `.env` | Restringido-PII | Todo lo cifrado se expone |
| Credenciales roles | `.env`, `entrega.yaml` | Confidencial | Acceso a la base |
| Bitácora | `audit.bitacora` | Interno | Sin trazabilidad = sin defensa |

## 3. Amenazas (STRIDE)

| # | Componente | STRIDE | Amenaza concreta | Probabilidad | Impacto | Control implementado | ¿Implementado? | ISO 27001:2022 |
|---|---|---|---|---|---|---|---|---|
| T1 | Ingesta maestros | Tampering | Fila hostil de `clientes.csv` inyecta SQL para borrar tablas (B7, eliminatorio) | M | A | Solo queries parametrizadas (psycopg2), csv.reader nunca ejecuta | Sí | A.8.26 app security |
| T2 | PII en reposo | Info disclosure | Robo de dump/acceso DBA lee nit/email/celular | A | A | AES-256-GCM por columna, llave fuera de la base; superusuario ve binario | Sí | A.8.24 crypto |
| T3 | Join por NIT | Info disclosure | Necesidad de unir factura↔cliente obliga a guardar NIT en claro | M | A | Índice ciego HMAC-SHA256; join sin exponer | Sí | A.8.24, A.8.11 masking |
| T4 | Roles DB | Elevation of privilege | `analista` lee tabla base o PII; `ingesta` lee lo cifrado | M | A | Grants mínimos + revoke a nivel columna + vista SECURITY DEFINER | Sí | A.8.3 access ctrl |
| T5 | `observaciones` | Info disclosure | PII embebida en texto libre llega a la vista del analista | A | A | `src/pii.py` enmascara antes de persistir; vista no trae columna cruda | Sí | A.8.11 masking |
| T6 | Logs | Info disclosure | `logs/pipeline.log` registra email/celular (eliminatorio) | M | A | Logger JSON solo con ids/conteos; PII nunca se loguea; grep-able | Sí | A.8.15 logging |
| T7 | Bitácora | Repudiation/Tampering | Se edita/borra una corrida para ocultar carga mala | B | M | REVOKE + trigger BEFORE UPDATE/DELETE = append-only | Sí | A.8.15, A.8.16 |
| T8 | Secretos en repo | Info disclosure | `.env`/`entrega.yaml` con passwords versionados | M | A | `.env` gitignored y aleatorio; `entrega.yaml` lo genera init; scaffolding saneado (C6) | Sí | A.8.9 config mgmt |
| T9 | Ingesta archivo | DoS/Tampering | Archivo corrupto/truncado corrompe meses ya cargados | M | A | Transacción por archivo + `control_archivos` sha256 + cuarentena | Sí | A.8.29 secure dev |
| T10 | Dependencias | Elevation | requests/urllib3/Jinja2/PyYAML viejos con CVEs en el entorno | M | M | `requirements.txt` saneado y fijado | Sí | A.8.8 vuln mgmt |
| T11 | Stream | DoS | Consumidor no termina → timeout del evaluador / recurso colgado | M | B | Idle-timeout + tope duro `_MAX_S` < 300s | Sí | A.8.20 networks |
| T12 | Cuarentena | Info disclosure | Payload crudo con PII queda consultable en claro | M | M | `payload_cifrado` para fuentes con PII | Sí | A.8.24 |

## 4. Riesgos aceptados

- **Llave en `.env` local** (no KMS real): es evaluación local; en AWS el
  espejo es la CMK de `infra/`. La llave nunca viaja a la base ni a git.
- **`analista` ve `razon_social`/`ciudad`** en la vista: son datos de
  empresa, clasificados Confidencial, no PII — necesarios para análisis.
- **MinIO sin endurecer a fondo**: no es parte del flujo de datos; se deja
  como pieza de infraestructura, sin secretos en el compose.
- **Trigger de bitácora dropeable por superusuario**: defensa en
  profundidad razonable; un superusuario malicioso siempre puede dropear
  triggers — eso ya es compromiso de la base.

## 5. Qué haría distinto con más tiempo / en producción real

- Llave en AWS KMS/Secrets Manager con rotación, no en `.env`.
- RLS (row-level security) además de grants por columna.
- TLS en Postgres/Kafka y SASL en Redpanda.
- Escaneo de dependencias en CI (`pip-audit`) y de secretos (`gitleaks`).
- MinIO con políticas por bucket y cifrado SSE.
