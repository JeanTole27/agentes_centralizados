"""Ventana de cambio (com.vc.*): guion de pasos de Capa Media y Base de Datos encadenados por el número de
cambio, en orden, con responsable por equipo y una sola evidencia.

- Se abre desde una plantilla (PLANTILLAS) con el número de cambio, ambiente, base y servidores/dominios.
- Cada paso es una acción del catálogo (se ejecuta con su formulario normal, con la referencia del cambio, y
  queda en el log de ejecuciones) o un paso manual (se marca hecho con una nota). Los pasos van en orden: solo
  el paso actual se puede ejecutar o marcar.
- Un paso de acción se da por hecho cuando se le vincula una ejecución EXITOSA de esa misma acción con la
  referencia del cambio. Una ejecución fallida lo deja pendiente (se reintenta).
- Al cerrar se consolida el resumen (pasos, quién, cuándo, ejecuciones con su SHA-256) y, si Jira está
  configurado y el cambio es una clave de Jira, se comenta en el caso.
"""
from __future__ import annotations

import re
import secrets
from typing import Any

from sqlalchemy import and_, select, update

from .almacen import Almacen, ejecucion, utc, ventana_cambio, ventana_paso
from .identidad import Usuario

RE_REFERENCIA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/.]{2,39}$")
EQUIPOS = ("BD", "CM", "AMBOS")
ESTADOS_FIN = ("hecho", "omitido")


class ErrorVentana(ValueError):
    pass


def _p(equipo: str, titulo: str, accion: str = "manual", params: dict | None = None, nota: str = "") -> dict:
    return {"equipo": equipo, "titulo": titulo, "accion": accion, "params": params or {}, "nota": nota}


