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
    # pantallas_ocultas: fuera del menú mientras sean "próxima etapa" (vaciar la lista para mostrarlas)
    "general": {"modo": "desarrollo", "pantallas_ocultas": ["ventana", "incidentes"]},
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
    "asistente": {"habilitado": False, "url": "", "motor": "", "credencial_ref": "asistente/api", "timeout_s": 60,
                  "max_caracteres": 2000},
    "secretos": {"proveedor": "archivo", "archivo": "config/secretos.dev.json"},
    "bd": {"cuenta_consulta": "OPS_CONSULTA", "cuenta_dba": "OPS_DBA", "paquete": "ADMINBD.PKG_OPS_CENTRAL",
           "call_timeout_s": 20, "max_paralelo": 10, "sesiones_por_base": 1, "refresco_min_s": 120,
           "circuito_fallos": 3, "circuito_espera_s": 600, "ruta_kit_oracle": "../oracle-agent-kiro-master",
           # carpeta del Oracle Instant Client (modo thick). Vacío = thin, o la variable ORACLE_CLIENT_DIR
           "oracle_client_dir": "",
           # true solo en demo sin Oracle: los reportes bd.rep.* salen con datos de MUESTRA (prohibido en producción)
           "reportes_muestra": False,
           # Recolección: periódica liviana (solo espacio), completa una vez al día; lo demás por alerta.
           "recoleccion_periodica_s": 3600, "secciones_periodicas": ["tablespaces", "diskgroups"],
           "recoleccion_completa_s": 86400, "linea_base_cache_s": 21600, "max_bases_por_evento": 10,
           # Bloqueos: las bases que muestran bloqueos se releen cada bloqueos_revision_s para confirmarlos o
           # limpiarlos; una lectura más vieja que bloqueos_vigencia_s se muestra como "sin confirmar" y no
           # cuenta. Una espera menor a bloqueo_min_espera_s no se considera bloqueo (contención normal).
           "bloqueos_revision_s": 300, "bloqueos_vigencia_s": 900, "bloqueo_min_espera_s": 5,
           # Al abrir Bloqueos o Salud se pide una lectura actual del ambiente visible, como mucho cada
           # refresco_vista_s por vista y ambiente (aunque la abran varias personas).
           "refresco_vista_s": 300,
           # Protección (FRA, RMAN, Data Guard): ambientes donde la falta de respaldo RMAN pinta el semáforo
           # (en DESA/TEST suele no haber RMAN y sería ruido) y umbrales que reemplazan a bd/salud.UMBRALES.
           "respaldo_ambientes": ["PROD"], "umbrales_salud": {},
           # Esquemas que no cuentan como inválidos propios (comodín %): Oracle no los marca como suyos.
           "invalidos_excluir": ["PUBLIC", "SYS", "SYSTEM", "DBSNMP", "OUTLN", "ORACLE_OCM", "APPQOSSYS", "AUDSYS",
                                 "DBSFWUSER", "REMOTE_SCHEDULER_AGENT", "SYS$UMF","APEX_%", "FLOWS_%", "ORDS_METADATA", "ORDS_PUBLIC_USER", "GGSYS",
                                 "GSMADMIN_INTERNAL", "GSMCATUSER", "GSMUSER", "SYSMAN", "MGMT_VIEW", "XDB",
                                 "OJVMSYS", "DVSYS", "LBACSYS", "MDSYS", "OLAPSYS", "WMSYS", "CTXSYS", "ORDSYS",
                                 "ORDDATA", "SI_INFORMTN_SCHEMA", "EXFSYS", "OWBSYS%", "SQLTXPLAIN", "SQLTXADMIN"]},
    # frm_lote_max: tope de procesos frmweb por lote al mitigar, por ambiente (la API lo aplica aunque se pida más)
    "cm": {"ruta_cmops": "capa-media-ops", "python_cmops": "python", "ttl_certificado_min": 10, "timeout_s": 3600,
           "frm_lote_max": {"PROD": 10, "DRP": 10, "*": 40}},
    "zabbix": {"ventana_episodio_min": 15, "secreto_webhook_ref": "zabbix/secreto_webhook",
               "token_ref": "zabbix/token_lectura", "puerto_trapper": 10051, "url": "",
               "verificar_tls": True, "timeout_s": 30, "user_agent": "OpsCentral/0.1",
               "severidad_min": 2, "dias_trends": 30, "max_items_host": 40, "vincular_s": 86400},
    # anomalías (M3): semanas de historia por día/hora y puntaje robusto mínimo para marcar
    "ml": {"umbrales": [85, 95, 100], "ventana_dias": 14, "min_puntos": 24, "anomalias_semanas": 6,
           "anomalias_umbral_z": 3.5},
    "inventario": {"archivo": "config/inventario.json", "editores": [], "bases_adicionales": [],
                   "insumos": {"servidores_cm": "../capa-media-ops/inventario/servidores.json",
                               "bases": "../oracle-agent-kiro-master/config/databases.json",
                               "bases_csv": "../oracle-agent-kiro-master/docs/databases.csv",
                               "mapa_oracle": "../zabbix-capacity-mcp-main/capacity-watchdog/config/oracle_map.yaml",
                               "relaciones": "config/relaciones.json"}},
}


CMOPS_INTERNO = RAIZ / "capa-media-ops"   # copia de capa-media-ops dentro del proyecto (su lógica sin cambios)


def ruta_cmops(cfg) -> Path:
    """Carpeta de capa-media-ops: la configurada ([cm] ruta_cmops, relativa a ops-central) y, si ahí no está
    (p. ej. un ops-central.toml viejo con "../capa-media-ops"), la copia interna del proyecto."""
    r = Path(cfg["cm"].get("ruta_cmops") or CMOPS_INTERNO)
    r = r if r.is_absolute() else (RAIZ / r).resolve()
    if not (r / "cmops" / "trabajos.py").exists() and (CMOPS_INTERNO / "cmops" / "trabajos.py").exists():
        return CMOPS_INTERNO
    return r


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
