import pytest

from opscentral import aprobaciones
from opscentral.autorizacion import decidir, permiso_de, puede_aprobar
from opscentral.catalogo import Catalogo, ErrorValidacion
from opscentral.identidad import SinPerfil, construir_usuario, perfiles_desde_grupos
from opscentral.secretos import AccesoSecretoDenegado, Secretos, SecretosArchivo
from opscentral.config import RAIZ

CAT = Catalogo.cargar()
K = "clave-de-prueba-de-32-caracteres-o-mas"


def test_catalogo_cargado_y_coherente():
    assert len(CAT) >= 100
    for a in CAT:
        if a.motor == "bd" and a.es_cambio and a.estado == "implementada":
            assert a.procedimiento, a.id
            assert a.cuenta_bd == "dba"
        if a.motor == "bd" and not a.es_cambio:
            assert a.cuenta_bd == "consulta"


def test_validacion_de_parametros():
    a = CAT["bd.ses.terminar"]
    p = a.validar_parametros({"base": "corprd", "sid": "1234", "serial": 5678, "motivo": "bloqueo del batch nocturno"})
    assert p == {"base": "CORPRD", "sid": 1234, "serial": 5678, "inst_id": 1, "motivo": "bloqueo del batch nocturno"}
    with pytest.raises(ErrorValidacion):
        a.validar_parametros({"base": "CORPRD", "sid": 0, "serial": 1, "motivo": "x" * 12})
    with pytest.raises(ErrorValidacion):
        a.validar_parametros({"base": "CORPRD; DROP", "sid": 1, "serial": 1, "motivo": "x" * 12})
    with pytest.raises(ErrorValidacion):
        a.validar_parametros({"base": "CORPRD", "sid": 1, "serial": 1, "motivo": "x" * 12, "sql": "ALTER SYSTEM"})


def test_no_se_aceptan_claves_en_parametros():
    with pytest.raises(ErrorValidacion, match="credencial"):
        CAT["usr.aplicar"].validar_parametros({"host": "WLPC02SBCC04", "acciones": [{"usuario": "x", "password": "y"}]})


def test_perfil_por_grupos_union_y_rechazo():
    m = {"GG_OPS_DBA": "dba", "GG_OPS_CM": "cm"}
    assert perfiles_desde_grupos(["CN=GG_OPS_DBA,OU=Grupos,DC=empresa", "gg_ops_cm"], m) == {"dba", "cm"}
    with pytest.raises(SinPerfil):
        construir_usuario("x", "X", ["OTRO_GRUPO"], m)


def test_matriz_de_permisos(usuarios):
    dba, cm, con, lid = usuarios["dba"], usuarios["cm"], usuarios["consulta"], usuarios["lider"]
    terminar = CAT["bd.ses.terminar"]
    assert not decidir(cm, terminar, "TEST").permitido            # CM no ejecuta cambios BD
    assert not decidir(con, CAT["bd.ses.bloqueos"], "TEST").permitido  # consulta solo ve
    d = decidir(dba, terminar, "PROD")          # con permiso: directo, MFA, caso opcional
    assert d.permitido and d.directa and not d.requiere_aprobacion and not d.requiere_cambio and d.requiere_stepup
    assert decidir(dba, terminar, "PROD", exigir_referencia_prod=True).requiere_cambio
    d = decidir(dba, terminar, "PROD", ejecucion_directa=False)
    assert d.requiere_aprobacion and not d.directa
    assert decidir(lid, terminar, "PROD").directa                  # sin cola de aprobación para nadie
    assert not decidir(lid, CAT["bd.usr.desbloquear"], "TEST").requiere_cambio   # ya no exige ticket
    d = decidir(dba, CAT["bd.inv.recompilar"], "TEST")
    assert d.permitido and d.clase == "O" and not d.requiere_aprobacion
    assert decidir(dba, CAT["bd.inv.recompilar"], "PROD").clase == "C"
    assert not decidir(dba, CAT["orq.stop"], "DESA").permitido     # DBA no baja dominios
    assert not decidir(lid, CAT["orq.stop"], "PROD").requiere_aprobacion       # Capa Media: directo
    assert decidir(lid, CAT["orq.stop"], "PROD", ejecucion_directa=False).requiere_aprobacion
    assert not decidir(dba, CAT["bd.grp.crear"], "TEST").permitido   # no cabe en OPS_DBA


def test_cuatro_ojos(usuarios):
    t = CAT["bd.ses.terminar"]
    assert not puede_aprobar(usuarios["dba"], "dba.demo", t)[0]   # propia
    assert puede_aprobar(usuarios["dba2"], "dba.demo", t)[0]       # otro DBA
    assert not puede_aprobar(usuarios["cm"], "dba.demo", t)[0]     # otro equipo
    assert puede_aprobar(usuarios["lider"], "dba.demo", t)[0]


def test_token_de_aprobacion():
    h = aprobaciones.huella("bd.ses.terminar", "CORPRD", {"sid": 1}, "CHG123")
    tok = aprobaciones.emitir(K, ejecucion_id="e1", huella_hex=h, aprobador="b", solicitante="a", vigencia_min=5)
    assert aprobaciones.verificar(K, tok, ejecucion_id="e1", huella_hex=h)["aprobador"] == "b"
    otra = aprobaciones.huella("bd.ses.terminar", "CORPRD", {"sid": 2}, "CHG123")
    with pytest.raises(aprobaciones.TokenInvalido):
        aprobaciones.verificar(K, tok, ejecucion_id="e1", huella_hex=otra)
    with pytest.raises(aprobaciones.TokenInvalido):
        aprobaciones.verificar("otra-clave-de-32-caracteres-xxxxxxxx", tok, ejecucion_id="e1", huella_hex=h)
    with pytest.raises(aprobaciones.TokenInvalido):
        aprobaciones.verificar(K, tok, ejecucion_id="e2", huella_hex=h)


def test_la_api_no_puede_leer_ops_dba():
    prov = SecretosArchivo(RAIZ / "config" / "secretos.dev.json")
    with pytest.raises(AccesoSecretoDenegado):
        Secretos(prov, "api").leer("oracle/PROD/OPS_DBA")
    with pytest.raises(AccesoSecretoDenegado):
        Secretos(prov, "worker-cm").leer("oracle/PROD/OPS_CONSULTA")
    assert Secretos(prov, "worker-bd").leer("oracle/PROD/OPS_DBA")["usuario"] == "OPS_DBA"


def test_automatizador_tiene_todos_los_permisos():
    from opscentral.identidad import ingreso_demo
    aut = ingreso_demo("Bot Automatizador", "aut")
    assert all(permiso_de(aut, a) == "ejecuta" for a in CAT)
    assert decidir(aut, CAT["orq.stop"], "DESA").permitido
    assert decidir(aut, CAT["bd.ses.terminar"], "PROD").directa
    assert not decidir(aut, CAT["bd.grp.crear"], "TEST").permitido      # lo no implementado sigue bloqueado


def test_referencia_libre():
    a = CAT["bd.snap.tomar"]
    for ref in ("OPS-123", "DESPLIEGUE-NOMINA-1110", "CHG0012345", "INC40001", "tarea/proactiva.1"):
        assert a.validar_parametros({"base": "CORTST", "cambio": ref, "momento": "ANTES"})["cambio"] == ref.upper()
    for mala in ("ab", "con espacio", "x" * 41, "-empieza"):
        with pytest.raises(ErrorValidacion):
            a.validar_parametros({"base": "CORTST", "cambio": mala, "momento": "ANTES"})
