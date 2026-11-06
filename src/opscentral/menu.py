"""Menú por perfil: el mismo de la interfaz elegida (demo-ops-central.html, 01 §3 y §4.1)."""
from __future__ import annotations

GRUPOS = [
    ("GENERAL", [("inicio", "Inicio"), ("ventana", "Ventana de cambio"), ("incidentes", "Incidentes y cambios"),
                 ("inventario", "Inventario")]),
    ("PREVENCIÓN", [("alertas", "Alertas (Zabbix)"), ("prevencion", "Predicciones ML")]),
    ("CAPA MEDIA", [("orquestacion", "Orquestación WebLogic"), ("usuarioswl", "Usuarios WebLogic"),
                    ("frmweb", "Forms · frmweb")]),
    ("BASE DE DATOS", [("snapshot", "Snapshot e inválidos"), ("bloqueos", "Sesiones bloqueantes"),
                       ("salud", "Salud de la flota"), ("tareas", "Tareas DBA"), ("reportes", "Reportes")]),
    ("CONTROL", [("aprobaciones", "Aprobaciones"), ("ejecuciones", "Ejecuciones y evidencia"),
                 ("admin", "Administración")]),
]

PERFIL = {
    "dba": {"nav": ["inicio", "alertas", "prevencion", "ventana", "incidentes", "inventario", "orquestacion", "snapshot",
                    "bloqueos", "salud", "tareas", "reportes", "aprobaciones", "ejecuciones"],
            "ro": ["orquestacion"]},
    "cm": {"nav": ["inicio", "alertas", "prevencion", "ventana", "incidentes", "inventario", "orquestacion",
                   "usuarioswl", "frmweb", "bloqueos", "salud", "aprobaciones", "ejecuciones"],
           "ro": ["bloqueos", "salud"]},
    "lid": {"nav": [p for _, ps in GRUPOS for p, _ in ps], "ro": []},
    "con": {"nav": ["inicio", "alertas", "prevencion", "ventana", "incidentes", "inventario", "orquestacion", "snapshot",
                    "bloqueos", "salud", "reportes", "ejecuciones"],
            "ro": ["alertas", "prevencion", "ventana", "orquestacion", "snapshot", "bloqueos", "salud", "reportes"]},
    "adm": {"nav": ["inicio", "inventario", "ejecuciones", "admin"], "ro": ["inventario", "ejecuciones"]},
}


def menu(perfiles) -> list[dict]:
    """Unión de pantallas; una pantalla es de solo lectura solo si lo es para TODOS los perfiles que la ven."""
    visibles: set[str] = set()
    editables: set[str] = set()
    for p in perfiles:
        cfg = PERFIL.get(p)
        if not cfg:
            continue
        visibles |= set(cfg["nav"])
        editables |= set(cfg["nav"]) - set(cfg["ro"])
    out = []
    for grupo, pantallas in GRUPOS:
        items = [{"id": i, "nombre": n, "solo_lectura": i not in editables} for i, n in pantallas if i in visibles]
        if items:
            out.append({"grupo": grupo, "pantallas": items})
    return out
