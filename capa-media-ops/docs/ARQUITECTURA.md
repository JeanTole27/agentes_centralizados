# Arquitectura - Capa Media Ops

## Vista general

```
 Navegador (operadores)                    Nodo central (Linux, cuenta svc_capamedia)
 ┌──────────────────────┐  HTTPS   ┌───────────────────────────────────────────────┐
 │ Interfaz web (web/)  │ ───────▶ │ cmops.web      API JSON + sesiones + roles     │
 └──────────────────────┘          │ cmops.trabajos cola, log, evidencia, bloqueo   │
 cron / CLI (bin/cm) ────────────▶ │ cmops.modulos  validación → plan de pasos      │
                                   │ cmops.ejecutor ssh/sudo, despliegue versionado │
                                   └──────────────┬────────────────────────────────┘
                                                  │ SSH (llave, puerto 22)
                     ┌────────────────────────────┼─────────────────────────────┐
                     ▼                            ▼                             ▼
            WLPC02SBCC04 (DESA)          AP104SBCC04 (ATGCPROD)        AP17SBCC04 (TRON Forms)
            sudo -u wlogic14c            sudo -u wlogic14c             sudo -u wlogic12c
            wl-services.sh …             WLST gestionar_usuarios …     mitigar_frmweb_huerfanos.sh
```

## Flujo de una acción

1. **Validación** (`cmops/modulos.py`): la acción y sus parámetros se validan contra el inventario (hosts y dominios en lista blanca, caracteres permitidos, contraseñas mínimas, prohibido eliminar `weblogic`).
2. **Permiso** (`cmops/seguridad.py`): se decide según el rol y si el host es PROD/DRP. En PROD/DRP las acciones con cambios exigen número de cambio.
3. **Bloqueo:** solo un trabajo con cambios por host a la vez.
4. **Ejecución** (`cmops/ejecutor.py`), en cada paso:
   - *Despliegue:* el módulo se empaqueta (tar determinístico) y se envía por stdin. El servidor solo lo extrae si su `.cmops_version` es distinta, así que el código remoto siempre es el del repositorio central.
   - *Comando:* `ssh … "sudo -n -u <usuario_so> -H bash -c '<script>'"`. Los secretos van por stdin.
5. **Evidencia:** la salida se guarda línea a línea en `evidencias/trabajos/<id>.log`, los metadatos en `<id>.json` y los eventos en `evidencias/auditoria.jsonl`.

## Decisiones

| Decisión | Motivo |
|---|---|
| Solo librería estándar de Python | No requiere autorizar software nuevo (Ansible está en espera) |
| Cliente `ssh` del SO en vez de paramiko | Usa llaves, `known_hosts`, `ControlMaster` y la política SSH ya existente |
| Scripts remotos en bash/WLST (los existentes) | Reutiliza lo que ya funciona y es conocido por el equipo |
| Configuración generada (`wl-config.sh`, `dominio.env`) | Un único origen de verdad; evita divergencias por edición manual |
| Inventario y evidencia en archivos JSON | Sin base de datos que administrar; fácil de respaldar y versionar |
| Modo simulación | Capacitación, demostraciones y pruebas de la interfaz sin riesgo |

## Migración futura a Ansible (si se autoriza)

- `inventario/servidores.json` + `configuracion_modulos.json` se convierten en el inventario de Ansible (grupos por ambiente o módulo y `host_vars`).
- Cada acción del catálogo pasa a ser un playbook que ejecuta el mismo script del módulo.
- La interfaz puede seguir igual llamando a `ansible-playbook`, o reemplazarse por AWX.
