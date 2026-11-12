"""API de Ops Central (FastAPI).

La interfaz solo habla con esta API. Lecturas: del almacén (nunca en vivo contra
bases o servidores). Acciones: se validan, se autorizan y se encolan (202);
la salida se sigue por SSE. Errores: 401 sin sesión · 403 sin permiso ·
409 conflicto/estado · 422 validación · 428 falta segundo factor.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from . import VERSION, estado, menu
from .almacen import episodio, evento_zbx, feedback_ml, prediccion, utc
from .autorizacion import PermisoDenegado, permiso_de, puede_ver
from .catalogo import ErrorValidacion
from .config import Config, cargar
from .ejecuciones import Conflicto, EstadoInvalido, RequiereStepUp
from .almacen import ejecucion as t_ejecucion, usuario_demo
from .bd.reportes import REPORTES
from .identidad import NOMBRES_PERFIL, PERFILES_DEMO, SinPerfil, Usuario, construir_usuario, ingreso_demo, ingreso_dev
from .asistente import Asistente, PreguntaInvalida
from .inventario_ajustes import CAMPOS as CAMPOS_INV, OPCIONES as OPCIONES_INV, ErrorInventario
from .jira import ErrorJira, JiraNoConfigurado, ServicioCasos
from .limites import pedir_refresco
from .nucleo import Nucleo, crear
from .sesiones import Sesiones

COOKIE = "opsc_sesion"
COOKIE_OIDC = "opsc_oidc"
CABECERA_CSRF = "x-ops-central"


# ------------------------------------------------------------------ modelos
class SolicitudEjecucion(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    destino: str | None = None
    cambio: str | None = None
    motivo: str | None = None


class LoteReporte(BaseModel):
    accion: str
    ambiente: str
    params: dict[str, Any] = Field(default_factory=dict)


class Resolucion(BaseModel):
    comentario: str = ""


class IngresoDev(BaseModel):
    login: str


class RegistroDemo(BaseModel):
    nombre: str
    perfil: str
    login: str = ""


class PedidoRefresco(BaseModel):
    objeto: str


class Feedback(BaseModel):
    valor: str


class ActivoInventario(BaseModel):
    id: str = ""
    datos: dict[str, Any] = Field(default_factory=dict)


class PublicarEvidencia(BaseModel):
    base: str
    grupo: str


class ComentarEjecucion(BaseModel):
    ejecucion_id: str


class Transicion(BaseModel):
    id: str


class PreguntaAsistente(BaseModel):
    pregunta: str
    contexto: dict[str, Any] = Field(default_factory=dict)


class ValoracionAsistente(BaseModel):
    respuesta_id: str
    util: bool
    comentario: str = ""


def crear_app(cfg: Config | None = None, nucleo: Nucleo | None = None) -> FastAPI:
    cfg = cfg or cargar()
    n = nucleo or crear("api", cfg)
    ses = Sesiones(n.almacen, n.auditoria, cfg)
    app = FastAPI(title="Ops Central", version=VERSION, docs_url=None if cfg.es_produccion else "/api/docs",
                  openapi_url=None if cfg.es_produccion else "/api/openapi.json")
    casos = ServicioCasos(cfg, n.secretos, n.inventario, n.catalogo)
    asistente = Asistente(cfg)
    app.state.n, app.state.ses, app.state.casos = n, ses, casos
    seguro = cfg.es_produccion
    sin_auth = cfg["auth"]["proveedor"] in ("dev", "demo") and not cfg.es_produccion
    directa = cfg["aprobaciones"].get("ejecucion_directa", cfg["aprobaciones"].get("dba_ejecuta_directo", True))
    ocultar = frozenset({"aprobaciones"}) if directa else frozenset()
    oidc = None
    if cfg["auth"]["proveedor"] == "oidc":
        from .oidc import ClienteOIDC
        o = cfg["auth"]["oidc"]
        oidc = ClienteOIDC(o["issuer"], o["client_id"], n.secretos.leer(o["client_secret_ref"])["valor"],
                           o["redirect_uri"], claim_grupos=o.get("claim_grupos", "groups"),
                           acr_mfa=o.get("acr_mfa"), clave_estado=secrets.token_urlsafe(32))

    # -------------------------------------------------------------- errores
    def _err(codigo: int):
        async def h(_req: Request, exc: Exception):
            return JSONResponse({"error": type(exc).__name__, "detalle": str(exc)}, status_code=codigo)
        return h

    for tipo, cod in ((ErrorValidacion, 422), (PermisoDenegado, 403), (SinPerfil, 403), (Conflicto, 409),
                      (EstadoInvalido, 409), (RequiereStepUp, 428), (KeyError, 404), (JiraNoConfigurado, 503),
                      (ErrorJira, 502), (PreguntaInvalida, 422), (ErrorInventario, 422)):
        app.add_exception_handler(tipo, _err(cod))

    # -------------------------------------------------------------- seguridad de cada pedido
    @app.middleware("http")
    async def cabeceras(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
                and not request.url.path.startswith("/api/zabbix/") and request.headers.get(CABECERA_CSRF) != "1":
            return JSONResponse({"error": "CSRF", "detalle": f"falta la cabecera {CABECERA_CSRF}: 1"}, status_code=403)
        r = await call_next(request)
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "same-origin"
        return r

    def usuario(request: Request) -> Usuario:
        u = ses.obtener(request.cookies.get(COOKIE))
        if not u:
            raise HTTPException(401, "sesión inexistente o vencida")
        u.ip = request.client.host if request.client else ""
        if n.inv_ajustes:
            n.inv_ajustes.sincronizar()      # otro proceso pudo editar el inventario
        return u

    def con_perfil(*perfiles: str):
        def dep(u: Usuario = Depends(usuario)) -> Usuario:
            if not set(perfiles) & set(u.perfiles):
                raise HTTPException(403, "tu perfil no tiene acceso")
            return u
        return dep

    def _cookie(resp: Response, sid: str) -> None:
        resp.set_cookie(COOKIE, sid, httponly=True, secure=seguro, samesite="strict", path="/",
                        max_age=cfg["auth"]["duracion_max_h"] * 3600)

    # ============================================================== ingreso
    @app.get("/api/salud")
    def salud_api():
        return {"ok": True, "version": VERSION, "proveedor_ingreso": cfg["auth"]["proveedor"],
                "ejecucion_directa": directa, "jira": casos.habilitado}

    # ---- demo: registro con nombre + perfil (no hay autenticación construida todavía)
    @app.get("/api/auth/demo/perfiles")
    def perfiles_demo():
        if not sin_auth:
            raise HTTPException(404)
        return [{"id": p, "nombre": NOMBRES_PERFIL[p]} for p in PERFILES_DEMO]

    @app.get("/api/auth/demo/usuarios")
    def usuarios_demo():
        if not sin_auth:
            raise HTTPException(404)
        with n.almacen.tx() as con:
            filas = con.execute(select(usuario_demo).order_by(usuario_demo.c.ultimo_ingreso.desc()).limit(30)).all()
        return [{"login": f.login, "nombre": f.nombre, "perfil": f.perfil} for f in filas]

    @app.post("/api/auth/demo/registrar")
    def registrar_demo(datos: RegistroDemo, request: Request, response: Response):
        if not sin_auth:
            raise HTTPException(404)
        try:
            u = ingreso_demo(datos.nombre, datos.perfil, datos.login,
                             ip=request.client.host if request.client else "")
        except ValueError as e:
            raise ErrorValidacion(str(e)) from None
        t = utc()
        with n.almacen.tx() as con:
            existe = con.execute(select(usuario_demo.c.login).where(usuario_demo.c.login == u.login)).first()
            if existe:
                con.execute(usuario_demo.update().where(usuario_demo.c.login == u.login)
                            .values(nombre=u.nombre, perfil=datos.perfil, ultimo_ingreso=t))
            else:
                con.execute(usuario_demo.insert().values(login=u.login, nombre=u.nombre, perfil=datos.perfil,
                                                         creado=t, ultimo_ingreso=t))
        n.auditoria.registrar("registro_demo", u.login, nombre=u.nombre, perfil=datos.perfil, nuevo=not existe)
        _cookie(response, ses.crear(u, "demo"))
        return _yo(u)

    @app.post("/api/auth/dev/ingresar")
    def ingresar_dev(datos: IngresoDev, request: Request, response: Response):
        if not sin_auth:
            raise HTTPException(404)
        u = ingreso_dev(datos.login, cfg["auth"]["grupos"], ip=request.client.host if request.client else "")
        _cookie(response, ses.crear(u, "dev"))
        return _yo(u)

    @app.post("/api/auth/dev/segundo-factor")
    def mfa_dev(request: Request, u: Usuario = Depends(usuario)):
        """Simula el segundo factor (solo desarrollo/demo). En OIDC: GET /api/auth/oidc/ingresar?stepup=1."""
        if not sin_auth:
            raise HTTPException(404)
        ses.registrar_mfa(request.cookies[COOKIE], u.login)
        return {"mfa": True, "vigencia_min": cfg["auth"]["stepup_vigencia_min"]}

    @app.get("/api/auth/oidc/ingresar")
    def oidc_ingresar(stepup: bool = False):
        if not oidc:
            raise HTTPException(404)
        url, cookie = oidc.iniciar(stepup=stepup)
        r = RedirectResponse(url, status_code=302)
        r.set_cookie(COOKIE_OIDC, cookie, httponly=True, secure=True, samesite="lax", max_age=600,
                     path="/api/auth/oidc")
        return r

    @app.get("/api/auth/oidc/callback")
    def oidc_callback(code: str, state: str, request: Request):
        if not oidc:
            raise HTTPException(404)
        d = oidc.completar(code, state, request.cookies.get(COOKIE_OIDC))
        r = RedirectResponse("/", status_code=302)
        r.delete_cookie(COOKIE_OIDC, path="/api/auth/oidc")
        actual = request.cookies.get(COOKIE)
        u_actual = ses.obtener(actual)
        if d["stepup"] and u_actual and u_actual.login == d["login"].lower():
            ses.registrar_mfa(actual, u_actual.login)
            return r
        u = construir_usuario(d["login"], d["nombre"], d["grupos"], cfg["auth"]["grupos"], correo=d["correo"],
                              mfa=d["mfa"], ip=request.client.host if request.client else "")
        _cookie(r, ses.crear(u, "oidc"))
        return r

    @app.post("/api/auth/salir")
    def salir(request: Request, response: Response):
        sid = request.cookies.get(COOKIE)
        u = ses.obtener(sid)
        ses.cerrar(sid, u.login if u else "")
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    def _yo(u: Usuario) -> dict:
        return {"login": u.login, "nombre": u.nombre, "perfiles": sorted(u.perfiles), "equipo": u.equipo,
                "mfa_reciente": u.mfa_reciente(cfg["auth"]["stepup_vigencia_min"]),
                "nombres_perfil": [NOMBRES_PERFIL.get(p, p) for p in sorted(u.perfiles)],
                "menu": menu.menu(u.perfiles, ocultar)}

    @app.get("/api/auth/yo")
    def yo(u: Usuario = Depends(usuario)):
        return _yo(u)

    # ============================================================== catálogo y acciones
    @app.get("/api/catalogo")
    def catalogo(u: Usuario = Depends(usuario)):
        out = []
        for a in n.catalogo:
            p = permiso_de(u, a)
            if p:
                out.append({"id": a.id, "nombre": a.nombre, "motor": a.motor, "clase": a.clase,
                            "clase_prod": a.clase_prod, "permiso": p, "estado": a.estado,
                            "parametros": a.parametros, "parametros_libres": a.parametros_libres,
                            "bloqueo": a.bloqueo_impl})
        return out

    # ============================================================== asistente (ventana de chat)
    # Disponible para todos los perfiles. Solo lectura: nunca ejecuta; las acciones que sugiere llevan a
    # la pantalla o al formulario normal de la acción. Mientras no haya motor responde "no_conectado".
    @app.get("/api/asistente/estado")
    def asistente_estado(u: Usuario = Depends(usuario)):
        return asistente.estado(sorted(u.perfiles))

    @app.post("/api/asistente/preguntar")
    def asistente_preguntar(p: PreguntaAsistente, u: Usuario = Depends(usuario)):
        q, ctx = asistente.validar(p.pregunta, p.contexto)
        r = asistente.preguntar(q, ctx, sorted(u.perfiles))
        n.auditoria.registrar("asistente_pregunta", u.login, respuesta=r.id, estado=r.estado, contexto=ctx,
                              pregunta=q[:500])
        return r.a_dict()

    @app.post("/api/asistente/valorar")
    def asistente_valorar(v: ValoracionAsistente, u: Usuario = Depends(usuario)):
        n.auditoria.registrar("asistente_valoracion", u.login, respuesta=v.respuesta_id[:20], util=v.util,
                              comentario=v.comentario[:500])
        return {"ok": True}

    # ============================================================== casos (Jira, opcionales)
    @app.get("/api/casos")
    def buscar_casos(q: str = "", u: Usuario = Depends(usuario)):
        return casos.buscar(q)

    @app.get("/api/casos/{clave}")
    def caso(clave: str, accion: str | None = None, u: Usuario = Depends(usuario)):
        try:
            c = casos.caso(clave, accion)
        except ValueError as e:
            raise ErrorValidacion(str(e)) from None
        n.auditoria.registrar("caso_consultado", u.login, caso=c["clave"], accion=accion)
        return c

    @app.post("/api/casos/{clave}/comentar")
    def caso_comentar(clave: str, p: ComentarEjecucion, u: Usuario = Depends(usuario)):
        """Deja en el caso de Jira el resultado de una ejecución de Ops Central."""
        e = _visible(p.ejecucion_id, u)
        try:
            r = casos.comentar_ejecucion(clave, e, u.login)
        except ValueError as ex:
            raise ErrorValidacion(str(ex)) from None
        n.auditoria.registrar("caso_comentado", u.login, caso=r["clave"], ejecucion=e["id"])
        return r

    @app.post("/api/casos/{clave}/evidencia-snapshot")
    def caso_evidencia(clave: str, p: PublicarEvidencia, u: Usuario = Depends(usuario)):
        """Adjunta al caso el ZIP de evidencia del snapshot y comenta el resultado."""
        _exigir_ver(u, "bd.snap.comparar")
        contenido, nombre, resumen, comp = _evidencia_snapshot(p.base, p.grupo, u)
        rr = (comp or {}).get("resumen") or {}
        if not comp:
            linea = "Todavía sin comparación: se adjuntan los snapshots."
        elif resumen["veredicto"] == "sin_nuevos":
            linea = "Resultado: el cambio no dejó objetos inválidos nuevos."
        else:
            linea = f"Resultado: {rr.get('nuevos', '?')} objeto(s) inválido(s) nuevo(s)."
        parrafos = [f"Ops Central · evidencia de objetos inválidos en {p.base.upper()} ({p.grupo.upper()})", linea,
                    (f"Nuevos {rr.get('nuevos', '-')} · corregidos {rr.get('corregidos', '-')} · persistentes "
                     f"{rr.get('persistentes', '-')} · en línea base {rr.get('en_linea_base', '-')}") if comp else "",
                    f"Adjunto {nombre} (SHA-256 {resumen['sha256_zip']}). Publicado por {u.login}."]
        try:
            r = casos.publicar_evidencia(clave, nombre, contenido, parrafos)
        except ValueError as ex:
            raise ErrorValidacion(str(ex)) from None
        n.auditoria.registrar("caso_evidencia_publicada", u.login, caso=r["clave"], base=p.base.upper(),
                              grupo=p.grupo.upper(), sha256=resumen["sha256_zip"])
        return {**r, "sha256": resumen["sha256_zip"]}

    @app.get("/api/casos/{clave}/transiciones")
    def caso_transiciones(clave: str, u: Usuario = Depends(usuario)):
        try:
            return casos.transiciones(clave)
        except ValueError as ex:
            raise ErrorValidacion(str(ex)) from None

    @app.post("/api/casos/{clave}/transicion")
    def caso_transicion(clave: str, t: Transicion, u: Usuario = Depends(usuario)):
        try:
            r = casos.transicionar(clave, t.id)
        except ValueError as ex:
            raise ErrorValidacion(str(ex)) from None
        n.auditoria.registrar("caso_transicion", u.login, caso=r["clave"], transicion=t.id)
        return r

    @app.post("/api/acciones/{accion_id}/evaluar")
    def evaluar(accion_id: str, s: SolicitudEjecucion, u: Usuario = Depends(usuario)):
        acc, p, dest, amb, d = n.ejecuciones.evaluar(u, accion_id, s.params, s.destino)
        return {"accion": acc.id, "destino": dest, "ambiente": amb, "clase": d.clase, "permitido": d.permitido,
                "motivo": d.motivo, "controles": d.controles, "requiere_cambio": d.requiere_cambio,
                "requiere_aprobacion": d.requiere_aprobacion, "requiere_segundo_factor": d.requiere_stepup,
                "ejecucion_directa": d.directa, "parametros": p}

    @app.post("/api/acciones/{accion_id}/ejecuciones", status_code=202)
    def solicitar(accion_id: str, s: SolicitudEjecucion, u: Usuario = Depends(usuario)):
        return n.ejecuciones.solicitar(u, accion_id, s.params, destino=s.destino, cambio=s.cambio, motivo=s.motivo)

    # ============================================================== ejecuciones
    @app.get("/api/ejecuciones")
    def ejecuciones(estado_ejec: str | None = Query(None, alias="estado"), cambio: str | None = None,
                    ambiente: str | None = None, limite: int = 100, u: Usuario = Depends(usuario)):
        return n.ejecuciones.listar(u, estado=estado_ejec, cambio=cambio, ambiente=ambiente, limite=limite)

    def _visible(eid: str, u: Usuario) -> dict:
        e = n.ejecuciones.obtener(eid)
        if not n.ejecuciones.visible_para(u, e):
            raise HTTPException(404)
        return e

    @app.get("/api/ejecuciones/{eid}")
    def ejecucion(eid: str, u: Usuario = Depends(usuario)):
        return _visible(eid, u)

    @app.get("/api/ejecuciones/{eid}/lineas")
    def lineas(eid: str, desde: int = 0, u: Usuario = Depends(usuario)):
        _visible(eid, u)
        return n.ejecuciones.lineas(eid, desde)

    @app.get("/api/ejecuciones/{eid}/eventos")
    async def eventos(eid: str, request: Request, u: Usuario = Depends(usuario)):
        """SSE: salida en vivo de la ejecución (lee del almacén, no del destino)."""
        _visible(eid, u)

        async def flujo():
            visto = 0
            while not await request.is_disconnected():
                for l in n.ejecuciones.lineas(eid, visto):
                    visto = l["n"]
                    yield f"event: linea\ndata: {json.dumps({'n': l['n'], 'texto': l['texto']}, ensure_ascii=False)}\n\n"
                e = n.ejecuciones.obtener(eid)
                if e["estado"] in ("exitosa", "fallida", "rechazada", "vencida", "cancelada"):
                    yield f"event: fin\ndata: {json.dumps({'estado': e['estado'], 'evidencia': e['evidencia']})}\n\n"
                    return
                yield ": latido\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(flujo(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/ejecuciones/{eid}/cancelar")
    def cancelar(eid: str, u: Usuario = Depends(usuario)):
        return n.ejecuciones.cancelar(u, eid)

    @app.get("/api/ejecuciones/{eid}/evidencia")
    def evidencia(eid: str, u: Usuario = Depends(usuario)):
        e = _visible(eid, u)
        if not e.get("evidencia"):
            raise HTTPException(404, "la ejecución aún no tiene evidencia")
        integra = n.evidencia.verificar(e["evidencia"], e["evidencia_sha256"])
        return {"sha256": e["evidencia_sha256"], "integra": integra, "contenido": n.evidencia.leer(e["evidencia"])}

    # ============================================================== reportes HTML (bd.rep.*)
    @app.get("/api/cm/opciones")
    def cm_opciones(u: Usuario = Depends(usuario)):
        """Servidores, instalaciones y dominios que acepta capa-media-ops, por módulo."""
        from pathlib import Path as _P
        from .cm.opciones import opciones, servidores_desde_json
        from .config import ruta_cmops
        if not any(puede_ver(u, a) for a in n.catalogo if a.motor == "cm"):
            raise HTTPException(403, "tu perfil no tiene acciones de Capa Media")
        r = ruta_cmops(n.cfg)

        def ambiente_de(host: str) -> str:
            a = n.inventario.buscar(host) if host else None
            return a.ambiente if a else ""
        return opciones(r, ambiente_de, servidores_desde_json(n.cfg.ruta("inventario", "archivo")))

    @app.get("/api/reportes/tipos")
    def reportes_tipos(u: Usuario = Depends(usuario)):
        """Los reportes que muestra la pantalla: nombre, skill, alcance y etapas de cada script."""
        from .bd.reportes import tipos
        return tipos()

    @app.get("/api/reportes")
    def reportes_generados(limite: int = 100, ambiente: str | None = None, u: Usuario = Depends(usuario)):
        """Reportes HTML generados (ejecuciones exitosas de bd.rep.* visibles para el usuario)."""
        filtro = [t_ejecucion.c.accion.in_(list(REPORTES)), t_ejecucion.c.estado == "exitosa"]
        if ambiente:
            filtro.append(t_ejecucion.c.ambiente == ambiente.upper())
        with n.almacen.tx() as con:
            filas = [dict(f._mapping) for f in con.execute(
                select(t_ejecucion).where(and_(*filtro))
                .order_by(t_ejecucion.c.creada.desc()).limit(min(max(limite, 1), 500)))]
        out = []
        for e in filas:
            info = (e.get("resultado") or {}).get("reporte")
            if not info or not n.ejecuciones.visible_para(u, e):
                continue
            res = (e.get("resultado") or {}).get("resumen_reporte") or {}
            out.append({"ejecucion": e["id"], "accion": e["accion"], "tipo": REPORTES[e["accion"]][1],
                        "destino": e["destino"], "ambiente": e["ambiente"], "solicitante": e["solicitante"],
                        "creada": e["creada"], "nombre": info["nombre"], "bytes": info["bytes"],
                        "sha256": info["sha256"], "estado": res.get("overall_status"),
                        "muestra": bool((e.get("resultado") or {}).get("muestra"))})
        return out

    # ---------------------------------------------------------- lote: todas las bases de un ambiente
    TERMINALES = {"exitosa", "fallida", "rechazada", "cancelada", "vencida"}

    @app.post("/api/reportes/lote", status_code=202)
    def reportes_lote(s: LoteReporte, u: Usuario = Depends(usuario)):
        """Pide un reporte por base para todas las bases del ambiente (una ejecución por base, cada
        una con sus propios permisos, auditoría y huella). El ZIP se arma luego con /api/reportes/zip."""
        if s.accion not in REPORTES:
            raise ErrorValidacion(f"{s.accion} no es un reporte")
        if "base" not in n.catalogo[s.accion].parametros:
            raise ErrorValidacion(f"{s.accion} no se genera por base: usa el alcance por ambiente")
        amb = (s.ambiente or "").strip().upper()
        bases = sorted(n.inventario.bases(amb), key=lambda b: b.id) if amb else []
        if not bases:
            raise ErrorValidacion(f"el inventario no tiene bases en el ambiente {amb or '(vacío)'}")
        extra = {k: v for k, v in s.params.items() if k not in ("base", "ambiente")}
        out = []
        for b in bases:
            try:
                e = n.ejecuciones.solicitar(u, s.accion, {**extra, "base": b.id})
                out.append({"base": b.id, "ejecucion": e["id"], "estado": e["estado"], "error": None})
            except (RequiereStepUp, PermisoDenegado):
                if not out:
                    raise          # el primero decide: segundo factor o sin permiso → nada se pidió
                out.append({"base": b.id, "ejecucion": None, "estado": "rechazada", "error": "sin permiso o segundo factor vencido"})
            except (ErrorValidacion, Conflicto, KeyError) as ex:
                out.append({"base": b.id, "ejecucion": None, "estado": "rechazada", "error": str(ex)})
        n.auditoria.registrar("reporte_lote_solicitado", u.login, accion=s.accion, ambiente=amb, bases=len(bases),
                              pedidas=sum(1 for x in out if x["ejecucion"]))
        return {"accion": s.accion, "ambiente": amb, "ejecuciones": out}

    def _ids(ids: str) -> list[str]:
        lista = list(dict.fromkeys(x.strip() for x in ids.split(",") if x.strip()))
        if not lista or len(lista) > 500:
            raise ErrorValidacion("indica entre 1 y 500 ejecuciones")
        return lista

    @app.get("/api/reportes/lote/estado")
    def reportes_lote_estado(ids: str, u: Usuario = Depends(usuario)):
        """Estado de las ejecuciones de un lote (lo consulta la pantalla mientras se generan)."""
        out = []
        for eid in _ids(ids):
            try:
                e = _visible(eid, u)
            except (HTTPException, KeyError):
                continue
            out.append({"ejecucion": eid, "base": e["destino"], "estado": e["estado"],
                        "terminal": e["estado"] in TERMINALES, "error": e.get("error"),
                        "reporte": bool((e.get("resultado") or {}).get("reporte"))})
        return out

    @app.get("/api/reportes/zip")
    def reportes_zip(ids: str, u: Usuario = Depends(usuario)):
        """ZIP con los HTML de las ejecuciones indicadas (huella verificada) y un indice.csv con el
        resultado de cada base, incluidas las que fallaron o no generaron reporte."""
        import csv
        import io
        import zipfile
        buf, indice = io.BytesIO(), io.StringIO()
        w = csv.writer(indice)
        w.writerow(["base", "ambiente", "reporte", "estado", "archivo", "sha256", "detalle"])
        raiz = n.cfg.ruta("reportes", "ruta").resolve()
        incluidos, accion, ambs = 0, None, set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for eid in _ids(ids):
                try:
                    e = _visible(eid, u)
                except (HTTPException, KeyError):
                    w.writerow(["", "", "", "no visible", "", "", eid])
                    continue
                if e["accion"] not in REPORTES:
                    continue
                accion = accion or e["accion"]
                ambs.add(e["ambiente"])
                tipo = REPORTES[e["accion"]][1]
                info = (e.get("resultado") or {}).get("reporte")
                if e["estado"] != "exitosa" or not info:
                    w.writerow([e["destino"], e["ambiente"], tipo, e["estado"], "", "", (e.get("error") or "")[:300]])
                    continue
                ruta = (raiz / info["archivo"]).resolve()
                if raiz not in ruta.parents or not ruta.is_file():
                    w.writerow([e["destino"], e["ambiente"], tipo, "sin archivo", "", "", ""])
                    continue
                datos = ruta.read_bytes()
                if hashlib.sha256(datos).hexdigest() != info["sha256"]:
                    w.writerow([e["destino"], e["ambiente"], tipo, "huella inválida", "", info["sha256"], ""])
                    continue
                nombre = f"{e['destino']}__{info['nombre']}"
                z.writestr(nombre, datos)
                incluidos += 1
                w.writerow([e["destino"], e["ambiente"], tipo, "exitosa", nombre, info["sha256"], ""])
            z.writestr("indice.csv", "﻿" + indice.getvalue())
        if not incluidos:
            raise HTTPException(404, "ninguna de las ejecuciones tiene un reporte disponible todavía")
        clave = REPORTES[accion][0] if accion else "reportes"
        amb = "-".join(sorted(ambs)).lower() or "varios"
        nombre_zip = f"reportes_{clave}_{amb}_{utc().strftime('%Y%m%d-%H%M')}.zip"
        n.auditoria.registrar("reporte_lote_descargado", u.login, accion=accion, ambiente=amb, reportes=incluidos)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{nombre_zip}"',
                                 "Cache-Control": "no-store", "X-Reportes-Incluidos": str(incluidos)})

    @app.get("/api/ejecuciones/{eid}/reporte")
    def reporte_html(eid: str, descargar: bool = False, u: Usuario = Depends(usuario)):
        """HTML del reporte. Se verifica la huella antes de servirlo y se entrega aislado
        (CSP sandbox: sin acceso a la sesión ni a la API de Ops Central)."""
        e = _visible(eid, u)
        info = (e.get("resultado") or {}).get("reporte")
        if not info:
            raise HTTPException(404, "la ejecución no generó un reporte HTML")
        raiz = n.cfg.ruta("reportes", "ruta").resolve()
        ruta = (raiz / info["archivo"]).resolve()
        if raiz not in ruta.parents or not ruta.is_file():
            raise HTTPException(404, "el archivo del reporte no está disponible")
        datos = ruta.read_bytes()
        if hashlib.sha256(datos).hexdigest() != info["sha256"]:
            raise HTTPException(409, "el archivo del reporte no coincide con su huella SHA-256")
        cab = {"Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; "
                                          "style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:",
               "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "X-Reporte-SHA256": info["sha256"]}
        if descargar:
            cab["Content-Disposition"] = f'attachment; filename="{info["nombre"]}"'
        return HTMLResponse(datos.decode("utf-8"), headers=cab)

    # ============================================================== aprobaciones
    @app.get("/api/aprobaciones/pendientes")
    def pendientes(u: Usuario = Depends(usuario)):
        return n.ejecuciones.pendientes_para(u)

    @app.post("/api/aprobaciones/{eid}/aprobar")
    def aprobar(eid: str, r: Resolucion, u: Usuario = Depends(usuario)):
        return n.ejecuciones.aprobar(u, eid, r.comentario)

    @app.post("/api/aprobaciones/{eid}/rechazar")
    def rechazar(eid: str, r: Resolucion, u: Usuario = Depends(usuario)):
        return n.ejecuciones.rechazar(u, eid, r.comentario)

    # ============================================================== estado (lo que pintan las pantallas)
    def _exigir_ver(u: Usuario, accion_id: str) -> None:
        if not puede_ver(u, n.catalogo[accion_id]):
            raise HTTPException(403, "tu perfil no tiene acceso a esta información")

    @app.get("/api/estado/bd/salud")
    def salud_flota(ambiente: str | None = None, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "bd.salud.flota")
        filas = estado.leer_tipo(n.almacen, "bd.salud")
        # El ambiente sale del inventario: las filas que solo registraron errores tienen datos = {}
        # y con el filtro por datos.ambiente desaparecían de la vista.
        for f in filas:
            f["ambiente"] = (f["datos"] or {}).get("ambiente") or n.inventario.ambiente_de(f["objeto"].split(":")[1]) or ""
        if ambiente:
            filas = [f for f in filas if f["ambiente"] == ambiente.upper()]
        suyas = _bases_visibles(u)
        if suyas is not None:
            filas = [f for f in filas if f["objeto"].split(":")[1] in suyas]
        return filas

    def _bases_visibles(u: Usuario) -> set[str] | None:
        """None = todas. Capa Media (sin DBA ni Líder) solo ve las bases que usan sus dominios."""
        if not ("cm" in u.perfiles and not ({"dba", "lid"} & set(u.perfiles))):
            return None
        suyas: set[str] = set()
        for s in n.inventario.servidores_de_equipo("cm"):
            suyas |= {c.split(":", 1)[1] for c in n.inventario.relacionados(f"servidor:{s}", 2)
                      if c.startswith("base:")}
        return suyas

    @app.get("/api/estado/bd/{base}/bloqueos")
    def bloqueos_base(base: str, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "bd.ses.bloqueos")
        suyas = _bases_visibles(u)
        if suyas is not None and base.upper() not in suyas:
            raise HTTPException(404, "esa base no la usan los dominios de tu equipo")
        f = estado.leer(n.almacen, f"bd:{base.upper()}:bloqueos")
        if not f:
            raise HTTPException(404, "sin datos todavía: pide un refresco")
        if "cm" in u.perfiles and not ({"dba", "lid"} & set(u.perfiles)):
            from .bd.bloqueos import arbol, filtrar_por_servidores, _aplanar
            filas = [{k: v for k, v in nodo.items() if k not in ("hijos", "nivel")}
                     for a in f["datos"]["arboles"] for nodo in _aplanar(a["arbol"])]
            f["datos"] = arbol(filtrar_por_servidores(filas, n.inventario.servidores_de_equipo("cm")))
            f["filtrado"] = "solo sesiones de servidores de Capa Media"
        return f

    @app.get("/api/estado/bd/{base}/snapshot")
    def snapshot_ultimo(base: str, u: Usuario = Depends(usuario)):
        """Sin referencia: la comparación del último snapshot ANTES de la base."""
        _exigir_ver(u, "bd.snap.comparar")
        from .almacen import snapshot as t_snapshot
        with n.almacen.tx() as con:
            fila = con.execute(select(t_snapshot.c.cambio).where(and_(
                t_snapshot.c.base == base.upper(), t_snapshot.c.momento == "ANTES"))
                .order_by(t_snapshot.c.tomado.desc()).limit(1)).first()
        grupo = fila[0] if fila else None
        if not grupo:
            raise HTTPException(404, "todavía no hay snapshots de esta base")
        f = estado.leer(n.almacen, f"bd:{base.upper()}:snapshot:{grupo}")
        if not f:
            raise HTTPException(404, f"todavía no hay comparación para {grupo}: toma el DESPUÉS y compara")
        return {**f, "grupo": grupo}

    @app.get("/api/estado/bd/{base}/snapshot/{cambio}")
    def snapshot_cambio(base: str, cambio: str, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "bd.snap.comparar")
        f = estado.leer(n.almacen, f"bd:{base.upper()}:snapshot:{cambio.upper()}")
        if not f:
            raise HTTPException(404, "todavía no hay comparación para ese cambio")
        return f

    def _evidencia_snapshot(base: str, grupo: str, u: Usuario):
        from .bd import evidencia_snapshot
        comp = (estado.leer(n.almacen, f"bd:{base.upper()}:snapshot:{grupo.upper()}") or {}).get("datos")
        contenido, nombre, resumen = evidencia_snapshot.paquete(n.almacen, base.upper(), grupo.upper(), comp,
                                                                quien=f"{u.nombre} ({u.login})")
        return contenido, nombre, resumen, comp

    @app.get("/api/estado/bd/{base}/snapshot/{cambio}/evidencia")
    def snapshot_evidencia(base: str, cambio: str, u: Usuario = Depends(usuario)):
        """ZIP de evidencia del grupo de snapshot: HTML legible, JSON de cada snapshot, comparación,
        ejecuciones con su salida y manifiesto SHA-256."""
        _exigir_ver(u, "bd.snap.comparar")
        contenido, nombre, resumen, _ = _evidencia_snapshot(base, cambio, u)
        n.auditoria.registrar("snapshot_evidencia_descargada", u.login, base=base.upper(), grupo=cambio.upper(),
                              sha256=resumen["sha256_zip"])
        return Response(contenido, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store",
                                 "X-Evidencia-Sha256": resumen["sha256_zip"]})

    @app.post("/api/refresco", status_code=202)
    def refrescar(p: PedidoRefresco, u: Usuario = Depends(usuario)):
        if not permiso_de(u, n.catalogo["com.refresco"]) == "ejecuta":
            raise HTTPException(403, "tu perfil no puede pedir refrescos")
        partes = p.objeto.split(":")
        if len(partes) < 2 or partes[0] != "bd" or not n.inventario.base(partes[1]):
            raise ErrorValidacion("objeto de refresco inválido (formato bd:<BASE>)")
        r = pedir_refresco(n.almacen, n.cola, f"bd:{partes[1].upper()}", minimo_s=cfg["bd"]["refresco_min_s"],
                           cola_destino="bd", payload={"base": partes[1].upper(), "motivo": f"persona:{u.login}"})
        n.auditoria.registrar("refresco_pedido", u.login, objeto=p.objeto, estado=r["estado"])
        return r

    # ============================================================== Zabbix
    @app.post("/api/zabbix/webhook", status_code=202)
    async def webhook(request: Request):
        from .zabbix import procesar, webhook as wh
        cuerpo = await request.body()
        secreto = n.secretos.leer(cfg["zabbix"]["secreto_webhook_ref"])["clave"]
        try:
            wh.verificar(secreto, request.headers.get("x-opscentral-tiempo"),
                         request.headers.get("x-opscentral-firma"), cuerpo)
        except wh.FirmaInvalida as e:
            n.auditoria.registrar("webhook_rechazado", "zabbix", motivo=str(e),
                                  ip=request.client.host if request.client else "")
            raise HTTPException(401, "firma inválida") from None
        ev = wh.normalizar(json.loads(cuerpo))
        r = procesar.procesar(n.almacen, n.cola, n.inventario, ev, ventana_min=cfg["zabbix"]["ventana_episodio_min"],
                              refresco_min_s=cfg["bd"]["refresco_min_s"],
                              max_bases=cfg["bd"]["max_bases_por_evento"])
        n.auditoria.registrar("evento_zabbix", "zabbix", eventid=ev.eventid, host=ev.host, episodio=r.get("episodio"),
                              problema=ev.problema)
        return r

    @app.get("/api/alertas/episodios")
    def episodios(estado_ep: str = Query("abierto", alias="estado"), u: Usuario = Depends(usuario)):
        _exigir_ver(u, "zbx.evento.correlacionar")
        with n.almacen.tx() as con:
            eps = [dict(f._mapping) for f in con.execute(
                select(episodio).where(episodio.c.estado == estado_ep).order_by(episodio.c.ultimo_evento.desc()).limit(200))]
            for ep in eps:
                ep["eventos"] = [dict(f._mapping) for f in con.execute(
                    select(evento_zbx.c.eventid, evento_zbx.c.host, evento_zbx.c.nombre, evento_zbx.c.severidad,
                           evento_zbx.c.estado, evento_zbx.c.inicio).where(evento_zbx.c.episodio_id == ep["id"]))]
        return eps

    @app.get("/api/zabbix/estado")
    def zabbix_estado(u: Usuario = Depends(usuario)):
        """Última lectura de la API de Zabbix: respaldo de problemas y trends de capacidad."""
        _exigir_ver(u, "zbx.evento.correlacionar")
        out = {}
        for clave, obj in (("problemas", "zbx:problemas"), ("capacidad", "zbx:capacidad")):
            f = estado.leer(n.almacen, obj)
            out[clave] = None if not f else {"datos": f["datos"], "actualizado": f["actualizado"],
                                             "antiguedad_s": f["antiguedad_s"], "error": f["error"]}
        return out

    # ============================================================== prevención
    @app.get("/api/prevencion/predicciones")
    def predicciones(equipo: str | None = None, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "ml.predicciones")
        with n.almacen.tx() as con:
            filas = [dict(f._mapping) for f in con.execute(
                select(prediccion).where(prediccion.c.vigente.is_(True)).order_by(prediccion.c.calculada.desc()))]
        def equipo_de(serie: str) -> str:
            if serie.startswith(("ts:", "asm:", "fra:")):
                return "bd"
            # fs:/mem:/cpu: vienen de Zabbix: son del equipo dueño del servidor en el inventario
            s = n.inventario.servidor(serie.split(":")[1]) if serie.startswith(("fs:", "mem:", "cpu:")) else None
            return "bd" if s and s.datos.get("equipo") == "dba" else "cm"
        if equipo in ("bd", "cm"):
            filas = [f for f in filas if equipo_de(f["serie"]) == equipo]
        filas.sort(key=lambda f: ((f["datos"].get("umbrales", {}).get("100") or {}).get("dias") or 1e9))
        return {"en_sombra": True, "nota": "En evaluación: aún no se publican en Zabbix", "predicciones": filas}

    @app.post("/api/prevencion/predicciones/{pid}/feedback")
    def feedback(pid: int, f: Feedback, u: Usuario = Depends(usuario)):
        if permiso_de(u, n.catalogo["ml.feedback"]) != "ejecuta":
            raise HTTPException(403)
        if f.valor not in ("util", "falsa_alarma"):
            raise ErrorValidacion("valor debe ser 'util' o 'falsa_alarma'")
        with n.almacen.tx() as con:
            if not con.execute(select(prediccion.c.id).where(prediccion.c.id == pid)).first():
                raise KeyError(pid)
            con.execute(feedback_ml.insert().values(prediccion_id=pid, login=u.login, valor=f.valor, ts=utc()))
        return {"ok": True}

    @app.get("/api/prevencion/serie")
    def serie(serie: str, dias: int = 20, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "ml.predicciones")
        from datetime import timedelta
        from .almacen import serie_punto
        desde = utc() - timedelta(days=min(dias, 400))
        with n.almacen.tx() as con:
            filas = con.execute(select(serie_punto.c.ts, serie_punto.c.valor).where(and_(
                serie_punto.c.serie == serie, serie_punto.c.ts >= desde)).order_by(serie_punto.c.ts)).all()
        return [{"ts": t.isoformat(), "valor": v} for t, v in filas]

    # ============================================================== inventario e inicio
    @app.get("/api/inventario")
    def inventario(u: Usuario = Depends(usuario)):
        _exigir_ver(u, "com.inv.servidores")
        por_tipo: dict[str, list] = {"servidor": [], "dominio": [], "base": []}
        for a in n.inventario.activos.values():
            d = {k: v for k, v in a.datos.items() if k not in ("dsn",)}
            por_tipo[a.tipo].append({"id": a.id, "ambiente": a.ambiente, **d,
                                     "relacionados": sorted(n.inventario.relacionados(a.clave, 1) - {a.clave})})
        return {"servidores": por_tipo["servidor"], "dominios": por_tipo["dominio"], "bases": por_tipo["base"],
                "editable": n.inv_ajustes.puede_editar(u.login) if n.inv_ajustes else False}

    # ---- CRUD del inventario (ajustes sobre el archivo generado). Por ahora: todos los perfiles que ven
    # el inventario ([inventario] editores vacío); luego se restringe a una persona con esa lista.
    def _editor(u: Usuario) -> None:
        _exigir_ver(u, "com.inv.servidores")
        if not n.inv_ajustes.puede_editar(u.login):
            raise HTTPException(403, "no estás en la lista de editores del inventario ([inventario] editores)")

    def _tipo(tipo: str) -> str:
        t = {"servidores": "servidor", "dominios": "dominio", "bases": "base"}.get(tipo, tipo)
        if t not in CAMPOS_INV:
            raise ErrorValidacion("tipo de activo inválido: servidor, dominio o base")
        return t

    @app.get("/api/inventario/esquema")
    def inventario_esquema(u: Usuario = Depends(usuario)):
        _exigir_ver(u, "com.inv.servidores")
        return {"campos": {t: [{"campo": c, "etiqueta": e, "tipo": k, "obligatorio": o} for c, (e, k, o) in cs.items()]
                           for t, cs in CAMPOS_INV.items()},
                "opciones": OPCIONES_INV, "editable": n.inv_ajustes.puede_editar(u.login)}

    @app.get("/api/inventario/ajustes")
    def inventario_ajustes(u: Usuario = Depends(usuario)):
        _exigir_ver(u, "com.inv.servidores")
        return n.inv_ajustes.ajustes()

    @app.post("/api/inventario/vincular-zabbix", status_code=202)
    def inventario_vincular(u: Usuario = Depends(usuario)):
        _editor(u)
        tid = n.cola.encolar("ingesta", "zbx_vincular", {}, clave_unica="plan:zbx_vincular")
        n.auditoria.registrar("inventario_vincular_zabbix", u.login)
        return {"estado": "encolado", "trabajo_id": tid}

    @app.post("/api/inventario/{tipo}", status_code=201)
    def inventario_crear(tipo: str, a: ActivoInventario, u: Usuario = Depends(usuario)):
        _editor(u)
        t = _tipo(tipo)
        d = n.inv_ajustes.crear(t, a.id, a.datos, u.login)
        n.auditoria.registrar("inventario_alta", u.login, tipo=t, id=d["id"], datos=a.datos)
        return d

    @app.put("/api/inventario/{tipo}/{id_activo}")
    def inventario_editar(tipo: str, id_activo: str, a: ActivoInventario, u: Usuario = Depends(usuario)):
        _editor(u)
        t = _tipo(tipo)
        d = n.inv_ajustes.editar(t, id_activo, a.datos, u.login)
        n.auditoria.registrar("inventario_cambio", u.login, tipo=t, id=id_activo, datos=a.datos)
        return d

    @app.delete("/api/inventario/{tipo}/{id_activo}")
    def inventario_baja(tipo: str, id_activo: str, u: Usuario = Depends(usuario)):
        _editor(u)
        t = _tipo(tipo)
        n.inv_ajustes.eliminar(t, id_activo, u.login)
        n.auditoria.registrar("inventario_baja", u.login, tipo=t, id=id_activo)
        return {"ok": True}

    @app.post("/api/inventario/{tipo}/{id_activo}/restaurar")
    def inventario_restaurar(tipo: str, id_activo: str, u: Usuario = Depends(usuario)):
        """Quita el ajuste: el activo vuelve a como viene del archivo generado (o desaparece si era un alta)."""
        _editor(u)
        t = _tipo(tipo)
        if not n.inv_ajustes.deshacer(t, id_activo):
            raise HTTPException(404, "ese activo no tiene ajustes")
        n.auditoria.registrar("inventario_restaurado", u.login, tipo=t, id=id_activo)
        return {"ok": True}

    @app.get("/api/estado/bd/bloqueos")
    def bloqueos_flota(u: Usuario = Depends(usuario)):
        _exigir_ver(u, "bd.ses.flota")
        suyas = _bases_visibles(u)
        out = []
        for f in estado.leer_tipo(n.almacen, "bd.bloqueos"):
            if suyas is not None and f["objeto"].split(":")[1] not in suyas:
                continue
            d = f["datos"] or {}
            out.append({"base": f["objeto"].split(":")[1], "bloqueadas": d.get("total_bloqueadas", 0),
                        "arboles": len(d.get("arboles", [])), "antiguedad_s": f["antiguedad_s"], "error": f["error"]})
        return out

    @app.get("/api/inicio")
    def inicio(u: Usuario = Depends(usuario)):
        """Resumen para el Inicio de cada perfil (01 §5.1). Todo sale del almacén."""
        from datetime import timedelta
        hace24 = utc() - timedelta(hours=24)
        visibles = n.ejecuciones.listar(u, limite=500)
        recientes = [e for e in visibles if (e["creada"] if e["creada"].tzinfo else e["creada"].replace(
            tzinfo=hace24.tzinfo)) >= hace24]
        salud = estado.leer_tipo(n.almacen, "bd.salud") if puede_ver(u, n.catalogo["bd.salud.flota"]) else []
        suyas = _bases_visibles(u)
        if suyas is not None:
            salud = [f for f in salud if f["objeto"].split(":")[1] in suyas]
        sem = {}
        for f in salud:
            s = (f["datos"] or {}).get("semaforo", "sin_dato")
            sem[s] = sem.get(s, 0) + 1
        riesgos = (estado.leer(n.almacen, "ml:riesgos") or {}).get("datos", {}).get("riesgos", [])
        if u.equipo == "dba" and "lid" not in u.perfiles:
            riesgos = [r for r in riesgos if r["serie"].startswith(("ts:", "asm:", "fra:"))]
        elif u.equipo == "cm" and "lid" not in u.perfiles:
            riesgos = [r for r in riesgos if r["serie"].startswith(("fs:", "heap:"))]
        with n.almacen.tx() as con:
            eps = con.execute(select(episodio.c.id).where(episodio.c.estado == "abierto")).all()
        atencion = []
        for p in n.ejecuciones.pendientes_para(u):
            atencion.append({"tipo": "aprobacion", "texto": f"Aprobar {p['accion']} en {p['destino']}",
                             "detalle": f"pedido por {p['solicitante']} · {p['cambio'] or 'sin caso'}",
                             "nivel": "warn", "ir": "aprobaciones", "id": p["id"]})
        for r in riesgos:
            if r["dias_100"] < 7:
                atencion.append({"tipo": "prediccion", "texto": f"{r['serie']} se llena en {r['dias_100']:.1f} días",
                                 "detalle": r["explicacion"], "nivel": "crit" if r["dias_100"] < 3 else "warn",
                                 "ir": "prevencion"})
        for f in salud:
            d = f["datos"] or {}
            if d.get("semaforo") == "rojo":
                atencion.append({"tipo": "salud", "texto": f"{d.get('base')} en rojo",
                                 "detalle": "; ".join(d.get("motivos", [])), "nivel": "crit", "ir": "salud"})
        for e in recientes:
            if e["estado"] == "fallida" and e["solicitante"] == u.login:
                atencion.append({"tipo": "ejecucion", "texto": f"Falló {e['accion']} en {e['destino']}",
                                 "detalle": (e["error"] or "")[:160], "nivel": "crit", "ir": "ejecuciones", "id": e["id"]})
        return {
            "kpis": {"pendientes_aprobacion": sum(1 for a in atencion if a["tipo"] == "aprobacion"),
                     "en_curso": sum(1 for e in visibles if e["estado"] in ("en_cola", "ejecutando")),
                     "fallidas_24h": sum(1 for e in recientes if e["estado"] == "fallida"),
                     "exitosas_24h": sum(1 for e in recientes if e["estado"] == "exitosa"),
                     "episodios_abiertos": len(eps), "riesgos_7d": sum(1 for r in riesgos if r["dias_100"] < 7),
                     "bases": sem},
            "atencion": atencion[:20], "ultimas": visibles[:8],
        }

    # ============================================================== control
    @app.get("/api/auditoria/verificar")
    def verificar_auditoria(u: Usuario = Depends(con_perfil("lid", "adm", "aut"))):
        ok, filas, msg = n.auditoria.verificar()
        return {"integra": ok, "registros": filas, "mensaje": msg}

    # ============================================================== interfaz compilada (apps/web/dist)
    from .config import RAIZ
    dist = RAIZ / "apps" / "web" / "dist"
    if dist.is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{ruta:path}", include_in_schema=False)
        def spa(ruta: str):
            if ruta.startswith("api/"):
                raise HTTPException(404)
            r = dist / ruta
            if ruta and r.is_file() and dist in r.resolve().parents:
                return FileResponse(r)
            return FileResponse(dist / "index.html")

    return app


def app_desde_entorno() -> FastAPI:
    """Para `uvicorn opscentral.api:app_desde_entorno --factory`."""
    return crear_app()
