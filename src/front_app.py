"""Dashboard Streamlit — plataforma de consumo Bia Energy.

    python3 -m streamlit run src/front_app.py   (o: make front)

Menor privilegio tambien en el front: la conexion a Postgres usa el rol
ANALISTA, que solo puede leer analytics.vw_consumo_analitico. Ningun dato
PII (nit, email, celular, direccion, representante) es alcanzable desde
esta app — la vista no los expone ni cifrados.

Bia es una COMERCIALIZADORA de energia: compra en el mercado mayorista
y revende a clientes industriales/comerciales. Por eso los graficos se
enfocan en lo que le importa a esa operacion: curva de carga agregada de
la cartera, perfil horario tipico, picos de demanda y participacion por
cliente/mercado/nivel de tension.

Modos:
  * con la base arriba: consulta la vista analitica en vivo
  * sin base: muestra los artefactos out/ y metrics/dq del ultimo `make run`
"""
from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
LOGS = ROOT / "logs"

st.set_page_config(page_title="Bia — Plataforma de consumo", layout="wide")


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------
def _env() -> dict:
    vals = {}
    ruta = ROOT / ".env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


@st.cache_resource(ttl=60)
def _conn_analista():
    """Conexion como rol_analista (solo lectura de la vista, sin PII)."""
    import psycopg2
    e = _env()
    return psycopg2.connect(
        host=e.get("PG_HOST", "127.0.0.1"), port=e.get("PG_PORT", "55432"),
        dbname=e.get("PG_DATABASE", "bia_datos"),
        user=e.get("PG_ROL_ANALISTA_USER", "rol_analista"),
        password=e.get("PG_ROL_ANALISTA_PASSWORD", ""),
        connect_timeout=5)


@st.cache_data(ttl=60)
def cargar_vista() -> pd.DataFrame | None:
    try:
        conn = _conn_analista()
        if conn.closed:
            st.cache_resource.clear()
            conn = _conn_analista()
        d = pd.read_sql("SELECT * FROM analytics.vw_consumo_analitico", conn)
        d["hora_local"] = pd.to_datetime(d["hora_local"])
        return d
    except Exception:
        return None


@st.cache_data(ttl=30)
def cargar_out(nombre: str):
    ruta = OUT / nombre
    if not ruta.exists() or ruta.stat().st_size == 0:
        return None
    if nombre.endswith(".json"):
        return json.loads(ruta.read_text(encoding="utf-8"))
    return pd.read_csv(ruta)


def _filtro(label: str, opciones: list, fmt=None, key=None) -> list:
    """Filtro centralizado: checkbox 'Todas' + multiselect.

    Con 'Todas' activo devuelve todas las opciones (equivalente a no
    filtrar); desactivado, devuelve solo lo elegido.
    """
    key = key or label
    todas = st.checkbox(f"{label}: todas", value=True, key=f"all_{key}")
    sel = st.multiselect(label, opciones, format_func=fmt or str,
                         disabled=todas, key=f"sel_{key}",
                         placeholder="activa 'todas' o elige aqui")
    return list(opciones) if todas else sel


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
st.title("Bia — Plataforma de consumo")
st.caption("Comercializadora de energía: vista de analista, sin PII. "
           "Pipeline batch + stream de mediciones.")

df = cargar_vista()
en_linea = df is not None
dq = cargar_out("dq_report.json")
metrics = cargar_out("metrics.json")
alertas = cargar_out("alertas.json")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fuente", "Postgres (vista)" if en_linea else "Archivos out/")
c2.metric("Lecturas (vista)", f"{len(df):,}" if en_linea else "—")
c3.metric("Energía total", f"{df['consumo_kwh'].sum():,.0f} kWh"
          if en_linea else "—")
c4.metric("Filas leídas (última corrida)",
          f"{dq['filas_leidas']:,}" if dq else "—")

if not en_linea:
    st.info("Sin conexión a la base como `rol_analista` — mostrando artefactos "
            "de `out/`. Corre `make up && make run` para datos en vivo.")

