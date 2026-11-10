"""Reportes HTML de BD generados por script (bd/reportes): formato de las skills, solo lectura,
huella SHA-256 y entrega por la API."""
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from opscentral import api as api_mod
from opscentral.bd.politica import validar_lectura
from opscentral.bd.reportes import analisis, diskgroups, hoja_vida, muestra, sentencias
from opscentral.workers import Worker

H = {"X-Ops-Central": "1"}


def correr(api_n, bd_n, usuario, accion, params):
    e = api_n.ejecuciones.solicitar(usuario, accion, params)
    w = Worker("bd", bd_n)
    for _ in range(3):
        if not w.una_vez():
            break
    r = api_n.ejecuciones.obtener(e["id"])
    assert r["estado"] == "exitosa", r["error"]
    return r


# ---------------------------------------------------------------- scripts (sin Ops Central)
def test_todas_las_consultas_de_los_reportes_son_de_solo_lectura():
    for mod in (analisis, sentencias):
        for nombre, sql in mod.SQL.items():
            assert validar_lectura(sql)[0], (mod.__name__, nombre)
    assert validar_lectura(diskgroups.SQL_DG)[0]


def test_hoja_de_vida_del_paquete_portable_con_cliente_de_muestra():
    nombre, html, res = hoja_vida.construir(muestra.ClienteMuestra("prod_core1"), "prod_core1",
                                            {"dsn": "10.7.0.34:1521/CORPRD", "environment": "prod"})
    assert nombre.startswith("hoja-de-vida-database-corprd-") and not nombre.endswith("-prod.html")
    assert res["not_collected"] == [] and html.rstrip().endswith("</html>")
    assert all(f'id="cat{i}"' in html for i in range(16))


def test_analisis_formato_y_reglas():
    nombre, html, res = analisis.construir(muestra.ClienteMuestra("test_core"), "test_core",
                                           {"dsn": "10.8.1.20:1521/CORTST", "environment": "test"})
    assert re.fullmatch(r"reporte-analisis-database-cortst-\d{8}-test\.html", nombre)
    assert all(f'id="cat{i}"' in html for i in range(10)) and html.rstrip().endswith("</html>")
    # §7.2: el job RMAN "COMPLETED" no cuenta si no hubo backup de datafiles (tipo D)
    assert any(a["id"].startswith("BACKUP-") for a in res["plan"]) or "has_valid_backup_last_7d" in html
    # §A.4: sin histórico nada es NORMAL
    assert "historial insuficiente" in html and "NORMAL" not in html
    # ids {DOMINIO}-{NNN} y prioridad determinística
    assert all(re.fullmatch(r"[A-Z]+-\d{3}", p["id"]) for p in res["plan"])
    assert res["plan"][0]["prioridad"] == "URGENTE" and res["overall_status"] == "CRITICAL"
    # §7.3: LEGADO tiene jobs fallidos e inválidos → hallazgo raíz
    assert {"owner": "LEGADO", "dominios": ["JOBS", "OBJ"]} in res["correlaciones"]


def test_seccion_que_falla_queda_not_collected_sin_abortar():
    class Roto(muestra.ClienteMuestra):
        def query(self, sql, params=None, max_rows=500):
            if "v$sga_target_advice" in sql:
                raise RuntimeError("ORA-00942: table or view does not exist")
            return super().query(sql, params, max_rows)
    _, html, res = analisis.construir(Roto("x"), "x", {"environment": "prod"})
    assert any(nc.startswith("memoria (ORA-00942") for nc in res["not_collected"])
    assert "NOT_COLLECTED" in html


def test_diskgroups_agrupa_por_servidor_deduplica_rac_y_filtra():
    entradas = [{"alias": "prod_core1", "dsn": "10.7.0.34:1521/CORPRD"},
                {"alias": "prod_core2", "dsn": "10.7.0.35:1521/CORPRD"},
                {"alias": "prod_hist", "dsn": "10.7.0.91:1521/HISPRD"},
                {"alias": "caido", "dsn": "10.7.0.99:1521/X"}]

    def abrir(e):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            if e["alias"] == "caido":
                raise RuntimeError("ORA-12170: TNS:Connect timeout occurred")
            yield muestra.ClienteMuestra(e["alias"])
        return cm()

    nombre, html, res = diskgroups.construir(entradas, abrir, "prod", 50)
    assert re.fullmatch(r"reporte_diskgroups_prod_\d{8}\.html", nombre)
    assert res["servidores_consultados"] == 1 and res["errores"] == 1          # .34 y .35 son el mismo RAC
    assert "Cluster RAC — 10.7.0.34 / 10.7.0.35" in html and "10.7.0.34/35" in html
    assert "sin ASM" in html and "ORA-12170" in html
    assert html.count('class="server-group"') == 1
    pcts = [float(x) for x in re.findall(r'class="pct-label">([\d.]+)%', html)]
    assert pcts == sorted(pcts, reverse=True) and min(pcts) >= 50
    assert diskgroups.estado(95)[0] == "CRITICO" and diskgroups.estado(75)[0] == "WARNING" \
        and diskgroups.estado(55)[0] == "MODERADO"


