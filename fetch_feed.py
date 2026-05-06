import requests
import os
from datetime import datetime

URL = "https://www.compuspain.eu/download/__cp_VSW/Services/outProductosTarifaDatosAll/?uID=C014557&uLG=WC014557&uPW=W119_9751&urVal=csv&uFR=1"

response = requests.get(URL, timeout=30)
response.raise_for_status()

os.makedirs("public", exist_ok=True)

with open("public/feed.csv", "w", encoding="utf-8") as f:
    f.write(response.text)

print(f"Feed actualizado: {datetime.now()}")
