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

# ---- CATEGORÍAS Y MÁRGENES ----
CATEGORIAS = {
    "CAJA": 0.07, "FUEN": 0.08, "PB": 0.07, "VIDE": 0.07,
    "REFR": 0.12, "MONI": 0.07, "MICR": 0.07, "RATO": 0.12,
    "TECL": 0.12, "MEMO": 0.07, "MULT": 0.15, "RED": 0.07, "ACCE": 0.15,
    "ADAP": 0.15, "CABL": 0.15, "CONS": 0.12, "ELEC": 0.15,
}

MARGENES = {
    "CAJA": 0.09,
    "FUEN": 0.06,
    "PB": 0.04,
    "VIDE": 0.04,
    "REFR": 0.05,
    "MONI": 0.05,
    "MICR": 0.04,
    "RATO": 0.06,
    "TECL": 0.06,
    "MEMO": 0.04,
    "MULT": 0.07,
    "RED": 0.07,
    "ACCE": 0.09,
    "ADAP": 0.09,
    "CABL": 0.08,
    "CONS": 0.08,
    "ELEC": 0.09,
}

# MAPEO DE DMI -> NUESTRAS CATEGORÍAS
MAPE_DMI = {
    "Placas base": "PB",
    "Tarjetas": "VIDE",
    "Refrigeración": "REFR",
    "Procesadores": "MICR",
    "Periféricos": "RATO",
    "Memoria RAM": "MEMO",
    "Routers y Modems": "RED",
    "Repetidores y extensores": "RED",
    "Switches y Transceptores": "RED",
    "Wifi": "RED",
    "Accesorios portátiles": "ACCE",
    "Adaptadores y Convertidores": "ADAP",
    "Altavoces": "MULT",
    "Auriculares": "MULT",
    "Cables y Conectores": "CABL",
    "Cajas y fuentes": "FUEN",
    "Consumibles": "CONS",
    "Cuidado personal": "ELEC",
    "Micrófonos": "MULT",
    "Pequeños electrodomésticos": "ELEC",
    
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

# -------------------------
# Función: parse_amount
# -------------------------
def parse_amount(s):
    """
    Parsea cantidades con formatos europeos o internacionales:
    - Elimina € y espacios
    - Convierte '1.234,56' -> 1234.56
    - Convierte '1.234' -> 1234
    - Convierte '1090,00' -> 1090.0
    Devuelve float. En caso de fallo devuelve 0.0 y deja un WARNING.
    """
    if s is None:
        return 0.0
    s = str(s).strip()

    # limpiar símbolos comunes
    for ch in ['€', 'EUR', ' ']:
        s = s.replace(ch, '')

    # Si tiene punto y coma: punto = miles, coma = decimal
    if '.' in s and ',' in s:
        s = s.replace('.', '').replace(',', '.')
    else:
        # si solo tiene coma -> coma decimal
        if ',' in s and '.' not in s:
            s = s.replace(',', '.')
        # si solo tiene punto y la parte tras el último punto tiene 3 dígitos -> punto = miles
        elif '.' in s:
            parts = s.split('.')
            if len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) == 3:
                s = s.replace('.', '')

    # eliminar cualquier carácter extraño excepto dígitos, punto y signo menos
    s = re.sub(r'[^0-9\.\-]', '', s)
    try:
        return float(s) if s != '' else 0.0
    except Exception:
        print(f"WARNING: no pude parsear amount raw='{s}', devolviendo 0.0")
        return 0.0

# ---- LÓGICA DE PRECIOS ----
def calcular_pvp(coste_con_iva, transporte_con_iva, familia):
    if familia not in CATEGORIAS:
        return None
    comision = CATEGORIAS[familia]
    if familia == "FUEN" and comision is None:
        p_est = (coste_con_iva + transporte_con_iva) / (1 - 0.08 - MARGENES["FUEN"])
        comision = 0.15 if p_est <= 50 else 0.08
    margen = MARGENES.get(familia, MARGEN_DEFAULT)
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
    app_key = os.environ.get("DMI_APPKEY")
    headers = {
        "x-api-key": app_key,
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(auth_url, json=creds, headers=headers, timeout=30)
        auth_json = r.json()
        token = auth_json.get("token")

        if not token:
            print(f"❌ [DMI] No se obtuvo token. Respuesta: {str(auth_json)[:300]}")
            return ""

        print(f"✅ [DMI] Token obtenido correctamente.")

        print("📥 [DMI] Descargando catálogo...")
        catalog_url = "https://api.dmi.es/api/v2/products/CustomCatalog"
        payload = {
            "FileFormat": "csv",
            "Columns": [
                "ProductId", "ManufacturerCode", "Ean", "Category",
                "Manufacturer", "Name", "Stock", "PriceOnly"
            ],
            "Encoding": "UTF-8",
            "Separator": ";",
            "SubSeparator": "|",
            "FieldSeparator": "=",
            "ColumnNames": {
                "ProductId": "Código de Producto",
                "ManufacturerCode": "PN",
                "Ean": "EAN",
                "Category": "Categoría",
                "Manufacturer": "Fabricante",
                "Name": "Nombre",
                "Stock": "Stock Disponible",
                "PriceOnly": "Precio"
            },
            "OutputFileName": "catalogo_personalizado",
            "ReturnFileDirectly": True,
            "Page": 1,
            "PageSize": 1000000,
            "Currency": "EUR",
            "Language": "es"
        }

        catalog_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "x-api-key": app_key
        }

        r_cat = requests.post(catalog_url, json=payload, headers=catalog_headers, timeout=120)

        if r_cat.status_code != 200:
            print(f"❌ [DMI] Error catálogo (status {r_cat.status_code}): {r_cat.text[:300]}")
            return ""

        return r_cat.content.decode("utf-8-sig")

    except Exception as e:
        print(f"❌ [ERROR DMI] {e}")
        return ""