def test_sentencias_panel_por_sql_id_y_origen():
    nombre, html, res = sentencias.construir(muestra.ClienteMuestra("prod_core1"), "Prod Core1",
                                             {"environment": "prod"}, criterio="CPU")
    assert re.fullmatch(r"reporte_sentencias_costosas_prod_core1_\d{8}\.html", nombre)
    assert res["investigadas"] == 10 and res["criterio"] == "CPU"
    for f in res["top"]:
        assert f'id="d-{f["sql_id"]}"' in html and f'data-id="{f["sql_id"]}"' in html
    assert "Job DBMS_SCHEDULER" in html and "dblink desde otra BD" in html and "Programa COBOL batch" in html
    assert "<script>" in html and "Por qué es costosa" in html and "linear-gradient(135deg,#1a237e,#283593)" in html
    assert sentencias.clasificar("oracle@db01 (TNS V1-V3)", None, {})[0] == "dblink desde otra BD"
    razones, _ = sentencias.por_que({"executions": 1, "elapsed_sec": 5, "cpu_sec": 1, "elapsed_por_exec": 5},
                                    "SELECT * FROM t WHERE UPPER(x) LIKE '%a'")
    assert len(razones) >= 4


# ---------------------------------------------------------------- dentro de Ops Central
def test_los_cuatro_reportes_desde_el_motor_bd(api_n, bd_n, usuarios, cfg):
    raiz = cfg.ruta("reportes", "ruta")
    casos = [("bd.rep.hoja_vida", {"base": "CORPRD"}, "hoja-de-vida-database-"),
             ("bd.rep.analisis", {"base": "CORTST"}, "reporte-analisis-database-"),
             ("bd.rep.diskgroups", {"ambiente": "PROD", "umbral": 60}, "reporte_diskgroups_prod_"),
             ("bd.rep.sql_costosas", {"base": "CORTST", "criterio": "GETS"}, "reporte_sentencias_costosas_cortst_")]
    for accion, params, prefijo in casos:
        e = correr(api_n, bd_n, usuarios["dba"], accion, params)
        info = e["resultado"]["reporte"]
        assert info["nombre"].startswith(prefijo) and e["resultado"]["muestra"] is False
        datos = (raiz / info["archivo"]).read_bytes()
        assert hashlib.sha256(datos).hexdigest() == info["sha256"] and len(datos) == info["bytes"]
        assert "DATOS DE MUESTRA" not in datos.decode("utf-8")
    # la hoja de vida conserva además el resultado estructurado de siempre
    e = correr(api_n, bd_n, usuarios["dba"], "bd.rep.hoja_vida", {"base": "CORPRD"})
    assert e["resultado"]["datos"]["hoja_vida"]["ambiente"] == "PROD"


def test_el_sql_de_reportes_pasa_por_la_politica_y_la_cuenta_de_lectura(bd_n):
    from opscentral.bd.conexion import ErrorBD
    from opscentral.credenciales import CredencialBD
    from .dobles import oracle_falso as simulador
    base = bd_n.inventario.base("CORTST")

    def conexion(tipo):
        return simulador.ConexionFalsa(base, CredencialBD("OPS_X", "x", tipo), persona="p", ejecucion_id="e",
                                       paquete="P", call_timeout_s=5)
    with pytest.raises(ErrorBD, match="consultas fijas"):
        conexion("consulta").consultar_sql("DELETE FROM app_core.clientes")
    with pytest.raises(ErrorBD, match="consultas fijas"):     # aunque sea de solo lectura
        conexion("consulta").consultar_sql("SELECT password FROM sys.user$")
    with pytest.raises(ErrorBD, match="lectura"):
        conexion("dba").consultar_sql(diskgroups.SQL_DG)
    assert conexion("consulta").consultar_sql("  " + diskgroups.SQL_DG.replace("\n", "\n   ") + ";")


