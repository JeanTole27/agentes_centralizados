import pytest

from opscentral import config, nucleo
from opscentral.bd import simulador
from opscentral.identidad import ingreso_dev, ahora


@pytest.fixture
def cfg(tmp_path):
    return config.cargar(extra={
        "general": {"modo": "desarrollo", "simulacion": True},
        "almacen": {"url": "sqlite:///" + (tmp_path / "ops.db").as_posix()},
        "evidencia": {"ruta": str(tmp_path / "evidencia")},
    })


@pytest.fixture(autouse=True)
def _sim():
    simulador.reiniciar()
    yield
    simulador.reiniciar()


@pytest.fixture
def api_n(cfg):
    return nucleo.crear("api", cfg)


@pytest.fixture
def bd_n(cfg, api_n):
    return nucleo.crear("worker-bd", cfg, almacen=api_n.almacen)


@pytest.fixture
def usuarios(cfg):
    m = cfg["auth"]["grupos"]
    u = {k: ingreso_dev(k + ".demo", m) for k in ("dba", "dba2", "cm", "cm2", "lider", "consulta", "admin")}
    return u


def con_mfa(u):
    u.mfa_en = ahora()
    return u
