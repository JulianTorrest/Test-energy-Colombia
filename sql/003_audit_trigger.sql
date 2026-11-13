-- ===========================================================================
-- 003_audit_trigger.sql — Bitacora append-only "de verdad" (C5):
-- aun con privilegios, un UPDATE/DELETE dispara excepcion. Complementa los
-- REVOKE de 002 (defensa en profundidad).
-- ===========================================================================

CREATE OR REPLACE FUNCTION audit.impedir_modificacion()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit.bitacora es append-only: % no permitido', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS bitacora_append_only ON audit.bitacora;
CREATE TRIGGER bitacora_append_only
    BEFORE UPDATE OR DELETE ON audit.bitacora
    FOR EACH ROW EXECUTE FUNCTION audit.impedir_modificacion();