# ---- PROCESAMIENTO PRINCIPAL ----
ofertas_finales = {}

# 1. PROCESAR COMPUSPAIN
print("📡 [COMPUSPAIN] Descargando y procesando...")
compu_total = 0
compu_skipped_no_ean = 0
compu_skipped_no_family = 0
compu_skipped_parse = 0
compu_added = 0

compu_url = os.environ.get("COMPUSPAIN_URL")
if compu_url:
    r = requests.get(compu_url, timeout=60)
    delimiter = ";" if ";" in r.text.split("\n")[0] else ","
    reader = csv.DictReader(io.StringIO(r.text), delimiter=delimiter)
    for row in reader:
        compu_total += 1
        ean = normalize_ean(row.get("ARTEAN"))
        if not ean:
            compu_skipped_no_ean += 1
            continue
        familia = (row.get("ARTFAMILIACODIGO") or "").strip().upper()
        if familia not in CATEGORIAS:
            compu_skipped_no_family += 1
            continue
        try:
            coste_raw = row.get("ARTPRECIO_RECURSOPRECIO_IMPUESTOS", "0")
            coste = parse_amount(coste_raw)  # suponemos que este campo ya incluye IVA
            stock = int(float(row.get("ARTSTOCKDISPONIBLE") or 0))
            pvp = calcular_pvp(coste, COMPU_PROMO_ENVIO, familia)

            if pvp:
                qty = max(stock - 3, 0)
                ofertas_finales[ean] = {
                    "sku": row.get("ARTPARTNUMBER"),
                    "ean": ean,
                    "precio": pvp,
                    "stock": qty,
                    "canon": 0.0,
                    "iva": 21.0,
                    "origen": "CompuSpain"
                }
                compu_added += 1
        except Exception as e:
            compu_skipped_parse += 1
            continue

print(f"[COMPUSPAIN] Total filas: {compu_total} | Añadidas: {compu_added} | Sin EAN: {compu_skipped_no_ean} | Familia no válida: {compu_skipped_no_family} | Error parse: {compu_skipped_parse}")

# 2. PROCESAR DMI (Y COMPARAR)
print("📡 [DMI] Descargando y procesando...")
dmi_total = 0
dmi_skipped_no_ean = 0
dmi_skipped_no_category = 0
dmi_skipped_parse = 0
dmi_added_or_updated = 0

dmi_text = get_dmi_data()

if dmi_text and len(dmi_text) > 100:
    first_line = dmi_text.split('\n')[0]
    dmi_delimiter = ";" if ";" in first_line else ","

    rows_dmi = list(csv.DictReader(io.StringIO(dmi_text), delimiter=dmi_delimiter))

    for row in rows_dmi:
        dmi_total += 1

        ean_raw = row.get("EAN") or row.get("Ean")
        ean = normalize_ean(ean_raw)

        if not ean:
            dmi_skipped_no_ean += 1
            continue

        cat_dmi = row.get("Categoría") or row.get("Category")
        familia = MAPE_DMI.get(cat_dmi)

        if not familia:
            dmi_skipped_no_category += 1
            continue

        try:
            precio_raw = row.get("Precio") or row.get("PriceOnly") or "0"
            coste_sin_iva = parse_amount(precio_raw)

            stock_raw = row.get("Stock Disponible") or row.get("Stock") or "0"
            stock = int(parse_amount(stock_raw))

            envio_con_iva = 0 if coste_sin_iva >= 100 else DMI_ENVIO_BASE
            transporte_total = envio_con_iva + DMI_GESTION_FIJA

            coste_con_iva = coste_sin_iva * IVA_FACTOR
            pvp = calcular_pvp(coste_con_iva, transporte_total, familia)

            if pvp:
                qty = max(stock - 4, 0)

                if ean not in ofertas_finales:
                    # Producto nuevo, solo añadir si DMI tiene stock
                    if qty > 0:
                        ofertas_finales[ean] = {
                            "sku": row.get("PN"),
                            "ean": ean,
                            "precio": pvp,
                            "stock": qty,
                            "canon": 0.0,
                            "iva": 21.0,
                            "origen": "DMI"
                        }
                        dmi_added_or_updated += 1
                else:
                    existing = ofertas_finales[ean]
                    existing_qty = int(existing.get("stock", 0))

                    # Si DMI no tiene stock, no tocar lo que ya hay
                    if qty == 0:
                        continue

                    # Si DMI tiene stock y el existente no, DMI gana
                    if existing_qty == 0:
                        ofertas_finales[ean] = {
                            "sku": row.get("PN"),
                            "ean": ean,
                            "precio": pvp,
                            "stock": qty,
                            "canon": 0.0,
                            "iva": 21.0,
                            "origen": "DMI"
                        }
                        dmi_added_or_updated += 1
                        continue

                    # Ambos tienen stock → gana el más barato
                    if pvp < float(existing.get("precio", 1e18)):
                        ofertas_finales[ean] = {
                            "sku": row.get("PN"),
                            "ean": ean,
                            "precio": pvp,
                            "stock": qty,
                            "canon": 0.0,
                            "iva": 21.0,
                            "origen": "DMI"
                        }
                        dmi_added_or_updated += 1

        except Exception as e:
            dmi_skipped_parse += 1
            continue

else:
    print("⚠️ [DMI] El catálogo descargado está vacío o es demasiado corto.")

print(f"[DMI] Total filas: {dmi_total} | Añadidas/actualizadas: {dmi_added_or_updated} | Sin EAN: {dmi_skipped_no_ean} | Categoría no mapeada: {dmi_skipped_no_category} | Error parse: {dmi_skipped_parse}")

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
