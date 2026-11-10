"""Qué ítems de Zabbix son métricas de capacidad y en qué serie de Ops Central se guardan.

Tomado del catálogo de zabbix-capacity-mcp (plantillas "Linux/Windows by Zabbix agent"
5.x–7.x y keys antiguas). El comodín es `*`; lo demás es literal.

  disco    vfs.fs.size[<punto>,pused] / vfs.fs.dependent.size[...]  → fs:<host>:<punto>
  memoria  vm.memory.utilization / vm.memory.size[pavailable]        → mem:<host>
  CPU      system.cpu.util / system.cpu.util[,idle]                   → cpu:<host>

Las series fs: entran al modelo de llenado (M1). mem: y cpu: se guardan para gráficos
y para la vista de capacidad del servidor; no tienen "fecha de llenado".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@lru_cache(maxsize=None)
def _rx(patron: str) -> re.Pattern[str]:
    return re.compile(".*".join(re.escape(p) for p in patron.split("*")), re.IGNORECASE)


@dataclass(frozen=True)
class Metrica:
    id: str
    categoria: str            # disco | memoria | cpu
    patrones: tuple[str, ...]
    invertida: bool = False   # True: el ítem mide lo libre (idle, pavailable) → se guarda 100 - valor
    prioridad: int = 0        # si un host tiene dos ítems para la misma serie, gana el de menor prioridad

    def coincide(self, key: str) -> bool:
        return any(_rx(p).fullmatch(key) for p in self.patrones)


CATALOGO: tuple[Metrica, ...] = (
    Metrica("disk_pused", "disco", ("vfs.fs.size[*,pused]", "vfs.fs.dependent.size[*,pused]")),
    Metrica("mem_util", "memoria", ("vm.memory.utilization", "vm.memory.util", "vm.memory.util[]")),
    Metrica("mem_pavailable", "memoria", ("vm.memory.size[pavailable]",), invertida=True, prioridad=1),
    Metrica("cpu_util", "cpu", ("system.cpu.util", "system.cpu.util[,,avg1]",
                                "perf_counter*processor*(_total)*% processor time*")),
    Metrica("cpu_idle", "cpu", ("system.cpu.util[,idle]", "system.cpu.util[,idle,avg1]",
                                "system.cpu.util[all,idle*"), invertida=True, prioridad=1),
)

# Puntos de montaje que no son capacidad real (montajes del sistema o de solo lectura)
_IGNORAR_FS = ("/dev", "/proc", "/sys", "/run", "/snap/")


def reconocer(key: str) -> Metrica | None:
    return next((m for m in CATALOGO if m.coincide(key)), None)


def punto_montaje(key: str) -> str | None:
    """vfs.fs.size[/u01,pused] → /u01 ; vfs.fs.dependent.size["C:",pused] → C:"""
    m = re.search(r"\[(.*),\s*pused\s*\]$", key)
    if not m:
        return None
    return m.group(1).strip().strip('"') or None


def serie_para(host: str, key: str) -> tuple[str, Metrica] | None:
    """Nombre de la serie de Ops Central para un ítem, o None si no es de capacidad."""
    m = reconocer(key)
    if m is None:
        return None
    if m.categoria == "disco":
        punto = punto_montaje(key)
        if not punto or punto.startswith(_IGNORAR_FS):
            return None
        return f"fs:{host}:{punto}", m
    return f"{'mem' if m.categoria == 'memoria' else 'cpu'}:{host}", m


def valor(m: Metrica, v: float) -> float:
    return round(100.0 - v, 3) if m.invertida else round(v, 3)
