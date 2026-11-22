"""
Scrapoodle: mascota Poodle pixel-art para la terminal.

El sprite se define UNA sola vez como grilla de píxeles (una letra = un color).
De esa grilla salen:
  - la versión de terminal, con medios bloques (▀ ▄ █): cada celda de texto
    muestra 2 píxeles verticales usando color de frente + color de fondo;
  - un SVG (``sprite_to_svg``) para la futura interfaz gráfica.

Igual que Clawd en Claude Code (modo normal, no fullscreen), el banner se
imprime una vez arriba y luego scrollea con el historial: no se usan scroll
regions (DECSTBM) ni alternate screen, así el scrollback queda intacto.
"""

import atexit
import os
import sys
import threading
import time
from typing import Dict, List, Optional

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

ui_lock = threading.Lock()
_console = Console(legacy_windows=False, highlight=False, soft_wrap=True)

# ---------------------------------------------------------------------------
# Sprite
# ---------------------------------------------------------------------------

TRANSPARENT = "."

# Colores en hex: Rich los degrada solo a 256/16 colores si la terminal no
# soporta truecolor (y respeta NO_COLOR).
PALETTE: Dict[str, str] = {
    "W": "#ffffff",  # pelaje
    "G": "#bcbcbc",  # sombra del copete / ojos cerrados
    "K": "#000000",  # ojos, nariz, boca
    "P": "#ff5f87",  # lengua
    "D": "#d7005f",  # línea de la lengua
}

# 19 x 12 píxeles -> 19 columnas x 6 filas de terminal.
_BASE: List[str] = [
    ".......WWWWWWW.....",
    ".....WWWWWWWWWWW...",
    "....WWWWWWWWWWWWW..",
    "...WWWGGGGGGGGGWWW.",
    "..WWW.WWWWWWWWW.WWW",
    "..WWW.WWKWWWKWW.WWW",
    "..WWW.WWWWWWWWW.WWW",
    "..WWW.WWWKKKWWW.WWW",
    "...W...WWWKWWW...W.",
    "........WWWWW......",
    "...................",
    "...................",
]


def _variant(**rows: str) -> List[str]:
    """Copia _BASE reemplazando filas: _variant(r5='...')."""
    grid = list(_BASE)
    for key, row in rows.items():
        grid[int(key[1:])] = row
    return grid


_EYES_OPEN = "..WWW.WWKWWWKWW.WWW"
_EYES_HAPPY = "..WWW.WKKWWWKKW.WWW"
_EYES_CLOSED = "..WWW.WWGWWWGWW.WWW"
_MOUTH_TONGUE = "........WWPWW......"

FRAMES: Dict[str, List[str]] = {
    "base": _BASE,
    "blink": _variant(r5=_EYES_CLOSED),
    "tongue1": _variant(r9=_MOUTH_TONGUE, r10=".........PPP......."),
    "tongue2": _variant(r9=_MOUTH_TONGUE, r10=".........PDP.......", r11=".........PPP......."),
    "happy": _variant(r5=_EYES_HAPPY, r9=_MOUTH_TONGUE,
                      r10=".........PDP.......", r11=".........PPP......."),
}

SPRITE_WIDTH = len(_BASE[0])
SPRITE_HEIGHT = len(_BASE) // 2  # filas de terminal

# Secuencia de la intro (la misma de tu script bash original).
INTRO_SEQUENCE = ["base", "base", "tongue1", "tongue2", "happy", "happy",
                  "happy", "tongue2", "tongue1", "base"]
INTRO_DELAY = 0.18

MOOD_SPRITES: Dict[str, List[str]] = {
    "idle": ["base", "blink"],
    "working": ["tongue1", "tongue2"],
    "downloading": ["tongue1", "tongue2"],
    "reading": ["base"],
    "extracting": ["base"],
    "happy": ["happy"],
    "success": ["happy"],
    "sleeping": ["blink"],
}

MOOD_ICONS: Dict[str, str] = {
    "idle": "🐶",
    "working": "🐾",
    "downloading": "⬇️",
    "reading": "📖",
    "extracting": "📝",
    "happy": "✨",
    "success": "🎉",
    "sleeping": "💤",
}


def render_sprite(grid: List[str], palette: Dict[str, str] = PALETTE) -> Text:
    """Convierte una grilla de píxeles en Text de Rich usando medios bloques."""
    out = Text(no_wrap=True, overflow="crop")
    for y in range(0, len(grid), 2):
        top_row = grid[y]
        bottom_row = grid[y + 1] if y + 1 < len(grid) else TRANSPARENT * len(top_row)
        for top, bottom in zip(top_row, bottom_row):
            if top == TRANSPARENT and bottom == TRANSPARENT:
                out.append(" ")
            elif top == bottom:
                out.append("█", Style(color=palette[top]))
            elif bottom == TRANSPARENT:
                out.append("▀", Style(color=palette[top]))
            elif top == TRANSPARENT:
                out.append("▄", Style(color=palette[bottom]))
            else:
                out.append("▀", Style(color=palette[top], bgcolor=palette[bottom]))
        if y + 2 < len(grid):
            out.append("\n")
    return out


