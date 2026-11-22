<p align="center">
  <img src="docs/banner.svg" alt="Scrapoodle: Scraper para la plataforma Moodle" width="100%">
</p>

# Scrapoodle

Scrapoodle descarga los materiales de un curso de Moodle 4.x y los ordena en carpetas. Convierte páginas, etiquetas y enlaces a Markdown, guarda un `manifest.json` con hashes y verifica que los enlaces locales apunten a archivos existentes.

## Requisitos

- Python 3.10 o superior.
- `pip install -r requirements.txt`.

## Configuración

Copiá `.env.example` a `.env`:

```ini
MOODLE_USER=tu_usuario
MOODLE_PASSWORD=tu_contraseña
MOODLE_BASE_URL=https://aulas.itu.uncu.edu.ar/itu/
DOWNLOAD_DIR=./downloads
MAX_WORKERS=3
REQUEST_DELAY=0.0
```

Para SSO de Google o Microsoft, dejá el usuario vacío y usá `MOODLE_SESSION_COOKIE`, `--cookie -` o el prompt interactivo. La contraseña no se guarda en disco.

## Uso

```bash
python main.py                  # menú interactivo
python main.py --all            # todos los cursos
python main.py --course-id 1234
python main.py --filter "Algebra"
python main.py --reporte-json   # eventos en downloads/reporte_eventos.json
python main.py --check-links ./downloads
python main.py --verify ./downloads
```

Flags: `--workers N`, `--delay S`, `--overwrite`, `--download-dir DIR`, `--env RUTA`, `--conf RUTA`, `--no-pet`, `--reporte-json [RUTA]`.

La consola incluye la mascota Poodle. `--no-pet` la desactiva.

![Salida de python main.py --help](docs/cli.png)

## Salida

```text
downloads/
└── Curso/
    ├── notas_y_teoria_completa.md
    ├── manifest.json
    └── Modulo/
        ├── resumen_tema.md
        └── Clase/
            ├── resumen_tema.md
            └── archivo.pdf
```

Cada curso guarda `manifest.json` con URL, ruta local, tamaño y SHA-256. `--verify` revalida los hashes. `--check-links` busca referencias rotas y genera `verificacion_enlaces.txt`.

## Eventos y observadores

El pipeline publica eventos en `BusEventos`. La consola, la mascota Poodle y el reporte JSON son observadores; el scraper no los conoce. `--reporte-json` escribe JSON Lines. La explicación, los eventos y la comparación están en [`docs/observer.md`](docs/observer.md).

## Tests

```bash
python -m pytest -q
```

109 tests.

## Códigos de salida

- `0`: todo correcto.
- `1`: archivos, imágenes o enlaces con problemas.
- `2`: sesión de Moodle expirada.
- `130`: interrumpido con Ctrl+C.

## Arquitectura

```text
main.py
moodle_scraper/
├── config.py
├── session.py
├── parser.py
├── downloader.py
├── markdown_writer.py
├── course_scraper.py
├── eventos.py
├── observadores.py
├── cli.py
└── utils.py
tests/
docs/
```
