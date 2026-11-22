#!/usr/bin/env python3
"""
Demostración visual de Scrapoodle Pet para la terminal.
Muestra el banner de bienvenida con la mascota Poodle, la tabla de cursos
completa con scroll 100% nativo y los distintos estados de ánimo (moods).

Uso:
    python poodle_demo.py
"""

import sys
import time

# Configurar UTF-8 nativo en terminales Windows
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.table import Table
from moodle_scraper.poodle import PoodleUI
from moodle_scraper.utils import Logger, _safe_print, Colors, console, _log_lock


def run_demo():
    print("Iniciando demostración de Scrapoodle CLI...")
    PoodleUI.start(
        status="Demostración interactiva",
        user_info="Estudiante ITU (Demo)",
        base_url="https://moodle.itu.uncuyo.edu.ar/"
    )

    try:
        time.sleep(0.8)
        Logger.set_pet_status("Buscando cursos matriculados...", mood="idle")
        Logger.info("Autenticando usuario...")
        time.sleep(0.8)
        Logger.success("Sesión validada con éxito en Moodle ITU.")
        time.sleep(0.6)

        # Simular 28 cursos como en el caso real reportado por el usuario
        cursos = [
            (1, "Álgebra y Estadística - Junin_DS", "101"),
            (2, "Arquitectura de Hardware Computacional - Junin_DS", "102"),
            (3, "Bases de Datos Relacionales", "103"),
            (4, "Comunicación y Redes de Datos", "104"),
            (5, "Desarrollo Web Frontend", "105"),
            (6, "Estructuras de Datos y Algoritmos", "106"),
            (7, "Ingeniería de Software I", "107"),
            (8, "Laboratorio de Programación Python", "108"),
            (9, "Metodologías Ágiles y Scrum", "109"),
            (10, "Modelado de Negocios y BPMN", "110"),
            (11, "Programación Orientada a Objetos", "111"),
            (12, "Seguridad de la Información", "112"),
            (13, "Sistemas Operativos Distribuidos", "113"),
            (14, "Taller de Práctica Profesionalizante I", "114"),
            (15, "Arquitectura Cloud y Microservicios", "115"),
            (16, "Big Data y Analítica de Datos", "116"),
            (17, "Computación Gráfica e Interfaces", "117"),
            (18, "Desarrollo Mobile Multiplataforma", "118"),
            (19, "DevOps y CI/CD Pipelines", "119"),
            (20, "Ética Profesional y Legislación Digital", "120"),
            (21, "Inteligencia Artificial y ML", "121"),
            (22, "Ingeniería de Requerimientos", "122"),
            (23, "Internet de las Cosas (IoT)", "123"),
            (24, "Pruebas de Software y QA Automatizado", "124"),
            (25, "Redes Neuronales y Deep Learning", "125"),
            (26, "Seminario de Tecnologías Emergentes", "126"),
            (27, "Taller de Práctica Profesionalizante II", "127"),
            (28, "Trabajo Final de Grado ITU UNCUYO", "128"),
        ]

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

        for idx, name, cid in cursos:
            table.add_row(f"[{idx:2d}]", name, cid)

        with _log_lock:
            console.print()
            console.print(table)
            console.print(
                "  [bold green][ a][/bold green] Descargar TODOS los cursos    "
                "[bold red][ q][/bold red] Salir\n"
            )
            console.print(
                "[dim italic](Nota: Todos los 28 cursos son visibles en el scrollback; "
                "puedes scrollear hacia arriba libremente)[/dim italic]\n"
            )

        time.sleep(1.0)

        # Simular descarga de 2 cursos demostrando los estados del poodle
        cursos_descarga = cursos[:2]
        for idx, (c_idx, c_name, cid) in enumerate(cursos_descarga, 1):
            Logger.course_header(c_name, idx, len(cursos_descarga))
            for seccion in ["Módulo 1 - Fundamentos", "Módulo 2 - Casos Prácticos"]:
                Logger.section(seccion)
                for f in range(1, 3):
                    Logger.set_pet_status(f"Descargando archivo {f}...", mood="working")
                    _safe_print(f"    ⬇ Descargando apunte_{f}.pdf (1.4 MB)... {Colors.GREEN}OK{Colors.RESET}")
                    time.sleep(0.15)
                Logger.set_pet_status(f"Extrayendo teoría a Markdown...", mood="reading")
                Logger.text_saved(f"resumen_{seccion.lower().replace(' ', '_')}.md")
                time.sleep(0.2)

        Logger.set_pet_status("¡Descarga completada con éxito! 🐾", mood="happy")
        Logger.header("RESUMEN DE DESCARGA")
        _safe_print(f"{Colors.BOLD}{Colors.GREEN}✔ Cursos procesados: 2{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.CYAN}✔ Archivos nuevos descargados: 4{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.MAGENTA}✔ Teoría extraída a Markdown: 4 páginas{Colors.RESET}")
        _safe_print(f"{Colors.BOLD}{Colors.WHITE}⏱ Tiempo total simulado: 2.1s{Colors.RESET}\n")

    except KeyboardInterrupt:
        _safe_print(f"\n{Colors.YELLOW}Demo interrumpido.{Colors.RESET}")
    finally:
        PoodleUI.stop()
        print(f"{Colors.GREEN}✔ Terminal en estado normal (scrollback activo).{Colors.RESET}")


if __name__ == "__main__":
    run_demo()
