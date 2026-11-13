-- ===========================================================================
-- 004_vista_analitica.sql — Unica puerta de lectura del rol_analista (C4).
-- SECURITY DEFINER implícito: expone el modelo sin dar acceso a las tablas
-- base. CERO columnas Restringido-PII: nit/email/celular/direccion/
-- representante no existen aqui, ni en claro ni cifrados.
-- ===========================================================================

CREATE OR REPLACE VIEW analytics.vw_consumo_analitico AS
SELECT
    f.frontera_id,
    to_char(f.ts AT TIME ZONE 'America/Bogota', 'YYYY-MM')       AS mes,
    (f.ts AT TIME ZONE 'America/Bogota')::date                   AS fecha_local,
    date_trunc('hour', f.ts AT TIME ZONE 'America/Bogota')       AS hora_local,
    f.consumo_kwh,
    f.calidad_lectura,
    fr.codigo_sic,
    fr.cliente_id,
    c.razon_social,
    c.ciudad,
    c.departamento,
    fr.mercado,
    fr.operador_red,
    fr.nivel_tension
FROM core.fact_consumo_horario f
LEFT JOIN core.dim_frontera fr USING (frontera_id)
LEFT JOIN core.dim_cliente  c  USING (cliente_id);

-- La vista ya existe aqui: este es el unico objeto que puede leer
-- el rol_analista (el grant se movio desde 002 por orden de creacion).
GRANT SELECT ON analytics.vw_consumo_analitico TO __ANALISTA_USER__;
