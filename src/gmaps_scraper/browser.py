import itertools
import random

from playwright.async_api import Browser, BrowserContext, Playwright

from .config import settings

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]

# Evasiones basicas de deteccion de automatizacion. No es infalible, pero
# evita las senales mas obvias que delatan a un navegador controlado por Playwright.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['es-CO', 'es', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
window.chrome = window.chrome || { runtime: {} };
"""

_proxy_cycle = itertools.cycle(settings.proxies) if settings.proxies else None


def _next_proxy() -> dict | None:
    if _proxy_cycle is None:
        return None
    proxy_url = next(_proxy_cycle)
    return {"server": proxy_url}


async def launch_browser(playwright: Playwright) -> Browser:
    return await playwright.chromium.launch(
        headless=settings.headless,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )


async def new_stealth_context(browser: Browser) -> BrowserContext:
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="es-CO",
        viewport={"width": 1366, "height": 900},
        proxy=_next_proxy(),
    )
    await context.add_init_script(STEALTH_JS)
    return context
