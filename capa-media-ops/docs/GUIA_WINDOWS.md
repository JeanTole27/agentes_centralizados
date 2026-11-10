# Guía: ejecutar Capa Media Ops desde Windows

La plataforma corre en Windows 10/11 sin instalar nada adicional. Usa:

- **Python** 3.6 o superior.
- **OpenSSH Client**, que ya viene en Windows: `C:\Windows\System32\OpenSSH\ssh.exe`.

En los servidores Linux no cambia nada: siguen recibiendo `ssh` + `sudo -n -u <usuario_so>` + bash.

Todos los comandos son para **PowerShell** y se ejecutan desde la carpeta del proyecto:

```powershell
cd "C:\Users\<usuario>\Downloads\Capa Media Desarrollo\capa-media-ops"
```

> Diferencia con Linux: en Windows no hay ControlMaster (reutilizar la conexión SSH). Cada paso abre su propia conexión. Funciona igual, solo un poco más lento. No hay que configurar nada.

---

## Paso 1. Verificar requisitos

```powershell
python --version      # 3.6 o superior. Si abre la Microsoft Store, use "py" en lugar de "python"
ssh -V                # OpenSSH_for_Windows_...
Test-NetConnection 10.1.7.100 -Port 22   # TcpTestSucceeded : True  (alcance de red al servidor)
```

Si `ssh` no existe: **Configuración → Sistema → Características opcionales → Agregar → "Cliente OpenSSH"**. Esto requiere permisos de administrador; pídalo a soporte si no los tiene.

## Paso 2. Ensayo en modo simulación (sin tocar servidores)

```powershell
python bin\cm usuario-app demo     # crea admin/operador/consulta; claves en config\CREDENCIALES_DEMO.txt
python bin\cm web                  # deja la consola abierta; Ctrl+C para detener
```

Abrir **http://127.0.0.1:8080** e iniciar sesión con un usuario demo. Recorrer Orquestación → Verificar credenciales → Credenciales → Consultar estado.

## Paso 3. Crear la llave SSH del nodo central

```powershell
mkdir $env:USERPROFILE\.ssh -Force
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\cmops_ed25519 -C "capa-media-ops"
```

La plataforma usa `BatchMode=yes`, que nunca pregunta nada. Hay dos opciones:

- **A (piloto):** llave **sin frase** (Enter dos veces), protegida por permisos de Windows (ver abajo).
- **B (recomendada):** llave **con frase**, cargada en el agente de Windows. Necesita un administrador una sola vez:
  ```powershell
  # como administrador:
  Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent
  # como su usuario:
  ssh-add $env:USERPROFILE\.ssh\cmops_ed25519
  ```

En ambos casos, deje la llave privada legible **solo** por su usuario. Si no, OpenSSH la rechaza con "UNPROTECTED PRIVATE KEY FILE":

