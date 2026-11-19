#Incluye inicialización automática de tablas (init_db) para garantizar que el esquema exista sin importar cómo se creó el 
#volumen de PostgreSQL.

import os
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor, Json


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "terapia_pie"),
        user=os.getenv("DB_USER", "bio_user"),
        password=os.getenv("DB_PASSWORD", "bio_password_2026"),
    )


def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        nombre_completo VARCHAR(120) NOT NULL,
        rol VARCHAR(20) NOT NULL DEFAULT 'paciente' CHECK (rol IN ('paciente', 'terapeuta')),
        creado_en TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sesiones_terapia (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        inicio_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        fin_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        duracion_seg NUMERIC(8, 2) NOT NULL,
        calidad_predicha NUMERIC(5, 2),
        calidad_validada NUMERIC(5, 2),
        notas TEXT
    );

    CREATE TABLE IF NOT EXISTS metricas_sesion (
        id SERIAL PRIMARY KEY,
        sesion_id INTEGER UNIQUE NOT NULL REFERENCES sesiones_terapia(id) ON DELETE CASCADE,
        rom_dorsiflexion NUMERIC(6, 2),
        rom_plantarflexion NUMERIC(6, 2),
        rom_inversion NUMERIC(6, 2),
        rom_eversion NUMERIC(6, 2),
        proporcion_activa NUMERIC(5, 4),
        balance_movimientos NUMERIC(5, 4),
        vel_angular_media NUMERIC(6, 2),
        vel_angular_std NUMERIC(6, 2),
        yaw_std NUMERIC(6, 2),
        cadencia_rpm NUMERIC(6, 2),
        indice_fatiga NUMERIC(6, 2),
        resumen_json JSONB
    );

    CREATE INDEX IF NOT EXISTS idx_sesiones_usuario
        ON sesiones_terapia(usuario_id, inicio_ts DESC);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


def registrar_usuario(username: str, password: str, nombre_completo: str, rol: str = "paciente") -> tuple[bool, str]:
    username = username.strip().lower()
    if len(username) < 3 or len(password) < 4:
        return False, "Usuario (mín. 3 caracteres) o contraseña (mín. 4) demasiado cortos."

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO usuarios (username, password_hash, nombre_completo, rol)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (username, pw_hash, nombre_completo.strip(), rol),
                )
            conn.commit()
        return True, "Usuario registrado correctamente."
    except psycopg2.errors.UniqueViolation:
        return False, "Ese nombre de usuario ya existe."
    except Exception as exc:
        return False, f"Error de base de datos: {exc}"


def autenticar_usuario(username: str, password: str) -> dict | None:
    username = username.strip().lower()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
            user = cur.fetchone()
    if user and bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return dict(user)
    return None


def guardar_sesion_completa(
    usuario_id: int,
    inicio_ts,
    fin_ts,
    duracion_seg: float,
    calidad_predicha: float,
    calidad_validada: float,
    notas: str,
    features: dict,
    resumen_extra: dict,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sesiones_terapia
                    (usuario_id, inicio_ts, fin_ts, duracion_seg, calidad_predicha, calidad_validada, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    usuario_id,
                    inicio_ts,
                    fin_ts,
                    round(duracion_seg, 2),
                    round(calidad_predicha, 2),
                    round(calidad_validada, 2),
                    notas,
                ),
            )
            sesion_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO metricas_sesion (
                    sesion_id, rom_dorsiflexion, rom_plantarflexion, rom_inversion, rom_eversion,
                    proporcion_activa, balance_movimientos, vel_angular_media, vel_angular_std,
                    yaw_std, cadencia_rpm, indice_fatiga, resumen_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sesion_id,
                    features["rom_dorsiflexion"],
                    features["rom_plantarflexion"],
                    features["rom_inversion"],
                    features["rom_eversion"],
                    features["proporcion_activa"],
                    features["balance_movimientos"],
                    features["vel_angular_media"],
                    features["vel_angular_std"],
                    features["yaw_std"],
                    features["cadencia_rpm"],
                    features["indice_fatiga"],
                    Json(resumen_extra),
                ),
            )
        conn.commit()
    return sesion_id


def obtener_historial_usuario(usuario_id: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    s.id, s.inicio_ts, s.duracion_seg, s.calidad_predicha, s.calidad_validada, s.notas,
                    m.rom_dorsiflexion, m.rom_plantarflexion, m.rom_inversion, m.rom_eversion,
                    m.proporcion_activa, m.balance_movimientos, m.vel_angular_media,
                    m.vel_angular_std, m.yaw_std, m.cadencia_rpm, m.indice_fatiga
                FROM sesiones_terapia s
                JOIN metricas_sesion m ON m.sesion_id = s.id
                WHERE s.usuario_id = %s
                ORDER BY s.inicio_ts DESC
                """,
                (usuario_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def obtener_dataset_entrenamiento() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    m.rom_dorsiflexion, m.rom_plantarflexion, m.rom_inversion, m.rom_eversion,
                    m.proporcion_activa, m.balance_movimientos, m.vel_angular_media,
                    m.vel_angular_std, m.yaw_std, m.cadencia_rpm, m.indice_fatiga,
                    s.calidad_validada AS target
                FROM sesiones_terapia s
                JOIN metricas_sesion m ON m.sesion_id = s.id
                WHERE s.calidad_validada IS NOT NULL
                """
            )
            return [dict(row) for row in cur.fetchall()]