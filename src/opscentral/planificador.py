"""Planificador: tareas periódicas (una sola instancia por ambiente).

- BD liviana cada [bd] recoleccion_periodica_s (1 h): instancia + espacio ([bd] secciones_periodicas).
- BD completa cada [bd] recoleccion_completa_s (1 día): incluye inválidos y bloqueos.
  Bloqueos, inválidos y tablespaces se actualizan además por alerta de Zabbix (refresco dirigido).
- Bases con bloqueos a la vista: se releen cada [bd] bloqueos_revision_s (5 min) hasta que se limpien.
- Scoring de predicciones cada hora (en sombra).
- Zabbix: problemas abiertos cada 5 min, trends de capacidad cada hora y vínculo de hosts por IP diario.
- Vence aprobaciones sin respuesta y libera trabajos de workers caídos.
"""
from __future__ import annotations

import logging
import threading
import time

from .nucleo import Nucleo, crear

log = logging.getLogger("opscentral.planificador")


def tareas(cfg) -> dict[str, tuple[int, str, str, dict]]:
    """nombre: (cada_s, cola, tipo, payload)"""
    bd, z = cfg["bd"], cfg["zabbix"]
    return {
        "recolectar": (int(bd["recoleccion_periodica_s"]), "bd", "recolectar",
                       {"secciones": list(bd["secciones_periodicas"])}),
        "recolectar_completo": (int(bd["recoleccion_completa_s"]), "bd", "recolectar", {}),
        # solo las bases que hoy muestran bloqueos: los confirma o los limpia (no deja lecturas viejas)
        "bloqueos_vigentes": (int(bd.get("bloqueos_revision_s", 300)), "bd", "bloqueos_vigentes", {}),
        "scoring": (3600, "ml", "scoring", {}),
        "zbx_problemas": (300, "ingesta", "zbx_problemas", {}),    # respaldo del webhook
        "zbx_capacidad": (3600, "ingesta", "zbx_capacidad", {}),   # trends → series de capacidad → scoring
        "zbx_vincular": (int(z["vincular_s"]), "ingesta", "zbx_vincular", {}),
    }


class Planificador:
    def __init__(self, nucleo: Nucleo | None = None):
        self.n = nucleo or crear("cli")
        self._ultimo: dict[str, float] = {}

    def tic(self, ahora: float | None = None) -> list[str]:
        ahora = ahora or time.time()
        hechas = []
        for nombre, (cada, cola, tipo, payload) in tareas(self.n.cfg).items():
            if ahora - self._ultimo.get(nombre, 0) >= cada:
                self.n.cola.encolar(cola, tipo, payload, clave_unica=f"plan:{nombre}")
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
