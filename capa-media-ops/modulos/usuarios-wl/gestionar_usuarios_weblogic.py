# ============================================================
# Script WLST para gestion de usuarios en WebLogic (generico)
# Capa Media Ops - modulo usuarios-wl
# Parametros por variables de entorno (dominio.env / ejecutar_wlst.sh):
#   ADMIN_URL, SECURITY_REALM, DOMINIO_ID, CMOPS_BASE_DIR,
#   ARCHIVO_USUARIOS (acciones) o LISTAR_SOLO=1 (solo listado)
# ============================================================

import sys
import os

ADMIN_URL = os.environ.get('ADMIN_URL', '')
DOMINIO_ID = os.environ.get('DOMINIO_ID', '')
RUTA_CREDENCIALES = os.environ.get('CMOPS_BASE_DIR', '.')
USER_CONFIG_FILE = RUTA_CREDENCIALES + '/.wl_userconfig'
USER_KEY_FILE = RUTA_CREDENCIALES + '/.wl_keyfile'

ARCHIVO_USUARIOS = os.environ.get('ARCHIVO_USUARIOS', '')
LISTAR_SOLO = os.environ.get('LISTAR_SOLO', '') == '1'
ARCHIVO_RESULTADO = RUTA_CREDENCIALES + '/usuarios_weblogic_resultado.txt'

SECURITY_REALM = os.environ.get('SECURITY_REALM', 'myrealm')


def log_info(mensaje):
    print('[INFO]  ' + str(mensaje))

def log_ok(mensaje):
    print('[OK]    ' + str(mensaje))

def log_error(mensaje):
    print('[ERROR] ' + str(mensaje))

def log_warn(mensaje):
    print('[WARN]  ' + str(mensaje))

def log_separador():
    print('=' * 60)


def conectar_admin_server():
    log_info('Conectando a AdminServer: ' + ADMIN_URL)

    if not os.path.exists(USER_CONFIG_FILE):
        log_error('No se encontro el archivo de credenciales: ' + USER_CONFIG_FILE)
        log_error('Ejecute primero: generar_credenciales_weblogic.py')
        return False

    if not os.path.exists(USER_KEY_FILE):
        log_error('No se encontro el archivo de llave: ' + USER_KEY_FILE)
        return False

    try:
        connect(userConfigFile=USER_CONFIG_FILE, userKeyFile=USER_KEY_FILE, url=ADMIN_URL)
        log_ok('Conexion exitosa al AdminServer')
        return True
    except Exception, e:
        log_error('No se pudo conectar al AdminServer: ' + str(e))
        return False


def obtener_ruta_realm():
    return '/SecurityConfiguration/' + domainName + '/Realms/' + SECURITY_REALM


def usuario_existe(nombre_usuario):
    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        return cmo.userExists(nombre_usuario)
    except Exception, e:
        log_error('Error al verificar usuario "' + nombre_usuario + '": ' + str(e))
        return False


def grupo_existe(nombre_grupo):
    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        return cmo.groupExists(nombre_grupo)
    except Exception, e:
        log_error('Error al verificar grupo "' + nombre_grupo + '": ' + str(e))
        return False


def es_miembro_grupo(nombre_usuario, nombre_grupo):
    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        return cmo.isMember(nombre_grupo, nombre_usuario, True)
    except Exception, e:
        log_error('Error al verificar membresia: ' + str(e))
        return False


def crear_usuario(nombre_usuario, password, descripcion=''):
    log_separador()
    log_info('CREAR usuario: ' + nombre_usuario)

    if not nombre_usuario or not password:
        log_error('Se requiere nombre de usuario y password para crear')
        return False

    if usuario_existe(nombre_usuario):
        log_warn('El usuario "' + nombre_usuario + '" ya existe. Se omite la creacion.')
        return False

    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        cmo.createUser(nombre_usuario, password, descripcion)

        if usuario_existe(nombre_usuario):
            log_ok('Usuario "' + nombre_usuario + '" creado exitosamente')
            return True
        else:
            log_error('No se pudo verificar la creacion del usuario')
            return False
    except Exception, e:
        log_error('Error al crear usuario "' + nombre_usuario + '": ' + str(e))
        return False


def eliminar_usuario(nombre_usuario):
    log_separador()
    log_info('ELIMINAR usuario: ' + nombre_usuario)

    if not nombre_usuario:
        log_error('Se requiere nombre de usuario para eliminar')
        return False

    if not usuario_existe(nombre_usuario):
        log_warn('El usuario "' + nombre_usuario + '" no existe. No se puede eliminar.')
        return False

    if nombre_usuario.lower() == 'weblogic':
        log_error('No se puede eliminar al usuario "weblogic" (administrador principal)')
        return False

    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        cmo.removeUser(nombre_usuario)

        if not usuario_existe(nombre_usuario):
            log_ok('Usuario "' + nombre_usuario + '" eliminado exitosamente')
            return True
        else:
            log_error('No se pudo verificar la eliminacion del usuario')
            return False
    except Exception, e:
        log_error('Error al eliminar usuario "' + nombre_usuario + '": ' + str(e))
        return False


