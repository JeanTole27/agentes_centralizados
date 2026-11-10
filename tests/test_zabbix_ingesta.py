"""Integración con la API de Zabbix: cliente, catálogo de métricas, trends de capacidad y respaldo de problemas."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from opscentral import estado
from opscentral.almacen import episodio, evento_zbx, prediccion, serie_punto
from opscentral.api import crear_app
from opscentral.ml.servicio import scoring
from opscentral.planificador import TAREAS
from opscentral.workers import Worker
from opscentral.zabbix import ingesta, metricas

from .dobles import zabbix_falso as simulador
from opscentral.zabbix.cliente import ClienteZabbix, ErrorZabbix, VersionNoSoportada

H = {"X-Ops-Central": "1"}


# ---------------------------------------------------------------- cliente
def _rpc(version, registro):
    def responder(req: httpx.Request):
        d = json.loads(req.content)
        registro.append((d["method"], dict(req.headers), d))
        res = version if d["method"] == "apiinfo.version" else []
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": res, "id": d["id"]})
    return httpx.MockTransport(responder)


def test_token_va_en_bearer_desde_6_4_y_en_el_campo_auth_en_6_0():
    for version, bearer in (("7.0.5", True), ("6.0.30", False)):
        reg = []
        c = ClienteZabbix("https://zbx.local", "tok", transporte=_rpc(version, reg), espera_base=0)
        c.llamar("host.get", {})
        _, cab, cuerpo = reg[-1]
        assert c.url == "https://zbx.local/api_jsonrpc.php"
        assert (cab.get("authorization") == "Bearer tok") is bearer
        assert ("auth" in cuerpo) is (not bearer)
        assert "authorization" not in reg[0][1] and "auth" not in reg[0][2]   # apiinfo.version va sin credencial


def test_version_antigua_se_rechaza():
    c = ClienteZabbix("https://zbx.local", "tok", transporte=_rpc("5.4.0", []), espera_base=0)
    with pytest.raises(VersionNoSoportada):
        c.conectar()


def test_reintenta_errores_5xx_y_reporta_errores_de_la_api():
    n = {"i": 0}

    def resp(req):
        d = json.loads(req.content)
        n["i"] += 1
        if d["method"] == "apiinfo.version":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "7.0.0", "id": d["id"]})
        if n["i"] < 4:
            return httpx.Response(502)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": d["id"],
                                         "error": {"code": -32500, "message": "Application error.",
                                                   "data": "No permissions to referred object."}})
    c = ClienteZabbix("https://zbx.local", "tok", transporte=httpx.MockTransport(resp), espera_base=0)
    with pytest.raises(ErrorZabbix) as e:
        c.llamar("host.get", {})
    assert e.value.es_permiso and n["i"] == 4      # versión + 2 intentos con 502 + 1 con error de la API


# ---------------------------------------------------------------- catálogo de métricas
@pytest.mark.parametrize("key,serie,v_in,v_out", [
    ("vfs.fs.size[/u01,pused]", "fs:H1:/u01", 80, 80),
    ('vfs.fs.dependent.size["C:",pused]', "fs:H1:C:", 50, 50),
    ("vm.memory.utilization", "mem:H1", 70, 70),
    ("vm.memory.size[pavailable]", "mem:H1", 30, 70),
    ("system.cpu.util[,idle]", "cpu:H1", 90, 10),
])
def test_catalogo_reconoce_keys_de_las_plantillas(key, serie, v_in, v_out):
    s, m = metricas.serie_para("H1", key)
    assert s == serie and metricas.valor(m, v_in) == v_out


def test_catalogo_ignora_lo_que_no_es_capacidad():
    for key in ("vfs.fs.size[/u01,total]", "net.if.in[eth0]", "vfs.fs.size[/dev/shm,pused]", "agent.ping"):
        assert metricas.serie_para("H1", key) is None


# ---------------------------------------------------------------- trends de capacidad
def _cli(n):
    return ingesta.crear_cliente(n.cfg, n.secretos, n.inventario)


def _puntos(alm, serie):
    with alm.tx() as con:
        return con.execute(select(func.count()).where(serie_punto.c.serie == serie)).scalar()


def test_trends_de_capacidad_llegan_a_las_series_y_al_modelo(cfg, ing_n):
    alm = ing_n.almacen
    r = ingesta.recolectar_capacidad(alm, ing_n.inventario, _cli(ing_n), cfg)
    assert r["version"] == simulador.VERSION and r["faltan_en_zabbix"] == []
    nombres = {s["serie"] for s in r["series"]}
    assert {"fs:AP23SBCC04:/oracle", "mem:AP23SBCC04", "cpu:AP23SBCC04", "fs:DBPRD01:/u02"} <= nombres
    assert _puntos(alm, "fs:AP23SBCC04:/oracle") >= 24 * 29

    # incremental: la segunda pasada no duplica horas
    r2 = ingesta.recolectar_capacidad(alm, ing_n.inventario, _cli(ing_n), cfg)
    assert r2["puntos_nuevos"] <= len(r2["series"])

    scoring(alm, cfg)
    with alm.tx() as con:
        pred = con.execute(select(prediccion.c.datos).where(prediccion.c.serie == "fs:AP23SBCC04:/oracle",
                                                            prediccion.c.vigente.is_(True))).scalar()
    dias = pred["umbrales"]["100"]["dias"]
    assert 10 < dias < 30                       # /oracle crece ~0,9 %/día desde 82 %
    riesgos = estado.leer(alm, "ml:riesgos")["datos"]["riesgos"]
    assert any(x["serie"] == "fs:AP23SBCC04:/oracle" for x in riesgos)
    assert not any(x["serie"].startswith(("cpu:", "mem:")) for x in riesgos)   # sin "fecha de llenado"


def test_worker_de_ingesta_pide_scoring_cuando_hay_datos_nuevos(cfg, ing_n):
    w = Worker("ingesta", ing_n)
    ing_n.cola.encolar("ingesta", "zbx_capacidad", {})
    assert w.una_vez()
    assert Worker("ml", ing_n).una_vez()        # el scoring quedó en cola
    assert estado.leer(ing_n.almacen, "zbx:capacidad")["datos"]["puntos_nuevos"] > 0
    assert TAREAS["zbx_capacidad"][1:] == ("ingesta", "zbx_capacidad")
    assert TAREAS["zbx_problemas"] == (300, "ingesta", "zbx_problemas")


# ---------------------------------------------------------------- problemas (respaldo del webhook)
def _eventos(alm):
    with alm.tx() as con:
        return {f.eventid: f for f in con.execute(select(evento_zbx))}


def test_respaldo_de_problemas_crea_episodios_y_cierra_los_recuperados(cfg, ing_n):
    alm, inv = ing_n.almacen, ing_n.inventario
    r = ingesta.sincronizar_problemas(alm, ing_n.cola, inv, _cli(ing_n), cfg)
    assert (r["abiertos"], r["nuevos"], r["recuperados"]) == (3, 3, 0)
    ev = _eventos(alm)
    assert ev["9001001"].activo == "CORPRD" and ev["9001001"].tipo_activo == "base"
    assert ev["9001002"].activo == "AP23SBCC09"
    # AP23SBCC09 → creditoprd → CORPRD (2 saltos) y 15 min de diferencia: mismo episodio, causa probable la base
    assert ev["9001001"].episodio_id == ev["9001002"].episodio_id
    with alm.tx() as con:
        assert con.execute(select(episodio.c.activo_raiz).where(
            episodio.c.id == ev["9001001"].episodio_id)).scalar() == "base:CORPRD"
    assert ev["9001003"].en_mantenimiento and ev["9001003"].episodio_id is None   # suppressed → sin episodio
    assert {"evento": "9001001", "accion": "bd.salud.tablespaces"} in r["sugerencias"]
    assert {"evento": "9001002", "accion": "cm.stuck_threads"} in r["sugerencias"]

    # idempotente: la segunda pasada no crea nada
    assert ingesta.sincronizar_problemas(alm, ing_n.cola, inv, _cli(ing_n), cfg)["nuevos"] == 0

    # se pierde el webhook de recuperación: la sincronización lo cierra
    simulador.instancia([]).resolver("9001001")
    r = ingesta.sincronizar_problemas(alm, ing_n.cola, inv, _cli(ing_n), cfg)
    assert r["recuperados"] == 1
    ev = _eventos(alm)
    assert ev["9001001"].estado == "RESOLVED" and ev["9001001"].fin is not None
    ep = ev["9001001"].episodio_id

    def estado_ep():
        with alm.tx() as con:
            return con.execute(select(episodio.c.estado).where(episodio.c.id == ep)).scalar()
    assert estado_ep() == "abierto"             # sigue abierto: el stuck threads del mismo episodio no se resolvió
    simulador.instancia([]).resolver("9001002")
    ingesta.sincronizar_problemas(alm, ing_n.cola, inv, _cli(ing_n), cfg)
    assert estado_ep() == "cerrado"


def test_estado_de_zabbix_en_la_api(cfg, api_n, ing_n):
    w = Worker("ingesta", ing_n)
    ing_n.cola.encolar("ingesta", "zbx_problemas", {})
    w.una_vez()
    c = TestClient(crear_app(cfg, api_n))
    c.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=H)
    d = c.get("/api/zabbix/estado").json()
    assert d["problemas"]["datos"]["abiertos"] == 3 and d["problemas"]["error"] is None
    assert d["capacidad"] is None
    assert len(c.get("/api/alertas/episodios").json()) == 1   # tablespace + stuck threads correlacionados


def test_error_de_zabbix_queda_registrado_sin_borrar_el_ultimo_dato(cfg, ing_n, monkeypatch):
    w = Worker("ingesta", ing_n)
    ing_n.cola.encolar("ingesta", "zbx_problemas", {})
    w.una_vez()

    def falla(*a, **k):
        raise ErrorZabbix("Zabbix no respondió tras 3 intentos (problem.get)")
    monkeypatch.setattr(ingesta, "sincronizar_problemas", falla)
    ing_n.cola.encolar("ingesta", "zbx_problemas", {})
    w.una_vez()
    f = estado.leer(ing_n.almacen, "zbx:problemas")
    assert "no respondió" in f["error"] and f["datos"]["abiertos"] == 3


def test_prueba_de_conexion(cfg, ing_n):
    r = ingesta.probar(cfg, ing_n.secretos, ing_n.inventario)
    assert r["version"] == simulador.VERSION and r["autenticacion"] == "token de API vía header Bearer"
    assert r["faltan"] == [] and r["encontrados"] == r["hosts_inventario"] == 8


def test_filesystems_de_zabbix_van_al_equipo_duenio_del_servidor(cfg, api_n, ing_n):
    ingesta.recolectar_capacidad(ing_n.almacen, ing_n.inventario, _cli(ing_n), cfg)
    scoring(ing_n.almacen, cfg)
    c = TestClient(crear_app(cfg, api_n))
    c.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=H)
    bd = {p["serie"] for p in c.get("/api/prevencion/predicciones?equipo=bd").json()["predicciones"]}
    cm = {p["serie"] for p in c.get("/api/prevencion/predicciones?equipo=cm").json()["predicciones"]}
    assert "fs:DBPRD01:/u02" in bd and "fs:DBPRD01:/u02" not in cm       # DBPRD01 es del equipo dba
    assert "fs:AP23SBCC04:/oracle" in cm and "fs:AP23SBCC04:/oracle" not in bd
