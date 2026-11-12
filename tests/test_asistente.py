"""Ventana de chat del asistente: contrato de la API mientras el motor no está conectado."""
import pytest
from fastapi.testclient import TestClient

from opscentral.api import crear_app

H = {"X-Ops-Central": "1"}


@pytest.fixture
def app(cfg, api_n):
    return crear_app(cfg, api_n)


def cliente(app, login):
    c = TestClient(app)
    assert c.post("/api/auth/dev/ingresar", json={"login": login}, headers=H).status_code == 200
    return c


def test_estado_no_conectado_con_sugerencias_por_perfil(app):
    assert TestClient(app).get("/api/asistente/estado").status_code == 401
    e = cliente(app, "dba.demo").get("/api/asistente/estado").json()
    assert e["conectado"] is False and e["mensaje"]
    assert any("inválidos" in s for s in e["sugerencias"])
    e_cm = cliente(app, "cm.demo").get("/api/asistente/estado").json()
    assert any("frmweb" in s for s in e_cm["sugerencias"])


def test_preguntar_responde_no_conectado_y_audita(app, api_n):
    c = cliente(app, "consulta.demo")   # todos los perfiles pueden usar el chat
    r = c.post("/api/asistente/preguntar", headers=H,
               json={"pregunta": "¿Qué es un objeto inválido?", "contexto": {"pantalla": "snapshot", "base": "ABBYY",
                                                                            "otro": "se descarta"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["estado"] == "no_conectado" and d["id"] and d["fuentes"] == []
    assert "snapshot" in d["texto"]
    assert c.post("/api/asistente/valorar", headers=H, json={"respuesta_id": d["id"], "util": False}).json() == {"ok": True}


def test_preguntar_valida_y_exige_csrf(app):
    c = cliente(app, "dba.demo")
    assert c.post("/api/asistente/preguntar", json={"pregunta": "hola"}).status_code == 403
    assert c.post("/api/asistente/preguntar", headers=H, json={"pregunta": "   "}).status_code == 422
    assert c.post("/api/asistente/preguntar", headers=H, json={"pregunta": "x" * 2001}).status_code == 422
