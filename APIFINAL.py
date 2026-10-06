"""
VNA Virtual API  v2.1
"""

import io # Creacion de BUFFERS en memoria para generar en disco PNG
import hashlib # Generacion de claves para el cache de imagenes
import json # Soporte para JSON
import time # Para usar marcas de tiempo
import numpy as np # Para operaciones matematicas (ECOSISTEMA CIENTIFICO)
import matplotlib
matplotlib.use("Agg") # BACKEND NO INTERACTIVO
import matplotlib.pyplot as plt # Libreria para graficar
import skrf as rf # Libreria de RF (Carta de SMITH) CIRCULOS DE IMPEDANCIA NORMALIZADA
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import status
from pymongo import MongoClient # Driver de MongoDB para Python
from functools import lru_cache # Libreria para poder guardar datos en cache
from contextlib import asynccontextmanager # Manejador de contexto para iniciar o finalizar
import bcrypt # Libreria usada para encriptado de contrasenas en Login
from pydantic import BaseModel, EmailStr, Field # Libreria para validacion de datos y parseo
from typing import Optional, Dict # Tipos de datos (OPCIONAL || Diccionario)
from bson import ObjectId # Manejador de ID's de MongoDB

# ==========================================================
# CONSTANTES DE IMAGEN
# ==========================================================

IMG_W_IN  = 600 / 90 # ANCHO
IMG_H_IN  = 400 / 90 # ALTO
IMG_SMITH = 420 / 90 # PARA SMITH
IMG_DPI   = 90       # Resolucion de Puntos por pulgada
CHART_N   = 300      # Numero de Puntos a trazar

# ==========================================================
# ESTADO GLOBAL
# ==========================================================

state: Dict = {
    "barrido": False,
    "frecuencia_centro": None,
    "frecuencia_lateral": None,
    "unidad": "MHz",
    "unidad_centro": "MHz",
    "unidad_lateral": "MHz",
    "antennatype": "monopole",
    "material": "copper",
    "L": 0.02,
    "a": 0.001,
    "fmin_mhz": 300,
    "fmax_mhz": 6000,
    "n": CHART_N,
    "charttype": "s11",
    "last_esp32_event": None,
    "last_esp32_pin": None,
    "last_esp32_value": None,
    "last_esp32_ts": None,
    "last_esp32_ip": None,
    "last_esp32_rssi": None,
}

# ----------------------------------------------------------
# Configuración pendiente del barrido.
# Se acumula con cada config_barrido mientras barrido==True.
# Se aplica al desactivar el barrido.
# ----------------------------------------------------------
barrido_pending: Dict = {
    "frecuencia_centro":  None,   # → FMin
    "frecuencia_lateral": None,   # → FMax
    "unidad_centro":      "MHz",
    "unidad_lateral":     "MHz",
}


# ==========================================================
# VARIABLES GLOBALES
# ==========================================================

Z0 = 50.0                   # Impedancia caracteristica del SISTEMA 50 ohms
client = None               # Cliente
coll = None                 # 
antenna_coll = None         # 

ESP32_TIMEOUT_SECONDS = 10  # Timeout del ESP32

# ==========================================================
# CACHÉ DE IMÁGENES PNG
# ==========================================================

_image_cache: Dict[str, bytes] = {}
_IMAGE_CACHE_MAX = 64

def _cache_key(**kwargs) -> str:
    blob = json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()

def _cache_get(key: str) -> Optional[bytes]:
    return _image_cache.get(key)

def _cache_put(key: str, png: bytes):
    if len(_image_cache) >= _IMAGE_CACHE_MAX:
        oldest = next(iter(_image_cache))
        del _image_cache[oldest]
    _image_cache[key] = png

# ==========================================================
# GENERACION DE BYES DE FIGURA
# ==========================================================

def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=IMG_DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# ==========================================================
# CONVERSIÓN DE UNIDADES A MHz
# ==========================================================

_UNIT_TO_MHZ = {
    "khz": 1e-3,
    "mhz": 1.0,
    "ghz": 1e3,
}

def _to_mhz(value_str: str, unit: str) -> Optional[float]:
    """
    Convierte un valor numérico en string + unidad a MHz.
    Devuelve None si el valor no es convertible.
    """
    if not value_str or value_str.strip() in ("", "---"):
        return None
    try:
        val = float(value_str.strip())
    except (ValueError, TypeError):
        return None
    factor = _UNIT_TO_MHZ.get(unit.strip().lower(), 1.0)
    return round(val * factor, 6)

# ==========================================================
# LIFESPAN
# ==========================================================

@asynccontextmanager # Al iniciar la API y al cerrar (Ciclo de vida)
async def lifespan(app: FastAPI):
    global client, coll, antenna_coll
    client = MongoClient("mongodb://localhost:27017")
    db = client["vna_db"]
    coll = db["traces"]
    antenna_coll = db["antennas"]
    yield
    client.close()

app = FastAPI(title="VNA Virtual API", version="2.1", lifespan=lifespan)

# ==========================================================
# CONFIGURACION DE CORS (CROSS ORIGIN RESOURSE SHARING)
# ==========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # ADMITE DE TODOS LADOS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# NORMALIZACIÓN
# ==========================================================

def normalize_antenna_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    mapping = {"dipolo": "dipole", "dipole": "dipole",
               "monopolo": "monopole", "monopole": "monopole"}
    if normalized not in mapping:
        raise HTTPException(status_code=400,
                            detail="Tipo de antena inválido. Usa Monopolo o Dipolo")
    return mapping[normalized]

def normalize_material(value: str) -> str:
    normalized = (value or "").strip().lower()
    mapping = {"cobre": "copper", "copper": "copper",
               "plata": "silver", "silver": "silver"}
    if normalized not in mapping:
        raise HTTPException(status_code=400,
                            detail="Material inválido. Usa Cobre o Plata")
    return mapping[normalized]

def normalize_charttype(value: str) -> str:
    v = (value or "s11").strip().lower()
    return v if v in ("s11", "roe", "smith") else "s11"

# ==========================================================
# HELPERS
# ==========================================================

_ANTENNA_LABELS = {
    "monopole": {"es": "Monopolo", "en": "Monopole"},
    "dipole":   {"es": "Dipolo",   "en": "Dipole"},
}
_MATERIAL_LABELS = {
    "copper": {"es": "Cobre", "en": "Copper"},
    "silver": {"es": "Plata", "en": "Silver"},
}

def _fmt(mhz: float) -> str:
    return f"{mhz/1000:.3f} GHz" if mhz >= 1000 else f"{mhz:.0f} MHz"

def _bandwidth_mhz(f_mhz: np.ndarray, s11_db: np.ndarray, threshold_db=-10.0):
    below = s11_db <= threshold_db
    if not np.any(below):
        return None
    crossings = np.where(np.diff(below.astype(int)))[0]
    if len(crossings) == 0:
        return None
    f_low  = float(f_mhz[crossings[0]])
    f_high = float(f_mhz[crossings[-1] + 1]) if crossings[-1]+1 < len(f_mhz) else float(f_mhz[-1])
    return {"bw_mhz": round(f_high - f_low, 4),
            "f_low_mhz": f_low, "f_high_mhz": f_high,
            "threshold_db": threshold_db}

def _proximity_score(L_req, a_req, L_used, a_used) -> float:
    if L_req == 0 and a_req == 0:
        return 1.0
    dL = abs(L_req - L_used) / (L_req or 1.0)
    da = abs(a_req - a_used) / (a_req or 1.0)
    return round(max(0.0, 1.0 - (dL + da) / 2.0), 4)

# ==========================================================
# CACHE DE DOCUMENTOS LEGACY (/trace)
# ==========================================================

@lru_cache(maxsize=512)
def load_trace(material: str, L: float, a: float):
    if coll is None:
        return None
    return coll.find_one({"material": material, "L": float(L), "a": float(a)}, {"_id": 0})

def get_available_range(material: str, L: float, a: float):
    doc = load_trace(material, L, a)
    if not doc:
        return None
    if "meta" in doc:
        return float(doc["meta"]["f_MHz_min"]), float(doc["meta"]["f_MHz_max"])
    freq = np.array(doc["freq_MHz"], dtype=float)
    return float(freq.min()), float(freq.max())

