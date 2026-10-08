"""
GUI local (Streamlit) para gmaps-scraper. Funcional primero, estetica despues.

Correr con:
    .venv\\Scripts\\streamlit run app.py
"""
import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from gmaps_scraper import db
from gmaps_scraper.config import settings
from gmaps_scraper.geocode import get_city_bbox
from gmaps_scraper.grid import generate_grid, zoom_for_radius
from gmaps_scraper.worker import run_workers

LOG_PATH = settings.screenshot_dir.parent / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    force=True,
)

st.set_page_config(page_title="gmaps-scraper", layout="wide")
db.init_db()


@st.cache_resource
def worker_state():
    return {"thread": None, "running": False, "stop_event": threading.Event()}


def start_worker(concurrency: int) -> None:
    state = worker_state()
    if state["running"]:
        return
    state["stop_event"].clear()

    def _target():
        state["running"] = True
        try:
            asyncio.run(run_workers(concurrency, state["stop_event"]))
        finally:
            state["running"] = False

    thread = threading.Thread(target=_target, daemon=True)
    state["thread"] = thread
    thread.start()


def stop_worker() -> None:
    state = worker_state()
    state["stop_event"].set()


st.title("gmaps-scraper")

tab_queue, tab_new, tab_worker, tab_results = st.tabs(
    ["Cola de jobs", "Nueva busqueda", "Worker", "Resultados"]
)

with tab_queue:
    counts = db.job_counts()
    cols = st.columns(4)
    for col, status in zip(cols, ["pending", "running", "done", "error"]):
        col.metric(status, counts.get(status, 0))

    c1, c2, c3 = st.columns(3)
    if c1.button("Actualizar"):
        st.rerun()
    if c2.button("Reintentar jobs con error"):
        n = db.reset_error_jobs()
        st.success(f"{n} jobs puestos de nuevo en 'pending'")
        st.rerun()
    if c3.button("Borrar jobs terminados (done/error)"):
        n = db.clear_finished_jobs()
        st.success(f"{n} jobs borrados")
        st.rerun()

    jobs = db.list_jobs(300)

    STALL_SECONDS = 90
    now = datetime.now(timezone.utc)
    stalled = []
    for j in jobs:
        if j["status"] == "running":
            age = (now - datetime.fromisoformat(j["updated_at"])).total_seconds()
            if age > STALL_SECONDS:
                stalled.append((j, age))

    if stalled:
        st.warning(
            f"⚠️ {len(stalled)} job(s) llevan mas de {STALL_SECONDS}s en 'running' sin avanzar. "
            f"Puede ser una celda con muchos lugares (normal, revisa el progreso abajo) o un bloqueo de Google "
            f"(revisa el log en la pestaña Worker - deberia decir 'BLOQUEADO POR GOOGLE' si es eso)."
        )
        for j, age in stalled:
            done = j["places_done"] if j["places_done"] is not None else "?"
            found = j["places_found"] if j["places_found"] is not None else "?"
            st.caption(f"Job #{j['id']} '{j['query']}': {done}/{found} lugares, sin cambios hace {int(age)}s")

    errors = [j for j in jobs if j["status"] == "error"]
    if errors:
        with st.expander(f"❌ {len(errors)} job(s) con error - ver detalle"):
            for j in errors:
                st.markdown(f"**Job #{j['id']}** '{j['query']}' en '{j['location']}'")
                st.code(j["last_error"] or "(sin mensaje)", language=None)

    if jobs:
        df_jobs = pd.DataFrame([dict(j) for j in jobs])
        st.dataframe(df_jobs, use_container_width=True, height=400)
    else:
        st.info("No hay jobs encolados todavia. Ve a 'Nueva busqueda'.")

