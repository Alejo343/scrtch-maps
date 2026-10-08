"""
Parsing de las paginas de Google Maps.

Los selectores de clase (Nv2PK, hfpxzc, DUwDvf, etc.) son los que Google usa
actualmente, pero los cambia sin aviso. Si la extraccion empieza a devolver
campos vacios, lo primero a revisar es esto: abrir una pagina de resultado en
modo HEADLESS=false e inspeccionar el DOM de nuevo.
"""
import re

from playwright.async_api import Page

COORDS_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),")
# Los botones de info (direccion, telefono) incluyen un icono renderizado con
# un glifo de fuente privada (Private Use Area) que innerText trae como texto.
PRIVATE_USE_RE = re.compile(r"[-]")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", PRIVATE_USE_RE.sub("", text)).strip()


async def collect_result_links(page: Page) -> dict[str, str]:
    """Devuelve {href: nombre} de las tarjetas visibles actualmente en el feed."""
    cards = page.locator('a.hfpxzc')
    count = await cards.count()
    results: dict[str, str] = {}
    for i in range(count):
        el = cards.nth(i)
        href = await el.get_attribute("href")
        name = await el.get_attribute("aria-label")
        if href:
            results[href] = name or ""
    return results


async def _text_or_none(page: Page, selector: str) -> str | None:
    loc = page.locator(selector).first
    if await loc.count() == 0:
        return None
    text = (await loc.inner_text()).strip()
    return text or None


async def extract_place_details(page: Page) -> dict:
    name = await _text_or_none(page, "h1.DUwDvf")

    category = None
    category_loc = page.locator("button.DkEaL").first
    if await category_loc.count() > 0:
        category = _clean(await category_loc.inner_text()) or None

    rating = None
    rating_text = await _text_or_none(page, "div.F7nice span[aria-hidden='true']")
    if rating_text:
        try:
            rating = float(rating_text.replace(",", "."))
        except ValueError:
            rating = None

    reviews_count = None
    reviews_text = await _text_or_none(page, "div.F7nice span[aria-label*='reseñas'], div.F7nice span[aria-label*='reviews']")
    if reviews_text:
        digits = re.sub(r"[^\d]", "", reviews_text)
        reviews_count = int(digits) if digits else None

    address = None
    addr_loc = page.locator('button[data-item-id="address"]').first
    if await addr_loc.count() > 0:
        address = _clean(await addr_loc.inner_text()) or None

    phone = None
    phone_loc = page.locator('button[data-item-id^="phone:tel:"]').first
    if await phone_loc.count() > 0:
        data_item_id = await phone_loc.get_attribute("data-item-id")
        if data_item_id:
            phone = data_item_id.split("phone:tel:")[-1]

    website = None
    website_loc = page.locator('a[data-item-id="authority"]').first
    if await website_loc.count() > 0:
        website = await website_loc.get_attribute("href")

    url = page.url
    latitude = longitude = None
    match = COORDS_RE.search(url)
    if match:
        latitude, longitude = float(match.group(1)), float(match.group(2))

    return {
        "name": name,
        "category": category,
        "rating": rating,
        "reviews_count": reviews_count,
        "address": address,
        "phone": phone,
        "website": website,
        "latitude": latitude,
        "longitude": longitude,
        "maps_url": url,
    }
