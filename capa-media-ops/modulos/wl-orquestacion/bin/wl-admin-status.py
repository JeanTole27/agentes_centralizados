# -*- coding: utf-8 -*-
# =============================================================================
# wl-admin-status.py  -  Estado REAL de los Managed Servers via AdminServer (t3s)
# -----------------------------------------------------------------------------
# El AdminServer es la fuente autoritativa del estado del ciclo de vida de los
# Managed Servers (a diferencia del Node Manager, que puede reportar un estado
# obsoleto si el MS no fue arrancado por el).
#
# Uso (via wlst.sh):
#   wlst.sh wl-admin-status.py <domainHome> <adminUrl> <server1> [server2 ...]
#
#   adminUrl = t3s://host:puerto  (puerto seguro del AdminServer)
#
# Autentica con la credencial de consola cifrada:
#   <domainHome>/nmsecure/adminUserConfig.secure
#   <domainHome>/nmsecure/adminUserKey.secure
#
# Imprime una linea por servidor:  SERVER=<nombre> STATE=<estado>
# Si no puede conectar, sale con codigo != 0 (para que el shell haga fallback a NM).
# =============================================================================
import sys

def main():
    if len(sys.argv) < 4:
        print("ERROR uso: wl-admin-status.py <domainHome> <adminUrl> <server...>")
        exit("2")

    domainHome = sys.argv[1]
    adminUrl   = sys.argv[2]
    servers    = sys.argv[3:]

    userConfig = domainHome + "/nmsecure/adminUserConfig.secure"
    userKey    = domainHome + "/nmsecure/adminUserKey.secure"

    try:
        connect(userConfigFile=userConfig, userKeyFile=userKey, url=adminUrl)
    except Exception, e:
        print("ADMIN_CONNECT_ERROR url=%s msg=%s" % (adminUrl, str(e)))
        exit("3")

    # Estado autoritativo desde el dominio (ServerLifeCycleRuntime)
    try:
        domainRuntime()
    except:
        pass

    rc = 0
    for srv in servers:
        st = None
        try:
            cd("/ServerLifeCycleRuntimes/" + srv)
            st = cmo.getState()
        except Exception:
            st = None
        if st is None:
            # Segundo intento: ServerRuntimes (solo aparece si esta arriba)
            try:
                cd("/ServerRuntimes/" + srv)
                st = cmo.getState()
            except Exception:
                st = "UNKNOWN"
        print("SERVER=%s STATE=%s" % (srv, st))

    try:
        disconnect()
    except:
        pass
    exit(str(rc))

main()
