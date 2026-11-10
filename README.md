# Ops Central

Backend e interfaz web únicos para Capa Media y Base de Datos. El diseño está en `../propuesta-ops-central/` (documentos 01 a 05).

**Estado:** la lógica de procesos de las etapas E0 a E4 está implementada. **No hay modo simulación**: el producto se conecta siempre a Oracle, a `cmops` y a la API de Zabbix reales. Los dobles de prueba viven solo en `tests/dobles/`. Todavía no se ha validado contra servidores, bases, SSO ni Zabbix reales (ver "Pendiente").

## Cómo correrlo (desarrollo)

```bash
pip install -e ".[dev]"            # o: set PYTHONPATH=src
python -m opscentral generar-inventario   # config/inventario.json desde los insumos reales
python -m opscentral dev                  # API + workers en http://127.0.0.1:8000 (docs en /api/docs)
python -m pytest -q                       # 82 pruebas, con los dobles de tests/dobles
```

## Modo demo (sin autenticación construida)

Con `[auth] proveedor = "demo"` (el valor del archivo de ejemplo) el ingreso es solo un **registro**: nombre, usuario opcional y un selector de perfil. No hay contraseña ni SSO todavía.

| Perfil | Id | Qué puede hacer |
|---|---|---|
| Especialista Capa Media | `cm` | Orquestación WebLogic, usuarios WL y frmweb |
| Administrador de bases de datos (DBA) | `dba` | Snapshot e inválidos, bloqueos, salud y tareas DBA |
| Administrador | `adm` | Administración, inventario y log de ejecuciones |
| Automatizador | `aut` | **Todos los permisos de ejecución y edición** sobre el catálogo completo |

- API: `GET /api/auth/demo/perfiles`, `GET /api/auth/demo/usuarios`, `POST /api/auth/demo/registrar {"nombre", "perfil", "login"?}`. Si el usuario ya existe, se actualiza su perfil.
- **Sin cola de aprobación** (`[aprobaciones] ejecucion_directa = true`), en Base de Datos y en Capa Media: quien tiene permiso de ejecución ejecuta. Cada ejecución queda en el **log de ejecuciones** con quién, destino, caso, motivo, salida, auditoría encadenada y evidencia con SHA-256. Con `ejecucion_directa = false` vuelve el esquema de cuatro ojos y aparece la pantalla Aprobaciones.
- Se mantienen: motivo obligatorio en clase C, MFA en PROD/DRP (en la demo se simula) y un cambio a la vez por destino.
- El login de desarrollo anterior (`POST /api/auth/dev/ingresar {"login": "dba.demo"}`) sigue disponible para las pruebas automáticas.

Todas las peticiones que modifican algo llevan la cabecera `X-Ops-Central: 1`.

## Caso asociado (opcional) y Jira

Una ejecución **no depende de un caso**: puede llevar una clave de Jira, cualquier otra referencia (3 a 40 caracteres: letras, números, `- _ . /`) o ninguna. Si se quiere exigir en PROD/DRP: `[aprobaciones] exigir_referencia_prod = true`.

Si `[jira] url` está configurado y existe el secreto `jira/api` (`{"usuario": "<correo>", "token": "<api token>"}`):

- `GET /api/casos/{clave}?accion=<id>` trae el caso (resumen, estado, tipo, prioridad, asignado, descripción) y propone valores para la acción: los campos de `[jira.campos]` (parámetro → campo de Jira), las bases/servidores/dominios del inventario que aparecen en el texto del caso y el motivo `"<CLAVE>: <resumen>"`.
- `GET /api/casos?q=<texto o clave>` busca casos (sin texto: `jql_abiertos`).
- En el formulario de cada acción, el botón **Traer de Jira** completa solo los campos vacíos; la persona revisa y confirma.
- Sin Jira configurado, la API responde 503 y la referencia queda como texto libre.
- Capa Media: `cmops` exige una referencia en PROD/DRP; si la ejecución no tiene caso se envía `OPS-<id de ejecución>`.

### Interfaz web (`apps/web`)

React 18 + TypeScript + Vite + TanStack Query + ECharts, con los estilos de `demo-ops-central.html` v0.2. Requiere Node 20 o superior.

```bash
cd apps/web
npm install
npm run build        # genera apps/web/dist; la API lo sirve en http://127.0.0.1:8000
npm run dev          # desarrollo con recarga en http://127.0.0.1:5173 (proxy de /api al puerto 8000)
```

