"""Casos de Jira opcionales: traer el caso y completar los parámetros de la acción."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from opscentral import config
from opscentral.api import crear_app
from opscentral.jira import JiraNoConfigurado, ServicioCasos, adf_a_texto

H = {"X-Ops-Central": "1"}

ISSUE = {
    "key": "OPS-123",
    "fields": {
        "summary": "Sesiones bloqueadas en CORPRD por el batch de cierre",
        "status": {"name": "En curso"}, "priority": {"name": "Alta"}, "issuetype": {"name": "Incidente"},
        "assignee": {"displayName": "María DBA"}, "reporter": {"displayName": "Mesa de servicio"},
        "project": {"key": "OPS"}, "created": "2026-11-10T08:00:00.000-0500", "updated": "2026-11-10T09:30:00.000-0500",
        "labels": ["batch"], "components": [{"name": "Base de datos"}],
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "El servidor AP23SBCC04 reporta lentitud."}]},
            {"type": "bulletList", "content": [{"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Afecta a clientes"}]}]}]}]},
        "customfield_10050": {"value": "CORTST"},
    },
}


def transporte_jira(peticiones: list):
    def manejar(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        if req.url.path == "/rest/api/3/issue/OPS-123":
            return httpx.Response(200, json=ISSUE)
        if req.url.path.startswith("/rest/api/3/issue/"):
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [ISSUE]})
        return httpx.Response(500)
    return httpx.MockTransport(manejar)


class SecretosFalsos:
    def leer(self, ruta, motivo=""):
        assert ruta == "jira/api"
        return {"usuario": "bot@empresa.com", "token": "t"}


def servicio(cfg, api_n, peticiones, campos=None):
    c = config.Config(config._fusionar(cfg, {"jira": {"url": "https://empresa.atlassian.net", "campos": campos or {}}}))
    return ServicioCasos(c, SecretosFalsos(), api_n.inventario, api_n.catalogo, transporte=transporte_jira(peticiones))


def test_adf_a_texto():
    assert "AP23SBCC04" in adf_a_texto(ISSUE["fields"]["description"])
    assert "Afecta a clientes" in adf_a_texto(ISSUE["fields"]["description"])


def test_sin_jira_configurado_el_caso_es_referencia_libre(cfg, api_n):
    s = ServicioCasos(cfg, SecretosFalsos(), api_n.inventario, api_n.catalogo)
    assert not s.habilitado
    with pytest.raises(JiraNoConfigurado):
        s.caso("OPS-1")


def test_caso_completa_parametros_de_la_accion(cfg, api_n):
    pet = []
    s = servicio(cfg, api_n, pet)
    c = s.caso("ops-123", "bd.ses.terminar")
    assert c["clave"] == "OPS-123" and c["estado"] == "En curso" and c["asignado"] == "María DBA"
    assert c["url"] == "https://empresa.atlassian.net/browse/OPS-123"
    assert c["sugeridos"] == {"base": "CORPRD", "motivo": "OPS-123: Sesiones bloqueadas en CORPRD por el batch de cierre",
                              "cambio": "OPS-123"}
    assert pet[0].headers["authorization"].startswith("Basic ")
    # en una acción de Capa Media propone el servidor que aparece en la descripción
    c = s.caso("OPS-123", "orq.status")
    assert c["sugeridos"].get("hosts") == "AP23SBCC04" and "base" not in c["sugeridos"]


def test_campo_configurado_manda_sobre_el_texto(cfg, api_n):
    s = servicio(cfg, api_n, [], campos={"base": "customfield_10050"})
    assert s.caso("OPS-123", "bd.ses.bloqueos")["sugeridos"]["base"] == "CORTST"


def test_buscar_y_caso_inexistente(cfg, api_n):
    pet = []
    s = servicio(cfg, api_n, pet)
    assert [c["clave"] for c in s.buscar("bloqueo \"CORPRD\"")] == ["OPS-123"]
    assert json.loads(pet[-1].content)["jql"] == 'text ~ "bloqueo  CORPRD " ORDER BY updated DESC'
    s.buscar("OPS-123")
    assert json.loads(pet[-1].content)["jql"] == 'key = "OPS-123"'
    with pytest.raises(KeyError):
        s.caso("OPS-999")
    with pytest.raises(ValueError):
        s.caso("no es clave")


def test_api_de_casos(cfg, api_n):
    app = crear_app(cfg, api_n)
    c = TestClient(app)
    c.post("/api/auth/demo/registrar", json={"nombre": "Carlos CM", "perfil": "cm"}, headers=H)
    assert c.get("/api/casos/OPS-123").status_code == 503              # Jira no configurado: 503 con detalle
    app.state.casos.c = {**app.state.casos.c, "url": "https://empresa.atlassian.net"}
    app.state.casos.sec = SecretosFalsos()
    app.state.casos._transporte = transporte_jira([])
    r = c.get("/api/casos/OPS-123", params={"accion": "orq.status"})
    assert r.status_code == 200 and r.json()["sugeridos"]["cambio"] == "OPS-123"
    assert c.get("/api/casos/OPS-999").status_code == 404
    assert c.get("/api/casos", params={"q": "bloqueo"}).json()[0]["clave"] == "OPS-123"
