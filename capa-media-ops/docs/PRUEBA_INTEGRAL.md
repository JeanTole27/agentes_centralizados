# Prueba integral (modo real) — usuarios por servidor

La versión original de `wl-orquestacion` tenía usuarios fijos en tres lugares:

- `weblogic` como usuario por defecto del Node Manager y de la consola, en `wl-bootstrap*.sh`.
- `wlogic14c` en las rutas de `wl-config.sh`.
- La suposición de que un servidor tiene un único usuario de SO.

En la realidad no es así:

- `WLPC02SBCC04` tiene **wlogic14c** (8 dominios) y **wlogic12c** (`acci_desa`).
- `.env` de capa_media trae un **usuario SSH distinto por host**.
- Cada dominio puede tener **su propio usuario de Node Manager y de consola**.

Capa Media Ops resuelve esto con tres niveles. **Ninguno está fijo en el código:**

| Nivel | Qué es | Dónde se define | Cómo se valida |
|---|---|---|---|
| 1. Usuario SSH | Cuenta con la que el nodo central entra al servidor (con llave, nunca con clave) | `ssh_usuario` en `inventario/servidores.json` (por host) o en `orquestacion.hosts.<H>.ssh_usuario`. Si está vacío se usa la cuenta de servicio global de `config/app.json`. Se carga con `bin/cm importar-usuarios-ssh <.env>`, que **ignora las claves**. | **Probar conexión** muestra `usuario=$(id -un)` real |
| 2. Usuario de SO por instalación | Dueño de los dominios. Se ejecuta con `sudo -n -u <usuario_so>` | `orquestacion.hosts.<H>.instalaciones[].usuario_so`. Un host puede tener varias instalaciones. | **Verificar credenciales** compara `stat -c %U` de cada DOMAIN_HOME con `id -un`. Además, `wl-services.sh` (`check_owner`) y `wl-credenciales.sh` **se niegan a operar** si no coincide. |
| 3. Credenciales WebLogic por dominio | Usuario/clave del Node Manager y, opcionalmente, de la consola | Se digitan en la interfaz (**Credenciales** en cada dominio). Viajan por stdin de SSH y quedan **solo cifradas en el servidor** (`<DOMAIN_HOME>/nmsecure/*.secure`, chmod 600). `nm_usuario`/`admin_usuario` del inventario solo pre-llenan el formulario. | **Verificar credenciales** muestra NM_CRED / ADMIN_CRED = OK / FALTA por dominio |

## 0. Prerrequisitos (una vez por servidor)

1. Crear la llave del nodo central y copiar la pública en `~/.ssh/authorized_keys` del **usuario SSH** de ese servidor. La guía está en el README, sección "Paso a modo real".
2. En el servidor, sudoers sin clave **solo** hacia los usuarios de SO de sus instalaciones. Usar `docs/sudoers.ejemplo` y listar exactamente `wlogic12c`, `wlogic14c`, etc.:
   `svc_capamedia ALL=(wlogic12c,wlogic14c) NOPASSWD: /bin/bash`
3. Registrar la huella del servidor en `known_hosts` del nodo central. StrictHostKeyChecking está activo.
4. En `config/app.json`, poner `modo_simulacion: false`.

## 1. Tabla de usuarios a confirmar antes de la prueba

Completar con infraestructura. Lo marcado con "?" está **pendiente de confirmar**:

| Servidor | IP SSH | Usuario SSH | Instalación | Usuario SO | Dominios | Usuario NM / consola |
|---|---|---|---|---|---|---|
| WLPC02SBCC04 | 10.1.7.100 ó 10.7.8.19 ? | ? | wlogic14c | wlogic14c | externosdesa, transversalesdesa, ARL_Web, saluddesa, serviciosdesa, Externos14, Terceros_desa, simondesa | ? (por dominio) |
| WLPC02SBCC04 | (mismo) | (mismo) | wlogic12c | wlogic12c | acci_desa (descubrir) | ? |
| AP104SBCC04 | ? | ? | usuarios_wl ATGCPROD | wlogic14c ? | ATGCPROD | admin consola ? |
| AP17SBCC04 | ? | ? | frmweb | wlogic12c | WLS_FORMS1_1/2/3 | n/a |