with tab_new:
    modo = st.radio(
        "Tipo de busqueda",
        ["Busqueda puntual", "Cobertura de ciudad (grilla)", "Dibujar zona en el mapa"],
    )

    if modo == "Busqueda puntual":
        with st.form("form_job"):
            query = st.text_input("Que buscar", placeholder="restaurantes")
            location = st.text_input("Ubicacion (texto)", placeholder="Bogota, Colombia")
            limit = st.number_input("Limite de resultados", min_value=1, max_value=500, value=settings.default_limit)
            submitted = st.form_submit_button("Encolar")
        if submitted:
            if not query.strip():
                st.error("Falta la busqueda")
            else:
                job_id = db.enqueue_job(query.strip(), location.strip() or None, int(limit))
                st.toast(f"Job #{job_id} encolado")
                st.rerun()

    elif modo == "Cobertura de ciudad (grilla)":
        radius_km = st.number_input(
            "Radio inicial de celda (km)", min_value=0.3, max_value=20.0, value=1.5, step=0.5,
            help="Zonas densas (centro de ciudad): usa un radio chico. Zonas dispersas: uno grande. "
                 "Si una celda sale saturada, se subdivide sola durante el run - no hace falta adivinar perfecto.",
        )
        st.caption(f"Zoom calculado automaticamente para este radio: **{zoom_for_radius(radius_km)}**")

        with st.form("form_city"):
            query = st.text_input("Que buscar", placeholder="restaurantes")
            city = st.text_input("Ciudad", placeholder="Bogota, Colombia")
            limit = st.number_input("Limite de resultados por celda", min_value=1, max_value=500, value=settings.default_limit)
            submitted = st.form_submit_button("Geocodificar y encolar grilla")
        if submitted:
            if not query.strip() or not city.strip():
                st.error("Faltan campos")
            else:
                try:
                    with st.spinner(f"Geocodificando '{city}'..."):
                        bbox = get_city_bbox(city.strip())
                        points = generate_grid(bbox, float(radius_km))
                    zoom = zoom_for_radius(radius_km)
                    for lat, lng in points:
                        db.enqueue_job(
                            query.strip(), city.strip(), int(limit),
                            latitude=lat, longitude=lng, zoom=zoom, radius_km=float(radius_km),
                        )
                    st.toast(f"{len(points)} jobs encolados cubriendo '{city}'")
                    st.rerun()
                except Exception as exc:
                    st.error(f"No se pudo geocodificar: {exc}")

    else:  # "Dibujar zona en el mapa"
        st.caption(
            "Usa el icono de rectangulo en la esquina superior izquierda del mapa "
            "para marcar la zona. Si dibujas otra, la ultima reemplaza a la anterior."
        )

        saved_bbox = st.session_state.get("drawn_bbox")
        center = st.session_state.get("last_bbox_center", [4.65, -74.1])
        m = folium.Map(location=center, zoom_start=11, tiles="OpenStreetMap")
        Draw(
            export=False,
            draw_options={
                "rectangle": True,
                "polygon": False,
                "circle": False,
                "circlemarker": False,
                "marker": False,
                "polyline": False,
            },
            edit_options={"edit": True, "remove": True},
        ).add_to(m)
        if saved_bbox:
            s, n, w, e = saved_bbox
            folium.Rectangle(
                bounds=[[s, w], [n, e]], color="#d33", weight=2, fill=True, fill_opacity=0.1
            ).add_to(m)

        map_data = st_folium(m, height=450, use_container_width=True, key="draw_map")

        if map_data and map_data.get("last_active_drawing"):
            coords = map_data["last_active_drawing"]["geometry"]["coordinates"][0]
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            new_bbox = (min(lats), max(lats), min(lons), max(lons))
            if new_bbox != saved_bbox:
                st.session_state["drawn_bbox"] = new_bbox
                st.session_state["last_bbox_center"] = [(new_bbox[0] + new_bbox[1]) / 2, (new_bbox[2] + new_bbox[3]) / 2]
                st.rerun()

        bbox = st.session_state.get("drawn_bbox")

        if bbox and st.button("Borrar zona seleccionada"):
            del st.session_state["drawn_bbox"]
            st.rerun()

        if not bbox:
            st.info("Dibuja un rectangulo en el mapa para continuar.")
        else:
            south, north, west, east = bbox
            st.success(f"Zona: lat {south:.4f} a {north:.4f} | lng {west:.4f} a {east:.4f}")

            query = st.text_input("Que buscar", placeholder="restaurantes", key="draw_query")
            radius_km = st.number_input(
                "Radio inicial de celda (km)", min_value=0.3, max_value=20.0, value=1.5, step=0.5, key="draw_radius",
                help="Si una celda sale saturada, se subdivide sola durante el run - no hace falta adivinar perfecto.",
            )
            st.caption(f"Zoom calculado automaticamente para este radio: **{zoom_for_radius(radius_km)}**")
            limit = st.number_input("Limite de resultados por celda", min_value=1, max_value=500, value=settings.default_limit, key="draw_limit")

            points = generate_grid(bbox, float(radius_km))
            st.caption(f"Esto generaria **{len(points)} celdas / jobs**.")

            if st.button("Encolar grilla sobre la zona dibujada"):
                if not query.strip():
                    st.error("Falta la busqueda")
                else:
                    label = f"zona dibujada ({south:.3f},{west:.3f})-({north:.3f},{east:.3f})"
                    zoom = zoom_for_radius(radius_km)
                    for lat, lng in points:
                        db.enqueue_job(
                            query.strip(), label, int(limit),
                            latitude=lat, longitude=lng, zoom=zoom, radius_km=float(radius_km),
                        )
                    st.toast(f"{len(points)} jobs encolados sobre la zona dibujada")
                    st.rerun()

