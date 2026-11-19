import pandas as pd
import streamlit as st
from db import (
    autenticar_usuario,
    guardar_sesion_completa,
    init_db,
    obtener_dataset_entrenamiento,
    obtener_historial_usuario,
    registrar_usuario,
)
from features import extraer_caracteristicas_sesion
from ml_model import entrenar_modelo, predecir_calidad
from mqtt_worker import TelemetryWorker

st.set_page_config(page_title="Telemetría Biomecánica del Pie", layout="wide")


@st.cache_resource
def bootstrap_services() -> TelemetryWorker:
    init_db()
    worker = TelemetryWorker()
    worker.start()
    return worker


worker = bootstrap_services()

if "user" not in st.session_state:
    st.session_state.user = None
if "ultima_sesion_evaluada" not in st.session_state:
    st.session_state.ultima_sesion_evaluada = None


# --- VISTA DE AUTENTICACIÓN ---
if st.session_state.user is None:
    st.title("Sistema IoT de Telemetría Biomecánica del Pie")
    st.caption("ESP32-S3 + MPU6050 | Monitoreo de Rehabilitación y Evaluación ML")

    tab_login, tab_reg = st.tabs(["Iniciar Sesión", "Crear Cuenta"])

    with tab_login:
        with st.form("login_form"):
            u_login = st.text_input("Usuario")
            p_login = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar", type="primary"):
                user = autenticar_usuario(u_login, p_login)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")

    with tab_reg:
        with st.form("register_form"):
            nombre_reg = st.text_input("Nombre completo")
            u_reg = st.text_input("Usuario")
            p_reg = st.text_input("Contraseña", type="password")
            rol_reg = st.selectbox("Rol", ["paciente", "terapeuta"])
            if st.form_submit_button("Registrar"):
                ok, msg = registrar_usuario(u_reg, p_reg, nombre_reg, rol_reg)
                if ok:
                    st.success(msg + " Ya puedes iniciar sesión.")
                else:
                    st.error(msg)
    st.stop()


# --- BARRA LATERAL ---
user = st.session_state.user
with st.sidebar:
    st.subheader(user["nombre_completo"])
    st.caption(f"Usuario: `{user['username']}` | Rol: **{user['rol'].upper()}**")
    st.divider()
    if worker.is_sensor_online():
        st.success("Sensor ESP32-S3: EN LÍNEA (50 Hz)")
    else:
        st.warning("Sensor ESP32-S3: SIN SEÑAL MQTT")
        if worker.last_error:
            st.caption(f"Detalle: `{worker.last_error}`")

    st.divider()
    if st.button("Cerrar Sesión", use_container_width=True):
        st.session_state.user = None
        st.session_state.ultima_sesion_evaluada = None
        st.rerun()


st.title("Panel de Rehabilitación del Pie")
tab_live, tab_hist = st.tabs(["Terapia en Vivo", "Historial y Progreso"])


