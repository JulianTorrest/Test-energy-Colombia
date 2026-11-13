-- ===========================================================================
-- 001_schemas_tables.sql — Modelo de datos (idempotente, seguro re-ejecutar)
--
-- Esquemas:
--   stage     → staging + cuarentena + control de archivos (solo rol_ingesta)
--   core      → modelo analitico (dims + hechos), PII cifrada en reposo
--   analytics → vista analitica sin PII (solo rol_analista)
--   audit     → bitacora append-only (inserta rol_ingesta, lee rol_auditor)
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS stage;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Higiene: el esquema public no se usa y no debe ser escribible por cualquiera.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- stage.control_archivos: una fila por archivo de origen ya visto.
-- Es la base de la idempotencia: sha256 igual + estado != 'rechazado' => skip.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage.control_archivos (
    archivo          text PRIMARY KEY,
    sha256           char(64) NOT NULL,
    filas_leidas     int  NOT NULL DEFAULT 0,
    filas_cargadas   int  NOT NULL DEFAULT 0,
    filas_rechazadas int  NOT NULL DEFAULT 0,
    estado           text NOT NULL,           -- procesado | rechazado
    motivo           text,
    procesado_en     timestamptz NOT NULL DEFAULT now(),
    corrida_id       uuid
);

-- ---------------------------------------------------------------------------
-- stage.consumo_lectura: TODA lectura valida y normalizada (kWh, hora Bogota),
-- ganadora o perdedora del dedup B3. Permite reconstruir el fact sin releer
-- archivos (así se absorben las correcciones tardias).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage.consumo_lectura (
    id              bigserial PRIMARY KEY,
    archivo         text NOT NULL,
    orden_archivo   int  NOT NULL,           -- rank lexicografico del nombre
    fila_num        int  NOT NULL,
    frontera_id     text NOT NULL,
    ts              timestamptz NOT NULL,    -- normalizado a instante (B1)
    consumo_kwh     numeric(18,3) NOT NULL,  -- siempre kWh (B2)
    version_lectura int  NOT NULL DEFAULT 1,
    calidad_lectura text,
    unidad_original text,
    UNIQUE (archivo, fila_num)
);
CREATE INDEX IF NOT EXISTS ix_lectura_key
    ON stage.consumo_lectura (frontera_id, ts);

-- ---------------------------------------------------------------------------
-- stage.cuarentena: todo lo rechazado, consultable, con motivo.
-- payload contiene la fila cruda en JSON; si el registro puede contener PII
-- (maestro clientes, facturacion) se guarda cifrado y payload_cifrado=true.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage.cuarentena (
    id              bigserial PRIMARY KEY,
    fuente          text NOT NULL,
    fila_num        int,
    motivo          text NOT NULL,
    payload         text,
    payload_cifrado boolean NOT NULL DEFAULT false,
    corrida_id      uuid,
    creado_en       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cuarentena_motivo ON stage.cuarentena (motivo);

-- ---------------------------------------------------------------------------
-- core.dim_cliente — razon_social: Confidencial; *_cifrado: Restringido-PII
-- (AES-256-GCM, bytea = nonce||ciphertext); nit_hash: indice ciego HMAC-SHA256
-- para poder unir con facturacion sin exponer el NIT.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.dim_cliente (
    cliente_id                  text PRIMARY KEY,
    razon_social                text NOT NULL,
    nit_cifrado                 bytea,
    nit_hash                    char(64),
    email_cifrado               bytea,
    celular_cifrado             bytea,
    direccion_cifrada           bytea,
    ciudad                      text,
    departamento                text,
    representante_cifrado       bytea,
    observaciones_enmascaradas  text
);

CREATE TABLE IF NOT EXISTS core.dim_frontera (
    frontera_id    text PRIMARY KEY,
    codigo_sic     text,
    cliente_id     text,          -- sin FK dura: los maestros llegan sucios
    mercado        text,
    operador_red   text,
    nivel_tension  int,
    fecha_registro date,
    estado         text
);

CREATE TABLE IF NOT EXISTS core.limite_frontera (
    frontera_id text PRIMARY KEY,
    min_kwh     numeric(18,3),
    max_kwh     numeric(18,3)
);

-- ---------------------------------------------------------------------------
-- core.fact_consumo_horario: UNA fila por (frontera, hora) = lectura ganadora
-- del dedup B3. Se reconstruye desde stage.consumo_lectura en cada corrida.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.fact_consumo_horario (
    frontera_id     text NOT NULL,
    ts              timestamptz NOT NULL,
    consumo_kwh     numeric(18,3) NOT NULL,
    version_lectura int  NOT NULL,
    calidad_lectura text,
    archivo_origen  text NOT NULL,
    PRIMARY KEY (frontera_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_fact_mes
    ON core.fact_consumo_horario (ts);

-- ---------------------------------------------------------------------------
-- core.fact_factura: facturas validas (frontera_id existe en dim_frontera).
-- Las huerfanas no llegan aca: van a stage.cuarentena (B6).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.fact_factura (
    factura_id             text NOT NULL,
    periodo                text NOT NULL,
    frontera_id            text NOT NULL,
    cliente_nit_cifrado    bytea,
    cliente_nit_hash       char(64),
    cliente_razon_social   text,
    cliente_email_cifrado  bytea,
    consumo_facturado_kwh  numeric(18,3),
    tarifa_cop_kwh         numeric(18,4),
    valor_total_cop        numeric(20,2),
    archivo                text NOT NULL,
    PRIMARY KEY (factura_id, periodo)
);

-- ---------------------------------------------------------------------------
-- audit.bitacora: append-only. UPDATE/DELETE bloqueados por trigger
-- (003_audit_trigger.sql) ademas de no estar otorgados a ningun rol.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit.bitacora (
    id              bigserial PRIMARY KEY,
    corrida_id      uuid NOT NULL,
    ts              timestamptz NOT NULL DEFAULT now(),
    usuario_db      text NOT NULL,
    etapa           text NOT NULL,
    objeto          text,
    filas_leidas    int,
    filas_cargadas  int,
    filas_rechazadas int,
    resultado       text NOT NULL,   -- ok | error | rechazado
    duracion_ms     int,
    detalle         jsonb            -- NUNCA contiene PII
);
