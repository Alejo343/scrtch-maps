"""
Geocodificacion de nombres de ciudad a un area (bounding box) usando
Nominatim (OpenStreetMap), que es gratuito. Limite de uso: max 1 request/seg
y un User-Agent descriptivo - por eso esto se usa solo una vez por ciudad,
no por cada busqueda.
"""
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "gmaps-scraper/0.1 (uso personal, contacto: davidsupremoxd@gmail.com)"}


def get_city_bbox(city: str) -> tuple[float, float, float, float]:
    """Devuelve (south, north, west, east) en grados para el nombre de ciudad dado."""
    params = {"q": city, "format": "jsonv2", "limit": 1}
    resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"No se encontro la ciudad: '{city}'")

    south, north, west, east = results[0]["boundingbox"]
    return float(south), float(north), float(west), float(east)
