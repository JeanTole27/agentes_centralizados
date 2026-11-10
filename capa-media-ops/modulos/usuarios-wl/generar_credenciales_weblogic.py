# ============================================================
# WLST: genera credenciales encriptadas (storeUserConfig)
# Parametros por variables de entorno (dominio.env + preparar_dominio.sh):
#   ADMIN_URL, CMOPS_BASE_DIR, WL_ADMIN_USER, WL_ADMIN_PASS
# ============================================================
import sys
import os

ADMIN_URL = os.environ.get('ADMIN_URL', '')
ADMIN_USER = os.environ.get('WL_ADMIN_USER', 'weblogic')
ADMIN_PASS = os.environ.get('WL_ADMIN_PASS', '')
BASE = os.environ.get('CMOPS_BASE_DIR', '.')
USER_CONFIG_FILE = BASE + '/.wl_userconfig'
USER_KEY_FILE = BASE + '/.wl_keyfile'

print('[INFO]  URL AdminServer: ' + ADMIN_URL)
print('[INFO]  Usuario: ' + ADMIN_USER)

if not ADMIN_PASS or not ADMIN_URL:
    print('[ERROR] Falta ADMIN_URL o la clave del administrador')
    sys.exit(1)

try:
    connect(ADMIN_USER, ADMIN_PASS, ADMIN_URL)
    print('[OK]    Conexion exitosa')
except Exception, e:
    print('[ERROR] No se pudo conectar: ' + str(e))
    sys.exit(1)

rc = 0
try:
    storeUserConfig(USER_CONFIG_FILE, USER_KEY_FILE)
    print('[OK]    Credenciales cifradas generadas: .wl_userconfig / .wl_keyfile')
except Exception, e:
    print('[ERROR] No se pudieron generar las credenciales: ' + str(e))
    rc = 1

try:
    disconnect()
except:
    pass
sys.exit(rc)
