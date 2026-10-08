# gmaps-scraper

Sistema para extraer datos basicos de negocios (nombre, direccion, telefono,
categoria, rating, coordenadas) desde Google Maps, usando Playwright.

## ⚠️ Aviso legal

Hacer scraping de Google Maps viola los Terminos de Servicio de Google. Esto
puede resultar en bloqueo de IPs, cuentas, o acciones legales segun el uso
que se le de a los datos. Usalo bajo tu propio riesgo y evalua si la
[Google Places API](https://developers.google.com/maps/documentation/places/web-service)
oficial es una alternativa viable para tu caso antes de operar esto a gran
escala de forma sostenida.

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

Edita `.env`: como minimo revisa `CONCURRENCY` y, si vas a operar a volumen
alto, llena `PROXIES` con tu lista de proxies residenciales/datacenter
rotativos. Sin proxies, Google bloqueara la IP rapido a este volumen.

## Interfaz grafica (GUI)

```bash
.venv\Scripts\streamlit run app.py
```

Abre `http://localhost:8501` con 4 pestañas: **Cola de jobs** (ver estado,
reintentar errores, limpiar), **Nueva busqueda** (puntual, cobertura de
ciudad por nombre, o dibujando un rectangulo en un mapa interactivo),
**Worker** (iniciar/detener, log en vivo, concurrencia) y **Resultados**
(tabla de lugares + descarga CSV). Pensada solo para uso local de un
usuario; no tiene autenticacion.

El modo "Dibujar zona en el mapa" usa el icono de rectangulo en la esquina
del mapa para marcar el area exacta a cubrir (util cuando el area que
interesa no coincide con los limites administrativos de una ciudad, ej. solo
una zona industrial o un barrio). El area dibujada se convierte en la misma
grilla de celdas que el modo por ciudad.

## Uso por linea de comandos (alternativa a la GUI)

```bash
# Inicializar la base de datos
python -m gmaps_scraper.cli init

# Encolar una busqueda puntual (una sola zona/texto, sirve para pruebas rapidas)
python -m gmaps_scraper.cli add-job "restaurantes" --location "Bogota, Colombia" --limit 50

# Cubrir una ciudad COMPLETA: geocodifica el nombre, genera una grilla de celdas
# y encola una busqueda por celda (ver seccion "Cobertura por zonas" abajo)
python -m gmaps_scraper.cli add-city-jobs "restaurantes" --city "Bogota, Colombia" --radius-km 1.5

# Encolar muchas busquedas desde un CSV (columnas: query,location,limit)
python -m gmaps_scraper.cli add-jobs-from-csv busquedas.csv

# Procesar la cola (usa CONCURRENCY de .env, o pasa -c)
python -m gmaps_scraper.cli run -c 3

# Ver estado de la cola
python -m gmaps_scraper.cli status

# Exportar resultados a CSV
python -m gmaps_scraper.cli export -o data/places.csv
```

## Como funciona

1. `add-job` / `add-jobs-from-csv` insertan filas en la tabla `jobs` (SQLite,
   `data/gmaps.db`) con estado `pending`.
2. `run` levanta N workers concurrentes (cada uno su propio contexto de
   navegador, con proxy y user-agent propios). Cada worker reclama un job
   pendiente a la vez, navega a la busqueda, hace scroll del feed de
   resultados, visita cada ficha de negocio, y extrae los campos.
3. Los resultados se guardan en la tabla `places` con una clave de dedupe
   (URL de Maps o nombre+direccion) para no duplicar si corres la misma
   busqueda dos veces.
4. Si algo falla al extraer una pagina, se guarda un screenshot en
   `logs/screenshots/` para poder diagnosticar que cambio en el DOM de Google.

## Cobertura por zonas (grid search)

Una sola busqueda de texto en Google Maps (ej. "restaurantes en Bogota") tiene
un tope no documentado de ~100-120 resultados sin importar cuanto scroll se
haga al feed. Para cubrir una ciudad completa de verdad, `add-city-jobs`:

1. Geocodifica el nombre de la ciudad a un area (bounding box) usando
   Nominatim/OpenStreetMap (gratuito, `src/gmaps_scraper/geocode.py`).
2. Genera una grilla de puntos (celdas circulares superpuestas) que cubre esa
   area (`src/gmaps_scraper/grid.py`).
3. Encola una busqueda por cada celda, centrada en sus coordenadas
   (`.../maps/search/<query>/@<lat>,<lng>,<zoom>z`), en vez de una busqueda
   de texto city-wide.

El dedupe de la tabla `places` (por URL de Maps) hace que los negocios que
caen en el solape entre celdas vecinas no se guarden duplicados.

```bash
# Radio de celda mas chico = mas celdas = mas cobertura en zonas densas,
# pero mas jobs (mas tiempo/costo). 1-2km es razonable para zona urbana.
python -m gmaps_scraper.cli add-city-jobs "restaurantes" --city "Medellin, Colombia" --radius-km 1.5 --limit 100
```

Para una ciudad como Bogota, un radio de 1.5km genera ~200 celdas; 3km genera
~60. Mas celdas = cobertura mas fina en zonas con muchos negocios, a costo de
mas busquedas (y por tanto mas tiempo y mas riesgo de bloqueo si la
concurrencia/proxies no estan bien dimensionados).

## Puntos fragiles (a vigilar)

- **Selectores del DOM** (`src/gmaps_scraper/extractor.py`): Google cambia
  los nombres de clase sin aviso. Si los campos empiezan a salir vacios, pon
  `HEADLESS=false`, corre un job, e inspecciona el DOM actual con las
  DevTools para actualizar los selectores.
- **Bloqueo por IP/captcha**: a mayor concurrencia y volumen, mayor
  probabilidad de bloqueo. Si ves que los jobs empiezan a fallar en cadena,
  revisa los screenshots de error — si muestran un captcha, hay que bajar la
  concurrencia, aumentar los delays (`MIN_DELAY`/`MAX_DELAY`), o rotar
  proxies con mas frecuencia.
- **Escalado futuro**: si decenas de miles de busquedas/mes saturan SQLite o
  un solo proceso, el siguiente paso natural es mover la cola a Redis/Celery
  y distribuir los workers en varias maquinas/IPs.
- **Conteo de reseñas**: en pruebas, el numero de reseñas no siempre esta
  presente en el HTML inicial (a veces el contenedor llega vacio aunque el
  negocio sí tenga reseñas). El rating si se extrae de forma confiable. Si
  este campo es importante, revisar `extractor.py` con `HEADLESS=false` para
  ver si en tu caso aparece via otra interaccion (scroll, click) antes de
  confiar en el.
