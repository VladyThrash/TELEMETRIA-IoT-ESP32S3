#Transforma la serie temporal capturada en el vector fijo de 11 variables biomecánicas usando las etiquetas de movimiento generadas 
#por el firmware (dorsiflexion, plantarflexion, inversion, eversion, neutro).

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "rom_dorsiflexion",
    "rom_plantarflexion",
    "rom_inversion",
    "rom_eversion",
    "proporcion_activa",
    "balance_movimientos",
    "vel_angular_media",
    "vel_angular_std",
    "yaw_std",
    "cadencia_rpm",
    "indice_fatiga",
]

ACTIVE_MOVES = ["dorsiflexion", "plantarflexion", "inversion", "eversion"]


def extraer_caracteristicas_sesion(samples: list[dict], duracion_seg: float) -> tuple[dict, dict]:
    df = pd.DataFrame(samples)

    pos_pitch = df.loc[df["pitch"] > 0, "pitch"]
    neg_pitch = df.loc[df["pitch"] < 0, "pitch"]
    neg_roll = df.loc[df["roll"] < 0, "roll"]
    pos_roll = df.loc[df["roll"] > 0, "roll"]

    rom_dorsiflexion = float(np.percentile(pos_pitch, 95)) if len(pos_pitch) else 0.0
    rom_plantarflexion = float(abs(np.percentile(neg_pitch, 5))) if len(neg_pitch) else 0.0
    rom_inversion = float(abs(np.percentile(neg_roll, 5))) if len(neg_roll) else 0.0
    rom_eversion = float(np.percentile(pos_roll, 95)) if len(pos_roll) else 0.0

    activos = df[df["movimiento"] != "neutro"]
    proporcion_activa = float(len(activos) / len(df)) if len(df) else 0.0

    # Entropía normalizada entre los 4 movimientos activos (0 = un solo eje, 1 = equilibrio total)
    counts = np.array([(df["movimiento"] == m).sum() for m in ACTIVE_MOVES], dtype=float)
    if counts.sum() > 0:
        probs = counts / counts.sum()
        probs_nz = probs[probs > 0]
        entropia = -np.sum(probs_nz * np.log2(probs_nz))
        balance_movimientos = float(entropia / np.log2(len(ACTIVE_MOVES)))
    else:
        balance_movimientos = 0.0

    if len(activos) > 1:
        vel_angular_media = float(activos["vel_angular"].mean())
        vel_angular_std = float(activos["vel_angular"].std(ddof=0))
        yaw_std = float(activos["yaw"].std(ddof=0))
    else:
        vel_angular_media = float(df["vel_angular"].mean())
        vel_angular_std = float(df["vel_angular"].std(ddof=0))
        yaw_std = float(df["yaw"].std(ddof=0))

    # Repeticiones: transiciones desde 'neutro' hacia un movimiento activo
    estados = df["movimiento"].tolist()
    reps = sum(
        1 for i in range(1, len(estados))
        if estados[i - 1] == "neutro" and estados[i] != "neutro"
    )
    minutos = max(duracion_seg / 60.0, 1e-3)
    cadencia_rpm = float(reps / minutos)

    # Índice de fatiga: caída porcentual de amplitud angular entre 1ra y 2da mitad
    mid = len(df) // 2
    if mid > 5:
        amp_1 = float(np.hypot(df["pitch"].iloc[:mid], df["roll"].iloc[:mid]).quantile(0.90))
        amp_2 = float(np.hypot(df["pitch"].iloc[mid:], df["roll"].iloc[mid:]).quantile(0.90))
        indice_fatiga = float(max(0.0, ((amp_1 - amp_2) / max(amp_1, 1.0)) * 100.0))
    else:
        indice_fatiga = 0.0

    features = {
        "rom_dorsiflexion": round(rom_dorsiflexion, 2),
        "rom_plantarflexion": round(rom_plantarflexion, 2),
        "rom_inversion": round(rom_inversion, 2),
        "rom_eversion": round(rom_eversion, 2),
        "proporcion_activa": round(proporcion_activa, 4),
        "balance_movimientos": round(balance_movimientos, 4),
        "vel_angular_media": round(vel_angular_media, 2),
        "vel_angular_std": round(vel_angular_std, 2),
        "yaw_std": round(yaw_std, 2),
        "cadencia_rpm": round(cadencia_rpm, 2),
        "indice_fatiga": round(indice_fatiga, 2),
    }

    dist_movimientos = {
        m: round(float((df["movimiento"] == m).mean() * 100.0), 1)
        for m in (ACTIVE_MOVES + ["neutro"])
    }
    resumen_extra = {
        "total_muestras": len(df),
        "repeticiones_detectadas": reps,
        "distribucion_porcentaje": dist_movimientos,
    }

    return features, resumen_extra