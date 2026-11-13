"""Mejoras de la auditoría DBA (2026-11-12).

- Espacio real: UNDO activo, TEMP en uso, ASM sin el espejo (sin falsas alarmas).
- Oracle 11g: sin ORACLE_MAINTAINED ni FETCH FIRST.
- Protección en el semáforo: FRA, respaldos RMAN, Data Guard.
- Inicio por equipo con fallos repetidos agrupados; menú sin pantallas "próxima etapa".
- frmweb con tope de lote por ambiente; anomalías (M3) calculadas en el scoring.
"""
from datetime import timedelta

from fastapi.testclient import TestClient

from opscentral import estado
from opscentral.almacen import serie_punto, utc
from opscentral.api import crear_app
from opscentral.bd import plantillas, salud
from opscentral.cm.adaptador import lote_maximo
from opscentral.ml.servicio import scoring

H = {"X-Ops-Central": "1"}
INST = {"status": "OPEN", "sesiones": 10, "procesos": 1000}


# ---------------------------------------------------------------- plantillas por versión
def test_variantes_11g_sin_oracle_maintained_ni_fetch_first():
    for nombre in ("invalidos", "usuarios_por_expirar", "jobs_problema", "topsql_cpu", "alertlog_24h"):
        sql = plantillas.sql_para(nombre, "11.2.0.4.0")
        assert "oracle_maintained" not in sql and "FETCH FIRST" not in sql, nombre
    assert "ROWNUM <= 25" in plantillas.sql_para("topsql_cpu", "11.2.0.4.0")
    # 12.1 y 19c siguen con la consulta original; 12.2+ lee el alert log de todas las instancias
    assert "oracle_maintained" in plantillas.sql_para("invalidos", "19.0.0.0.0")
    assert "gv$diag_alert_ext" in plantillas.sql_para("alertlog_24h", "19.0.0.0.0")
    assert "gv$diag_alert_ext" not in plantillas.sql_para("alertlog_24h", "12.1.0.2.0")
    assert plantillas.sql_para("invalidos", None) == plantillas.PLANTILLAS["invalidos"]


def test_tablespaces_y_asm_miden_lo_real():
    ts = plantillas.PLANTILLAS["tablespaces"]
    assert "dba_undo_extents" in ts and "gv$sort_segment" in ts and "pct_asignado" in ts
    assert "required_mirror_free_mb" in plantillas.PLANTILLAS["diskgroups"]


# ---------------------------------------------------------------- semáforo
def test_undo_con_extents_activos_bajos_no_alarma():
    # la vista de métricas dice 99 % (asignado), pero lo ACTIVO es 12 %: verde
    r = salud.evaluar(INST, [{"tablespace_name": "UNDOTBS1", "tipo": "UNDO", "pct_usado": 12.0,
                              "pct_asignado": 99.0}], [])
    assert r["semaforo"] == "verde"
    r = salud.evaluar(INST, [{"tablespace_name": "UNDOTBS1", "tipo": "UNDO", "pct_usado": 97.0}], [])
    assert r["semaforo"] == "rojo" and "UNDO activo" in r["motivos"][0]


def test_asm_sin_espacio_para_el_espejo_es_rojo():
    r = salud.evaluar(INST, [], [{"name": "DATA", "pct_usado": 60.0, "usable_file_mb": -2048}])
    assert r["semaforo"] == "rojo" and "espejo" in r["motivos"][0]


def test_proteccion_fra_respaldo_y_dataguard():
    prot = {"fra": [{"name": "+FRA", "pct_usado": 96.0}],
            "respaldo": [{"input_type": "DB INCR", "horas": 50.0}, {"input_type": "ARCHIVELOG", "horas": 1.0}],
            "dataguard": {"rol": "PRIMARY", "lag": {},
                          "destinos": [{"dest_id": 2, "dest_name": "DEST_2", "status": "ERROR",
                                        "error": "ORA-12541: TNS:no listener", "gap_status": None}]}}
    r = salud.evaluar(INST, [], [], proteccion=prot, exigir_respaldo=True)
    texto = " | ".join(r["motivos"])
    assert r["semaforo"] == "rojo" and r["proteccion_semaforo"] == "rojo"
    assert "FRA al 96" in texto and "hace 50 h" in texto and "DEST_2" in texto
    # sin exigir respaldo (DESA/TEST) el RMAN viejo no cuenta
    r = salud.evaluar(INST, [], [], proteccion={"respaldo": []}, exigir_respaldo=False)
    assert r["semaforo"] == "verde" and r["proteccion_semaforo"] == "verde"
    r = salud.evaluar(INST, [], [], proteccion={"respaldo": []}, exigir_respaldo=True)
    assert r["semaforo"] == "rojo" and "sin respaldo" in r["motivos"][0]
    # sin la sección leída no hay semáforo de protección
    assert salud.evaluar(INST, [], [])["proteccion_semaforo"] is None


