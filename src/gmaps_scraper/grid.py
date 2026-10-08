"""
Genera una grilla de puntos (lat, lng) que cubre un bounding box, para hacer
busqueda por zonas en vez de una sola busqueda city-wide (que Google topa
en ~100-120 resultados sin importar el scroll).

Cada punto es el centro de una celda circular de radio `cell_radius_km`. El
espaciado entre puntos es 1.5x el radio para que las celdas se superpongan
un poco y no queden huecos sin cubrir en los bordes.
"""
import math

KM_PER_DEGREE_LAT = 111.0
MIN_SPLIT_RADIUS_KM = 0.15  # debajo de esto ya no vale la pena subdividir mas


def km_per_degree_lng(lat: float) -> float:
    return max(KM_PER_DEGREE_LAT * math.cos(math.radians(lat)), 1e-6)


def zoom_for_radius(cell_radius_km: float) -> int:
    """
    Calcula el nivel de zoom de Google Maps cuya area visible aproximada
    coincide con el diametro de una celda de este radio, usando la relacion
    estandar de zoom web-mercator (area visible ~ 40075km / 2^zoom).
    Asi el usuario solo elige un radio; el zoom deja de ser una variable
    que se pueda desconfigurar por separado.
    """
    diameter_km = max(cell_radius_km * 2, 0.05)
    zoom = round(math.log2(40075 / diameter_km))
    return max(10, min(zoom, 19))


def generate_grid(
    bbox: tuple[float, float, float, float], cell_radius_km: float
) -> list[tuple[float, float]]:
    south, north, west, east = bbox
    step_km = cell_radius_km * 1.5
    lat_step = step_km / KM_PER_DEGREE_LAT

    points: list[tuple[float, float]] = []
    lat = south
    while lat <= north:
        lng_step = step_km / km_per_degree_lng(lat)

        lng = west
        while lng <= east:
            points.append((round(lat, 6), round(lng, 6)))
            lng += lng_step
        lat += lat_step

    return points


def split_cell(lat: float, lng: float, radius_km: float) -> list[tuple[float, float, float]]:
    """
    Subdivide una celda saturada (que probablemente topo el limite de Google)
    en 4 celdas hijas mas chicas, en cuadrantes alrededor del mismo centro.
    Devuelve [(lat, lng, nuevo_radio_km), ...].
    """
    child_radius = radius_km / 2
    offset_km = child_radius
    lat_offset = offset_km / KM_PER_DEGREE_LAT
    lng_offset = offset_km / km_per_degree_lng(lat)

    return [
        (round(lat + lat_offset, 6), round(lng + lng_offset, 6), child_radius),
        (round(lat + lat_offset, 6), round(lng - lng_offset, 6), child_radius),
        (round(lat - lat_offset, 6), round(lng + lng_offset, 6), child_radius),
        (round(lat - lat_offset, 6), round(lng - lng_offset, 6), child_radius),
    ]