def interpolate_and_compute(doc, fmin_hz, fmax_hz, n):
    fmin_mhz, fmax_mhz = fmin_hz, fmax_hz
    if fmin_mhz >= fmax_mhz:
        raise HTTPException(status_code=400, detail="fmin debe ser menor que fmax")
    freq_src = np.array(doc["freq_MHz"], dtype=float)
    R_src = np.array(doc["R_ohm"], dtype=float)
    X_src = np.array(doc["X_ohm"], dtype=float)
    lo = max(fmin_mhz, float(freq_src.min()))
    hi = min(fmax_mhz, float(freq_src.max()))
    if lo >= hi:
        raise HTTPException(status_code=400, detail=f"Sin intersección [{fmin_mhz}–{fmax_mhz}]")
    f_vec = np.linspace(lo, hi, n)
    R_i = np.interp(f_vec, freq_src, R_src)
    X_i = np.interp(f_vec, freq_src, X_src)
    Zin = R_i + 1j * X_i
    S11 = (Zin - Z0) / (Zin + Z0)
    mag = np.abs(S11)
    S11_dB = 20 * np.log10(np.clip(mag, 1e-12, 1.0))
    ROE = (1 + mag) / (1 - mag + 1e-12)
    return f_vec, Zin, S11, S11_dB, ROE, (lo, hi)

# ==========================================================
# CACHE Y BÚSQUEDA DE ANTENAS
# ==========================================================

def _antenna_doc_projection():
    return {"_id": 0, "antenna_type": 1, "material": 1, "geometry": 1,
            "freq": 1, "Rin": 1, "Xin": 1, "meta": 1, "f0": 1, "f_res": 1}

@lru_cache(maxsize=512)
def find_nearest_antenna_doc(antenna_type: str, material: str, L: float, a: float):
    if antenna_coll is None:
        return None, False, None
    at  = normalize_antenna_type(antenna_type)
    mat = normalize_material(material)
    proj = _antenna_doc_projection()
    doc = antenna_coll.find_one(
        {"antenna_type": at, "material": mat, "geometry.L": float(L), "geometry.a": float(a)}, proj)
    if doc is not None:
        return doc, False, None
    all_docs = list(antenna_coll.find({"antenna_type": at, "material": mat}, proj))
    if not all_docs:
        return None, False, None
    same_a = [d for d in all_docs if round(d["geometry"]["a"], 8) == round(a, 8)]
    if same_a:
        best = min(same_a, key=lambda d: abs(d["geometry"]["L"] - L))
        return best, True, {"strategy": "nearest_L_same_a",
                             "requested": {"L": L, "a": a},
                             "used": {"L": best["geometry"]["L"], "a": best["geometry"]["a"]}}
    L_vals = [d["geometry"]["L"] for d in all_docs]
    a_vals = [d["geometry"]["a"] for d in all_docs]
    L_range = max(L_vals) - min(L_vals) or 1.0
    a_range = max(a_vals) - min(a_vals) or 1.0
    best = min(all_docs, key=lambda d: (
        ((d["geometry"]["L"] - L) / L_range) ** 2 +
        ((d["geometry"]["a"] - a) / a_range) ** 2))
    return best, True, {"strategy": "nearest_euclidean",
                         "requested": {"L": L, "a": a},
                         "used": {"L": best["geometry"]["L"], "a": best["geometry"]["a"]}}

def _extract_antenna_arrays(doc):
    freq = np.array(doc["freq"], dtype=float)
    r_src = np.array(doc["Rin"], dtype=float)
    x_src = np.array(doc["Xin"], dtype=float)
    if freq.max() <= 1e7:
        freq = freq * 1e6
    idx = np.argsort(freq)
    return freq[idx], r_src[idx], x_src[idx]

def get_available_range_antenna(antenna_type, material, L, a):
    doc, _, _ = find_nearest_antenna_doc(antenna_type, material, L, a)
    if not doc:
        return None
    meta = doc.get("meta", {})
    if "f_MHz_min" in meta and "f_MHz_max" in meta:
        return float(meta["f_MHz_min"]), float(meta["f_MHz_max"])
    if "f_min" in meta and "f_max" in meta:
        return float(meta["f_min"]), float(meta["f_max"])
    freq_hz, _, _ = _extract_antenna_arrays(doc)
    return float(freq_hz.min()), float(freq_hz.max())

def interpolate_antenna_and_compute(doc, fmin_hz, fmax_hz, n):
    if fmin_hz >= fmax_hz:
        raise HTTPException(status_code=400, detail="fmin debe ser menor que fmax")
    freq_src, r_src, x_src = _extract_antenna_arrays(doc)
    lo = max(fmin_hz, float(freq_src.min()))
    hi = min(fmax_hz, float(freq_src.max()))
    if lo >= hi:
        raise HTTPException(status_code=400,
            detail=f"Sin intersección [{fmin_hz/1e6:.1f}–{fmax_hz/1e6:.1f}] MHz")
    f_vec = np.linspace(lo, hi, n)
    r_i = np.interp(f_vec, freq_src, r_src)
    x_i = np.interp(f_vec, freq_src, x_src)
    zin  = r_i + 1j * x_i
    s11  = (zin - Z0) / (zin + Z0)
    mag  = np.abs(s11)
    s11_db = 20 * np.log10(np.clip(mag, 1e-12, 1.0))
    roe    = (1 + mag) / (1 - mag + 1e-12)
    z_norm = zin / Z0
    return f_vec, zin, s11, s11_db, roe, z_norm, (lo, hi)

# ==========================================================
# GENERADORES DE PNG OPTIMIZADOS
# ==========================================================

_STYLE = {
    "bg":      "#0d1117",
    "fg":      "#e6edf3",
    "grid":    "#21262d",
    "line":    "#58a6ff",
    "ref":     "#f85149",
    "marker":  "#ffa657",
}

def _apply_dark_style(fig, ax):
    fig.patch.set_facecolor(_STYLE["bg"])
    ax.set_facecolor(_STYLE["bg"])
    for spine in ax.spines.values():
        spine.set_edgecolor(_STYLE["fg"])
    ax.tick_params(colors=_STYLE["fg"], labelsize=7)
    ax.xaxis.label.set_color(_STYLE["fg"])
    ax.yaxis.label.set_color(_STYLE["fg"])
    ax.title.set_color(_STYLE["fg"])
    ax.grid(True, color=_STYLE["grid"], linewidth=0.5)


def generate_s11_png(
    f_mhz: np.ndarray,
    s11_db: np.ndarray,
    f_res_mhz: float,
    s11_res_db: float,
    label: str = "",
) -> bytes:
    key = _cache_key(
        chart="s11",
        f0=round(f_mhz[0], 3), f1=round(f_mhz[-1], 3),
        s11_min=round(s11_res_db, 3), f_res=round(f_res_mhz, 3),
        n=len(f_mhz), label=label
    )
    cached = _cache_get(key)
    if cached:
        return cached

    fig, ax = plt.subplots(figsize=(IMG_W_IN, IMG_H_IN))
    _apply_dark_style(fig, ax)

    ax.plot(f_mhz, s11_db, color=_STYLE["line"], lw=1.5, zorder=3)
    ax.axhline(-10, color=_STYLE["ref"], ls="--", lw=1.0, label="-10 dB")
    ax.axvline(f_res_mhz, color=_STYLE["marker"], ls=":", lw=1.0,
               label=f"f_res={_fmt(f_res_mhz)}")
    ax.plot(f_res_mhz, s11_res_db, "o", color=_STYLE["marker"], ms=5, zorder=4)

    ax.set_xlabel("Frecuencia (MHz)", fontsize=8)
    ax.set_ylabel("|S11| (dB)", fontsize=8)
    ax.set_title(f"S11  {label}", fontsize=8, pad=4)
    ax.legend(fontsize=6, framealpha=0.4, facecolor=_STYLE["bg"],
              labelcolor=_STYLE["fg"])

    png = _fig_to_png_bytes(fig)
    _cache_put(key, png)
    return png


def generate_roe_png(
    f_mhz: np.ndarray,
    roe: np.ndarray,
    f_res_mhz: float,
    roe_res: float,
    label: str = "",
) -> bytes:
    key = _cache_key(
        chart="roe",
        f0=round(f_mhz[0], 3), f1=round(f_mhz[-1], 3),
        roe_min=round(roe_res, 3), f_res=round(f_res_mhz, 3),
        n=len(f_mhz), label=label
    )
    cached = _cache_get(key)
    if cached:
        return cached

    roe_plot = np.clip(roe, 1.0, 20.0)

    fig, ax = plt.subplots(figsize=(IMG_W_IN, IMG_H_IN))
    _apply_dark_style(fig, ax)

    ax.plot(f_mhz, roe_plot, color=_STYLE["line"], lw=1.5, zorder=3)
    ax.axhline(2.0, color=_STYLE["ref"], ls="--", lw=1.0, label="ROE = 2:1")
    ax.axvline(f_res_mhz, color=_STYLE["marker"], ls=":", lw=1.0,
               label=f"f_res={_fmt(f_res_mhz)}")
    ax.plot(f_res_mhz, min(float(roe_res), 20.0), "o",
            color=_STYLE["marker"], ms=5, zorder=4)

    ax.set_ylim(bottom=1.0, top=min(float(roe_plot.max()) * 1.1, 20.0))
    ax.set_xlabel("Frecuencia (MHz)", fontsize=8)
    ax.set_ylabel("ROE", fontsize=8)
    ax.set_title(f"ROE  {label}", fontsize=8, pad=4)
    ax.legend(fontsize=6, framealpha=0.4, facecolor=_STYLE["bg"],
              labelcolor=_STYLE["fg"])

    png = _fig_to_png_bytes(fig)
    _cache_put(key, png)
    return png


