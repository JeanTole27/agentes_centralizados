"""Integración real con capa-media-ops (en su modo simulación) a través del puente."""
import pytest

from opscentral import nucleo
from opscentral.cm.adaptador import referencia_cmops, rol_cmops
from opscentral.workers import Worker


def test_rol_que_recibe_cmops(api_n):
    cat = api_n.catalogo
    assert rol_cmops(cat["orq.status"], "PROD") == "consulta"
    assert rol_cmops(cat["orq.stop"], "DESA") == "operador"
    assert rol_cmops(cat["orq.stop"], "PROD") == "admin"


def test_orq_status_por_el_puente(cfg, api_n, usuarios):
    cm_n = nucleo.crear("worker-cm", cfg, almacen=api_n.almacen)
    w = Worker("cm", cm_n)
    if not (w.motor_cm.ruta / "cmops" / "trabajos.py").exists():
        pytest.skip("capa-media-ops no está junto a ops-central")
    e = api_n.ejecuciones.solicitar(usuarios["cm"], "sys.conectividad", {"host": "WLPC02SBCC04"})
    assert w.una_vez()
    r = api_n.ejecuciones.obtener(e["id"])
    lineas = [l["texto"] for l in api_n.ejecuciones.lineas(e["id"])]
    assert r["estado"] == "exitosa", (r["error"], lineas[-10:])
    assert r["resultado"]["estado"] == "exitoso"


def test_dba_no_opera_weblogic(api_n, usuarios):
    with pytest.raises(Exception, match="perfil|lectura"):
        api_n.ejecuciones.solicitar(usuarios["dba"], "orq.stop", {"host": "WLPC02SBCC04", "dominios": "simondesa"})


def test_referencia_para_cmops_sin_caso():
    """cmops exige referencia en PROD: sin caso se usa el id de la ejecución."""
    assert referencia_cmops({"id": "20261110120000abcdef", "cambio": None}) == "OPS-20261110120000abcdef"
    assert referencia_cmops({"id": "x", "cambio": "OPS-77"}) == "OPS-77"


def test_clase_c_de_capa_media_sin_caso_ni_motivo(api_n, usuarios):
    """Demo: ni caso ni motivo son obligatorios para ejecutar una acción de Capa Media de clase C."""
    from opscentral.autorizacion import decidir
    from .conftest import con_mfa
    cm = con_mfa(usuarios["cm"]) if "cm" in usuarios else con_mfa(usuarios["lid"])
    e = api_n.ejecuciones.solicitar(cm, "frm.mitigar", {"host": "AP17SBCC04"})
    assert e["cambio"] is None and e["motivo"] is None and e["estado"] in ("en_cola", "pendiente_aprobacion")
    d = decidir(cm, api_n.catalogo["orq.stop"], "PROD", ejecucion_directa=True)
    assert "motivo obligatorio" not in d.controles
    assert "motivo obligatorio" in decidir(cm, api_n.catalogo["orq.stop"], "PROD", ejecucion_directa=True,
                                           motivo_obligatorio_c=True).controles


