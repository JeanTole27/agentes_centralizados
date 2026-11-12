"""Cambios por el feedback de la demo de Base de Datos (2026-11-12).

- Salud, bloqueos e inválidos se actualizan por alerta de Zabbix, solo la sección y las bases afectadas.
- La recolección periódica es liviana; las secciones no leídas conservan su último valor.
- Inválidos: sin papelera ni esquemas excluidos.
- Vínculo de servidores con Zabbix por IP, guardado como ajuste del inventario.
"""
from datetime import datetime, timezone

from opscentral import estado
from opscentral.bd import snapshot
from opscentral.workers import Worker
from opscentral.zabbix import ingesta, procesar
from opscentral.zabbix.cliente import ClienteZabbix
from opscentral.zabbix.webhook import Evento

from .dobles.zabbix_falso import SimuladorZabbix


def _ev(host, nombre, eid="77001", problema=True):
    return Evento(eventid=eid, host=host, nombre=nombre, severidad=4, problema=problema,
                  inicio=datetime.now(timezone.utc), en_mantenimiento=False, etiquetas={}, crudo={})


def _trabajos(alm):
    from sqlalchemy import select
    from opscentral.almacen import trabajo
    with alm.tx() as con:
        return [dict(f._mapping) for f in con.execute(select(trabajo).where(trabajo.c.tipo == "refrescar"))]


# ---------------------------------------------------------------- refresco dirigido por alerta
def test_alerta_de_bloqueo_en_servidor_refresca_solo_bloqueos_de_la_base_nombrada(cfg, api_n):
    r = procesar.procesar(api_n.almacen, api_n.cola, api_n.inventario, _ev("DBPRD01", "Lock wait detected on CORPRD"),
                          ventana_min=15, refresco_min_s=120)
    assert r["refresco"]["estado"] == "encolado" and r["refresco"]["secciones"] == ["bloqueos"]
    assert set(r["refresco"]["bases"]) == {"CORPRD"}
    assert r["sugerencia"] == "bd.ses.bloqueos"
    t = _trabajos(api_n.almacen)
    assert len(t) == 1 and t[0]["payload"]["secciones"] == ["bloqueos"] and t[0]["payload"]["base"] == "CORPRD"


def test_alerta_del_servidor_sin_base_nombrada_refresca_todas_sus_bases_solo_instancia(cfg, api_n):
    r = procesar.procesar(api_n.almacen, api_n.cola, api_n.inventario, _ev("DBPRD01", "High CPU utilization"),
                          ventana_min=15, refresco_min_s=120)
    assert set(r["refresco"]["bases"]) == {"CORPRD", "CRMPRD"} and r["refresco"]["secciones"] == []


def test_secciones_por_trigger():
    assert procesar.secciones_de(_ev("x", "Tablespace USERS is 96% full")) == ["tablespaces"]
    assert procesar.secciones_de(_ev("x", "ASM diskgroup DATA usage high")) == ["diskgroups"]
    assert procesar.secciones_de(_ev("x", "Invalid objects count > 0")) == ["invalidos"]
    assert procesar.secciones_de(_ev("x", "Instance is down")) == []


# ---------------------------------------------------------------- recolección por secciones
def test_recolectar_solo_bloqueos_conserva_espacio_e_invalidos_previos(api_n, bd_n):
    m = Worker("bd", bd_n).motor_bd
    m.recolectar_base("CORPRD")                                # completa
    antes = estado.leer(api_n.almacen, "bd:CORPRD:salud")["datos"]
    r = m.recolectar_base("CORPRD", origen="evento", secciones=["bloqueos"])
    assert r["secciones"] == ["bloqueos"]
    d = estado.leer(api_n.almacen, "bd:CORPRD:salud")["datos"]
    assert d["tablespaces"] == antes["tablespaces"] and d["invalidos"] == antes["invalidos"]
    assert d["ultima_pasada"] == {"origen": "evento", "secciones": ["bloqueos"]}
    assert d["leidas"]["bloqueos"] > antes["leidas"]["bloqueos"]
    assert d["leidas"]["tablespaces"] == antes["leidas"]["tablespaces"]


def test_pasada_liviana_no_toca_bloqueos_ni_invalidos(api_n, bd_n):
    m = Worker("bd", bd_n).motor_bd
    leidas = []

    class Conexion(m.clase_conexion):
        def consultar(self, plantilla, params=None):
            leidas.append(plantilla)
            return super().consultar(plantilla, params)
    m.clase_conexion = Conexion
    m.recolectar_base("CORPRD", secciones=["tablespaces", "diskgroups"])
    assert set(leidas) == {"salud_instancia", "tablespaces", "diskgroups"}


def test_objeto_del_bloqueo_se_busca_aparte(api_n, bd_n):
    m = Worker("bd", bd_n).motor_bd
    from tests.dobles import oracle_falso
    oracle_falso._estado("CORPRD")["bloqueo"] = True
    m.recolectar_base("CORPRD", secciones=["bloqueos"])
    b = estado.leer(api_n.almacen, "bd:CORPRD:bloqueos")["datos"]
    assert b["total_bloqueadas"] == 4 and b["arboles"][0]["objetos"] == ["APP_CORE.CLIENTES"]


# ---------------------------------------------------------------- inválidos
def test_invalidos_sin_papelera_ni_esquemas_excluidos(api_n, bd_n):
    filas = [{"owner": "APP", "object_name": "P1", "object_type": "PACKAGE"},
             {"owner": "PUBLIC", "object_name": "SYN1", "object_type": "SYNONYM"},
             {"owner": "APEX_210200", "object_name": "X", "object_type": "VIEW"},
             {"owner": "ORDS_METADATA", "object_name": "Y", "object_type": "VIEW"}]
    assert [o["owner"] for o in snapshot.excluir(filas, ["PUBLIC", "APEX_%", "ORDS_METADATA"])] == ["APP"]
    m = Worker("bd", bd_n).motor_bd
    m.recolectar_base("CORPRD", secciones=["invalidos"])
    d = estado.leer(api_n.almacen, "bd:CORPRD:salud")["datos"]
    # el doble tiene 1 en línea base + 1 de la papelera (BIN$): la papelera no cuenta
    assert d["invalidos"] == 1 and d["invalidos_en_lb"] == 1 and d["invalidos_fuera_lb"] == 0


# ---------------------------------------------------------------- vínculo con Zabbix por IP
def test_vincular_servidores_por_ip_queda_como_ajuste(cfg, api_n):
    sim = SimuladorZabbix(["BD104ZBX"])
    sim.ips["BD104ZBX"] = "10.1.0.104"                  # IP de AP104SBCC04 en el inventario de pruebas
    cli = ClienteZabbix("https://zbx.local", "tok", transporte=sim.transporte(), espera_base=0)
    r = ingesta.vincular_hosts(api_n.almacen, api_n.inventario, cli, api_n.inv_ajustes)
    assert r["vinculados"] == [{"servidor": "AP104SBCC04", "ip": "10.1.0.104", "zabbix_host": "BD104ZBX"}]
    assert api_n.inventario.por_zabbix("BD104ZBX").id == "AP104SBCC04"
    assert any(a["clave"] == "servidor:AP104SBCC04" for a in api_n.inv_ajustes.ajustes())
