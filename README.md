# Ops Central

Backend e interfaz web únicos para Capa Media y Base de Datos. El diseño está en `../propuesta-ops-central/` (documentos 01 a 05).

**Estado:** la lógica de procesos de las etapas E0 a E4 está implementada y probada en **modo simulación**. Todavía no se ha validado contra servidores, bases, SSO ni Zabbix reales (ver "Pendiente").

## Cómo correrlo (desarrollo)

```bash
pip install -e ".[dev]"            # o: set PYTHONPATH=src
python -m opscentral sembrar-demo   # series sintéticas para ver predicciones
python -m opscentral dev            # API + workers en http://127.0.0.1:8000 (docs en /api/docs)
python -m pytest -q                 # 32 pruebas
```

Para entrar en desarrollo: `POST /api/auth/dev/ingresar {"login": "dba.demo"}`. Los usuarios de prueba son `dba.demo`, `dba2.demo`, `cm.demo`, `cm2.demo`, `lider.demo`, `consulta.demo` y `admin.demo`. Todas las peticiones que modifican algo llevan la cabecera `X-Ops-Central: 1`.

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
- Si la API responde 428, se pide el segundo factor. En OIDC se redirige a `/api/auth/oidc/ingresar?stepup=1`; en desarrollo el segundo factor se simula y se reintenta la acción.
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
| `bd/` | Plantillas de solo lectura, conexión Oracle protegida, snapshot y línea base, recompilación, bloqueos, salud, recolección |
| `cm/` | Puente hacia `capa-media-ops/cmops` sin modificarlo (Python 3.6+, proceso aparte) |
| `zabbix/` | Webhook firmado con HMAC, episodios por grafo, refresco dirigido, cliente de la API y trapper |
| `ml/` | M1 (tiempo hasta llenarse), M2 (heap JVM) y M3 (anomalías). Corren en sombra |
| `api.py` | API FastAPI: lecturas del almacén, acciones con respuesta 202 y salida en vivo por SSE |
| `db/oracle/` | `01_pkg_ops_central.sql` (paquete acotado y línea base) y `02_cuentas_ops_central.sql` (cuentas, perfiles, trigger de origen, auditoría) |
| `db/postgres/001_esquema.sql` | DDL generado desde el código (`python -m opscentral ddl-postgres`) |
| `deploy/zabbix/`, `deploy/capa-media/` | Tipo de medio de Zabbix; sshd, CA, sudoers acotado y comando forzado en los servidores |

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
7. Construir la interfaz en React sobre esta API, siguiendo `demo-ops-central.html`.
8. Integrar el kit Oracle: hoy se usa la política SQL mínima interna, porque `SqlPolicyEngine` del kit necesita sus dependencias (`fastmcp`).
