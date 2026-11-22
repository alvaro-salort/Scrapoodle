"""
Pruebas unitarias para la interfaz de la mascota Poodle (PoodleUI).
Verifica estados de ánimo, renderizado del banner y compatibilidad de scroll.
"""

from moodle_scraper.poodle import (
    FRAMES,
    INTRO_SEQUENCE,
    MOOD_ICONS,
    MOOD_SPRITES,
    PALETTE,
    SPRITE_HEIGHT,
    SPRITE_WIDTH,
    TRANSPARENT,
    PoodleUI,
    render_sprite,
    sprite_to_svg,
)


from rich.cells import cell_len
from rich.text import Text


def test_poodle_frames_are_consistent():
    """Todos los frames tienen el mismo tamaño y solo usan colores de la paleta."""
    for name, grid in FRAMES.items():
        assert len(grid) == SPRITE_HEIGHT * 2, name
        for row in grid:
            assert len(row) == SPRITE_WIDTH, name
            assert set(row) <= set(PALETTE) | {TRANSPARENT}, name


def test_poodle_mood_sprites_point_to_frames():
    for mood, frames in MOOD_SPRITES.items():
        assert frames, mood
        assert set(frames) <= set(FRAMES), mood
    assert set(INTRO_SEQUENCE) <= set(FRAMES)


def test_render_sprite_dimensions():
    """La terminal recibe 6 filas de 19 celdas (2 píxeles por celda)."""
    text = render_sprite(FRAMES["base"])
    lines = text.plain.split("\n")
    assert len(lines) == SPRITE_HEIGHT
    assert all(cell_len(line) == SPRITE_WIDTH for line in lines)


def test_render_sprite_half_blocks():
    """Píxel arriba/abajo/ambos/dos colores se mapean al bloque correcto."""
    pal = {"A": "#ff0000", "B": "#00ff00"}
    text = render_sprite(["A.AA.", ".AAB."], palette=pal)
    assert text.plain == "▀▄█▀ "
    # La última celda con dos colores usa fondo para el píxel de abajo
    styles = [span.style for span in text.spans]
    assert any(getattr(st, "bgcolor", None) is not None for st in styles)


def test_sprite_to_svg():
    svg = sprite_to_svg(FRAMES["base"], scale=4)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    filled = sum(c != TRANSPARENT for row in FRAMES["base"] for c in row)
    assert svg.count("<rect") == filled


def test_banner_renders_without_animation_off_terminal():
    """Fuera de una TTY no se anima y no se rompe la salida."""
    PoodleUI.render_banner(status="Test", animate=True)


def test_poodle_set_status_auto_mood_deduction():
    """Verifica que el estado deduzca automáticamente el ánimo visual de la mascota."""
    PoodleUI._disabled = False
    PoodleUI.set_status("Descargando archivo 1...")
    assert PoodleUI.get_mood() == "working"

    PoodleUI.set_status("Extrayendo teoría a Markdown...")
    assert PoodleUI.get_mood() == "reading"

    PoodleUI.set_status("¡Descarga completada con éxito!")
    assert PoodleUI.get_mood() == "happy"

    PoodleUI.set_status("Esperando selección del usuario...")
    assert PoodleUI.get_mood() == "sleeping"

    PoodleUI.set_status("Listo para raspar")
    assert PoodleUI.get_mood() == "idle"


def test_poodle_explicit_mood():
    """Verifica que se pueda forzar un mood explícito."""
    PoodleUI.set_status("Cualquier texto", mood="happy")
    assert PoodleUI.get_mood() == "happy"
    assert PoodleUI.get_frame_name("happy") == "happy"


def test_poodle_get_badge():
    """Verifica la generación del badge compacto de la mascota."""
    PoodleUI.set_status("Verificando enlaces", mood="working")
    badge = PoodleUI.get_badge()
    assert "Poodle:" in badge
    assert "Verificando enlaces" in badge


def test_poodle_start_and_stop():
    """Verifica el ciclo de activación y desactivación sin romper stdout."""
    PoodleUI._disabled = False
    PoodleUI._banner_rendered = True  # Para no re-imprimir en tests
    PoodleUI.start(disabled=False)
    assert PoodleUI.is_active() is True

    PoodleUI.stop()
    assert PoodleUI.is_active() is False


def test_poodle_disabled_mode():
    """Verifica que si disabled=True, la mascota permanece inactiva."""
    PoodleUI._active = False
    PoodleUI._disabled = False
    PoodleUI.start(disabled=True)
    assert PoodleUI.is_active() is False
