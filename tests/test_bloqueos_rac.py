"""Correcciones tras la presentación (2026-11-12).

- Bloqueos: falsos positivos de procesos de background (SMON en "DFS lock handle" contra el LGWR de otro
  nodo), esperas mínimas, objeto residual, lecturas viejas y prohibición de terminar procesos de background.
- Bases RAC: varios alias del kit (PROD_TRON, PROD_TRON1..3) son la misma base; se lee una vez y no se
  repite ni se suma el dato.
"""
import json
from pathlib import Path

import pytest

from opscentral import config, estado, nucleo
from opscentral.bd import bloqueos
from opscentral.workers import Worker


def _ses(sid, inst=1, **kw):
    base = {"inst_id": inst, "sid": sid, "serial": sid * 10, "username": "APP", "type": "USER", "status": "ACTIVE",
            "machine": "AP01", "program": "JDBC Thin Client", "module": None, "sql_id": None,
            "event": "SQL*Net message from client", "wait_class": "Idle", "state": "WAITING", "seconds_in_wait": 0,
            "last_call_et": 0, "blocking_instance": None, "blocking_session": None,
            "final_blocking_instance": None, "final_blocking_session": None, "obj_id": -1}
    base.update(kw)
    return base


@pytest.fixture
def motor(bd_n):
    return Worker("bd", bd_n).motor_bd


# ---------------------------------------------------------------- filtro de bloqueos
def test_smon_en_dfs_lock_handle_contra_lgwr_no_es_bloqueo(motor):
    lgwr = _ses(10351, 3, username=None, type="BACKGROUND", program="oracle@BD40SBCC04 (LGWR)",
                event="rdbms ipc message")
    smon = _ses(11758, 1, username=None, type="BACKGROUND", program="oracle@BD38SBCC04 (SMON)",
                event="DFS lock handle", wait_class="Other", blocking_instance=3, blocking_session=10351,
                seconds_in_wait=0, obj_id=746168)
    assert bloqueos.arbol(motor._filtrar_bloqueos([lgwr, smon]))["total_bloqueadas"] == 0


def test_bloqueo_real_tx_se_conserva_y_espera_corta_no(motor):
    raiz = _ses(14617, 1, username="C_COMUNES")
    larga = _ses(11776, 2, username="C_COMUNES", event="enq: TX - row lock contention", wait_class="Application",
                 blocking_instance=1, blocking_session=14617, seconds_in_wait=120, obj_id=5001)
    corta = _ses(2000, 1, event="enq: TX - row lock contention", wait_class="Application",
                 blocking_instance=1, blocking_session=14617, seconds_in_wait=1, obj_id=5001)
    r = bloqueos.arbol(motor._filtrar_bloqueos([raiz, larga, corta]))
    assert r["total_bloqueadas"] == 1
    assert r["arboles"][0]["raiz"]["sid"] == 14617


def test_objeto_solo_para_eventos_tx_tm(motor):
    raiz = _ses(1, 1)
    lc = _ses(2, 1, event="library cache lock", wait_class="Concurrency", blocking_instance=1,
              blocking_session=1, seconds_in_wait=60, obj_id=999)
    filas = motor._filtrar_bloqueos([raiz, lc])
    assert next(f for f in filas if f["sid"] == 2)["obj_id"] is None


def test_raiz_de_background_queda_marcada(motor):
    raiz = _ses(5, 1, username=None, type="BACKGROUND", program="oracle@X (CKPT)")
    hijo = _ses(6, 1, event="enq: CF - contention", wait_class="Other", blocking_instance=1, blocking_session=5,
                seconds_in_wait=30)
    filas = motor._filtrar_bloqueos([raiz, hijo])
    assert next(f for f in filas if f["sid"] == 5)["background"] is True


def test_terminar_sesion_de_background_se_rechaza(motor, monkeypatch):
    raiz = _ses(10351, 3, username=None, type="BACKGROUND", program="oracle@BD40SBCC04 (LGWR)")
    hijo = _ses(7, 1, event="enq: TX - row lock contention", blocking_instance=3, blocking_session=10351,
                seconds_in_wait=60)
    monkeypatch.setattr(motor, "_leer_bloqueos", lambda c: motor._filtrar_bloqueos([dict(raiz), dict(hijo)]))
    e = {"id": "e1", "solicitante": "dba.demo",
         "params": {"base": "CORPRD", "sid": 10351, "serial": 1, "inst_id": 3, "motivo": "x"}}
    with pytest.raises(ValueError, match="background"):
        motor.terminar_sesion(e, lambda _t: None)


# ---------------------------------------------------------------- lecturas viejas
def test_flota_marca_lectura_vieja_como_no_vigente(api_n, motor):
    from datetime import timedelta
    from sqlalchemy import update
    from opscentral.almacen import estado_actual as estado_obj, utc
    motor.recolectar_base("CORPRD", secciones=["bloqueos"])
    with api_n.almacen.tx() as con:
        con.execute(update(estado_obj).where(estado_obj.c.objeto == "bd:CORPRD:bloqueos")
                    .values(actualizado=utc() - timedelta(minutes=312)))
    f = estado.leer(api_n.almacen, "bd:CORPRD:bloqueos")
    assert f["antiguedad_s"] > api_n.cfg["bd"]["bloqueos_vigencia_s"]