def test_opciones_de_capa_media_salen_del_inventario_de_ops_central(tmp_path):
    import json
    from opscentral.cm.opciones import opciones, servidores_desde_json
    (tmp_path / "inventario.json").write_text(json.dumps({
        "servidores": [{"id": "WL1", "ip": "10.0.0.1", "ambiente": "DESA", "equipo": "cm"},
                       {"id": "AP07", "ip": "10.0.0.7", "ambiente": "PROD", "equipo": "cm"},
                       {"id": "AP104", "ip": "10.0.0.4", "ambiente": "PROD", "equipo": "cm"},
                       {"id": "BD-1", "ip": "10.0.0.9", "ambiente": "PROD", "equipo": "dba"}],
        "dominios": [{"id": "otro", "nombre": "otro", "servidor": "WL1"},
                     {"id": "IPM Stellent", "nombre": "IPM Stellent", "servidor": "AP07"},
                     {"id": "CONCISO/CONCILIA", "nombre": "CONCISO/CONCILIA", "servidor": "AP07"},
                     {"id": "ATGCPROD", "nombre": "ATGCPROD", "servidores": ["AP104"]},
                     {"id": "tercerosprod", "nombre": "tercerosprod", "servidores": ["AP104"]}]}), encoding="utf-8")
    srv = servidores_desde_json(tmp_path / "inventario.json")
    assert set(srv) == {"WL1", "AP07", "AP104"} and srv["AP07"]["ip"] == "10.0.0.7"   # sin servidores de BD
    (tmp_path / "inventario").mkdir()
    (tmp_path / "inventario" / "configuracion_modulos.json").write_text(json.dumps({
        "orquestacion": {"hosts": {"WL1": {"instalaciones": [
            {"id": "wl14", "usuario_so": "wlogic14c", "dominios": [{"nombre": "ARL_Web"}, {"nombre": "Externos"}]},
            {"id": "wl12", "usuario_so": "wlogic12c", "dominios": []}]}}},
        "usuarios_wl": {"dominios": {"ATGCPROD": {"host": "AP104", "admin_usuario": "no-sale"}}},
        "frmweb": {"hosts": {"AP17": {"managed_servers": ["WLS_FORMS1_1"], "descripcion": "TRON"}}}}), encoding="utf-8")
    o = opciones(tmp_path, lambda h: {"WL1": "DESA"}.get(h, ""), srv)
    orq = {h["host"]: h for h in o["orquestacion"]}
    assert set(orq) == {"AP07", "AP104", "WL1"}
    assert orq["WL1"]["configurado"] and orq["WL1"]["ambiente"] == "DESA"
    assert orq["WL1"]["instalaciones"][0]["dominios"] == [{"id": "ARL_Web", "nombre": "ARL_Web"},
                                                          {"id": "Externos", "nombre": "Externos"}]
    assert not orq["AP07"]["configurado"] and orq["AP07"]["ambiente"] == "PROD"
    assert orq["AP07"]["instalaciones"][0]["dominios"] == [{"id": "IPM_Stellent", "nombre": "IPM Stellent"},
                                                           {"id": "CONCISO_CONCILIA", "nombre": "CONCISO/CONCILIA"}]
    usr = {d["dominio"]: d for d in o["usuarios_wl"]}
    assert usr["ATGCPROD"]["configurado"] and not usr["tercerosprod"]["configurado"] and "IPM_Stellent" in usr
    assert {h["host"] for h in o["frmweb"]} == {"AP07", "AP104", "AP17", "WL1"}
    assert "no-sale" not in json.dumps(o) and "config/inventario.json" in o["fuente"]


def test_completar_configuracion_solo_en_simulacion():
    from opscentral.cm.opciones import completar_configuracion
    srv = {"AP07": {"ip": "10.0.0.7", "dominios": [{"nombre": "IPM Stellent"}]}}
    m = {}
    aviso = completar_configuracion(m, srv, "orq.stop", {"host": "AP07"}, {"modo_simulacion": True})
    assert "estándar" in aviso and m["orquestacion"]["hosts"]["AP07"]["instalaciones"][0]["dominios"] == [{"nombre": "IPM_Stellent"}]
    assert completar_configuracion(m, srv, "orq.stop", {"host": "AP07"}, {"modo_simulacion": True}) == ""  # ya está
    completar_configuracion(m, srv, "usr.listar", {"host": "AP07", "dominio": "IPM_Stellent"}, {"modo_simulacion": True})
    assert m["usuarios_wl"]["dominios"]["IPM_Stellent"]["admin_ip"] == "10.0.0.7"
    with pytest.raises(ValueError, match="inventario de Ops Central"):
        completar_configuracion({}, srv, "orq.stop", {"host": "NO-EXISTE"}, {"modo_simulacion": True})
    with pytest.raises(ValueError, match="Recolectar"):
        completar_configuracion({}, srv, "frm.validar", {"host": "AP07"}, {"modo_simulacion": False})


