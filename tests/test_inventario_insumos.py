"""Generador del inventario a partir de los insumos reales (formatos de capa-media-ops y del kit Oracle)."""
import json

from opscentral.inventario import Inventario
from opscentral.inventario_insumos import generar


def _insumos(tmp_path, relaciones=None):
    cm = {"servidores": {
        "ap01": {"ip": "10.0.0.1", "ambientes": ["stage", "prod"],
                 "dominios": [{"nombre": "pagos", "ambiente": "prod"}, {"nombre": "pagos", "ambiente": "stage"}]},
        "ap02": {"ip": "10.0.0.2", "ambientes": ["prod"], "dominios": [{"nombre": "pagos", "ambiente": "prod"}]},
        "ap03": {"ip": "10.0.0.3", "ambientes": ["desarrollo"], "habilitado": False, "dominios": []},
    }}
    kit = {"databases": [
        {"alias": "ventas", "dsn": "10.9.0.1:1521/VENTAS", "environment": "test"},
        {"alias": "prod_ventas1", "dsn": "10.9.0.5:1521/ventas", "environment": "prod"},
        {"alias": "prod_ventas2", "dsn": "10.9.0.6:1521/ventas", "environment": "prod"},
        {"alias": "nomina", "dsn": "10.9.0.7:1521/nomina", "environment": "prod"},
        {"alias": "roto", "dsn": "sin-formato", "environment": "prod"},
    ]}
    rutas = {"servidores_cm": tmp_path / "s.json", "bases": tmp_path / "b.json",
             "bases_csv": tmp_path / "b.csv", "mapa_oracle": tmp_path / "m.yaml", "relaciones": tmp_path / "r.json"}
    rutas["servidores_cm"].write_text(json.dumps(cm), encoding="utf-8")
    rutas["bases"].write_text(json.dumps(kit), encoding="utf-8")
    rutas["bases_csv"].write_text("alias,dsn,criticality,owner_team\nnomina,//10.9.0.7:1521/nomina,alto,dba-aoc\n",
                                  encoding="utf-8")
    rutas["mapa_oracle"].write_text('BD01:\n  - { service: ventas, dsn: "10.9.0.5:1521/ventas" }\n', encoding="utf-8")
    if relaciones is not None:
        rutas["relaciones"].write_text(json.dumps(relaciones), encoding="utf-8")
    return rutas


def test_servidores_dominios_y_bases(tmp_path):
    inv, avisos = generar(_insumos(tmp_path))
    srv = {s["id"]: s for s in inv["servidores"]}
    assert "AP03" not in srv                                   # deshabilitado
    assert srv["AP01"]["ambiente"] == "PROD"                   # el más protegido
    dom = {d["id"]: d for d in inv["dominios"]}
    assert dom["pagos (PROD)"]["servidores"] == ["AP01", "AP02"] and dom["pagos (QA)"]["ambiente"] == "QA"
    bases = {b["id"]: b for b in inv["bases"]}
    # servicio repetido → alias; servicio único → nombre del servicio; ninguna conexión se pierde
    assert set(bases) == {"VENTAS", "PROD_VENTAS1", "PROD_VENTAS2", "NOMINA"}
    assert bases["PROD_VENTAS1"]["servidor"] == "BD01" and bases["PROD_VENTAS2"]["servidor"] == "BD-10.9.0.6"
    assert bases["NOMINA"]["criticidad"] == "alta" and bases["NOMINA"]["equipo_dueno"] == "dba-aoc"
    assert all("clave" not in json.dumps(b).lower() for b in inv["bases"])
    assert any("sin-formato" in a for a in avisos) and any("sin relaciones" in a for a in avisos)
    i = Inventario(inv)
    assert i.ambiente_de("PROD_VENTAS1") == "PROD" and i.servidor("BD01").datos["equipo"] == "dba"


def test_relaciones_opcionales(tmp_path):
    inv, avisos = generar(_insumos(tmp_path, [{"dominio": "pagos (PROD)", "base": "nomina"},
                                              {"dominio": "no-existe", "base": "NOMINA"}]))
    assert inv["relaciones"] == [{"dominio": "pagos (PROD)", "base": "NOMINA", "datasource": None}]
    assert any("relación ignorada" in a for a in avisos)
    i = Inventario(inv)
    assert "base:NOMINA" in i.relacionados("servidor:AP02")
