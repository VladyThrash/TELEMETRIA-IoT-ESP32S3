#Entrena un pipeline StandardScaler + Ridge en /app/models/calidad_modelo.joblib. Arranca con 80 perfiles sintéticos calibrados a 
#los umbrales del firmware (> 10° dorsiflexión, > 12° flexión plantar, > 10° inversión/eversión) y se reentrena ponderando las 
#sesiones reales validadas en PostgreSQL.

import os
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from features import FEATURE_COLUMNS

MODEL_DIR = os.getenv("MODEL_DIR", "/app/models")
MODEL_PATH = os.path.join(MODEL_DIR, "calidad_modelo.joblib")


def _generar_dataset_semilla(n_samples: int = 80, seed: int = 42) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    rows = []
    targets = []

    for _ in range(n_samples):
        nivel = rng.uniform(0.2, 1.0)  # Calidad latente de ejecución
        rom_df = rng.uniform(4.0, 26.0) * (0.4 + 0.6 * nivel)
        rom_pf = rng.uniform(5.0, 32.0) * (0.4 + 0.6 * nivel)
        rom_inv = rng.uniform(4.0, 24.0) * (0.4 + 0.6 * nivel)
        rom_ev = rng.uniform(4.0, 22.0) * (0.4 + 0.6 * nivel)

        prop_act = np.clip(rng.normal(0.35 + 0.45 * nivel, 0.08), 0.05, 0.95)
        balance = np.clip(rng.normal(0.30 + 0.60 * nivel, 0.10), 0.0, 1.0)
        vel_media = np.clip(rng.normal(38.0 - 10.0 * (1.0 - nivel), 8.0), 5.0, 110.0)
        vel_std = np.clip(rng.normal(28.0 * (1.15 - nivel), 4.0), 2.0, 50.0)
        yaw_std = np.clip(rng.normal(14.0 * (1.15 - nivel), 2.5), 0.5, 30.0)
        cadencia = np.clip(rng.normal(6.0 + 10.0 * nivel, 2.5), 1.0, 28.0)
        fatiga = np.clip(rng.normal(25.0 * (1.1 - nivel), 5.0), 0.0, 50.0)

        # Fórmula clínica de referencia (0 a 100)
        score_rom = (
            min(rom_df / 18.0, 1.2)
            + min(rom_pf / 22.0, 1.2)
            + min(rom_inv / 16.0, 1.2)
            + min(rom_ev / 15.0, 1.2)
        ) / 4.8 * 40.0
        score_control = max(0.0, 25.0 - (vel_std * 0.45 + yaw_std * 0.65))
        score_constancia = (prop_act * 15.0) + (balance * 12.0) + max(0.0, 8.0 - fatiga * 0.25)
        score = float(np.clip(score_rom + score_control + score_constancia + rng.normal(0, 2.0), 0.0, 100.0))

        rows.append([
            rom_df, rom_pf, rom_inv, rom_ev,
            prop_act, balance, vel_media, vel_std,
            yaw_std, cadencia, fatiga,
        ])
        targets.append(score)

    return pd.DataFrame(rows, columns=FEATURE_COLUMNS), np.array(targets)


def entrenar_modelo(sesiones_reales: list[dict] | None = None) -> Pipeline:
    os.makedirs(MODEL_DIR, exist_ok=True)
    X_seed, y_seed = _generar_dataset_semilla()
    weights = np.ones(len(X_seed))

    if sesiones_reales:
        df_real = pd.DataFrame(sesiones_reales)
        for col in FEATURE_COLUMNS + ["target"]:
            df_real[col] = df_real[col].astype(float)
        X_real = df_real[FEATURE_COLUMNS]
        y_real = df_real["target"].to_numpy()

        X = pd.concat([X_seed, X_real], ignore_index=True)
        y = np.concatenate([y_seed, y_real])
        # Las sesiones validadas reales pesan 5x más que los ejemplos semilla
        weights = np.concatenate([weights, np.full(len(X_real), 5.0)])
    else:
        X, y = X_seed, y_seed

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("regressor", Ridge(alpha=1.5)),
    ])
    pipeline.fit(X, y, regressor__sample_weight=weights)
    joblib.dump(pipeline, MODEL_PATH)
    return pipeline


def cargar_o_crear_modelo() -> Pipeline:
    if os.path.exists(MODEL_PATH):
        return joblib.load(MODEL_PATH)
    return entrenar_modelo()


def predecir_calidad(features: dict) -> float:
    modelo = cargar_o_crear_modelo()
    df_in = pd.DataFrame([[float(features[c]) for c in FEATURE_COLUMNS]], columns=FEATURE_COLUMNS)
    pred = float(modelo.predict(df_in)[0])
    return round(float(np.clip(pred, 0.0, 100.0)), 2)