def test_revision_relee_solo_bases_con_bloqueos(motor, api_n):
    estado.guardar(api_n.almacen, "bd:CORPRD:bloqueos", "bd.bloqueos", {"total_bloqueadas": 2, "arboles": []},
                   origen="prueba")
    estado.guardar(api_n.almacen, "bd:CRMPRD:bloqueos", "bd.bloqueos", {"total_bloqueadas": 0, "arboles": []},
                   origen="prueba")
    assert motor.revisar_bloqueos() == ["CORPRD"]


# ---------------------------------------------------------------- bases RAC
@pytest.fixture
def cfg_rac(tmp_path):
    inv = json.loads((Path(__file__).parent / "dobles" / "inventario_pruebas.json").read_text(encoding="utf-8"))
    nodos = [dict(b) for b in inv["bases"] if b["id"] == "CORPRD"]
    for i in (1, 2):
        n = dict(nodos[0], id=f"CORPRD{i}", alias=f"corprd{i}")
        inv["bases"].append(n)
    for b in inv["bases"]:
        if b["id"].startswith("CORPRD"):
            b["servicio"] = "CORP"
    ruta = tmp_path / "inv_rac.json"
    ruta.write_text(json.dumps(inv), encoding="utf-8")
    return config.cargar(extra={
        "general": {"modo": "desarrollo"}, "zabbix": {"url": "https://zabbix.pruebas.local"},
        "almacen": {"url": "sqlite:///" + (tmp_path / "ops.db").as_posix()},
        "evidencia": {"ruta": str(tmp_path / "evidencia")}, "reportes": {"ruta": str(tmp_path / "reportes")},
        "bd": {"reportes_muestra": False}, "inventario": {"archivo": str(ruta)}})


def test_grupo_rac_y_representante(cfg_rac):
    n = nucleo.crear("api", cfg_rac)
    assert n.inventario.grupo_rac("CORPRD2") == ["CORPRD", "CORPRD1", "CORPRD2"]
    assert n.inventario.representante("CORPRD1") == "CORPRD"
    assert n.inventario.grupo_rac("CRMPRD") == ["CRMPRD"]


def test_alias_rac_reutiliza_el_dato_del_representante(cfg_rac):
    api = nucleo.crear("api", cfg_rac)
    m = Worker("bd", nucleo.crear("worker-bd", cfg_rac, almacen=api.almacen)).motor_bd
    r = m.barrido("PROD", persona="sistema", ejecucion_id="x", emitir=lambda _t: None)
    assert {x["base"] for x in r["bases"]} == {"CORPRD", "CORPRD1", "CORPRD2", "CRMPRD"}
    assert {x.get("datos_de") for x in r["bases"] if x["base"] in ("CORPRD1", "CORPRD2")} == {"CORPRD"}
    rep = estado.leer(api.almacen, "bd:CORPRD:salud")["datos"]
    ali = estado.leer(api.almacen, "bd:CORPRD2:salud")["datos"]
    assert ali["invalidos"] == rep["invalidos"] and ali["rac"]["representante"] == "CORPRD"
    # los bloqueos se guardan una sola vez, en el representante
    assert estado.leer(api.almacen, "bd:CORPRD1:bloqueos") is None


# ---------------------------------------------------------------- lectura actual al abrir la vista
def test_abrir_bloqueos_pide_lectura_actual_una_vez_y_el_worker_la_hace(cfg, api_n, bd_n):
    from fastapi.testclient import TestClient
    from opscentral.api import crear_app
    h = {"X-Ops-Central": "1"}
    c = TestClient(crear_app(cfg, api_n))
    assert c.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=h).status_code == 200
    r = c.post("/api/refresco/vista", json={"vista": "bloqueos", "ambiente": "PROD"}, headers=h).json()
    assert r["estado"] == "encolado" and r["en_curso"] is True
    # otra persona abre la misma vista enseguida: se une a la lectura en curso, no lanza otra
    assert c.post("/api/refresco/vista", json={"vista": "bloqueos", "ambiente": "PROD"}, headers=h).json()["estado"] == "reciente"
    w = Worker("bd", bd_n)
    while w.una_vez():
        pass
    assert c.get("/api/refresco/vista?vista=bloqueos&ambiente=PROD").json()["en_curso"] is False
    assert estado.leer(api_n.almacen, "bd:CORPRD:bloqueos")["antiguedad_s"] < 60
    assert estado.leer(api_n.almacen, "bd:CORTST:bloqueos") is None          # solo el ambiente visible
    assert c.post("/api/refresco/vista", json={"vista": "otra"}, headers=h).status_code == 422


def test_refrescar_todo_exige_permiso_y_respeta_minimo(cfg, api_n):
    from fastapi.testclient import TestClient
    from opscentral.api import crear_app
    h = {"X-Ops-Central": "1"}
    app = crear_app(cfg, api_n)
    dba, consulta = TestClient(app), TestClient(app)
    dba.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=h)
    consulta.post("/api/auth/dev/ingresar", json={"login": "consulta.demo"}, headers=h)
    cuerpo = {"vista": "salud", "ambiente": "TEST", "forzar": True}
    assert consulta.post("/api/refresco/vista", json=cuerpo, headers=h).status_code == 403
    assert dba.post("/api/refresco/vista", json=cuerpo, headers=h).json()["estado"] == "encolado"
    # pulsarlo de nuevo enseguida no lanza otra lectura completa (mínimo [bd] refresco_min_s)
    assert dba.post("/api/refresco/vista", json=cuerpo, headers=h).json()["estado"] == "reciente"
