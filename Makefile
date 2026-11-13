# ===========================================================================
# CONTRATO DE EJECUCION  --  NO RENOMBRES NI ELIMINES ESTOS TARGETS
#
# El evaluador corre tu entrega de forma automatica usando exactamente estos
# comandos, en este orden. Si un target no existe o falla, se pierden los
# puntos asociados sin revision manual.
#
# Podes agregar los targets que quieras. Podes cambiar COMO se implementa
# cada uno. No podes cambiar su NOMBRE ni su CONTRATO de salida.
# ===========================================================================

PY ?= python3
COMPOSE ?= docker compose

.PHONY: setup up run stream test down clean verificar

## Instala dependencias. Idempotente y no interactivo.
setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

## Levanta la infraestructura local y la deja lista: genera .env con
## credenciales aleatorias si falta, sube los contenedores, espera a Postgres
## y aplica DDL + roles + grants + vista (tools/init_db.py). Al terminar,
## 'make run' corre sin pasos manuales.
up:
	$(PY) tools/ensure_env.py
	$(COMPOSE) --env-file .env up -d
	$(PY) tools/init_db.py

## Ejecuta el pipeline batch completo de punta a punta.
## Idempotente: dos corridas seguidas dejan el mismo estado (los archivos ya
## procesados se omiten por sha256; el fact se reconstruye desde staging).
## Salidas: out/consumo_mensual.csv, out/dq_report.json, out/metrics.json,
## logs/pipeline.log
run:
	$(PY) -m src.pipeline

## Procesa el stream de mediciones y termina solo (no se queda colgado).
## Alimenta el topico con tools/producer.py si esta vacio, consume hasta el
## fin del stream y escribe out/alertas.json. Timeout del evaluador: 300 s.
stream:
	$(PY) -m src.stream_runner

## Corre la suite de pruebas. Exit code 0 = todo paso.
test:
	$(PY) -m pytest tests/ -q

## Dashboard Streamlit (rol analista, sin PII). No forma parte del contrato
## del evaluador: es el front solicitado para explorar los datos.
front:
	$(PY) -m streamlit run src/front_app.py

## Apaga la infraestructura.
down:
	$(COMPOSE) down

## Borra artefactos generados (out/, logs/, .env, volumenes).
clean:
	$(COMPOSE) down -v
	rm -rf out/ logs/ .env .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

## YA ESTA RESUELTO. Comprueba que tu entrega.yaml describe lo que levantaste:
## que los roles conectan y que los objetos que nombraste existen. No califica
## nada. Correlo con la infra arriba, antes de comprimir.
verificar:
	@$(PY) tools/verificar_entrega.py