def generate_smith_png(
    s11_real: np.ndarray,
    s11_imag: np.ndarray,
    f_vec_hz: np.ndarray,
    f_res_idx: int,
    label: str = "",
) -> bytes:
    key = _cache_key(
        chart="smith",
        r0=round(float(s11_real[0]), 4), i0=round(float(s11_imag[0]), 4),
        r1=round(float(s11_real[-1]), 4), i1=round(float(s11_imag[-1]), 4),
        n=len(s11_real), label=label
    )
    cached = _cache_get(key)
    if cached:
        return cached

    s11_c = s11_real + 1j * s11_imag
    ntwk = rf.Network(
        frequency=rf.Frequency.from_f(f_vec_hz, unit="Hz"),
        s=s11_c.reshape(-1, 1, 1),
        z0=Z0
    )

    fig = plt.figure(figsize=(IMG_SMITH, IMG_SMITH))
    ax  = fig.add_subplot(111)
    fig.patch.set_facecolor(_STYLE["bg"])
    ax.set_facecolor(_STYLE["bg"])

    ntwk.plot_s_smith(ax=ax, color="#334155", lw=0.6, label=None)

    f_norm = (f_vec_hz - f_vec_hz.min()) / max(1e-12, f_vec_hz.max() - f_vec_hz.min())
    sc = ax.scatter(s11_real, s11_imag, c=f_norm, cmap="cool", s=4, zorder=3, linewidths=0)
    ax.plot(s11_real, s11_imag, color=_STYLE["line"], lw=1.2, zorder=2)

    ax.plot(s11_real[0],         s11_imag[0],         "o", color="#94d82d", ms=5, label="fmin", zorder=5)
    ax.plot(s11_real[-1],        s11_imag[-1],        "s", color="#74c0fc", ms=5, label="fmax", zorder=5)
    ax.plot(s11_real[f_res_idx], s11_imag[f_res_idx], "D", color=_STYLE["marker"],
            ms=6, label="|S11| min", zorder=6)

    cbar = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("f relativa", color=_STYLE["fg"], fontsize=6)
    cbar.ax.yaxis.set_tick_params(color=_STYLE["fg"], labelsize=6)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=_STYLE["fg"])

    ax.set_title(f"Smith  {label}", fontsize=8, color=_STYLE["fg"], pad=4)
    ax.legend(fontsize=5, framealpha=0.4, facecolor=_STYLE["bg"],
              labelcolor=_STYLE["fg"], loc="upper left")
    for spine in ax.spines.values():
        spine.set_edgecolor(_STYLE["fg"])
    ax.tick_params(colors=_STYLE["fg"], labelsize=6)

    png = _fig_to_png_bytes(fig)
    _cache_put(key, png)
    return png

# ==========================================================
# LÓGICA CENTRAL: calcular + generar PNG + metadatos
# ==========================================================

def _compute_and_render(
    antenna_type: str,
    material: str,
    L: float,
    a: float,
    fmin_mhz: float,
    fmax_mhz: float,
    n: int,
    charttype: str,
) -> tuple[dict, bytes]:
    doc, is_neighbor, neighbor_info = find_nearest_antenna_doc(antenna_type, material, L, a)
    if doc is None:
        raise HTTPException(status_code=404,
            detail=f"Sin datos: antenna_type={antenna_type}, material={material}")

    L_used = float(doc["geometry"]["L"])
    a_used = float(doc["geometry"]["a"])
    at_label  = _ANTENNA_LABELS.get(antenna_type, {"es": antenna_type})
    mat_label = _MATERIAL_LABELS.get(material,     {"es": material})
    label = (
        f"[{at_label['es']} / {mat_label['es']}] "
        f"L={L_used*100:.3f} cm  a={a_used*1000:.2f} mm"
    )

    f_vec, zin, s11, s11_db, roe, z_norm, f_range = interpolate_antenna_and_compute(
        doc, fmin_mhz * 1e6, fmax_mhz * 1e6, n
    )
    f_mhz   = f_vec / 1e6
    i_min   = int(np.nanargmin(s11_db))
    f_res   = float(f_mhz[i_min])
    s11_res = float(s11_db[i_min])
    roe_res = float(roe[i_min])
    R_res   = float(np.real(zin[i_min]))
    X_res   = float(np.imag(zin[i_min]))
    bw      = _bandwidth_mhz(f_mhz, s11_db, -10.0)

    if charttype == "smith":
        w_px = int(IMG_SMITH * IMG_DPI)
        h_px = w_px
    else:
        w_px = int(IMG_W_IN * IMG_DPI)
        h_px = int(IMG_H_IN * IMG_DPI)

    freq_hz, _, _ = _extract_antenna_arrays(doc)
    avail_fmin = float(freq_hz.min()) / 1e6
    avail_fmax = float(freq_hz.max()) / 1e6

    meta = {
        "type":        "chart_meta",
        "charttype":   charttype,
        "width":       w_px,
        "height":      h_px,
        "dpi":         IMG_DPI,
        "f_res_mhz":   round(f_res, 4),
        "s11_min_db":  round(s11_res, 3),
        "roe_at_res":  round(roe_res, 3),
        "R_at_res":    round(R_res, 3),
        "X_at_res":    round(X_res, 3),
        "fmin_mhz":    round(float(f_mhz[0]), 3),
        "fmax_mhz":    round(float(f_mhz[-1]), 3),
        "bandwidth_10db": bw,
        "antenna_type":   antenna_type,
        "material":       material,
        "L_used_m":       round(L_used, 6),
        "a_used_m":       round(a_used, 6),
        "is_neighbor":    is_neighbor,
        "esp32_online":   is_esp32_online(),
        "barrido_active": state["barrido"],
        "timestamp":      int(time.time()),
        "avail_fmin_mhz": round(avail_fmin, 3),
        "avail_fmax_mhz": round(avail_fmax, 3),
    }

    if charttype == "s11":
        png = generate_s11_png(f_mhz, s11_db, f_res, s11_res, label)
    elif charttype == "roe":
        png = generate_roe_png(f_mhz, roe, f_res, roe_res, label)
    elif charttype == "smith":
        png = generate_smith_png(
            np.real(s11), np.imag(s11), f_vec, i_min, label
        )
    else:
        png = generate_s11_png(f_mhz, s11_db, f_res, s11_res, label)

    meta["png_size_bytes"] = len(png)
    return meta, png

# ==========================================================
# CONNECTION MANAGER
# ==========================================================

class ConnectionManager:
    def __init__(self):
        self.clients = {"esp32": set(), "quest": set()}

    async def connect(self, ws: WebSocket, client_type: str):
        self.clients[client_type].add(ws)

    def disconnect(self, ws: WebSocket, client_type: str):
        self.clients.get(client_type, set()).discard(ws)

    async def broadcast_image_to_quest(self, meta: dict, png: bytes):
        dead = []
        for ws in self.clients["quest"]:
            try:
                await ws.send_json(meta)
                await ws.send_bytes(png)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients["quest"].discard(ws)

    async def broadcast_json_to_quest(self, msg: dict):
        dead = []
        for ws in self.clients["quest"]:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients["quest"].discard(ws)

manager = ConnectionManager()

# ==========================================================
# ESTADO ESP32
# ==========================================================

def is_esp32_online() -> bool:
    ts = state.get("last_esp32_ts")
    return ts is not None and (time.time() - ts) < ESP32_TIMEOUT_SECONDS

# ==========================================================
# BROADCAST
# ==========================================================

