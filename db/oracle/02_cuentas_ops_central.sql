-- =============================================================================
-- Ops Central · Cuentas de servicio en cada base Oracle
--   OPS_CONSULTA : lectura para todo el equipo (recolección, pantallas, asistente)
--   OPS_DBA      : funciones DBA; SOLO puede ejecutar ADMINBD.PKG_OPS_CENTRAL
-- Ejecutar como DBA, primero en TEST. Las claves las genera y rota el gestor de
-- secretos: aquí van marcadores que se reemplazan en el despliegue.
-- Requisito previo: 01_pkg_ops_central.sql (esquema ADMINBD, tablas y paquete).
-- REVISAR CON EL EQUIPO BD antes de PROD (nombres de perfil, tablespaces, IPs).
-- =============================================================================

-- ---------------------------------------------------------------- perfiles
CREATE PROFILE PRF_OPS_CONSULTA LIMIT
  SESSIONS_PER_USER        2          -- 1 de recolección + 1 de refresco a demanda
  CPU_PER_CALL             3000       -- 30 s de CPU por llamada
  LOGICAL_READS_PER_CALL   2000000
  IDLE_TIME                15
  CONNECT_TIME             120
  FAILED_LOGIN_ATTEMPTS    5
  PASSWORD_LOCK_TIME       1/96       -- 15 min
  PASSWORD_LIFE_TIME       UNLIMITED  -- la rotación la hace el gestor de secretos (90 días)
  PASSWORD_REUSE_MAX       12;

CREATE PROFILE PRF_OPS_DBA LIMIT
  SESSIONS_PER_USER        2
  CPU_PER_CALL             60000
  IDLE_TIME                10
  CONNECT_TIME             120
  FAILED_LOGIN_ATTEMPTS    3
  PASSWORD_LOCK_TIME       1/24
  PASSWORD_LIFE_TIME       UNLIMITED  -- rotación por el gestor de secretos (30 días)
  PASSWORD_REUSE_MAX       24;

-- ---------------------------------------------------------------- OPS_CONSULTA
CREATE USER OPS_CONSULTA IDENTIFIED BY "&CLAVE_OPS_CONSULTA"
  DEFAULT TABLESPACE USERS QUOTA 0 ON USERS
  PROFILE PRF_OPS_CONSULTA;
GRANT CREATE SESSION TO OPS_CONSULTA;
GRANT SELECT_CATALOG_ROLE TO OPS_CONSULTA;           -- V$ / DBA_ de solo lectura
GRANT SELECT ON ADMINBD.TBL_BASE_OBJETOS_INVALIDOS TO OPS_CONSULTA;
-- Sin privilegios de sistema adicionales, sin DML, sin DDL.

-- ---------------------------------------------------------------- OPS_DBA
CREATE USER OPS_DBA IDENTIFIED BY "&CLAVE_OPS_DBA"
  DEFAULT TABLESPACE USERS QUOTA 0 ON USERS
  PROFILE PRF_OPS_DBA;
GRANT CREATE SESSION TO OPS_DBA;
GRANT EXECUTE ON ADMINBD.PKG_OPS_CENTRAL TO OPS_DBA;  -- lo ÚNICO que puede hacer
-- NO: rol DBA, privilegios ANY, ALTER SYSTEM, SELECT_CATALOG_ROLE.
-- Si la clave de OPS_DBA se filtrara, solo serviría para lo que el paquete valida.

-- ---------------------------------------------------------------- consumo bajo (Resource Manager)
BEGIN
  DBMS_RESOURCE_MANAGER.CREATE_PENDING_AREA;
  DBMS_RESOURCE_MANAGER.CREATE_CONSUMER_GROUP('OPS_CENTRAL_BAJA', 'Ops Central: lectura de baja prioridad');
  DBMS_RESOURCE_MANAGER.SET_CONSUMER_GROUP_MAPPING(DBMS_RESOURCE_MANAGER.ORACLE_USER, 'OPS_CONSULTA', 'OPS_CENTRAL_BAJA');
  DBMS_RESOURCE_MANAGER.SUBMIT_PENDING_AREA;
  DBMS_RESOURCE_MANAGER_PRIVS.GRANT_SWITCH_CONSUMER_GROUP('OPS_CONSULTA', 'OPS_CENTRAL_BAJA', FALSE);
END;
/
-- Nota: el grupo debe incluirse en el plan de recursos activo de cada base (lo define el equipo BD).

-- ---------------------------------------------------------------- solo desde los workers
-- Rechaza conexiones de las cuentas de servicio que no vengan de las IPs de Ops Central.
CREATE OR REPLACE TRIGGER ADMINBD.TRG_OPS_CENTRAL_ORIGEN
AFTER LOGON ON DATABASE
DECLARE
  v_usr VARCHAR2(128) := SYS_CONTEXT('USERENV', 'SESSION_USER');
  v_ip  VARCHAR2(64)  := SYS_CONTEXT('USERENV', 'IP_ADDRESS');
BEGIN
  IF v_usr IN ('OPS_CONSULTA', 'OPS_DBA') THEN
    -- &IPS_WORKERS: lista de IPs de worker-bd / worker-ingesta, p.ej. '10.9.0.21','10.9.0.22'
    IF v_ip IS NULL OR v_ip NOT IN (&IPS_WORKERS) THEN
      RAISE_APPLICATION_ERROR(-20900, 'Ops Central: origen no autorizado para ' || v_usr);
    END IF;
  END IF;
END;
/

-- ---------------------------------------------------------------- auditoría unificada
CREATE AUDIT POLICY POL_OPS_DBA
  ACTIONS ALL
  ACTIONS COMPONENT=DATAPUMP ALL;
AUDIT POLICY POL_OPS_DBA BY OPS_DBA;

CREATE AUDIT POLICY POL_OPS_CONSULTA_LOGON ACTIONS LOGON, LOGOFF;
AUDIT POLICY POL_OPS_CONSULTA_LOGON BY OPS_CONSULTA WHENEVER NOT SUCCESSFUL;
-- Consultar: SELECT event_timestamp, dbusername, client_identifier, action_name, sql_text
--              FROM unified_audit_trail WHERE dbusername = 'OPS_DBA' ORDER BY 1 DESC;
-- client_identifier = persona de Ops Central; module/action = 'OpsCentral' / id de ejecución.
