# Capa Media Ops

Plataforma central con interfaz web para operar las iniciativas de Capa Media desde **un solo punto**, en lugar de copiar y ejecutar scripts en cada servidor.

| Módulo | Origen | Qué hace |
|---|---|---|
| **Orquestación WebLogic** | `wl-orquestacion` | Estado, snapshot, bajar y subir dominios (Node Manager + AdminServer) |
| **Usuarios WebLogic** | `atgc_deploy` | Preparar dominio (truststore + credenciales), listar, crear, eliminar, dar admin y agregar a grupo |
| **Forms: frmweb huérfanos** | `MitigarHuerfanos` | Validación (dry-run) y mitigación por lotes |
| **Inventario y recolección** | `capa_media` | Línea base de servidores y recolección de información por SSH (solo lectura) |

**Sin software nuevo:** usa solo Python 3.6+ (librería estándar), bash y el cliente OpenSSH del sistema operativo. No requiere Ansible, pip, Node ni bases de datos.

---

## Arranque rápido (modo simulación)

El modo simulación permite probar la interfaz sin conectarse a ningún servidor.

```bash
python3 bin/cm usuario-app demo     # crea admin / operador / consulta (claves en config/CREDENCIALES_DEMO.txt)
python3 bin/cm web                  # http://127.0.0.1:8080
```

## Paso a modo real

