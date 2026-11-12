"""Feedback de la demo: evidencia descargable del snapshot, Jira con escritura y CRUD del inventario."""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from opscentral.api import crear_app
from opscentral.workers import Worker

from .dobles.oracle_falso import simular_despliegue
from .test_casos_jira import ISSUE, SecretosFalsos

H = {"X-Ops-Central": "1"}


@pytest.fixture
def app(cfg, api_n):
    return crear_app(cfg, api_n)


def cliente(app, login):
    c = TestClient(app)
    assert c.post("/api/auth/dev/ingresar", json={"login": login}, headers=H).status_code == 200
    return c


def _snapshot_completo(api_n, bd_n, usuarios, base="CORTST", grupo="CHG2001"):
    w = Worker("bd", bd_n)
    ej, dba = api_n.ejecuciones, usuarios["dba"]
    for momento in ("ANTES", "DESPUES"):
        if momento == "DESPUES":
            simular_despliegue(base)
        ej.solicitar(dba, "bd.snap.tomar", {"base": base, "cambio": grupo, "momento": momento})
        while w.una_vez():
            pass
    ej.solicitar(dba, "bd.snap.comparar", {"base": base, "cambio": grupo})
    while w.una_vez():
        pass


# ---------------------------------------------------------------- evidencia del snapshot
def test_zip_de_evidencia_con_manifiesto(app, api_n, bd_n, usuarios):
    _snapshot_completo(api_n, bd_n, usuarios)
    r = cliente(app, "dba.demo").get("/api/estado/bd/CORTST/snapshot/CHG2001/evidencia")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "evidencia_CORTST_CHG2001_" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    nombres = set(z.namelist())
    assert {"evidencia.html", "comparacion.json", "ejecuciones.json", "MANIFIESTO.json",
            "snapshots/ANTES.json", "snapshots/DESPUES.json"} <= nombres
    man = json.loads(z.read("MANIFIESTO.json"))
    import hashlib
    for k, h in man["archivos"].items():
        assert hashlib.sha256(z.read(k)).hexdigest() == h
    assert "3 objeto(s) inválido(s) nuevo(s)" in z.read("evidencia.html").decode()
    ejs = json.loads(z.read("ejecuciones.json"))
    assert {e["accion"] for e in ejs} == {"bd.snap.tomar", "bd.snap.comparar"} and all(e["salida"] for e in ejs)


def test_zip_sin_snapshots_es_404(app):
    assert cliente(app, "dba.demo").get("/api/estado/bd/CORTST/snapshot/NOEXISTE/evidencia").status_code == 404


