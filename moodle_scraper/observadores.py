"""Observadores concretos del scraper."""

import json
from pathlib import Path
from threading import Lock

from moodle_scraper.eventos import BusEventos, Evento
from moodle_scraper.poodle import PoodleUI
from moodle_scraper.utils import Logger


class ObservadorConsola:
    """Mantiene la salida actual de la consola a partir de los eventos."""

    def actualizar(self, evento: Evento) -> None:
        datos = evento.datos

        if evento.tipo == "curso_iniciado":
            Logger.course_header(
                datos.get("curso", ""),
                datos.get("indice", 0),
                datos.get("total", 0),
            )
            return

        if evento.tipo == "seccion_iniciada":
            Logger.section(datos.get("nombre", ""))
            return

        if evento.tipo == "log":
            nivel = datos.get("nivel", "info")
            mensaje = datos.get("mensaje", "")
            metodo = getattr(Logger, nivel, Logger.info)
            metodo(mensaje)
            return

        if evento.tipo == "descarga_iniciada":
            Logger.info(
                f"Descargando: {datos.get('archivo', '')}"
                f"{datos.get('info', '')} ({datos.get('total', '0 B')})..."
            )
            return

        if evento.tipo == "descarga_omitida":
            Logger.skip(f"{datos.get('archivo', '')} ({datos.get('tamano', '0 B')}) ya existe.")
            return

        if evento.tipo == "descarga_completada":
            Logger.success(f"Descargado: {datos.get('archivo', '')} ({datos.get('tamano', '0 B')})")
            return

        if evento.tipo == "descarga_fallida":
            mensaje = datos.get("mensaje")
            if not mensaje:
                mensaje = f"Fallo en descarga: {datos.get('error', '')}: {datos.get('url', '')}"
            Logger.error(mensaje)


class ObservadorPoodle:
    """Actualiza el estado y el humor de la mascota."""

    def actualizar(self, evento: Evento) -> None:
        datos = evento.datos

        if evento.tipo == "curso_iniciado":
            curso = datos.get("curso", "")[:25]
            PoodleUI.set_status(f"Curso: {curso}", mood="working")
            return

        if evento.tipo == "seccion_iniciada":
            PoodleUI.set_status(f"Sección: {datos.get('nombre', '')[:30]}", mood="reading")
            return

        if evento.tipo == "descarga_iniciada":
            PoodleUI.set_status(f"Descargando: {datos.get('archivo', '')[:30]}", mood="downloading")
            return

        if evento.tipo == "descarga_completada":
            PoodleUI.set_status("Archivo descargado", mood="happy")
            return

        if evento.tipo == "curso_finalizado":
            PoodleUI.set_status("Curso completado", mood="success")


class ObservadorReporteJSON:
    """Guarda cada evento como una línea JSON."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_text("", encoding="utf-8")
        self._lock = Lock()

    def actualizar(self, evento: Evento) -> None:
        linea = json.dumps(
            {
                "tipo": evento.tipo,
                "momento": evento.momento,
                "datos": evento.datos,
            },
            ensure_ascii=False,
            default=str,
        )
        with self._lock:
            with self.ruta.open("a", encoding="utf-8") as archivo:
                archivo.write(linea + "\n")


def crear_bus_por_defecto() -> BusEventos:
    return BusEventos([ObservadorConsola(), ObservadorPoodle()])
