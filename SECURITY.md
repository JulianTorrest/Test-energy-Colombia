# Seguridad de la solución

## 1. Clasificación de datos

`data_classification.yaml` etiqueta cada columna en `Público` / `Interno` /
`Confidencial` / `Restringido-PII`. Es un **artefacto vivo**: el pipeline lo
lee en cada corrida (`src/config.py::columnas_pii`) y cualquier columna
marcada `Restringido-PII` se cifra antes de llegar a la base. Cambiar una
clasificación en el YAML cambia lo que se cifra, sin tocar código.

## 2. Cifrado

| Qué | Dónde | Mecanismo | Gestión de la llave |
|---|---|---|---|
| `nit`, `email_contacto`, `celular_contacto`, `direccion`, `representante_legal` | `core.dim_cliente` (columnas `*_cifrado`) | AES-256-GCM, `bytea` = nonce‖ct‖tag | `PII_ENCRYPTION_KEY` en `.env` (gitignored), hex 64c |
| `cliente.nit`, `cliente.email_facturacion` | `core.fact_factura` | idem | idem |
| payloads de cuarentena con posible PII | `stage.cuarentena.payload` | idem (`payload_cifrado=true`) | idem |

**Joins sobre campos sensibles sin exponerlos:** se guarda además
`nit_hash` = HMAC-SHA256(nit normalizado, llave). Es determinista (permite
`JOIN`), no reversible, y no revela el NIT. `fact_factura.cliente_nit_hash`
= `dim_cliente.nit_hash`.

**Por qué cifrado a nivel de aplicación y no pgcrypto:** la llave nunca vive
en la base — quien se roba un dump o lee como superusuario ve solo
`nonce‖ciphertext`. Con pgcrypto la llave viaja en cada query (queda en
logs/statement) o se guarda en la base misma. Verificado: `SELECT nit_cifrado
FROM core.dim_cliente` como superusuario devuelve bytes binarios ilegibles.

## 3. Control de accesos (menor privilegio)

| Rol | Puede | NO puede | GRANT |
|---|---|---|---|
| `rol_ingesta` | INSERT/UPDATE/DELETE en `stage.*`, `core.*`; INSERT en `audit.bitacora`; SELECT solo en columnas no-PII de `dim_cliente` | Leer columnas `*_cifrado` (revocado a nivel columna), leer la vista analítica, tocar `analytics` | `sql/002_roles_grants.sql` |
| `rol_analista` | SELECT en `analytics.vw_consumo_analitico` | Todo lo demás: tablas base, escribir, PII en claro o cifrada | `sql/002_roles_grants.sql` |
| `rol_auditor` | SELECT en `audit.bitacora` | Todo lo demás | `sql/002_roles_grants.sql` |

## 4. Enmascaramiento de PII

`src/pii.py` detecta en `observaciones` (texto libre): correos, celulares
(`3XXXXXXXXX`) y NIT/cédulas (con o sin puntos de miles y dígito de
verificación). Se reemplazan por `[email]`/`[celular]`/`[nit/cedula]`
in-place y se guarda `observaciones_enmascaradas`. El conteo de clientes
afectados va a `dq_report.json → clientes_con_pii_en_texto_libre`. El texto
original **no** se persiste en claro en ninguna parte.

## 5. Gestión de secretos

- Cero credenciales en código: todo viene de `.env` (gitignored), generado
  por `tools/ensure_env.py` con valores aleatorios por corrida de setup.
- **Hallazgos del scaffolding heredado (todos eliminados):**
  - `config/pipeline.yaml`: `password: S3cr3t_H3r3d4d0`, `llave_maestra`
    base64 con `algoritmo: none`, `access_key/secret_key: minioadmin`,
    webhook de Slack → reemplazado por config sin secretos.
  - `docker-compose.yml`: `POSTGRES_PASSWORD: postgres`,
    `MINIO_ROOT_USER/PASSWORD: minioadmin` → ahora `${VAR}` desde `.env`.
- `entrega.yaml` se auto-completa desde `.env` en `make up`
  (`tools/init_db.py`), para que el manifiesto siempre coincida con lo
  levantado.

## 6. Trazabilidad y auditoría

`audit.bitacora`: corrida_id, usuario_db, etapa, objeto, filas
leídas/cargadas/rechazadas, resultado, duración, detalle JSON **sin PII**.
Append-only de verdad en dos capas:
1. `REVOKE UPDATE, DELETE` de PUBLIC y de los tres roles.
2. Trigger `BEFORE UPDATE OR DELETE` que lanza excepción — bloquea incluso
   al superusuario (salvo que dropee el trigger, que ya es sabotaje DBA).

## 7. Dependencias

`requirements.txt` heredado traía `requests 2.19.1`, `PyYAML 5.3.1`,
`urllib3 1.24.1`, `Jinja2 2.11.2` — todas con CVEs públicos conocidos y sin
uso real en el pipeline. Se eliminaron; `PyYAML` se subió a `6.0.2` (se
necesita), y se fijaron versiones recientes de `psycopg2-binary`,
`kafka-python`, `cryptography`, `python-dotenv`, `pytest`. `pandas` se
eliminó: el pipeline usa `csv`/`json` stdlib + SQL (menos superficie).

## 8. Cómo verificar todo lo anterior

```bash
make up && make run && make verificar

# C3: como superusuario, todo PII es binario ilegible
psql -h localhost -U $PG_SUPERUSER -d bia_datos \
  -c "SELECT cliente_id, nit_cifrado FROM core.dim_cliente LIMIT 3"

# C4: como analista, solo la vista; un intento a la tabla base falla
psql -h localhost -U rol_analista -d bia_datos \
  -c "SELECT * FROM core.dim_cliente"          # permission denied

# C5: como superusuario incluso, UPDATE a la bitácora falla por trigger
psql -h localhost -U $PG_SUPERUSER -d bia_datos \
  -c "UPDATE audit.bitacora SET resultado='x' WHERE id=1"   # ERROR
```
