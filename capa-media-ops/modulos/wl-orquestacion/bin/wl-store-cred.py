# -*- coding: utf-8 -*-
# =============================================================================
# wl-store-cred.py - Genera credenciales cifradas (storeUserConfig) sin usuarios fijos
#   wlst.sh wl-store-cred.py nm    <domainName> <domainHome> <nmHost> <nmPort> <nmType>
#   wlst.sh wl-store-cred.py admin <domainHome> <adminUrl>
# Usuario y clave llegan por variables de entorno CMOPS_U / CMOPS_P
# (las fija wl-credenciales.sh a partir de STDIN). Imprime CRED_OK / CRED_ERROR.
# =============================================================================
import os
import sys

modo = sys.argv[1]
u = os.environ.get('CMOPS_U', '')
p = os.environ.get('CMOPS_P', '')

try:
    if modo == 'nm':
        domainName, domainHome, nmHost, nmPort, nmType = sys.argv[2:7]
        nmConnect(username=u, password=p, host=nmHost, port=nmPort,
                  domainName=domainName, domainDir=domainHome, nmType=nmType)
        storeUserConfig(userConfigFile=domainHome + '/nmsecure/nmUserConfig.secure',
                        userKeyFile=domainHome + '/nmsecure/nmUserKey.secure', nm='true')
        nmDisconnect()
    else:
        domainHome, adminUrl = sys.argv[2:4]
        connect(username=u, password=p, url=adminUrl)
        storeUserConfig(userConfigFile=domainHome + '/nmsecure/adminUserConfig.secure',
                        userKeyFile=domainHome + '/nmsecure/adminUserKey.secure')
        disconnect()
    print('CRED_OK ' + modo)
except Exception, e:
    print('CRED_ERROR ' + modo + ' ' + str(e))
    exit('5')