1. **Nodo central:** un servidor Linux con Python 3.6+ y OpenSSH. Copiar esta carpeta a `/opt/capa-media-ops`.
2. **Cuenta de servicio** (p.ej. `svc_capamedia`):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/cmops_ed25519 -C capa-media-ops
   ```
   Instalar la llave pública en cada servidor destino (`~svc_capamedia/.ssh/authorized_keys`).
3. **sudoers acotado** en cada servidor destino (ejemplo en `docs/sudoers.ejemplo`).
4. **known_hosts:** cargar las llaves de host de los servidores. Se usa `StrictHostKeyChecking=yes`.
   ```bash
   ssh-keyscan -f lista_ips.txt >> ~/.ssh/known_hosts
   ```
   Validar las huellas con infraestructura antes de confiar en ellas.
5. Editar `config/app.json`:
   ```json
   { "modo_simulacion": false, "escuchar": "0.0.0.0", "puerto": 8443,
     "tls": { "certificado": "/opt/capa-media-ops/config/cert.pem", "llave": "/opt/capa-media-ops/config/key.pem" },
     "ssh": { "usuario": "svc_capamedia", "llave": "/home/svc_capamedia/.ssh/cmops_ed25519" } }
   ```
6. **Usuarios de la interfaz:** `bin/cm usuario-app crear <usuario> --rol admin`. Borrar `config/CREDENCIALES_DEMO.txt` y los usuarios demo (editar `config/usuarios_app.json`).
7. **Servicio:** usar `docs/capa-media-ops.service` (systemd) o dejarlo en `cron @reboot`.
8. **Piloto:** WLPC02SBCC04 (DESA). Ejecutar primero *Probar conexión* y *Consultar estado* antes de cualquier acción con cambios.

## Configuración del inventario

| Archivo | Contenido | Cómo se actualiza |
|---|---|---|
| `inventario/servidores.json` | Línea base: 182 hosts, IP, ambientes y dominios | `bin/cm importar-inventario ../capa_media/config/servers_drp.json` |
| `inventario/configuracion_modulos.json` | Hosts y dominios de cada módulo (orquestación, usuarios WL, frmweb) | Edición manual o `bin/cm importar-wlconfig <wl-config.sh> --host H --usuario-so U` |
| `inventario/hechos/<HOST>.json` | Información recolectada por SSH | Acción *Recolectar información* |

El `wl-config.sh` de cada servidor y el `dominio.env` de usuarios WL **se generan** desde `configuracion_modulos.json`. No se editan a mano en el servidor.

### Usuarios: nada fijo en el código

- **Usuario SSH, por servidor.** Es el campo `ssh_usuario` del host. Si está vacío, se usa la cuenta de servicio global. Para cargarlos: `bin/cm importar-usuarios-ssh ../capa_media/config/.env`. Solo lee `SSH_USER_<HOST>` y **descarta las claves**. Al re-importar el inventario, los usuarios SSH se conservan.
- **Usuario de SO, por instalación.** Un host puede tener varias instalaciones, cada una con su `usuario_so`, `ruta` y dominios. Por ejemplo, WLPC02SBCC04 tiene wlogic14c y wlogic12c. Para descubrirlas desde la recolección: `bin/cm proponer-orquestacion <HOST> [--aplicar]`. Para importarlas desde un wl-config.sh: `bin/cm importar-wlconfig <archivo> --host H --usuario-so U [--instalacion ID]`.
- **Credenciales Node Manager y consola, por dominio.** Se registran desde la interfaz con el botón **Credenciales** del dominio. Quedan solo cifradas en el servidor (`nmsecure/*.secure`). **Verificar credenciales** muestra el propietario real de cada dominio y si falta alguna credencial.

El checklist para la prueba en servidores reales está en [docs/PRUEBA_INTEGRAL.md](docs/PRUEBA_INTEGRAL.md).
Para ejecutarlo desde Windows: [docs/GUIA_WINDOWS.md](docs/GUIA_WINDOWS.md).
Guía de usuario (paso a paso, con pantallas): [docs/GUIA_USUARIO.md](docs/GUIA_USUARIO.md).

## CLI (desatendido / cron)

```bash
bin/cm acciones
bin/cm ejecutar orq.verificar host=WLPC02SBCC04 instalacion=wlogic14c
bin/cm ejecutar orq.status host=WLPC02SBCC04 instalacion=wlogic14c
bin/cm ejecutar orq.stop host=WLPC02SBCC04 dominios=simondesa,saluddesa paralelo=true
bin/cm ejecutar frm.validar host=AP17SBCC04
bin/cm ejecutar frm.mitigar host=AP17SBCC04 lote=10 --cambio CHG0012345
bin/cm ejecutar rec.recolectar hosts=AP23SBCC04,AP23SBCC09
bin/cm ejecutar usr.aplicar dominio=ATGCPROD --acciones acciones.txt --cambio CHG0012345
```

## Roles

| Rol | Permisos |
|---|---|
| `consulta` | Solo acciones de lectura: estado, listados, dry-run, recolección y conexión |
| `operador` | Lo anterior + acciones con cambios en ambientes **no productivos** |
| `admin` | Todo. En **PROD/DRP**, cualquier acción con cambios exige número de cambio |

## Seguridad

- Las claves (admin WebLogic, usuarios nuevos) viajan **solo por stdin de SSH**. No se escriben en disco del nodo central, en logs ni en auditoría; los parámetros se registran enmascarados.
- Sesión con cookie HttpOnly + SameSite=Strict, token CSRF, bloqueo tras 5 intentos fallidos, expiración por inactividad.
- Validación estricta de entradas (lista blanca de dominios, hosts y caracteres) antes de construir cualquier comando remoto.
- Un solo trabajo con cambios a la vez por host.
- Evidencia completa por ejecución en `evidencias/trabajos/` y auditoría en `evidencias/auditoria.jsonl`. Respaldar ambas carpetas.

## Estructura

```
bin/cm                      CLI (web, ejecutar, usuarios, importadores)
cmops/                      núcleo: config, inventario, ejecutor SSH, simulación, trabajos, seguridad, web
modulos/                    lo que se despliega en los servidores (por versión, solo si cambia)
  wl-orquestacion/bin/      scripts originales (wl-config.sh se genera)
  usuarios-wl/              WLST parametrizado (dominio.env se genera)
  frmweb-huerfanos/         script con --yes y PPID dinámico
  recoleccion/              recolector de solo lectura (JSON)
web/                        interfaz (HTML/CSS/JS sin dependencias externas)
inventario/                 inventario central
herramientas/               importadores
docs/                       arquitectura, sudoers, servicio systemd
```
