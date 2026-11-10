import json
import time

import pytest
from fastapi.testclient import TestClient

from opscentral.api import crear_app
from opscentral.workers import Worker
from opscentral.zabbix.webhook import firmar

H = {"X-Ops-Central": "1"}


@pytest.fixture
def app(cfg, api_n):
    return crear_app(cfg, api_n)


def cliente(app, login):
    c = TestClient(app)
    r = c.post("/api/auth/dev/ingresar", json={"login": login}, headers=H)
    assert r.status_code == 200, r.text
    return c


def test_ingreso_y_menu_por_perfil(app):
    assert TestClient(app).get("/api/auth/yo").status_code == 401
    c = cliente(app, "cm.demo")
    yo = c.get("/api/auth/yo").json()
    assert yo["perfiles"] == ["cm"]
    pantallas = {p["id"]: p["solo_lectura"] for g in yo["menu"] for p in g["pantallas"]}
    assert pantallas["bloqueos"] is True and pantallas["orquestacion"] is False
    assert "snapshot" not in pantallas and "admin" not in pantallas
    r = TestClient(app).post("/api/auth/dev/ingresar", json={"login": "intruso"}, headers=H)
    assert r.status_code == 403


def test_csrf_obligatorio(app):
    c = cliente(app, "dba.demo")
    r = c.post("/api/acciones/bd.ses.bloqueos/ejecuciones", json={"params": {"base": "CORPRD"}})
    assert r.status_code == 403


def test_solicitud_permisos_y_segundo_factor(app):
    dba = cliente(app, "dba.demo")
    r = dba.post("/api/acciones/bd.ses.bloqueos/ejecuciones", json={"params": {"base": "CORPRD"}}, headers=H)
    assert r.status_code == 202 and r.json()["estado"] == "en_cola"

    cm = cliente(app, "cm.demo")
    r = cm.post("/api/acciones/bd.ses.terminar/ejecuciones", headers=H, json={
        "params": {"base": "CORPRD", "sid": 1234, "serial": 5678, "motivo": "lo intenta Capa Media"}, "cambio": "CHG777"})
    assert r.status_code == 403

    prod = {"params": {"base": "CORPRD", "sid": 1234, "serial": 5678, "motivo": "Batch colgado en cierre"}}
    # Con permiso: ejecución directa (sin cola de aprobación), sin caso obligatorio, con segundo factor en PROD
    ev = dba.post("/api/acciones/bd.ses.terminar/evaluar", json=prod, headers=H).json()
    assert not ev["requiere_aprobacion"] and ev["ejecucion_directa"] and ev["requiere_segundo_factor"]
    assert dba.post("/api/acciones/bd.ses.terminar/ejecuciones", json=prod, headers=H).status_code == 428
    dba.post("/api/auth/dev/segundo-factor", headers=H)
    r = dba.post("/api/acciones/bd.ses.terminar/ejecuciones", json=prod, headers=H)
    assert r.status_code == 202 and r.json()["estado"] == "en_cola" and "token_aprobacion" not in r.json()
    assert "Ejecución directa" in r.json()["comentario"]
    dba.post(f"/api/ejecuciones/{r.json()['id']}/cancelar", headers=H)

    # El caso es opcional; si se envía, queda en la ejecución
    r = dba.post("/api/acciones/bd.ses.terminar/ejecuciones", json={**prod, "cambio": "OPS-123"}, headers=H)
    assert r.status_code == 202 and r.json()["cambio"] == "OPS-123"
    dba.post(f"/api/ejecuciones/{r.json()['id']}/cancelar", headers=H)
    assert dba.post("/api/acciones/bd.ses.terminar/ejecuciones", json={**prod, "cambio": "x"},
                    headers=H).status_code == 422                       # referencia inválida (muy corta)