# ---------------------------------------------------------------- Jira con escritura
def _jira(app, peticiones):
    def manejar(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        if req.url.path.endswith("/attachments"):
            return httpx.Response(200, json=[{"id": "1", "filename": "x.zip"}])
        if req.url.path.endswith("/comment"):
            return httpx.Response(201, json={"id": "c1"})
        if req.url.path.endswith("/transitions") and req.method == "GET":
            return httpx.Response(200, json={"transitions": [{"id": "31", "name": "Resolver", "to": {"name": "Resuelto"}}]})
        if req.url.path.endswith("/transitions"):
            return httpx.Response(204)
        if req.url.path == "/rest/api/3/issue/OPS-123":
            return httpx.Response(200, json=ISSUE)
        return httpx.Response(404, json={"errorMessages": ["no"]})
    app.state.casos.c = {**app.state.casos.c, "url": "https://empresa.atlassian.net"}
    app.state.casos.sec = SecretosFalsos()
    app.state.casos._transporte = httpx.MockTransport(manejar)


def test_publicar_evidencia_y_transicion_en_jira(app, api_n, bd_n, usuarios):
    _snapshot_completo(api_n, bd_n, usuarios)
    pet = []
    _jira(app, pet)
    c = cliente(app, "dba.demo")
    r = c.post("/api/casos/ops-123/evidencia-snapshot", headers=H, json={"base": "CORTST", "grupo": "CHG2001"})
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == "OPS-123" and r.json()["adjunto"].endswith(".zip")
    adj = next(p for p in pet if p.url.path.endswith("/attachments"))
    assert adj.headers["x-atlassian-token"] == "no-check" and b"PK" in adj.content
    com = next(p for p in pet if p.url.path.endswith("/comment"))
    assert "objeto(s) inválido(s) nuevo(s)" in com.content.decode()
    assert c.get("/api/casos/OPS-123/transiciones").json() == [{"id": "31", "nombre": "Resolver", "a": "Resuelto"}]
    assert c.post("/api/casos/OPS-123/transicion", headers=H, json={"id": "99"}).status_code == 422
    assert c.post("/api/casos/OPS-123/transicion", headers=H, json={"id": "31"}).status_code == 200


def test_comentar_ejecucion_en_jira(app, api_n, bd_n, usuarios):
    _snapshot_completo(api_n, bd_n, usuarios)
    pet = []
    _jira(app, pet)
    c = cliente(app, "dba.demo")
    eid = c.get("/api/ejecuciones").json()[0]["id"]
    r = c.post("/api/casos/OPS-123/comentar", headers=H, json={"ejecucion_id": eid})
    assert r.status_code == 200 and "Ops Central" in pet[-1].content.decode()


# ---------------------------------------------------------------- CRUD del inventario
def test_crud_inventario_para_cualquier_perfil(app, api_n):
    c = cliente(app, "consulta.demo")                  # por ahora todos pueden editar (modo demo)
    assert c.get("/api/inventario").json()["editable"] is True
    r = c.post("/api/inventario/bases", headers=H, json={"id": "corqa", "datos": {
        "servidor": "DBTST01", "ambiente": "qa", "dsn": "dbtst01:1521/CORQA", "criticidad": "media"}})
    assert r.status_code == 201, r.text
    assert api_n.inventario.base("CORQA").ambiente == "QA"
    assert any(b["id"] == "CORQA" and b["_ajuste"] == "alta" for b in c.get("/api/inventario").json()["bases"])
    # editar: corregir ambiente y host de Zabbix
    r = c.put("/api/inventario/base/CORTST", headers=H, json={"datos": {"ambiente": "DESA", "zabbix_host": "dbtst01"}})
    assert r.status_code == 200 and api_n.inventario.base("CORTST").ambiente == "DESA"
    assert api_n.inventario.por_zabbix("DBTST01").id == "CORTST"      # el host de Zabbix ahora apunta a la base
    # validaciones
    assert c.post("/api/inventario/base", headers=H, json={"id": "X1", "datos": {"ambiente": "MARTE"}}).status_code == 422
    assert c.post("/api/inventario/base", headers=H, json={"id": "CORQA", "datos": {"ambiente": "QA"}}).status_code == 422
    assert c.delete("/api/inventario/servidor/DBTST01", headers=H).status_code == 422   # tiene bases
    # baja y restaurar
    assert c.delete("/api/inventario/base/CORTST", headers=H).status_code == 200
    assert api_n.inventario.base("CORTST") is None
    assert c.post("/api/inventario/base/CORTST/restaurar", headers=H).status_code == 200
    assert api_n.inventario.base("CORTST").ambiente == "TEST"           # vuelve a como viene del archivo
    # un alta que se da de baja desaparece sin dejar rastro en los ajustes
    assert c.delete("/api/inventario/base/CORQA", headers=H).status_code == 200
    assert not any(a["id"] == "CORQA" for a in c.get("/api/inventario/ajustes").json())


def test_editores_restringidos(cfg, api_n):
    from opscentral import config
    cfg2 = config.Config(config._fusionar(cfg, {"inventario": {"editores": ["admin.demo"]}}))
    api_n.inv_ajustes.cfg = cfg2
    app = crear_app(cfg2, api_n)
    c = cliente(app, "dba.demo")
    assert c.get("/api/inventario").json()["editable"] is False
    assert c.put("/api/inventario/base/CORTST", headers=H, json={"datos": {"ambiente": "QA"}}).status_code == 403


def test_ajustes_sobreviven_y_otro_proceso_los_ve(cfg, api_n):
    from opscentral import nucleo
    api_n.inv_ajustes.editar("base", "CORPRD", {"criticidad": "baja"}, "x")
    otro = nucleo.crear("worker-bd", cfg, almacen=api_n.almacen)        # otro proceso, mismo almacén
    assert otro.inventario.base("CORPRD").datos["criticidad"] == "baja"
    api_n.inv_ajustes.editar("base", "CORPRD", {"criticidad": "alta"}, "x")
    assert otro.inv_ajustes.sincronizar() is True
    assert otro.inventario.base("CORPRD").datos["criticidad"] == "alta"
