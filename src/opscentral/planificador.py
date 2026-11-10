"""Planificador: tareas periódicas (una sola instancia por ambiente).

- Recolección de la flota cada 15 min (protegida por los límites de 04 §3).
- Scoring de predicciones cada hora (en sombra).
- Zabbix: problemas abiertos cada 5 min y trends de capacidad cada hora.
- Vence aprobaciones sin respuesta y libera trabajos de workers caídos.
"""
from __future__ import annotations

import logging
import threading
import time

from .nucleo import Nucleo, crear

log = logging.getLogger("opscentral.planificador")

TAREAS = {  # nombre: (cada_s, cola, tipo)
    "recolectar": (900, "bd", "recolectar"),
    "scoring": (3600, "ml", "scoring"),
    "zbx_problemas": (300, "ingesta", "zbx_problemas"),    # respaldo del webhook
    "zbx_capacidad": (3600, "ingesta", "zbx_capacidad"),   # trends → series de capacidad → scoring
}


class Planificador:
    def __init__(self, nucleo: Nucleo | None = None):
        self.n = nucleo or crear("cli")
        self._ultimo: dict[str, float] = {}

    def tic(self, ahora: float | None = None) -> list[str]:
        ahora = ahora or time.time()
        hechas = []
        for nombre, (cada, cola, tipo) in TAREAS.items():
            if ahora - self._ultimo.get(nombre, 0) >= cada:
                self.n.cola.encolar(cola, tipo, {}, clave_unica=f"plan:{nombre}")
                self._ultimo[nombre] = ahora
                hechas.append(nombre)
        vencidas = self.n.ejecuciones.vencer_pendientes()
        liberados = self.n.cola.liberar_huerfanos()
        if vencidas or liberados:
            log.info("aprobaciones vencidas=%s trabajos liberados=%s", vencidas, liberados)
        return hechas

    def correr(self, detener: threading.Event, pausa: float = 30) -> None:
        while not detener.is_set():
            try:
                self.tic()
            except Exception:
                log.exception("error en el planificador")
            detener.wait(pausa)
