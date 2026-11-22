"""Bus de eventos para desacoplar el scraper de sus salidas."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Protocol, Tuple
import sys


@dataclass(frozen=True)
class Evento:
    tipo: str
    datos: Dict[str, Any] = field(default_factory=dict)
    momento: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Observador(Protocol):
    def actualizar(self, evento: Evento) -> None:
        ...


class BusEventos:
    """Publica eventos a una lista de observadores y aísla sus errores."""

    def __init__(self, observadores: Iterable[Observador] | None = None):
        self._observadores: List[Observador] = list(observadores or [])
        self._lock = RLock()
        self.errores: List[Tuple[Observador, Evento, Exception]] = []

    def suscribir(self, observador: Observador) -> None:
        with self._lock:
            if observador not in self._observadores:
                self._observadores.append(observador)

    def desuscribir(self, observador: Observador) -> None:
        with self._lock:
            if observador in self._observadores:
                self._observadores.remove(observador)

    def publicar(self, evento: Evento) -> None:
        with self._lock:
            observadores = list(self._observadores)

        for observador in observadores:
            try:
                observador.actualizar(evento)
            except Exception as exc:
                if len(self.errores) < 100:
                    self.errores.append((observador, evento, exc))
                print(
                    f"[eventos] {observador.__class__.__name__} falló en {evento.tipo}: {exc}",
                    file=sys.stderr,
                )
