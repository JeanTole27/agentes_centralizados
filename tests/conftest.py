from pathlib import Path
import pytest

from opscentral import config, nucleo
from opscentral.bd.servicio import MotorBD
from opscentral.zabbix import ingesta

from .dobles import oracle_falso as simulador
from .dobles import zabbix_falso as sim_zabbix
from opscentral.identidad import ingreso_dev, ahora


@pytest.fixture
def cfg(tmp_path):
    return config.cargar(extra={
        "general": {"modo": "desarrollo"},
        "zabbix": {"url": "https://zabbix.pruebas.local"},
        "almacen": {"url": "sqlite:///" + (tmp_path / "ops.db").as_posix()},
        "evidencia": {"ruta": str(tmp_path / "evidencia")},
        "inventario": {"archivo": str(Path(__file__).parent / "dobles" / "inventario_pruebas.json")},
    })


@pytest.fixture(autouse=True)
def _sim(monkeypatch):
    # Los dobles viven en tests/dobles: el producto siempre usa Oracle y la API real de Zabbix.
    monkeypatch.setattr(MotorBD, "clase_conexion", simulador.ConexionFalsa)
    original = ingesta.crear_cliente

    def cliente_falso(cfg, secretos, inv, *, transporte=None):
        cli = original(cfg, secretos, inv, transporte=sim_zabbix.instancia(list(inv.hosts_zabbix())).transporte())
        cli._espera = 0
        return cli
    monkeypatch.setattr(ingesta, "crear_cliente", cliente_falso)
    simulador.reiniciar()
    sim_zabbix.reiniciar()
    yield
    simulador.reiniciar()
    sim_zabbix.reiniciar()


@pytest.fixture
def api_n(cfg):
    return nucleo.crear("api", cfg)


@pytest.fixture
def bd_n(cfg, api_n):
    return nucleo.crear("worker-bd", cfg, almacen=api_n.almacen)


@pytest.fixture
def ing_n(cfg, api_n):
    return nucleo.crear("worker-ingesta", cfg, almacen=api_n.almacen)


@pytest.fixture
def usuarios(cfg):
    m = cfg["auth"]["grupos"]
    u = {k: ingreso_dev(k + ".demo", m) for k in ("dba", "dba2", "cm", "cm2", "lider", "consulta", "admin")}
    return u


def con_mfa(u):
    u.mfa_en = ahora()
    return u
