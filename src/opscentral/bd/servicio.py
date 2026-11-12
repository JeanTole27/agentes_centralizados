"""Motor BD: ejecuta las acciones bd.* del catálogo (worker-bd) y la recolección programada.

Cada acción toma su cuenta según la clase (credenciales.tipo_cuenta_bd):
lectura con OPS_CONSULTA y cambios con OPS_DBA, que solo llama al paquete.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable

from sqlalchemy import and_, select

from .. import estado
from ..almacen import Almacen, prediccion, serie_punto, snapshot as t_snapshot, utc
from ..catalogo import Accion
from ..credenciales import credencial_bd, tipo_cuenta_bd
from ..inventario import Activo, Inventario
from ..limites import Limites
from ..secretos import Secretos
from . import analisis, bloqueos, recompilar, reportes, salud, snapshot
from .conexion import ConexionOracle, ErrorBD

Emitir = Callable[[str], None]
TOPSQL = {"CPU": "topsql_cpu", "ELAPSED": "topsql_elapsed", "GETS": "topsql_gets"}


def _es_error_linea_base(ex: Exception) -> bool:
    """Errores de la tabla de línea base que no deben frenar una comparación:
    ORA-00942 (no existe o sin GRANT) y ORA-00904 (columna inexistente: la tabla tiene otra estructura)."""
    t = str(ex)
    return "ORA-00942" in t or "ORA-00904" in t


class MotorBD:
    clase_conexion = ConexionOracle   # las pruebas lo reemplazan por un doble; el producto siempre usa Oracle

    def __init__(self, cfg, almacen: Almacen, inventario: Inventario, secretos: Secretos, limites: Limites):
        self.cfg, self.a, self.inv, self.sec, self.lim = cfg, almacen, inventario, secretos, limites
        self._lb_cache: dict[str, tuple[float, list[dict]]] = {}   # línea base por base (cambia poco)

    # ------------------------------------------------------------ lecturas compartidas
    def _leer_bloqueos(self, c) -> list[dict]:
        """Sesiones bloqueadas y sus raíces. El nombre del objeto en espera se busca aparte y solo si hay
        bloqueos (lo normal es que no haya: así no se toca DBA_OBJECTS en cada pasada)."""
        filas = self._filtrar_bloqueos(c.consultar("bloqueos"))
        faltan = {int(f["obj_id"]) for f in filas if f.get("obj_id") and int(f["obj_id"]) > 0 and not f.get("objeto")}
        if faltan:
            try:
                nombres = {int(o["object_id"]): o["objeto"] for o in c.consultar("objetos_bloqueo")}
            except Exception:  # noqa: BLE001 — sin el nombre del objeto el árbol igual sirve
                nombres = {}
            for f in filas:
                if not f.get("objeto") and f.get("obj_id"):
                    f["objeto"] = nombres.get(int(f["obj_id"]))
        return filas

    def _filtrar_bloqueos(self, filas: list[dict]) -> list[dict]:
        """Deja solo bloqueos reales: la bloqueada es de usuario y lleva esperando al menos
        [bd] bloqueo_min_espera_s; las raíces que ya no bloquean a nadie se descartan. Marca las sesiones de
        background (no se pueden terminar) y anula el objeto cuando el evento no es TX/TM (row_wait_obj# queda
        con un valor viejo en otros eventos y apuntaba a objetos que no tenían nada que ver)."""
        minimo = int(self.cfg["bd"].get("bloqueo_min_espera_s", 5))
        for f in filas:
            f["background"] = str(f.get("type") or "").upper() == "BACKGROUND" or (
                f.get("username") is None and str(f.get("program") or "").rstrip().endswith(")"))
            ev = str(f.get("event") or "")
            if f.get("blocking_session") is not None and not (ev.startswith("enq: TX") or ev.startswith("enq: TM")):
                f["obj_id"] = None

        def bloqueada(f):
            return (f.get("blocking_session") is not None and not f["background"]
                    and int(f.get("seconds_in_wait") or 0) >= minimo)

        esperan = [f for f in filas if bloqueada(f)]
        quedan = {(int(f.get("inst_id") or 1), int(f["sid"])) for f in esperan}
        padres = {(int(f.get("blocking_instance") or f.get("inst_id") or 1), int(f["blocking_session"]))
                  for f in esperan}
        # una bloqueada que a su vez bloquea a otra se conserva por estar en `esperan`
        return esperan + [f for f in filas if (int(f.get("inst_id") or 1), int(f["sid"])) in padres - quedan]

    def _leer_invalidos(self, c) -> list[dict]:
        """Inválidos sin los esquemas excluidos por configuración ([bd] invalidos_excluir: productos
        instalados que Oracle no marca como propios, PUBLIC, etc.)."""
        return snapshot.excluir(c.consultar("invalidos"), self.cfg["bd"].get("invalidos_excluir") or [])

    def _leer_linea_base(self, base: str, c) -> list[dict]:
        """La línea base cambia poco: se guarda en memoria [bd] linea_base_cache_s (6 h por defecto)."""
        ttl = int(self.cfg["bd"].get("linea_base_cache_s", 21600))
        hit = self._lb_cache.get(base)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        lb = c.consultar("linea_base")
        self._lb_cache[base] = (time.time(), lb)
        return lb

    # ------------------------------------------------------------ conexión
    def _base(self, nombre: str) -> Activo:
        b = self.inv.base(nombre)
        if not b:
            raise ErrorBD(f"la base {nombre} no está en el inventario")
        return b

    @contextmanager
    def conectar(self, base: str, tipo: str, *, persona: str, ejecucion_id: str, motivo: str):
        b = self._base(base)
        cred = credencial_bd(self.sec, self.cfg, b.ambiente, tipo, motivo=motivo)
        with self.lim.turno(b.id):
            with self.clase_conexion(b, cred, persona=persona, ejecucion_id=ejecucion_id,
                                 paquete=self.cfg["bd"]["paquete"], call_timeout_s=self.cfg["bd"]["call_timeout_s"]) as c:
                yield c

    # ------------------------------------------------------------ despacho
    def ejecutar(self, e: dict, acc: Accion, emitir: Emitir):
        p, base, eid, quien = e["params"], e["params"].get("base"), e["id"], e["solicitante"]
        tipo = tipo_cuenta_bd(acc, e["ambiente"])
        motivo = f"{acc.id} {eid}"
        emitir(f"Acción {acc.id} sobre {base or e['destino']} con cuenta "
               f"{self.cfg['bd']['cuenta_dba'] if tipo == 'dba' else self.cfg['bd']['cuenta_consulta']}")

        if acc.id in ("bd.salud.flota", "bd.ses.flota"):
            return self.barrido(p.get("ambiente"), persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id in ("bd.snap.tomar", "bd.snap.comparar", "bd.inv.generar", "bd.inv.recompilar"):
            grupo = self.grupo_snapshot(base, p.get("cambio"), p.get("momento", ""))
            if not p.get("cambio"):
                emitir(f"Sin referencia: el snapshot se agrupa como {grupo}")
        if acc.id == "bd.snap.tomar":
            return self.tomar_snapshot(base, grupo, p["momento"], persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id in ("bd.snap.comparar", "bd.inv.generar"):
            comp = self.comparar(base, grupo, persona=quien, ejecucion_id=eid, emitir=emitir)
            if acc.id == "bd.inv.generar":
                comp["plan"] = recompilar.plan(comp["nuevos"])
                emitir(f"{len(comp['plan'])} sentencia(s) generadas (vista previa; se ejecutan por el paquete)")
            emitir(f"Veredicto: {comp['veredicto']} · {comp['resumen']}")
            return comp
        if acc.id == "bd.inv.recompilar":
            return self.recompilar(base, grupo, persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id == "bd.ses.terminar":
            return self.terminar_sesion(e, emitir)
        ctx = dict(persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id in analisis.POR_ACCION:
            return self.lectura_analizada(acc.id, base, p, **ctx)
        especiales = {
            "bd.perf.topsql": lambda: self.top_sql(base, p.get("criterio", "CPU"), **ctx),
            "bd.ts.crecimiento": lambda: self.crecimiento(base, **ctx),
            "bd.esquema.diff": lambda: self.diff_esquema(base, p["base_destino"], p["esquema"], **ctx),
            "cm.bd_relacionada": lambda: self.bd_relacionada(p["dominio"], **ctx),
        }
        if acc.id in reportes.REPORTES:
            return self.reporte(acc.id, p, **ctx)
        if acc.id in especiales:
            return especiales[acc.id]()
        if acc.plantilla:
            with self.conectar(base, tipo, persona=quien, ejecucion_id=eid, motivo=motivo) as c:
                if acc.plantilla == "bloqueos":
                    filas = self._leer_bloqueos(c)
                elif acc.plantilla == "invalidos":
                    filas = self._leer_invalidos(c)
                elif acc.plantilla == "linea_base":
                    filas = c.consultar("linea_base")
                    self._lb_cache.pop(base, None)
                else:
                    filas = c.consultar(acc.plantilla, p)
            emitir(f"{len(filas)} fila(s)")
            if acc.plantilla == "bloqueos":
                r = bloqueos.arbol(filas)
                estado.guardar(self.a, f"bd:{self.inv.representante(base)}:bloqueos", "bd.bloqueos", r, origen="ejecucion")
                self._actualizar_salud(base, emitir, secciones=["bloqueos"])
                return r
            if acc.plantilla == "invalidos":
                filas = [o for o in filas if not snapshot.es_papelera(o)]
            return {"filas": filas}
        if acc.procedimiento:
            args = {k: ("S" if v is True else "N" if v is False else v) for k, v in p.items()
                    if k not in ("base", "cambio")}
            with self.conectar(base, "dba", persona=quien, ejecucion_id=eid, motivo=motivo) as c:
                r = c.llamar(acc.procedimiento, args)
            emitir(f"{acc.procedimiento} → {r}")
            return {"resultado": r}
        raise ErrorBD(f"{acc.id} no tiene lógica en el motor BD")

    # ------------------------------------------------------------ lecturas con análisis
    def _umbrales(self) -> dict:
        return {**analisis.UMBRALES, **(self.cfg["bd"].get("umbrales_analisis") or {})}

    def _consultar_varias(self, c, plantillas, params: dict, emitir: Emitir) -> tuple[dict, dict]:
        """Corre varias plantillas; el error de una (sin ASM, sin Data Guard, sin licencia AWR...)
        no tumba las demás: queda registrado y se informa."""
        datos, errores = {}, {}
        for pl in plantillas:
            try:
                datos[pl] = c.consultar(pl, params)
                emitir(f"  {pl}: {len(datos[pl])} fila(s)")
            except Exception as ex:  # noqa: BLE001 - se reporta por plantilla
                datos[pl], errores[pl] = [], str(ex)[:300]
                emitir(f"  {pl}: no disponible ({str(ex)[:120]})")
        return datos, errores

    def _resultado(self, datos: dict, an: dict | None, errores: dict, emitir: Emitir, **extra) -> dict:
        hallazgos = list((an or {}).get("hallazgos", []))
        hallazgos += [{"nivel": "info", "texto": f"{pl} no disponible: {err}"} for pl, err in errores.items()]
        v = analisis.veredicto(hallazgos)
        emitir(f"Veredicto: {v} · {sum(1 for h in hallazgos if h['nivel'] != 'info')} hallazgo(s)")
        for h in hallazgos[:20]:
            emitir(f"  [{h['nivel']}] {h['texto']}")
        return {"veredicto": v, "hallazgos": hallazgos, "resumen": (an or {}).get("resumen", {}),
                "datos": datos, "errores": errores, **extra}

    def lectura_analizada(self, accion_id: str, base: str, p: dict, *, persona: str, ejecucion_id: str,
                          emitir: Emitir) -> dict:
        plantillas, fn = analisis.POR_ACCION[accion_id]
        u = self._umbrales()
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo=accion_id) as c:
            datos, errores = self._consultar_varias(c, plantillas, p, emitir)
        if accion_id == "bd.salud.parametros" and p.get("base_comparar"):
            otra = p["base_comparar"]
            if otra == base:
                raise ErrorBD("la base a comparar es la misma")
            with self.conectar(otra, "consulta", persona=persona, ejecucion_id=ejecucion_id,
                               motivo="deriva de parámetros") as c:
                datos_otra, err_otra = self._consultar_varias(c, ("parametros",), {}, emitir)
            errores.update({f"{otra}:{k}": v for k, v in err_otra.items()})
            datos["deriva"] = analisis.deriva_parametros(datos["parametros"], datos_otra["parametros"])
            datos["base_comparar"] = otra
        an = fn(datos, u) if fn else None
        return self._resultado(datos, an, errores, emitir)

    def top_sql(self, base: str, criterio: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        pl = TOPSQL[criterio]
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="top SQL") as c:
            datos, errores = self._consultar_varias(c, (pl,), {}, emitir)
        return self._resultado(datos, None, errores, emitir, criterio=criterio)

    def crecimiento(self, base: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        """Uso actual + crecimiento de 30 días (series del almacén) + predicción vigente del modelo.
        No usa DBA_HIST_TBSPC_SPACE_USAGE: así no depende de la licencia de Diagnostics Pack."""
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="crecimiento") as c:
            ts = c.consultar("tablespaces")
        desde = utc() - timedelta(days=30)
        filas, hallazgos = [], []
        with self.a.tx() as con:
            for t in ts:
                serie = f"ts:{base}:{t['tablespace_name']}"
                pts = con.execute(select(serie_punto.c.ts, serie_punto.c.valor).where(and_(
                    serie_punto.c.serie == serie, serie_punto.c.ts >= desde)).order_by(serie_punto.c.ts)).all()
                pred = con.execute(select(prediccion.c.datos).where(and_(
                    prediccion.c.serie == serie, prediccion.c.vigente.is_(True)))).scalar()
                dias_hist = (pts[-1][0] - pts[0][0]).total_seconds() / 86400 if len(pts) > 1 else 0
                por_dia = round((pts[-1][1] - pts[0][1]) / dias_hist, 3) if dias_hist >= 1 else None
                d100 = ((pred or {}).get("umbrales", {}).get("100") or {}).get("dias")
                filas.append({"tablespace": t["tablespace_name"], "pct_usado": t["pct_usado"],
                              "usado_mb": t["usado_mb"], "maximo_mb": t["maximo_mb"],
                              "crecimiento_pct_dia": por_dia, "dias_historia": round(dias_hist, 1),
                              "dias_hasta_100": d100, "confianza": (pred or {}).get("confianza")})
                if d100 is not None and d100 < 7:
                    hallazgos.append({"nivel": "rojo", "texto": f"{t['tablespace_name']} se llena en {d100:.1f} días"})
                elif d100 is not None and d100 < 30:
                    hallazgos.append({"nivel": "amarillo", "texto": f"{t['tablespace_name']} se llena en {d100:.0f} días"})
        if not any(f["dias_historia"] for f in filas):
            hallazgos.append({"nivel": "info", "texto": "Aún no hay historia: la recolección guarda un punto por hora"})
        filas.sort(key=lambda f: f["dias_hasta_100"] if f["dias_hasta_100"] is not None else 1e9)
        return self._resultado({"tablespaces": filas},
                               {"hallazgos": hallazgos, "resumen": {"tablespaces": len(filas)}}, {}, emitir)

    def diff_esquema(self, base: str, destino: str, esquema: str, *, persona: str, ejecucion_id: str,
                     emitir: Emitir) -> dict:
        if base == destino:
            raise ErrorBD("la base de origen y la de destino son la misma")
        listas = {}
        for b in (base, destino):
            with self.conectar(b, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="diff de esquema") as c:
                listas[b] = c.consultar("objetos_esquema", {"esquema": esquema})
            emitir(f"{b}: {len(listas[b])} objeto(s) en {esquema}")
        d = analisis.diff_esquema(listas[base], listas[destino])
        datos = {k: d.pop(k) for k in ("solo_en_origen", "solo_en_destino", "estado_distinto")}
        return self._resultado(datos, d, {}, emitir, origen=base, destino=destino, esquema=esquema)

    def bd_relacionada(self, dominio: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        """Bases que usa un dominio (relaciones del inventario) con su salud, bloqueos y las sesiones
        que abre la aplicación desde el servidor del dominio."""
        dom = self.inv.activos.get(f"dominio:{dominio}")
        if not dom:
            raise ErrorBD(f"el dominio {dominio} no está en el inventario")
        bases = sorted(k.split(":", 1)[1] for k in self.inv.relacionados(dom.clave, saltos=1) if k.startswith("base:"))
        if not bases:
            raise ErrorBD(f"el inventario no tiene relaciones dominio → base para {dominio}")
        servidores = dom.datos.get("servidores") or [dom.datos.get("servidor", "")]
        servidor = ", ".join(servidores)
        filas, hallazgos = [], []
        for b in bases:
            fila = {"base": b, "salud": ((estado.leer(self.a, f"bd:{b}:salud") or {}).get("datos") or {}).get("semaforo")}
            try:
                with self.conectar(b, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="bd del dominio") as c:
                    fila["sesiones_app"] = [f for srv in servidores
                                            for f in c.consultar("sesiones_por_maquina", {"maquina": srv})]
                    fila["bloqueos"] = bloqueos.arbol(self._leer_bloqueos(c))["total_bloqueadas"]
            except Exception as ex:  # noqa: BLE001
                fila["error"] = str(ex)[:300]
                hallazgos.append({"nivel": "rojo", "texto": f"{b}: sin respuesta ({str(ex)[:120]})"})
            if fila.get("bloqueos"):
                hallazgos.append({"nivel": "amarillo", "texto": f"{b}: {fila['bloqueos']} sesión(es) bloqueada(s)"})
            if fila["salud"] == "rojo":
                hallazgos.append({"nivel": "rojo", "texto": f"{b}: salud en rojo"})
            emitir(f"{b}: salud {fila['salud'] or 'sin dato'}, bloqueadas {fila.get('bloqueos', '?')}")
            filas.append(fila)
        return self._resultado({"bases": filas}, {"hallazgos": hallazgos, "resumen": {"bases": len(filas)}}, {},
                               emitir, dominio=dominio, servidor=servidor)

    # ------------------------------------------------------------ reportes
    def hoja_vida(self, base: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        b = self._base(base)
        pl = ("salud_instancia", "dg_rol", "parametros", "tablespaces", "diskgroups", "fra_uso", "parches",
              "rman_7d", "usuarios_por_expirar")
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="hoja de vida") as c:
            datos, errores = self._consultar_varias(c, pl, {}, emitir)
        clave = {"sga_target", "pga_aggregate_target", "memory_target", "processes", "sessions", "db_block_size",
                 "compatible", "db_recovery_file_dest_size", "undo_retention", "cluster_database", "audit_trail"}
        dominios = sorted(k.split(":", 1)[1] for k in self.inv.relacionados(b.clave, saltos=1)
                          if k.startswith("dominio:"))
        respaldo = analisis.rman({"rman_7d": datos["rman_7d"]}, self._umbrales())
        hoja = {"base": base, "ambiente": b.ambiente, "criticidad": b.datos.get("criticidad"),
                "servidor": b.datos.get("servidor"), "dsn": b.datos.get("dsn"), "dominios": dominios,
                "instancia": (datos["salud_instancia"] or [None])[0], "rol": (datos["dg_rol"] or [None])[0],
                "parametros_clave": {f["name"]: f["value"] for f in datos["parametros"] if f["name"] in clave},
                "tablespaces": datos["tablespaces"], "diskgroups": datos["diskgroups"],
                "fra": (datos["fra_uso"] or [None])[0], "ultimo_parche": (datos["parches"] or [None])[0],
                "respaldos": respaldo["resumen"], "cuentas_por_expirar": datos["usuarios_por_expirar"]}
        return self._resultado({"hoja_vida": hoja}, respaldo, errores, emitir)

    # hallazgo → acción del catálogo que lo atiende (plan de acción del análisis)
    SUGERENCIAS = [("tablespace", "bd.ts.agregar_df"), ("diskgroup", "bd.asm.diskgroups"),
                   ("FRA", "bd.salud.fra"), ("backup", "bd.salud.rman"), ("Job", "bd.job.reintentar"),
                   ("estadísticas", "bd.stats.recolectar"), ("bloquead", "bd.ses.bloqueos"),
                   ("transacción abierta", "bd.ses.idle_tx"), ("ORA-01555", "bd.salud.undo_temp"),
                   ("ORA-", "bd.salud.alertlog"), ("inválidos", "bd.inv.listar")]

    def analisis_base(self, base: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        """Análisis y plan de acción: corre los chequeos, ordena los hallazgos (Top-N) y sugiere la acción."""
        u = self._umbrales()
        chequeos = ["bd.salud.fra", "bd.salud.rman", "bd.salud.jobs", "bd.salud.undo_temp", "bd.salud.alertlog",
                    "bd.ses.idle_tx", "bd.stats.obsoletas"]
        pl = ["salud_instancia", "tablespaces", "diskgroups", "bloqueos", "invalidos", "linea_base"]
        for ch in chequeos:
            pl += list(analisis.POR_ACCION[ch][0])
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="análisis") as c:
            datos, errores = self._consultar_varias(c, pl, {"min_segundos": u["idle_tx_amarillo_s"]}, emitir)
        datos["invalidos"] = snapshot.excluir(datos["invalidos"], self.cfg["bd"].get("invalidos_excluir") or [])
        lbk = {snapshot.clave(o) for o in datos["linea_base"]}
        fuera = [o for o in datos["invalidos"] if snapshot.clave(o) not in lbk and not snapshot.es_papelera(o)]
        sem = salud.evaluar((datos["salud_instancia"] or [None])[0], datos["tablespaces"], datos["diskgroups"],
                            bloqueadas=bloqueos.arbol(datos["bloqueos"])["total_bloqueadas"],
                            invalidos_nuevos=len(fuera))
        hallazgos = [{"nivel": "rojo" if sem["semaforo"] == "rojo" else "amarillo", "texto": m}
                     for m in sem["motivos"]]
        for ch in chequeos:
            hallazgos += analisis.POR_ACCION[ch][1](datos, u)["hallazgos"]
        hallazgos.sort(key=lambda h: -analisis.NIVEL[h["nivel"]])
        for h in hallazgos:
            h["accion"] = next((a for palabra, a in self.SUGERENCIAS if palabra.lower() in h["texto"].lower()), None)
        top = [h for h in hallazgos if h["nivel"] != "info"][:10]
        return self._resultado({"plan_de_accion": top},
                               {"hallazgos": hallazgos, "resumen": {"semaforo_salud": sem["semaforo"],
                                                                    "hallazgos": len(top)}}, errores, emitir)

    def sql_costosas(self, base: str, criterio: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="SQL costosas") as c:
            top = c.consultar(TOPSQL[criterio])
            detalle = []
            for f in top[:5]:
                t = (c.consultar("sql_texto", {"sql_id": f["sql_id"]}) or [{}])[0]
                detalle.append({**f, "sql_fulltext": t.get("sql_fulltext")})
        emitir(f"{len(top)} SQL por {criterio}; detalle completo de las 5 primeras")
        return self._resultado({"top": top, "detalle": detalle}, None, {}, emitir, criterio=criterio)

    # ------------------------------------------------------------ reportes HTML (bd/reportes, sin LLM)
    @contextmanager
    def _cliente_reporte(self, base: str, *, persona: str, ejecucion_id: str, motivo: str):
        """Cliente para los scripts de reporte: la conexión OPS_CONSULTA (cada SQL pasa por la
        política de solo lectura) o, solo en demo sin Oracle, el cliente de datos de muestra."""
        if self.cfg["bd"].get("reportes_muestra"):
            from .reportes.muestra import ClienteMuestra
            yield ClienteMuestra(base)
            return
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo=motivo) as c:
            yield reportes.ClienteOps(c)

    def _entrada(self, b: Activo, modulo: str) -> dict:
        return {"alias": b.id, "dsn": b.datos.get("dsn") or "", "environment": b.ambiente.lower(),
                "servidor": b.datos.get("servidor"), "herramienta": f"Ops Central · bd/reportes/{modulo}.py"}

    def _guardar_reporte(self, ejecucion_id: str, nombre: str, contenido: str, tipo: str) -> dict:
        datos = contenido.encode("utf-8")
        raiz = self.cfg.ruta("reportes", "ruta")
        carpeta = raiz / ejecucion_id[:8]
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"{ejecucion_id}--{nombre}"
        if ruta.exists():
            raise FileExistsError(f"el reporte de {ejecucion_id} ya existe y no se sobrescribe")
        ruta.write_bytes(datos)
        return {"archivo": ruta.relative_to(raiz).as_posix(), "nombre": nombre, "tipo": tipo,
                "sha256": hashlib.sha256(datos).hexdigest(), "bytes": len(datos)}

    def reporte(self, accion_id: str, p: dict, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        """Genera el HTML del reporte con el script correspondiente y lo guarda con su huella.
        Con Oracle, además devuelve el resultado estructurado de siempre (hallazgos, datos)."""
        from .reportes import analisis as r_analisis, diskgroups as r_dg, hoja_vida as r_hv, sentencias as r_sen
        from .reportes.muestra import AVISO_HTML
        modulo, etiqueta = reportes.REPORTES[accion_id]
        muestra = bool(self.cfg["bd"].get("reportes_muestra"))
        ctx = dict(persona=persona, ejecucion_id=ejecucion_id, emitir=emitir)
        abrir = dict(persona=persona, ejecucion_id=ejecucion_id, motivo=f"reporte {etiqueta}")
        if muestra:
            emitir("DATOS DE MUESTRA: bd.reportes_muestra está activo; no se consulta Oracle")
        estructurado: dict | None = None
        etapas = {"hoja_vida": r_hv, "analisis": r_analisis, "diskgroups": r_dg, "sentencias": r_sen}[modulo].ETAPAS

        def progreso(etapa: str) -> None:  # la pantalla de Reportes marca las etapas con estas líneas
            emitir(f"Etapa {etapas.index(etapa) + 1}/{len(etapas)}: {etapa}")
        if accion_id == "bd.rep.diskgroups":
            amb = (p.get("ambiente") or "").upper()
            umbral = float(p.get("umbral") or r_dg.UMBRAL_DEFECTO)
            bases = [b for b in self.inv.bases() if b.ambiente == amb]
            if not bases:
                raise ErrorBD(f"el inventario no tiene bases en {amb}")
            emitir(f"{len(bases)} base(s) en {amb}; una consulta por servidor (umbral {umbral:g} %)")
            nombre, html_txt, res = r_dg.construir(
                [self._entrada(b, modulo) for b in bases],
                lambda e: self._cliente_reporte(e["alias"], **abrir), amb, umbral,
                herramienta=f"Ops Central · bd/reportes/{modulo}.py", progreso=progreso)
        else:
            base = p["base"]
            b = self._base(base)
            if not muestra:
                if accion_id == "bd.rep.hoja_vida":
                    estructurado = self.hoja_vida(base, **ctx)
                elif accion_id == "bd.rep.analisis":
                    estructurado = self.analisis_base(base, **ctx)
                else:
                    estructurado = self.sql_costosas(base, p.get("criterio", "ELAPSED"), **ctx)
            emitir(f"Generando el HTML de {etiqueta} con el script {modulo}.py")
            with self._cliente_reporte(base, **abrir) as cl:
                if accion_id == "bd.rep.hoja_vida":
                    nombre, html_txt, res = r_hv.construir(cl, base, self._entrada(b, modulo), progreso=progreso)
                elif accion_id == "bd.rep.analisis":
                    nombre, html_txt, res = r_analisis.construir(cl, base, self._entrada(b, modulo),
                                                                 progreso=progreso)
                else:
                    nombre, html_txt, res = r_sen.construir(cl, base, self._entrada(b, modulo),
                                                            criterio=p.get("criterio", "ELAPSED"),
                                                            perfil=self.cfg["bd"]["cuenta_consulta"],
                                                            progreso=progreso)
        if muestra:
            html_txt = html_txt.replace("<body>", "<body>" + AVISO_HTML, 1)
        info = self._guardar_reporte(ejecucion_id, nombre, html_txt, modulo)
        emitir(f"Reporte {nombre} · {info['bytes']:,} bytes · SHA-256 {info['sha256'][:16]}… · "
               f"estado {res.get('overall_status')}")
        for nc in res.get("not_collected") or []:
            emitir(f"  sección no recolectada: {nc}")
        if estructurado is None:
            nivel = {"CRITICAL": "rojo", "WARNING": "amarillo"}.get(res.get("overall_status"), None)
            hallazgos = [{"nivel": nivel, "texto": f"{etiqueta}: estado {res.get('overall_status')}"}] if nivel else []
            estructurado = self._resultado({}, {"hallazgos": hallazgos, "resumen": res}, {}, emitir)
        return {**estructurado, "reporte": info, "resumen_reporte": res, "muestra": muestra}

    # ------------------------------------------------------------ snapshot y recompilación
    def tomar_snapshot(self, base: str, cambio: str, momento: str, *, persona: str, ejecucion_id: str,
                       emitir: Emitir) -> dict:
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id,
                           motivo=f"snapshot {momento}") as c:
            inv = self._leer_invalidos(c)
            ts = c.consultar("tablespaces")
        s = snapshot.armar(base, cambio, momento, inv, {"tablespaces": ts})
        with self.a.tx() as con:
            con.execute(t_snapshot.insert().values(id=uuid.uuid4().hex, base=base, cambio=cambio, momento=momento,
                                                   tomado=utc(), datos=s, huella=s["huella"],
                                                   ejecucion_id=ejecucion_id))
        emitir(f"Snapshot {momento}: {s['total']} objeto(s) inválido(s)")
        return {"momento": momento, "total": s["total"], "huella": s["huella"]}

    def grupo_snapshot(self, base: str, cambio: str | None, momento: str = "") -> str:
        """La referencia agrupa ANTES / DESPUÉS / FINAL. Es opcional: sin ella, un ANTES abre un
        grupo automático (SNAP-AAAAMMDD-HHMMSS) y lo demás usa el último ANTES de esa base."""
        if cambio:
            return cambio.upper()
        if momento == "ANTES":
            return "SNAP-" + utc().strftime("%Y%m%d-%H%M%S")
        g = self.ultimo_grupo(base)
        if not g:
            raise ErrorBD(f"no hay un snapshot ANTES de {base}: tómalo primero (la referencia es opcional)")
        return g

    def ultimo_grupo(self, base: str) -> str | None:
        with self.a.tx() as con:
            f = con.execute(select(t_snapshot.c.cambio).where(and_(
                t_snapshot.c.base == base, t_snapshot.c.momento == "ANTES"))
                .order_by(t_snapshot.c.tomado.desc()).limit(1)).first()
        return f[0] if f else None

    def _ultimo(self, base: str, cambio: str, momentos: tuple[str, ...]) -> dict | None:
        with self.a.tx() as con:
            f = con.execute(select(t_snapshot.c.datos).where(and_(
                t_snapshot.c.base == base, t_snapshot.c.cambio == cambio, t_snapshot.c.momento.in_(momentos)))
                .order_by(t_snapshot.c.tomado.desc()).limit(1)).first()
        return f[0] if f else None

    def comparar(self, base: str, cambio: str, *, persona: str, ejecucion_id: str,
                 emitir: Emitir | None = None) -> dict:
        """ANTES contra DESPUÉS/FINAL, sin contar la línea base. Si la línea base no se puede leer
        (tabla ausente, sin GRANT o con otra estructura) la comparación sigue sin ella y lo avisa:
        los «nuevos» salen de ANTES vs DESPUÉS, así que lo ya inválido antes del cambio no entra igual."""
        antes = self._ultimo(base, cambio, ("ANTES",))
        despues = self._ultimo(base, cambio, ("FINAL", "DESPUES"))
        if not antes or not despues:
            raise ErrorBD(f"faltan snapshots para {cambio} en {base}: se necesitan ANTES y DESPUÉS")
        aviso = None
        try:
            with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="línea base") as c:
                lb = self._leer_linea_base(base, c)
        except Exception as ex:  # noqa: BLE001
            if not _es_error_linea_base(ex):
                raise
            lb = []
            aviso = ("Línea base de inválidos no disponible (ADMINBD.TBL_BASE_OBJETOS_INVALIDOS): "
                     f"{str(ex).splitlines()[0][:200]}. Se compara solo ANTES contra DESPUÉS.")
            if emitir:
                emitir("Advertencia: " + aviso)
        comp = snapshot.comparar(antes, despues, lb)
        comp["linea_base_disponible"] = aviso is None
        if aviso:
            comp["aviso_linea_base"] = aviso
        estado.guardar(self.a, f"bd:{base}:snapshot:{cambio}", "bd.snapshot", comp, origen="ejecucion")
        return comp

    def recompilar(self, base: str, cambio: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        comp = self.comparar(base, cambio, persona=persona, ejecucion_id=ejecucion_id, emitir=emitir)
        if not comp["nuevos"]:
            emitir("No hay objetos nuevos por el cambio: nada que recompilar")
            return {"comparacion": comp, "recompilacion": None}
        emitir(f"{len(comp['nuevos'])} objeto(s) nuevo(s); se excluyen {comp['resumen']['en_linea_base']} de la línea base "
               f"y {comp['resumen']['papelera']} de la papelera")
        with self.conectar(base, "dba", persona=persona, ejecucion_id=ejecucion_id, motivo=f"recompilar {cambio}") as c:
            r = recompilar.ejecutar(c, comp["nuevos"], emitir=emitir)
        emitir("Snapshot FINAL automático")
        self.tomar_snapshot(base, cambio, "FINAL", persona=persona, ejecucion_id=ejecucion_id, emitir=emitir)
        final = self.comparar(base, cambio, persona=persona, ejecucion_id=ejecucion_id, emitir=emitir)
        self._actualizar_salud(base, emitir, secciones=["invalidos"])
        return {"recompilacion": r, "comparacion_final": final}

    def _actualizar_salud(self, base: str, emitir: Emitir, secciones: list[str] | None = None) -> None:
        """Tras una acción que cambia lo que muestra Salud (bloqueos, inválidos), se relee SOLO esa
        sección de la base para que el semáforo no quede con el dato anterior."""
        r = self.recolectar_base(base, origen="ejecucion", secciones=secciones)
        if r.get("error"):
            emitir(f"Salud no se pudo actualizar: {r['error']} (se actualizará en la próxima recolección)")
        else:
            emitir(f"Salud de {base} actualizada: {r['semaforo']}")

    # ------------------------------------------------------------ sesiones
    def terminar_sesion(self, e: dict, emitir: Emitir) -> dict:
        p, base = e["params"], e["params"]["base"]
        with self.conectar(base, "consulta", persona=e["solicitante"], ejecucion_id=e["id"], motivo="verificar raíz") as c:
            antes = bloqueos.arbol(self._leer_bloqueos(c))
        raices = {(a["raiz"]["inst_id"], a["raiz"]["sid"]) for a in antes["arboles"]}
        objetivo = (p.get("inst_id", 1), p["sid"])
        nodo = next((x for a in antes["arboles"] for x in bloqueos._aplanar(a["arbol"])
                     if (x.get("inst_id"), x.get("sid")) == objetivo), None)
        if nodo and nodo.get("background"):
            # LGWR, SMON, PMON...: terminarlos tumba la instancia. Nunca se hace desde Ops Central.
            raise ValueError(f"la sesión {p['sid']} es un proceso de background de Oracle "
                             f"({nodo.get('program') or 'sin programa'}): no se termina desde Ops Central")
        if objetivo not in raices:
            emitir("Advertencia: la sesión ya no es raíz de un bloqueo; el paquete validará antes de terminar")
        with self.conectar(base, "dba", persona=e["solicitante"], ejecucion_id=e["id"], motivo="terminar sesión") as c:
            r = c.llamar("TERMINAR_SESION", {"sid": p["sid"], "serial": p["serial"], "inst_id": p.get("inst_id", 1),
                                             "motivo": p["motivo"]})
        emitir(f"TERMINAR_SESION → {r}")
        with self.conectar(base, "consulta", persona=e["solicitante"], ejecucion_id=e["id"], motivo="verificar") as c:
            despues = bloqueos.arbol(self._leer_bloqueos(c))
        estado.guardar(self.a, f"bd:{self.inv.representante(base)}:bloqueos", "bd.bloqueos", despues, origen="ejecucion")
        sigue = any((a["raiz"]["inst_id"], a["raiz"]["sid"]) == objetivo for a in despues["arboles"])
        emitir("El bloqueo desapareció" if not sigue else "El bloqueo continúa: revisar")
        self._actualizar_salud(base, emitir, secciones=["bloqueos"])
        return {"resultado": r, "bloqueo_resuelto": not sigue, "bloqueadas_antes": antes["total_bloqueadas"],
                "bloqueadas_despues": despues["total_bloqueadas"]}

    # ------------------------------------------------------------ recolección
    # Secciones que se pueden perder sin descartar la pasada: si fallan se anota el motivo y se sigue.
    # La instancia (salud_instancia) es obligatoria y barata: sin ella no hay semáforo.
    # Ya NO se leen todas cada 15 min (era pesado para 180+ bases):
    #   - periódica liviana ([bd] secciones_periodicas, cada [bd] recoleccion_periodica_s): espacio;
    #   - por alerta de Zabbix: solo la sección que indica la alerta (bloqueos, tablespaces, inválidos…);
    #   - diaria completa ([bd] recoleccion_completa_s) y a pedido (Refrescar, o después de una acción).
    # Las secciones que no se releen conservan el último valor guardado, con su fecha en `leidas`.
    SECCIONES_OPCIONALES = {
        "tablespaces": "tablespaces", "diskgroups": "ASM", "invalidos": "objetos inválidos",
        "linea_base": "línea base de inválidos", "bloqueos": "bloqueos",
        "fra": "FRA", "respaldo": "respaldos RMAN", "dataguard": "Data Guard",
    }
    # Secciones de protección: se guardan juntas en datos["proteccion"] y entran al semáforo.
    PROTECCION = ("fra", "respaldo", "dataguard")
    # Sin estas no es una falla de la pasada (base sin standby, sin ASM).
    SIN_AVISO = ("diskgroups", "dataguard")

    def _umbrales_salud(self) -> dict:
        return {**salud.UMBRALES, **(self.cfg["bd"].get("umbrales_salud") or {})}

    def _exige_respaldo(self, ambiente: str | None) -> bool:
        return str(ambiente or "").upper() in {a.upper() for a in self.cfg["bd"].get("respaldo_ambientes") or []}

    def _leer_dataguard(self, c, inst: dict | None) -> dict:
        """Primaria: estado de los destinos standby (error, gap). Standby: desfase de transporte y aplicación."""
        rol = str((inst or {}).get("database_role") or "").upper()
        r: dict = {"rol": rol or None, "destinos": [], "lag": {}}
        if "STANDBY" in rol:
            for f in c.consultar("dg_stats"):
                if f.get("name") in ("transport lag", "apply lag"):
                    r["lag"][f["name"]] = f.get("value")
        else:
            r["destinos"] = c.consultar("dg_destinos")
        return r

    def recolectar_base(self, base: str, *, origen: str = "recoleccion", secciones: list[str] | None = None,
                        refrescar_representante: bool = True) -> dict:
        """Recolecta una base. Si es un alias más de una base RAC (mismo servicio y ambiente que otro alias),
        las secciones de toda la base (GV$/DBA_: espacio, inválidos, bloqueos) se leen UNA vez en el
        representante y este alias solo lee su instancia; así PROD_TRON1..3 no repiten ni triplican el dato."""
        base = base.upper()
        rep = self.inv.representante(base)
        if rep == base:
            return self._recolectar_propia(base, origen=origen, secciones=secciones)
        pedidas = list(self.SECCIONES_OPCIONALES) if secciones is None else list(secciones)
        if refrescar_representante and pedidas:
            self._recolectar_propia(rep, origen=origen, secciones=secciones)
        return self._recolectar_alias(base, rep, origen=origen, secciones=secciones)

    def _recolectar_alias(self, base: str, rep: str, *, origen: str, secciones: list[str] | None) -> dict:
        b = self._base(base)
        try:
            with self.conectar(base, "consulta", persona="sistema", ejecucion_id=f"rec-{utc():%Y%m%d%H%M}",
                               motivo="recolección") as c:
                inst = (c.consultar("salud_instancia") or [None])[0]
        except Exception as ex:
            estado.marcar_error(self.a, f"bd:{base}:salud", "bd.salud", str(ex), origen=origen)
            return {"base": base, "semaforo": "sin_dato", "error": str(ex)}
        dr = (estado.leer(self.a, f"bd:{rep}:salud") or {}).get("datos") or {}
        id_rep, id_yo = (dr.get("instancia") or {}).get("dbid"), (inst or {}).get("dbid")
        if id_rep is not None and id_yo is not None and str(id_rep) != str(id_yo):
            # mismo servicio pero otra base (DBID distinto): se recolecta como base independiente
            return self._recolectar_propia(base, origen=origen, secciones=secciones)
        sem = salud.evaluar(inst, dr.get("tablespaces") or [], dr.get("diskgroups") or [],
                            bloqueadas=dr.get("bloqueadas") or 0, invalidos_nuevos=dr.get("invalidos_fuera_lb") or 0,
                            proteccion=dr.get("proteccion"), exigir_respaldo=self._exige_respaldo(b.ambiente),
                            u=self._umbrales_salud())
        datos = {k: dr.get(k) for k in ("tablespaces", "diskgroups", "bloqueadas", "invalidos", "invalidos_fuera_lb",
                                        "invalidos_en_lb", "invalidos_esquemas", "proteccion", "parciales", "leidas")}
        datos.update({"base": base, "ambiente": b.ambiente, "instancia": inst,
                      "rac": {"representante": rep, "grupo": self.inv.grupo_rac(base)},
                      "ultima_pasada": {"origen": origen, "secciones": [], "datos_de": rep}, **sem})
        estado.guardar(self.a, f"bd:{base}:salud", "bd.salud", datos, origen=origen)
        return {"base": base, "semaforo": sem["semaforo"], "parcial": [], "secciones": [], "datos_de": rep}

    def _recolectar_propia(self, base: str, *, origen: str = "recoleccion", secciones: list[str] | None = None) -> dict:
        """Lee la instancia y las secciones pedidas (todas si `secciones` es None) y actualiza el semáforo.

        Cada sección va en su propio try: si una falla (p. ej. `linea_base` con ORA-00942, o `bloqueos`
        con DPY-4024 por timeout) se guarda lo demás y la falla queda en `datos.parciales`."""
        eid = f"rec-{utc():%Y%m%d%H%M}"
        b = self._base(base)
        pedidas = list(self.SECCIONES_OPCIONALES) if secciones is None else \
            [x for x in self.SECCIONES_OPCIONALES if x in secciones]
        if "invalidos" in pedidas and "linea_base" not in pedidas:
            pedidas.append("linea_base")          # sin línea base no se sabe cuáles son nuevos (va en caché)
        previo = (estado.leer(self.a, f"bd:{base}:salud") or {}).get("datos") or {}
        parciales: dict[str, str] = {}
        res: dict[str, list] = {}
        try:
            with self.conectar(base, "consulta", persona="sistema", ejecucion_id=eid, motivo="recolección") as c:
                inst = (c.consultar("salud_instancia") or [None])[0]
                # bloqueos al final: si excede call_timeout_s la conexión puede quedar inutilizable
                for sec in sorted(pedidas, key=lambda x: x == "bloqueos"):
                    try:
                        if sec == "bloqueos":
                            res[sec] = self._leer_bloqueos(c)
                        elif sec == "invalidos":
                            res[sec] = self._leer_invalidos(c)
                        elif sec == "linea_base":
                            res[sec] = self._leer_linea_base(base, c)
                        elif sec == "fra":
                            res[sec] = c.consultar("fra_uso")
                        elif sec == "respaldo":
                            res[sec] = c.consultar("rman_ultimo")
                        elif sec == "dataguard":
                            res[sec] = self._leer_dataguard(c, inst)
                        else:
                            res[sec] = c.consultar(sec)
                    except Exception as ex:  # noqa: BLE001 — la sección se pierde, la pasada sigue
                        if sec not in self.SIN_AVISO:     # sin ASM o sin standby es normal: no es una falla
                            parciales[sec] = str(ex)[:500]
        except Exception as ex:
            estado.marcar_error(self.a, f"bd:{base}:salud", "bd.salud", str(ex), origen=origen)
            return {"base": base, "semaforo": "sin_dato", "error": str(ex)}

        ahora = utc().isoformat()
        leidas = dict(previo.get("leidas") or {})
        for sec in res:
            leidas[sec] = ahora
        ts = res["tablespaces"] if "tablespaces" in res else previo.get("tablespaces") or []
        dg = res["diskgroups"] if "diskgroups" in res else previo.get("diskgroups") or []

        blq = bloqueos.arbol(res["bloqueos"]) if "bloqueos" in res else None
        bloqueadas = blq["total_bloqueadas"] if blq else previo.get("bloqueadas")

        avisos = [f"{self.SECCIONES_OPCIONALES[k]} no disponible: {v.splitlines()[0][:160]}" for k, v in parciales.items()]
        if "invalidos" in res:
            inv = [o for o in res["invalidos"] if not snapshot.es_papelera(o)]
            n_inv, fuera_n, en_lb = len(inv), None, None
            lbk = {snapshot.clave(o) for o in res["linea_base"]} if "linea_base" in res else None
            if lbk is not None:
                fuera_n = sum(1 for o in inv if snapshot.clave(o) not in lbk)
                en_lb = n_inv - fuera_n
            else:
                avisos.append(f"{n_inv} objetos inválidos (sin línea base para comparar)")
            # desglose por esquema: deja ver de dónde sale el número y qué esquema falta excluir
            por_esq: dict[str, list[int]] = {}
            for o in inv:
                t = por_esq.setdefault(str(o.get("owner")), [0, 0])
                t[0] += 1
                t[1] += int(lbk is not None and snapshot.clave(o) not in lbk)
            esquemas = [{"owner": k, "total": v[0], "fuera_lb": v[1] if lbk is not None else None}
                        for k, v in sorted(por_esq.items(), key=lambda x: -x[1][0])[:20]]
        else:
            n_inv, fuera_n, en_lb = (previo.get("invalidos"), previo.get("invalidos_fuera_lb"),
                                     previo.get("invalidos_en_lb"))
            esquemas = previo.get("invalidos_esquemas")

        proteccion = dict(previo.get("proteccion") or {})
        for sec in self.PROTECCION:
            if sec in res:
                proteccion[sec] = res[sec]
        sem = salud.evaluar(inst, ts, dg, bloqueadas=bloqueadas or 0, invalidos_nuevos=fuera_n or 0,
                            proteccion=proteccion, exigir_respaldo=self._exige_respaldo(b.ambiente),
                            u=self._umbrales_salud())
        datos = {"base": base, "ambiente": b.ambiente, "instancia": inst, "tablespaces": ts, "diskgroups": dg,
                 "bloqueadas": bloqueadas, "invalidos": n_inv, "invalidos_fuera_lb": fuera_n,
                 "invalidos_en_lb": en_lb, "invalidos_esquemas": esquemas, "proteccion": proteccion or None,
                 "parciales": parciales, "leidas": leidas,
                 "ultima_pasada": {"origen": origen, "secciones": sorted(res)}, **sem}
        grupo = self.inv.grupo_rac(base)
        if len(grupo) > 1:
            datos["rac"] = {"representante": grupo[0], "grupo": grupo}
        if avisos:
            datos["motivos"] = list(sem.get("motivos") or []) + avisos
        estado.guardar(self.a, f"bd:{base}:salud", "bd.salud", datos, origen=origen)
        if blq is not None:
            estado.guardar(self.a, f"bd:{self.inv.representante(base)}:bloqueos", "bd.bloqueos", blq, origen=origen)
        if "tablespaces" in res or "diskgroups" in res:
            self._series(base, res.get("tablespaces", []), res.get("diskgroups", []))
        return {"base": base, "semaforo": sem["semaforo"], "parcial": sorted(parciales), "secciones": sorted(res)}

    def revisar_bloqueos(self) -> list[str]:
        """Relee los bloqueos de las bases que hoy muestran sesiones bloqueadas, para confirmarlos o
        limpiarlos ([bd] bloqueos_revision_s). Las demás se actualizan por alerta de Zabbix o la pasada diaria.
        Así una lectura vieja no queda en pantalla como si el bloqueo siguiera."""
        revisadas = []
        for f in estado.leer_tipo(self.a, "bd.bloqueos"):
            base = f["objeto"].split(":")[1]
            if not (f.get("datos") or {}).get("total_bloqueadas") or not self.inv.base(base):
                continue
            if self.inv.representante(base) != base:
                continue
            self.recolectar_base(base, origen="revision", secciones=["bloqueos"])
            revisadas.append(base)
        return revisadas

    def _series(self, base: str, ts: list[dict], dg: list[dict]) -> None:
        hora = utc().replace(minute=0, second=0, microsecond=0)
        puntos = [(f"ts:{base}:{t['tablespace_name']}", float(t["pct_usado"])) for t in ts] + \
                 [(f"asm:{base}:{d['name']}", float(d["pct_usado"])) for d in dg]
        with self.a.tx() as con:
            for serie, valor in puntos:
                r = con.execute(serie_punto.update().where(and_(serie_punto.c.serie == serie, serie_punto.c.ts == hora))
                                .values(valor=valor))
                if r.rowcount == 0:
                    con.execute(serie_punto.insert().values(serie=serie, ts=hora, valor=valor))

    def barrido(self, ambiente: str | None, *, persona: str, ejecucion_id: str, emitir: Emitir,
                secciones: list[str] | None = None) -> dict:
        bases = [b.id for b in self.inv.bases(ambiente)]
        alcance = "completo" if secciones is None else ", ".join(secciones) or "solo instancia"
        emitir(f"Barrido de {len(bases)} base(s) ({alcance}) con máximo {self.cfg['bd']['max_paralelo']} en paralelo")
        # primero los representantes (leen la base completa); después los demás alias RAC copian su dato
        reps = [b for b in bases if self.inv.representante(b) == b]
        alias = [b for b in bases if self.inv.representante(b) != b]
        if alias:
            emitir(f"  {len(alias)} alias de bases RAC reutilizan el dato de su representante")
        with ThreadPoolExecutor(max_workers=self.cfg["bd"]["max_paralelo"]) as ex:
            res = list(ex.map(lambda b: self.recolectar_base(b, origen="barrido", secciones=secciones), reps))
            res += list(ex.map(lambda b: self.recolectar_base(b, origen="barrido", secciones=secciones,
                                                              refrescar_representante=False), alias))
        conteo: dict[str, int] = {}
        for r in res:
            conteo[r["semaforo"]] = conteo.get(r["semaforo"], 0) + 1
            extra = f" ({r['error']})" if r.get("error") else (f" (parcial: {', '.join(r['parcial'])})" if r.get("parcial") else "")
            emitir(f"  {r['base']}: {r['semaforo']}{extra}")
        con_error = sum(1 for r in res if r.get("error"))
        return {"bases": res, "conteo": conteo, "con_error": con_error,
                "parciales": sum(1 for r in res if r.get("parcial")), "total": len(res)}
