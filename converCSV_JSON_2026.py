# csv_to_json.py

import json
import numpy as np
import pandas as pd
from pathlib import Path

# ==========================================================
# CONFIG
# ==========================================================

CSV_PATH = Path("Zin_dipolo_plata_GHz_optimizado_4_bordes.csv")
OUT_JSON = Path("dataDipoloPlata2.json")

# Velocidad real de la luz
c = 299792458

# ==========================================================
# CARGA CSV
# ==========================================================

df = pd.read_csv(CSV_PATH)

# Limpiar nombres
df.columns = [col.strip() for col in df.columns]

# ==========================================================
# VALIDACION
# ==========================================================

required_cols = {"L", "a", "f", "Rin", "Xin"}

missing = required_cols - set(df.columns)

if missing:
    raise ValueError(f"Faltan columnas: {missing}")

# ==========================================================
# MATERIAL DEFAULT
# ==========================================================

if "material" not in df.columns:
    df["material"] = "silver"  # Valor por defecto, ajustar si es necesario

# ==========================================================
# NORMALIZACION NUMERICA
# ==========================================================

numeric_cols = ["L", "a", "f", "Rin", "Xin"]

for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df = df.dropna()

# ==========================================================
# RECORDS
# ==========================================================

records = []

# ==========================================================
# GROUP BY
# ==========================================================

grouped = df.groupby(["material", "L", "a"])

total_groups = len(grouped)

print(f"Procesando {total_groups} geometrías...")

# ==========================================================
# LOOP
# ==========================================================

for idx, ((material, L, a), g) in enumerate(grouped):

    # ------------------------------------------------------
    # Ordenar por frecuencia
    # ------------------------------------------------------

    g = g.sort_values("f")

    # ------------------------------------------------------
    # Arrays
    # ------------------------------------------------------

    f_vals = g["f"].to_numpy(dtype=np.float64)

    Rin = g["Rin"].to_numpy(dtype=np.float32)

    Xin = g["Xin"].to_numpy(dtype=np.float32)

    # ------------------------------------------------------
    # Resonancia aproximada
    # ------------------------------------------------------

    idx_res = int(np.argmin(np.abs(Xin)))

    f_res = float(f_vals[idx_res])

    # ------------------------------------------------------
    # Frecuencia teorica
    # ------------------------------------------------------

    f0 = float(c / (2 * L))

    # ------------------------------------------------------
    # Compresion numerica
    # ------------------------------------------------------

    freq_list = np.round(f_vals, 2).tolist()

    Rin_list = np.round(Rin, 4).tolist()

    Xin_list = np.round(Xin, 4).tolist()

    # ------------------------------------------------------
    # Item JSON
    # ------------------------------------------------------

    item = {

        # --------------------------------------------------
        # Geometria
        # --------------------------------------------------

        "material": str(material),

        "geometry": {

            "L": round(float(L), 6),

            "a": round(float(a), 6)

        },

        # --------------------------------------------------
        # Frecuencias
        # --------------------------------------------------

        "f0": round(f0, 2),

        "f_res": round(f_res, 2),

        # --------------------------------------------------
        # Curvas
        # --------------------------------------------------

        "freq": freq_list,

        "Rin": Rin_list,

        "Xin": Xin_list,

        # --------------------------------------------------
        # Metadata
        # --------------------------------------------------

        "meta": {

            "f_min": round(float(f_vals.min()), 2),

            "f_max": round(float(f_vals.max()), 2),

            "points": int(len(f_vals)),

            "source": CSV_PATH.name

        }

    }

    records.append(item)

    # ------------------------------------------------------
    # Progreso
    # ------------------------------------------------------

    if (idx + 1) % 50 == 0 or (idx + 1) == total_groups:

        print(
            f"[{idx+1}/{total_groups}] "
            f"L={L:.4f} "
            f"a={a:.6f}"
        )

# ==========================================================
# EXPORT JSON
# ==========================================================

with OUT_JSON.open("w", encoding="utf-8") as f:

    json.dump(
        records,
        f,
        ensure_ascii=False,
        separators=(",", ":")
    )

# ==========================================================
# STATS
# ==========================================================

size_mb = OUT_JSON.stat().st_size / (1024 * 1024)

print("\n===================================")

print(f"OK -> {OUT_JSON}")

print(f"Geometrías: {len(records)}")

print(f"Tamaño JSON: {size_mb:.2f} MB")

print("===================================")