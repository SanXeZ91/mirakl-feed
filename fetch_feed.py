import requests
import os
from datetime import datetime

# Lee la URL desde los Secrets de GitHub
URL = os.environ.get("COMPUSPAIN_URL")

if not URL:
    print("Error: No se ha encontrado la URL en los Secrets")
    exit(1)

print(f"Descargando feed desde el distribuidor... {datetime.now()}")

response = requests.get(URL, timeout=30)
response.raise_for_status()

# Guarda el archivo en la RAÍZ (sin carpeta public)
with open("feed.csv", "w", encoding="utf-8") as f:
    f.write(response.text)

print(f"✅ Feed actualizado con éxito: {datetime.now()}")
print(f"Tamaño del archivo: {len(response.text)} caracteres")