def otorgar_admin(nombre_usuario):
    return agregar_a_grupo(nombre_usuario, 'Administrators')


def agregar_a_grupo(nombre_usuario, nombre_grupo):
    log_separador()
    log_info('AGREGAR usuario "' + nombre_usuario + '" al grupo "' + nombre_grupo + '"')

    if not nombre_usuario or not nombre_grupo:
        log_error('Se requiere nombre de usuario y grupo')
        return False

    if not usuario_existe(nombre_usuario):
        log_error('El usuario "' + nombre_usuario + '" no existe')
        return False

    if not grupo_existe(nombre_grupo):
        log_error('El grupo "' + nombre_grupo + '" no existe')
        return False

    if es_miembro_grupo(nombre_usuario, nombre_grupo):
        log_warn('El usuario "' + nombre_usuario + '" ya es miembro del grupo "' + nombre_grupo + '"')
        return False

    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        cmo.addMemberToGroup(nombre_grupo, nombre_usuario)

        if es_miembro_grupo(nombre_usuario, nombre_grupo):
            log_ok('Usuario "' + nombre_usuario + '" agregado al grupo "' + nombre_grupo + '" exitosamente')
            return True
        else:
            log_error('No se pudo verificar la adicion al grupo')
            return False
    except Exception, e:
        log_error('Error al agregar usuario al grupo: ' + str(e))
        return False


def leer_archivo_usuarios(ruta_archivo):
    acciones = []

    if not os.path.exists(ruta_archivo):
        log_error('No se encontro el archivo: ' + ruta_archivo)
        return acciones

    log_info('Leyendo archivo: ' + ruta_archivo)

    try:
        archivo = open(ruta_archivo, 'r')
        lineas = archivo.readlines()
        archivo.close()

        numero_linea = 0
        for linea in lineas:
            numero_linea = numero_linea + 1
            linea = linea.strip()

            if not linea or linea.startswith('#'):
                continue

            campos = linea.split('|')

            if len(campos) < 2:
                log_warn('Linea ' + str(numero_linea) + ' formato invalido: ' + linea)
                continue

            accion = campos[0].strip().upper()

            if len(campos) > 1:
                usuario = campos[1].strip()
            else:
                usuario = ''

            if len(campos) > 2:
                password = campos[2].strip()
            else:
                password = ''

            if len(campos) > 3:
                descripcion = campos[3].strip()
            else:
                descripcion = ''

            if len(campos) > 4:
                grupo = campos[4].strip()
            else:
                grupo = ''

            if not accion or not usuario:
                log_warn('Linea ' + str(numero_linea) + ': accion o usuario vacio')
                continue

            acciones.append({
                'linea': numero_linea,
                'accion': accion,
                'usuario': usuario,
                'password': password,
                'descripcion': descripcion,
                'grupo': grupo
            })

        log_info('Se encontraron ' + str(len(acciones)) + ' acciones a procesar')
        return acciones
    except Exception, e:
        log_error('Error al leer archivo: ' + str(e))
        return acciones


def procesar_acciones(acciones):
    resultados = {'exitosas': 0, 'fallidas': 0, 'omitidas': 0, 'total': len(acciones)}

    for item in acciones:
        accion = item['accion']
        usuario = item['usuario']
        password = item['password']
        descripcion = item['descripcion']
        grupo = item['grupo']

        exito = False

        if accion == 'CREAR':
            if not password:
                log_error('Linea ' + str(item['linea']) + ': Se requiere password para crear')
                resultados['fallidas'] = resultados['fallidas'] + 1
                continue
            exito = crear_usuario(usuario, password, descripcion)
            if exito and grupo:
                # si falla el alta al grupo, la accion se cuenta como fallida
                if not agregar_a_grupo(usuario, grupo):
                    log_error('Usuario creado pero no se pudo agregar al grupo "' + grupo + '"')
                    exito = False

        elif accion == 'ELIMINAR':
            exito = eliminar_usuario(usuario)

        elif accion == 'ADMIN':
            exito = otorgar_admin(usuario)

        elif accion == 'GRUPO':
            if not grupo:
                log_error('Linea ' + str(item['linea']) + ': Se requiere grupo para accion GRUPO')
                resultados['fallidas'] = resultados['fallidas'] + 1
                continue
            exito = agregar_a_grupo(usuario, grupo)

        else:
            log_warn('Accion desconocida en linea ' + str(item['linea']) + ': ' + accion)
            resultados['omitidas'] = resultados['omitidas'] + 1
            continue

        if exito:
            resultados['exitosas'] = resultados['exitosas'] + 1
        else:
            resultados['fallidas'] = resultados['fallidas'] + 1

    return resultados


