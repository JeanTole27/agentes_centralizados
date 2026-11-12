"""Asistente operativo (ventana de chat) — contrato listo, motor aún no conectado.

La interfaz ya tiene la ventana de chat (demo-ops-central.html, 01 §4.3): envía la pregunta con
el contexto de la pantalla (perfil, pantalla, base/servidor elegido) y muestra la respuesta,
las fuentes, las herramientas consultadas y las acciones sugeridas.

Mientras `[asistente] habilitado = false` (o falte `url`), la API responde con estado
"no_conectado" y un mensaje fijo: no inventa respuestas. Cuando se conecte el motor
(RAG de runbooks e incidentes + herramientas de solo lectura), solo cambia `_consultar_motor`;
la forma de la respuesta es la misma.

Reglas que se mantienen al conectarlo:
- Solo lectura: el asistente nunca ejecuta. Una acción sugerida es un enlace a la pantalla o
  una propuesta que pasa por el formulario normal (evaluar → confirmar → ejecutar).
- Cada pregunta y cada valoración quedan en la auditoría.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

PANTALLAS_CONOCIDAS = {
    "inicio", "ventana", "incidentes", "inventario", "alertas", "prevencion", "orquestacion", "usuarioswl",
    "frmweb", "snapshot", "bloqueos", "salud", "tareas", "reportes", "aprobaciones", "ejecuciones", "admin",
}

SUGERENCIAS = {  # preguntas de ejemplo por perfil (las mismas de la demo)
    "dba": ["¿Qué se va a llenar esta semana?", "¿Cómo atiendo objetos inválidos tras un despliegue?",
            "¿Qué hago con una sesión que bloquea a otras?", "¿Hay cuentas de aplicación por expirar?"],
    "cm": ["¿Qué se va a llenar esta semana?", "¿Por qué hay stuck threads en un dominio?",
           "¿Cuál es el orden para subir un dominio?", "¿Cómo mitigo frmweb huérfanos?"],
    "lid": ["Resume el último cambio", "¿Qué alertas siguen abiertas?", "¿Cómo está el lag de Data Guard?"],
    "con": ["¿Qué es un objeto inválido?", "¿Qué alertas siguen abiertas?"],
}
SUGERENCIAS["aut"] = SUGERENCIAS["dba"][:2] + SUGERENCIAS["cm"][1:3]
SUGERENCIAS["adm"] = ["¿Cómo agrego una base al inventario?", "¿Qué ejecuciones fallaron hoy?"]


class PreguntaInvalida(ValueError):
    pass


@dataclass
class Respuesta:
    estado: str                                  # "no_conectado" | "ok" | "error"
    texto: str
    fuentes: list[dict] = field(default_factory=list)       # [{documento, seccion, version}]
    herramientas: list[str] = field(default_factory=list)   # consultas de solo lectura usadas
    acciones: list[dict] = field(default_factory=list)      # [{etiqueta, tipo: "ir"|"accion", destino}]
    id: str = field(default_factory=lambda: os.urandom(6).hex())

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "estado": self.estado, "texto": self.texto, "fuentes": self.fuentes,
                "herramientas": self.herramientas, "acciones": self.acciones}


class Asistente:
    def __init__(self, cfg):
        self.c = cfg["asistente"]

    @property
    def conectado(self) -> bool:
        return bool(self.c.get("habilitado") and self.c.get("url"))

    def estado(self, perfiles) -> dict:
        sug: list[str] = []
        for p in perfiles:
            sug += [s for s in SUGERENCIAS.get(p, []) if s not in sug]
        return {"conectado": self.conectado, "motor": self.c.get("motor") or None,
                "max_caracteres": self.c["max_caracteres"], "sugerencias": sug[:5],
                "mensaje": None if self.conectado else
                "El asistente todavía no está conectado: la ventana registra las preguntas para "
                "priorizar los runbooks, pero aún no responde."}

    def validar(self, pregunta: str, contexto: dict | None) -> tuple[str, dict]:
        q = (pregunta or "").strip()
        if not q:
            raise PreguntaInvalida("la pregunta está vacía")
        if len(q) > self.c["max_caracteres"]:
            raise PreguntaInvalida(f"la pregunta supera {self.c['max_caracteres']} caracteres")
        ctx = {k: str(v)[:80] for k, v in (contexto or {}).items() if k in ("pantalla", "base", "servidor", "dominio",
                                                                             "ambiente", "referencia") and v}
        if ctx.get("pantalla") and ctx["pantalla"] not in PANTALLAS_CONOCIDAS:
            ctx.pop("pantalla")
        return q, ctx

    def preguntar(self, pregunta: str, contexto: dict, perfiles) -> Respuesta:
        if not self.conectado:
            donde = f" (pantalla {contexto['pantalla']})" if contexto.get("pantalla") else ""
            acciones = []
            if contexto.get("pantalla") in ("snapshot", "bloqueos", "salud"):
                acciones.append({"etiqueta": "Ver el log de ejecuciones", "tipo": "ir", "destino": "ejecuciones"})
            return Respuesta(
                estado="no_conectado",
                texto=("Recibí tu pregunta" + donde + ", pero el asistente aún no está conectado a la base de "
                       "conocimiento (runbooks, incidentes y cambios). Quedó registrada para priorizar qué documentar."),
                acciones=acciones)
        return self._consultar_motor(pregunta, contexto, perfiles)

    def _consultar_motor(self, pregunta: str, contexto: dict, perfiles) -> Respuesta:
        # Punto de conexión del motor (RAG + herramientas de solo lectura). Pendiente de etapa posterior.
        return Respuesta(estado="error", texto="El motor del asistente está configurado pero su conector aún no "
                                               "está implementado en esta versión.")
