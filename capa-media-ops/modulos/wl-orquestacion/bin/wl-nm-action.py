# -*- coding: utf-8 -*-
# =============================================================================
# wl-nm-action.py  -  Arranca o detiene Managed Servers via Node Manager
# -----------------------------------------------------------------------------
# Uso (via wlst.sh):
#   wlst.sh wl-nm-action.py <action> <domainName> <domainHome> <nmHost> <nmPort> <nmType> <server1> [server2 ...]
#
#   action = start | kill
#   nmType = plain | ssl
#
# start -> nmStart  (arranca el MS por Node Manager)
# kill  -> nmKill   (detiene el MS por Node Manager)
#
# Autentica con:
#   <domainHome>/nmsecure/nmUserConfig.secure
#   <domainHome>/nmsecure/nmUserKey.secure
#
# Imprime por servidor:  SERVER=<nombre> ACTION=<action> RESULT=<OK|ERROR msg>
# =============================================================================
import sys

def main():
    if len(sys.argv) < 8:
        print("ERROR uso: wl-nm-action.py <start|kill> <domainName> <domainHome> <nmHost> <nmPort> <nmType> <server...>")
        exit("2")

    action     = sys.argv[1].lower()
    domainName = sys.argv[2]
    domainHome = sys.argv[3]
    nmHost     = sys.argv[4]
    nmPort     = sys.argv[5]
    nmType     = sys.argv[6].lower()
    servers    = sys.argv[7:]

    if action not in ("start", "kill"):
        print("ERROR action invalida: %s (usar start|kill)" % action)
        exit("2")

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

    rc = 0
    for srv in servers:
        try:
            if action == "start":
                nmStart(serverName=srv)
            else:
                nmKill(serverName=srv)
            print("SERVER=%s ACTION=%s RESULT=OK" % (srv, action))
        except Exception, e:
            msg = str(e)
            low = msg.lower()
            # Idempotencia: si el NM dice que el servidor YA esta corriendo (o en
            # proceso de arranque), para 'start' es exito, no error. Evita falsos
            # ERROR por condiciones de carrera (AdminServer recien arrancado, etc).
            if action == "start" and ("already running" in low or "process of starting" in low or "restarting" in low):
                print("SERVER=%s ACTION=%s RESULT=ALREADY_RUNNING" % (srv, action))
            # Para 'kill', si ya estaba detenido tambien es exito.
            elif action == "kill" and ("not running" in low or "no server" in low or "shutdown" in low):
                print("SERVER=%s ACTION=%s RESULT=ALREADY_DOWN" % (srv, action))
            else:
                rc = 4
                print("SERVER=%s ACTION=%s RESULT=ERROR(%s)" % (srv, action, msg))

    try:
        nmDisconnect()
    except:
        pass

    exit(str(rc))

main()