def obtener_grupos_usuario(nombre_usuario):
    grupos = []
    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        cursor = cmo.listMemberGroups(nombre_usuario)
        while cmo.haveCurrent(cursor):
            grupos.append(cmo.getCurrentName(cursor))
            cmo.advance(cursor)
        cmo.close(cursor)
    except Exception, e:
        pass
    return grupos


def listar_usuarios():
    log_separador()
    print('')
    print('  LISTADO DE USUARIOS EN EL REALM')
    print('  --------------------------------')

    archivo_salida = ARCHIVO_RESULTADO

    lineas_salida = []
    lineas_salida.append('=' * 70)
    lineas_salida.append('  LISTADO DE USUARIOS WEBLOGIC')
    lineas_salida.append('  Servidor: ' + ADMIN_URL)
    lineas_salida.append('  Realm: ' + SECURITY_REALM)
    lineas_salida.append('=' * 70)
    lineas_salida.append('')
    lineas_salida.append('No.   USUARIO                        GRUPOS')
    lineas_salida.append('-' * 70)

    try:
        ruta_realm = obtener_ruta_realm()
        cd(ruta_realm + '/AuthenticationProviders/DefaultAuthenticator')
        cursor = cmo.listUsers('*', 0)
        contador = 0
        while cmo.haveCurrent(cursor):
            nombre = cmo.getCurrentName(cursor)
            grupos = obtener_grupos_usuario(nombre)

            if len(grupos) > 0:
                grupos_str = ', '.join(grupos)
            else:
                grupos_str = '(sin grupo)'

            contador = contador + 1
            print('  ' + str(contador) + '. ' + nombre + '  -->  ' + grupos_str)
            lineas_salida.append(str(contador) + '     ' + nombre + '     ' + grupos_str)

            cmo.advance(cursor)
        cmo.close(cursor)

        print('')
        print('  Total de usuarios: ' + str(contador))

        lineas_salida.append('')
        lineas_salida.append('-' * 70)
        lineas_salida.append('  Total de usuarios: ' + str(contador))
        lineas_salida.append('=' * 70)

        try:
            f = open(archivo_salida, 'w')
            for linea in lineas_salida:
                f.write(linea + '\n')
            f.close()
            print('')
            log_ok('Listado guardado en: ' + archivo_salida)
        except Exception, e:
            log_error('Error al escribir archivo de salida: ' + str(e))

    except Exception, e:
        log_error('Error al listar usuarios: ' + str(e))
    print('')
    log_separador()


def mostrar_resumen(resultados):
    log_separador()
    print('')
    print('  RESUMEN DE EJECUCION')
    print('  --------------------')
    print('  Total de acciones:   ' + str(resultados['total']))
    print('  Exitosas:            ' + str(resultados['exitosas']))
    print('  Fallidas:            ' + str(resultados['fallidas']))
    print('  Omitidas:            ' + str(resultados['omitidas']))
    print('')
    log_separador()


def main():
    print('')
    log_separador()
    print('  GESTION DE USUARIOS WEBLOGIC - WLST')
    print('  Dominio: ' + DOMINIO_ID + '  |  AdminServer: ' + ADMIN_URL)
    log_separador()
    print('')

    if LISTAR_SOLO:
        if not conectar_admin_server():
            sys.exit(3)
        try:
            serverConfig()
        except:
            pass
        listar_usuarios()
        try:
            disconnect()
        except:
            pass
        return

    acciones = leer_archivo_usuarios(ARCHIVO_USUARIOS)

    if not acciones:
        log_error('No hay acciones a procesar. Verifique: ' + ARCHIVO_USUARIOS)
        sys.exit(2)

    print('')
    log_info('Acciones a ejecutar:')
    for item in acciones:
        print('  - [' + item['accion'] + '] ' + item['usuario'])
    print('')

    if not conectar_admin_server():
        log_error('Abortando: No se pudo conectar al AdminServer')
        sys.exit(3)

    try:
        serverConfig()
    except:
        pass

    log_info('Iniciando procesamiento de acciones...')
    print('')
    resultados = procesar_acciones(acciones)

    mostrar_resumen(resultados)
    global CODIGO_SALIDA
    if resultados['fallidas'] > 0:
        CODIGO_SALIDA = 4

    log_info('Obteniendo listado final de usuarios...')
    listar_usuarios()

    try:
        disconnect()
        log_ok('Desconectado del AdminServer')
    except:
        pass


CODIGO_SALIDA = 0
main()
sys.exit(CODIGO_SALIDA)