def sprite_to_svg(grid: List[str], scale: int = 8, palette: Dict[str, str] = PALETTE) -> str:
    """Exporta la grilla a SVG (pensado para la futura GUI)."""
    width, height = len(grid[0]) * scale, len(grid) * scale
    rects = [
        f'<rect x="{x * scale}" y="{y * scale}" width="{scale}" height="{scale}" fill="{palette[c]}"/>'
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c != TRANSPARENT
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" shape-rendering="crispEdges">'
        + "".join(rects)
        + "</svg>"
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _animations_enabled(console: Console) -> bool:
    if not console.is_terminal or console.is_dumb_terminal:
        return False
    return os.environ.get("SCRAPOODLE_NO_ANIM", "").strip() not in ("1", "true", "yes")


class PoodleUI:
    """Estado de la mascota y banner de bienvenida estilo Claude Code."""

    _active: bool = False
    _disabled: bool = False
    _status: str = "Listo para raspar"
    _mood: str = "idle"
    _banner_rendered: bool = False
    _user_info: str = "ITU UNCUYO"
    _base_url: str = "https://moodle.itu.uncuyo.edu.ar/"

    @classmethod
    def start(
        cls,
        disabled: bool = False,
        status: str = "Listo para raspar",
        user_info: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        if cls._disabled or disabled:
            cls._disabled = True
            return

        cls._active = True
        cls._status = status
        if user_info:
            cls._user_info = user_info
        if base_url:
            cls._base_url = base_url

        if not cls._banner_rendered:
            cls.render_banner(animate=True)
            cls._banner_rendered = True

        atexit.register(cls.stop)

    @classmethod
    def stop(cls) -> None:
        cls._active = False

    @classmethod
    def set_status(cls, status: str, mood: Optional[str] = None) -> None:
        cls._status = status
        if mood:
            cls._mood = mood
            return
        st = status.lower()
        if any(w in st for w in ("descargando", "bajando", "obteniendo", "download")):
            cls._mood = "working"
        elif any(w in st for w in ("teoría", "teoria", "markdown", "resumen", "leyendo", "extray")):
            cls._mood = "reading"
        elif any(w in st for w in ("éxito", "exito", "completad", "finaliz", "correcto")):
            cls._mood = "happy"
        elif any(w in st for w in ("esperando", "pausa", "reposo", "dormir")):
            cls._mood = "sleeping"
        else:
            cls._mood = "idle"

    @classmethod
    def get_status(cls) -> str:
        return cls._status

    @classmethod
    def get_mood(cls) -> str:
        return cls._mood

    @classmethod
    def is_active(cls) -> bool:
        return cls._active and not cls._disabled

    @classmethod
    def get_frame_name(cls, mood: Optional[str] = None, frame_idx: int = 0) -> str:
        frames = MOOD_SPRITES.get(mood or cls._mood, MOOD_SPRITES["idle"])
        return frames[frame_idx % len(frames)]

    @classmethod
    def get_pet_art(cls, mood: Optional[str] = None, frame_idx: int = 0) -> Text:
        """Sprite renderizado (Text de Rich) para el mood pedido."""
        return render_sprite(FRAMES[cls.get_frame_name(mood, frame_idx)])

    @classmethod
    def get_badge(cls, mood: Optional[str] = None) -> str:
        icon = MOOD_ICONS.get(mood or cls._mood, "🐶")
        return f"[bold cyan]{icon} Poodle:[/] [dim]{cls._status}[/]"

    @classmethod
    def _build_banner(cls, sprite: Text, status: str, user_info: str, base_url: str) -> Panel:
        info = Text(no_wrap=True, overflow="ellipsis")
        info.append("Scrapoodle", style="bold")
        info.append(" v2.0\n", style="dim")
        info.append("Moodle Scraper · ITU UNCUYO\n\n", style="dim")
        info.append("● ", style="green")
        info.append(f"{status}\n", style="bold")
        info.append(f"{user_info}\n", style="dim")
        info.append(base_url, style="dim cyan")

        grid = Table.grid(padding=(0, 3))
        grid.add_column(width=SPRITE_WIDTH, no_wrap=True)
        grid.add_column(no_wrap=True, max_width=48)
        grid.add_row(sprite, info)

        return Panel(
            grid,
            box=box.ROUNDED,
            border_style="cyan",
            padding=(1, 2),
            expand=False,
        )

    @classmethod
    def render_banner(
        cls,
        status: Optional[str] = None,
        user_info: Optional[str] = None,
        base_url: Optional[str] = None,
        mood: Optional[str] = None,
        animate: bool = False,
    ) -> None:
        """Imprime el banner. Con animate=True reproduce la intro en el lugar."""
        st = status or cls._status
        usr = user_info or cls._user_info
        burl = base_url or cls._base_url

        def banner(frame: str) -> Panel:
            return cls._build_banner(render_sprite(FRAMES[frame]), st, usr, burl)

        final_frame = cls.get_frame_name(mood or "idle")

        with ui_lock:
            _console.print()
            if animate and _animations_enabled(_console):
                try:
                    with Live(banner(INTRO_SEQUENCE[0]), console=_console,
                              auto_refresh=False, transient=False) as live:
                        for frame in INTRO_SEQUENCE[1:]:
                            time.sleep(INTRO_DELAY)
                            live.update(banner(frame), refresh=True)
                        live.update(banner(final_frame), refresh=True)
                except KeyboardInterrupt:
                    pass
            else:
                _console.print(banner(final_frame))
            _console.print()


def _preview() -> None:
    """python -m moodle_scraper.poodle  -> muestra todos los frames y la intro."""
    for name, grid in FRAMES.items():
        _console.print(f"[dim]{name}[/dim]")
        _console.print(render_sprite(grid))
    PoodleUI.render_banner(status="Vista previa", animate=True)


if __name__ == "__main__":
    _preview()
