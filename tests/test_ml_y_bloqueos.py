from datetime import datetime, timedelta, timezone

from opscentral.bd.bloqueos import arbol, filtrar_por_servidores
from opscentral.bd.recompilar import plan
from opscentral.bd.salud import evaluar
from opscentral.bd.snapshot import armar, comparar
from opscentral.ml.anomalias import evaluar as anomalia
from opscentral.ml.llenado import predecir, serie_sintetica


def test_m1_crecimiento_lineal():
    pts = serie_sintetica(dias=20, inicio=60, pendiente=1.5, ruido=0.3)
    r = predecir("ts:X:DATA", pts)
    assert r["estado"] == "ok"
    assert 1.2 < r["pendiente_dia"] < 1.8
    d = r["umbrales"]["100"]
    assert d["dias"] is not None and d["fecha_p10"] <= d["fecha"] <= d["fecha_p90"]
    assert r["timeleft_zabbix_dias"] is not None and r["confianza"] in ("alta", "media")


def test_m1_sin_crecimiento_y_datos_insuficientes():
    plano = predecir("ts:X:USERS", serie_sintetica(dias=20, inicio=50, pendiente=0.0, ruido=0.2))
    assert plano["umbrales"]["100"]["dias"] is None or plano["umbrales"]["100"]["dias"] > 100
    assert predecir("ts:X:Y", serie_sintetica(dias=1)[:10])["estado"] == "datos_insuficientes"


def test_m1_diente_de_sierra_se_proyectan_picos():
    pts = serie_sintetica(dias=20, inicio=40, pendiente=2.5, purgas_cada_h=96, salto_purga=9, ruido=0.2)
    r = predecir("fra:X", pts)
    assert "diente de sierra" in r["explicacion"]
    assert r["pendiente_dia"] > 0


def test_m2_heap_usa_minimo_diario():
    r = predecir("heap:H:MS1", serie_sintetica(dias=20, inicio=50, pendiente=1.2, ruido=3), modo="heap")
    assert r["modelo"].startswith("M2") and r["pendiente_dia"] > 0.5


def test_m3_anomalia_por_hora_de_la_semana():
    ahora = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)   # jueves 08:00
    hist = [(ahora - timedelta(weeks=w), 100.0 + w) for w in range(1, 6)]
    hist += [(ahora - timedelta(weeks=w, hours=20), 5.0) for w in range(1, 6)]   # otra hora: no cuenta
    r = anomalia("met:CORPRD:sesiones", hist, (ahora, 320.0))
    assert r["anomalo"] and "jueves 08:00" in r["explicacion"]
    assert not anomalia("met:CORPRD:sesiones", hist, (ahora, 103.0))["anomalo"]


def test_arbol_de_bloqueo_y_ciclo():
    filas = [
        {"inst_id": 1, "sid": 10, "blocking_session": None, "machine": "AP23SBCC04"},
        {"inst_id": 1, "sid": 11, "blocking_session": 10, "blocking_instance": 1, "seconds_in_wait": 50, "machine": "X"},
        {"inst_id": 1, "sid": 12, "blocking_session": 11, "blocking_instance": 1, "seconds_in_wait": 90, "machine": "Y"},
        {"inst_id": 1, "sid": 20, "blocking_session": 21, "blocking_instance": 1},
        {"inst_id": 1, "sid": 21, "blocking_session": 20, "blocking_instance": 1},
    ]
    r = arbol(filas)
    assert r["arboles"][0]["raiz"]["sid"] == 10 and r["arboles"][0]["afectadas"] == 2
    assert r["arboles"][0]["espera_max_s"] == 90
    assert len(r["ciclos"]) == 1
    suyas = filtrar_por_servidores(filas, {"ap23sbcc04"})
    assert {f["sid"] for f in suyas} == {10}


def test_comparacion_respeta_linea_base_y_papelera():
    o = lambda n, t="PACKAGE BODY", ow="APP": {"owner": ow, "object_name": n, "object_type": t}  # noqa: E731
    antes = armar("B", "CHG100", "ANTES", [o("VIEJO")])
    despues = armar("B", "CHG100", "DESPUES", [o("VIEJO"), o("NUEVO"), o("BIN$abc==$0", "TRIGGER"), o("CONOCIDO")])
    c = comparar(antes, despues, [o("CONOCIDO")])
    assert [x["object_name"] for x in c["nuevos"]] == ["NUEVO"]
    assert c["resumen"]["en_linea_base"] == 1 and c["resumen"]["papelera"] == 1
    p = plan([o("V", "VIEW"), o("P", "PACKAGE"), o("T", "TRIGGER"), o("BIN$x", "TRIGGER")])
    assert [x["object_type"] for x in p] == ["PACKAGE", "VIEW", "TRIGGER"]


def test_semaforo_de_salud():
    inst = {"status": "OPEN", "sesiones": 100, "procesos": 1000}
    assert evaluar(inst, [{"tablespace_name": "A", "pct_usado": 50}], [])["semaforo"] == "verde"
    assert evaluar(inst, [{"tablespace_name": "A", "pct_usado": 96}], [])["semaforo"] == "rojo"
    assert evaluar(None, [], [])["semaforo"] == "sin_dato"
