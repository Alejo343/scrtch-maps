import asyncio
import logging
import threading

from playwright.async_api import async_playwright

from . import db
from .browser import launch_browser, new_stealth_context
from .config import settings
from .grid import MIN_SPLIT_RADIUS_KM, split_cell, zoom_for_radius
from .scraper import BlockedError, scrape_query

log = logging.getLogger("gmaps_scraper")


async def _run_single_worker(worker_id: int, stop_event: threading.Event | None) -> None:
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright)
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    log.info("[worker %d] detenido por el usuario", worker_id)
                    break

                job = db.claim_next_job()
                if job is None:
                    break

                log.info("[worker %d] job #%d: '%s' en '%s'", worker_id, job["id"], job["query"], job["location"])
                context = await new_stealth_context(browser)
                try:
                    places, truncated, blocked = await scrape_query(
                        context, job["query"], job["location"], job["limit_count"],
                        job["latitude"], job["longitude"], job["zoom"],
                        on_progress=lambda done, found: db.update_job_progress(job["id"], found, done),
                    )
                    for place in places:
                        db.upsert_place(job["id"], place)

                    if blocked:
                        db.mark_job_error(
                            job["id"],
                            f"Bloqueado por Google a mitad de celda - se guardaron {len(places)} de "
                            f"{job['limit_count']} antes del bloqueo. Reintentar mas tarde o con proxy nuevo.",
                        )
                        log.error(
                            "[worker %d] job #%d BLOQUEADO POR GOOGLE - deteniendo todo el worker "
                            "(reintentar mas tarde o configurar PROXIES en .env)",
                            worker_id, job["id"],
                        )
                        if stop_event is not None:
                            stop_event.set()
                        break

                    db.mark_job_done(job["id"])
                    log.info("[worker %d] job #%d completado: %d lugares", worker_id, job["id"], len(places))

                    radius_km = job["radius_km"]
                    if truncated and radius_km and radius_km / 2 >= MIN_SPLIT_RADIUS_KM:
                        children = split_cell(job["latitude"], job["longitude"], radius_km)
                        for lat, lng, child_radius in children:
                            db.enqueue_job(
                                job["query"], job["location"], job["limit_count"],
                                latitude=lat, longitude=lng,
                                zoom=zoom_for_radius(child_radius), radius_km=child_radius,
                            )
                        log.warning(
                            "[worker %d] job #%d saturado (%d resultados) - subdividido en %d celdas de %.2fkm",
                            worker_id, job["id"], len(places), len(children), children[0][2],
                        )
                    elif truncated:
                        log.warning(
                            "[worker %d] job #%d saturado (%d resultados) pero no se puede subdividir mas "
                            "(sin radio de celda o ya en el minimo) - puede faltar informacion",
                            worker_id, job["id"], len(places),
                        )
                except BlockedError as exc:
                    db.mark_job_error(job["id"], f"Bloqueado por Google antes de extraer nada: {exc}")
                    log.error(
                        "[worker %d] job #%d BLOQUEADO POR GOOGLE (antes de la busqueda) - deteniendo "
                        "todo el worker (reintentar mas tarde o configurar PROXIES en .env)",
                        worker_id, job["id"],
                    )
                    if stop_event is not None:
                        stop_event.set()
                    break
                except Exception as exc:
                    db.mark_job_error(job["id"], str(exc))
                    log.exception("[worker %d] job #%d fallo", worker_id, job["id"])
                finally:
                    await context.close()
        finally:
            await browser.close()


async def run_workers(concurrency: int | None = None, stop_event: threading.Event | None = None) -> None:
    db.init_db()
    n = concurrency or settings.concurrency
    await asyncio.gather(*(_run_single_worker(i, stop_event) for i in range(n)))
