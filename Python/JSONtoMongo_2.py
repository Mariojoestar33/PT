"""
JSONtoMongo_bulk.py
====================
Carga los 4 archivos JSON de antenas en MongoDB.
Usa upsert, por lo que nunca borra documentos existentes:
  - Si ya existe un doc con el mismo (antenna_type, material, L, a) → actualiza
  - Si no existe → inserta

Archivos que carga (ajusta ARCHIVOS si es necesario):
  • dataDipoloCobre.json   → antenna_type=dipole,   material=copper
  • dataDipoloPlata.json   → antenna_type=dipole,   material=silver
  • dataMonopoloCobre.json → antenna_type=monopole, material=copper
  • dataMonopoloPlata.json → antenna_type=monopole, material=silver
"""

import json
from pathlib import Path
from pymongo import MongoClient, ASCENDING

# ==========================================================
# CONFIG — ajusta rutas y conexión si es necesario
# ==========================================================

MONGO_URI       = "mongodb://localhost:27017"
DB_NAME         = "vna_db"
COLLECTION_NAME = "antennas"
BATCH_SIZE      = 100

# Mapa: nombre_de_archivo → (antenna_type, material canónico)
# El "material canónico" es el valor que quedará en MongoDB.
# Si el JSON ya trae el valor correcto en inglés se puede omitir
# la normalización, pero lo hacemos explícito para mayor seguridad.
ARCHIVOS = [
    {
        "path":         Path("dataDipoloCobre.json"),
        "antenna_type": "dipole",
        "material":     "copper",
    },
    {
        "path":         Path("dataDipoloPlata.json"),
        "antenna_type": "dipole",
        "material":     "silver",
    },
    {
        "path":         Path("dataMonopoloCobre.json"),
        "antenna_type": "monopole",
        "material":     "copper",
    },
    {
        "path":         Path("dataMonopoloSilver.json"),
        "antenna_type": "monopole",
        "material":     "silver",
    },
]

# Columnas requeridas en cada documento JSON
REQUIRED_ROOT     = ["geometry", "freq", "Rin", "Xin"]
REQUIRED_GEOMETRY = ["L", "a"]

# ==========================================================
# CONEXIÓN
# ==========================================================

client = MongoClient(MONGO_URI)
db     = client[DB_NAME]
coll   = db[COLLECTION_NAME]

# ==========================================================
# ÍNDICES (se crean una sola vez; MongoDB los ignora si ya existen)
# ==========================================================

coll.create_index(
    [
        ("antenna_type", ASCENDING),
        ("material",     ASCENDING),
        ("geometry.L",   ASCENDING),
        ("geometry.a",   ASCENDING),
    ],
    unique=True,
    name="idx_unique_antenna",
)
coll.create_index([("f_res", ASCENDING)], name="idx_f_res")
coll.create_index([("antenna_type", ASCENDING)], name="idx_antenna_type")
coll.create_index([("material",     ASCENDING)], name="idx_material")

print("=" * 60)
print(f"Base de datos : {DB_NAME}")
print(f"Colección     : {COLLECTION_NAME}")
print("=" * 60)

total_inserted = 0
total_updated  = 0
total_errors   = 0

# ==========================================================
# LOOP PRINCIPAL — un archivo a la vez
# ==========================================================

for cfg in ARCHIVOS:

    ruta         = cfg["path"]
    antenna_type = cfg["antenna_type"]
    material     = cfg["material"]

    print(f"\n[{antenna_type.upper()} / {material}]  →  {ruta}")

    # ----------------------------------------------------------
    # Verificar que el archivo existe
    # ----------------------------------------------------------
    if not ruta.exists():
        print(f"  ⚠  Archivo no encontrado, se omite: {ruta}")
        continue

    # ----------------------------------------------------------
    # Cargar JSON
    # ----------------------------------------------------------
    records = json.loads(ruta.read_text(encoding="utf-8"))
    print(f"  Documentos en JSON : {len(records)}")

    # ----------------------------------------------------------
    # Validar y normalizar
    # ----------------------------------------------------------
    validos = 0
    for idx, r in enumerate(records):

        # Forzar campos canónicos (sobreescribe lo que traiga el JSON)
        r["antenna_type"] = antenna_type
        r["material"]     = material

        # Validar claves raíz
        for key in REQUIRED_ROOT:
            if key not in r:
                raise ValueError(
                    f"  [ARCHIVO {ruta}] Documento {idx}: falta '{key}'"
                )

        # Validar geometría
        for key in REQUIRED_GEOMETRY:
            if key not in r.get("geometry", {}):
                raise ValueError(
                    f"  [ARCHIVO {ruta}] Documento {idx}: geometry.{key} faltante"
                )

        # Validar longitudes
        n = len(r["freq"])
        if len(r["Rin"]) != n or len(r["Xin"]) != n:
            raise ValueError(
                f"  [ARCHIVO {ruta}] Documento {idx}: freq/Rin/Xin tienen tamaños distintos"
            )

        validos += 1

    print(f"  Validados          : {validos}/{len(records)}")

    # ----------------------------------------------------------
    # Upsert
    # ----------------------------------------------------------
    inserted = 0
    updated  = 0
    errors   = 0

    for idx, r in enumerate(records):

        filtro = {
            "antenna_type": r["antenna_type"],
            "material":     r["material"],
            "geometry.L":   r["geometry"]["L"],
            "geometry.a":   r["geometry"]["a"],
        }

        # Todo el documento menos _id va en $set
        update_fields = {k: v for k, v in r.items() if k != "_id"}

        try:
            result = coll.update_one(
                filtro,
                {"$set": update_fields},
                upsert=True,
            )
            if result.upserted_id is not None:
                inserted += 1
            else:
                updated += 1

        except Exception as e:
            errors += 1
            print(f"  [ERROR] doc {idx}: {e}")

        # Progreso
        if (idx + 1) % BATCH_SIZE == 0 or (idx + 1) == len(records):
            print(
                f"  Procesados: {idx+1:>5}/{len(records)}"
                f"  |  nuevos: {inserted}"
                f"  |  actualizados: {updated}"
                f"  |  errores: {errors}"
            )

    total_inserted += inserted
    total_updated  += updated
    total_errors   += errors

    print(
        f"  ✔  Listo — nuevos: {inserted}  "
        f"actualizados: {updated}  errores: {errors}"
    )

# ==========================================================
# RESUMEN FINAL
# ==========================================================

count = coll.count_documents({})

print("\n" + "=" * 60)
print("RESUMEN FINAL")
print("=" * 60)
print(f"Documentos totales en colección : {count}")
print(f"  Nuevos insertados             : {total_inserted}")
print(f"  Actualizados                  : {total_updated}")
print(f"  Errores                       : {total_errors}")
print("=" * 60)

# Desglose por tipo y material
for at in ("dipole", "monopole"):
    for mat in ("copper", "silver"):
        n = coll.count_documents({"antenna_type": at, "material": mat})
        print(f"  {at:>10} / {mat:<6} : {n} docs")

print("=" * 60)