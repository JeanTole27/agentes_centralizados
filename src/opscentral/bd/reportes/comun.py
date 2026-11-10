# -*- coding: utf-8 -*-
"""Utilidades compartidas por los generadores de reportes de BD.

Reutiliza las piezas de generar_hoja_vida.py (cliente Oracle de solo lectura,
credenciales por perfil, Section/safe_collect, formato y CSS verde institucional)
para que todos los reportes se comporten igual: 100 % SELECT, cada sección que
falla queda NOT_COLLECTED con el error real y el reporte nunca aborta.

Funciona en dos lugares:
  • dentro de Ops Central (paquete opscentral.bd.reportes), y
  • copiado junto a generar_hoja_vida.py en el paquete portable (reportes_comun.py).
"""
from __future__ import annotations

import argparse
import datetime
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

try:  # dentro de Ops Central
    from . import hoja_vida as hv
except ImportError:  # paquete portable: este archivo vive junto a generar_hoja_vida.py
    import generar_hoja_vida as hv  # type: ignore

Section = hv.Section
safe_collect = hv.safe_collect
esc, num, badge, worst = hv.esc, hv.num, hv.badge, hv.worst
tabla = hv._table
CSS_VERDE = hv.CSS
COT_TZ = hv.COT_TZ
log = hv._log

SEV = {"OK": 0, "UNKNOWN": 1, "NO_DATA": 1, "WARNING": 2, "CRITICAL": 3}


def ahora() -> datetime.datetime:
    return datetime.datetime.now()


def sufijo_ambiente(ambiente: str) -> str:
    """Igual que la hoja de vida: sin sufijo en prod, '-test', '-qa'... en el resto."""
    a = (ambiente or "").strip().lower()
    return "" if a in ("prod", "") else f"-{a}"


def q(client, sql: str, params: Optional[dict] = None, max_rows: int = 500) -> list[dict]:
    return client.query(sql, params or {}, max_rows=max_rows)


def q1(client, sql: str, params: Optional[dict] = None) -> dict:
    return client.query_one(sql, params or {}) or {}


def nc(section: Section, etiqueta: str) -> str:
    return (f'<div class="nc">{esc(etiqueta)}: <strong>NOT_COLLECTED</strong> — '
            f'{esc(section.error)}</div>')


def vacio(texto: str) -> str:
    return f'<div class="note">{esc(texto)}</div>'


def a_float(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  CLI común (modo portable): --alias / --env / --all, igual que generar_hoja_vida
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def abrir_cliente(entry: dict, muestra: bool = False):
    """Cliente de solo lectura para una entrada del inventario portable."""
    if muestra:
        try:
            from . import muestra as m
        except ImportError:
            import reportes_muestra as m  # type: ignore
        yield m.ClienteMuestra(entry["alias"])
        return
    user, password = hv.resolve_credentials(entry["alias"], entry.get("environment", ""))
    if not user or not password:
        raise RuntimeError(f"No se resolvieron credenciales para alias '{entry['alias']}'. "
                           "Verifica config/.env y el perfil activo en config/.active_profile.")
    client = hv.OracleClient(entry["alias"], entry["dsn"], user, password)
    try:
        yield client
    finally:
        client.close()


def _inventario_muestra() -> dict[str, dict]:
    return {
        "prod_core1": {"alias": "prod_core1", "dsn": "10.7.0.34:1521/CORPRD", "environment": "prod"},
        "prod_core2": {"alias": "prod_core2", "dsn": "10.7.0.35:1521/CORPRD", "environment": "prod"},
        "prod_crm": {"alias": "prod_crm", "dsn": "10.7.0.34:1521/CRMPRD", "environment": "prod"},
        "prod_pagos": {"alias": "prod_pagos", "dsn": "10.7.0.89:1521/PAGPRD", "environment": "prod"},
        "prod_hist": {"alias": "prod_hist", "dsn": "10.7.0.91:1521/HISPRD", "environment": "prod"},
        "test_core": {"alias": "test_core", "dsn": "10.8.1.20:1521/CORTST", "environment": "test"},
    }


def cargar_inventario(args) -> dict[str, dict]:
    if args.muestra:
        return _inventario_muestra()
    hv.load_env()
    hv.init_thick_if_configured()
    return hv.load_inventory(Path(args.inventory))


def argumentos(descripcion: str, *, por_ambiente: bool = False) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=descripcion)
    if not por_ambiente:
        p.add_argument("--alias", default="", help="Alias de una BD en config/databases.json.")
        p.add_argument("--all", action="store_true", help="Todas las BD del inventario (dedup RAC).")
    p.add_argument("--env", default="", help="Ambiente (prod/test/…)." if por_ambiente
                   else "Todas las BD de este ambiente (dedup RAC). Con --alias, fuerza el ambiente.")
    p.add_argument("--output-dir", default="", help="Carpeta de salida (default: reportes/HV_{env}_{YYYYMMDD}/).")
    p.add_argument("--inventory", default=str(hv.DEFAULT_INVENTORY), help="Inventario JSON.")
    p.add_argument("--muestra", action="store_true",
                   help="Genera con datos de muestra, sin conectarse (para revisar el formato).")
    return p


def carpeta_salida(args, ambiente: str) -> Path:
    if args.output_dir:
        return Path(args.output_dir)
    return hv.REPORTS_DIR / f"HV_{ambiente or 'all'}_{ahora().strftime('%Y%m%d')}"


def escribir(carpeta: Path, nombre: str, contenido: str) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre
    ruta.write_text(contenido, encoding="utf-8")
    log(f"OK — reporte generado: {ruta}")
    return ruta


def cli_por_base(descripcion: str, construir: Callable[..., tuple[str, str, dict]],
                 extra: Callable[[argparse.ArgumentParser], None] | None = None,
                 kwargs_de: Callable[[Any], dict] | None = None) -> int:
    """CLI estándar para reportes de una base (análisis, sentencias costosas)."""
    p = argumentos(descripcion)
    if extra:
        extra(p)
    args = p.parse_args()
    if not (args.alias or args.env or args.all):
        log("ERROR: indica --alias <alias>, --env <ambiente> o --all.")
        return 2
    inventario = cargar_inventario(args)
    if args.alias:
        if args.alias not in inventario:
            log(f"ERROR: alias '{args.alias}' no está en el inventario.")
            return 2
        objetivos = [dict(inventario[args.alias], alias=args.alias)]
        if args.env:
            objetivos[0]["environment"] = args.env
    else:
        objetivos = [dict(e, alias=a) for a, e in inventario.items()]
        if args.env:
            objetivos = [e for e in objetivos if (e.get("environment") or "").lower() == args.env.lower()]
        objetivos = hv._dedup_physical(objetivos)
    if not objetivos:
        log("ERROR: no hay BD que coincidan con la selección.")
        return 2
    kw = kwargs_de(args) if kwargs_de else {}
    fallos = 0
    for e in objetivos:
        try:
            with abrir_cliente(e, args.muestra) as client:
                nombre, html_txt, resumen = construir(client, e["alias"], e, **kw)
            print(escribir(carpeta_salida(args, e.get("environment", "")), nombre, html_txt))
            log(f"{e['alias']}: overall_status={resumen.get('overall_status')}")
        except Exception as ex:  # noqa: BLE001
            fallos += 1
            log(f"{e['alias']}: FALLO — {str(ex).strip().splitlines()[0] if str(ex).strip() else repr(ex)}")
    return 0 if not fallos else 1
