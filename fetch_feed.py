#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import io
import re
import sys
import time
import urllib.request
import urllib.parse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# =========================
# 1) CONFIGURACIÓN
# =========================

# URL base del feed (sin credenciales en duro si puedes; usa Secrets)
DISTRIBUTOR_BASE_URL = os.getenv(
    "DISTRIBUTOR_BASE_URL",
    "https://www.compuspain.eu/download/__cp_VSW/Services/outProductosTarifaDatosAll/"
)

# Parámetros del distribuidor (MEJOR en GitHub Secrets)
DISTRIBUTOR_UID = os.getenv("COMPUSPAIN_UID", "")
DISTRIBUTOR_ULG = os.getenv("COMPUSPAIN_ULG", "")
DISTRIBUTOR_UPW = os.getenv("COMPUSPAIN_UPW", "")

# Otros params típicos de tu URL
DISTRIBUTOR_URVAL = os.getenv("COMPUSPAIN_URVAL", "csv")  # ojo: en tu URL aparece "urVal"
DISTRIBUTOR_UFR = os.getenv("COMPUSPAIN_UFR", "1")

# Donde guardar archivos para servirlos por GitHub Pages
OUT_DIR = os.getenv("OUT_DIR", "docs")
RAW_FILENAME = os.getenv("RAW_FILENAME", "distributor_raw.csv")
MIRAKL_FILENAME = os.getenv("MIRAKL_FILENAME", "mirakl_offers.csv")

# CSV de salida (ajusta a lo que pida Mirakl)
OUTPUT_DELIMITER = os.getenv("OUTPUT_DELIMITER", ";")
OUTPUT_ENCODING = "utf-8"

# Columns esperadas por TU importador Mirakl (ajústalo exactamente a tu plantilla)
# Ejemplo típico (pero puede variar según marketplace/config):
OUTPUT_COLUMNS = [
    "sku",
    "product-id",
    "product-id-type",
    "price",
    "quantity",
    # Si tu plantilla tiene estas, descomenta y rellena abajo:
    # "canon",
    # "tipo-iva",
]

# Si tu Mirakl exige campos extra fijos, ponlos aquí (nombre EXACTO de columna en tu plantilla)
EXTRA_FIXED_FIELDS = {
    # "tipo-iva": "21",
    # "canon": "0",
}

# =========================
# 2) MAPEOS: columnas origen
#    (ajusta a tu CSV del distribuidor)
# =========================

SOURCE_SKU_COLUMNS = ["SKU", "sku", "ARTCOD", "ARTICULO", "CODIGO", "Codigo", "codigo"]
SOURCE_EAN_COLUMNS = ["ARTEAN", "EAN", "ean", "CODBARRAS", "cod_barras", "barcode", "BarCode"]
SOURCE_STOCK_COLUMNS = ["STOCK", "stock", "QTY", "qty", "Cantidad", "cantidad", "UNIDADES", "unidades"]
SOURCE_PRICE_COLUMNS = ["PVP", "pvp", "Precio", "precio", "PRICE", "price", "PVPR", "TARIFA"]

# Si tu import de Mirakl trabaja por EAN: normalmente product-id-type = "EAN"
DEFAULT_PRODUCT_ID_TYPE = os.getenv("DEFAULT_PRODUCT_ID_TYPE", "EAN")


# =========================
# 3) Helpers
# =========================

def build_distributor_url() -> str:
    """
    Construye la URL con query params. Si no quieres exponer credenciales en logs,
    asegúrate de usar Secrets y NO imprimir la URL completa.
    """
    params = {}
    if DISTRIBUTOR_UID:
        params["uID"] = DISTRIBUTOR_UID
    if DISTRIBUTOR_ULG:
        params["uLG"] = DISTRIBUTOR_ULG
    if DISTRIBUTOR_UPW:
        params["uPW"] = DISTRIBUTOR_UPW

    # En tu URL aparece "urVal" (no "uVal"), lo respetamos:
    if DISTRIBUTOR_URVAL:
        params["urVal"] = DISTRIBUTOR_URVAL
    if DISTRIBUTOR_UFR:
        params["uFR"] = str(DISTRIBUTOR_UFR)

    qs = urllib.parse.urlencode(params, doseq=True)
    return f"{DISTRIBUTOR_BASE_URL}?{qs}" if qs else DISTRIBUTOR_BASE_URL


def ensure_out_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def download_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mirakl-feed-fetcher/1.0",
            "Accept": "*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decode_csv_bytes(b: bytes) -> str:
    # Intentos comunes: UTF-8 con BOM, UTF-8, Latin-1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    # último recurso
    return b.decode("latin-1", errors="replace")


def sniff_dialect(sample_text: str):
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect
    except csv.Error:
        # default razonable
        class D:
            delimiter = ";"
            quotechar = '"'
            escapechar = None
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return D()


