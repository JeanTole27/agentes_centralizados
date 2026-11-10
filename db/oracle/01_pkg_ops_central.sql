-- =============================================================================
-- Ops Central · ADMINBD.PKG_OPS_CENTRAL (derechos del dueño)
-- Es la única puerta de cambio de OPS_DBA. Cada procedimiento valida sus
-- parámetros, arma la sentencia con DBMS_ASSERT y deja bitácora.
-- Amplía el ADMINBD.PRC_TERMINAR_SESION previsto en H2.
--
-- Privilegios que necesita ADMINBD (DIRECTOS, no por rol, por ser definer rights).
-- REVISAR CON EL EQUIPO BD: otorgar solo los de las funciones que se habiliten.
--   GRANT ALTER SYSTEM              TO ADMINBD;  -- solo para KILL SESSION
--   GRANT ALTER ANY PROCEDURE, ALTER ANY TRIGGER, ALTER ANY TYPE,
--         ALTER ANY MATERIALIZED VIEW, CREATE ANY VIEW TO ADMINBD;  -- recompilar (vistas: ALTER VIEW)
--   GRANT CREATE PUBLIC SYNONYM     TO ADMINBD;  -- recompilar sinónimos públicos
--   GRANT ALTER USER                TO ADMINBD;  -- desbloquear cuentas
--   GRANT ALTER TABLESPACE          TO ADMINBD;  -- agregar datafile
--   GRANT ANALYZE ANY               TO ADMINBD;  -- DBMS_STATS en otros esquemas
--   GRANT CREATE ANY JOB, MANAGE SCHEDULER TO ADMINBD;  -- jobs de otros esquemas
--   GRANT SELECT ON SYS.GV_$SESSION TO ADMINBD;
--   GRANT SELECT ON SYS.DBA_OBJECTS TO ADMINBD;  (y DBA_USERS, DBA_TABLESPACES, DBA_SCHEDULER_JOBS, V_$ASM_DISKGROUP)
-- =============================================================================

-- ---------------------------------------------------------------- tablas de apoyo
CREATE TABLE ADMINBD.TBL_BASE_OBJETOS_INVALIDOS (
  OWNER           VARCHAR2(128) NOT NULL,
  OBJECT_NAME     VARCHAR2(128) NOT NULL,
  OBJECT_TYPE     VARCHAR2(30)  NOT NULL,
  JUSTIFICACION   VARCHAR2(500) NOT NULL,
  REGISTRADO_POR  VARCHAR2(128) NOT NULL,
  FECHA_REGISTRO  DATE DEFAULT SYSDATE NOT NULL,
  ACTIVO          CHAR(1) DEFAULT 'S' NOT NULL CHECK (ACTIVO IN ('S','N')),
  CONSTRAINT PK_BASE_OBJ_INV PRIMARY KEY (OWNER, OBJECT_NAME, OBJECT_TYPE)
);
-- Si la tabla ya existe con otra estructura, adaptar las columnas de la plantilla 'linea_base'.

CREATE TABLE ADMINBD.TBL_OPS_BITACORA (
  ID              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  FECHA           TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  PROCEDIMIENTO   VARCHAR2(40)  NOT NULL,
  EJECUCION       VARCHAR2(40)  NOT NULL,
  PERSONA         VARCHAR2(64),
  DETALLE         VARCHAR2(2000),
  RESULTADO       VARCHAR2(200)
);

