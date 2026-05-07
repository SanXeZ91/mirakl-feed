import requests
import os
import csv
import io
import re
from datetime import datetime
from collections import Counter
from decimal import Decimal

# ---- CONFIGURACIÓN GLOBAL ----
IVA_FACTOR = 1.21
MARGEN_DEFAULT = 0.12

# Gastos Compuspain
COMPU_PROMO_ENVIO = 4.50 * IVA_FACTOR # 5.445€ con IVA

# Gastos DMI
DMI_ENVIO_BASE = 4.95 * IVA_FACTOR
DMI_GESTION_FIJA = 0.99 * IVA_FACTOR

# ---- CATEGORÍAS Y MÁRGENES (Base unificada) ----
CATEGORIAS = {
    "CAJA": 0.07, "FUEN": 0.08, "PB": 0.07, "VIDE": 0.07,
    "REFR": 0.12, "MONI": 0.07, "MICR": 0.07, "RATO": 0.12,
    "TECL": 0.12, "MEMO": 0.07, "RED": 0.07
}

MARGENES = {
    "CAJA": 0.12, "FUEN": 0.10, "PB": 0.08, "VIDE": 0.08,
    "REFR": 0.12, "MONI": 0.10, "MICR": 0.06, "RATO": 0.16,
    "TECL": 0.16, "MEMO": 0.10, "RED": 0.12
}

# MAPEO DE DMI -> NUESTRAS CATEGORÍAS
# Según me has dicho: 
# PB -> Placas base, VIDE -> Tarjetas, REFR -> Refrigeración, 
# MICR -> Procesadores, RATO/TECL -> Periféricos, MEMO -> Memoria RAM, RED -> Routers y Modems
MAPE_DMI = {
    "Placas base": "PB",
    "Tarjetas": "VIDE",
    "Refrigeración": "REFR",
    "Procesadores": "MICR",
    "Periféricos": "RATO", 
    "Memoria RAM": "MEMO",
    "Routers y Modems": "RED"
}

def fmt_precio(x):
    try: return f"{float(x):.2f}".replace(".", ",")
    except: return "0,00"

def fmt_decimal(x):
    try: return f"{float(x):.2f}"
    except: return "0.00"

def normalize_ean(value):
    s = str(value or "").strip()
    if "e" in s.lower():
        try: s = str(int(Decimal(s.replace(",", "."))))
        except: pass
    return re.sub(r"\D+", "", s)

# ---- LÓGICA DE PRECIOS ----
def calcular_pvp(coste_con_iva, transporte_con_iva, familia):
    if familia not in CATEGORIAS:
        return None
    comision = CATEGORIAS[familia]
    # Caso especial FUEN si lo tenías dinámico, si no, usa el de CATEGORIAS
    if familia == "FUEN" and comision is None:
        p_est = (coste_con_iva + transporte_con_iva) / (1 - 0.08 - MARGENES["FUEN"])
        comision = 0.15 if p_est <= 50 else 0.08
    
    margen = MARGENES[familia]
    divisor = 1 - comision - margen
    if divisor <= 0: return None
    return round((coste_con_iva + transporte_con_iva) / divisor, 2)

# ---- DESCARGA DMI ----
def get_dmi_data():
    print("🔐 [DMI] Autenticando...")
    auth_url = "https://api.dmi.es/api/v2/users/authenticate"
    creds = {
        "username": os.environ.get("DMI_USERNAME"),
        "password": os.environ.get("DMI_PASSWORD")
    }
    headers = {"x-api-key": os.environ.get("DMI_APPKEY"), "Content-Type": "application/json"}
    
    try:
        r = requests.post(auth_url, json=creds, headers=headers, timeout=30)
        token = r.json().get("token")
        
        print("📥 [DMI] Descargando catálogo...")
        catalog_url = "https://api.dmi.es/api/v2/products/CustomCatalog"
        payload = {
            "FileFormat": "csv",
            "Columns": ["PN", "Ean", "Category", "Stock", "PriceOnly"],
            "Separator": ";",
            "ReturnFileDirectly": True
        }
        headers["Authorization"] = f"Bearer {token}"
        r_cat = requests.post(catalog_url, json=payload, headers=headers, timeout=60)
        return r_cat.text
    except Exception as e:
        print(f"❌ [ERROR DMI] {e}")
        return ""

