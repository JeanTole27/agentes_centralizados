"""Acciones de diagnóstico, comparación y reportes de BD: plantillas + análisis con hallazgos."""
import pytest

from opscentral.bd import analisis, recompilar
from opscentral.bd.plantillas import BINDS, PLANTILLAS
from opscentral.bd.politica import validar_lectura
from opscentral.catalogo import Catalogo
from opscentral.workers import Worker

CAT = Catalogo.cargar()


def correr(api_n, bd_n, usuario, accion, params):
    e = api_n.ejecuciones.solicitar(usuario, accion, params)
    w = Worker("bd", bd_n)
    for _ in range(3):
        if not w.una_vez():
            break
    r = api_n.ejecuciones.obtener(e["id"])
    assert r["estado"] == "exitosa", r["error"]
    return r["resultado"]


def textos(r, nivel=None):
    return [h["texto"] for h in r["hallazgos"] if nivel is None or h["nivel"] == nivel]


# ---------------------------------------------------------------- plantillas y catálogo
def test_todas_las_plantillas_son_de_solo_lectura_y_declaran_sus_binds():
    import re
    for nombre, sql in PLANTILLAS.items():
        assert validar_lectura(sql)[0], nombre
        assert set(re.findall(r":([a-z_]+)", sql)) == set(BINDS.get(nombre, ())), nombre


def test_cada_accion_bd_implementada_tiene_logica():
    from opscentral.bd.servicio import MotorBD
    import inspect
    fuente = inspect.getsource(MotorBD.ejecutar)
    for a in CAT._acciones.values():
        if a.motor != "bd" or a.estado != "implementada":
            continue
        assert (a.plantilla or a.procedimiento or a.id in analisis.POR_ACCION or f'"{a.id}"' in fuente), a.id
        for pl in analisis.POR_ACCION.get(a.id, ((),))[0]:
            assert pl in PLANTILLAS, (a.id, pl)


def test_vista_previa_de_recompilacion_entre_comillas():
    p = recompilar.plan([{"owner": "APP_CORE", "object_name": "Pkg$Mixto", "object_type": "PACKAGE BODY"}])
    assert p[0]["sentencia"] == 'ALTER PACKAGE "APP_CORE"."Pkg$Mixto" COMPILE BODY'
    with pytest.raises(ValueError):
        recompilar.plan([{"owner": "X", "object_name": 'A" COMPILE; DROP', "object_type": "VIEW"}])


# ---------------------------------------------------------------- lecturas con análisis
def test_salud_con_hallazgos(api_n, bd_n, usuarios):
    dba = usuarios["dba"]
    r = correr(api_n, bd_n, dba, "bd.salud.fra", {"base": "CORTST"})
    assert r["veredicto"] == "rojo" and any("FRA al 92" in t for t in textos(r, "rojo"))
    assert any("Pico de archivelogs" in t for t in textos(r, "amarillo"))

    r = correr(api_n, bd_n, dba, "bd.salud.rman", {"base": "CORTST"})
    assert any("Último backup de datos" in t for t in textos(r, "rojo"))
    assert r["resumen"]["fallidos"] == 1

    r = correr(api_n, bd_n, dba, "bd.salud.jobs", {"base": "CORTST"})
    assert any("BROKEN" in t for t in textos(r, "rojo")) and any("falló 3" in t for t in textos(r, "rojo"))

    r = correr(api_n, bd_n, dba, "bd.salud.alertlog", {"base": "CORTST"})
    assert r["resumen"]["codigos"] == {"00600": 2, "01555": 1}
    assert any(t.startswith("ORA-00600 × 2") for t in textos(r, "rojo"))

    r = correr(api_n, bd_n, dba, "bd.salud.undo_temp", {"base": "CORTST"})
    assert any("ORA-01555" in t for t in textos(r, "rojo")) and any("TEMP" in t for t in textos(r, "amarillo"))