with tab_worker:
    state = worker_state()

    st.write("Estado:", "🟢 corriendo" if state["running"] else "⚪ detenido")
    st.caption(f"Proxies configurados: {len(settings.proxies)} | Headless: {settings.headless}")

    blocked_jobs = [
        j for j in db.list_jobs(50)
        if j["status"] == "error" and j["last_error"] and "BLOQUE" in j["last_error"].upper()
    ]
    if blocked_jobs:
        st.error(
            f"🚫 Google bloqueo esta IP ({len(blocked_jobs)} job(s) afectados) - el worker se detuvo solo "
            f"para no seguir insistiendo. Opciones: esperar un rato antes de reintentar, o configurar "
            f"PROXIES en el .env. Usa 'Reintentar jobs con error' en Cola de jobs cuando quieras retomar."
        )

    concurrency = st.slider("Concurrencia (navegadores en paralelo)", 1, 10, settings.concurrency)

    c1, c2 = st.columns(2)
    if c1.button("Iniciar worker", disabled=state["running"]):
        start_worker(concurrency)
        st.rerun()
    if c2.button("Detener worker", disabled=not state["running"]):
        stop_worker()
        st.info("Deteniendo... (termina el job actual de cada navegador, no corta a mitad)")

    st.subheader("Log en vivo")
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        st.code("\n".join(lines), language=None)
    else:
        st.caption("Aun no hay log.")

    auto = st.checkbox("Auto-actualizar cada 5s", value=state["running"])
    if auto:
        time.sleep(5)
        st.rerun()

with tab_results:
    total = db.count_places()
    st.metric("Lugares guardados (todas las busquedas)", total)

    groups = db.list_job_groups()
    options = ["Todas las busquedas"] + [
        f"{g['query']} — {g['location'] or '(sin ubicacion)'} — {g['n_jobs']} celda(s), {g['n_done']} completas — {g['created_at'][:16]}"
        for g in groups
    ]
    selected = st.selectbox("Filtrar por busqueda", options)

    if selected == "Todas las busquedas":
        with db.connect() as conn:
            df_places = pd.read_sql_query("SELECT * FROM places ORDER BY id DESC LIMIT 5000", conn)
        filename = "places_todo.csv"
    else:
        idx = options.index(selected) - 1
        g = groups[idx]
        job_ids = db.job_ids_for_group(g["query"], g["location"])
        if job_ids:
            placeholders = ",".join("?" * len(job_ids))
            with db.connect() as conn:
                df_places = pd.read_sql_query(
                    f"SELECT * FROM places WHERE job_id IN ({placeholders}) ORDER BY id DESC",
                    conn, params=job_ids,
                )
        else:
            df_places = pd.DataFrame()
        safe_query = "".join(c if c.isalnum() else "_" for c in g["query"])[:40]
        filename = f"places_{safe_query}.csv"

    st.caption(f"Mostrando **{len(df_places)}** lugares de esta seleccion.")

    if not df_places.empty:
        st.dataframe(df_places, use_container_width=True, height=500)
        st.download_button(
            f"Descargar CSV ({len(df_places)} lugares)",
            data=df_places.to_csv(index=False).encode("utf-8"),
            file_name=filename,
            mime="text/csv",
        )
    else:
        st.info("Todavia no hay resultados para esta seleccion.")