CREATE OR REPLACE PACKAGE ADMINBD.PKG_OPS_CENTRAL AUTHID DEFINER AS
  -- Todos devuelven p_resultado y registran en TBL_OPS_BITACORA.
  PROCEDURE TERMINAR_SESION(p_sid IN NUMBER, p_serial IN NUMBER, p_inst_id IN NUMBER, p_motivo IN VARCHAR2,
                            p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE RECOMPILAR_OBJETO(p_propietario IN VARCHAR2, p_objeto IN VARCHAR2, p_tipo_objeto IN VARCHAR2,
                              p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE COMPILAR_ESQUEMA(p_esquema IN VARCHAR2, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE ACTUALIZAR_LINEA_BASE(p_operacion IN VARCHAR2, p_propietario IN VARCHAR2, p_objeto IN VARCHAR2,
                                  p_tipo_objeto IN VARCHAR2, p_justificacion IN VARCHAR2,
                                  p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE DESBLOQUEAR_USUARIO(p_usuario IN VARCHAR2, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE AGREGAR_DATAFILE(p_tablespace IN VARCHAR2, p_diskgroup IN VARCHAR2, p_tamano_gb IN NUMBER,
                             p_maximo_gb IN NUMBER, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE RECOLECTAR_ESTADISTICAS(p_esquema IN VARCHAR2, p_tabla IN VARCHAR2 DEFAULT NULL,
                                    p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
  PROCEDURE EJECUTAR_JOB(p_propietario IN VARCHAR2, p_job IN VARCHAR2, p_ejecucion IN VARCHAR2,
                         p_resultado OUT VARCHAR2);
  PROCEDURE CAMBIAR_ESTADO_JOB(p_propietario IN VARCHAR2, p_job IN VARCHAR2, p_habilitar IN VARCHAR2,
                               p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2);
END PKG_OPS_CENTRAL;
/

CREATE OR REPLACE PACKAGE BODY ADMINBD.PKG_OPS_CENTRAL AS

  -- Cuentas que nunca se tocan, además de las ORACLE_MAINTAINED.
  FUNCTION es_protegido(p_usuario IN VARCHAR2) RETURN BOOLEAN IS
    v_om VARCHAR2(1);
  BEGIN
    IF UPPER(p_usuario) IN ('SYS','SYSTEM','ADMINBD','OPS_CONSULTA','OPS_DBA','DBSNMP','SYSMAN') THEN
      RETURN TRUE;
    END IF;
    SELECT oracle_maintained INTO v_om FROM dba_users WHERE username = UPPER(p_usuario);
    RETURN v_om = 'Y';
  EXCEPTION WHEN NO_DATA_FOUND THEN
    RETURN FALSE;
  END;

  PROCEDURE bitacora(p_proc VARCHAR2, p_ejec VARCHAR2, p_det VARCHAR2, p_res VARCHAR2) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    INSERT INTO adminbd.tbl_ops_bitacora (procedimiento, ejecucion, persona, detalle, resultado)
    VALUES (p_proc, SUBSTR(p_ejec, 1, 40), SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER'),
            SUBSTR(p_det, 1, 2000), SUBSTR(p_res, 1, 200));
    COMMIT;
  END;

  PROCEDURE exigir_ejecucion(p_ejec VARCHAR2) IS
  BEGIN
    IF p_ejec IS NULL OR NOT REGEXP_LIKE(p_ejec, '^[0-9a-z-]{8,40}$') THEN
      RAISE_APPLICATION_ERROR(-20001, 'Falta el identificador de ejecución de Ops Central');
    END IF;
  END;

  -- -------------------------------------------------------------- sesiones
  PROCEDURE TERMINAR_SESION(p_sid IN NUMBER, p_serial IN NUMBER, p_inst_id IN NUMBER, p_motivo IN VARCHAR2,
                            p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_usr  VARCHAR2(128);
    v_tipo VARCHAR2(10);
    v_mod  VARCHAR2(64);
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF p_motivo IS NULL OR LENGTH(p_motivo) < 10 THEN
      RAISE_APPLICATION_ERROR(-20021, 'Motivo obligatorio (mínimo 10 caracteres)');
    END IF;
    BEGIN
      SELECT username, type, module INTO v_usr, v_tipo, v_mod
        FROM gv$session WHERE sid = p_sid AND serial# = p_serial AND inst_id = NVL(p_inst_id, 1);
    EXCEPTION WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20020, 'La sesión no existe o ya terminó');
    END;
    IF v_tipo = 'BACKGROUND' OR v_usr IS NULL THEN
      RAISE_APPLICATION_ERROR(-20022, 'No se terminan procesos background');
    END IF;
    IF es_protegido(v_usr) THEN
      RAISE_APPLICATION_ERROR(-20023, 'La sesión pertenece a una cuenta protegida: ' || v_usr);
    END IF;
    IF v_mod = 'OpsCentral' THEN
      RAISE_APPLICATION_ERROR(-20024, 'No se terminan sesiones de la propia plataforma');
    END IF;
    EXECUTE IMMEDIATE 'ALTER SYSTEM KILL SESSION ''' || TO_CHAR(p_sid) || ',' || TO_CHAR(p_serial) ||
                      ',@' || TO_CHAR(NVL(p_inst_id, 1)) || ''' IMMEDIATE';
    p_resultado := 'TERMINADA';
    bitacora('TERMINAR_SESION', p_ejecucion,
             'sid=' || p_sid || ' serial=' || p_serial || ' inst=' || p_inst_id || ' usuario=' || v_usr ||
             ' motivo=' || p_motivo, p_resultado);
  END;

  -- -------------------------------------------------------------- objetos inválidos
  PROCEDURE RECOMPILAR_OBJETO(p_propietario IN VARCHAR2, p_objeto IN VARCHAR2, p_tipo_objeto IN VARCHAR2,
                              p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_own  VARCHAR2(128) := UPPER(p_propietario);
    v_obj  VARCHAR2(128) := p_objeto;
    v_tipo VARCHAR2(30)  := UPPER(p_tipo_objeto);
    v_est  VARCHAR2(10);
    v_lb   NUMBER;
    v_sql  VARCHAR2(600);
    v_nom  VARCHAR2(300);
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF v_obj LIKE 'BIN$%' THEN
      RAISE_APPLICATION_ERROR(-20010, 'No se recompilan objetos de la papelera');
    END IF;
    IF v_own <> 'PUBLIC' AND es_protegido(v_own) THEN
      RAISE_APPLICATION_ERROR(-20012, 'Esquema protegido: ' || v_own);
    END IF;
    SELECT COUNT(*) INTO v_lb FROM adminbd.tbl_base_objetos_invalidos
     WHERE owner = v_own AND object_name = v_obj AND object_type = v_tipo AND activo = 'S';
    IF v_lb > 0 THEN
      RAISE_APPLICATION_ERROR(-20011, 'El objeto está en la línea base de inválidos conocidos');
    END IF;
    BEGIN
      SELECT status INTO v_est FROM dba_objects WHERE owner = v_own AND object_name = v_obj AND object_type = v_tipo;
    EXCEPTION WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20013, 'El objeto no existe');
    END;
    IF v_est = 'VALID' THEN
      p_resultado := 'VALID';
      RETURN;
    END IF;
    IF v_own = 'PUBLIC' THEN
      v_nom := DBMS_ASSERT.ENQUOTE_NAME(v_obj, FALSE);
    ELSE
      v_nom := DBMS_ASSERT.ENQUOTE_NAME(v_own, FALSE) || '.' || DBMS_ASSERT.ENQUOTE_NAME(v_obj, FALSE);
    END IF;
    v_sql := CASE v_tipo
      WHEN 'PACKAGE'           THEN 'ALTER PACKAGE '           || v_nom || ' COMPILE'
      WHEN 'PACKAGE BODY'      THEN 'ALTER PACKAGE '           || v_nom || ' COMPILE BODY'
      WHEN 'TYPE'              THEN 'ALTER TYPE '              || v_nom || ' COMPILE'
      WHEN 'TYPE BODY'         THEN 'ALTER TYPE '              || v_nom || ' COMPILE BODY'
      WHEN 'PROCEDURE'         THEN 'ALTER PROCEDURE '         || v_nom || ' COMPILE'
      WHEN 'FUNCTION'          THEN 'ALTER FUNCTION '          || v_nom || ' COMPILE'
      WHEN 'TRIGGER'           THEN 'ALTER TRIGGER '           || v_nom || ' COMPILE'
      WHEN 'VIEW'              THEN 'ALTER VIEW '              || v_nom || ' COMPILE'
      WHEN 'MATERIALIZED VIEW' THEN 'ALTER MATERIALIZED VIEW ' || v_nom || ' COMPILE'
      WHEN 'SYNONYM'           THEN CASE WHEN v_own = 'PUBLIC' THEN 'ALTER PUBLIC SYNONYM ' ELSE 'ALTER SYNONYM ' END
                                    || v_nom || ' COMPILE'
    END;
    IF v_sql IS NULL THEN
      RAISE_APPLICATION_ERROR(-20014, 'Tipo de objeto no soportado: ' || v_tipo);
    END IF;
    BEGIN
      EXECUTE IMMEDIATE v_sql;
    EXCEPTION WHEN OTHERS THEN
      IF SQLCODE <> -24344 THEN   -- ORA-24344 = compiló con errores: se informa el estado final
        -- Cualquier otro error (p. ej. ORA-01031 sin privilegio) no se oculta como si fuera INVALID
        bitacora('RECOMPILAR_OBJETO', p_ejecucion, v_tipo || ' ' || v_own || '.' || v_obj || ' -> ' || SQLERRM, 'ERROR');
        RAISE_APPLICATION_ERROR(-20017, 'No se pudo recompilar ' || v_own || '.' || v_obj || ': ' || SQLERRM);
      END IF;
    END;
    SELECT status INTO v_est FROM dba_objects WHERE owner = v_own AND object_name = v_obj AND object_type = v_tipo;
    p_resultado := v_est;
    bitacora('RECOMPILAR_OBJETO', p_ejecucion, v_tipo || ' ' || v_own || '.' || v_obj || ' (' || v_sql || ')', p_resultado);
  END;

  PROCEDURE COMPILAR_ESQUEMA(p_esquema IN VARCHAR2, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_n NUMBER;
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF es_protegido(p_esquema) THEN
      RAISE_APPLICATION_ERROR(-20012, 'Esquema protegido: ' || p_esquema);
    END IF;
    DBMS_UTILITY.COMPILE_SCHEMA(schema => DBMS_ASSERT.SCHEMA_NAME(UPPER(p_esquema)), compile_all => FALSE);
    SELECT COUNT(*) INTO v_n FROM dba_objects WHERE owner = UPPER(p_esquema) AND status = 'INVALID';
    p_resultado := 'INVALIDOS_RESTANTES=' || v_n;
    bitacora('COMPILAR_ESQUEMA', p_ejecucion, p_esquema, p_resultado);
  END;

  PROCEDURE ACTUALIZAR_LINEA_BASE(p_operacion IN VARCHAR2, p_propietario IN VARCHAR2, p_objeto IN VARCHAR2,
                                  p_tipo_objeto IN VARCHAR2, p_justificacion IN VARCHAR2,
                                  p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_n NUMBER;
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF p_justificacion IS NULL OR LENGTH(p_justificacion) < 15 THEN
      RAISE_APPLICATION_ERROR(-20015, 'Justificación obligatoria (mínimo 15 caracteres)');
    END IF;
    IF UPPER(p_operacion) = 'AGREGAR' THEN
      SELECT COUNT(*) INTO v_n FROM dba_objects
       WHERE owner = UPPER(p_propietario) AND object_name = p_objeto AND object_type = UPPER(p_tipo_objeto);
      IF v_n = 0 THEN
        RAISE_APPLICATION_ERROR(-20013, 'El objeto no existe: no se agrega a la línea base');
      END IF;
      MERGE INTO adminbd.tbl_base_objetos_invalidos t
      USING (SELECT UPPER(p_propietario) o, p_objeto n, UPPER(p_tipo_objeto) ty FROM dual) s
         ON (t.owner = s.o AND t.object_name = s.n AND t.object_type = s.ty)
      WHEN MATCHED THEN UPDATE SET activo = 'S', justificacion = p_justificacion,
                                   registrado_por = NVL(SYS_CONTEXT('USERENV','CLIENT_IDENTIFIER'), USER),
                                   fecha_registro = SYSDATE
      WHEN NOT MATCHED THEN INSERT (owner, object_name, object_type, justificacion, registrado_por)
                            VALUES (s.o, s.n, s.ty, p_justificacion,
                                    NVL(SYS_CONTEXT('USERENV','CLIENT_IDENTIFIER'), USER));
    ELSIF UPPER(p_operacion) = 'RETIRAR' THEN
      UPDATE adminbd.tbl_base_objetos_invalidos SET activo = 'N'
       WHERE owner = UPPER(p_propietario) AND object_name = p_objeto AND object_type = UPPER(p_tipo_objeto);
    ELSE
      RAISE_APPLICATION_ERROR(-20016, 'Operación inválida');
    END IF;
    COMMIT;
    p_resultado := UPPER(p_operacion);
    bitacora('ACTUALIZAR_LINEA_BASE', p_ejecucion, p_operacion || ' ' || p_propietario || '.' || p_objeto ||
             ' (' || p_justificacion || ')', p_resultado);
  END;

  -- -------------------------------------------------------------- cuentas
  PROCEDURE DESBLOQUEAR_USUARIO(p_usuario IN VARCHAR2, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_est VARCHAR2(32);
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF es_protegido(p_usuario) THEN
      RAISE_APPLICATION_ERROR(-20030, 'Usuario protegido');
    END IF;
    BEGIN
      SELECT account_status INTO v_est FROM dba_users WHERE username = UPPER(p_usuario);
    EXCEPTION WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20031, 'El usuario no existe');
    END;
    IF v_est NOT LIKE '%LOCKED%' THEN   -- idempotente: no se toca una cuenta que no está bloqueada
      p_resultado := v_est;
      bitacora('DESBLOQUEAR_USUARIO', p_ejecucion, p_usuario || ' no estaba bloqueada', p_resultado);
      RETURN;
    END IF;
    -- EXPIRED & LOCKED: se desbloquea, pero la clave sigue vencida (el cambio de clave va por canal seguro)
    EXECUTE IMMEDIATE 'ALTER USER ' || DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_usuario), FALSE) || ' ACCOUNT UNLOCK';
    SELECT account_status INTO p_resultado FROM dba_users WHERE username = UPPER(p_usuario);
    bitacora('DESBLOQUEAR_USUARIO', p_ejecucion, p_usuario || ' estaba ' || v_est, p_resultado);
  END;

  -- -------------------------------------------------------------- espacio
  PROCEDURE AGREGAR_DATAFILE(p_tablespace IN VARCHAR2, p_diskgroup IN VARCHAR2, p_tamano_gb IN NUMBER,
                             p_maximo_gb IN NUMBER, p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_n       NUMBER;
    v_free    NUMBER;
    v_bigfile VARCHAR2(3);
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF p_tamano_gb NOT BETWEEN 1 AND 32 OR p_maximo_gb NOT BETWEEN p_tamano_gb AND 32 THEN
      RAISE_APPLICATION_ERROR(-20040, 'Tamaño fuera de rango (1–32 GB, máximo >= inicial)');
    END IF;
    BEGIN
      SELECT bigfile INTO v_bigfile FROM dba_tablespaces
       WHERE tablespace_name = UPPER(p_tablespace) AND contents = 'PERMANENT';
    EXCEPTION WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20041, 'Tablespace inexistente o no permanente');
    END;
    IF v_bigfile = 'YES' THEN
      RAISE_APPLICATION_ERROR(-20045, 'Tablespace BIGFILE: no admite más datafiles (se amplía con RESIZE, manual)');
    END IF;
    IF UPPER(p_tablespace) IN ('SYSTEM', 'SYSAUX') THEN
      RAISE_APPLICATION_ERROR(-20042, 'SYSTEM y SYSAUX se amplían manualmente');
    END IF;
    BEGIN
      SELECT usable_file_mb INTO v_free FROM v$asm_diskgroup WHERE name = UPPER(p_diskgroup);
    EXCEPTION WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20043, 'Diskgroup inexistente');
    END;
    IF v_free < p_maximo_gb * 1024 * 1.2 THEN
      RAISE_APPLICATION_ERROR(-20044, 'Espacio insuficiente en el diskgroup para el máximo solicitado');
    END IF;
    EXECUTE IMMEDIATE 'ALTER TABLESPACE ' || DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_tablespace), FALSE) ||
                      ' ADD DATAFILE ''+' || DBMS_ASSERT.SIMPLE_SQL_NAME(UPPER(p_diskgroup)) || ''' SIZE ' ||
                      TRUNC(p_tamano_gb) || 'G AUTOEXTEND ON NEXT 1G MAXSIZE ' || TRUNC(p_maximo_gb) || 'G';
    p_resultado := 'DATAFILE_AGREGADO';
    bitacora('AGREGAR_DATAFILE', p_ejecucion, p_tablespace || ' +' || p_diskgroup || ' ' || p_tamano_gb || 'G/' ||
             p_maximo_gb || 'G', p_resultado);
  END;

  -- -------------------------------------------------------------- estadísticas y jobs
  PROCEDURE RECOLECTAR_ESTADISTICAS(p_esquema IN VARCHAR2, p_tabla IN VARCHAR2 DEFAULT NULL,
                                    p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF es_protegido(p_esquema) THEN
      RAISE_APPLICATION_ERROR(-20050, 'Esquema protegido');
    END IF;
    IF p_tabla IS NULL THEN
      DBMS_STATS.GATHER_SCHEMA_STATS(ownname => DBMS_ASSERT.SCHEMA_NAME(UPPER(p_esquema)), options => 'GATHER STALE');
    ELSE
      DBMS_STATS.GATHER_TABLE_STATS(ownname => DBMS_ASSERT.SCHEMA_NAME(UPPER(p_esquema)),
                                    tabname => DBMS_ASSERT.SIMPLE_SQL_NAME(UPPER(p_tabla)), cascade => TRUE);
    END IF;
    p_resultado := 'OK';
    bitacora('RECOLECTAR_ESTADISTICAS', p_ejecucion, p_esquema || '.' || NVL(p_tabla, '*'), p_resultado);
  END;

  PROCEDURE validar_job(p_propietario VARCHAR2, p_job VARCHAR2) IS
    v_n NUMBER;
  BEGIN
    IF es_protegido(p_propietario) THEN
      RAISE_APPLICATION_ERROR(-20060, 'Los jobs de cuentas protegidas no se tocan');
    END IF;
    SELECT COUNT(*) INTO v_n FROM dba_scheduler_jobs WHERE owner = UPPER(p_propietario) AND job_name = UPPER(p_job);
    IF v_n = 0 THEN
      RAISE_APPLICATION_ERROR(-20061, 'Job inexistente');
    END IF;
  END;

  PROCEDURE EJECUTAR_JOB(p_propietario IN VARCHAR2, p_job IN VARCHAR2, p_ejecucion IN VARCHAR2,
                         p_resultado OUT VARCHAR2) IS
  BEGIN
    exigir_ejecucion(p_ejecucion);
    validar_job(p_propietario, p_job);
    DBMS_SCHEDULER.RUN_JOB(job_name => DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_propietario), FALSE) || '.' ||
                                       DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_job), FALSE), use_current_session => FALSE);
    p_resultado := 'LANZADO';
    bitacora('EJECUTAR_JOB', p_ejecucion, p_propietario || '.' || p_job, p_resultado);
  END;

  PROCEDURE CAMBIAR_ESTADO_JOB(p_propietario IN VARCHAR2, p_job IN VARCHAR2, p_habilitar IN VARCHAR2,
                               p_ejecucion IN VARCHAR2, p_resultado OUT VARCHAR2) IS
    v_nom VARCHAR2(300);
  BEGIN
    exigir_ejecucion(p_ejecucion);
    IF p_habilitar IS NULL OR p_habilitar NOT IN ('S', 'N') THEN
      RAISE_APPLICATION_ERROR(-20062, 'p_habilitar debe ser S o N');
    END IF;
    validar_job(p_propietario, p_job);
    v_nom := DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_propietario), FALSE) || '.' || DBMS_ASSERT.ENQUOTE_NAME(UPPER(p_job), FALSE);
    IF p_habilitar = 'S' THEN
      DBMS_SCHEDULER.ENABLE(v_nom);
      p_resultado := 'HABILITADO';
    ELSE
      DBMS_SCHEDULER.DISABLE(v_nom, force => FALSE);
      p_resultado := 'DESHABILITADO';
    END IF;
    bitacora('CAMBIAR_ESTADO_JOB', p_ejecucion, p_propietario || '.' || p_job, p_resultado);
  END;

END PKG_OPS_CENTRAL;
/