def test_standby_con_desfase():
    assert salud.segundos_intervalo("+00 01:02:03") == 3723.0
    prot = {"dataguard": {"rol": "PHYSICAL STANDBY", "destinos": [], "lag": {"apply lag": "+00 03:00:00"}}}
    r = salud.evaluar(INST, [], [], proteccion=prot)
    assert r["semaforo"] == "rojo" and "apply lag de 180 min" in r["motivos"][0]


def test_pasada_completa_guarda_proteccion(bd_n, api_n):
    from opscentral.workers import Worker
    m = Worker("bd", bd_n).motor_bd
    m.recolectar_base("CORPRD")
    d = estado.leer(api_n.almacen, "bd:CORPRD:salud")["datos"]
    assert set(d["proteccion"]) == {"fra", "respaldo", "dataguard"}
    assert d["proteccion"]["dataguard"]["destinos"][0]["gap_status"] == "NO GAP"
    assert d["proteccion_semaforo"] in ("verde", "amarillo", "rojo")
    # una pasada solo de espacio conserva la protección leída antes
    m.recolectar_base("CORPRD", secciones=["tablespaces"])
    assert estado.leer(api_n.almacen, "bd:CORPRD:salud")["datos"]["proteccion"]["fra"]


# ---------------------------------------------------------------- Inicio y menú
def _cliente(cfg, api_n, login):
    c = TestClient(crear_app(cfg, api_n))
    assert c.post("/api/auth/dev/ingresar", json={"login": login}, headers=H).status_code == 200
    return c


def test_menu_oculta_las_pantallas_configuradas(cfg, api_n):
    c = _cliente(cfg, api_n, "lider.demo")
    ids = {p["id"] for g in c.get("/api/auth/yo").json()["menu"] for p in g["pantallas"]}
    assert {"ventana", "incidentes", "prevencion"} <= ids          # ya implementadas: visibles
    cfg["general"]["pantallas_ocultas"] = ["ventana"]
    c = _cliente(cfg, api_n, "lider.demo")
    ids = {p["id"] for g in c.get("/api/auth/yo").json()["menu"] for p in g["pantallas"]}
    assert "ventana" not in ids and "incidentes" in ids


def test_inicio_filtra_por_equipo_y_agrupa_repetidas(cfg, api_n):
    from opscentral.almacen import ejecucion
    ahora = utc()
    def fila(i, accion, motor, estado_e, quien, minutos):
        return dict(id=f"e{i}", accion=accion, motor=motor, clase="L", destino="AP01", ambiente="TEST", params={},
                    solicitante=quien, estado=estado_e, huella="x", creada=ahora - timedelta(minutes=minutos),
                    actualizada=ahora, origen="persona")
    filas = [fila(i, "orq.status", "cm", "fallida", "cm2.demo", i) for i in range(4)]
    filas.append(fila(9, "bd.salud.flota", "bd", "exitosa", "dba2.demo", 10))
    with api_n.almacen.tx() as con:
        for f in filas:
            con.execute(ejecucion.insert().values(**f))
    dba = _cliente(cfg, api_n, "dba.demo").get("/api/inicio").json()
    assert [e["accion"] for e in dba["ultimas"]] == ["bd.salud.flota"] and dba["otras_equipos"] == 4
    cm = _cliente(cfg, api_n, "cm.demo").get("/api/inicio").json()
    assert cm["ultimas"][0]["accion"] == "orq.status" and cm["ultimas"][0]["veces"] == 4
    todas = _cliente(cfg, api_n, "dba.demo").get("/api/inicio?todas=true").json()
    assert {e["accion"] for e in todas["ultimas"]} == {"orq.status", "bd.salud.flota"}


# ---------------------------------------------------------------- Capa Media y ML
def test_tope_de_lote_frmweb_por_ambiente(cfg):
    assert lote_maximo(cfg, "PROD") == 10 and lote_maximo(cfg, "desa") == 40


def test_scoring_marca_anomalias_por_hora_de_la_semana(cfg, api_n):
    alm, s = api_n.almacen, "asm:CORPRD:DATA"
    fin = utc().replace(minute=0, second=0, microsecond=0)
    puntos = [{"serie": s, "ts": fin - timedelta(weeks=w), "valor": 50.0 + w * 0.1} for w in range(1, 6)]
    puntos.append({"serie": s, "ts": fin, "valor": 90.0})          # hoy, a esta hora: muy por encima
    with alm.tx() as con:
        con.execute(serie_punto.insert(), puntos)
    r = scoring(alm, cfg)
    assert r["anomalias"] >= 1
    c = _cliente(cfg, api_n, "dba.demo")
    a = c.get("/api/prevencion/anomalias?equipo=bd").json()["anomalias"]
    assert a[0]["serie"] == s and a[0]["z"] > 0