# ---------------------------------------------------------------------------
# Sidebar: filtros centralizados, todos con opcion "Todas"
# ---------------------------------------------------------------------------
etiqueta = {}
with st.sidebar:
    st.header("Filtros")
    if st.button("Refrescar datos", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

    if en_linea:
        dims = (df[["frontera_id", "razon_social", "ciudad", "mercado",
                    "nivel_tension"]]
                .drop_duplicates("frontera_id").fillna(""))
        etiqueta = {r.frontera_id:
                    f"{r.frontera_id} · {r.razon_social or '(s/r)'}"
                    f" — {r.ciudad}" for r in dims.itertuples()}

        mes_sel = _filtro("Mes", sorted(df["mes"].dropna().unique()))
        mercado_sel = _filtro("Mercado",
                              sorted(df["mercado"].dropna().unique()))
        cliente_sel = _filtro("Cliente",
                              sorted(df["razon_social"].dropna().unique()))
        frontera_sel = _filtro(
            "Frontera", sorted(df["frontera_id"].dropna().unique()),
            fmt=lambda f: etiqueta.get(f, f))
        calidad_sel = _filtro(
            "Calidad lectura",
            sorted(df["calidad_lectura"].dropna().unique()))
    else:
        mes_sel = mercado_sel = cliente_sel = frontera_sel = calidad_sel = []

    # Tipos de alerta tambien van aqui: el sidebar es el UNICO lugar
    # donde se filtra (las alertas vienen de out/, no de la base).
    tipos_al = sorted({a.get("tipo") for a in alertas
                       if a.get("tipo")}) if alertas else []
    if tipos_al:
        alerta_sel = _filtro("Tipo de alerta", tipos_al,
                             key="tipo_alerta")
    else:
        alerta_sel = []

# Filtro global aplicado a la vista
if en_linea:
    dff = df[df["mes"].isin(mes_sel)
             & df["mercado"].isin(mercado_sel)
             & df["razon_social"].isin(cliente_sel)
             & df["frontera_id"].isin(frontera_sel)
             & df["calidad_lectura"].isin(calidad_sel)]
else:
    dff = df

tab_demanda, tab_cartera, tab_dq, tab_alertas, tab_ops, tab_arq = st.tabs(
    ["Demanda", "Cartera", "Calidad de datos", "Alertas stream",
     "Operación", "Arquitectura"])

# ---------------------------------------------------------------------------
# DEMANDA: lo que importa a una comercializadora
# ---------------------------------------------------------------------------
with tab_demanda:
    if en_linea:
        if dff.empty:
            st.warning("El filtro actual no devuelve lecturas.")
        else:
            # KPIs de operacion comercial
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Energía (filtro)", f"{dff['consumo_kwh'].sum():,.0f} kWh")
            demanda_hora = dff.groupby("hora_local")["consumo_kwh"].sum()
            pico_ts = demanda_hora.idxmax()
            k2.metric("Pico de demanda",
                      f"{demanda_hora.max():,.0f} kWh",
                      help=pico_ts.strftime("%Y-%m-%d %H:%M"))
            k3.metric("Demanda media horaria",
                      f"{demanda_hora.mean():,.0f} kWh")
            k4.metric("Fronteras activas", dff["frontera_id"].nunique())

            # 1) Curva de carga agregada de la cartera
            st.subheader("Curva de carga agregada (kWh por hora)")
            st.caption("Suma de todos los puntos de medición filtrados — "
                       "la demanda que Bia debe atender/comprar en bolsa.")
            st.line_chart(demanda_hora.sort_index())

            # 2) Perfil de carga tipico por hora del dia
            st.subheader("Perfil de carga típico (kWh medio por hora del día)")
            st.caption("Patrón horario de la cartera — base para la "
                       "programación de compras en el mercado mayorista.")
            perfil = (dff.assign(hora=dff["hora_local"].dt.hour)
                      .groupby("hora")["consumo_kwh"].mean()
                      .reindex(range(24), fill_value=0))
            st.bar_chart(perfil)

            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Top 10 picos de demanda agregada")
                picos = (demanda_hora.nlargest(10).round(1)
                         .rename("kwh_total_hora").reset_index()
                         .assign(hora_local=lambda t:
                                 t["hora_local"].dt.strftime("%Y-%m-%d %H:%M")))
                st.dataframe(picos.set_index("hora_local"),
                             use_container_width=True)
            with col_b:
                st.subheader("Energía por mes")
                por_mes = (dff.groupby("mes")["consumo_kwh"].sum()
                           .round(1).rename("kwh"))
                st.bar_chart(por_mes)

            # 3) Curva por frontera (limitado para legibilidad)
            st.subheader("Curva de carga por frontera (top 5 del filtro)")
            top5_ids = (dff.groupby("frontera_id")["consumo_kwh"].sum()
                        .nlargest(5).index.tolist())
            sub = dff[dff["frontera_id"].isin(top5_ids)]
            piv = (sub.pivot_table(index="hora_local", columns="frontera_id",
                                   values="consumo_kwh", aggfunc="sum")
                   .fillna(0))
            piv.columns = [etiqueta.get(c, c).split(" · ")[0]
                           for c in piv.columns]
            st.line_chart(piv)

            with st.expander("Detalle (primeras 500 filas del filtro) "
                             "+ descarga"):
                st.dataframe(dff.head(500), use_container_width=True)
                st.download_button("Descargar filtro como CSV",
                                   dff.to_csv(index=False).encode("utf-8"),
                                   "consumo_filtrado.csv", "text/csv")
    else:
        cm = cargar_out("consumo_mensual.csv")
        if cm is not None:
            st.subheader("Consumo mensual (out/consumo_mensual.csv)")
            st.dataframe(cm, use_container_width=True)
            piv = (cm.pivot_table(index="mes", columns="frontera_id",
                                  values="consumo_kwh", aggfunc="sum"))
            st.bar_chart(piv)
        else:
            st.warning("No hay datos: corre `make run` primero.")

# ---------------------------------------------------------------------------
# CARTERA: participacion por cliente / mercado / tension / region
# ---------------------------------------------------------------------------
with tab_cartera:
    if en_linea and not dff.empty:
        st.subheader("Energía por cliente (participación de cartera)")
        por_cli = (dff.groupby("razon_social")["consumo_kwh"].sum()
                   .nlargest(15).round(1))
        st.bar_chart(por_cli, horizontal=True)

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.subheader("Por mercado")
            st.bar_chart(dff.groupby("mercado")["consumo_kwh"]
                         .sum().round(1))
        with col_b:
            st.subheader("Por nivel de tensión")
            st.bar_chart(dff.groupby("nivel_tension")["consumo_kwh"]
                         .sum().round(1))
        with col_c:
            st.subheader("Por departamento")
            st.bar_chart(dff.groupby("departamento")["consumo_kwh"]
                         .sum().nlargest(12).round(1))

        st.subheader("Resumen por cliente")
        resumen = (dff.groupby("razon_social")
                   .agg(fronteras=("frontera_id", "nunique"),
                        energia_kwh=("consumo_kwh", "sum"),
                        kwh_medio_hora=("consumo_kwh", "mean"))
                   .round(1).sort_values("energia_kwh", ascending=False))
        resumen["participacion_%"] = (
            resumen["energia_kwh"] / resumen["energia_kwh"].sum() * 100
        ).round(2)
        st.dataframe(resumen, use_container_width=True)
        st.download_button("Descargar resumen por cliente",
                           resumen.to_csv().encode("utf-8"),
                           "resumen_clientes.csv", "text/csv")
    elif en_linea:
        st.warning("El filtro actual no devuelve lecturas.")
    else:
        st.warning("Sin conexión a la base: corre `make up && make run`.")

# ---------------------------------------------------------------------------
with tab_dq:
    if dq:
        st.subheader("Reporte de calidad (dq_report.json)")
        a, b, c, d = st.columns(4)
        a.metric("Duplicados exactos", dq["duplicados_exactos"])
        b.metric("Duplicados conflictivos", dq["duplicados_conflictivos"])
        c.metric("Correcciones tardías", dq["correcciones_tardias_aplicadas"])
        d.metric("MWh convertidos", dq["registros_mwh_convertidos"])
        a2, b2, c2, d2 = st.columns(4)
        a2.metric("Nulos", dq["registros_nulos"])
        b2.metric("Negativos", dq["registros_negativos"])
        c2.metric("Outliers", dq["registros_outlier"])
        d2.metric("Facturas huérfanas", dq["fronteras_huerfanas_facturacion"])
        st.metric("Clientes con PII en texto libre",
                  dq["clientes_con_pii_en_texto_libre"])

        st.subheader("Huecos por frontera (horas sin lectura válida)")
        huecos = pd.Series(dq["horas_sin_lectura_valida_por_frontera"],
                           name="horas_sin_dato")
        if len(huecos):
            st.bar_chart(huecos, horizontal=True)
            st.caption(f"Total: {dq['horas_sin_lectura_valida_total']:,} horas")
        else:
            st.success("Sin huecos en los meses procesados.")

        if metrics and metrics.get("filas_rechazadas_por_motivo"):
            st.subheader("Rechazos a cuarentena por motivo")
            st.bar_chart(pd.Series(metrics["filas_rechazadas_por_motivo"]))
    else:
        st.warning("No existe out/dq_report.json: corre `make run`.")

    # --- Graficos sobre la vista (requieren la base arriba) ----------------
    if en_linea and not dff.empty:
        st.divider()
        st.subheader("Calidad de lectura en la vista (filtro actual)")

        heat = (dff.assign(
                no_ok=dff["calidad_lectura"].str.upper()
                .ne("OK").astype(int))
                .groupby(["frontera_id", "mes"])
                .agg(total=("consumo_kwh", "size"), malas=("no_ok", "sum"))
                .reset_index())
        heat["pct_no_ok"] = (heat["malas"] / heat["total"] * 100).round(2)

        cob = (dff.groupby(["frontera_id", "mes"])
               .agg(horas=("hora_local", "nunique")).reset_index())
        cob["horas_mes"] = cob["mes"].map(
            lambda m: pd.Period(m).days_in_month * 24)
        cob["pct_hueco"] = ((1 - cob["horas"] / cob["horas_mes"]) * 100
                            ).round(2).clip(lower=0)

        ha, hb = st.columns(2)
        with ha:
            st.markdown("**Mapa de calor — % lecturas no-ok** "
                        "(frontera x mes)")
            h1 = (alt.Chart(heat).mark_rect()
                  .encode(
                      x=alt.X("mes:O", title="Mes"),
                      y=alt.Y("frontera_id:N", title="Frontera"),
                      color=alt.Color("pct_no_ok:Q",
                                      scale=alt.Scale(scheme="reds"),
                                      title="% no-ok"),
                      tooltip=["frontera_id", "mes", "total", "malas",
                               "pct_no_ok"]))
            st.altair_chart(h1, use_container_width=True)
        with hb:
            st.markdown("**Mapa de calor — % horas del mes sin lectura** "
                        "(frontera x mes)")
            h2 = (alt.Chart(cob).mark_rect()
                  .encode(
                      x=alt.X("mes:O", title="Mes"),
                      y=alt.Y("frontera_id:N", title="Frontera"),
                      color=alt.Color("pct_hueco:Q",
                                      scale=alt.Scale(scheme="reds"),
                                      title="% hueco"),
                      tooltip=["frontera_id", "mes", "horas",
                               "horas_mes", "pct_hueco"]))
            st.altair_chart(h2, use_container_width=True)

        hc, hd = st.columns(2)
        with hc:
            st.subheader("Distribución de calidad_lectura")
            st.bar_chart(dff["calidad_lectura"].value_counts())
        with hd:
            st.subheader("Lecturas no-OK por día")
            st.caption("Lecturas cuya marca `calidad_lectura` no es OK — "
                       "en estos datos son las `CORREGIDA`: lecturas "
                       "reemplazadas por archivos `*_correccion.csv`.")
            diario = (dff.assign(dia=dff["hora_local"].dt.date,
                                 no_ok=dff["calidad_lectura"].str.upper()
                                 .ne("OK").astype(int))
                      .groupby("dia")["no_ok"].sum())
            st.line_chart(diario)
    elif en_linea:
        st.caption("El filtro actual no devuelve lecturas para graficar.")

# ---------------------------------------------------------------------------
with tab_alertas:
    if alertas:
        df_al = pd.DataFrame(alertas)
        st.subheader("Alertas del stream (out/alertas.json)")
        conteo = df_al["tipo"].value_counts()
        cols = st.columns(len(conteo))
        for col, (tipo, n) in zip(cols, conteo.items()):
            col.metric(tipo, int(n))

        # tipo de alerta se filtra en el sidebar centralizado
        df_f = df_al[df_al["tipo"].isin(alerta_sel)]
        # y hereda el filtro global de frontera del sidebar
        df_f = df_f[df_f["frontera_id"].isin(frontera_sel)] \
            if en_linea else df_f

        st.subheader("Alertas por frontera")
        top_al = df_f["frontera_id"].value_counts().head(15)
        top_al.index = [etiqueta.get(f, f) for f in top_al.index]
        st.bar_chart(top_al, horizontal=True)

        st.subheader("Alertas en el tiempo")
        if "ts" in df_f.columns and len(df_f):
            tl = (df_f.assign(ts=pd.to_datetime(df_f["ts"], utc=True,
                                                errors="coerce"))
                  .dropna(subset=["ts"])
                  .groupby([pd.Grouper(key="ts", freq="D"), "tipo"])
                  .size().unstack(fill_value=0))
            if len(tl):
                st.bar_chart(tl)
        st.dataframe(df_f.sort_values(["frontera_id", "ts"])
                     if "ts" in df_f.columns else df_f,
                     use_container_width=True)
        st.download_button("Descargar alertas (filtro) como CSV",
                           df_f.to_csv(index=False).encode("utf-8"),
                           "alertas_filtradas.csv", "text/csv")
    else:
        st.warning("No existe out/alertas.json: corre `make stream`.")

# ---------------------------------------------------------------------------
with tab_ops:
    if metrics:
        st.subheader("Última corrida (metrics.json)")
        a, b, c = st.columns(3)
        a.metric("Leídas", f"{metrics.get('filas_leidas', 0):,}")
        b.metric("Cargadas", f"{metrics.get('filas_cargadas', 0):,}")
        c.metric("Duración total", f"{metrics.get('duracion_total_ms', 0)} ms")
        st.caption(f"corrida_id: `{metrics.get('corrida_id')}` — "
                   f"inicio: {metrics.get('inicio')}")

        st.subheader("Por etapa")
        etapas = pd.DataFrame(metrics.get("etapas", {})).T
        st.dataframe(etapas, use_container_width=True)
        if "duracion_ms" in etapas.columns:
            st.bar_chart(etapas["duracion_ms"].astype(float))

        st.subheader("Rechazos por motivo")
        rech = metrics.get("filas_rechazadas_por_motivo", {})
        if rech:
            st.bar_chart(pd.Series(rech))
        else:
            st.success("Sin rechazos.")
    else:
        st.warning("No existe out/metrics.json: corre `make run`.")
    with st.expander("logs/pipeline.log (últimas 50 líneas, sin PII)"):
        log_path = LOGS / "pipeline.log"
        if log_path.exists():
            lineas = log_path.read_text(encoding="utf-8").splitlines()[-50:]
            st.code("\n".join(lineas), language="json")
        else:
            st.caption("Aún no hay log.")

# ---------------------------------------------------------------------------
with tab_arq:
    st.subheader("Arquitectura de la solución")
    persp = st.selectbox(
        "Perspectiva",
        ["Pipeline de datos", "Seguridad y control de acceso",
         "Despliegue local (Docker)", "Cloud propuesto (AWS)"],
        help="Cada perspectiva muestra el mismo sistema desde un "
             "ángulo distinto: flujo de datos, seguridad, infraestructura "
             "local o su equivalente en la nube.")

    if persp == "Pipeline de datos":
        st.graphviz_chart("""
        digraph {
            rankdir=LR; node [shape=box, style="rounded,filled",
                              fillcolor="#e8f0fe", fontname="Segoe UI"];
            subgraph cluster_src { label="Fuentes (data/)"; style=dashed;
                "consumos/*.csv"; "maestros/*.csv"; "facturacion/*.json";
                "mediciones_stream.ndjson"; }
            subgraph cluster_stage { label="stage (crudo+control)";
                "consumo_lectura"; "control_archivos"; "cuarentena"; }
            subgraph cluster_core { label="core (modelo)";
                "dim_cliente/dim_frontera"; "limite_frontera";
                "fact_consumo_horario"; "fact_factura"; }
            "analytics.vw_consumo_analitico" [fillcolor="#d9ead3"];
            "out/*.csv|json" [fillcolor="#fff2cc"];
            "consumos/*.csv" -> "parser por contenido"
                -> "consumo_lectura";
            "maestros/*.csv" -> "dim_cliente/dim_frontera";
            "maestros/*.csv" -> "limite_frontera";
            "facturacion/*.json" -> "fact_factura";
            "parser por contenido" -> "cuarentena" [label="rechazos"];
            "consumo_lectura" -> "ranking B3 (dedup)"
                -> "fact_consumo_horario";
            "fact_consumo_horario" -> "analytics.vw_consumo_analitico";
            "analytics.vw_consumo_analitico" -> "Streamlit (rol_analista)";
            "consumo_lectura" -> "control_archivos" [label="sha256"];
            "mediciones_stream.ndjson" -> "producer" -> "topic mediciones.raw"
                -> "stream_runner" -> "out/*.csv|json";
            "fact_consumo_horario" -> "out/*.csv|json" [label="reportes"];
        }""")
        st.markdown("""
- **Descubrimiento** por `data/consumos/*.csv` — sin lista fija de meses.
- **Staging inmutable** + SHA-256 en `control_archivos` → idempotencia y
  procesamiento incremental (solo lo nuevo/cambiado).
- **Fact reconstruido** por ranking `version_lectura → orden_archivo →
  fila_num`: absorbe `*_correccion.csv` sin releer todo y el resultado
  es determinista.
- **Cuarentena** como estado vigente de rechazos; la historia de corridas
  vive en `audit.bitacora`.""")

    elif persp == "Seguridad y control de acceso":
        st.graphviz_chart("""
        digraph {
            rankdir=TB; node [shape=box, style="rounded,filled",
                              fillcolor="#fce5cd", fontname="Segoe UI"];
            subgraph cluster_roles {
                label="Roles Postgres (menor privilegio)";
                style=dashed;
                "rol_ingesta"; "rol_analista"; "rol_auditor"; }
            "rol_ingesta" -> "stage.* (CRUD)" [color="#6aa84f"];
            "rol_ingesta" -> "core.dim_cliente (solo cols no-PII)"
                [color="#6aa84f"];
            "rol_ingesta" -> "audit.bitacora (solo INSERT)"
                [color="#6aa84f"];
            "rol_analista" -> "analytics.vw_consumo_analitico (SELECT)"
                [color="#3c78d8"];
            "rol_auditor" -> "audit.bitacora (SELECT)" [color="#3c78d8"];
            subgraph cluster_crypto { label="Criptografía en reposo";
                style=dashed;
                "AES-256-GCM"; "HMAC-SHA256"; }
            "PII_ENCRYPTION_KEY (.env)" -> "AES-256-GCM"
                -> "nit/email/celular/direccion/representante cifrados";
            "PII_ENCRYPTION_KEY (.env)" -> "HMAC-SHA256"
                -> "nit_hash (índice ciego p/joins)";
            "trigger + REVOKE" -> "audit.bitacora" [label="append-only"];
        }""")
        st.markdown("""
- **rol_ingesta** escribe PII cifrada pero **no puede leerla** (grant a
  nivel columna solo sobre columnas no-PII).
- **rol_analista** solo lee la vista — cero columnas PII, ni en claro ni
  cifradas. Es el rol que usa este dashboard.
- **rol_auditor** solo lee `audit.bitacora`.
- **Append-only doble**: `REVOKE UPDATE/DELETE` + trigger que rechaza
  cualquier modificación de la bitácora.
- **Índice ciego** (HMAC) permite deduplicar/join por NIT sin guardar el
  valor en claro.""")

    elif persp == "Despliegue local (Docker)":
        st.graphviz_chart("""
        digraph {
            rankdir=LR; node [shape=box, style="rounded,filled",
                              fillcolor="#ead1dc", fontname="Segoe UI"];
            subgraph cluster_host { label="Host (Windows)"; style=dashed;
                "src.pipeline"; "src.stream_runner"; "tools.producer";
                "Streamlit :8501"; }
            subgraph cluster_docker { label="docker compose"; style=dashed;
                "bia_pg Postgres 16 (:55432 a 5432)";
                "bia_redpanda Redpanda (:9092)"; }
            "src.pipeline" -> "bia_pg Postgres 16 (:55432 a 5432)";
            "Streamlit :8501" -> "bia_pg Postgres 16 (:55432 a 5432)"
                [label="rol_analista"];
            "tools.producer" -> "bia_redpanda Redpanda (:9092)";
            "src.stream_runner" -> "bia_redpanda Redpanda (:9092)"
                [label="consume mediciones.raw"];
        }""")
        st.markdown("""
- **Postgres 16 Alpine** publicado en `:55432` (los Postgres nativos de la
  máquina ocupan 5432/5433 — el puerto host viene de `PG_PORT` en `.env`).
- **Redpanda** (API Kafka) en `:9092` para el stream; el consumidor
  auto-siembra el tópico si está vacío y termina por idle-timeout.
- `.env` con credenciales aleatorias se genera en `make up` —
  nada queda en claro en el repo.
- MinIO del PoC se eliminó: la imagen ya no se publica y no hacía parte
  del flujo.""")

    else:  # Cloud AWS
        st.graphviz_chart("""
        digraph {
            rankdir=LR; node [shape=box, style="rounded,filled",
                              fillcolor="#d0e0e3", fontname="Segoe UI"];
            "Fuentes on-prem" -> "S3 raw (SSE-KMS, privado, versionado)"
                -> "Glue/Lambda ingest" -> "RDS Postgres";
            "MSK (Kafka)" -> "ECS consumer" -> "SNS alertas";
            "RDS Postgres" -> "QuickSight/BI";
            "KMS CMK" -> "S3 raw (SSE-KMS, privado, versionado)";
            "KMS CMK" -> "RDS Postgres";
            "IAM roles" -> "Glue/Lambda ingest" [style=dashed];
            "IAM roles" -> "ECS consumer" [style=dashed];
            "CloudTrail/CloudWatch" -> "audit append-only";
        }""")
        st.markdown("""
- **Equivalente cloud** modelado en `infra/main.tf`: bucket S3 privado,
  versionado y cifrado con CMK KMS; roles IAM de menor privilegio;
  outputs listos para conectar el pipeline.
- **Llave PII** (`PII_ENCRYPTION_KEY` hoy en `.env`) sería la CMK de KMS —
  mismo esquema AES-GCM, gestión real de llaves.
- El `make` local se mapea a ECS/Lambda; Redpanda → MSK; alertas → SNS.""")

    st.divider()
    st.caption("Detalle de decisiones en `DECISIONS.md`, controles en "
               "`SECURITY.md` y amenazas en `THREAT_MODEL.md`.")
