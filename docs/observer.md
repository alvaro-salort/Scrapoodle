# Observer en Scrapoodle

El scraper tenía la salida repartida por todo el pipeline. `course_scraper.py` llamaba a `Logger` 28 veces y `downloader.py` 9 veces. La mascota Poodle se actualizaba desde `Logger`. Agregar un reporte JSON o una barra de progreso nueva implicaba editar esos dos módulos.

Ahora el pipeline publica eventos y los consumidores se suscriben.

| Archivo | Llamadas directas a Logger antes | Después |
|---|---:|---:|
| `moodle_scraper/course_scraper.py` | 28 | 0 |
| `moodle_scraper/downloader.py` | 9 | 0 |

`Logger` sigue existiendo para el CLI y para `ObservadorConsola`. El pipeline ya no lo importa.

## Piezas

- `moodle_scraper/eventos.py`
  - `Evento`: tipo, datos y momento.
  - `BusEventos`: suscribir, desuscribir y publicar.
  - `publicar` recorre una copia de la lista y atrapa excepciones por observador. Un observador roto no corta la descarga; el error queda en `bus.errores` y se informa por stderr.
- `moodle_scraper/observadores.py`
  - `ObservadorConsola`: traduce eventos a los métodos de `Logger`.
  - `ObservadorPoodle`: actualiza el estado y el humor de la mascota.
  - `ObservadorReporteJSON`: escribe JSON Lines.
  - `crear_bus_por_defecto`: consola y Poodle, para conservar el comportamiento anterior cuando no se pasa un bus.
- `CourseScraper` y `Downloader` reciben un `bus_eventos` opcional y publican eventos. Ya no importan `Logger`.
- `MoodleCLI` arma el bus y agrega el reporte JSON cuando se pide con `--reporte-json`.

## Eventos

| Evento | Datos principales |
|---|---|
| `curso_iniciado` | curso, índice, total |
| `seccion_iniciada` | nombre |
| `descarga_iniciada` | archivo, info, total |
| `descarga_omitida` | archivo, tamaño |
| `descarga_completada` | archivo, tamaño |
| `descarga_fallida` | archivo, url, error, mensaje |
| `curso_finalizado` | curso, stats |
| `log` | nivel, mensaje |

## Cómo se usa

Reporte JSON en la carpeta de descargas:

```bash
python main.py --reporte-json
```

Reporte en una ruta concreta:

```bash
python main.py --reporte-json ./reporte_eventos.json
```

Sin la mascota, pero con reporte:

```bash
python main.py --no-pet --reporte-json
```

Cada línea del reporte es un evento:

```json
{"tipo": "descarga_completada", "momento": "2026-11-22T21:10:03+00:00", "datos": {"archivo": "clase.pdf", "tamano": "2.31 MB"}}
```

## Comparación

| Cambio | Antes | Después |
|---|---|---|
| Agregar reporte JSON | editar `course_scraper.py` y `downloader.py` en cada punto de salida | una clase nueva y una suscripción en el CLI |
| Quitar la consola | editar el pipeline | no suscribir `ObservadorConsola` |
| Cambiar el estado de Poodle | tocar `Logger` | cambiar `ObservadorPoodle` |
| Agregar una barra de progreso | tocar `Downloader` y `CourseScraper` | conectar el callback de progreso existente a un evento y suscribir un observador |

El reporte JSON no necesita que el scraper sepa que existe. El bus no conoce a los observadores concretos.

## Costos

- Más indirección: para entender una salida hay que mirar el evento y el observador.
- El orden de notificación es el orden de suscripción. No hay prioridades.
- Los observadores corren en el mismo hilo que publica. Un observador lento frena la descarga.
- El bus atrapa errores, pero no reintenta ni guarda colas. Si un observador falla, se pierde ese evento para ese observador.
- `Logger` sigue en el CLI para autenticación, selección de cursos y resumen general. Eso no es parte del pipeline de un curso.

## Pruebas

```bash
python -m pytest tests/test_eventos.py -q
```

Cubren suscripción, baja, aislamiento de errores y escritura del reporte JSON.
