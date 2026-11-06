# Capa Media · Configuración de los servidores para Ops Central

Esto se aplica en cada servidor WebLogic o Forms. Revisarlo con el equipo de Capa Media y con Seguridad antes de pasar a PROD.

## 1. Cuenta de servicio

```bash
useradd -m -s /bin/bash svc_opscentral   # sin contraseña: solo entra con certificado
passwd -l svc_opscentral
```

## 2. Confiar en la CA de Ops Central (certificados de 5 a 10 min)

En `/etc/ssh/sshd_config`:

```
TrustedUserCAKeys /etc/ssh/ops_central_ca.pub
Match User svc_opscentral
    AuthorizedPrincipalsFile /etc/ssh/principals/%u
    AllowTcpForwarding no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ForceCommand /opt/opscentral/bin/ops-dispatch
```

- `/etc/ssh/principals/svc_opscentral` contiene una línea: `svc_opscentral`.
- `/etc/ssh/ops_central_ca.pub` es la llave pública de la CA del gestor de secretos (`vault read -field=public_key ssh-client-signer/config/ca`).
- El `key_id` de cada certificado lleva `persona#ejecución` y aparece en `/var/log/secure`.

**Alternativa si no hay CA:** una llave ed25519 guardada en el gestor de secretos y rotada cada 90 días. En `~svc_opscentral/.ssh/authorized_keys`:

```
from="10.9.0.31",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,command="/opt/opscentral/bin/ops-dispatch" ssh-ed25519 AAAA... ops-central
```

## 3. Firewall

Solo la IP del worker-cm (por ejemplo 10.9.0.31) puede llegar al puerto 22 con la cuenta `svc_opscentral`.

## 4. sudoers acotado (cierra el riesgo "sudoers amplio" de H5)

`/etc/sudoers.d/ops-central` (editar con `visudo -f`):

```
Cmnd_Alias OPSC_WL  = /oracle/app/wlogic14c/wl-orquestacion/bin/wl-services.sh, \
                      /oracle/app/wlogic14c/wl-orquestacion/bin/wl-credenciales.sh
Cmnd_Alias OPSC_USR = /opt/cmops/usuarios-wl/aplicar_acciones.sh, /opt/cmops/usuarios-wl/preparar_dominio.sh
Cmnd_Alias OPSC_FRM = /opt/cmops/frmweb-huerfanos/mitigar_frmweb_huerfanos.sh
svc_opscentral ALL=(weblogic) NOPASSWD: OPSC_WL, OPSC_USR, OPSC_FRM
Defaults:svc_opscentral !requiretty, log_output, logfile=/var/log/sudo-opscentral.log
```

- Los scripts pertenecen a `root:root`, con permisos `0755`. Nadie más puede modificarlos.
- No se usa `ALL` ni comodines en los argumentos.
- Ajustar las rutas a las de `inventario/configuracion_modulos.json` de cada servidor.

## 5. ops-dispatch

`ops-dispatch` es el comando forzado. Lee `SSH_ORIGINAL_COMMAND`, acepta solo los comandos que genera `cmops` para las 13 acciones del catálogo y rechaza todo lo demás. Se construye en la etapa E1, con las rutas reales validadas en WLPC02SBCC04.