## 2. Secuencia por servidor (empezar por DESA)

Cada paso queda con evidencia en `evidencias/trabajos/<id>.log` y en la auditoría.

| # | Dónde | Acción | Resultado esperado |
|---|---|---|---|
| 1 | Inventario → ficha del host | **Probar conexión** | `CMOPS_OK host=... usuario=<usuario SSH>`. Si falla: llave, known_hosts o IP. |
| 2 | Inventario → ficha | **Recolectar información** | La ficha muestra los dominios WebLogic con su **propietario** (p.ej. `acci_desa · wlogic12c`). |
| 3 | Terminal del nodo central | `bin/cm proponer-orquestacion <HOST>`, revisar y luego `--aplicar` | Agrega las instalaciones o dominios faltantes marcados `por_validar`. Completar `nm_host`, `admin_url`, `java_home` y `trust_keystore`. Después, **Recargar inventario** en Admin. |
| 4 | Orquestación → host → instalación | **Verificar credenciales** | PROPIETARIO debe coincidir con la instalación (badge verde). Si sale rojo, corregir `usuario_so` antes de seguir. |
| 5 | Orquestación → dominio → **Credenciales** | Digitar usuario/clave NM (y consola si se usa) | `CRED_OK nm` (y `CRED_OK admin`). Verificar de nuevo: NM_CRED=OK. |
| 6 | Orquestación | **Consultar estado** | AdminServer y Managed Servers con su estado real |
| 7 | Orquestación | **Tomar snapshot** | `state/<dom>.snapshot` con los MS en RUNNING |
| 8 | Orquestación (1 dominio DESA, en ventana acordada) | **Bajar** | Snapshot automático, luego nmKill de los MS y SIGTERM del AdminServer |
| 9 | Orquestación | **Subir** | Arriba el AdminServer y luego solo los MS del snapshot |
| 10 | Orquestación | **Consultar estado** y comparar con el paso 6 | Mismo estado que antes de bajar |

Repetir los pasos 4 a 10 para cada instalación del host (wlogic14c y wlogic12c).

## 3. Qué pasa si un usuario está mal

| Error | Qué lo detecta | Mensaje |
|---|---|---|
| Usuario SSH incorrecto o sin llave | Probar conexión | `Permission denied (publickey)` |
| Falta sudoers hacia el usuario SO | Cualquier acción | `sudo: a password is required` (por `sudo -n`, nunca queda esperando) |
| `usuario_so` no es el dueño del dominio | Verificar credenciales, `check_owner`, `wl-credenciales.sh` | `COINCIDE=NO`, o bien `ERROR: el dominio X pertenece a 'wlogic12c' y se esta ejecutando como 'wlogic14c'` |
| Usuario o clave NM incorrecto | Registrar credenciales | `CRED_ERROR nm ...` y el trabajo queda fallido. No se guardan archivos. |
| Dominio sin credencial registrada | Estado, Bajar, Subir | `NM_CONNECT_ERROR` / `FALTA`. Bajar y Subir se abortan antes de tocar algo. |
| Instalación sin dominios | Interfaz y API | Botones deshabilitados y un mensaje que remite al paso 3 |

## 4. Ensayo previo en simulación

Con `modo_simulacion: true` se ejercitan los mismos flujos. En el simulador, algunos dominios empiezan con FALTA a propósito para probar el registro de credenciales:

```bash
python3 bin/cm ejecutar orq.verificar host=WLPC02SBCC04 instalacion=wlogic14c
python3 bin/cm ejecutar rec.recolectar hosts=WLPC02SBCC04
python3 bin/cm proponer-orquestacion WLPC02SBCC04
```