async def broadcast_chart_to_quest():
    at        = state.get("antennatype", "monopole")
    mat       = state.get("material",    "copper")
    L         = state["L"]
    a         = state["a"]
    charttype = state.get("charttype", "s11")
    fmin      = state["fmin_mhz"]
    fmax      = state["fmax_mhz"]
    n         = max(50, min(int(state["n"]), CHART_N))

    try:
        meta, png = _compute_and_render(at, mat, L, a, fmin, fmax, n, charttype)
    except HTTPException as e:
        available = get_available_range_antenna(at, mat, L, a)
        await manager.broadcast_json_to_quest({
            "type": "error",
            "message": e.detail,
            "available_range_mhz": (
                {"fmin": available[0], "fmax": available[1]} if available else None
            ),
            "esp32_online": is_esp32_online(),
        })
        return

    await manager.broadcast_image_to_quest(meta, png)

# ==========================================================
# LÓGICA DE BARRIDO
# ──────────────────────────────────────────────────────────
# El ESP32 envía dos tipos de mensajes relacionados con el
# barrido, sin cambios en el firmware:
#
#   1. toggle_barrido  {value: "1"|"0"}
#      · "1" → activa barrido; se congela la gráfica (no broadcast).
#      · "0" → desactiva barrido; se aplica barrido_pending y se
#               dispara broadcast con los nuevos límites de frecuencia.
#
#   2. config_barrido  {frecuencia_centro, frecuencia_lateral,
#                       unidad_centro, unidad_lateral}
#      · Se acumula siempre en barrido_pending.
#      · FC → FMin  |  FL → FMax  (conversión de unidades incluida).
#      · NO modifica el estado activo ni dispara broadcast.
#
# Reglas de bloqueo:
#   • Mientras barrido == True, los mensajes update_params del Quest
#     son rechazados con error "barrido_active".
#   • Solo se permiten: get_state, get_range (solo lectura).
# ==========================================================

def _apply_barrido_pending():
    """
    Convierte barrido_pending a MHz y actualiza fmin_mhz / fmax_mhz
    del estado activo. Ignora valores nulos o inválidos (conserva los
    límites anteriores).
    """
    fc_raw = barrido_pending.get("frecuencia_centro")
    fl_raw = barrido_pending.get("frecuencia_lateral")
    uc     = barrido_pending.get("unidad_centro",  "MHz")
    ul     = barrido_pending.get("unidad_lateral", "MHz")

    fmin_new = _to_mhz(str(fc_raw) if fc_raw is not None else "", uc)
    fmax_new = _to_mhz(str(fl_raw) if fl_raw is not None else "", ul)

    if fmin_new is not None:
        state["fmin_mhz"] = fmin_new
        state["frecuencia_centro"] = fc_raw
        state["unidad_centro"]     = uc

    if fmax_new is not None:
        state["fmax_mhz"] = fmax_new
        state["frecuencia_lateral"] = fl_raw
        state["unidad_lateral"]     = ul

    # Guardar unidad global de referencia
    if uc == ul:
        state["unidad"] = uc
    else:
        state["unidad"] = "MIX"

    print(
        f"[BARRIDO] Aplicando pending → "
        f"fmin={state['fmin_mhz']} MHz  fmax={state['fmax_mhz']} MHz"
    )


def _accumulate_barrido_config(data: dict):
    """
    Actualiza barrido_pending con los valores recibidos en config_barrido.
    Se llama tanto cuando barrido está activo como cuando llega config_barrido
    antes de desactivarlo (el ESP32 puede enviar la config justo antes del
    toggle de desactivación).
    """
    legacy_unit = data.get("unidad", barrido_pending["unidad_centro"])

    fc = data.get("frecuencia_centro",  barrido_pending["frecuencia_centro"])
    fl = data.get("frecuencia_lateral", barrido_pending["frecuencia_lateral"])
    uc = data.get("unidad_centro",  legacy_unit)
    ul = data.get("unidad_lateral", legacy_unit)

    # Ignorar strings vacíos / placeholder "---"
    if fc not in (None, "", "---"):
        barrido_pending["frecuencia_centro"] = fc
    if fl not in (None, "", "---"):
        barrido_pending["frecuencia_lateral"] = fl

    barrido_pending["unidad_centro"]  = uc
    barrido_pending["unidad_lateral"] = ul

    print(
        f"[BARRIDO] Pending acumulado → "
        f"FC={barrido_pending['frecuencia_centro']} {uc} | "
        f"FL={barrido_pending['frecuencia_lateral']} {ul}"
    )

# ==========================================================
# MANEJADORES DE MENSAJES WEBSOCKET
# ==========================================================

async def handle_esp32_event(data: dict):
    msg_type = data.get("type")
    print(f"[ESP32 RX] {data}")

    state["last_esp32_event"] = msg_type
    state["last_esp32_pin"]   = data.get("id")
    state["last_esp32_value"] = data.get("value")
    state["last_esp32_ip"]    = data.get("ip")
    state["last_esp32_rssi"]  = data.get("rssi")
    state["last_esp32_ts"]    = time.time()

    # ----------------------------------------------------------
    # toggle_barrido
    # ----------------------------------------------------------
    if msg_type == "toggle_barrido":
        activating = data.get("value") in ("1", 1, True, "true", "True")

        if activating:
            # ── Activar barrido ──────────────────────────────
            state["barrido"] = True
            print("[BARRIDO] ACTIVADO — gráfica congelada, acumulando config")
            # Notificar al Quest que el barrido está activo (sin imagen nueva)
            await manager.broadcast_json_to_quest({
                "type":          "barrido_status",
                "barrido_active": True,
                "message":       "Barrido activo. Gráfica congelada hasta desactivación.",
                "esp32_online":  is_esp32_online(),
            })
            # NO se dispara broadcast de imagen

        else:
            # ── Desactivar barrido ───────────────────────────
            state["barrido"] = False
            print("[BARRIDO] DESACTIVADO — aplicando config pendiente")
            _apply_barrido_pending()
            # Ahora sí actualizar y difundir imagen
            await broadcast_chart_to_quest()

    # ----------------------------------------------------------
    # config_barrido  — acumular siempre, sin broadcast de imagen
    # ----------------------------------------------------------
    elif msg_type == "config_barrido":
        _accumulate_barrido_config(data)

        # El ESP32 envía config_barrido también antes de desactivar,
        # por eso aquí NO se dispara broadcast; el toggle_barrido "0"
        # lo hará después de aplicar la config.
        #
        # Solo notificamos al Quest los metadatos del pending (sin PNG).
        await manager.broadcast_json_to_quest({
            "type":           "barrido_config_update",
            "barrido_active": state["barrido"],
            "pending": {
                "frecuencia_centro":  barrido_pending["frecuencia_centro"],
                "frecuencia_lateral": barrido_pending["frecuencia_lateral"],
                "unidad_centro":      barrido_pending["unidad_centro"],
                "unidad_lateral":     barrido_pending["unidad_lateral"],
                "fmin_preview_mhz":   _to_mhz(
                    str(barrido_pending["frecuencia_centro"])
                    if barrido_pending["frecuencia_centro"] is not None else "",
                    barrido_pending["unidad_centro"]
                ),
                "fmax_preview_mhz":   _to_mhz(
                    str(barrido_pending["frecuencia_lateral"])
                    if barrido_pending["frecuencia_lateral"] is not None else "",
                    barrido_pending["unidad_lateral"]
                ),
            },
            "esp32_online": is_esp32_online(),
        })

    # ----------------------------------------------------------
    # update_params — solo si barrido está inactivo
    # ----------------------------------------------------------
    elif msg_type == "update_params":
        if state["barrido"]:
            print("[BARRIDO] update_params ignorado (barrido activo)")
            await manager.broadcast_json_to_quest({
                "type":    "error",
                "code":    "barrido_active",
                "message": "Barrido activo. Los parámetros están bloqueados.",
                "barrido_active": True,
            })
            return

        new_at  = normalize_antenna_type(data.get("antennatype", state["antennatype"]))
        new_mat = normalize_material(data.get("material",    state["material"]))
        new_L   = data.get("L", state["L"])
        new_a   = data.get("a", state["a"])
        if any([new_at != state["antennatype"], new_mat != state["material"],
                new_L  != state["L"],           new_a   != state["a"]]):
            find_nearest_antenna_doc.cache_clear()
        state.update({
            "antennatype": new_at,  "material": new_mat,
            "L": new_L,             "a": new_a,
            "fmin_mhz": data.get("fmin_mhz", state["fmin_mhz"]),
            "fmax_mhz": data.get("fmax_mhz", state["fmax_mhz"]),
            "n":         data.get("n",        state["n"]),
            "charttype": normalize_charttype(data.get("charttype", state["charttype"])),
        })
        await broadcast_chart_to_quest()

    # ----------------------------------------------------------
    # Eventos de acción (medir_s11 / medir_roe / mostrar_smith cambian
    # el tipo de gráfica activo; captura/exportar solo se reenvían como
    # ui_action para que Unity dispare el botón correspondiente).
    # ----------------------------------------------------------
    elif msg_type in ("medir_s11", "mostrar_smith", "medir_roe",
                      "captura_pantalla", "exportar_datos"):
        print(f"[ESP32] {msg_type} activado")

        if state["barrido"]:
            print("[BARRIDO] acción ignorada (barrido activo)")
            return

        chart_map = {
            "medir_s11":     "s11",
            "medir_roe":     "roe",
            "mostrar_smith": "smith",
        }

        if msg_type in chart_map:
            new_chart = chart_map[msg_type]
            if new_chart != state["charttype"]:
                state["charttype"] = new_chart

            # Reenviar al Quest como ui_action para que AntennaApiSync
            # dispare OnUiAction y, si aplica, los botones de captura/exportar.
            await manager.broadcast_json_to_quest({
                "type":   "ui_action",
                "action": msg_type,
                "esp32_online": is_esp32_online(),
            })

            # Recalcular y enviar la nueva gráfica
            await broadcast_chart_to_quest()
        else:
            # captura_pantalla / exportar_datos: no cambian la gráfica,
            # solo notifican a Unity para que active el botón correspondiente.
            await manager.broadcast_json_to_quest({
                "type":   "ui_action",
                "action": msg_type,
                "esp32_online": is_esp32_online(),
            })

    elif msg_type == "heartbeat":
        state["last_esp32_ts"]    = time.time()
        state["last_esp32_event"] = "heartbeat"
        state["last_esp32_ip"]    = data.get("ip",   state.get("last_esp32_ip"))
        state["last_esp32_rssi"]  = data.get("rssi", state.get("last_esp32_rssi"))
        print("[ESP32] heartbeat")
        return  # heartbeat nunca dispara broadcast

    elif msg_type == "esp32_online":
        state["last_esp32_ts"]    = time.time()
        state["last_esp32_event"] = "esp32_online"
        print("[ESP32] ONLINE")
        # Solo enviamos imagen inicial si barrido está inactivo
        if not state["barrido"]:
            await broadcast_chart_to_quest()

    else:
        print(f"[ESP32] tipo no manejado: {msg_type}")


