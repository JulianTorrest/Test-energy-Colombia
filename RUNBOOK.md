# RUNBOOK — qué hacer cuando esto falla a las 2 a.m.

## Diagnóstico rápido (en orden)

1. **`logs/pipeline.log`** — un JSON por línea. `nivel: "error"` o
   `"warning"` dice la etapa y el archivo. Nunca hay PII ahí.
2. **`out/metrics.json`** — última corrida: filas por etapa y rechazos por
   motivo. `duracion_ms` por etapa muestra dónde se quedó.
3. **`stage.cuarentena`** — qué se rechazó y por qué:
   ```sql
   SELECT fuente, motivo, count(*) FROM stage.cuarentena GROUP BY 1,2;
   ```
4. **`stage.control_archivos`** — estado por archivo
   (`procesado`/`rechazado`) y su sha256.

## Síntomas frecuentes

| Síntoma | Causa probable | Acción |
|---|---|---|
| `make up` no conecta a PG | Contenedor no listo o `.env` viejo | `docker compose ps`; borra `.env` y `make up` de nuevo |
| `make run` → `Falta PII_ENCRYPTION_KEY` | `.env` no generado | Corre `make up` (crea `.env`) |
| `psycopg2.OperationalError` auth failed | `.env` no coincide con roles | `make down` + `docker compose down -v` + `make up` |
| Mes nuevo no aparece en salidas | Archivo quedó `rechazado` o mal nombre | `SELECT * FROM stage.control_archivos`; revisa cuarentena |
| Archivo corrupto rompió la corrida | Por diseño: se rechaza entero | Mira `audit.bitacora` y cuarentena; el resto de meses sigue igual |
| `make stream` se queda | Broker caído | El runner sale por `_MAX_S`; levanta `redpanda` y re-corre |
| Números distintos tras re-correr | Algo no idempotente | Compara `control_archivos`: no debería haber `filas_leidas` nuevas |

## Re-correr limpio (reset total)

```bash
make down
docker compose down -v      # borra volumenes (pgdata, miniodata)
rm -rf out/ logs/ .env
make up
make run
make verificar
```

## Controles que NO se deben "arreglar" a mano

- No edites `data/` (inmutable por contrato).
- No metas PII en `logs/` ni en `out/` — es **eliminatorio**.
- No des `UPDATE`/`DELETE` a `audit.bitacora`: el trigger lo bloquea a
  propósito (append-only).
- No pongas credenciales en código ni en `config/` — solo `.env`.