```powershell
icacls "$env:USERPROFILE\.ssh\cmops_ed25519" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

## Paso 4. Instalar la llave pública en cada servidor

La llave pública es `%USERPROFILE%\.ssh\cmops_ed25519.pub`. Debe agregarse al `~/.ssh/authorized_keys` del **usuario SSH de ese servidor**, que es el `SSH_USER_<HOST>` del `.env`.

Hay dos formas:

- **Por infraestructura (recomendado en PROD):** entregarles el archivo `.pub`.
- **Usted mismo, si conoce la clave del usuario SSH.** La pide **una sola vez**; después todo funciona con la llave. Windows no trae `ssh-copy-id`, así que se usa:
  ```powershell
  type $env:USERPROFILE\.ssh\cmops_ed25519.pub | ssh <usuario_ssh>@<IP> "umask 077; mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
  ```

## Paso 5. Registrar la huella de cada servidor (known_hosts)

La plataforma verifica la identidad del servidor (StrictHostKeyChecking=yes). La primera conexión manual registra la huella; antes de aceptarla, confírmela con infraestructura:

```powershell
ssh -i $env:USERPROFILE\.ssh\cmops_ed25519 <usuario_ssh>@<IP> "hostname; id -un"
# responder "yes" a la huella; debe mostrar el hostname y el usuario SSH
```

## Paso 6. Validar sudo hacia el usuario de SO de cada instalación (lo configura infraestructura)

En cada servidor debe existir una regla sudoers **sin clave**, limitada a los usuarios de SO de sus instalaciones. Hay un ejemplo en `docs\sudoers.ejemplo`:

```
<usuario_ssh> ALL=(wlogic12c,wlogic14c) NOPASSWD: /bin/bash
```

Prueba desde Windows. Debe responder el usuario de SO, sin pedir clave:

```powershell
ssh -i $env:USERPROFILE\.ssh\cmops_ed25519 -o BatchMode=yes <usuario_ssh>@<IP> "sudo -n -u wlogic14c -H bash -c 'id -un'"
```

## Paso 7. Configurar `config\app.json` para modo real

Edite con el Bloc de notas. **En JSON use `/` en las rutas** (o `\\`):

```json
"modo_simulacion": false,
"ssh": {
  "usuario": "svc_capamedia",
  "llave": "C:/Users/<usuario>/.ssh/cmops_ed25519",
  "known_hosts": "C:/Users/<usuario>/.ssh/known_hosts",
  "verificar_host": true,
  "timeout_conexion": 10,
  "control_master": true
}
```

- `ssh.usuario` es la cuenta por defecto, para los servidores que no tienen `ssh_usuario` propio.
- `"llave"` puede quedar vacía si usa la opción B del paso 3 (agente).

## Paso 8. Cargar los usuarios SSH por servidor y revisar las instalaciones

```powershell
python bin\cm importar-usuarios-ssh "..\capa_media\config\.env"   # el .env REAL; las claves se ignoran
```

- Revise `inventario\configuracion_modulos.json`: los usuarios de SO, rutas y la IP SSH de WLPC02SBCC04.
- Complete la tabla de usuarios de `docs\PRUEBA_INTEGRAL.md`.

## Paso 9. Crear los usuarios reales de la interfaz y quitar los demo

Ejecute esto en una ventana normal de PowerShell o CMD, porque las claves se piden de forma oculta:

```powershell
python bin\cm usuario-app crear <su_usuario> --rol admin
python bin\cm usuario-app listar
del config\CREDENCIALES_DEMO.txt
```

Para quitar `admin`, `operador` y `consulta` (los usuarios demo), elimine sus entradas en `config\usuarios_app.json`.

## Paso 10. Levantar la plataforma y hacer la prueba integral

```powershell
python bin\cm web
```

Abra **http://127.0.0.1:8080** y siga `docs\PRUEBA_INTEGRAL.md`:

1. Probar conexión.
2. Recolectar información.
3. Proponer y validar las instalaciones.
4. Verificar credenciales.
5. Registrar credenciales.
6. Consultar estado.
7. Tomar snapshot.
8. Bajar y subir un dominio DESA.

Empiece por WLPC02SBCC04.

Equivalente por CLI, sin la interfaz:

```powershell
python bin\cm ejecutar sys.conectividad hosts=WLPC02SBCC04
python bin\cm ejecutar orq.verificar host=WLPC02SBCC04 instalacion=wlogic14c
```

---

## Opcional: dejarla corriendo

- **Al iniciar sesión en Windows**, sin instalar servicios:
  ```powershell
  schtasks /Create /SC ONLOGON /TN "CapaMediaOps" /TR "python \"C:\Users\<usuario>\Downloads\Capa Media Desarrollo\capa-media-ops\bin\cm\" web"
  ```
- **Compartirla con el equipo:**
  - En `app.json`, `"escuchar": "0.0.0.0"` y certificado TLS en `"tls"`.
  - Una regla en el firewall de Windows para el puerto 8080, que requiere un administrador.
  - Mientras corra en un PC personal, la evidencia y la auditoría quedan en ese PC. Para operación permanente conviene un servidor (Linux o Windows) dedicado.

## Errores frecuentes

| Mensaje | Causa | Solución |
|---|---|---|
| `UNPROTECTED PRIVATE KEY FILE` | La llave tiene permisos heredados | Comando `icacls` del paso 3 |
| `Host key verification failed` | Servidor no registrado en known_hosts, o cambió su huella | Paso 5 (confirmar la huella con infraestructura) |
| `Permission denied (publickey)` | Llave pública no instalada en ese usuario SSH, o `ssh_usuario` errado | Paso 4 y revisar `ssh_usuario` del host |
| `sudo: a password is required` | Falta la regla sudoers hacia ese usuario de SO | Paso 6 |
| `ERROR: el dominio X pertenece a 'A' y se esta ejecutando como 'B'` | `usuario_so` de la instalación incorrecto | Corregirlo en `configuracion_modulos.json` y **Recargar inventario** |
| `Connection timed out` | Firewall o IP SSH incorrecta | `Test-NetConnection <IP> -Port 22`, revisar `ssh_ip` |
| `python` abre la Microsoft Store | Alias de Windows | Use `py`, o desactive el alias en Configuración → Aplicaciones → Alias de ejecución |
| `OSError: [WinError 10048]` al levantar | Puerto 8080 ocupado | Cambiar `"puerto"` en `app.json` |
