import requests
import os
import csv
import io
from datetime import datetime

# Lee la URL desde los Secrets de GitHub
URL = os.environ.get("COMPUSPAIN_URL")

if not URL:
    print("Error: No se ha encontrado la URL en los Secrets")
    exit(1)

# ---- CONFIGURACIÓN ----
TRANSPORTE_SIN_IVA = 4.50
IVA_TRANSPORTE     = 1.21
TRANSPORTE_CON_IVA = round(TRANSPORTE_SIN_IVA * IVA_TRANSPORTE, 4)  # 5.445€
MARGEN             = 0.10   # 10% sobre el precio de venta
STOCK_SEGURIDAD    = 2      # Unidades que restamos para evitar sobreventa

# Categorías permitidas y sus comisiones
# FUEN tiene comisión variable según precio de venta
CATEGORIAS = {
    "CAJA": 0.07,
    "FUEN": None,   # Variable: 8% si PV > 50€, 15% si PV <= 50€
    "PB":   0.07,
    "VIDE": 0.07,
    "REFR": 0.12,
    "MONI": 0.07,
    "MICR": 0.07,
    "RATO": 0.12,
    "TECL": 0.12,
}

def calcular_precio_venta(coste_con_iva, familia):
    """
    Calcula el precio de venta final aplicando transporte, comisión y margen.
    Fórmula: P = (Coste + Transporte) / (1 - comision - margen)
    Para FUEN la comisión depende del precio de venta, así que iteramos.
    """
    if familia not in CATEGORIAS:
        return None

    comision = CATEGORIAS[familia]

    if familia == "FUEN":
        # Primero estimamos con comisión 8% para saber si el PV > 50€
        precio_estimado = (coste_con_iva + TRANSPORTE_CON_IVA) / (1 - 0.08 - MARGEN)
        if precio_estimado <= 50:
            comision = 0.15
        else:
            comision = 0.08

    divisor = 1 - comision - MARGEN
    if divisor <= 0:
        return None

    precio = (coste_con_iva + TRANSPORTE_CON_IVA) / divisor
    return round(precio, 2)


print(f"Descargando feed desde Compuspain... {datetime.now()}")
response = requests.get(URL, timeout=30)
response.raise_for_status()

# El CSV de Compuspain usa TABULACIÓN como separador
reader = csv.DictReader(io.StringIO(response.text), delimiter='\t')

output_rows = []
skipped     = 0
errors      = 0

for row in reader:
    familia = row.get("ARTFAMILIACODIGO", "").strip().upper()

    # Solo procesamos las categorías que nos interesan
    if familia not in CATEGORIAS:
        skipped += 1
        continue

    try:
        stock_original = int(row.get("ARTSTOCKDISPONIBLE", 0))
        precio_str     = row.get("ARTPRECIO_RECURSOPRECIO_IMPUESTOS", "0").replace(",", ".")
        coste_con_iva  = float(precio_str)
    except (ValueError, TypeError):
        errors += 1
        continue

    # Calculamos precio de venta
    precio_venta = calcular_precio_venta(coste_con_iva, familia)
    if precio_venta is None:
        errors += 1
        continue

    # Stock de seguridad
    quantity = max(0, stock_original - STOCK_SEGURIDAD)

    # Canon e IVA directamente del CSV del distribuidor
    canon   = row.get("ARTRECURSOIMPORTE", "").strip()
    tipo_iva = row.get("ARTIVAREQUIVALENCIA", "").strip()

    output_rows.append({
        "sku":                 row.get("ARTPARTNUMBER", "").strip(),
        "product-id":          row.get("ARTEAN", "").strip(),
        "product-id-type":     "EAN",
        "price":               precio_venta,
        "quantity":            quantity,
        "state":               "11",   # 11 = Nuevo
        "discount-price":      "",
        "discount-start-date": "",
        "discount-end-date":   "",
        "update-delete":       "update",
        "canon":               canon,
        "tipo-iva":            tipo_iva,
    })

# Escribimos el CSV final con ; como separador (estándar Mirakl)
fieldnames = [
    "sku", "product-id", "product-id-type", "price", "quantity", "state",
    "discount-price", "discount-start-date", "discount-end-date",
    "update-delete", "canon", "tipo-iva"
]

output = io.StringIO()
writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=';')
writer.writeheader()
writer.writerows(output_rows)

with open("feed.csv", "w", encoding="utf-8") as f:
    f.write(output.getvalue())

print(f"✅ Feed generado correctamente: {datetime.now()}")
print(f"✅ Ofertas incluidas:  {len(output_rows)}")
print(f"⏭️  Categorías omitidas: {skipped} productos")
print(f"⚠️  Errores de datos:    {errors} productos")
