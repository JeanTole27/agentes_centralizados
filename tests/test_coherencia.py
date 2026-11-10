"""Coherencia entre pantallas: Prevención vs Inicio, y lo que ve Capa Media en bloqueos y salud."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from opscentral import estado
from opscentral.almacen import prediccion, serie_punto, utc
from opscentral.api import crear_app
from .dobles.series import serie_sintetica
from opscentral.ml.servicio import scoring
from opscentral.workers import Worker

H = {"X-Ops-Central": "1"}


def _cargar(alm, serie, puntos):
    with alm.tx() as con:
        con.execute(serie_punto.delete().where(serie_punto.c.serie == serie))
        con.execute(serie_punto.insert(), [{"serie": serie, "ts": p.ts, "valor": p.valor} for p in puntos])


def _vigentes(alm, serie):
    with alm.tx() as con:
        return con.execute(select(prediccion.c.id).where(prediccion.c.serie == serie,
                                                         prediccion.c.vigente.is_(True))).all()


def test_prediccion_que_ya_no_se_puede_calcular_deja_de_estar_vigente(cfg, api_n):
    alm, s = api_n.almacen, "ts:CORPRD:APP_DATA"
    _cargar(alm, s, serie_sintetica(20, 30, 3.2, semilla=1))
    scoring(alm, cfg)
    assert len(_vigentes(alm, s)) == 1
    assert any(r["serie"] == s for r in estado.leer(alm, "ml:riesgos")["datos"]["riesgos"])

    # Un salto de nivel (p. ej. un dato real que no sigue la historia) deja al modelo sin predicción
    pts = serie_sintetica(20, 30, 3.2, semilla=1)
    with alm.tx() as con:
        con.execute(serie_punto.insert().values(serie=s, ts=pts[-1].ts + timedelta(hours=1), valor=20.0))
    scoring(alm, cfg)
    assert _vigentes(alm, s) == []          # Prevención ya no muestra la predicción vieja…
    assert not any(r["serie"] == s for r in estado.leer(alm, "ml:riesgos")["datos"]["riesgos"])  # …ni el Inicio


def test_capa_media_solo_ve_bases_de_sus_dominios(cfg, api_n, bd_n, monkeypatch):
    w = Worker("bd", bd_n)
    for b in ("CORPRD", "CRMPRD", "CORTST"):
        w.motor_bd.recolectar_base(b)
    # En el inventario de ejemplo los servidores de CM tocan las 3 bases; se acota a uno
    # (AP23SBCC04 → clientesprd → CORPRD) para que quede una base ajena.
    monkeypatch.setattr(api_n.inventario, "servidores_de_equipo", lambda equipo: ["AP23SBCC04"])
    suyas = {"CORPRD"}

    app = crear_app(cfg, api_n)
    cm = TestClient(app)
    cm.post("/api/auth/dev/ingresar", json={"login": "cm.demo"}, headers=H)
    assert {f["base"] for f in cm.get("/api/estado/bd/bloqueos").json()} == suyas
    assert {f["objeto"].split(":")[1] for f in cm.get("/api/estado/bd/salud").json()} == suyas
    ajena = ({"CORPRD", "CRMPRD", "CORTST"} - suyas).pop()
    assert cm.get(f"/api/estado/bd/{ajena}/bloqueos").status_code == 404
    assert sum(cm.get("/api/inicio").json()["kpis"]["bases"].values()) == len(suyas)

    dba = TestClient(app)
    dba.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=H)
    assert len(dba.get("/api/estado/bd/bloqueos").json()) == 3
