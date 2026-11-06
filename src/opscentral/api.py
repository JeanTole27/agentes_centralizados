"""API de Ops Central (FastAPI).

La interfaz solo habla con esta API. Lecturas: del almacén (nunca en vivo contra
bases o servidores). Acciones: se validan, se autorizan y se encolan (202);
la salida se sigue por SSE. Errores: 401 sin sesión · 403 sin permiso ·
409 conflicto/estado · 422 validación · 428 falta segundo factor.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from . import VERSION, estado, menu
from .almacen import episodio, evento_zbx, feedback_ml, prediccion, utc
from .autorizacion import PermisoDenegado, permiso_de, puede_ver
from .catalogo import ErrorValidacion
from .config import Config, cargar
from .ejecuciones import Conflicto, EstadoInvalido, RequiereStepUp
from .identidad import SinPerfil, Usuario, construir_usuario, ingreso_dev
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


class Resolucion(BaseModel):
    comentario: str = ""


class IngresoDev(BaseModel):
    login: str


class PedidoRefresco(BaseModel):
    objeto: str


class Feedback(BaseModel):
    valor: str


def crear_app(cfg: Config | None = None, nucleo: Nucleo | None = None) -> FastAPI:
    cfg = cfg or cargar()
    n = nucleo or crear("api", cfg)
    ses = Sesiones(n.almacen, n.auditoria, cfg)
    app = FastAPI(title="Ops Central", version=VERSION, docs_url=None if cfg.es_produccion else "/api/docs",
                  openapi_url=None if cfg.es_produccion else "/api/openapi.json")
    app.state.n, app.state.ses = n, ses
    seguro = cfg.es_produccion
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
                      (EstadoInvalido, 409), (RequiereStepUp, 428), (KeyError, 404)):
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
        return {"ok": True, "version": VERSION, "simulacion": cfg.simulacion, "proveedor_ingreso": cfg["auth"]["proveedor"]}

    @app.post("/api/auth/dev/ingresar")
    def ingresar_dev(datos: IngresoDev, request: Request, response: Response):
        if cfg["auth"]["proveedor"] != "dev" or cfg.es_produccion:
            raise HTTPException(404)
        u = ingreso_dev(datos.login, cfg["auth"]["grupos"], ip=request.client.host if request.client else "")
        _cookie(response, ses.crear(u, "dev"))
        return _yo(u)

    @app.post("/api/auth/dev/segundo-factor")
    def mfa_dev(request: Request, u: Usuario = Depends(usuario)):
        """Simula el segundo factor (solo desarrollo). En OIDC: GET /api/auth/oidc/ingresar?stepup=1."""
        if cfg["auth"]["proveedor"] != "dev" or cfg.es_produccion:
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
                "mfa_reciente": u.mfa_reciente(cfg["auth"]["stepup_vigencia_min"]), "menu": menu.menu(u.perfiles),
                "simulacion": cfg.simulacion}

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
                            "parametros": a.parametros, "bloqueo": a.bloqueo_impl})
        return out

    @app.post("/api/acciones/{accion_id}/evaluar")
    def evaluar(accion_id: str, s: SolicitudEjecucion, u: Usuario = Depends(usuario)):
        acc, p, dest, amb, d = n.ejecuciones.evaluar(u, accion_id, s.params, s.destino)
        return {"accion": acc.id, "destino": dest, "ambiente": amb, "clase": d.clase, "permitido": d.permitido,
                "motivo": d.motivo, "controles": d.controles, "requiere_cambio": d.requiere_cambio,
                "requiere_aprobacion": d.requiere_aprobacion, "requiere_segundo_factor": d.requiere_stepup,
                "parametros": p}

    @app.post("/api/acciones/{accion_id}/ejecuciones", status_code=202)
    def solicitar(accion_id: str, s: SolicitudEjecucion, u: Usuario = Depends(usuario)):
        return n.ejecuciones.solicitar(u, accion_id, s.params, destino=s.destino, cambio=s.cambio, motivo=s.motivo)

    # ============================================================== ejecuciones
    @app.get("/api/ejecuciones")
    def ejecuciones(estado_ejec: str | None = Query(None, alias="estado"), cambio: str | None = None,
                    limite: int = 100, u: Usuario = Depends(usuario)):
        return n.ejecuciones.listar(u, estado=estado_ejec, cambio=cambio, limite=limite)

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
        if ambiente:
            filas = [f for f in filas if (f["datos"] or {}).get("ambiente") == ambiente.upper()]
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

    @app.get("/api/estado/bd/{base}/snapshot/{cambio}")
    def snapshot_cambio(base: str, cambio: str, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "bd.snap.comparar")
        f = estado.leer(n.almacen, f"bd:{base.upper()}:snapshot:{cambio.upper()}")
        if not f:
            raise HTTPException(404, "todavía no hay comparación para ese cambio")
        return f

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
                              refresco_min_s=cfg["bd"]["refresco_min_s"])
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

    # ============================================================== prevención
    @app.get("/api/prevencion/predicciones")
    def predicciones(equipo: str | None = None, u: Usuario = Depends(usuario)):
        _exigir_ver(u, "ml.predicciones")
        with n.almacen.tx() as con:
            filas = [dict(f._mapping) for f in con.execute(
                select(prediccion).where(prediccion.c.vigente.is_(True)).order_by(prediccion.c.calculada.desc()))]
        if equipo == "bd":
            filas = [f for f in filas if f["serie"].startswith(("ts:", "asm:", "fra:"))]
        elif equipo == "cm":
            filas = [f for f in filas if f["serie"].startswith(("fs:", "heap:"))]
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
        return {"servidores": por_tipo["servidor"], "dominios": por_tipo["dominio"], "bases": por_tipo["base"]}

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
                             "detalle": f"pedido por {p['solicitante']} · {p['cambio'] or 'sin cambio'}",
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
    def verificar_auditoria(u: Usuario = Depends(con_perfil("lid", "adm"))):
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
