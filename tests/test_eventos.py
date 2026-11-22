import json

from moodle_scraper.eventos import BusEventos, Evento
from moodle_scraper.observadores import ObservadorReporteJSON


class ObservadorDePrueba:
    def __init__(self):
        self.eventos = []

    def actualizar(self, evento):
        self.eventos.append(evento)


class ObservadorQueFalla:
    def actualizar(self, evento):
        raise RuntimeError("observador roto")


def test_bus_publica_a_todos_los_observadores():
    primero = ObservadorDePrueba()
    segundo = ObservadorDePrueba()
    bus = BusEventos([primero, segundo])

    evento = Evento("curso_iniciado", {"curso": "Diseno de Software"})
    bus.publicar(evento)

    assert primero.eventos == [evento]
    assert segundo.eventos == [evento]


def test_bus_permite_desuscribir():
    observador = ObservadorDePrueba()
    bus = BusEventos([observador])

    bus.publicar(Evento("log", {"mensaje": "uno"}))
    bus.desuscribir(observador)
    bus.publicar(Evento("log", {"mensaje": "dos"}))

    assert len(observador.eventos) == 1


def test_un_observador_roto_no_corta_la_notificacion(capsys):
    sano = ObservadorDePrueba()
    bus = BusEventos([ObservadorQueFalla(), sano])

    bus.publicar(Evento("descarga_fallida", {"archivo": "apunte.pdf"}))

    assert len(sano.eventos) == 1
    assert len(bus.errores) == 1
    assert "ObservadorQueFalla" in capsys.readouterr().err


def test_reporte_json_escribe_una_linea_por_evento(tmp_path):
    ruta = tmp_path / "reporte_eventos.json"
    reporte = ObservadorReporteJSON(ruta)
    bus = BusEventos([reporte])

    bus.publicar(Evento("curso_iniciado", {"curso": "Base de Datos"}))
    bus.publicar(Evento("descarga_completada", {"archivo": "clase.pdf", "tamano": "2 MB"}))

    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2

    primero = json.loads(lineas[0])
    segundo = json.loads(lineas[1])
    assert primero["tipo"] == "curso_iniciado"
    assert primero["datos"]["curso"] == "Base de Datos"
    assert segundo["tipo"] == "descarga_completada"
    assert segundo["datos"]["archivo"] == "clase.pdf"