# Variables de los parámetros: {base}, {hosts}, {dominios}, {cambio}. Los pasos que la plataforma todavía no
# automatiza (punto de restauración) quedan como manuales con la instrucción.
PLANTILLAS: dict[str, dict[str, Any]] = {
    "despliegue_bd_cm": {
        "nombre": "Despliegue PL/SQL con bajada de dominios",
        "descripcion": "Snapshot y punto de restauración, bajada de dominios, despliegue, inválidos y subida.",
        "requiere": ["base", "hosts"],
        "pasos": [
            _p("BD", "Snapshot ANTES", "bd.snap.tomar", {"base": "{base}", "cambio": "{cambio}", "momento": "ANTES"}),
            _p("BD", "Crear punto de restauración garantizado",
               nota="CREATE RESTORE POINT RP_{cambio} GUARANTEE FLASHBACK DATABASE (DBA; la plataforma aún no lo automatiza)"),
            _p("CM", "Inventario de Managed Servers", "orq.inventory", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("CM", "Bajar dominios", "orq.stop", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("BD", "Verificar sesiones de aplicación", "bd.ses.activas", {"base": "{base}"}),
            _p("BD", "Desplegar el script del cambio", nota="Paso manual: anotar el log o adjunto del despliegue"),
            _p("BD", "Snapshot DESPUÉS", "bd.snap.tomar", {"base": "{base}", "cambio": "{cambio}", "momento": "DESPUES"}),
            _p("BD", "Comparar ANTES vs DESPUÉS", "bd.snap.comparar", {"base": "{base}", "cambio": "{cambio}"}),
            _p("BD", "Recompilar inválidos nuevos", "bd.inv.recompilar", {"base": "{base}", "cambio": "{cambio}"}),
            _p("CM", "Subir dominios", "orq.start", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("CM", "Verificar RUNNING", "orq.status", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("BD", "Borrar punto de restauración", nota="DROP RESTORE POINT RP_{cambio} (DBA)"),
        ],
    },
    "despliegue_bd": {
        "nombre": "Despliegue solo en base de datos",
        "descripcion": "Snapshot antes y después, comparación de inválidos y recompilación.",
        "requiere": ["base"],
        "pasos": [
            _p("BD", "Snapshot ANTES", "bd.snap.tomar", {"base": "{base}", "cambio": "{cambio}", "momento": "ANTES"}),
            _p("BD", "Desplegar el script del cambio", nota="Paso manual: anotar el log o adjunto del despliegue"),
            _p("BD", "Snapshot DESPUÉS", "bd.snap.tomar", {"base": "{base}", "cambio": "{cambio}", "momento": "DESPUES"}),
            _p("BD", "Comparar ANTES vs DESPUÉS", "bd.snap.comparar", {"base": "{base}", "cambio": "{cambio}"}),
            _p("BD", "Recompilar inválidos nuevos", "bd.inv.recompilar", {"base": "{base}", "cambio": "{cambio}"}),
        ],
    },
    "reinicio_dominios": {
        "nombre": "Reinicio ordenado de dominios WebLogic",
        "descripcion": "Inventario, bajada, subida y verificación de los dominios.",
        "requiere": ["hosts"],
        "pasos": [
            _p("CM", "Inventario de Managed Servers", "orq.inventory", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("CM", "Bajar dominios", "orq.stop", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("CM", "Subir dominios", "orq.start", {"hosts": "{hosts}", "dominios": "{dominios}"}),
            _p("CM", "Verificar RUNNING", "orq.status", {"hosts": "{hosts}", "dominios": "{dominios}"}),
        ],
    },
}


def plantillas() -> list[dict]:
    return [{"id": k, "nombre": v["nombre"], "descripcion": v["descripcion"], "requiere": v["requiere"],
             "pasos": [{"equipo": p["equipo"], "titulo": p["titulo"], "accion": p["accion"]} for p in v["pasos"]]}
            for k, v in PLANTILLAS.items()]


def _sustituir(v: Any, vars_: dict[str, str]) -> Any:
    if isinstance(v, str):
        for k, x in vars_.items():
            v = v.replace("{" + k + "}", x)
        return v
    return v


def puede_operar_paso(u: Usuario, equipo: str) -> bool:
    """El paso de BD lo marca un DBA; el de Capa Media, alguien de Capa Media; el líder, cualquiera."""
    if "lid" in u.perfiles or u.total:
        return True
    if equipo == "AMBOS":
        return bool({"dba", "cm"} & set(u.perfiles))
    return ("dba" if equipo == "BD" else "cm") in u.perfiles


class ServicioVentanas:
    def __init__(self, almacen: Almacen, inventario):
        self.a, self.inv = almacen, inventario

    # -------------------------------------------------------------- lectura
    def listar(self, estado: str | None = None) -> list[dict]:
        q = select(ventana_cambio).order_by(ventana_cambio.c.creada.desc()).limit(200)
        if estado:
            q = q.where(ventana_cambio.c.estado == estado)
        with self.a.tx() as con:
            vs = [dict(f._mapping) for f in con.execute(q)]
            pasos = [dict(f._mapping) for f in con.execute(select(ventana_paso))]
        for v in vs:
            ps = [p for p in pasos if p["ventana_id"] == v["id"]]
            v["total_pasos"] = len(ps)
            v["hechos"] = sum(1 for p in ps if p["estado"] in ESTADOS_FIN)
        return vs

    def obtener(self, vid: str) -> dict:
        with self.a.tx() as con:
            v = con.execute(select(ventana_cambio).where(ventana_cambio.c.id == vid)).first()
            if not v:
                raise KeyError(vid)
            v = dict(v._mapping)
            v["pasos"] = [dict(f._mapping) for f in con.execute(
                select(ventana_paso).where(ventana_paso.c.ventana_id == vid).order_by(ventana_paso.c.orden))]
            ids = [p["ejecucion_id"] for p in v["pasos"] if p.get("ejecucion_id")]
            ejs = {f.id: dict(f._mapping) for f in con.execute(
                select(ejecucion.c.id, ejecucion.c.estado, ejecucion.c.evidencia_sha256, ejecucion.c.terminada)
                .where(ejecucion.c.id.in_(ids)))} if ids else {}
        actual = next((p["orden"] for p in v["pasos"] if p["estado"] not in ESTADOS_FIN), None)
        for p in v["pasos"]:
            p["ejecucion"] = ejs.get(p.get("ejecucion_id"))
            p["actual"] = p["orden"] == actual
        v["paso_actual"] = actual
        v["hechos"] = sum(1 for p in v["pasos"] if p["estado"] in ESTADOS_FIN)
        return v

    # -------------------------------------------------------------- escritura
    def abrir(self, u: Usuario, *, cambio: str, plantilla: str, titulo: str, ambiente: str,
              base: str = "", hosts: str = "", dominios: str = "", inicio=None, fin=None) -> dict:
        cambio = (cambio or "").strip().upper()
        if not RE_REFERENCIA.match(cambio):
            raise ErrorVentana("el número de cambio es obligatorio (p. ej. CHG0012345 o una clave de Jira)")
        if plantilla not in PLANTILLAS:
            raise ErrorVentana(f"plantilla desconocida: {plantilla}")
        pl = PLANTILLAS[plantilla]
        vars_ = {"base": (base or "").strip().upper(), "hosts": (hosts or "").strip(),
                 "dominios": (dominios or "").strip(), "cambio": cambio}
        for r in pl["requiere"]:
            if not vars_[r]:
                raise ErrorVentana(f"la plantilla «{pl['nombre']}» necesita {'la base' if r == 'base' else 'los servidores'}")
        if vars_["base"] and self.inv is not None and not self.inv.base(vars_["base"]):
            raise ErrorVentana(f"la base {vars_['base']} no está en el inventario")
        with self.a.tx() as con:
            abierta = con.execute(select(ventana_cambio.c.id).where(and_(
                ventana_cambio.c.cambio == cambio, ventana_cambio.c.estado == "en_curso"))).first()
            if abierta:
                raise ErrorVentana(f"ya hay una ventana en curso para {cambio} ({abierta.id})")
            vid = "VC-" + secrets.token_hex(4).upper()
            ahora = utc()
            con.execute(ventana_cambio.insert().values(
                id=vid, cambio=cambio, titulo=(titulo or pl["nombre"])[:200], plantilla=plantilla,
                ambiente=(ambiente or "").upper() or None, base=vars_["base"] or None, hosts=vars_["hosts"] or None,
                dominios=vars_["dominios"] or None, inicio=inicio, fin=fin, estado="en_curso",
                creada_por=u.login, creada=ahora, resumen=None))
            for i, p in enumerate(pl["pasos"], start=1):
                params = {k: _sustituir(v, vars_) for k, v in p["params"].items()}
                params = {k: v for k, v in params.items() if v not in ("", None)}
                con.execute(ventana_paso.insert().values(
                    ventana_id=vid, orden=i, equipo=p["equipo"], titulo=p["titulo"], accion=p["accion"],
                    params=params, nota=_sustituir(p["nota"], vars_) or None, estado="pendiente"))
        return self.obtener(vid)

    def _paso_actual(self, v: dict, orden: int, u: Usuario) -> dict:
        if v["estado"] != "en_curso":
            raise ErrorVentana("la ventana ya está cerrada")
        p = next((x for x in v["pasos"] if x["orden"] == orden), None)
        if not p:
            raise KeyError(orden)
        if not p["actual"]:
            raise ErrorVentana("los pasos van en orden: solo se puede operar el paso actual")
        if not puede_operar_paso(u, p["equipo"]):
            raise PermissionError(f"el paso {orden} es de {p['equipo']}: lo opera ese equipo o el líder")
        return p

    def marcar(self, vid: str, orden: int, u: Usuario, *, estado: str, nota: str = "") -> dict:
        """Paso manual hecho, u omitir un paso (con motivo)."""
        v = self.obtener(vid)
        p = self._paso_actual(v, orden, u)
        if estado not in ESTADOS_FIN:
            raise ErrorVentana("estado debe ser 'hecho' u 'omitido'")
        if estado == "hecho" and p["accion"] != "manual":
            raise ErrorVentana("este paso se completa ejecutando su acción")
        if estado == "omitido" and not (nota or "").strip():
            raise ErrorVentana("para omitir un paso hay que dejar el motivo")
        with self.a.tx() as con:
            con.execute(update(ventana_paso).where(and_(ventana_paso.c.ventana_id == vid, ventana_paso.c.orden == orden))
                        .values(estado=estado, hecho_por=u.login, hecho_en=utc(),
                                comentario=(nota or "").strip()[:1000] or None))
        return self.obtener(vid)

    def vincular(self, vid: str, orden: int, u: Usuario, ejecucion_id: str) -> dict:
        """Liga al paso la ejecución de su acción. Exitosa = paso hecho; fallida = sigue pendiente."""
        v = self.obtener(vid)
        p = self._paso_actual(v, orden, u)
        with self.a.tx() as con:
            e = con.execute(select(ejecucion).where(ejecucion.c.id == ejecucion_id)).first()
        if not e:
            raise ErrorVentana("la ejecución no existe")
        e = dict(e._mapping)
        if e["accion"] != p["accion"]:
            raise ErrorVentana(f"la ejecución es de {e['accion']}, el paso pide {p['accion']}")
        if (e.get("cambio") or "").upper() != v["cambio"]:
            raise ErrorVentana(f"la ejecución no tiene la referencia {v['cambio']}")
        hecho = e["estado"] == "exitosa"
        with self.a.tx() as con:
            con.execute(update(ventana_paso).where(and_(ventana_paso.c.ventana_id == vid, ventana_paso.c.orden == orden))
                        .values(ejecucion_id=e["id"], estado="hecho" if hecho else "pendiente",
                                hecho_por=e["solicitante"] if hecho else None, hecho_en=utc() if hecho else None,
                                comentario=None if hecho else f"última ejecución {e['id']}: {e['estado']}"))
        return self.obtener(vid)

    def cerrar(self, vid: str, u: Usuario, *, forzar_nota: str = "") -> dict:
        v = self.obtener(vid)
        if v["estado"] != "en_curso":
            raise ErrorVentana("la ventana ya está cerrada")
        faltan = [p for p in v["pasos"] if p["estado"] not in ESTADOS_FIN]
        if faltan and not forzar_nota.strip():
            raise ErrorVentana(f"quedan {len(faltan)} paso(s) pendientes: complétalos, omítelos o cierra con motivo")
        lineas = [f"Ventana {v['id']} · {v['cambio']} · {v['titulo']}"]
        for p in v["pasos"]:
            e = p.get("ejecucion") or {}
            extra = f" · ejecución {p['ejecucion_id']}" + (f" (SHA-256 {e['evidencia_sha256'][:12]}…)"
                                                         if e.get("evidencia_sha256") else "") if p.get("ejecucion_id") else ""
            lineas.append(f"{p['orden']}. [{p['equipo']}] {p['titulo']}: {p['estado']}"
                          f"{' por ' + p['hecho_por'] if p.get('hecho_por') else ''}{extra}"
                          f"{' — ' + p['comentario'] if p.get('comentario') else ''}")
        if faltan:
            lineas.append(f"Cerrada con {len(faltan)} paso(s) pendientes: {forzar_nota.strip()[:500]}")
        lineas.append(f"Cerrada por {u.login}.")
        estado = "cerrada" if not faltan else "cerrada_incompleta"
        with self.a.tx() as con:
            con.execute(update(ventana_cambio).where(ventana_cambio.c.id == vid)
                        .values(estado=estado, cerrada=utc(), cerrada_por=u.login, resumen="\n".join(lineas)))
        return self.obtener(vid)
