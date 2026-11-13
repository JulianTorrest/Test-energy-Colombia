# Uso de IA en esta prueba

> Obligatorio. No afecta la nota que hayas usado mucha o poca IA; afecta la
> calidad de lo que contás acá.

## 1. Herramientas y dónde las usé

| Herramienta | Para qué | Aproximadamente cuánto del resultado |
|---|---|---|
| Cascade (SWE-1.6) en el IDE | Leer el enunciado y los datos, diseñar el modelo, escribir `src/`, `sql/`, `tools/`, tests, docs | ~80% del código, 100% de la exploración de datos |

## 2. Prompts que movieron la aguja

```
Revisá los 4 archivos de consumo y decime las diferencias de formato:
separador, nombres de columna, formato de timestamp, unidades, encoding.
No asumas nada por el nombre del archivo.
```
**Qué obtuve y qué hice con eso:** confirmé que feb usa `;` + `timestamp_utc`
+ decimal-coma, mar agrega columna `unidad` (MWh). Con eso diseñé el parser
por contenido y no por nombre de archivo.

```
Diseñá la estrategia de idempotencia y correcciones tardías: cómo hago que
un *_correccion.csv reemplace lecturas ya cargadas sin recargar todo.
```
**Qué obtuve y qué hice con eso:** la idea de staging inmutable + fact
reconstruido por ranking (`ROW_NUMBER` por version/orden_archivo/fila),
que terminó siendo ADR-001.

```
¿Cómo hago joins por un campo PII sin exponerlo ni guardarlo en claro?
```
**Qué obtuve y qué hice con eso:** índice ciego HMAC-SHA256 (`nit_hash`),
implementado en `src/crypto.py` y usado en `fact_factura`.

## 3. Qué generó la IA que cambié, y por qué

| Archivo / función | Qué propuso la IA | Qué dejé yo | Por qué |
|---|---|---|---|
| `reconstruir_fact` | MERGE/UPSERT fila a fila con `ON CONFLICT` | `TRUNCATE`+`INSERT` del ranking en una transacción | Idempotencia real y corrección tardía trivial; el ranking es declarativo |
| `alertas_sin_reporte` | Alertar por cada 3er ausente aun en rachas largas | Una sola alerta por tramo de caída (racha==3 exacto) | El enunciado dice "una sola alerta por caída" |
| `docker-compose.yml` | Credenciales en variables con defaults | Sin defaults — `.env` obligatorio generado aleatorio | Un default en compose vuelve a ser un secreto hardcodeado (eliminatorio) |

## 4. Un error que cometió la IA y que tuve que corregir

En `src/parse.py`, la primera versión de `detectar_delimitador` usaba
`csv.Sniffer().sniff()`. Fallaba con los datos de febrero porque la presencia
de comillas anidadas y decimales con coma hacía que el Sniffer eligiera `,`
en vez de `;`, partiendo `163,795` en dos campos. Lo detecté al correr el
test `test_b1_utc_a_bogota`: `consumo_kwh` quedaba `None`. Lo cambié por un
conteo de separadores en la línea de encabezado, que es determinista. La
corrección está en `src/parse.py::detectar_delimitador` y la cubre
`tests/test_parse.py`.

## 5. Qué NO le delegué a la IA, a propósito

- Las decisiones de seguridad (qué es `Restringido-PII`, índice ciego,
  grants por columna): las definí yo leyendo los requisitos C1–C8.
- La semántica exacta de B3/B5 y de las tres reglas del stream: las fijé
  con tests antes de implementar.
- El threat model: las amenazas son sobre mi arquitectura, no genéricas.
