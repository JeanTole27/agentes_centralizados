"""Salud de bases de datos: recolección parcial, último intento y filtro por ambiente
(informe salud-bases-no-actualiza-2026-11-11)."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text, update

from opscentral import api as api_mod, estado
from opscentral.almacen import Almacen, estado_actual, utc
from opscentral.workers import Worker

from .dobles.oracle_falso import ConexionFalsa

H = {"X-Ops-Central": "1"}


def _con_fallas(fallas: dict[str, str]):
    class Conexion(ConexionFalsa):
        def consultar(self, plantilla, params=None):
            if plantilla in fallas:
                raise RuntimeError(fallas[plantilla])
            return super().consultar(plantilla, params)
    return Conexion


def test_linea_base_ausente_no_tumba_la_recoleccion(api_n, bd_n):
    m = Worker("bd", bd_n).motor_bd
    m.clase_conexion = _con_fallas({"linea_base": "ORA-00942: table or view does not exist",
                                    "bloqueos": "DPY-4024: call timeout of 20000 ms exceeded"})
    r = m.recolectar_base("CORPRD")
    assert r["semaforo"] in ("verde", "amarillo", "rojo") and r["parcial"] == ["bloqueos", "linea_base"]
    s = estado.leer(api_n.almacen, "bd:CORPRD:salud")
    assert s["error"] is None and s["con_dato"] and s["datos"]["tablespaces"]
    assert set(s["datos"]["parciales"]) == {"linea_base", "bloqueos"}
    assert s["datos"]["invalidos_fuera_lb"] is None and s["datos"]["bloqueadas"] is None
    assert s["datos"]["invalidos"] is not None
    assert any("línea base" in x for x in s["datos"]["motivos"])
    # sin bloqueos no se pisa el estado de bloqueos con algo vacío
    assert estado.leer(api_n.almacen, "bd:CORPRD:bloqueos") is None


def test_error_conserva_dato_bueno_y_avanza_el_intento(api_n, bd_n):
    m = Worker("bd", bd_n).motor_bd
    m.recolectar_base("CORPRD")
    antes = utc() - timedelta(hours=5)
    with api_n.almacen.tx() as con:
        con.execute(update(estado_actual).where(estado_actual.c.objeto == "bd:CORPRD:salud")
                    .values(actualizado=antes, intentado=antes))
    m.clase_conexion = _con_fallas({"salud_instancia": "ORA-01034: ORACLE not available"})
    r = m.recolectar_base("CORPRD")
    assert r["semaforo"] == "sin_dato" and "ORA-01034" in r["error"]
    s = estado.leer(api_n.almacen, "bd:CORPRD:salud")
    assert "ORA-01034" in s["error"] and s["con_dato"] and s["datos"]["base"] == "CORPRD"
    assert s["antiguedad_s"] > 4 * 3600          # el último dato bueno sigue siendo el de hace 5 h
    assert s["intento_s"] < 60                   # pero se ve que se reintentó ahora


def test_filtro_por_ambiente_incluye_filas_que_solo_tienen_error(api_n, bd_n, cfg):
    estado.marcar_error(api_n.almacen, "bd:CORTST:salud", "bd.salud", "DPY-3015: password verifier", origen="barrido")
    Worker("bd", bd_n).motor_bd.recolectar_base("CORPRD")
    c = TestClient(api_mod.crear_app(cfg, api_n))
    assert c.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=H).status_code == 200
    test = c.get("/api/estado/bd/salud", params={"ambiente": "TEST"}).json()
    assert [f["objeto"] for f in test] == ["bd:CORTST:salud"]
    assert test[0]["ambiente"] == "TEST" and test[0]["con_dato"] is False and "intento_s" in test[0]
    prod = c.get("/api/estado/bd/salud", params={"ambiente": "prod"}).json()
    assert [f["objeto"] for f in prod] == ["bd:CORPRD:salud"]


def test_menu_dice_salud_de_bases_de_datos():
    from opscentral import menu
    assert "Salud de bases de datos" in repr(vars(menu))


def test_crear_esquema_agrega_la_columna_intentado_a_un_almacen_viejo(tmp_path):
    url = f"sqlite:///{tmp_path / 'viejo.db'}"
    eng = create_engine(url)
    with eng.begin() as con:
        con.execute(text("CREATE TABLE estado_actual (objeto VARCHAR(200) PRIMARY KEY, tipo VARCHAR(40) NOT NULL, "
                         "datos JSON NOT NULL, origen VARCHAR(40) NOT NULL, actualizado TIMESTAMP NOT NULL, error TEXT)"))
    eng.dispose()
    a = Almacen(url)
    a.crear_esquema()
    assert "intentado" in {c["name"] for c in inspect(a.engine).get_columns("estado_actual")}
    estado.marcar_error(a, "bd:X:salud", "bd.salud", "ORA-00942", origen="barrido")
    assert estado.leer(a, "bd:X:salud")["intento_s"] < 60


# ---------------------------------------------------------------- línea base con la tabla del equipo DBA
def test_plantilla_linea_base_usa_solo_columnas_de_la_tabla_del_dba():
    from opscentral.bd.plantillas import PLANTILLAS
    sql = PLANTILLAS["linea_base"].lower()
    for col in ("activo", "justificacion", "registrado_por"):
        assert col not in sql
    assert "owner" in sql and "object_name" in sql and "object_type" in sql and "fecha_registro" in sql


def _snapshots(api_n, w, dba, grupo):
    from .dobles.oracle_falso import simular_despliegue
    for momento in ("ANTES", "DESPUES"):
        if momento == "DESPUES":
            simular_despliegue("CORTST")
        e = api_n.ejecuciones.solicitar(dba, "bd.snap.tomar", {"base": "CORTST", "cambio": grupo, "momento": momento})
        for _ in range(4):
            if not w.una_vez():
                break
        assert api_n.ejecuciones.obtener(e["id"])["estado"] == "exitosa"


def test_generar_sentencias_sigue_si_la_linea_base_falla(api_n, bd_n, usuarios):
    import pytest
    for error in ("ORA-00942: table or view does not exist",
                  'ORA-00904: "ACTIVO": invalid identifier'):
        w = Worker("bd", bd_n)
        w.motor_bd.clase_conexion = _con_fallas({"linea_base": error})
        grupo = "CHG" + error[4:9]
        _snapshots(api_n, w, usuarios["dba"], grupo)
        e = api_n.ejecuciones.solicitar(usuarios["dba"], "bd.inv.generar", {"base": "CORTST", "cambio": grupo})
        for _ in range(4):
            if not w.una_vez():
                break
        r = api_n.ejecuciones.obtener(e["id"])
        assert r["estado"] == "exitosa", r["error"]
        assert r["resultado"]["linea_base_disponible"] is False and error[:9] in r["resultado"]["aviso_linea_base"]
        assert {o["object_name"] for o in r["resultado"]["plan"]} == {"PKG_CLIENTES", "VW_SALDOS", "TRG_AUDITA_CLIENTE"}
        assert any("Advertencia: Línea base" in x["texto"] for x in api_n.ejecuciones.lineas(e["id"]))
        from .dobles import oracle_falso
        oracle_falso._estado("CORTST")["cambio_aplicado"] = False   # siguiente vuelta: despliegue de nuevo
        oracle_falso._estado("CORTST")["invalidos"] = [o for o in oracle_falso._estado("CORTST")["invalidos"]
                                                        if o["object_name"] not in ("PKG_CLIENTES", "VW_SALDOS", "TRG_AUDITA_CLIENTE")]
    # otros errores (p. ej. de conexión) sí frenan la comparación
    w = Worker("bd", bd_n)
    w.motor_bd.clase_conexion = _con_fallas({"linea_base": "DPY-6005: cannot connect"})
    with pytest.raises(Exception, match="DPY-6005"):
        w.motor_bd.comparar("CORTST", "CHG00942", persona="p", ejecucion_id="e")


def test_script_01_compatible_con_la_tabla_del_dba():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "db" / "oracle" / "01_pkg_ops_central.sql").read_text(encoding="utf-8")
    assert "activo" not in sql.lower().replace("activo = 'y'", "")
    assert "FROM dba_tables WHERE owner = 'ADMINBD' AND table_name = 'TBL_BASE_OBJETOS_INVALIDOS'" in sql
    # Ops Central no escribe en la línea base: ni INSERT, ni DELETE, ni UPDATE, ni MERGE sobre ella
    for verbo in ("INSERT INTO", "DELETE FROM", "UPDATE", "MERGE INTO"):
        assert f"{verbo} adminbd.tbl_base_objetos_invalidos" not in sql.lower().replace(verbo.lower(), verbo)
    assert "PROCEDURE ACTUALIZAR_LINEA_BASE" not in sql


def test_linea_base_es_solo_de_consulta(api_n, usuarios):
    import pytest
    from opscentral.bd.conexion import ErrorBD
    from opscentral.bd.politica import PROCEDIMIENTOS, validar_procedimiento
    assert "ACTUALIZAR_LINEA_BASE" not in PROCEDIMIENTOS
    with pytest.raises((ErrorBD, ValueError, PermissionError)):
        r = validar_procedimiento("ACTUALIZAR_LINEA_BASE")
        if isinstance(r, tuple) and not r[0]:
            raise ValueError(r[1])
    assert api_n.catalogo["bd.lb.actualizar"].estado != "implementada"
    with pytest.raises(Exception):
        api_n.ejecuciones.solicitar(usuarios["dba"], "bd.lb.actualizar", {
            "base": "CORTST", "operacion": "AGREGAR", "propietario": "APP_CORE", "objeto": "X",
            "tipo_objeto": "VIEW", "justificacion": "justificación de prueba larga"})
    # ver la línea base sigue disponible (lectura)
    assert api_n.catalogo["bd.lb.ver"].estado == "implementada"
