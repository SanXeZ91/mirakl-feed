import requests
import os
from datetime import datetime

# Lee la URL desde los Secrets de GitHub
URL = os.environ.get("COMPUSPAIN_URL")

if not URL:
    print("Error: No se ha encontrado la URL en los Secrets")
    exit(1)

response = requests.get(URL, timeout=30)
response.raise_for_status()

# Crea la carpeta public si no existe
os.makedirs("public", exist_ok=True)

# Guarda el archivo
with open("public/feed.csv", "w", encoding="utf-8") as f:
    f.write(response.text)

print(f"Feed actualizado con éxito: {datetime.now()}")