def test_cuatro_ojos_si_se_apaga_la_ejecucion_directa(cfg, api_n):
    from opscentral import config
    cfg2 = config.Config(config._fusionar(cfg, {"aprobaciones": {"ejecucion_directa": False}}))
    api_n.cfg = api_n.ejecuciones.cfg = cfg2
    app = crear_app(cfg2, api_n)
    prod = {"params": {"base": "CORPRD", "sid": 1234, "serial": 5678, "motivo": "Batch colgado en cierre"}}
    lider, dba = cliente(app, "lider.demo"), cliente(app, "dba.demo")
    assert "aprobaciones" in {p["id"] for g in lider.get("/api/auth/yo").json()["menu"] for p in g["pantallas"]}
    lider.post("/api/auth/dev/segundo-factor", headers=H)
    ev = lider.post("/api/acciones/bd.ses.terminar/evaluar", json=prod, headers=H).json()
    assert ev["requiere_aprobacion"] and not ev["ejecucion_directa"]
    r = lider.post("/api/acciones/bd.ses.terminar/ejecuciones", json=prod, headers=H)
    assert r.status_code == 202 and r.json()["estado"] == "pendiente_aprobacion"
    eid = r.json()["id"]
    dba.post("/api/auth/dev/segundo-factor", headers=H)
    assert [p["id"] for p in dba.get("/api/aprobaciones/pendientes").json()] == [eid]
    assert lider.get("/api/aprobaciones/pendientes").json() == []        # no ve la propia
    r = dba.post(f"/api/aprobaciones/{eid}/aprobar", json={"comentario": "ok"}, headers=H)
    assert r.status_code == 200 and r.json()["estado"] == "en_cola"
    assert "token_aprobacion" not in r.json()


def test_registro_demo_con_perfil(app):
    c = TestClient(app)
    perfiles = [p["id"] for p in c.get("/api/auth/demo/perfiles").json()]
    assert perfiles == ["cm", "dba", "adm", "aut"]
    assert c.post("/api/auth/demo/registrar", json={"nombre": "Ana", "perfil": "lid"}, headers=H).status_code == 422
    r = c.post("/api/auth/demo/registrar", json={"nombre": "María José Pérez", "perfil": "cm"}, headers=H)
    assert r.status_code == 200 and r.json()["login"] == "maria.jose.perez" and r.json()["perfiles"] == ["cm"]
    pantallas = {p["id"] for g in r.json()["menu"] for p in g["pantallas"]}
    assert "aprobaciones" not in pantallas and "orquestacion" in pantallas      # sin cola de aprobación
    # vuelve a entrar con otro perfil: se actualiza
    r = c.post("/api/auth/demo/registrar", json={"nombre": "María José Pérez", "perfil": "dba"}, headers=H)
    assert r.json()["perfiles"] == ["dba"]
    assert [u["login"] for u in c.get("/api/auth/demo/usuarios").json()] == ["maria.jose.perez"]


def test_automatizador_ejecuta_y_edita_todo(app):
    c = TestClient(app)
    yo = c.post("/api/auth/demo/registrar", json={"nombre": "Bot Automatizador", "perfil": "aut"}, headers=H).json()
    pantallas = {p["id"]: p["solo_lectura"] for g in yo["menu"] for p in g["pantallas"]}
    assert pantallas and not any(pantallas.values())                       # todo con edición
    assert {"orquestacion", "snapshot", "admin", "ejecuciones"} <= set(pantallas)
    cat = c.get("/api/catalogo").json()
    assert cat and all(a["permiso"] == "ejecuta" for a in cat)
    # ejecuta en BD y en Capa Media sin cola de aprobación
    r = c.post("/api/acciones/bd.ses.bloqueos/ejecuciones", json={"params": {"base": "CORPRD"}}, headers=H)
    assert r.status_code == 202 and r.json()["estado"] == "en_cola"
    ev = c.post("/api/acciones/orq.stop/evaluar", headers=H,
                json={"params": {"host": "WLPC02SBCC04", "dominios": "simondesa"}}).json()
    assert ev["permitido"] and not ev["requiere_aprobacion"]
    assert c.get("/api/auditoria/verificar").json()["integra"] is True


