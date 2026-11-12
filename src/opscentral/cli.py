"""Línea de comandos de Ops Central.

  ops-central iniciar-almacen          Crea las tablas (desarrollo) o muestra el DDL de PostgreSQL
  ops-central ddl-postgres             Escribe db/postgres/001_esquema.sql
  ops-central api [--puerto 8000]      Levanta la API
  ops-central worker <cola>            Worker de una cola: bd | cm | ingesta | ml
  ops-central planificador             Tareas periódicas
  ops-central dev                      API + workers + planificador en un proceso (solo desarrollo)
  ops-central verificar-auditoria      Revisa la cadena de hash
  ops-central generar-inventario       Arma config/inventario.json desde los insumos reales
  ops-central zabbix-vincular          Completa el host de Zabbix de cada servidor buscando su IP en Zabbix
  ops-central zabbix-probar            Prueba la conexión con la API de Zabbix
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import timedelta

from .config import RAIZ, cargar


def _log():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_iniciar_almacen(a):
    from .almacen import Almacen
    cfg = cargar()
    Almacen(cfg.url_almacen()).crear_esquema()
    print("Almacén listo:", cfg.url_almacen())


def cmd_ddl(a):
    from .almacen import ddl_postgres
    ruta = RAIZ / "db" / "postgres" / "001_esquema.sql"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("-- Generado desde src/opscentral/almacen.py (no editar a mano)\n\n" + ddl_postgres(), encoding="utf-8")
    print("Escrito", ruta)


def cmd_api(a):
    import uvicorn
    from .api import crear_app
    uvicorn.run(crear_app(), host=a.host, port=a.puerto, log_level="info")


def _esperar(detener: threading.Event):
    signal.signal(signal.SIGINT, lambda *_: detener.set())
    signal.signal(signal.SIGTERM, lambda *_: detener.set())
    while not detener.is_set():
        detener.wait(1)


def cmd_worker(a):
    _log()
    from .workers import Worker
    detener = threading.Event()
    hilos = [threading.Thread(target=Worker(a.cola).correr, args=(detener,), daemon=True) for _ in range(a.hilos)]
    for h in hilos:
        h.start()
    _esperar(detener)


def cmd_planificador(a):
    _log()
    from .planificador import Planificador
    detener = threading.Event()
    threading.Thread(target=Planificador().correr, args=(detener,), daemon=True).start()
    _esperar(detener)


def cmd_dev(a):
    _log()
    cfg = cargar()
    if cfg.es_produccion:
        sys.exit("el modo dev no se usa en producción")
    import uvicorn
    from .api import crear_app
    from .planificador import Planificador
    from .workers import iniciar_hilos
    detener = threading.Event()
    iniciar_hilos(["bd", "cm", "ingesta", "ml"], detener)
    if not a.sin_planificador:
        threading.Thread(target=Planificador().correr, args=(detener,), daemon=True).start()
    try:
        uvicorn.run(crear_app(cfg), host="127.0.0.1", port=a.puerto, log_level="info")
    finally:
        detener.set()


def cmd_zabbix_probar(a):
    import json as _json
    from .zabbix import ingesta
    from .nucleo import crear
    n = crear("worker-ingesta")
    print(_json.dumps(ingesta.probar(n.cfg, n.secretos, n.inventario), indent=2, ensure_ascii=False))


def cmd_zabbix_vincular(a):
    import json as _json
    from .nucleo import crear
    from .zabbix import ingesta
    n = crear("cli")
    cli = ingesta.crear_cliente(n.cfg, n.secretos, n.inventario)
    try:
        r = ingesta.vincular_hosts(n.almacen, n.inventario, cli, n.inv_ajustes, por="cli")
    finally:
        cli.cerrar()
    print(_json.dumps({k: (v if not isinstance(v, list) or len(v) < 40 else f"{len(v)} elementos")
                       for k, v in r.items()}, indent=2, ensure_ascii=False))


def cmd_generar_inventario(a):
    import json as _json
    from .inventario_insumos import generar
    cfg = cargar()
    rutas = {k: (RAIZ / v).resolve() for k, v in cfg["inventario"]["insumos"].items()}
    rutas["bases_adicionales"] = [(RAIZ / v).resolve() for v in cfg["inventario"].get("bases_adicionales") or []]
    faltan = [str(rutas[k]) for k in ("servidores_cm", "bases") if not rutas[k].exists()]
    if faltan:
        sys.exit("No se encuentran los insumos obligatorios: " + ", ".join(faltan))
    inv, avisos = generar(rutas)
    destino = a.salida and (RAIZ / a.salida) or cfg.ruta("inventario", "archivo")
    destino.write_text(_json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{destino}: {len(inv['servidores'])} servidores, {len(inv['dominios'])} dominios, "
          f"{len(inv['bases'])} bases, {len(inv['relaciones'])} relaciones")
    for x in avisos:
        print("  aviso:", x)


def cmd_verificar(a):
    from .almacen import Almacen
    from .auditoria import Auditoria
    cfg = cargar()
    ok, n, msg = Auditoria(Almacen(cfg.url_almacen())).verificar()
    print(f"{msg} ({n} registros)")
    sys.exit(0 if ok else 1)


def main(argv=None):
    for flujo in (sys.stdout, sys.stderr):     # consola de Windows (cp1252): no fallar con → ó …
        try:
            flujo.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(prog="ops-central", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("iniciar-almacen").set_defaults(f=cmd_iniciar_almacen)
    s.add_parser("ddl-postgres").set_defaults(f=cmd_ddl)
    x = s.add_parser("api")
    x.add_argument("--host", default="127.0.0.1")
    x.add_argument("--puerto", type=int, default=8000)
    x.set_defaults(f=cmd_api)
    x = s.add_parser("worker")
    x.add_argument("cola", choices=["bd", "cm", "ingesta", "ml"])
    x.add_argument("--hilos", type=int, default=1)
    x.set_defaults(f=cmd_worker)
    s.add_parser("planificador").set_defaults(f=cmd_planificador)
    x = s.add_parser("dev")
    x.add_argument("--puerto", type=int, default=8000)
    x.add_argument("--sin-planificador", action="store_true")
    x.set_defaults(f=cmd_dev)
    s.add_parser("verificar-auditoria").set_defaults(f=cmd_verificar)
    s.add_parser("zabbix-probar").set_defaults(f=cmd_zabbix_probar)
    s.add_parser("zabbix-vincular").set_defaults(f=cmd_zabbix_vincular)
    x = s.add_parser("generar-inventario")
    x.add_argument("--salida", help="ruta de salida (por defecto [inventario].archivo)")
    x.set_defaults(f=cmd_generar_inventario)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
