"""Integración real con capa-media-ops (en su modo simulación) a través del puente."""
import pytest

from opscentral import nucleo
from opscentral.cm.adaptador import rol_cmops
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
