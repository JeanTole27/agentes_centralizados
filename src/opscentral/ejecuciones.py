"""Ciclo de vida de una ejecución (F1–F3, RF-APR, RF-AUD).

    solicitar ─► pendiente_aprobacion ─► (aprobar) ─► en_cola ─► ejecutando ─► exitosa | fallida
        │                 └─► rechazada | vencida | cancelada
        └─► en_cola (si no requiere aprobación)

Garantías:
- La API valida contra el catálogo y decide permisos; la interfaz no decide nada.
- La huella (acción + destino + parámetros + referencia del caso) se calcula al solicitar.
- El caso asociado (clave de Jira u otra referencia) es opcional.
- Sin cola de aprobación por defecto: quien tiene permiso de ejecución ejecuta, y todo queda en el log.
  El worker la recalcula y verifica el token de aprobación antes de ejecutar.
- Un solo cambio (clase O/C) a la vez por destino.
- Toda ejecución termina con evidencia (JSON + SHA-256) y registros de auditoría.
- Un destino que no está en el inventario se trata como PROD (lo más restrictivo).
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from . import aprobaciones
from .almacen import Almacen, bloqueo_destino, ejecucion, linea, utc
from .auditoria import Auditoria
from .autorizacion import PermisoDenegado, decidir, exigir, permiso_de, puede_aprobar
from .catalogo import RE_REFERENCIA, Accion, Catalogo, ErrorValidacion
from .cola import Cola
from .evidencia import Evidencia
from .identidad import Usuario
from .inventario import Inventario
from .secretos import Secretos

FINALES = {"exitosa", "fallida", "rechazada", "vencida", "cancelada"}
COLA_DE_MOTOR = {"bd": "bd", "cm": "cm", "refresco": "ingesta", "zabbix": "ingesta", "ml": "ml"}


class Conflicto(RuntimeError):
    pass


class RequiereStepUp(PermissionError):
    """El cliente debe pedir un segundo factor y reintentar."""


class EstadoInvalido(RuntimeError):
    pass


def nuevo_id() -> str:
    return time.strftime("%Y%m%d%H%M%S") + os.urandom(3).hex()


class ServicioEjecuciones:
    def __init__(self, cfg, almacen: Almacen, catalogo: Catalogo, inventario: Inventario,
                 auditoria: Auditoria, cola: Cola, secretos: Secretos, evidencia: Evidencia):
        self.cfg, self.a, self.cat, self.inv = cfg, almacen, catalogo, inventario
        self.aud, self.cola, self.sec, self.evid = auditoria, cola, secretos, evidencia

    # ================================================================ lado API
    def destino_de(self, accion: Accion, params: dict, destino: str | None) -> str:
        if accion.motor == "bd":
            base = params.get("base")
            if base:
                if not self.inv.base(base):
                    raise ErrorValidacion(f"la base {base} no está en el inventario")
                return base
            for clave in ("dominio", "servidor"):      # cm.bd_relacionada, bd.rep.diskgroups
                if params.get(clave):
                    a = self.inv.buscar(params[clave])
                    if not a:
                        raise ErrorValidacion(f"{clave} {params[clave]} no está en el inventario")
                    return a.id
        if accion.motor == "cm":
            d = params.get("host") or params.get("hosts") or destino
            if not d:
                raise ErrorValidacion("indica el servidor (host o hosts)")
            return str(d).upper()
        if not destino:
            raise ErrorValidacion("falta el destino")
        return destino

    def ambiente(self, destino: str) -> str:
        return self.inv.ambiente_de(destino) or "PROD"

    def evaluar(self, usuario: Usuario, accion_id: str, params: dict, destino: str | None = None):
        """Lo que la interfaz muestra antes de confirmar: controles exigidos y vista previa."""
        acc = self.cat[accion_id]
        p = acc.validar_parametros(params)
        dest = self.destino_de(acc, p, destino)
        amb = self.ambiente(dest)
        c = self.cfg["aprobaciones"]
        d = decidir(usuario, acc, amb, aprobacion_c_no_prod=c["aprobacion_c_no_prod"],
                    ejecucion_directa=c.get("ejecucion_directa", c.get("dba_ejecuta_directo", True)),
                    exigir_referencia_prod=c.get("exigir_referencia_prod", False))
        return acc, p, dest, amb, d

    def solicitar(self, usuario: Usuario, accion_id: str, params: dict, *, destino: str | None = None,
                  cambio: str | None = None, motivo: str | None = None, origen: str = "persona") -> dict:
        acc, p, dest, amb, d = self.evaluar(usuario, accion_id, params, destino)
        exigir(d)
        cambio = (cambio or p.get("cambio") or "").strip().upper() or None
        if cambio and not RE_REFERENCIA.match(cambio):
            raise ErrorValidacion("referencia del caso inválida (3 a 40 caracteres: letras, números, - _ . /)")
        if d.requiere_cambio and not cambio:
            raise ErrorValidacion(f"{acc.id} en {amb} requiere un caso asociado (exigir_referencia_prod = true)")
        if d.requiere_stepup and not usuario.mfa_reciente(self.cfg["auth"]["stepup_vigencia_min"]):
            raise RequiereStepUp("confirma tu identidad con el segundo factor para continuar")
        if d.clase in ("O", "C") and not motivo and acc.motor in ("bd", "cm"):
            motivo = p.get("motivo") or p.get("justificacion")
        if d.clase == "C" and not motivo:
            raise ErrorValidacion("los cambios de clase C requieren un motivo")
        if d.clase in ("O", "C"):
            self._sin_conflicto(dest)

        eid = nuevo_id()
        h = aprobaciones.huella(acc.id, dest, p, cambio)
        estado = "pendiente_aprobacion" if d.requiere_aprobacion else "en_cola"
        token = comentario = None
        if d.directa:
            # El worker exige un token firmado para toda clase C en PROD/DRP. Para la ejecución directa
            # lo firma la política (no una persona): queda ligado a la misma huella y no se puede reutilizar.
            clave = self.sec.leer(self.cfg["aprobaciones"]["clave_firma_ref"], motivo=f"ejecución directa {eid}")["clave"]
            token = aprobaciones.emitir(clave, ejecucion_id=eid, huella_hex=h, aprobador=aprobaciones.POLITICA_DIRECTA,
                                        solicitante=usuario.login,
                                        vigencia_min=self.cfg["aprobaciones"]["vigencia_min"])
            comentario = "Ejecución directa: el usuario tiene permiso de ejecución sobre la acción (sin cola de aprobación)"
        t = utc()
        with self.a.tx() as con:
            con.execute(ejecucion.insert().values(
                id=eid, accion=acc.id, motor=acc.motor, clase=d.clase, destino=dest, ambiente=amb, params=p,
                cambio=cambio, motivo=motivo, solicitante=usuario.login, estado=estado, huella=h,
                creada=t, actualizada=t, origen=origen, token_aprobacion=token, comentario=comentario))
            if estado == "en_cola":
                self._encolar(con, eid, acc)
        self.aud.registrar("ejecucion_solicitada", usuario.login, ejecucion=eid, accion=acc.id, destino=dest,
                           ambiente=amb, clase=d.clase, cambio=cambio, controles=d.controles, huella=h, origen=origen,
                           ejecucion_directa=d.directa)
        return self.obtener(eid)

    def _encolar(self, con, eid: str, acc: Accion) -> None:
        cola = COLA_DE_MOTOR.get(acc.motor)
        if not cola:
            raise ErrorValidacion(f"el motor {acc.motor} aún no tiene worker")
        self.cola.encolar(cola, "ejecutar", {"ejecucion_id": eid}, clave_unica=f"ejec:{eid}", con=con)

    def _sin_conflicto(self, destino: str) -> None:
        with self.a.tx() as con:
            f = con.execute(select(bloqueo_destino).where(bloqueo_destino.c.destino == destino)).first()
            if f:
                raise Conflicto(f"ya hay un cambio en curso sobre {destino} (ejecución {f.ejecucion_id})")
            activa = con.execute(select(ejecucion.c.id).where(and_(
                ejecucion.c.destino == destino, ejecucion.c.clase.in_(("O", "C")),
                ejecucion.c.estado.in_(("en_cola", "ejecutando"))))).first()
            if activa:
                raise Conflicto(f"ya hay un cambio en cola sobre {destino} (ejecución {activa[0]})")

    def aprobar(self, aprobador: Usuario, eid: str, comentario: str = "") -> dict:
        e = self._fila(eid)
        if e["estado"] != "pendiente_aprobacion":
            raise EstadoInvalido(f"la ejecución está en estado {e['estado']}")
        acc = self.cat[e["accion"]]
        ok, motivo = puede_aprobar(aprobador, e["solicitante"], acc)
        if not ok:
            raise PermisoDenegado(motivo)
        if e["clase"] == "C" and not aprobador.mfa_reciente(self.cfg["auth"]["stepup_vigencia_min"]):
            raise RequiereStepUp("aprobar un cambio de clase C exige segundo factor reciente")
        if self._vencida(e):
            self._marcar(eid, "vencida", actor="sistema")
            raise EstadoInvalido("la solicitud venció; se debe solicitar de nuevo")
        clave = self.sec.leer(self.cfg["aprobaciones"]["clave_firma_ref"], motivo=f"aprobar {eid}")["clave"]
        token = aprobaciones.emitir(clave, ejecucion_id=eid, huella_hex=e["huella"], aprobador=aprobador.login,
                                    solicitante=e["solicitante"], vigencia_min=self.cfg["aprobaciones"]["vigencia_min"])
        with self.a.tx() as con:
            r = con.execute(update(ejecucion).where(and_(ejecucion.c.id == eid,
                                                         ejecucion.c.estado == "pendiente_aprobacion"))
                            .values(estado="en_cola", aprobador=aprobador.login, token_aprobacion=token,
                                    comentario=comentario, actualizada=utc()))
            if r.rowcount != 1:
                raise EstadoInvalido("la solicitud cambió mientras se aprobaba")
            self._encolar(con, eid, acc)
        self.aud.registrar("ejecucion_aprobada", aprobador.login, ejecucion=eid, solicitante=e["solicitante"],
                           huella=e["huella"], comentario=comentario)
        return self.obtener(eid)

    def rechazar(self, aprobador: Usuario, eid: str, comentario: str) -> dict:
        if len((comentario or "").strip()) < 5:
            raise ErrorValidacion("indica el motivo del rechazo")
        e = self._fila(eid)
        if e["estado"] != "pendiente_aprobacion":
            raise EstadoInvalido(f"la ejecución está en estado {e['estado']}")
        ok, motivo = puede_aprobar(aprobador, e["solicitante"], self.cat[e["accion"]])
        if not ok:
            raise PermisoDenegado(motivo)
        self._marcar(eid, "rechazada", actor=aprobador.login, aprobador=aprobador.login, comentario=comentario)
        return self.obtener(eid)

    def cancelar(self, usuario: Usuario, eid: str) -> dict:
        e = self._fila(eid)
        if e["estado"] not in ("pendiente_aprobacion", "en_cola"):
            raise EstadoInvalido("solo se cancela lo que no ha empezado")
        if usuario.login != e["solicitante"] and not ({"lid", "aut"} & set(usuario.perfiles)):
            raise PermisoDenegado("solo quien la pidió o el líder pueden cancelar")
        self._marcar(eid, "cancelada", actor=usuario.login)
        return self.obtener(eid)

    def _vencida(self, e: dict) -> bool:
        creada = utc(e["creada"]) if e["creada"].tzinfo else e["creada"].replace(tzinfo=utc().tzinfo)
        return utc() - creada > timedelta(minutes=self.cfg["aprobaciones"]["vigencia_min"])

    def vencer_pendientes(self) -> int:
        limite = utc() - timedelta(minutes=self.cfg["aprobaciones"]["vigencia_min"])
        with self.a.tx() as con:
            ids = [r[0] for r in con.execute(select(ejecucion.c.id).where(and_(
                ejecucion.c.estado == "pendiente_aprobacion", ejecucion.c.creada < limite)))]
        for i in ids:
            self._marcar(i, "vencida", actor="sistema")
        return len(ids)

    # ---------------------------------------------------------------- lecturas
    def _fila(self, eid: str) -> dict:
        with self.a.tx() as con:
            f = con.execute(select(ejecucion).where(ejecucion.c.id == eid)).first()
        if not f:
            raise KeyError(f"ejecución {eid} no existe")
        return dict(f._mapping)

    def obtener(self, eid: str) -> dict:
        e = self._fila(eid)
        e.pop("token_aprobacion", None)
        return e

    def visible_para(self, usuario: Usuario, e: dict) -> bool:
        if usuario.total or e["solicitante"] == usuario.login or e.get("aprobador") == usuario.login:
            return True
        if e["accion"] in self.cat:
            return permiso_de(usuario, self.cat[e["accion"]]) is not None
        return "lid" in usuario.perfiles

    def listar(self, usuario: Usuario, *, estado: str | None = None, cambio: str | None = None,
               limite: int = 100) -> list[dict]:
        q = select(ejecucion).order_by(ejecucion.c.creada.desc()).limit(min(limite, 500))
        if estado:
            q = q.where(ejecucion.c.estado == estado)
        if cambio:
            q = q.where(ejecucion.c.cambio == cambio.upper())
        with self.a.tx() as con:
            filas = [dict(f._mapping) for f in con.execute(q)]
        out = []
        for e in filas:
            e.pop("token_aprobacion", None)
            if self.visible_para(usuario, e):
                out.append(e)
        return out

    def pendientes_para(self, aprobador: Usuario) -> list[dict]:
        with self.a.tx() as con:
            filas = [dict(f._mapping) for f in con.execute(select(ejecucion).where(
                ejecucion.c.estado == "pendiente_aprobacion").order_by(ejecucion.c.creada))]
        out = []
        for e in filas:
            e.pop("token_aprobacion", None)
            if puede_aprobar(aprobador, e["solicitante"], self.cat[e["accion"]])[0]:
                out.append(e)
        return out

    def lineas(self, eid: str, desde: int = 0) -> list[dict]:
        with self.a.tx() as con:
            return [dict(f._mapping) for f in con.execute(
                select(linea.c.n, linea.c.ts, linea.c.texto).where(and_(linea.c.ejecucion_id == eid, linea.c.n > desde))
                .order_by(linea.c.n).limit(2000))]

    def _marcar(self, eid: str, estado: str, *, actor: str, **extra) -> None:
        with self.a.tx() as con:
            con.execute(update(ejecucion).where(ejecucion.c.id == eid)
                        .values(estado=estado, actualizada=utc(),
                                terminada=utc() if estado in FINALES else None, **extra))
        self.aud.registrar(f"ejecucion_{estado}", actor, ejecucion=eid, **{k: v for k, v in extra.items()})

    # ================================================================ lado worker
    def iniciar(self, eid: str, worker: str) -> tuple[dict, Accion]:
        """Verificaciones finales antes de tocar un destino. Lanza si algo no cuadra."""
        e = self._fila(eid)
        if e["estado"] != "en_cola":
            raise EstadoInvalido(f"la ejecución {eid} está en {e['estado']}, no se ejecuta")
        acc = self.cat[e["accion"]]
        h = aprobaciones.huella(e["accion"], e["destino"], e["params"], e["cambio"])
        if h != e["huella"]:
            self._fallar_seguridad(eid, worker, "la huella almacenada no coincide con los parámetros")
        requiere = e["clase"] == "C" and (e["ambiente"] in ("PROD", "DRP") or
                                          self.cfg["aprobaciones"]["aprobacion_c_no_prod"])
        if requiere or e["token_aprobacion"]:
            if not e["token_aprobacion"]:
                self._fallar_seguridad(eid, worker, "falta la aprobación")
            clave = self.sec.leer(self.cfg["aprobaciones"]["clave_firma_ref"], motivo=f"verificar {eid}")["clave"]
            try:
                aprobaciones.verificar(clave, e["token_aprobacion"], ejecucion_id=eid, huella_hex=h)
            except aprobaciones.TokenInvalido as ex:
                self._fallar_seguridad(eid, worker, str(ex))
        if e["clase"] in ("O", "C"):
            try:
                with self.a.tx() as con:
                    con.execute(bloqueo_destino.insert().values(destino=e["destino"], ejecucion_id=eid, desde=utc()))
            except IntegrityError:
                raise Conflicto(f"{e['destino']} está ocupado por otro cambio") from None
        with self.a.tx() as con:
            con.execute(update(ejecucion).where(ejecucion.c.id == eid)
                        .values(estado="ejecutando", iniciada=utc(), actualizada=utc()))
        self.aud.registrar("ejecucion_iniciada", worker, ejecucion=eid, accion=e["accion"], destino=e["destino"])
        e["estado"] = "ejecutando"
        return e, acc

    def _fallar_seguridad(self, eid: str, worker: str, motivo: str):
        self.aud.registrar("ejecucion_bloqueada_seguridad", worker, ejecucion=eid, motivo=motivo)
        with self.a.tx() as con:
            con.execute(update(ejecucion).where(ejecucion.c.id == eid).values(
                estado="fallida", error=f"bloqueada por seguridad: {motivo}", terminada=utc(), actualizada=utc()))
        raise PermisoDenegado(motivo)

    def emitir(self, eid: str, texto: str) -> None:
        with self.a.tx() as con:
            n = con.execute(select(func.coalesce(func.max(linea.c.n), 0)).where(linea.c.ejecucion_id == eid)).scalar()
            for i, t in enumerate(str(texto).splitlines() or [""], start=1):
                con.execute(linea.insert().values(ejecucion_id=eid, n=n + i, ts=utc(), texto=t[:4000]))

    def finalizar(self, eid: str, worker: str, *, ok: bool, resultado: Any = None, error: str | None = None) -> dict:
        e = self._fila(eid)
        e.pop("token_aprobacion", None)
        contenido = {"ejecucion": e, "resultado": resultado, "error": error,
                     "lineas": [l["texto"] for l in self.lineas(eid)], "worker": worker,
                     "terminada": utc().isoformat()}
        ruta, sha = self.evid.guardar(eid, contenido)
        estado = "exitosa" if ok else "fallida"
        with self.a.tx() as con:
            con.execute(update(ejecucion).where(ejecucion.c.id == eid).values(
                estado=estado, resultado=_json_seguro(resultado), error=error, evidencia=ruta, evidencia_sha256=sha,
                terminada=utc(), actualizada=utc()))
            con.execute(delete(bloqueo_destino).where(bloqueo_destino.c.ejecucion_id == eid))
        self.aud.registrar(f"ejecucion_{estado}", worker, ejecucion=eid, accion=e["accion"], destino=e["destino"],
                           evidencia=ruta, sha256=sha, error=error)
        return self.obtener(eid)


def _json_seguro(v):
    import json
    try:
        json.dumps(v, default=str)
        return json.loads(json.dumps(v, default=str))
    except (TypeError, ValueError):
        return {"texto": str(v)}
