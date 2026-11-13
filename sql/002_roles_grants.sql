-- ===========================================================================
-- 002_roles_grants.sql — Roles de menor privilegio (C4).
-- Los placeholders __*_USER__ / __*_PASS__ los sustituye tools/init_db.py
-- con los valores del .env generado por tools/ensure_env.py.
-- Idempotente: CREATE si no existe + ALTER ... PASSWORD siempre.
-- ===========================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '__INGESTA_USER__') THEN
        CREATE ROLE __INGESTA_USER__ LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '__ANALISTA_USER__') THEN
        CREATE ROLE __ANALISTA_USER__ LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '__AUDITOR_USER__') THEN
        CREATE ROLE __AUDITOR_USER__ LOGIN;
    END IF;
END $$;

ALTER ROLE __INGESTA_USER__  WITH LOGIN PASSWORD :'ingesta_pass';
ALTER ROLE __ANALISTA_USER__ WITH LOGIN PASSWORD :'analista_pass';
ALTER ROLE __AUDITOR_USER__  WITH LOGIN PASSWORD :'auditor_pass';

-- ---------------------------------------------------------------------------
-- rol_ingesta: escribe el modelo, inserta en la bitacora.
-- NO lee PII: de dim_cliente solo ve columnas no-PII (nit_hash es HMAC,
-- no reversible). Las columnas cifradas las escribe pero no las lee.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE bia_datos TO __INGESTA_USER__;
GRANT USAGE ON SCHEMA stage, core, audit TO __INGESTA_USER__;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA stage TO __INGESTA_USER__;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA stage TO __INGESTA_USER__;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO __INGESTA_USER__;
REVOKE SELECT ON core.dim_cliente FROM __INGESTA_USER__;
GRANT SELECT (cliente_id, razon_social, nit_hash, ciudad, departamento,
              observaciones_enmascaradas)
    ON core.dim_cliente TO __INGESTA_USER__;

GRANT INSERT ON audit.bitacora TO __INGESTA_USER__;
GRANT USAGE ON SEQUENCE audit.bitacora_id_seq TO __INGESTA_USER__;

-- Defaults para objetos creados despues por el superusuario (mismo rol que
-- corre init_db): mantener los privilegios si se agregan tablas.
ALTER DEFAULT PRIVILEGES FOR ROLE __SUPER_USER__ IN SCHEMA stage
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO __INGESTA_USER__;
ALTER DEFAULT PRIVILEGES FOR ROLE __SUPER_USER__ IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO __INGESTA_USER__;

-- ---------------------------------------------------------------------------
-- rol_analista: SOLO consulta la vista analitica. Sin tablas base, sin PII
-- en claro, sin escritura alguna.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE bia_datos TO __ANALISTA_USER__;
GRANT USAGE ON SCHEMA analytics TO __ANALISTA_USER__;
-- NOTA: el GRANT SELECT sobre vw_consumo_analitico va en
-- 004_vista_analitica.sql, despues del CREATE VIEW.

-- ---------------------------------------------------------------------------
-- rol_auditor: SOLO lee la bitacora. Sin PII, sin nada mas.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE bia_datos TO __AUDITOR_USER__;
GRANT USAGE ON SCHEMA audit TO __AUDITOR_USER__;
GRANT SELECT ON audit.bitacora TO __AUDITOR_USER__;

-- ---------------------------------------------------------------------------
-- Append-only de verdad: nadie (salvo que se dropee el trigger) puede
-- UPDATE/DELETE la bitacora. Revocacion explicita aunque nunca se otorgo.
-- ---------------------------------------------------------------------------
REVOKE UPDATE, DELETE ON audit.bitacora FROM PUBLIC;
REVOKE UPDATE, DELETE ON audit.bitacora FROM __INGESTA_USER__;
REVOKE UPDATE, DELETE ON audit.bitacora FROM __ANALISTA_USER__;
REVOKE UPDATE, DELETE ON audit.bitacora FROM __AUDITOR_USER__;