def test_consulta_no_ejecuta_pero_lee(app, cfg, api_n):
    dba = cliente(app, "dba.demo")
    eid = dba.post("/api/acciones/bd.ses.bloqueos/ejecuciones", json={"params": {"base": "CORPRD"}},
                   headers=H).json()["id"]
    from opscentral import nucleo
    w = Worker("bd", nucleo.crear("worker-bd", cfg, almacen=api_n.almacen))
    w.una_vez()
    con = cliente(app, "consulta.demo")
    assert con.post("/api/acciones/bd.ses.bloqueos/ejecuciones", json={"params": {"base": "CORPRD"}},
                    headers=H).status_code == 403
    assert con.get(f"/api/ejecuciones/{eid}").status_code == 200
    r = con.get("/api/estado/bd/CORPRD/bloqueos")
    assert r.status_code in (200, 404)
    lineas = dba.get(f"/api/ejecuciones/{eid}/lineas").json()
    assert lineas and "OPS_CONSULTA" in lineas[0]["texto"]
    ev = dba.get(f"/api/ejecuciones/{eid}/evidencia").json()
    assert ev["integra"] is True


def test_refresco_con_limite(app):
    dba = cliente(app, "dba.demo")
    r1 = dba.post("/api/refresco", json={"objeto": "bd:CORPRD"}, headers=H).json()
    r2 = dba.post("/api/refresco", json={"objeto": "bd:CORPRD"}, headers=H).json()
    assert r1["estado"] == "encolado" and r2["estado"] == "reciente" and r2["reintentar_en_s"] > 0
    assert cliente(app, "consulta.demo").post("/api/refresco", json={"objeto": "bd:CORPRD"},
                                              headers=H).status_code == 403


def test_webhook_zabbix_firma_y_episodio(app, cfg, api_n):
    c = TestClient(app)
    secreto = api_n.secretos.leer(cfg["zabbix"]["secreto_webhook_ref"])["clave"]

    def enviar(d, secreto_=secreto, desfase=0):
        cuerpo = json.dumps(d).encode()
        t = str(int(time.time()) + desfase)
        return c.post("/api/zabbix/webhook", content=cuerpo, headers={
            "Content-Type": "application/json", "X-OpsCentral-Tiempo": t,
            "X-OpsCentral-Firma": firmar(secreto_, t, cuerpo)})

    base = {"event_value": "1", "severity": 4, "clock": int(time.time())}
    assert enviar({**base, "eventid": "1", "host": "CORPRD", "name": "x"}, secreto_="malo").status_code == 401
    assert enviar({**base, "eventid": "1", "host": "CORPRD", "name": "x"}, desfase=-900).status_code == 401
    r1 = enviar({**base, "eventid": "100", "host": "CORPRD", "name": "Oracle: sesiones en lock"}).json()
    r2 = enviar({**base, "eventid": "101", "host": "AP23SBCC04", "name": "Stuck threads en clientesprd"}).json()
    assert r1["nuevo"] and r1["sugerencia"] == "bd.ses.bloqueos" and r1["refresco"]["estado"] == "encolado"
    assert r2["episodio"] == r1["episodio"] and not r2["nuevo"]          # mismo episodio por el grafo
    eps = cliente(app, "consulta.demo").get("/api/alertas/episodios").json()
    assert len(eps) == 1 and eps[0]["activo_raiz"] == "base:CORPRD" and len(eps[0]["eventos"]) == 2
    enviar({**base, "eventid": "100", "event_value": "0", "host": "CORPRD", "name": "x"})
    enviar({**base, "eventid": "101", "event_value": "0", "host": "AP23SBCC04", "name": "x"})
    assert cliente(app, "dba.demo").get("/api/alertas/episodios").json() == []


def test_auditoria_solo_lider_o_admin(app):
    assert cliente(app, "dba.demo").get("/api/auditoria/verificar").status_code == 403
    r = cliente(app, "lider.demo").get("/api/auditoria/verificar").json()
    assert r["integra"] is True