# ---- PROCESAMIENTO PRINCIPAL ----
ofertas_finales = {} 

# 1. PROCESAR COMPUSPAIN
print("📡 [COMPUSPAIN] Descargando y procesando...")
compu_url = os.environ.get("COMPUSPAIN_URL")
if compu_url:
    r = requests.get(compu_url, timeout=60)
    # Detectamos delimitador (por si acaso)
    delimiter = ";" if ";" in r.text.split("\n")[0] else ","
    reader = csv.DictReader(io.StringIO(r.text), delimiter=delimiter)
    for row in reader:
        ean = normalize_ean(row.get("ARTEAN"))
        familia = (row.get("ARTFAMILIACODIGO") or "").strip().upper()
        if ean and familia in CATEGORIAS:
            try:
                coste_str = row.get("ARTPRECIO_RECURSOPRECIO_IMPUESTOS", "0").replace(",", ".")
                coste = float(coste_str)
                stock = int(float(row.get("ARTSTOCKDISPONIBLE") or 0))
                pvp = calcular_pvp(coste, COMPU_PROMO_ENVIO, familia)
                
                if pvp and stock > 2:
                    ofertas_finales[ean] = {
                        "sku": row.get("ARTPARTNUMBER"), "ean": ean, "precio": pvp, 
                        "stock": stock - 2, "canon": row.get("ARTRECURSOIMPORTE", "0"),
                        "iva": row.get("ARTIVAREQUIVALENCIA", "21"),
                        "origen": "CompuSpain"
                    }
            except: continue

# 2. PROCESAR DMI (Y COMPARAR)
dmi_text = get_dmi_data()
if dmi_text:
    reader = csv.DictReader(io.StringIO(dmi_text), delimiter=";")
    for row in reader:
        ean = normalize_ean(row.get("EAN"))
        cat_dmi = row.get("Categoría")
        familia = MAPE_DMI.get(cat_dmi)
        
        if ean and familia:
            try:
                coste_sin_iva = float(row.get("Precio", "0").replace(",", "."))
                # Cálculo transporte DMI
                envio = 0 if (coste_sin_iva * IVA_FACTOR) >= 100 else DMI_ENVIO_BASE
                transporte_total = envio + DMI_GESTION_FIJA
                
                pvp = calcular_pvp(coste_sin_iva * IVA_FACTOR, transporte_total, familia)
                stock = int(float(row.get("Stock Disponible") or 0))
                
                if pvp and stock > 2:
                    # REGLA: Si no existe o si el de DMI es más barato, ganamos
                    if ean not in ofertas_finales or pvp < ofertas_finales[ean]["precio"]:
                        ofertas_finales[ean] = {
                            "sku": row.get("PN"), "ean": ean, "precio": pvp,
                            "stock": stock - 2, "canon": "0.00", "iva": "21.00",
                            "origen": "DMI"
                        }
            except: continue

# 3. ESCRIBIR RESULTADO
out_fieldnames = ["sku", "product-id", "product-id-type", "price", "quantity", "state", "update-delete", "canon", "tipo-iva"]
with open("feed_unificado.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=out_fieldnames, delimiter=";")
    writer.writeheader()
    for ean, data in ofertas_finales.items():
        writer.writerow({
            "sku": data["sku"], "product-id": data["ean"], "product-id-type": "EAN",
            "price": fmt_precio(data["precio"]), "quantity": data["stock"],
            "state": "11", "update-delete": "update", "canon": fmt_decimal(data["canon"]),
            "tipo-iva": fmt_decimal(data["iva"])
        })

print(f"✅ [OK] Proceso terminado. Feed unificado con {len(ofertas_finales)} productos.")
