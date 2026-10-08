import asyncio
import logging
import random
from typing import Callable
from urllib.parse import quote

from playwright.async_api import BrowserContext
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_random_exponential

from .config import settings
from .extractor import collect_result_links, extract_place_details

log = logging.getLogger("gmaps_scraper")

# Si una celda devuelve esta cantidad o mas, asumimos que probablemente topo
# el limite no documentado de Google (~100-120) y que puede haber mas
# resultados que no se estan mostrando - se marca como "truncated" para que
# el worker la subdivida en celdas mas chicas en vez de darla por completa.
TRUNCATION_THRESHOLD = 90


class BlockedError(Exception):
    """Google redirigio a su pagina de captcha/bloqueo (.../sorry/...) en vez
    de mostrar el resultado esperado. No tiene sentido reintentar de inmediato
    ni seguir con el resto de la cola usando la misma IP."""


async def _random_delay() -> None:
    await asyncio.sleep(random.uniform(settings.min_delay, settings.max_delay))


async def _screenshot(page, label: str) -> None:
    try:
        path = settings.screenshot_dir / f"{label}.png"
        await page.screenshot(path=str(path))
        log.warning("Screenshot guardado en %s", path)
    except Exception:
        pass


def _check_blocked(page) -> None:
    if "/sorry/" in page.url or "google.com/sorry" in page.url:
        raise BlockedError(f"Google bloqueo esta IP (redirigio a: {page.url})")


def _build_search_url(query: str, location: str | None, latitude: float | None, longitude: float | None, zoom: int | None) -> str:
    base = f"https://www.google.com/maps/search/{quote(query)}"
    if latitude is not None and longitude is not None:
        z = zoom or 14
        return f"{base}/@{latitude},{longitude},{z}z"
    term = f"{query} {location}".strip() if location else query
    return f"https://www.google.com/maps/search/{quote(term)}"


async def collect_search_links(
    context: BrowserContext,
    query: str,
    location: str | None,
    limit: int,
    latitude: float | None = None,
    longitude: float | None = None,
    zoom: int | None = None,
) -> tuple[list[dict], bool]:
    """Devuelve (links, truncated). `truncated=True` significa que probablemente
    hay mas resultados de los que Google mostro para esta busqueda puntual."""
    url = _build_search_url(query, location, latitude, longitude, zoom)

    page = await context.new_page()
    try:
        await page.goto(url, timeout=60_000)
        _check_blocked(page)
        await page.wait_for_selector('div[role="feed"]', timeout=30_000)
        feed = page.locator('div[role="feed"]')

        collected: dict[str, str] = {}
        stable_rounds = 0
        while len(collected) < limit and stable_rounds < 3:
            before = len(collected)
            collected.update(await collect_result_links(page))
            stable_rounds = stable_rounds + 1 if len(collected) == before else 0
            await feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
            await _random_delay()

        truncated = len(collected) >= TRUNCATION_THRESHOLD
        links = [{"name": name, "href": href} for href, name in collected.items()][:limit]
        return links, truncated
    except BlockedError:
        raise
    except Exception:
        await _screenshot(page, f"search_error_{query[:30]}")
        raise
    finally:
        await page.close()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(min=2, max=20),
    retry=retry_if_not_exception_type(BlockedError),
)
async def scrape_one_place(context: BrowserContext, href: str) -> dict:
    page = await context.new_page()
    try:
        await page.goto(href, timeout=60_000)
        _check_blocked(page)
        await page.wait_for_selector("h1.DUwDvf", timeout=20_000)
        await _random_delay()
        return await extract_place_details(page)
    except BlockedError:
        raise
    except Exception:
        await _screenshot(page, f"place_error_{abs(hash(href))}")
        raise
    finally:
        await page.close()


async def scrape_query(
    context: BrowserContext,
    query: str,
    location: str | None,
    limit: int,
    latitude: float | None = None,
    longitude: float | None = None,
    zoom: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict], bool, bool]:
    """Devuelve (resultados, truncated, blocked). Si `blocked=True`, se corto
    antes de terminar la celda porque Google bloqueo la IP - lo ya extraido
    hasta ese punto se devuelve igual, no se pierde."""
    links, truncated = await collect_search_links(context, query, location, limit, latitude, longitude, zoom)
    zone = f"{latitude},{longitude}" if latitude is not None else location
    log.info(
        "Encontrados %d resultados para '%s' (%s)%s",
        len(links), query, zone, " [posible truncamiento]" if truncated else "",
    )
    if on_progress:
        on_progress(0, len(links))

    results = []
    blocked = False
    for i, item in enumerate(links):
        try:
            details = await scrape_one_place(context, item["href"])
            results.append(details)
        except BlockedError as exc:
            log.error("Bloqueado a mitad de celda, en lugar %d/%d: %s", i + 1, len(links), exc)
            blocked = True
            break
        except Exception as exc:
            log.error("Fallo extrayendo %s: %s", item.get("name"), exc)
        if on_progress:
            on_progress(i + 1, len(links))
        await _random_delay()
    return results, truncated, blocked
