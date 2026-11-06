"""Flujos completos en simulación: F2 (snapshot + recompilación) y F3 (bloqueos)."""
import pytest
from sqlalchemy import update

from opscentral.almacen import ejecucion
from opscentral.autorizacion import PermisoDenegado
from opscentral.ejecuciones import RequiereStepUp
from opscentral.workers import Worker

from .conftest import con_mfa


def correr(worker, veces=5):
    for _ in range(veces):
        if not worker.una_vez():
            break


def test_f2_snapshot_comparacion_y_recompilacion(api_n, bd_n, usuarios):
    w = Worker("bd", bd_n)
    ej, dba = api_n.ejecuciones, usuarios["dba"]
    for momento in ("ANTES", "DESPUES"):
        e = ej.solicitar(dba, "bd.snap.tomar", {"base": "CORTST", "cambio": "CHG1001", "momento": momento})
        assert e["estado"] == "en_cola"
        correr(w)
        assert ej.obtener(e["id"])["estado"] == "exitosa"

    e = ej.solicitar(dba, "bd.snap.comparar", {"base": "CORTST", "cambio": "CHG1001"})
    correr(w)
    comp = ej.obtener(e["id"])["resultado"]
    assert comp["resumen"] == {"nuevos": 3, "corregidos": 0, "persistentes": 2, "en_linea_base": 0, "papelera": 0}
    nombres = {o["object_name"] for o in comp["nuevos"]}
    assert nombres == {"PKG_CLIENTES", "VW_SALDOS", "TRG_AUDITA_CLIENTE"}

    e = ej.solicitar(dba, "bd.inv.recompilar", {"base": "CORTST", "cambio": "CHG1001"})
    assert e["clase"] == "O" and e["estado"] == "en_cola"     # TEST: O, sin aprobación
    correr(w)
    r = ej.obtener(e["id"])
    assert r["estado"] == "exitosa", r["error"]
    assert r["resultado"]["recompilacion"]["validos"] == 3
    assert r["resultado"]["comparacion_final"]["veredicto"] == "sin_nuevos"
    assert r["evidencia"] and api_n.evidencia.verificar(r["evidencia"], r["evidencia_sha256"])
    # el objeto de la línea base y el BIN$ siguen sin tocarse
    assert {o["object_name"] for o in r["resultado"]["comparacion_final"]["persistentes"]} == \
        {"PKG_REPORTES_OLD", "BIN$q1w2e3==$0"}


def test_f3_bloqueo_con_aprobacion_en_prod(api_n, bd_n, usuarios):
    w = Worker("bd", bd_n)
    ej = api_n.ejecuciones
    dba, dba2 = con_mfa(usuarios["dba"]), usuarios["dba2"]
    v = ej.solicitar(dba, "bd.ses.bloqueos", {"base": "CORPRD"})
    correr(w)
    arbol = ej.obtener(v["id"])["resultado"]
    if not arbol["arboles"]:
        pytest.skip("la simulación de esta base no generó bloqueo")
    raiz = arbol["arboles"][0]["raiz"]
    assert arbol["arboles"][0]["afectadas"] == 4

    params = {"base": "CORPRD", "sid": raiz["sid"], "serial": raiz["serial"], "motivo": "Batch colgado bloquea clientes"}
    with pytest.raises(Exception, match="cambio"):
        ej.solicitar(dba, "bd.ses.terminar", params)                      # falta CHG en PROD
    e = ej.solicitar(dba, "bd.ses.terminar", params, cambio="CHG2002")
    assert e["estado"] == "pendiente_aprobacion"
    assert not w.una_vez()                                                 # sin aprobación no hay trabajo
    with pytest.raises(PermisoDenegado):
        ej.aprobar(dba, e["id"])                                           # autoaprobación
    with pytest.raises(RequiereStepUp):
        ej.aprobar(dba2, e["id"])                                          # clase C pide MFA reciente
    ej.aprobar(con_mfa(dba2), e["id"], "Validado con el dueño del batch")
    correr(w)
    r = ej.obtener(e["id"])
    assert r["estado"] == "exitosa", r["error"]
    assert r["resultado"]["bloqueo_resuelto"] is True
    assert r["aprobador"] == "dba2.demo"
    # Salud no queda con el dato anterior: la ejecución repite la pasada de recolección de la base
    from opscentral import estado
    salud = estado.leer(api_n.almacen, "bd:CORPRD:salud")
    assert salud["origen"] == "ejecucion" and salud["datos"]["bloqueadas"] == 0


def test_alterar_parametros_despues_de_aprobar_se_bloquea(api_n, bd_n, usuarios):
    ej = api_n.ejecuciones
    dba, dba2 = con_mfa(usuarios["dba"]), con_mfa(usuarios["dba2"])
    e = ej.solicitar(dba, "bd.usr.desbloquear", {"base": "CORTST", "usuario": "APP_BATCH"},
                     cambio="INC40001", motivo="Cuenta bloqueada por intentos fallidos")
    ej.aprobar(dba2, e["id"])
    with api_n.almacen.tx() as con:   # alguien cambia el usuario directamente en el almacén
        con.execute(update(ejecucion).where(ejecucion.c.id == e["id"]).values(
            params={"base": "CORTST", "usuario": "SYSTEM"}))
    Worker("bd", bd_n).una_vez()
    r = ej.obtener(e["id"])
    assert r["estado"] == "fallida" and "seguridad" in r["error"]


def test_un_cambio_a_la_vez_por_destino(api_n, usuarios):
    ej, dba = api_n.ejecuciones, usuarios["dba"]
    ej.solicitar(dba, "bd.inv.recompilar", {"base": "CORTST", "cambio": "CHG1001"})
    with pytest.raises(Exception, match="cambio en cola"):
        ej.solicitar(dba, "bd.inv.recompilar", {"base": "CORTST", "cambio": "CHG1001"})


def test_destino_fuera_de_inventario(api_n, usuarios):
    with pytest.raises(Exception, match="inventario"):
        api_n.ejecuciones.solicitar(usuarios["dba"], "bd.ses.bloqueos", {"base": "NOEXISTE"})


def test_recoleccion_y_auditoria(api_n, bd_n, usuarios):
    from opscentral import estado
    m = Worker("bd", bd_n).motor_bd
    r = m.recolectar_base("CORPRD")
    assert r["semaforo"] in ("verde", "amarillo", "rojo")
    s = estado.leer(api_n.almacen, "bd:CORPRD:salud")
    assert s["datos"]["base"] == "CORPRD" and s["antiguedad_s"] < 60
    api_n.ejecuciones.solicitar(usuarios["dba"], "bd.ses.bloqueos", {"base": "CORPRD"})
    ok, n, msg = api_n.auditoria.verificar()
    assert ok and n >= 1, msg