async def handle_quest_request(ws: WebSocket, data: dict):
    msg_type = data.get("type", "update_params")

    # ── get_range: solo JSON (sin imagen) ──────────────────
    if msg_type == "get_range":
        available = get_available_range_antenna(
            state["antennatype"], state["material"], state["L"], state["a"]
        )
        await ws.send_json({
            "type": "range_info",
            "antennatype": state["antennatype"],
            "material":    state["material"],
            "L": state["L"], "a": state["a"],
            "available_range_mhz": (
                {"fmin": available[0], "fmax": available[1]} if available else None
            ),
        })
        return

    # ── get_state: envía imagen del estado actual ───────────
    if msg_type == "get_state":
        if state["barrido"]:
            # En modo barrido devolvemos solo metadatos del pending, sin imagen nueva
            await ws.send_json({
                "type":           "barrido_frozen",
                "barrido_active": True,
                "message":        "Barrido activo. La gráfica está congelada.",
                "current_fmin_mhz": state["fmin_mhz"],
                "current_fmax_mhz": state["fmax_mhz"],
                "pending": {
                    "frecuencia_centro":  barrido_pending["frecuencia_centro"],
                    "frecuencia_lateral": barrido_pending["frecuencia_lateral"],
                    "unidad_centro":      barrido_pending["unidad_centro"],
                    "unidad_lateral":     barrido_pending["unidad_lateral"],
                    "fmin_preview_mhz": _to_mhz(
                        str(barrido_pending["frecuencia_centro"])
                        if barrido_pending["frecuencia_centro"] is not None else "",
                        barrido_pending["unidad_centro"]
                    ),
                    "fmax_preview_mhz": _to_mhz(
                        str(barrido_pending["frecuencia_lateral"])
                        if barrido_pending["frecuencia_lateral"] is not None else "",
                        barrido_pending["unidad_lateral"]
                    ),
                },
                "esp32_online": is_esp32_online(),
            })
            return

        try:
            meta, png = _compute_and_render(
                state["antennatype"], state["material"],
                state["L"], state["a"],
                state["fmin_mhz"], state["fmax_mhz"],
                state["n"], state["charttype"]
            )
            await ws.send_json(meta)
            await ws.send_bytes(png)
        except HTTPException as e:
            await ws.send_json({"type": "error", "message": e.detail})
        return

    # ── update_params: bloqueado durante barrido ───────────
    if msg_type == "update_params":
        if state["barrido"]:
            await ws.send_json({
                "type":    "error",
                "code":    "barrido_active",
                "message": "Barrido activo. Los parámetros están bloqueados hasta que se desactive.",
                "barrido_active": True,
            })
            return

        new_at  = normalize_antenna_type(data.get("antennatype", state["antennatype"]))
        new_mat = normalize_material(data.get("material",    state["material"]))
        new_L   = data.get("L", state["L"])
        new_a   = data.get("a", state["a"])
        if any([new_at != state["antennatype"], new_mat != state["material"],
                new_L  != state["L"],           new_a   != state["a"]]):
            find_nearest_antenna_doc.cache_clear()
        state.update({
            "antennatype": new_at,  "material": new_mat,
            "L": new_L,             "a": new_a,
            "fmin_mhz": data.get("fmin_mhz", state["fmin_mhz"]),
            "fmax_mhz": data.get("fmax_mhz", state["fmax_mhz"]),
            "n":         data.get("n",        state["n"]),
            "charttype": normalize_charttype(data.get("charttype", state["charttype"])),
        })
        await broadcast_chart_to_quest()

# ==========================================================
# WEBSOCKETS
# ==========================================================

async def _websocket_handler(websocket: WebSocket):
    await websocket.accept()
    client_type     = None
    pending_message = None

    try:
        path = websocket.url.path

        if path == "/esp32":
            client_type = "esp32"
        elif path == "/quest":
            client_type = "quest"
        else:
            init_msg = await websocket.receive_json()
            maybe_client = init_msg.get("client")
            if maybe_client in ("esp32", "quest"):
                client_type = maybe_client
            else:
                pending_message = init_msg

        if client_type in ("esp32", "quest"):
            await manager.connect(websocket, client_type)

            if client_type == "quest":
                if state["barrido"]:
                    # Quest se conecta mientras hay barrido activo: solo JSON
                    await websocket.send_json({
                        "type":           "barrido_frozen",
                        "barrido_active": True,
                        "message":        "Barrido activo. Gráfica congelada.",
                        "current_fmin_mhz": state["fmin_mhz"],
                        "current_fmax_mhz": state["fmax_mhz"],
                        "esp32_online": is_esp32_online(),
                    })
                else:
                    try:
                        meta, png = _compute_and_render(
                            state["antennatype"], state["material"],
                            state["L"], state["a"],
                            state["fmin_mhz"], state["fmax_mhz"],
                            state["n"], state["charttype"]
                        )
                        await websocket.send_json(meta)
                        await websocket.send_bytes(png)
                    except HTTPException as e:
                        await websocket.send_json({"type": "error", "message": e.detail})

            if pending_message is not None:
                if client_type == "esp32":
                    await handle_esp32_event(pending_message)
                else:
                    await handle_quest_request(websocket, pending_message)

            while True:
                data = await websocket.receive_json()
                if client_type == "esp32":
                    await handle_esp32_event(data)
                else:
                    await handle_quest_request(websocket, data)

        else:
            if pending_message is not None:
                await websocket.send_json(_legacy_trace_response(pending_message))
            while True:
                data = await websocket.receive_json()
                await websocket.send_json(_legacy_trace_response(data))

    except WebSocketDisconnect:
        print(f"[WS] disconnect: {client_type}")
        if client_type in ("esp32", "quest"):
            manager.disconnect(websocket, client_type)
        if client_type == "esp32":
            state["last_esp32_ts"] = None
            await manager.broadcast_json_to_quest(
                {"type": "status", "esp32_online": False})
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


@app.websocket("/ws")
async def websocket_ws(websocket: WebSocket):
    await _websocket_handler(websocket)

@app.websocket("/esp32")
async def websocket_esp32(websocket: WebSocket):
    await _websocket_handler(websocket)

@app.websocket("/quest")
async def websocket_quest(websocket: WebSocket):
    await _websocket_handler(websocket)

# ==========================================================
# LEGACY: /ws payload numérico
# ==========================================================

