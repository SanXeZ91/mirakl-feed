import requests
import os
import csv
import io
import re
from datetime import datetime
from collections import Counter
from decimal import Decimal, InvalidOperation

# Si Mirakl te daba error de formato en canon/tipo-iva, prueba con "." en vez de ","
DECIMAL_SEPARATOR = ","  # cambia a "." si Mirakl lo exige

def fmt_precio(x):
    """Para price: usa coma decimal"""
    try:
        return f"{float(x):.2f}".replace(".", ",")
    except (ValueError, TypeError):
        return "0,00"

def fmt_decimal(x):
    """Para canon y tipo-iva: usa punto decimal"""
    try:
        return f"{float(x):.2f}".replace(",", ".")
    except (ValueError, TypeError):
        return "0.00"

def safe_decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            pass
    return content.decode("utf-8", errors="replace")

def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t;,")
        return dialect.delimiter
    except Exception:
        header = (text.splitlines()[0] if text.splitlines() else "")
        counts = {d: header.count(d) for d in ["\t", ";", ","]}
        return max(counts, key=counts.get) if counts else "\t"

def parse_int(value) -> int:
    s = ("" if value is None else str(value)).strip().replace(",", ".")
    if s == "":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0

def parse_float(value) -> float:
    s = ("" if value is None else str(value)).strip().replace(",", ".")
    if s == "":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

def normalize_ean(value: str) -> str:
    """
    - Si viene tipo 8,43E+12 -> lo convierte a entero sin notación científica
    - Luego deja solo dígitos
    """
    s = ("" if value is None else str(value)).strip()
    if not s:
        return ""

    s_no_spaces = s.replace(" ", "")

    # Si viene en notación científica, usar Decimal (más seguro que float)
    if "e" in s_no_spaces.lower():
        # si usa coma decimal, la pasamos a punto para Decimal
        s_norm = s_no_spaces.replace(",", ".")
        try:
            as_int = int(Decimal(s_norm))
            s_no_spaces = str(as_int)
        except (InvalidOperation, ValueError):
            # si no se puede convertir, seguimos con el original
            pass

    # Dejar solo dígitos
    digits = re.sub(r"\D+", "", s_no_spaces)
    return digits

def calcular_precio_venta(coste_con_iva: float, familia: str):
    if familia not in CATEGORIAS:
        return None

    if familia == "FUEN":
        p_est = (coste_con_iva + TRANSPORTE_CON_IVA) / (1 - 0.08 - margen)
        comision = 0.15 if p_est <= 50 else 0.08
    else:
        comision = CATEGORIAS[familia]

    divisor = 1 - comision - margen
    if divisor <= 0:
        return None

    p = (coste_con_iva + TRANSPORTE_CON_IVA) / divisor
    return round(p, 2)

URL = os.environ.get("COMPUSPAIN_URL")
if not URL:
    raise SystemExit("Error: falta el secret COMPUSPAIN_URL")

# ---- CONFIG ----
TRANSPORTE_SIN_IVA = 4.50
IVA_TRANSPORTE = 1.21
TRANSPORTE_CON_IVA = TRANSPORTE_SIN_IVA * IVA_TRANSPORTE  # 5.445
STOCK_SEGURIDAD = 2

CATEGORIAS = {
    "CAJA": 0.07,
    "FUEN": None,  # variable
    "PB": 0.07,
    "VIDE": 0.07,
    "REFR": 0.12,
    "MONI": 0.07,
    "MICR": 0.07,
    "RATO": 0.12,
    "TECL": 0.12,
}

MARGENES = {
    "CAJA": 0.15,
    "FUEN": 0.12,
    "PB": 0.10,
    "VIDE": 0.10,
    "REFR": 0.15,
    "MONI": 0.10,
    "MICR": 0.10,
    "RATO": 0.18,
    "TECL": 0.18,
}

REQUIRED_INPUT_COLS = [
    "ARTPARTNUMBER",
    "ARTEAN",
    "ARTSTOCKDISPONIBLE",
    "ARTFAMILIACODIGO",
    "ARTIVAREQUIVALENCIA",
    "ARTRECURSOIMPORTE",
    "ARTPRECIO_RECURSOPRECIO_IMPUESTOS",
]

print(f"[{datetime.now()}] Descargando feed...")
resp = requests.get(URL, timeout=60)
resp.raise_for_status()

text = safe_decode(resp.content)
delimiter = sniff_delimiter(text)
print(f"[INFO] Delimitador detectado: {repr(delimiter)}")

reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

fieldnames_in = reader.fieldnames or []
missing = [c for c in REQUIRED_INPUT_COLS if c not in fieldnames_in]
print(f"[INFO] Columnas detectadas ({len(fieldnames_in)}): {fieldnames_in}")
if missing:
    raise SystemExit(f"[ERROR] Faltan columnas esperadas: {missing}")

out_rows = []
familias_counter = Counter()
skipped_cat = 0
errors = 0
total = 0

for row in reader:
    total += 1
    familia = (row.get("ARTFAMILIACODIGO") or "").strip().upper()
    familias_counter[familia] += 1

    if familia not in CATEGORIAS:
        skipped_cat += 1
        continue

    sku = (row.get("ARTPARTNUMBER") or "").strip()

    ean_raw = (row.get("ARTEAN") or "").strip()
    ean = normalize_ean(ean_raw)

    if not sku or not ean:
        errors += 1
        continue

    stock = parse_int(row.get("ARTSTOCKDISPONIBLE"))
    coste = parse_float(row.get("ARTPRECIO_RECURSOPRECIO_IMPUESTOS"))
    canon = parse_float(row.get("ARTRECURSOIMPORTE"))
    tipo_iva = parse_float(row.get("ARTIVAREQUIVALENCIA"))

    price = calcular_precio_venta(coste, familia)
    if price is None:
        errors += 1
        continue

    quantity = max(0, stock - STOCK_SEGURIDAD)

    out_rows.append({
        "sku": sku,
        "product-id": ean,
        "product-id-type": "EAN",
        "price": fmt_precio(price),
        "quantity": quantity,
        "state": "11",
        "update-delete": "update",
        "canon": fmt_decimal(canon),
        "tipo-iva": fmt_decimal(tipo_iva),
    })

print(f"[INFO] Filas totales leídas: {total}")
print(f"[INFO] Top familias (20): {familias_counter.most_common(20)}")
print(f"[INFO] Incluidas: {len(out_rows)} | Omitidas (cat): {skipped_cat} | Errores: {errors}")

if len(out_rows) == 0:
    raise SystemExit("[ERROR] feed.csv generado sin productos. Revisa códigos ARTFAMILIACODIGO.")

out_fieldnames = [
    "sku", "product-id", "product-id-type", "price", "quantity", "state",
    "update-delete", "canon", "tipo-iva"
]

output = io.StringIO()
writer = csv.DictWriter(output, fieldnames=out_fieldnames, delimiter=";")
writer.writeheader()
writer.writerows(out_rows)

with open("feed.csv", "w", encoding="utf-8", newline="") as f:
    f.write(output.getvalue())

print(f"[OK] feed.csv escrito correctamente con {len(out_rows)} ofertas.")
