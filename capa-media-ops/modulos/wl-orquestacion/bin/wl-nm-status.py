# -*- coding: utf-8 -*-
# =============================================================================
# wl-nm-status.py  -  Consulta el estado de los Managed Servers via Node Manager
# -----------------------------------------------------------------------------
# Uso (via wlst.sh):
#   wlst.sh wl-nm-status.py <domainName> <domainHome> <nmHost> <nmPort> <nmType> <server1> [server2 ...]
#
#   nmType = plain | ssl
#
# Autentica con los archivos cifrados generados por nmConnect / bootstrap:
#   <domainHome>/nmsecure/nmUserConfig.secure
#   <domainHome>/nmsecure/nmUserKey.secure
#
# Imprime una linea por servidor:  SERVER=<nombre> STATE=<estado>
# =============================================================================
import sys

def main():
    if len(sys.argv) < 7:
        print("ERROR uso: wl-nm-status.py <domainName> <domainHome> <nmHost> <nmPort> <nmType> <server...>")
        exit("2")

    domainName = sys.argv[1]
    domainHome = sys.argv[2]
    nmHost     = sys.argv[3]
    nmPort     = sys.argv[4]
    nmType     = sys.argv[5].lower()
    servers    = sys.argv[6:]

    userConfig = domainHome + "/nmsecure/nmUserConfig.secure"
    userKey    = domainHome + "/nmsecure/nmUserKey.secure"

    try:
        nmConnect(userConfigFile=userConfig, userKeyFile=userKey,
                  host=nmHost, port=nmPort,
                  domainName=domainName, domainDir=domainHome,
                  nmType=nmType)
    except Exception, e:
        print("NM_CONNECT_ERROR domain=%s host=%s port=%s msg=%s" % (domainName, nmHost, nmPort, str(e)))
        exit("3")

    for srv in servers:
        try:
            st = nmServerStatus(serverName=srv)
            print("SERVER=%s STATE=%s" % (srv, st))
        except Exception, e:
            print("SERVER=%s STATE=UNKNOWN_ERROR(%s)" % (srv, str(e)))

    try:
        nmDisconnect()
    except:
        pass

main()