def test_las_consultas_fijas_cubren_todo_lo_que_mandan_los_scripts():
    from opscentral.bd.reportes import es_plantilla_fija, plantillas_fijas
    vistas = []

    class Espia(muestra.ClienteMuestra):
        def query(self, sql, params=None, max_rows=500):
            vistas.append(sql)
            return super().query(sql, params, max_rows)
    hoja_vida.construir(Espia("a"), "a", {"environment": "prod"})
    analisis.construir(Espia("a"), "a", {"environment": "prod"})
    for crit in ("CPU", "ELAPSED", "GETS"):
        sentencias.construir(Espia("a"), "a", {"environment": "prod"}, criterio=crit)
    assert vistas and all(es_plantilla_fija(s) for s in vistas)
    assert all(validar_lectura(s)[0] for s in plantillas_fijas())


def test_etapas_del_diseno_se_emiten_en_orden(api_n, bd_n, usuarios):
    for accion, params, mod in (("bd.rep.hoja_vida", {"base": "CORPRD"}, hoja_vida),
                                ("bd.rep.analisis", {"base": "CORTST"}, analisis),
                                ("bd.rep.diskgroups", {"ambiente": "TEST"}, diskgroups),
                                ("bd.rep.sql_costosas", {"base": "CORTST"}, sentencias)):
        e = correr(api_n, bd_n, usuarios["dba"], accion, params)
        lineas = [x["texto"] for x in api_n.ejecuciones.lineas(e["id"])]
        etapas = [t.split(": ", 1)[1] for t in lineas if re.match(r"Etapa \d+/\d+: ", t)]
        assert etapas == mod.ETAPAS, (accion, etapas)


def test_api_lista_sirve_y_descarga_el_reporte(api_n, bd_n, usuarios, cfg):
    e = correr(api_n, bd_n, usuarios["dba"], "bd.rep.sql_costosas", {"base": "CORTST"})
    c = TestClient(api_mod.crear_app(cfg, api_n))
    assert c.post("/api/auth/dev/ingresar", json={"login": "dba.demo"}, headers=H).status_code == 200
    fila = next(r for r in c.get("/api/reportes").json() if r["ejecucion"] == e["id"])
    assert any(r["ejecucion"] == e["id"] for r in c.get("/api/reportes", params={"ambiente": "test"}).json())
    assert all(r["ejecucion"] != e["id"] for r in c.get("/api/reportes", params={"ambiente": "PROD"}).json())
    assert all(x["ambiente"] == "TEST" for x in c.get("/api/ejecuciones", params={"ambiente": "TEST"}).json())
    assert fila["tipo"] == "Sentencias costosas" and fila["muestra"] is False and fila["nombre"].endswith(".html")
    t = {x["accion"]: x for x in c.get("/api/reportes/tipos").json()}
    assert t["bd.rep.diskgroups"]["alcance"] == "ambiente" and t["bd.rep.analisis"]["etapas"] == analisis.ETAPAS
    assert t["bd.rep.hoja_vida"]["skill"] == "hoja-de-vida-bd"
    r = c.get(f"/api/ejecuciones/{e['id']}/reporte")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-reporte-sha256"] == e["resultado"]["reporte"]["sha256"] and "<!DOCTYPE html>" in r.text
    d = c.get(f"/api/ejecuciones/{e['id']}/reporte", params={"descargar": True})
    assert d.headers["content-disposition"].startswith("attachment;")
    # otro perfil sin permiso sobre la acción no la ve
    cm = TestClient(api_mod.crear_app(cfg, api_n))
    cm.post("/api/auth/dev/ingresar", json={"login": "cm.demo"}, headers=H)
    assert all(x["ejecucion"] != e["id"] for x in cm.get("/api/reportes").json())
    assert cm.get(f"/api/ejecuciones/{e['id']}/reporte").status_code == 404
    # archivo alterado → 409
    (cfg.ruta("reportes", "ruta") / e["resultado"]["reporte"]["archivo"]).write_text("alterado", encoding="utf-8")
    assert c.get(f"/api/ejecuciones/{e['id']}/reporte").status_code == 409


def test_modo_muestra_marca_el_reporte_y_no_se_permite_en_produccion(api_n, bd_n, usuarios, cfg):
    from opscentral import config
    bd_n.cfg["bd"]["reportes_muestra"] = True
    try:
        e = correr(api_n, bd_n, usuarios["dba"], "bd.rep.analisis", {"base": "CORTST"})
    finally:
        bd_n.cfg["bd"]["reportes_muestra"] = False
    assert e["resultado"]["muestra"] is True
    html = (cfg.ruta("reportes", "ruta") / e["resultado"]["reporte"]["archivo"]).read_text(encoding="utf-8")
    assert "DATOS DE MUESTRA" in html
    with pytest.raises(ValueError, match="reportes_muestra"):
        config.cargar(extra={"general": {"modo": "produccion"}, "auth": {"proveedor": "oidc"},
                             "secretos": {"proveedor": "vault"}, "almacen": {"url": "postgresql://x/y"},
                             "bd": {"reportes_muestra": True}})