def _legacy_trace_response(data: dict) -> dict:
    material = data.get("material", "cobre")
    L        = data.get("L", 0.02)
    a        = data.get("a", 0.001)
    fmin_mhz = data.get("fmin_mhz", 300)
    fmax_mhz = data.get("fmax_mhz", 6000)
    n        = data.get("n", 501)
    if fmin_mhz >= fmax_mhz:
        return {"error": "fmin_mhz debe ser < fmax_mhz"}
    doc = load_trace(material, L, a)
    if not doc:
        return {"error": f"No data para {material}/L={L}/a={a}"}
    try:
        f_vec, Zin, S11, S11_dB, ROE, f_range = interpolate_and_compute(
            doc, fmin_mhz * 1e6, fmax_mhz * 1e6, n)
    except HTTPException as e:
        return {"error": e.detail}
    return {
        "material": material, "L_m": L, "a_m": a,
        "f_MHz": f_vec.tolist(), "S11_dB": S11_dB.tolist(), "ROE": ROE.tolist(),
        "Zin_real": np.real(Zin).tolist(), "Zin_imag": np.imag(Zin).tolist(),
        "S11_real": np.real(S11).tolist(), "S11_imag": np.imag(S11).tolist(),
        "f_range_used_mhz": {"fmin": f_range[0], "fmax": f_range[1]},
    }

# ==========================================================
# ENDPOINTS REST — BARRIDO STATE
# ==========================================================

@app.get("/barrido/state")
def get_barrido_state():
    """
    Devuelve el estado actual del modo barrido y la configuración pendiente.

    Campos:
      - barrido_active      : bool   → True si el barrido está activo.
      - current_fmin_mhz    : float  → FMin actualmente aplicado a la gráfica.
      - current_fmax_mhz    : float  → FMax actualmente aplicado a la gráfica.
      - pending             : object → Configuración acumulada aún no aplicada.
        - frecuencia_centro   : valor raw de FC (FMin) desde el ESP32.
        - frecuencia_lateral  : valor raw de FL (FMax) desde el ESP32.
        - unidad_centro       : unidad de FC (kHz | MHz | GHz).
        - unidad_lateral      : unidad de FL.
        - fmin_preview_mhz    : FC convertido a MHz (puede ser null).
        - fmax_preview_mhz    : FL convertido a MHz (puede ser null).
      - esp32_online        : bool
      - timestamp           : int    → Unix timestamp actual.
    """
    fc_raw = barrido_pending.get("frecuencia_centro")
    fl_raw = barrido_pending.get("frecuencia_lateral")
    uc     = barrido_pending.get("unidad_centro",  "MHz")
    ul     = barrido_pending.get("unidad_lateral", "MHz")

    return {
        "barrido_active":   state["barrido"],
        "current_fmin_mhz": state["fmin_mhz"],
        "current_fmax_mhz": state["fmax_mhz"],
        "pending": {
            "frecuencia_centro":  fc_raw,
            "frecuencia_lateral": fl_raw,
            "unidad_centro":      uc,
            "unidad_lateral":     ul,
            "fmin_preview_mhz":   _to_mhz(str(fc_raw) if fc_raw is not None else "", uc),
            "fmax_preview_mhz":   _to_mhz(str(fl_raw) if fl_raw is not None else "", ul),
        },
        "esp32_online": is_esp32_online(),
        "timestamp":    int(time.time()),
    }

# ==========================================================
# ENDPOINTS REST — PLOTS (StreamingResponse PNG)
# ==========================================================

def _fig_to_streaming(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=IMG_DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

def fig_to_png(fig):
    return _fig_to_streaming(fig)

@app.get("/plot/s11")
def plot_s11(material: str, L: float, a: float,
             fmin_mhz: float, fmax_mhz: float, n: int = 501):
    doc = load_trace(material, L, a)
    if not doc:
        raise HTTPException(status_code=404, detail="No hay datos")
    f_vec, _, _, S11_dB, _, _ = interpolate_and_compute(doc, fmin_mhz*1e6, fmax_mhz*1e6, n)
    i_min = int(np.nanargmin(S11_dB))
    fig, ax = plt.subplots(figsize=(IMG_W_IN, IMG_H_IN))
    _apply_dark_style(fig, ax)
    ax.plot(f_vec/1e6, S11_dB, color=_STYLE["line"], lw=1.5)
    ax.axhline(-10, color=_STYLE["ref"], ls="--", lw=1.0, label="-10 dB")
    ax.axvline(f_vec[i_min]/1e6, color=_STYLE["marker"], ls=":", lw=1.0)
    ax.set_xlabel("Frecuencia (MHz)", fontsize=8)
    ax.set_ylabel("|S11| (dB)", fontsize=8)
    ax.set_title(f"S11 – {material} L={L:.5f}m a={a:.5f}m", fontsize=8)
    ax.legend(fontsize=6, framealpha=0.4, facecolor=_STYLE["bg"], labelcolor=_STYLE["fg"])
    return _fig_to_streaming(fig)

@app.get("/plot/roe")
def plot_roe(material: str, L: float, a: float,
             fmin_mhz: float, fmax_mhz: float, n: int = 501):
    doc = load_trace(material, L, a)
    if not doc:
        raise HTTPException(status_code=404, detail="No hay datos")
    f_vec, _, _, _, ROE, _ = interpolate_and_compute(doc, fmin_mhz*1e6, fmax_mhz*1e6, n)
    roe_plot = np.clip(ROE, 1.0, 20.0)
    fig, ax = plt.subplots(figsize=(IMG_W_IN, IMG_H_IN))
    _apply_dark_style(fig, ax)
    ax.plot(f_vec/1e6, roe_plot, color=_STYLE["line"], lw=1.5)
    ax.axhline(2.0, color=_STYLE["ref"], ls="--", lw=1.0, label="ROE=2:1")
    ax.set_ylim(bottom=1.0)
    ax.set_xlabel("Frecuencia (MHz)", fontsize=8)
    ax.set_ylabel("ROE", fontsize=8)
    ax.set_title(f"ROE – {material} L={L:.5f}m a={a:.5f}m", fontsize=8)
    ax.legend(fontsize=6, framealpha=0.4, facecolor=_STYLE["bg"], labelcolor=_STYLE["fg"])
    return _fig_to_streaming(fig)

@app.get("/plot/smith")
def plot_smith(material: str, L: float, a: float,
               fmin_mhz: float, fmax_mhz: float, n: int = 801):
    doc = load_trace(material, L, a)
    if not doc:
        raise HTTPException(status_code=404, detail="No hay datos")
    f_vec, _, S11, S11_dB, _, _ = interpolate_and_compute(doc, fmin_mhz*1e6, fmax_mhz*1e6, n)
    ntwk = rf.Network(frequency=rf.Frequency.from_f(f_vec, unit="Hz"),
                      s=S11.reshape(-1,1,1), z0=Z0)
    i_min = int(np.nanargmin(np.abs(S11)))
    fig = plt.figure(figsize=(IMG_SMITH, IMG_SMITH))
    ax  = fig.add_subplot(111)
    fig.patch.set_facecolor(_STYLE["bg"])
    ax.set_facecolor(_STYLE["bg"])
    ntwk.plot_s_smith(ax=ax, color="#334155", lw=0.6, label=None)
    f_norm = (f_vec - f_vec.min()) / max(1e-12, f_vec.max() - f_vec.min())
    sc = ax.scatter(np.real(S11), np.imag(S11), c=f_norm, cmap="cool", s=6, zorder=3, linewidths=0)
    ax.plot(np.real(S11), np.imag(S11), color=_STYLE["line"], lw=1.2, zorder=2)
    ax.plot(np.real(S11[0]),     np.imag(S11[0]),     "o", color="#94d82d", ms=5, label="fmin")
    ax.plot(np.real(S11[-1]),    np.imag(S11[-1]),    "s", color="#74c0fc", ms=5, label="fmax")
    ax.plot(np.real(S11[i_min]), np.imag(S11[i_min]),"D", color=_STYLE["marker"], ms=6, label="|S11| min")
    cbar = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("f relativa", color=_STYLE["fg"], fontsize=6)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=_STYLE["fg"])
    ax.set_title(f"Smith – {material} L={L:.5f}m", fontsize=8, color=_STYLE["fg"])
    ax.legend(fontsize=5, framealpha=0.4, facecolor=_STYLE["bg"], labelcolor=_STYLE["fg"])
    for spine in ax.spines.values():
        spine.set_edgecolor(_STYLE["fg"])
    ax.tick_params(colors=_STYLE["fg"], labelsize=6)
    return _fig_to_streaming(fig)

# ==========================================================
# ENDPOINTS REST — LEGACY TRACE / METADATA
# ==========================================================

