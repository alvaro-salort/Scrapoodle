"""
Interfaz de Línea de Comandos (CLI) interactiva y automatizada para Scrapoodle.
Permite descubrir cursos, seleccionar interactivamente o procesar en lote con la mascota Poodle.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional
from moodle_scraper.config import ScraperConfig, prompt_retry_credentials
from moodle_scraper.course_scraper import CourseScraper, CourseProcessingStats
from moodle_scraper.course_selector import CourseSelection, CourseSelector
from moodle_scraper.observadores import ObservadorReporteJSON, crear_bus_por_defecto
from moodle_scraper.parser import Course, MoodleParser
from moodle_scraper.session import MoodleSession, MoodleAuthError
from moodle_scraper.utils import Logger, Colors, _safe_print, console, _log_lock
from rich.table import Table


class MoodleCLI:
    """Controlador de interacción y flujo CLI de Scrapoodle."""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self.session = MoodleSession(config)
        self.bus_eventos = crear_bus_por_defecto()
        self.course_scraper = CourseScraper(self.session, config, bus_eventos=self.bus_eventos)
        self.course_selector = CourseSelector(config.base_url)
        self.discovered_courses: List[Course] = []

    def _prompt_credential_retry(self) -> bool:
        """Ofrece reintentar el ingreso de contraseña/credenciales si fueron rechazadas."""
        return prompt_retry_credentials(self.config)

    def run(self, args: Optional[argparse.Namespace] = None) -> int:
        """Punto de entrada principal del flujo CLI."""
        # Validar configuración
        Logger.set_pet_status("Verificando configuración...")
        errors = self.config.validate()
        if errors:
            for err in errors:
                Logger.error(err)
            Logger.warn("Por favor configure sus credenciales en el archivo .env o scraper.conf")
            return 1

        reporte = getattr(args, "reporte_json", None) if args else None
        if reporte:
            ruta_reporte = Path(reporte) if isinstance(reporte, str) else self.config.download_dir / "reporte_eventos.json"
            self.bus_eventos.suscribir(ObservadorReporteJSON(ruta_reporte))
            Logger.info(f"Reporte de eventos: {ruta_reporte}")

        # 1. Autenticación (con opción de reintentar contraseña si las credenciales son inválidas)
        while True:
            try:
                Logger.set_pet_status("Iniciando sesión en Moodle...")
                self.session.login()
                break
            except MoodleAuthError as e:
                Logger.set_pet_status("Error de autenticación")
                Logger.error(str(e))
                if self.session.is_invalid_credentials_error(e) and self._prompt_credential_retry():
                    continue
                return 1
            except Exception as e:
                Logger.set_pet_status("Error al conectar")
                Logger.error(f"Error inesperado al iniciar sesión: {e}")
                return 1

        # 2. Descubrimiento de Cursos
        try:
            Logger.set_pet_status("Buscando cursos matriculados...")
            self.discovered_courses = self.discover_enrolled_courses()
        except MoodleAuthError as e:
            Logger.error(str(e))
            return 2
        if not self.discovered_courses:
            Logger.set_pet_status("Sin cursos matriculados")
            Logger.warn("No se encontraron cursos matriculados en la cuenta o la plataforma no los listó.")
            return 0

        # 3. Selección de cursos a procesar
        Logger.set_pet_status("Esperando selección de cursos...")
        selected_courses = self._select_courses(args)
        if not selected_courses:
            Logger.set_pet_status("Operación cancelada")
            Logger.info("Operación cancelada por el usuario.")
            return 0

        # 4. Procesamiento
        start_time = time.time()
        total_stats = CourseProcessingStats()

        Logger.header(f"INICIANDO DESCARGA DE {len(selected_courses)} CURSO(S)")

        try:
            for idx, course in enumerate(selected_courses, 1):
                Logger.set_pet_status(f"Descargando [{idx}/{len(selected_courses)}]: {course.name[:20]}")
                stats = self.course_scraper.process_course(
                    course,
                    course_index=idx,
                    total_courses=len(selected_courses)
                )
                total_stats.files_downloaded += stats.files_downloaded
                total_stats.files_skipped += stats.files_skipped
                total_stats.files_failed += stats.files_failed
                total_stats.images_downloaded += stats.images_downloaded
                total_stats.images_skipped += stats.images_skipped
                total_stats.images_failed += stats.images_failed
                total_stats.pages_extracted += stats.pages_extracted
                total_stats.sections_count += stats.sections_count
                total_stats.links_broken += stats.links_broken
        except MoodleAuthError as e:
            Logger.set_pet_status("Sesión expirada")
            Logger.error(str(e))
            return 2

        elapsed = time.time() - start_time

        # 5. Resumen final
        Logger.set_pet_status("¡Descarga completada con éxito!")
        Logger.header("RESUMEN GENERAL DE DESCARGA")
        _safe_print(f"{Colors.BOLD}{Colors.GREEN}✔ Cursos procesados: {len(selected_courses)}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.CYAN}✔ Archivos nuevos descargados: {total_stats.files_downloaded}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.YELLOW}✔ Archivos omitidos (ya existentes): {total_stats.files_skipped}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.CYAN}✔ Imágenes descargadas: {total_stats.images_downloaded}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.YELLOW}✔ Imágenes omitidas (ya existentes): {total_stats.images_skipped}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.MAGENTA}✔ Páginas teóricas extraídas a Markdown: {total_stats.pages_extracted}{Colors.RESET}")
        if total_stats.files_failed > 0:
            _safe_print(f"{Colors.BOLD}{Colors.RED}✖ Errores en archivos: {total_stats.files_failed}{Colors.RESET}")
        if total_stats.images_failed > 0:
            _safe_print(f"{Colors.BOLD}{Colors.RED}✖ Errores en imágenes: {total_stats.images_failed}{Colors.RESET}")
        if total_stats.links_broken > 0:
            _safe_print(f"{Colors.BOLD}{Colors.RED}✖ Enlaces locales rotos: {total_stats.links_broken}{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.WHITE}⏱ Tiempo total: {elapsed:.1f} segundos{Colors.RESET}")
        _safe_print(f"\n{Colors.GREEN}Archivos y teoría organizados en: {self.config.download_dir.resolve()}{Colors.RESET}\n")

        if (
            total_stats.files_failed > 0
            or total_stats.images_failed > 0
            or total_stats.links_broken > 0
        ):
            return 1
        return 0

    def discover_enrolled_courses(self) -> List[Course]:
        """Descubre cursos utilizando múltiples estrategias (AJAX, my/courses.php, my/)."""
        courses_map = {}
        Logger.info("Buscando cursos matriculados...")

        # Estrategia 1: AJAX Endpoint de Moodle 4.x con sesskey
        if self.session.sesskey:
            try:
                ajax_url = f"lib/ajax/service.php?sesskey={self.session.sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
                payload = [{
                    "index": 0,
                    "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                    "args": {
                        "offset": 0,
                        "limit": 0,
                        "classification": "all",
                        "sort": "fullname",
                        "customfieldname": "",
                        "customfieldvalue": ""
                    }
                }]
                resp = self.session.post(ajax_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    ajax_courses = MoodleParser.parse_courses_from_ajax(data, self.config.base_url)
                    for c in ajax_courses:
                        courses_map[c.id] = c
                    if ajax_courses:
                        Logger.success(f"Se encontraron {len(ajax_courses)} cursos mediante la API de Moodle.")
            except MoodleAuthError:
                raise
            except Exception as e:
                Logger.warn(f"No se pudo consultar el servicio AJAX de cursos: {e}")

        # Estrategia 2: my/courses.php (Vista Mis Cursos Moodle 4.x)
        if not courses_map:
            try:
                resp = self.session.get("my/courses.php")
                if resp.status_code == 200:
                    html_courses = MoodleParser.parse_courses_from_html(resp.text, self.config.base_url)
                    for c in html_courses:
                        courses_map[c.id] = c
            except MoodleAuthError:
                raise
            except Exception as e:
                Logger.warn(f"No se pudo leer my/courses.php: {e}")

        # Estrategia 3: my/ (Dashboard principal)
        if not courses_map:
            try:
                resp = self.session.get("my/")
                if resp.status_code == 200:
                    html_courses = MoodleParser.parse_courses_from_html(resp.text, self.config.base_url)
                    for c in html_courses:
                        courses_map[c.id] = c
            except MoodleAuthError:
                raise
            except Exception as e:
                Logger.warn(f"No se pudo leer el dashboard my/: {e}")

        courses = list(courses_map.values())
        courses.sort(key=lambda x: x.name)
        return courses

    def _select_courses(self, args: Optional[argparse.Namespace]) -> List[Course]:
        """Gestiona la selección interactiva o por parámetros CLI."""
        selection = CourseSelection.from_args(args)
        selected_courses = self.course_selector.select_explicit(
            self.discovered_courses,
            selection,
        )
        if selected_courses is not None:
            return selected_courses
        if selection.name_filter:
            Logger.warn(
                f"No se encontraron cursos que coincidan con el filtro "
                f"'{selection.name_filter}'"
            )

        # 4. Modo interactivo: Tabla estilizada con scrollback 100% natural
        table = Table(
            title="[bold cyan]Cursos Disponibles en su Cuenta (ITU UNCUYO)[/bold cyan]",
            title_justify="left",
            border_style="cyan dim",
            header_style="bold cyan",
            expand=False,
            padding=(0, 1),
        )
        table.add_column("#", justify="right", style="bold yellow", width=5)
        table.add_column("Materia / Curso", style="bold white")
        table.add_column("ID Moodle", justify="right", style="dim cyan", width=12)

        for i, c in enumerate(self.discovered_courses, 1):
            table.add_row(f"[{i:2d}]", c.name, str(c.id))

        with _log_lock:
            console.print()
            console.print(table)
            console.print(
                "  [bold green][ a][/bold green] Descargar TODOS los cursos    "
                "[bold red][ q][/bold red] Salir\n"
            )

        while True:
            choice = input("Seleccione una opción (ej: 1, 1-3, 1,4,7 o 'a'): ").strip().lower()
            if not choice:
                continue
            selected_courses = self.course_selector.select_interactive_choice(
                self.discovered_courses,
                choice,
            )
            if selected_courses is not None:
                return selected_courses

            with _log_lock:
                console.print("[bold red]✖ Selección inválida. Intente de nuevo.[/bold red]")
