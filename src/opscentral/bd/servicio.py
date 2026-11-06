"""Motor BD: ejecuta las acciones bd.* del catálogo (worker-bd) y la recolección programada.

Cada acción toma su cuenta según la clase (credenciales.tipo_cuenta_bd):
lectura con OPS_CONSULTA y cambios con OPS_DBA, que solo llama al paquete.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable

from sqlalchemy import and_, select

from .. import estado
from ..almacen import Almacen, serie_punto, snapshot as t_snapshot, utc
from ..catalogo import Accion
from ..credenciales import credencial_bd, tipo_cuenta_bd
from ..inventario import Activo, Inventario
from ..limites import Limites
from ..secretos import Secretos
from . import bloqueos, recompilar, salud, snapshot
from .conexion import ConexionOracle, ErrorBD
from .simulador import ConexionSimulada, simular_despliegue

Emitir = Callable[[str], None]


class MotorBD:
    def __init__(self, cfg, almacen: Almacen, inventario: Inventario, secretos: Secretos, limites: Limites):
        self.cfg, self.a, self.inv, self.sec, self.lim = cfg, almacen, inventario, secretos, limites
        self._clase_con = ConexionSimulada if cfg.simulacion else ConexionOracle

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
            with self._clase_con(b, cred, persona=persona, ejecucion_id=ejecucion_id,
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
        if acc.id == "bd.snap.tomar":
            return self.tomar_snapshot(base, p["cambio"], p["momento"], persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id in ("bd.snap.comparar", "bd.inv.generar"):
            comp = self.comparar(base, p["cambio"], persona=quien, ejecucion_id=eid)
            if acc.id == "bd.inv.generar":
                comp["plan"] = recompilar.plan(comp["nuevos"])
                emitir(f"{len(comp['plan'])} sentencia(s) generadas (vista previa; se ejecutan por el paquete)")
            emitir(f"Veredicto: {comp['veredicto']} · {comp['resumen']}")
            return comp
        if acc.id == "bd.inv.recompilar":
            return self.recompilar(base, p["cambio"], persona=quien, ejecucion_id=eid, emitir=emitir)
        if acc.id == "bd.ses.terminar":
            return self.terminar_sesion(e, emitir)
        if acc.plantilla:
            with self.conectar(base, tipo, persona=quien, ejecucion_id=eid, motivo=motivo) as c:
                filas = c.consultar(acc.plantilla, p)
            emitir(f"{len(filas)} fila(s)")
            if acc.plantilla == "bloqueos":
                r = bloqueos.arbol(filas)
                estado.guardar(self.a, f"bd:{base}:bloqueos", "bd.bloqueos", r, origen="ejecucion")
                self._actualizar_salud(base, emitir)
                return r
            return {"filas": filas}
        if acc.procedimiento:
            args = {k: ("S" if v is True else "N" if v is False else v) for k, v in p.items()
                    if k not in ("base", "cambio")}
            with self.conectar(base, "dba", persona=quien, ejecucion_id=eid, motivo=motivo) as c:
                r = c.llamar(acc.procedimiento, args)
            emitir(f"{acc.procedimiento} → {r}")
            return {"resultado": r}
        raise ErrorBD(f"{acc.id} no tiene lógica en el motor BD")

    # ------------------------------------------------------------ snapshot y recompilación
    def tomar_snapshot(self, base: str, cambio: str, momento: str, *, persona: str, ejecucion_id: str,
                       emitir: Emitir) -> dict:
        if self.cfg.simulacion and momento == "DESPUES":
            simular_despliegue(base)
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id,
                           motivo=f"snapshot {momento}") as c:
            inv = c.consultar("invalidos")
            ts = c.consultar("tablespaces")
        s = snapshot.armar(base, cambio, momento, inv, {"tablespaces": ts})
        with self.a.tx() as con:
            con.execute(t_snapshot.insert().values(id=uuid.uuid4().hex, base=base, cambio=cambio, momento=momento,
                                                   tomado=utc(), datos=s, huella=s["huella"],
                                                   ejecucion_id=ejecucion_id))
        emitir(f"Snapshot {momento}: {s['total']} objeto(s) inválido(s)")
        return {"momento": momento, "total": s["total"], "huella": s["huella"]}

    def _ultimo(self, base: str, cambio: str, momentos: tuple[str, ...]) -> dict | None:
        with self.a.tx() as con:
            f = con.execute(select(t_snapshot.c.datos).where(and_(
                t_snapshot.c.base == base, t_snapshot.c.cambio == cambio, t_snapshot.c.momento.in_(momentos)))
                .order_by(t_snapshot.c.tomado.desc()).limit(1)).first()
        return f[0] if f else None

    def comparar(self, base: str, cambio: str, *, persona: str, ejecucion_id: str) -> dict:
        antes = self._ultimo(base, cambio, ("ANTES",))
        despues = self._ultimo(base, cambio, ("FINAL", "DESPUES"))
        if not antes or not despues:
            raise ErrorBD(f"faltan snapshots para {cambio} en {base}: se necesitan ANTES y DESPUÉS")
        with self.conectar(base, "consulta", persona=persona, ejecucion_id=ejecucion_id, motivo="línea base") as c:
            lb = c.consultar("linea_base")
        comp = snapshot.comparar(antes, despues, lb)
        estado.guardar(self.a, f"bd:{base}:snapshot:{cambio}", "bd.snapshot", comp, origen="ejecucion")
        return comp

    def recompilar(self, base: str, cambio: str, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        comp = self.comparar(base, cambio, persona=persona, ejecucion_id=ejecucion_id)
        if not comp["nuevos"]:
            emitir("No hay objetos nuevos por el cambio: nada que recompilar")
            return {"comparacion": comp, "recompilacion": None}
        emitir(f"{len(comp['nuevos'])} objeto(s) nuevo(s); se excluyen {comp['resumen']['en_linea_base']} de la línea base "
               f"y {comp['resumen']['papelera']} de la papelera")
        with self.conectar(base, "dba", persona=persona, ejecucion_id=ejecucion_id, motivo=f"recompilar {cambio}") as c:
            r = recompilar.ejecutar(c, comp["nuevos"], emitir=emitir)
        emitir("Snapshot FINAL automático")
        self.tomar_snapshot(base, cambio, "FINAL", persona=persona, ejecucion_id=ejecucion_id, emitir=emitir)
        final = self.comparar(base, cambio, persona=persona, ejecucion_id=ejecucion_id)
        self._actualizar_salud(base, emitir)
        return {"recompilacion": r, "comparacion_final": final}

    def _actualizar_salud(self, base: str, emitir: Emitir) -> None:
        """Tras una acción que cambia lo que muestra Salud (bloqueos, inválidos), se repite la pasada
        liviana de recolección de esa base para que el semáforo no quede con el dato anterior."""
        r = self.recolectar_base(base, origen="ejecucion")
        if r.get("error"):
            emitir(f"Salud no se pudo actualizar: {r['error']} (se actualizará en la próxima recolección)")
        else:
            emitir(f"Salud de {base} actualizada: {r['semaforo']}")

    # ------------------------------------------------------------ sesiones
    def terminar_sesion(self, e: dict, emitir: Emitir) -> dict:
        p, base = e["params"], e["params"]["base"]
        with self.conectar(base, "consulta", persona=e["solicitante"], ejecucion_id=e["id"], motivo="verificar raíz") as c:
            antes = bloqueos.arbol(c.consultar("bloqueos"))
        raices = {(a["raiz"]["inst_id"], a["raiz"]["sid"]) for a in antes["arboles"]}
        objetivo = (p.get("inst_id", 1), p["sid"])
        if objetivo not in raices:
            emitir("Advertencia: la sesión ya no es raíz de un bloqueo; el paquete validará antes de terminar")
        with self.conectar(base, "dba", persona=e["solicitante"], ejecucion_id=e["id"], motivo="terminar sesión") as c:
            r = c.llamar("TERMINAR_SESION", {"sid": p["sid"], "serial": p["serial"], "inst_id": p.get("inst_id", 1),
                                             "motivo": p["motivo"]})
        emitir(f"TERMINAR_SESION → {r}")
        with self.conectar(base, "consulta", persona=e["solicitante"], ejecucion_id=e["id"], motivo="verificar") as c:
            despues = bloqueos.arbol(c.consultar("bloqueos"))
        estado.guardar(self.a, f"bd:{base}:bloqueos", "bd.bloqueos", despues, origen="ejecucion")
        sigue = any((a["raiz"]["inst_id"], a["raiz"]["sid"]) == objetivo for a in despues["arboles"])
        emitir("El bloqueo desapareció" if not sigue else "El bloqueo continúa: revisar")
        self._actualizar_salud(base, emitir)
        return {"resultado": r, "bloqueo_resuelto": not sigue, "bloqueadas_antes": antes["total_bloqueadas"],
                "bloqueadas_despues": despues["total_bloqueadas"]}

    # ------------------------------------------------------------ recolección programada
    def recolectar_base(self, base: str, *, origen: str = "recoleccion") -> dict:
        """Una pasada liviana por base: salud, tablespaces, diskgroups, bloqueos e inválidos."""
        eid = f"rec-{utc():%Y%m%d%H%M}"
        b = self._base(base)
        try:
            with self.conectar(base, "consulta", persona="sistema", ejecucion_id=eid, motivo="recolección") as c:
                inst = (c.consultar("salud_instancia") or [None])[0]
                ts = c.consultar("tablespaces")
                try:
                    dg = c.consultar("diskgroups")
                except Exception:  # sin ASM
                    dg = []
                blq = bloqueos.arbol(c.consultar("bloqueos"))
                inv = c.consultar("invalidos")
                lb = c.consultar("linea_base")
        except Exception as ex:
            estado.marcar_error(self.a, f"bd:{base}:salud", "bd.salud", str(ex), origen=origen)
            return {"base": base, "semaforo": "sin_dato", "error": str(ex)}
        lbk = {snapshot.clave(o) for o in lb}
        fuera_lb = [o for o in inv if snapshot.clave(o) not in lbk and not snapshot.es_papelera(o)]
        sem = salud.evaluar(inst, ts, dg, bloqueadas=blq["total_bloqueadas"], invalidos_nuevos=len(fuera_lb))
        datos = {"base": base, "ambiente": b.ambiente, "instancia": inst, "tablespaces": ts, "diskgroups": dg,
                 "bloqueadas": blq["total_bloqueadas"], "invalidos_fuera_lb": len(fuera_lb), **sem}
        estado.guardar(self.a, f"bd:{base}:salud", "bd.salud", datos, origen=origen)
        estado.guardar(self.a, f"bd:{base}:bloqueos", "bd.bloqueos", blq, origen=origen)
        self._series(base, ts, dg)
        return {"base": base, "semaforo": sem["semaforo"]}

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

    def barrido(self, ambiente: str | None, *, persona: str, ejecucion_id: str, emitir: Emitir) -> dict:
        bases = [b.id for b in self.inv.bases(ambiente)]
        emitir(f"Barrido de {len(bases)} base(s) con máximo {self.cfg['bd']['max_paralelo']} en paralelo")
        with ThreadPoolExecutor(max_workers=self.cfg["bd"]["max_paralelo"]) as ex:
            res = list(ex.map(lambda b: self.recolectar_base(b, origen="barrido"), bases))
        conteo: dict[str, int] = {}
        for r in res:
            conteo[r["semaforo"]] = conteo.get(r["semaforo"], 0) + 1
            emitir(f"  {r['base']}: {r['semaforo']}" + (f" ({r['error']})" if r.get("error") else ""))
        return {"bases": res, "conteo": conteo}