@app.get("/metadata")
def metadata(material: str = Query(None), L: float = Query(None), a: float = Query(None)):
    q = {"material": material} if material else {}
    mats   = coll.distinct("material")
    combos = list(coll.find(q, {"_id": 0, "L": 1, "a": 1}).sort([("L", 1), ("a", 1)]))
    doc_fmin = coll.find_one({}, sort=[("meta.f_MHz_min",  1)])
    doc_fmax = coll.find_one({}, sort=[("meta.f_MHz_max", -1)])
    global_fmin = doc_fmin["meta"]["f_MHz_min"] if doc_fmin and "meta" in doc_fmin else None
    global_fmax = doc_fmax["meta"]["f_MHz_max"] if doc_fmax and "meta" in doc_fmax else None
    result = {"materiales": mats, "combos_L_a": combos,
              "f_MHz_min": global_fmin, "f_MHz_max": global_fmax}
    if L is not None and a is not None:
        mat = material or state["material"]
        available = get_available_range(mat, L, a)
        result["geometry_range_mhz"] = (
            {"fmin": available[0], "fmax": available[1]} if available else None)
    return result

@app.get("/range")
def get_range(material: str = Query(...), L: float = Query(...), a: float = Query(...)):
    available = get_available_range(material, L, a)
    if not available:
        raise HTTPException(status_code=404, detail=f"Sin datos: {material} L={L} a={a}")
    return {"material": material, "L": L, "a": a,
            "fmin_mhz": available[0], "fmax_mhz": available[1]}

@app.get("/trace")
def trace(material: str = Query(...), L: float = Query(...), a: float = Query(...),
          fmin_mhz: float = Query(..., gt=0), fmax_mhz: float = Query(..., gt=0),
          n: int = Query(501, ge=10, le=5001)):
    doc = load_trace(material, L, a)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Sin datos: {material} L={L} a={a}")
    f_vec, Zin, S11, S11_dB, ROE, f_range = interpolate_and_compute(
        doc, fmin_mhz*1e6, fmax_mhz*1e6, n)
    available = get_available_range(material, L, a)
    return JSONResponse({
        "material": material, "L_m": L, "a_m": a, "z0_ohm": Z0,
        "f_MHz":    f_vec.tolist(),
        "R_ohm":    np.real(Zin).tolist(), "X_ohm": np.imag(Zin).tolist(),
        "S11_real": np.real(S11).tolist(), "S11_imag": np.imag(S11).tolist(),
        "S11_dB":   S11_dB.tolist(),       "ROE": ROE.tolist(),
        "f_range_used_mhz":  {"fmin": f_range[0], "fmax": f_range[1]},
        "available_range_mhz": {"fmin": available[0], "fmax": available[1]},
    })

# ==========================================================
# ENDPOINTS REST — ANTENAS
# ==========================================================

def _antenna_payload_base(antenna_type, material, L, a, fmin_mhz, fmax_mhz, n, chart_type="s11"):
    doc, is_neighbor, neighbor_info = find_nearest_antenna_doc(antenna_type, material, L, a)
    if not doc:
        raise HTTPException(status_code=404,
            detail=f"Sin datos: {antenna_type}/{material}")

    L_used = float(doc["geometry"]["L"]); a_used = float(doc["geometry"]["a"])
    f_vec, zin, s11, s11_db, roe, z_norm, f_range = interpolate_antenna_and_compute(
        doc, fmin_mhz*1e6, fmax_mhz*1e6, n)
    freq_hz, _, _ = _extract_antenna_arrays(doc)
    available = (float(freq_hz.min()), float(freq_hz.max()))
    i_min = int(np.nanargmin(s11_db)); f_mhz = f_vec / 1e6
    at_label  = _ANTENNA_LABELS.get(antenna_type, {"es": antenna_type, "en": antenna_type})
    mat_label = _MATERIAL_LABELS.get(material,    {"es": material,    "en": material})
    bw_10db = _bandwidth_mhz(f_mhz, s11_db, -10.0)
    bw_3db  = _bandwidth_mhz(f_mhz, s11_db, -3.0)
    return {
        "simulation": {
            "antenna_type": antenna_type, "antenna_type_label": at_label,
            "material": material, "material_label": mat_label,
            "L_requested_m": float(L), "a_requested_m": float(a),
            "L_used_m": L_used,        "a_used_m": a_used, "Z0_ohm": float(Z0),
        },
        "availability": {
            "is_neighbor": is_neighbor, "neighbor_info": neighbor_info,
            "proximity_score": _proximity_score(L, a, L_used, a_used),
            "available_range_mhz": {"fmin": available[0]/1e6, "fmax": available[1]/1e6},
            "used_range_mhz": {"fmin": f_range[0]/1e6, "fmax": f_range[1]/1e6},
            "n_points": int(n),
        },
        "chart": {
            "type": chart_type,
            "resonance": {
                "f_res_mhz": float(f_mhz[i_min]),
                "s11_db":    float(s11_db[i_min]),
                "roe":       float(roe[i_min]),
                "R_ohm":     float(np.real(zin[i_min])),
                "X_ohm":     float(np.imag(zin[i_min])),
            },
            "bandwidth_10db": bw_10db, "bandwidth_3db": bw_3db,
        },
        "data": {
            "f_MHz":      f_mhz.tolist(),
            "S11_dB":     s11_db.tolist(),
            "ROE":        roe.tolist(),
            "S11_real":   np.real(s11).tolist(),   "S11_imag":   np.imag(s11).tolist(),
            "Znorm_real": np.real(z_norm).tolist(), "Znorm_imag": np.imag(z_norm).tolist(),
        },
    }

@app.get("/antennas/metadata")
def antenna_metadata(antenna_type: str = Query(None), material: str = Query(None),
                     L: float = Query(None), a: float = Query(None)):
    if antenna_coll is None:
        raise HTTPException(status_code=503, detail="Colección de antenas no disponible")
    q = {}
    if antenna_type: q["antenna_type"] = normalize_antenna_type(antenna_type)
    if material:     q["material"]     = normalize_material(material)
    docs = antenna_coll.find(q, {"_id": 0, "antenna_type": 1, "material": 1, "geometry": 1})
    combos = []
    seen = set()
    for doc in docs:
        geom = doc.get("geometry", {})
        key = (doc.get("antenna_type"), doc.get("material"),
               float(geom.get("L", 0)), float(geom.get("a", 0)))
        if key in seen: continue
        seen.add(key)
        combos.append({"antenna_type": doc.get("antenna_type"),
                       "material": doc.get("material"),
                       "L": float(geom.get("L", 0)), "a": float(geom.get("a", 0))})
    combos.sort(key=lambda x: (x["antenna_type"], x["material"], x["L"], x["a"]))
    return {"tipos_antena": sorted(set(antenna_coll.distinct("antenna_type", q))),
            "materiales":   sorted(set(antenna_coll.distinct("material", q))),
            "combos_L_a":   combos}

@app.get("/antennas/windows")
def antenna_windows(antenna_type: str = Query(...), material: str = Query(...),
                    L: float = Query(...), a: float = Query(...),
                    fmin_mhz: Optional[float] = Query(None, gt=0),
                    fmax_mhz: Optional[float] = Query(None, gt=0),
                    n: int = Query(501, ge=10, le=5001)):
    at_norm  = normalize_antenna_type(antenna_type)
    mat_norm = normalize_material(material)
    doc, is_neighbor, neighbor_info = find_nearest_antenna_doc(at_norm, mat_norm, L, a)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Sin datos: {antenna_type}/{material}")
    L_used = float(doc["geometry"]["L"]); a_used = float(doc["geometry"]["a"])
    freq_hz, _, _ = _extract_antenna_arrays(doc)
    available = (float(freq_hz.min()/1e6), float(freq_hz.max()/1e6))
    req_fmin = float(fmin_mhz) if fmin_mhz else available[0]
    req_fmax = float(fmax_mhz) if fmax_mhz else available[1]
    lo = max(req_fmin, available[0]); hi = min(req_fmax, available[1])
    return {"antenna_type": at_norm, "material": mat_norm,
            "L_m": float(L), "a_m": float(a), "L_used_m": L_used, "a_used_m": a_used,
            "is_neighbor": is_neighbor, "neighbor_info": neighbor_info,
            "proximity_score": _proximity_score(L, a, L_used, a_used),
            "available_range_mhz": {"fmin": available[0], "fmax": available[1]},
            "usable_range_mhz": {"fmin": lo, "fmax": hi} if lo < hi else None,
            "has_overlap": lo < hi}