- El menú se arma con `GET /api/auth/yo`, según el perfil. Las pantallas de "solo ver" no muestran botones de acción, y la API rechaza igual cualquier acción no permitida.
- Todas las acciones usan el mismo formulario (`src/Accion.tsx`), generado desde los parámetros del catálogo. El flujo es: evaluar → confirmar viendo los controles exigidos (cambio, aprobación, MFA) → ejecutar (202) → salida en vivo por SSE.
- Si la API responde 428, se pide el segundo factor. En OIDC se redirige a `/api/auth/oidc/ingresar?stepup=1`; en desarrollo (login de desarrollo) el segundo factor se confirma sin proveedor y se reintenta la acción.
- Archivos: `src/pantallas.tsx` (una función por pantalla), `src/Shell.tsx` (menú y rutas), `src/comun.tsx` (consola SSE, tablas, modal, segundo factor).

## Estructura

| Ruta | Qué hace |
|---|---|
| `catalogo/acciones.yaml` | **Fuente única** de las 101 acciones: clase L/O/C/X, perfiles, motor, parámetros y cuenta de BD |
| `src/opscentral/identidad.py`, `sesiones.py`, `oidc.py` | Ingreso: perfil por grupos `GG_OPS_*`, sesión en servidor, SSO OIDC con PKCE y step-up de MFA |
| `autorizacion.py`, `aprobaciones.py` | Quién puede qué; cuatro ojos; token firmado ligado a la huella de lo aprobado (H3) |
| `secretos.py`, `credenciales.py` | Gestor de secretos con acceso por componente; `OPS_CONSULTA` / `OPS_DBA`; certificados SSH efímeros |
| `ejecuciones.py`, `cola.py`, `workers.py`, `planificador.py` | Ciclo solicitar → aprobar → encolar → ejecutar → evidencia; cola en el almacén, sin Redis |
| `auditoria.py`, `evidencia.py` | Auditoría de solo agregar con cadena de hash; evidencia JSON con SHA-256 |
| `bd/` | 40 plantillas de solo lectura, conexión Oracle protegida, snapshot y línea base, recompilación, bloqueos, salud, recolección; `analisis.py` convierte cada lectura en hallazgos rojo/amarillo/info con umbrales configurables (`[bd.umbrales_analisis]`) |
| `inventario.py`, `inventario_insumos.py` | Grafo servidor ↔ dominio ↔ base, y su generador desde los insumos reales |
| `cm/` | Puente hacia `capa-media-ops/cmops` sin modificarlo (Python 3.6+, proceso aparte) |
| `zabbix/` | Webhook firmado con HMAC, episodios por grafo, refresco dirigido; cliente de la API (adaptado de zabbix-capacity-mcp), ingesta de problemas y trends de capacidad, trapper |
| `ml/` | M1 (tiempo hasta llenarse), M2 (heap JVM) y M3 (anomalías). Corren en sombra |
| `api.py` | API FastAPI: lecturas del almacén, acciones con respuesta 202 y salida en vivo por SSE |
| `db/oracle/` | `01_pkg_ops_central.sql` (paquete acotado y línea base) y `02_cuentas_ops_central.sql` (cuentas, perfiles, trigger de origen, auditoría) |
| `db/postgres/001_esquema.sql` | DDL generado desde el código (`python -m opscentral ddl-postgres`) |
| `deploy/zabbix/`, `deploy/capa-media/` | Tipo de medio de Zabbix; sshd, CA, sudoers acotado y comando forzado en los servidores |

## Inventario desde los insumos reales

`python -m opscentral generar-inventario` escribe `config/inventario.json` (rutas en `[inventario.insumos]`):

- **Servidores y dominios de Capa Media**: `capa-media-ops/inventario/servidores.json`. Los ambientes se traducen así: desarrollo→DESA, pruebas→TEST, stage→QA, prod→PROD, drp→DRP. Un servidor con varios ambientes queda con el más protegido. Un dominio con el mismo nombre y ambiente en varios nodos es uno solo, en clúster (`servidores`). Si el nombre se repite en otro ambiente, el id lleva el ambiente: `ATGCPROD (DRP)`.
- **Bases**: `oracle-agent-kiro-master/config/databases.json`, con la criticidad y el equipo dueño de `docs/databases.csv`. Cada alias del kit es una conexión. El id es el nombre del servicio cuando es único; si el servicio se repite (nodos RAC, o copias PROD/TEST como `TRON`), el id es el alias (`PROD_TRON1`).
- **Servidor de cada base**: el nombre del host en `oracle_map.yaml` de zabbix-capacity-mcp; si no está, el servidor de Capa Media con esa IP; si no, `BD-<ip>`. Los que quedan por IP no se cruzan con Zabbix hasta agregarlos al mapa.
- **Relaciones dominio → base**: los insumos no las traen. Van en `config/relaciones.json` (`[{"dominio", "base", "datasource"}]`). Mientras falten, `cm.bd_relacionada` responde sin bases.
- El archivo no lleva credenciales. Se regenera, no se edita a mano.

## Ejecución directa del DBA

