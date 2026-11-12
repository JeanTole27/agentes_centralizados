"""Ventana de cambio (com.vc.*) e Incidentes y cambios (com.itsm.*)."""
from datetime import timedelta

from fastapi.testclient import TestClient

from opscentral.almacen import ejecucion, utc
from opscentral.api import crear_app

H = {"X-Ops-Central": "1"}


def _cliente(cfg, api_n, login):
    c = TestClient(crear_app(cfg, api_n))
    assert c.post("/api/auth/dev/ingresar", json={"login": login}, headers=H).status_code == 200
    return c


def _ejecucion(api_n, eid, accion, cambio, estado="exitosa", quien="dba.demo", motor="bd", destino="CORPRD"):
    ahora = utc()
    with api_n.almacen.tx() as con:
        con.execute(ejecucion.insert().values(
            id=eid, accion=accion, motor=motor, clase="L", destino=destino, ambiente="PROD", params={},
            cambio=cambio, solicitante=quien, estado=estado, huella="x", creada=ahora - timedelta(minutes=1),
            actualizada=ahora, origen="persona"))


def _abrir(c, **kw):
    cuerpo = {"cambio": "CHG0012345", "plantilla": "despliegue_bd", "ambiente": "PROD", "base": "CORPRD", **kw}
    return c.post("/api/ventanas", json=cuerpo, headers=H)


def test_ventana_flujo_completo_en_orden(cfg, api_n):
    dba = _cliente(cfg, api_n, "dba.demo")
    r = _abrir(dba)
    assert r.status_code == 200, r.text
    v = r.json()
    assert [p["accion"] for p in v["pasos"]] == ["bd.snap.tomar", "manual", "bd.snap.tomar", "bd.snap.comparar",
                                                 "bd.inv.recompilar"]
    assert v["pasos"][0]["params"] == {"base": "CORPRD", "cambio": "CHG0012345", "momento": "ANTES"}
    vid = v["id"]
    # no se abre otra ventana en curso para el mismo cambio
    assert _abrir(dba).status_code == 422

    # paso 1: una ejecución fallida no lo completa; una exitosa sí
    _ejecucion(api_n, "E1", "bd.snap.tomar", "CHG0012345", estado="fallida")
    r = dba.post(f"/api/ventanas/{vid}/pasos/1/vincular", json={"ejecucion_id": "E1"}, headers=H).json()
    assert r["pasos"][0]["estado"] == "pendiente" and "fallida" in r["pasos"][0]["comentario"]
    _ejecucion(api_n, "E2", "bd.snap.tomar", "OTRO-1")
    assert dba.post(f"/api/ventanas/{vid}/pasos/1/vincular", json={"ejecucion_id": "E2"}, headers=H).status_code == 422
    _ejecucion(api_n, "E3", "bd.snap.tomar", "CHG0012345")
    r = dba.post(f"/api/ventanas/{vid}/pasos/1/vincular", json={"ejecucion_id": "E3"}, headers=H).json()
    assert r["pasos"][0]["estado"] == "hecho" and r["paso_actual"] == 2

    # los pasos van en orden
    assert dba.post(f"/api/ventanas/{vid}/pasos/4/marcar", json={"estado": "omitido", "nota": "x"},
                    headers=H).status_code == 422
    # paso 2 (manual) lo marca BD; Capa Media no puede
    cm = _cliente(cfg, api_n, "cm.demo")
    assert cm.post(f"/api/ventanas/{vid}/pasos/2/marcar", json={"estado": "hecho"}, headers=H).status_code == 403
    r = dba.post(f"/api/ventanas/{vid}/pasos/2/marcar", json={"estado": "hecho", "nota": "deploy.log ok"},
                 headers=H).json()
    assert r["pasos"][1]["estado"] == "hecho" and r["pasos"][1]["hecho_por"] == "dba.demo"
    # omitir exige motivo
    assert dba.post(f"/api/ventanas/{vid}/pasos/3/marcar", json={"estado": "omitido"}, headers=H).status_code == 422
    dba.post(f"/api/ventanas/{vid}/pasos/3/marcar", json={"estado": "omitido", "nota": "sin cambios en BD"}, headers=H)

    # cerrar con pendientes exige motivo y queda incompleta
    assert dba.post(f"/api/ventanas/{vid}/cerrar", json={}, headers=H).status_code == 422
    r = dba.post(f"/api/ventanas/{vid}/cerrar", json={"nota": "se pospone la recompilación"}, headers=H).json()
    assert r["estado"] == "cerrada_incompleta" and "E3" in r["resumen"] and "se pospone" in r["resumen"]
    assert r["comentado_jira"] is False                     # sin Jira configurado
    assert dba.post(f"/api/ventanas/{vid}/pasos/4/marcar", json={"estado": "omitido", "nota": "x"},
                    headers=H).status_code == 422             # ya cerrada


def test_ventana_valida_plantilla_y_permisos(cfg, api_n):
    dba = _cliente(cfg, api_n, "dba.demo")
    assert _abrir(dba, plantilla="despliegue_bd_cm").status_code == 422          # pide servidores
    assert _abrir(dba, base="NO_EXISTE").status_code == 422
    assert _abrir(dba, cambio="").status_code == 422
    consulta = _cliente(cfg, api_n, "consulta.demo")
    assert consulta.get("/api/ventanas").status_code == 200                      # ve
    assert _abrir(consulta).status_code == 403                                   # no abre
    pls = dba.get("/api/ventanas/plantillas").json()
    assert {p["id"] for p in pls} == {"despliegue_bd_cm", "despliegue_bd", "reinicio_dominios"}


def test_incidentes_sin_jira_usa_las_referencias_de_ops_central(cfg, api_n):
    dba = _cliente(cfg, api_n, "dba.demo")
    _ejecucion(api_n, "E10", "bd.snap.tomar", "CHG0099", estado="fallida")
    _ejecucion(api_n, "E11", "bd.snap.comparar", "CHG0099")
    _abrir(dba, cambio="CHG0099", titulo="Despliegue de prueba")
    lista = dba.get("/api/incidentes").json()
    assert lista["jira"] is False and lista["casos"] == []
    r = next(x for x in lista["referencias"] if x["clave"] == "CHG0099")
    assert r["ejecuciones"] == 2 and r["fallidas"] == 1 and r["ventanas"] == 1 and r["titulo"] == "Despliegue de prueba"
    d = dba.get("/api/incidentes/CHG0099").json()
    assert {e["id"] for e in d["ejecuciones"]} == {"E10", "E11"}
    assert d["ventanas"][0]["cambio"] == "CHG0099"
    assert any(a["id"] == "CORPRD" and a["tipo"] == "base" for a in d["activos"])
    assert dba.get("/api/incidentes/NADA-1").status_code == 404
    # comentar un caso sin Jira configurado: 503 con el motivo
    assert dba.post("/api/casos/OPS-1/nota", json={"texto": "hola"}, headers=H).status_code == 503