@app.get("/antennas/s11")
def antenna_s11(antenna_type: str = Query(...), material: str = Query(...),
                L: float = Query(...), a: float = Query(...),
                fmin_mhz: Optional[float] = Query(None, gt=0),
                fmax_mhz: Optional[float] = Query(None, gt=0),
                n: int = Query(501, ge=10, le=5001)):
    at_norm = normalize_antenna_type(antenna_type)
    mat_norm = normalize_material(material)
    available = get_available_range_antenna(at_norm, mat_norm, L, a)
    fmin = float(fmin_mhz) if fmin_mhz else (available[0] if available else 300.0)
    fmax = float(fmax_mhz) if fmax_mhz else (available[1] if available else 6000.0)
    return _antenna_payload_base(at_norm, mat_norm, L, a, fmin, fmax, n, "s11")

@app.get("/antennas/roe")
def antenna_roe(antenna_type: str = Query(...), material: str = Query(...),
                L: float = Query(...), a: float = Query(...),
                fmin_mhz: Optional[float] = Query(None, gt=0),
                fmax_mhz: Optional[float] = Query(None, gt=0),
                n: int = Query(501, ge=10, le=5001)):
    at_norm = normalize_antenna_type(antenna_type)
    mat_norm = normalize_material(material)
    available = get_available_range_antenna(at_norm, mat_norm, L, a)
    fmin = float(fmin_mhz) if fmin_mhz else (available[0] if available else 300.0)
    fmax = float(fmax_mhz) if fmax_mhz else (available[1] if available else 6000.0)
    return _antenna_payload_base(at_norm, mat_norm, L, a, fmin, fmax, n, "roe")

@app.get("/antennas/smith")
def antenna_smith(antenna_type: str = Query(...), material: str = Query(...),
                  L: float = Query(...), a: float = Query(...),
                  fmin_mhz: Optional[float] = Query(None, gt=0),
                  fmax_mhz: Optional[float] = Query(None, gt=0),
                  n: int = Query(801, ge=10, le=5001)):
    at_norm = normalize_antenna_type(antenna_type)
    mat_norm = normalize_material(material)
    available = get_available_range_antenna(at_norm, mat_norm, L, a)
    fmin = float(fmin_mhz) if fmin_mhz else (available[0] if available else 300.0)
    fmax = float(fmax_mhz) if fmax_mhz else (available[1] if available else 6000.0)
    return _antenna_payload_base(at_norm, mat_norm, L, a, fmin, fmax, n, "smith")

@app.get("/antennas/full")
def antenna_full(antenna_type: str = Query(...), material: str = Query(...),
                 L: float = Query(...), a: float = Query(...),
                 fmin_mhz: Optional[float] = Query(None, gt=0),
                 fmax_mhz: Optional[float] = Query(None, gt=0),
                 n: int = Query(501, ge=10, le=5001),
                 n_smith: int = Query(801, ge=10, le=5001)):
    at_norm = normalize_antenna_type(antenna_type)
    mat_norm = normalize_material(material)
    available = get_available_range_antenna(at_norm, mat_norm, L, a)
    fmin = float(fmin_mhz) if fmin_mhz else (available[0] if available else 300.0)
    fmax = float(fmax_mhz) if fmax_mhz else (available[1] if available else 6000.0)
    payload   = _antenna_payload_base(at_norm, mat_norm, L, a, fmin, fmax, n, "s11")
    s_payload = _antenna_payload_base(at_norm, mat_norm, L, a, fmin, fmax, n_smith, "smith")
    payload["data"]["smith"] = {
        k: s_payload["data"][k]
        for k in ("S11_real", "S11_imag", "Znorm_real", "Znorm_imag")
    }
    payload["chart"]["type"] = "full"
    return payload

# ==========================================================
# AUTH
# ==========================================================

class UsuarioRegistro(BaseModel):
    nombre:      str      = Field(..., min_length=2, max_length=100)
    correo:      EmailStr
    contrasenia: str      = Field(..., min_length=6)
    escuela:     str      = Field(..., min_length=2, max_length=200)

class UsuarioLogin(BaseModel):
    correo:      EmailStr
    contrasenia: str

class UsuarioRespuesta(BaseModel):
    idusuario: str; nombre: str; correo: str; escuela: str

def get_usuarios_collection():
    return client["vna_db"]["usuarios"]

@app.post("/auth/registro", response_model=UsuarioRespuesta, status_code=201)
def registrar_usuario(usuario: UsuarioRegistro):
    col = get_usuarios_collection()
    if col.find_one({"correo": usuario.correo}):
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    salt  = bcrypt.gensalt()
    hash_ = bcrypt.hashpw(usuario.contrasenia.encode(), salt)
    res = col.insert_one({"nombre": usuario.nombre, "correo": usuario.correo,
                          "contrasenia": hash_, "escuela": usuario.escuela})
    return UsuarioRespuesta(idusuario=str(res.inserted_id), nombre=usuario.nombre,
                            correo=usuario.correo, escuela=usuario.escuela)

@app.post("/auth/login", response_model=UsuarioRespuesta)
def iniciar_sesion(credenciales: UsuarioLogin):
    col = get_usuarios_collection()
    usuario = col.find_one({"correo": credenciales.correo})
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not bcrypt.checkpw(credenciales.contrasenia.encode(), usuario["contrasenia"]):
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")
    return UsuarioRespuesta(idusuario=str(usuario["_id"]), nombre=usuario["nombre"],
                            correo=usuario["correo"], escuela=usuario["escuela"])

@app.get("/auth/usuario/{idusuario}", response_model=UsuarioRespuesta)
def obtener_usuario(idusuario: str):
    col = get_usuarios_collection()
    try:
        usuario = col.find_one({"_id": ObjectId(idusuario)})
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return UsuarioRespuesta(idusuario=str(usuario["_id"]), nombre=usuario["nombre"],
                            correo=usuario["correo"], escuela=usuario["escuela"])

# ==========================================================
# SESIONES
# ==========================================================

class SesionCrear(BaseModel):
    idusuario: str; name: str = Field(..., min_length=1, max_length=120)
    type: str; material: str; L: float; widthIndex: int
    fmin: float; fmax: float; timestamp: str

class SesionRespuesta(BaseModel):
    id: str; idusuario: str; name: str; type: str; material: str
    L: float; widthIndex: int; fmin: float; fmax: float; timestamp: str

def get_sesiones_collection():
    return client["vna_db"]["sesiones"]

@app.post("/sesiones", response_model=SesionRespuesta, status_code=201)
def crear_sesion(sesion: SesionCrear):
    usuarios_col = get_usuarios_collection()
    sesiones_col = get_sesiones_collection()
    try:
        uid = ObjectId(sesion.idusuario)
    except Exception:
        raise HTTPException(status_code=400, detail="ID de usuario inválido")
    if not usuarios_col.find_one({"_id": uid}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    doc = sesion.model_dump()
    res = sesiones_col.insert_one(doc)
    return SesionRespuesta(id=str(res.inserted_id), **{k: doc[k] for k in SesionRespuesta.model_fields if k != "id"})

@app.get("/sesiones/{idusuario}", response_model=list[SesionRespuesta])
def obtener_sesiones_usuario(idusuario: str):
    usuarios_col = get_usuarios_collection()
    sesiones_col = get_sesiones_collection()
    try:
        uid = ObjectId(idusuario)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not usuarios_col.find_one({"_id": uid}):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return [SesionRespuesta(id=str(s["_id"]), **{k: s[k] for k in SesionRespuesta.model_fields if k != "id"})
            for s in sesiones_col.find({"idusuario": idusuario}).sort("timestamp", -1)]

@app.delete("/sesiones/{id_sesion}", status_code=204)
def eliminar_sesion(id_sesion: str):
    sesiones_col = get_sesiones_collection()
    try:
        obj_id = ObjectId(id_sesion)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    if sesiones_col.delete_one({"_id": obj_id}).deleted_count == 0:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

# ==========================================================
# HEALTH CHECK
# ==========================================================

@app.get("/health", status_code=status.HTTP_200_OK)
def health():
    try:
        _ = coll.find_one({}, {"_id": 1})
        return {
            "status": "OK", "db": "online",
            "esp32_online":     is_esp32_online(),
            "last_esp32_event": state.get("last_esp32_event"),
            "last_esp32_ts":    state.get("last_esp32_ts"),
            "last_esp32_ip":    state.get("last_esp32_ip"),
            "last_esp32_rssi":  state.get("last_esp32_rssi"),
            "barrido_active":   state["barrido"],
            "image_cache_size": len(_image_cache),
            "websocket": "/ws | /esp32 | /quest  →  PNG streaming ready",
            "api_version": "2.1",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {str(e)}")