Una acción clase C en PROD/DRP pasa por la cola de aprobación, salvo que quien la pide tenga el perfil `dba` y el catálogo le dé `ejecuta` sobre esa acción. En ese caso se ejecuta directo: se emite el token firmado ligado a la huella, con aprobador `politica:dba-directo`, y la auditoría lo registra como `ejecucion_directa`. El worker sigue verificando la firma, así que un token falsificado bloquea la ejecución. Se apaga con `[aprobaciones] dba_ejecuta_directo = false`. Un líder o un CM que pide la misma acción sigue pasando por la aprobación.

## Diagnóstico y reportes de BD

Las 24 acciones de diagnóstico devuelven `{veredicto, hallazgos, resumen, datos, errores}`. Si una plantilla falla (vista sin permiso, sin Data Guard), queda en `errores` y las demás siguen.

- **Salud**: FRA y archivelogs, Data Guard, RMAN, jobs, UNDO/TEMP, alert log (códigos ORA críticos), esperas, parámetros (con deriva contra otra base) y parches.
- **Sesiones y seguridad**: transacciones inactivas abiertas, privilegios y roles poderosos.
- **Espacio**: crecimiento con las series del almacén, candidatos a shrink y papelera.
- **Rendimiento**: top SQL por CPU, tiempo o gets, regresión de plan (usa AWR: requiere Diagnostics Pack), operaciones largas y estadísticas obsoletas.
- **Comparación y reportes**: diff de esquema entre bases, bases de un dominio, hoja de vida, análisis con plan de acción (cada hallazgo sugiere la acción del catálogo), diskgroups por servidor y SQL costosas.

## Integración con la API de Zabbix

Tomada de `zabbix-capacity-mcp-main` (cliente, catálogo de métricas y colector), sin LLM, en el worker de ingesta:

- **Problemas, cada 5 min** (`zbx.problemas`): `problem.get` + `trigger.get` sirven de respaldo del webhook. Si un problema no llegó por webhook, entra por el mismo camino (episodio, refresco dirigido y acción sugerida). Si un evento sigue abierto en Ops Central pero Zabbix ya no lo tiene, se cierra como recuperado.
- **Capacidad, cada hora** (`zbx.trends`): `host.get` → `item.get` → `trend.get` de los servidores del inventario, de forma incremental. Se guardan las series `fs:<host>:<punto>`, `mem:<host>` y `cpu:<host>`. Las `fs:` entran al modelo de llenado y al terminar se pide un scoring.
- **Cliente**: detecta la versión (6.0 LTS a 8.0) y envía el token en `Bearer` o en el campo `auth` según corresponda. Reintenta los errores 5xx. El token es de solo lectura y vive en el gestor de secretos (`zabbix/token_lectura`).
- **Prueba de conexión**: `python -m opscentral zabbix-probar` (muestra la versión, el modo de autenticación y los hosts del inventario que faltan en Zabbix). Estado en la API: `GET /api/zabbix/estado`.

## Reglas que el código hace cumplir

- La interfaz **solo lee del almacén**: nunca consulta en vivo las bases ni los servidores.
- Recolección con límites: 1 sesión por base, 10 bases en paralelo, `call_timeout` de 20 s, circuit breaker y refresco como máximo cada 2 min.
- La cuenta de BD la decide la **acción**, no la persona:
  - lectura con `OPS_CONSULTA`;
  - cambios con `OPS_DBA`, que solo puede llamar a `ADMINBD.PKG_OPS_CENTRAL`.
- La API no puede leer `OPS_DBA` ni las llaves de WebLogic: solo los workers tienen acceso.
- Los parámetros nunca traen claves; se rechazan.
- Un destino que no está en el inventario se trata como PROD.
- Se ejecuta un solo cambio a la vez por destino.

## Pendiente (validación, al final, según lo acordado)

1. Probar `cmops` en WLPC02SBCC04 con certificado SSH y `ops-dispatch` (H5). El comando forzado está pendiente.
2. Instalar el paquete y las cuentas en una base de TEST. Revisar los privilegios de `ADMINBD` con el equipo BD.
3. Conectar el SSO real. Con Entra ID, el claim `groups` puede traer GUIDs en lugar de nombres: mapearlos en `auth.grupos`.
4. Conectar Vault (AppRole, KV v2 y motor SSH) y PostgreSQL 16.
5. Conectar el webhook y la API de Zabbix reales, y hacer el backfill de *trends* para el backtest de M1.
6. Verificar el cifrado de red de Oracle: el modo thin no soporta Native Network Encryption. Usar TCPS o el modo thick.
7. Cargar `config/relaciones.json` (dominio → base) o leerlas de los datasources de WebLogic, y completar `oracle_map.yaml` con los servidores de BD que hoy quedan por IP.
8. Integrar el kit Oracle: hoy se usa la política SQL mínima interna, porque `SqlPolicyEngine` del kit necesita sus dependencias (`fastmcp`).