@pytest.mark.parametrize("accion,params", [
    ("orq.stop", {"host": "WLPC02SBCC04", "instalacion": "wlogic14c", "dominios": "ARL_Web,saluddesa"}),
    ("orq.status", {"host": "WLPC02SBCC04", "instalacion": "wlogic14c"}),
    ("usr.aplicar", {"host": "AP104SBCC04", "dominio": "ATGCPROD",
                     "acciones": [{"accion": "ADMIN", "usuario": "jperez"}, {"accion": "GRUPO", "usuario": "ana", "grupo": "Monitors"}]}),
    ("frm.mitigar", {"host": "AP17SBCC04", "lote": 10}),
    # Del inventario general, sin configuración por módulo en cmops:
    ("orq.stop", {"host": "AP05SBCC01", "instalacion": "principal", "dominios": "serviciosdesa,externosdesa"}),
    ("usr.listar", {"host": "AP104SBCC04", "dominio": "tercerosprod"}),
    ("frm.validar", {"host": "AP05SBCC01"}),
])
def test_acciones_de_capa_media_con_las_opciones_de_cmops_sin_caso(cfg, api_n, usuarios, accion, params):
    """Lo que manda la pantalla (instalación, dominios, filas, lote) llega a cmops y corre, sin caso ni motivo."""
    from .conftest import con_mfa
    cm_n = nucleo.crear("worker-cm", cfg, almacen=api_n.almacen)
    w = Worker("cm", cm_n)
    if not (w.motor_cm.ruta / "cmops" / "trabajos.py").exists():
        pytest.skip("capa-media-ops no está junto a ops-central")
    e = api_n.ejecuciones.solicitar(con_mfa(usuarios["cm"]), accion, params)
    assert e["cambio"] is None and e["motivo"] is None
    assert w.una_vez()
    r = api_n.ejecuciones.obtener(e["id"])
    lineas = [l["texto"] for l in api_n.ejecuciones.lineas(e["id"])]
    # cmops aceptó los parámetros y corrió el trabajo. En simulación algunos dominios no tienen credenciales de
    # Node Manager y los usuarios inventados no existen en el realm: el trabajo puede terminar con rc=4.
    assert r["estado"] == "exitosa" or (r["error"] or "").startswith("ErrorCM: trabajo"), (r["error"], lineas[-8:])
    texto = "\n".join(lineas)
    if accion == "usr.aplicar":
        assert "jperez" in texto and "Monitors" in texto
    if accion == "orq.stop" and params["host"] == "WLPC02SBCC04":
        assert "ARL_Web" in texto and "saluddesa" in texto
    if params["host"] == "AP05SBCC01" or params.get("dominio") == "tercerosprod":
        assert "configuración detallada" in texto or "configuración frmweb" in texto
    if accion == "orq.stop" and params["host"] == "AP05SBCC01":
        assert "serviciosdesa" in texto and "externosdesa" in texto


def test_sin_carpeta_capa_media_ops_hay_opciones_y_aviso(tmp_path, cfg):
    from opscentral.cm.opciones import opciones, servidores_desde_json
    o = opciones(tmp_path / "no-existe", lambda h: "", servidores_desde_json(cfg.ruta("inventario", "archivo")))
    assert o["orquestacion"] and o["frmweb"] and o["usuarios_wl"]
    assert o["cmops_disponible"] is False and "no-existe" in o["aviso"] and "ruta_cmops" in o["aviso"]


def test_cmops_recibe_los_servidores_del_inventario_de_ops_central():
    """No hay un segundo inventario: la copia interna de capa-media-ops ya no trae servidores.json."""
    from opscentral.config import CMOPS_INTERNO
    assert not (CMOPS_INTERNO / "inventario" / "servidores.json").exists()
    assert (CMOPS_INTERNO / "inventario" / "configuracion_modulos.json").exists()
