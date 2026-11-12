"""Workers: toman trabajos de su cola y los ejecutan contra su motor.

  worker-bd       cola bd       ejecuciones bd.*, refrescos y recolección de bases
  worker-cm       cola cm       ejecuciones de Capa Media (cmops)
  worker-ingesta  cola ingesta  Zabbix: problemas (respaldo del webhook) y trends de capacidad
  worker-ml       cola ml       scoring y entrenamiento

Cada worker es un proceso con su propio contexto de secretos: la API nunca
tiene las credenciales de cambio.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import traceback

from .autorizacion import PermisoDenegado
from .ejecuciones import Conflicto, EstadoInvalido
from .limites import CircuitoAbierto, Limites, Ocupado
from .nucleo import Nucleo, crear

log = logging.getLogger("opscentral.worker")

CONTEXTO = {"bd": "worker-bd", "cm": "worker-cm", "ingesta": "worker-ingesta", "ml": "worker-ml"}


class Worker:
    def __init__(self, cola: str, nucleo: Nucleo | None = None):
        if cola not in CONTEXTO:
            raise ValueError(f"cola desconocida: {cola}")
        self.cola = cola
        self.n = nucleo or crear(CONTEXTO[cola])
        self.nombre = f"{CONTEXTO[cola]}@{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 10000}"
        self._motor_bd = None
        self._motor_cm = None

    # -------------------------------------------------------------- motores (perezosos)
    @property
    def motor_bd(self):
        if self._motor_bd is None:
            from .bd.servicio import MotorBD
            c = self.n.cfg["bd"]
            lim = Limites(self.n.almacen, por_destino=c["sesiones_por_base"], paralelo=c["max_paralelo"],
                          fallos_max=c["circuito_fallos"], espera_s=c["circuito_espera_s"])
            self._motor_bd = MotorBD(self.n.cfg, self.n.almacen, self.n.inventario, self.n.secretos, lim)
        return self._motor_bd

    @property
    def motor_cm(self):
        if self._motor_cm is None:
            from .cm.adaptador import MotorCM
            self._motor_cm = MotorCM(self.n.cfg, self.n.secretos)
        return self._motor_cm

    # -------------------------------------------------------------- ciclo
    def una_vez(self) -> bool:
        t = self.n.cola.tomar(self.cola, self.nombre)
        if not t:
            return False
        try:
            if self.n.inv_ajustes:
                self.n.inv_ajustes.sincronizar()     # ajustes del inventario hechos desde la interfaz
            getattr(self, f"_t_{t.tipo}")(t.payload)
            self.n.cola.completar(t.id)
        except _Reprogramar as r:
            self.n.cola.completar(t.id)
            self.n.cola.encolar(self.cola, t.tipo, t.payload, en_segundos=r.segundos)
        except Exception as e:
            log.exception("trabajo %s falló", t.id)
            self.n.cola.fallar(t, f"{type(e).__name__}: {e}")
        return True

    def correr(self, detener: threading.Event, pausa: float = 1.0) -> None:
        log.info("%s escuchando la cola %s", self.nombre, self.cola)
        while not detener.is_set():
            try:
                if not self.una_vez():
                    detener.wait(pausa)
            except Exception:  # nunca muere el ciclo
                log.exception("error en el ciclo del worker")
                detener.wait(5)

    # -------------------------------------------------------------- tipos de trabajo
    def _t_ejecutar(self, p: dict) -> None:
        ej = self.n.ejecuciones
        eid = p["ejecucion_id"]
        try:
            e, acc = ej.iniciar(eid, self.nombre)
        except Conflicto:
            raise _Reprogramar(60) from None
        except (PermisoDenegado, EstadoInvalido) as ex:
            log.warning("ejecución %s no se ejecuta: %s", eid, ex)
            return
        emitir = lambda texto: ej.emitir(eid, texto)  # noqa: E731
        try:
            if acc.motor == "bd":
                r = self.motor_bd.ejecutar(e, acc, emitir)
            elif acc.motor == "cm":
                r = self.motor_cm.ejecutar(e, acc, emitir)
            else:
                raise RuntimeError(f"el worker {self.cola} no ejecuta el motor {acc.motor}")
            ej.finalizar(eid, self.nombre, ok=True, resultado=r)
        except (CircuitoAbierto, Ocupado) as ex:
            emitir(f"No se ejecutó: {ex}")
            ej.finalizar(eid, self.nombre, ok=False, error=str(ex))
        except Exception as ex:
            emitir(f"ERROR: {ex}")
            ej.finalizar(eid, self.nombre, ok=False, error=f"{type(ex).__name__}: {ex}",
                         resultado={"traza": traceback.format_exc(limit=5)})

    def _t_refrescar(self, p: dict) -> None:
        if self.cola == "bd":
            origen = "evento" if str(p.get("motivo", "")).startswith("zabbix:") else "refresco"
            self.motor_bd.recolectar_base(p["base"], origen=origen, secciones=p.get("secciones"))

    def _t_recolectar(self, p: dict) -> None:
        # p["secciones"]: lista (pasada liviana) o ausente/None (completa)
        self.motor_bd.barrido(p.get("ambiente"), persona="sistema", ejecucion_id="recoleccion",
                              emitir=lambda _t: None, secciones=p.get("secciones"))

    def _t_bloqueos_vigentes(self, p: dict) -> None:
        self.motor_bd.revisar_bloqueos()

    def _t_zbx_vincular(self, p: dict) -> None:
        from . import estado
        try:
            r = self._zabbix(lambda ing, cli: ing.vincular_hosts(self.n.almacen, self.n.inventario, cli,
                                                                 self.n.inv_ajustes))
        except Exception as e:
            estado.marcar_error(self.n.almacen, "zbx:vinculos", "zbx.vinculos", str(e), origen="zabbix")
            raise
        if r["vinculados"]:
            self.n.auditoria.registrar("zabbix_vinculos", self.nombre, vinculados=len(r["vinculados"]))

    # -------------------------------------------------------------- Zabbix (cola ingesta)
    def _zabbix(self, fn):
        from .zabbix import ingesta
        cli = ingesta.crear_cliente(self.n.cfg, self.n.secretos, self.n.inventario)
        try:
            return fn(ingesta, cli)
        finally:
            cli.cerrar()

    def _t_zbx_problemas(self, p: dict) -> None:
        from . import estado
        try:
            r = self._zabbix(lambda ing, cli: ing.sincronizar_problemas(
                self.n.almacen, self.n.cola, self.n.inventario, cli, self.n.cfg))
        except Exception as e:
            estado.marcar_error(self.n.almacen, "zbx:problemas", "zbx.problemas", str(e), origen="zabbix")
            raise
        if r["nuevos"] or r["recuperados"]:
            self.n.auditoria.registrar("zabbix_sincronizacion", self.nombre, abiertos=r["abiertos"],
                                       nuevos=r["nuevos"], recuperados=r["recuperados"])

    def _t_zbx_capacidad(self, p: dict) -> None:
        from . import estado
        try:
            r = self._zabbix(lambda ing, cli: ing.recolectar_capacidad(self.n.almacen, self.n.inventario, cli,
                                                                       self.n.cfg))
        except Exception as e:
            estado.marcar_error(self.n.almacen, "zbx:capacidad", "zbx.capacidad", str(e), origen="zabbix")
            raise
        if r["puntos_nuevos"]:   # hay datos nuevos: se recalculan las predicciones
            self.n.cola.encolar("ml", "scoring", {}, clave_unica="plan:scoring")

    def _t_scoring(self, p: dict) -> None:
        from .ml.servicio import scoring
        r = scoring(self.n.almacen, self.n.cfg)
        self.n.auditoria.registrar("ml_scoring", self.nombre, **r)


class _Reprogramar(Exception):
    def __init__(self, segundos: int):
        self.segundos = segundos


def iniciar_hilos(colas: list[str], detener: threading.Event) -> list[threading.Thread]:
    hilos = []
    for c in colas:
        w = Worker(c)
        h = threading.Thread(target=w.correr, args=(detener,), name=f"worker-{c}", daemon=True)
        h.start()
        hilos.append(h)
    return hilos