# --- TAB 1: TERAPIA EN VIVO ---
with tab_live:
    _, is_rec, rec_count = worker.get_preview_snapshot()

    col_ctrl1, col_ctrl2 = st.columns([1, 2])
    with col_ctrl1:
        if not is_rec:
            if st.button("Iniciar Sesión de Terapia", type="primary", use_container_width=True):
                st.session_state.ultima_sesion_evaluada = None
                worker.start_session()
                st.rerun()
        else:
            if st.button("Finalizar y Evaluar Sesión", type="primary", use_container_width=True):
                captura = worker.stop_session()
                if len(captura["samples"]) < 15:
                    st.warning("Se capturaron muy pocas muestras. Verifica que el sensor esté enviando datos.")
                else:
                    feats, resumen = extraer_caracteristicas_sesion(
                        captura["samples"], captura["duracion_seg"]
                    )
                    calidad_ia = predecir_calidad(feats)
                    st.session_state.ultima_sesion_evaluada = {
                        "inicio_ts": captura["inicio_ts"],
                        "fin_ts": captura["fin_ts"],
                        "duracion_seg": captura["duracion_seg"],
                        "features": feats,
                        "resumen": resumen,
                        "calidad_predicha": calidad_ia,
                    }
                st.rerun()

    with col_ctrl2:
        if is_rec:
            st.info(f"Grabando sesión libre... Muestras capturadas: **{rec_count}**")
        else:
            st.caption("Modo monitoreo: la placa transmite continuamente, pero solo se guarda al iniciar terapia.")

    @st.fragment(run_every=0.15)
    def render_live_telemetry():
        buf, recording, count = worker.get_preview_snapshot()
        if not buf:
            st.info("Esperando tramas JSON en el tópico `telemetria/pie`...")
            return

        ultimo = buf[-1]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Pitch (°)", f"{ultimo['pitch']:.1f}")
        m2.metric("Roll (°)", f"{ultimo['roll']:.1f}")
        m3.metric("Yaw (°)", f"{ultimo['yaw']:.1f}")
        m4.metric("Vel. Angular (°/s)", f"{ultimo['vel_angular']:.1f}")
        m5.metric("Movimiento Activo", ultimo["movimiento"].upper())

        df_live = pd.DataFrame(buf)[["pitch", "roll", "yaw"]]
        st.line_chart(df_live, height=240)

    render_live_telemetry()

    # --- RESULTADO DE REGRESIÓN Y GUARDADO ---
    evaluacion = st.session_state.ultima_sesion_evaluada
    if evaluacion is not None:
        st.divider()
        st.subheader("Evaluación Biomecánica de la Sesión")

        c_score, c_rom, c_ctrl = st.columns(3)
        feats = evaluacion["features"]
        resumen = evaluacion["resumen"]

        with c_score:
            st.metric("Calidad Inferida (Regresión)", f"{evaluacion['calidad_predicha']:.1f} / 100")
            st.caption(
                f"Duración: **{evaluacion['duracion_seg']:.1f} s** | "
                f"Muestras: **{resumen['total_muestras']}** | "
                f"Repeticiones: **{resumen['repeticiones_detectadas']}**"
            )

        with c_rom:
            st.markdown("**Rango Articular Efectivo (ROM)**")
            st.write(
                f"- Dorsiflexión: **{feats['rom_dorsiflexion']}°**\n"
                f"- Flexión plantar: **{feats['rom_plantarflexion']}°**\n"
                f"- Inversión: **{feats['rom_inversion']}°**\n"
                f"- Eversión: **{feats['rom_eversion']}°**"
            )

        with c_ctrl:
            st.markdown("**Control Motor y Ritmo**")
            st.write(
                f"- Tiempo activo: **{feats['proporcion_activa']*100:.1f}%**\n"
                f"- Balance ejercicios: **{feats['balance_movimientos']*100:.1f}%**\n"
                f"- Cadencia: **{feats['cadencia_rpm']:.1f} rep/min**\n"
                f"- Índice fatiga: **{feats['indice_fatiga']:.1f}%**"
            )

        with st.form("save_session_form"):
            calidad_val = st.slider(
                "Calificación clínica validada (ajusta si deseas retroalimentar el modelo):",
                min_value=0.0,
                max_value=100.0,
                value=float(evaluacion["calidad_predicha"]),
                step=0.5,
            )
            notas = st.text_area("Observaciones de la sesión", placeholder="Ej. Sin molestia en dorsiflexión...")
            reentrenar = st.checkbox("Reentrenar modelo de regresión al guardar", value=True)

            if st.form_submit_button("Guardar Sesión en PostgreSQL", type="primary"):
                sesion_id = guardar_sesion_completa(
                    usuario_id=user["id"],
                    inicio_ts=evaluacion["inicio_ts"],
                    fin_ts=evaluacion["fin_ts"],
                    duracion_seg=evaluacion["duracion_seg"],
                    calidad_predicha=evaluacion["calidad_predicha"],
                    calidad_validada=calidad_val,
                    notas=notas,
                    features=feats,
                    resumen_extra=resumen,
                )
                if reentrenar:
                    dataset = obtener_dataset_entrenamiento()
                    entrenar_modelo(dataset)
                st.session_state.ultima_sesion_evaluada = None
                st.success(f"Sesión #{sesion_id} guardada correctamente.")
                st.rerun()


# --- TAB 2: HISTORIAL Y PROGRESO ---
with tab_hist:
    historial = obtener_historial_usuario(user["id"])
    if not historial:
        st.info("Aún no tienes sesiones guardadas. Inicia una terapia en la pestaña anterior.")
    else:
        df_hist = pd.DataFrame(historial)
        for col in [
            "calidad_predicha", "calidad_validada", "duracion_seg",
            "rom_dorsiflexion", "rom_plantarflexion", "rom_inversion", "rom_eversion",
        ]:
            df_hist[col] = df_hist[col].astype(float)

        df_plot = df_hist.sort_values("inicio_ts").set_index("inicio_ts")

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.markdown("**Evolución de Calidad de Sesión**")
            st.line_chart(df_plot[["calidad_predicha", "calidad_validada"]], height=250)
        with col_g2:
            st.markdown("**Progreso de Rango Articular (ROM °)**")
            st.line_chart(
                df_plot[["rom_dorsiflexion", "rom_plantarflexion", "rom_inversion", "rom_eversion"]],
                height=250,
            )

        st.markdown("**Registro Detallado de Sesiones**")
        st.dataframe(
            df_hist[[
                "id", "inicio_ts", "duracion_seg", "calidad_predicha", "calidad_validada",
                "rom_dorsiflexion", "rom_plantarflexion", "rom_inversion", "rom_eversion",
                "cadencia_rpm", "notas",
            ]],
            use_container_width=True,
            hide_index=True,
        )