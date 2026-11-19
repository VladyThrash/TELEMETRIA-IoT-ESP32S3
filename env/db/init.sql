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

CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones_terapia(usuario_id, inicio_ts DESC);