"""Carga de configuración (TOML) con valores por defecto.

La ruta se toma de OPS_CENTRAL_CONFIG; si no existe, config/ops-central.toml y,
como último recurso, config/ops-central.ejemplo.toml (solo útil en desarrollo).
Las rutas relativas se resuelven contra la raíz del proyecto.
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]

DEFECTOS: dict[str, Any] = {
    "general": {"modo": "desarrollo"},
    "almacen": {"url": "sqlite:///datos/ops-central.db"},
    "evidencia": {"ruta": "datos/evidencia"},
    "reportes": {"ruta": "datos/reportes"},
    "auth": {"proveedor": "dev", "inactividad_min": 30, "duracion_max_h": 8, "stepup_vigencia_min": 5,
             "intentos_max": 5, "bloqueo_min": 15,
             "grupos": {"GG_OPS_DBA": "dba", "GG_OPS_CM": "cm", "GG_OPS_LIDER": "lid",
                        "GG_OPS_CONSULTA": "con", "GG_OPS_ADMIN": "adm"},
             "oidc": {}},
    "aprobaciones": {"clave_firma_ref": "ops/firma_aprobaciones", "vigencia_min": 120, "aprobacion_c_no_prod": True,
                     "ejecucion_directa": True, "exigir_referencia_prod": False, "motivo_obligatorio_c": False},
    "jira": {"url": "", "credencial_ref": "jira/api", "timeout_s": 20, "verificar_tls": True,
             "jql_abiertos": "statusCategory != Done ORDER BY updated DESC", "max_resultados": 25,
             "campos": {}},
    "secretos": {"proveedor": "archivo", "archivo": "config/secretos.dev.json"},
    "bd": {"cuenta_consulta": "OPS_CONSULTA", "cuenta_dba": "OPS_DBA", "paquete": "ADMINBD.PKG_OPS_CENTRAL",
           "call_timeout_s": 20, "max_paralelo": 10, "sesiones_por_base": 1, "refresco_min_s": 120,
           "circuito_fallos": 3, "circuito_espera_s": 600, "ruta_kit_oracle": "../oracle-agent-kiro-master",
           # true solo en demo sin Oracle: los reportes bd.rep.* salen con datos de MUESTRA (prohibido en producción)
           "reportes_muestra": False},
    "cm": {"ruta_cmops": "../capa-media-ops", "python_cmops": "python", "ttl_certificado_min": 10, "timeout_s": 3600},
    "zabbix": {"ventana_episodio_min": 15, "secreto_webhook_ref": "zabbix/secreto_webhook",
               "token_ref": "zabbix/token_lectura", "puerto_trapper": 10051, "url": "",
               "verificar_tls": True, "timeout_s": 30, "user_agent": "OpsCentral/0.1",
               "severidad_min": 2, "dias_trends": 30, "max_items_host": 40},
    "ml": {"umbrales": [85, 95, 100], "ventana_dias": 14, "min_puntos": 24},
    "inventario": {"archivo": "config/inventario.json",
                   "insumos": {"servidores_cm": "../capa-media-ops/inventario/servidores.json",
                               "bases": "../oracle-agent-kiro-master/config/databases.json",
                               "bases_csv": "../oracle-agent-kiro-master/docs/databases.csv",
                               "mapa_oracle": "../zabbix-capacity-mcp-main/capacity-watchdog/config/oracle_map.yaml",
                               "relaciones": "config/relaciones.json"}},
}


def _fusionar(base: dict, extra: dict) -> dict:
    r = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = _fusionar(r[k], v)
        else:
            r[k] = v
    return r


class Config(dict):
    """dict con utilidades: cfg.ruta('evidencia', 'ruta') resuelve contra la raíz."""

    def ruta(self, seccion: str, clave: str) -> Path:
        p = Path(self[seccion][clave])
        return p if p.is_absolute() else (RAIZ / p)

    @property
    def es_produccion(self) -> bool:
        return self["general"].get("modo") == "produccion"

    def url_almacen(self) -> str:
        url = self["almacen"]["url"]
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
            rel = url[len("sqlite:///"):]
            p = Path(rel)
            if not p.is_absolute():
                p = RAIZ / p
            p.parent.mkdir(parents=True, exist_ok=True)
            return "sqlite:///" + p.as_posix()
        return url


def cargar(ruta: str | os.PathLike | None = None, extra: dict | None = None) -> Config:
    candidatos = [ruta, os.environ.get("OPS_CENTRAL_CONFIG"),
                  RAIZ / "config" / "ops-central.toml", RAIZ / "config" / "ops-central.ejemplo.toml"]
    datos: dict = {}
    for c in candidatos:
        if c and Path(c).is_file():
            with open(c, "rb") as f:
                datos = tomllib.load(f)
            break
    cfg = Config(_fusionar(DEFECTOS, datos))
    if extra:
        cfg = Config(_fusionar(cfg, extra))
    _validar(cfg)
    return cfg


def _validar(cfg: Config) -> None:
    """Reglas que no se pueden saltar en producción."""
    if cfg.es_produccion:
        problemas = []
        if cfg["auth"]["proveedor"] in ("dev", "demo"):
            problemas.append(f"auth.proveedor = '{cfg['auth']['proveedor']}' no se permite en producción")
        if cfg["secretos"]["proveedor"] == "archivo":
            problemas.append("secretos.proveedor = 'archivo' no se permite en producción")
        if cfg["almacen"]["url"].startswith("sqlite"):
            problemas.append("el almacén de producción es PostgreSQL")
        if cfg["bd"].get("reportes_muestra"):
            problemas.append("bd.reportes_muestra = true no se permite en producción")
        if problemas:
            raise ValueError("Configuración inválida para producción: " + "; ".join(problemas))
