<p align="center">
  <img src="docs/banner.svg" alt="Scrapoodle: Scraper para la plataforma Moodle" width="100%">
</p>

# Scrapoodle

Scrapoodle descarga los materiales de un curso de Moodle 4.x y los ordena en carpetas. Convierte páginas, etiquetas y enlaces a Markdown, guarda un `manifest.json` con hashes y verifica que los enlaces locales apunten a archivos existentes.

## Requisitos

- Python 3.10 o superior.
- `pip install -r requirements.txt`.


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

<img width="989" height="539" alt="scrapoodle" src="https://github.com/user-attachments/assets/aa2f6156-b6e2-4283-8416-305c7d8a6483" />

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

Cada curso guarda `manifest.json` con URL, ruta local, tamaño y SHA-256. Las imágenes embebidas se descargan en `imagenes/` y los Markdown las enlazan localmente. `--verify` revalida los hashes. `--check-links` busca referencias rotas y genera `verificacion_enlaces.txt`.

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