def get_first(row: dict, candidates: list[str]) -> str:
    for k in candidates:
        if k in row and row[k] is not None:
            v = str(row[k]).strip()
            if v != "":
                return v
    return ""


def to_decimal_loose(x: str) -> Decimal | None:
    """
    Convierte números que pueden venir como:
    - "1234.56"
    - "1.234,56"
    - "1234,56"
    - "8,43E+12"
    """
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None

    # Quitar espacios
    s = s.replace(" ", "")

    # Caso notación científica con coma decimal (8,43E+12)
    # pasamos coma a punto si parece un decimal
    if re.search(r"[eE]", s) and "," in s and "." not in s:
        s = s.replace(",", ".")

    # Caso europeo: "1.234,56" -> "1234.56"
    if "," in s and "." in s:
        # asumimos "." miles y "," decimal
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        # asumimos "," decimal
        s = s.replace(",", ".")

    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def normalize_ean(value: str) -> str:
    """
    Devuelve solo dígitos.
    Si viene en notación científica, lo convierte a entero.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if s == "":
        return ""

    d = to_decimal_loose(s)
    if d is not None:
        # Convertimos a entero (truncando decimales si existieran)
        try:
            i = int(d.to_integral_value(rounding=ROUND_HALF_UP))
            s2 = str(i)
        except Exception:
            s2 = s
    else:
        s2 = s

    digits = re.sub(r"\D+", "", s2)
    return digits


def fmt_quantity(x: str) -> str:
    d = to_decimal_loose(x)
    if d is None:
        return "0"
    try:
        return str(int(d))
    except Exception:
        return "0"


def fmt_price(x: str) -> str:
    d = to_decimal_loose(x)
    if d is None:
        return ""
    # 2 decimales con punto
    d2 = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(d2, "f")


# =========================
# 4) Transformación principal
# =========================

def transform(distributor_text: str) -> tuple[list[str], list[dict]]:
    # Tomamos una muestra para detectar separador
    sample = distributor_text[:4096]
    dialect = sniff_dialect(sample)

    f = io.StringIO(distributor_text)
    reader = csv.DictReader(f, delimiter=dialect.delimiter)

    if not reader.fieldnames:
        raise RuntimeError("El CSV no tiene cabecera (fieldnames vacíos).")

    out_rows = []
    for row in reader:
        # 1) Obtener valores desde el CSV origen
        sku = get_first(row, SOURCE_SKU_COLUMNS)
        ean_raw = get_first(row, SOURCE_EAN_COLUMNS)
        stock_raw = get_first(row, SOURCE_STOCK_COLUMNS)
        price_raw = get_first(row, SOURCE_PRICE_COLUMNS)

        ean = normalize_ean(ean_raw)

        # 2) Reglas mínimas de filtrado
        # Si no hay EAN ni SKU, no podemos publicar oferta
        if sku == "" and ean == "":
            continue

        # quantity / price
        quantity = fmt_quantity(stock_raw)
        price = fmt_price(price_raw)

        # 3) Construir fila salida según plantilla Mirakl
        out = {col: "" for col in OUTPUT_COLUMNS}

        # Estos nombres son los típicos; si tu plantilla usa otros, cambia OUTPUT_COLUMNS arriba
        if "sku" in out:
            out["sku"] = sku
        if "product-id" in out:
            out["product-id"] = ean
        if "product-id-type" in out:
            out["product-id-type"] = DEFAULT_PRODUCT_ID_TYPE
        if "quantity" in out:
            out["quantity"] = quantity
        if "price" in out:
            out["price"] = price

        # Campos fijos extra (si los necesitas)
        for k, v in EXTRA_FIXED_FIELDS.items():
            if k in out:
                out[k] = v

        out_rows.append(out)

    return reader.fieldnames, out_rows


def write_csv(path: str, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding=OUTPUT_ENCODING, newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=columns,
            delimiter=OUTPUT_DELIMITER,
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    url = build_distributor_url()

    # Evita loguear password en GitHub Actions
    safe_url = DISTRIBUTOR_BASE_URL
    print(f"Downloading distributor CSV from base: {safe_url}")

    b = download_bytes(url)
    text = decode_csv_bytes(b)

    ensure_out_dir(OUT_DIR)

    raw_path = os.path.join(OUT_DIR, RAW_FILENAME)
    with open(raw_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"Saved raw CSV -> {raw_path} ({len(text)} chars)")

    in_fields, out_rows = transform(text)
    print(f"Input columns detected ({len(in_fields)}): {in_fields}")
    print(f"Output rows: {len(out_rows)}")

    mirakl_path = os.path.join(OUT_DIR, MIRAKL_FILENAME)
    write_csv(mirakl_path, OUTPUT_COLUMNS, out_rows)
    print(f"Saved Mirakl offers CSV -> {mirakl_path}")

    # Indicador simple de "última actualización"
    ts_path = os.path.join(OUT_DIR, "last_update.txt")
    with open(ts_path, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S %z"))
    print(f"Saved timestamp -> {ts_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
