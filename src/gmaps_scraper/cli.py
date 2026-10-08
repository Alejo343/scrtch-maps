import asyncio
import logging

import pandas as pd
import typer

from . import db
from .config import settings
from .geocode import get_city_bbox
from .grid import generate_grid, zoom_for_radius
from .worker import run_workers

app = typer.Typer(help="Sistema de obtencion de datos de Google Maps")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.command()
def init():
    """Crea las tablas de la base de datos si no existen."""
    db.init_db()
    typer.echo(f"Base de datos lista en {settings.db_path}")


@app.command("add-job")
def add_job(
    query: str = typer.Argument(..., help="Que buscar, ej: 'restaurantes'"),
    location: str = typer.Option(None, "--location", "-l", help="Ej: 'Bogota, Colombia'"),
    limit: int = typer.Option(settings.default_limit, "--limit", "-n", help="Maximo de resultados"),
):
    """Encola una busqueda para ser procesada por el worker."""
    db.init_db()
    job_id = db.enqueue_job(query, location, limit)
    typer.echo(f"Job #{job_id} encolado: '{query}' en '{location}' (limite {limit})")


@app.command("add-city-jobs")
def add_city_jobs(
    query: str = typer.Argument(..., help="Que buscar, ej: 'restaurantes'"),
    city: str = typer.Option(..., "--city", "-c", help="Ej: 'Bogota, Colombia'"),
    radius_km: float = typer.Option(1.5, "--radius-km", "-r", help="Radio inicial de cada celda de la grilla, en km"),
    limit: int = typer.Option(settings.default_limit, "--limit", "-n", help="Maximo de resultados por celda"),
):
    """Cubre una ciudad completa encolando una busqueda por cada celda de una grilla,
    para no quedar atados al tope de ~100-120 resultados de una sola busqueda city-wide.
    El zoom se calcula automaticamente a partir del radio, y las celdas que salgan
    saturadas se subdividen solas durante el run (ver worker.py)."""
    db.init_db()
    south, north, west, east = get_city_bbox(city)
    points = generate_grid((south, north, west, east), radius_km)
    zoom = zoom_for_radius(radius_km)

    for lat, lng in points:
        db.enqueue_job(query, city, limit, latitude=lat, longitude=lng, zoom=zoom, radius_km=radius_km)

    typer.echo(
        f"{len(points)} jobs encolados para '{query}' en '{city}' "
        f"(grilla de celdas de {radius_km}km de radio)"
    )


@app.command("add-jobs-from-csv")
def add_jobs_from_csv(path: str = typer.Argument(..., help="CSV con columnas: query,location,limit")):
    """Encola muchas busquedas a la vez desde un archivo CSV."""
    db.init_db()
    df = pd.read_csv(path)
    count = 0
    for _, row in df.iterrows():
        limit = int(row["limit"]) if "limit" in row and not pd.isna(row["limit"]) else settings.default_limit
        location = row.get("location") if "location" in row and not pd.isna(row.get("location")) else None
        db.enqueue_job(str(row["query"]), location, limit)
        count += 1
    typer.echo(f"{count} jobs encolados desde {path}")


@app.command()
def run(concurrency: int = typer.Option(None, "--concurrency", "-c", help="Navegadores en paralelo")):
    """Procesa todos los jobs pendientes en la cola."""
    db.init_db()
    asyncio.run(run_workers(concurrency))
    typer.echo("Worker finalizado (no quedan jobs pendientes).")


@app.command()
def export(output: str = typer.Option("data/places.csv", "--output", "-o")):
    """Exporta todos los lugares recolectados a un CSV."""
    with db.connect() as conn:
        df = pd.read_sql_query("SELECT * FROM places", conn)
    df.to_csv(output, index=False)
    typer.echo(f"{len(df)} lugares exportados a {output}")


@app.command()
def status():
    """Muestra el estado de la cola de jobs."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
        ).fetchall()
    for row in rows:
        typer.echo(f"{row['status']}: {row['n']}")


if __name__ == "__main__":
    app()