def test_una_plantilla_que_falla_no_tumba_las_demas(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.salud.dataguard", {"base": "CORTST"})
    assert "dg_stats" in r["errores"] and r["datos"]["dg_rol"]
    assert any("dg_stats no disponible" in t for t in textos(r, "info"))
    assert r["veredicto"] == "verde"


def test_sesiones_inactivas_con_transaccion(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.ses.idle_tx", {"base": "CORTST", "min_segundos": 600})
    assert r["resumen"]["sesiones"] == 2
    assert any("APP_BATCH" in t for t in textos(r, "rojo")) and any("APP_WEB" in t for t in textos(r, "amarillo"))


def test_privilegios_peligrosos(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.usr.privilegios", {"base": "CORTST", "usuario": "APP_BATCH"})
    assert "Tiene el rol DBA" in textos(r, "rojo")
    assert any("SELECT ANY TABLE" in t for t in textos(r, "amarillo"))


def test_regresion_de_plan(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.perf.plan", {"base": "CORTST", "sql_id": "7h35uxf5uhmm1"})
    assert r["veredicto"] == "rojo" and any("Regresión de plan" in t for t in textos(r))
    assert r["resumen"]["mejor_plan"] == 111


def test_top_sql_por_criterio(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.perf.topsql", {"base": "CORTST", "criterio": "gets"})
    assert r["criterio"] == "GETS" and r["datos"]["topsql_gets"][0]["sql_id"] == "0w2qpuc6u2zsp"
    with pytest.raises(Exception, match="criterio"):
        api_n.ejecuciones.solicitar(usuarios["dba"], "bd.perf.topsql", {"base": "CORTST", "criterio": "DROP"})


# ---------------------------------------------------------------- comparaciones entre bases
def test_deriva_de_parametros_entre_bases(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.salud.parametros", {"base": "CORPRD", "base_comparar": "CRMPRD"})
    assert r["datos"]["deriva"] == [{"parametro": "sga_target", "origen": "32G", "destino": "16G"}]   # db_unique_name se ignora


def test_diff_de_esquema_entre_bases(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.esquema.diff",
               {"base": "CORTST", "base_destino": "CORPRD", "esquema": "APP_CORE"})
    assert r["datos"]["solo_en_origen"] == [{"object_type": "PACKAGE", "object_name": "PKG_NUEVO"}]
    assert r["datos"]["estado_distinto"][0]["object_name"] == "VW_SALDOS"
    assert r["veredicto"] == "amarillo"


def test_bases_de_un_dominio(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["cm"], "cm.bd_relacionada", {"dominio": "creditoprd"})
    assert {b["base"] for b in r["datos"]["bases"]} == {"CORPRD", "CRMPRD"}
    assert r["servidor"] == "AP23SBCC09" and r["datos"]["bases"][0]["sesiones_app"]


# ---------------------------------------------------------------- reportes
def test_hoja_de_vida(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.rep.hoja_vida", {"base": "CORPRD"})
    h = r["datos"]["hoja_vida"]
    assert h["ambiente"] == "PROD" and h["dominios"] == ["clientesprd", "creditoprd"]
    assert h["parametros_clave"]["processes"] == "1500" and h["ultimo_parche"]["patch_id"] == 36233263


def test_analisis_con_plan_de_accion(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.rep.analisis", {"base": "CORTST"})
    plan = r["datos"]["plan_de_accion"]
    assert plan and all(p["nivel"] in ("rojo", "amarillo") for p in plan)
    assert plan[0]["nivel"] == "rojo"
    acciones = {p["accion"] for p in plan}
    assert {"bd.salud.fra", "bd.job.reintentar"} <= acciones


def test_diskgroups_por_servidor_y_sql_costosas(api_n, bd_n, usuarios):
    r = correr(api_n, bd_n, usuarios["dba"], "bd.rep.diskgroups", {"servidor": "DBPRD01"})
    assert r["bases_del_servidor"] == ["CORPRD", "CRMPRD"] and r["datos"]["diskgroups"]
    r = correr(api_n, bd_n, usuarios["dba"], "bd.rep.sql_costosas", {"base": "CORTST"})
    assert r["datos"]["detalle"][0]["sql_fulltext"].startswith("UPDATE")


def test_crecimiento_usa_las_series_del_almacen(api_n, bd_n, usuarios):
    from datetime import timedelta
    from opscentral.almacen import serie_punto, utc
    ahora = utc().replace(minute=0, second=0, microsecond=0)
    with api_n.almacen.tx() as con:
        con.execute(serie_punto.insert(), [{"serie": "ts:CORTST:USERS", "ts": ahora - timedelta(days=10 - d),
                                            "valor": 50.0 + d} for d in range(11)])
    r = correr(api_n, bd_n, usuarios["dba"], "bd.ts.crecimiento", {"base": "CORTST"})
    users = next(f for f in r["datos"]["tablespaces"] if f["tablespace"] == "USERS")
    assert users["crecimiento_pct_dia"] == 1.0 and users["dias_historia"] == 10.0
