# Plataforma de datos — Bia Energy (prueba técnica)

Pipeline batch + streaming que toma archivos crudos de `data/` y produce
información confiable y custodiada en Postgres, con PII cifrada en reposo,
menor privilegio y trazabilidad append-only.

## Cómo correrlo (contrato de ejecución)

```bash
make setup      # instala dependencias (requirements.txt)
make up         # .env aleatorio + docker compose + DDL/roles/vista
make run        # pipeline batch -> out/*.csv|json, logs/pipeline.log
make run        # 2da corrida = mismo estado (idempotente)
make stream     # consumidor Kafka -> out/alertas.json (termina solo)
make test       # pytest tests/
make down
```

`make up` genera `.env` con credenciales aleatorias, levanta Postgres +
MinIO + Redpanda, espera a PG, aplica `sql/*.sql` y publica las credenciales
en `entrega.yaml` (para `make verificar`).

**Si no tienes `make` (Windows sin make):** los targets son 1:1 con
comandos — `python3 tools/ensure_env.py`, `docker compose --env-file .env
up -d`, `python3 tools/init_db.py`, `python3 -m src.pipeline`,
`python3 -m src.stream_runner`, `python3 -m pytest tests/ -q`.

## Qué hice

- **A — Ingesta batch:** descubrimiento por glob (no lista fija), parser por
  contenido (encoding/delimitador/columnas/timestamp/unidad), staging
  inmutable + fact reconstruido por ranking → correcciones tardías e
  idempotencia reales. Transacción por archivo; los corruptos van a
  cuarentena sin tocar meses ya cargados.
- **B — Calidad:** B1 UTC→Bogotá, B2 unidad→kWh, B3 dedup
  versión/orden_archivo/fila, B4 nulo/negativo/outlier descartados,
  B5 huecos reportados, B6 facturas huérfanas a cuarentena, B7 entradas
  hostiles manejadas (csv.reader + queries parametrizadas).
- **C — Seguridad:** `data_classification.yaml` vivo, PII enmascarada en
  texto libre, AES-256-GCM por columna + índice ciego HMAC para joins,
  3 roles de menor privilegio, bitácora append-only (revoke + trigger),
  cero secretos en código, dependencias saneadas, threat model STRIDE.
- **D — Streaming:** 3 reglas exactas, consumidor que termina solo.
- **E — Observabilidad:** logs JSON sin PII, `metrics.json`, RUNBOOK.
- **F — IaC:** `infra/` Terraform (S3+KMS+IAM menor privilegio), `validate` OK.

## Front (dashboard)

```bash
python3 -m streamlit run src/front_app.py   # o: make front
```

Abre en `http://localhost:8501`. El dashboard corre como **rol_analista**:
solo lee `analytics.vw_consumo_analitico` — ningún dato PII es alcanzable
desde el front. Con la base caída, muestra los artefactos de `out/`
(consumo_mensual.csv, dq_report.json, alertas.json, metrics.json). Pestañas:
Consumo, Calidad de datos, Alertas del stream y Operación.

## Estructura

```
src/            pipeline (parse, ingest_*, crypto, pii, alerts, reports,
                front_app.py = dashboard Streamlit)
sql/            DDL, roles/grants, trigger append-only, vista analítica
tools/          producer.py (dado), init_db.py, ensure_env.py, verificar_entrega.py
tests/          pytest (parser, pii/crypto, alertas) — sin Docker
infra/          Terraform AWS
data/           datos crudos (inmutables)
out/ logs/      artefactos generados (gitignored)
```

Ver `DECISIONS.md` (ADRs), `SECURITY.md` (controles y cómo verificarlos),
`THREAT_MODEL.md` (STRIDE + ISO 27001), `RUNBOOK.md`.

## Qué no implementé / límites

Declarado también en `entrega.yaml → no_implementado`:

- MinIO queda como pieza de infra sin integrar al flujo (no hay S3 real en
  el pipeline; el origen es el FS `data/`). Terraform S3 modela el
  equivalente cloud.
- La llave PII vive en `.env` local (no KMS real); en AWS sería la CMK de
  `infra/main.tf`.
- `analista` ve `razon_social`/`ciudad` (Confidencial, no PII) en la vista.
