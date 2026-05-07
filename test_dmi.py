import requests
import os
import json

def test_dmi():
    print("🚀 Iniciando prueba de conexión con DMI...")
    
    # 1. Autenticación
    auth_url = "https://api.dmi.es/api/v2/users/authenticate"
    creds = {
        "username": os.environ.get("DMI_USERNAME"),
        "password": os.environ.get("DMI_PASSWORD")
    }
    headers = {
        "x-api-key": os.environ.get("DMI_APPKEY"),
        "Content-Type": "application/json"
    }
    
    try:
        print(f"🔐 Intentando login con usuario: {creds['username']}")
        r = requests.post(auth_url, json=creds, headers=headers, timeout=30)
        
        if r.status_code != 200:
            print(f"❌ Error en login. Status: {r.status_code}")
            print(f"Respuesta: {r.text}")
            return

        token = r.json().get("token")
        print("✅ Token obtenido correctamente.")

        # 2. Prueba de descarga de catálogo (solo 5 productos para probar)
        print("📋 Solicitando una muestra del catálogo...")
        catalog_url = "https://api.dmi.es/api/v2/products/CustomCatalog"
        payload = {
            "FileFormat": "csv",
            "Columns": ["PN", "Ean", "Category", "Stock", "PriceOnly"],
            "Separator": ";",
            "ReturnFileDirectly": True,
            "PageSize": 5  # Solo pedimos 5 para ir rápido
        }
        headers["Authorization"] = f"Bearer {token}"
        
        r_cat = requests.post(catalog_url, json=payload, headers=headers, timeout=60)
        
        if r_cat.status_code == 200:
            print("✅ Catálogo recibido con éxito!")
            print("📄 Contenido de la muestra:")
            print("-" * 30)
            print(r_cat.text)
            print("-" * 30)
        else:
            print(f"❌ Error al descargar catálogo. Status: {r_cat.status_code}")
            print(f"Respuesta: {r_cat.text}")

    except Exception as e:
        print(f"💥 Error inesperado: {e}")

if __name__ == "__main__":
    test_dmi()
