import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";

type Salud = { ok: boolean; proveedor_ingreso: string; version: string };
type PerfilDemo = { id: string; nombre: string };
type UsuarioDemo = { login: string; nombre: string; perfil: string };

// Demo: todavía no hay autenticación construida (ni SSO ni cuentas de BD). La persona se registra con su
// nombre y elige el perfil; con ese perfil se arma el menú y se deciden los permisos en la API.
const DESCRIPCION: Record<string, string> = {
  cm: "Orquestación WebLogic, usuarios WL y frmweb. Ejecuta las acciones de Capa Media.",
  dba: "Snapshot e inválidos, sesiones bloqueantes, salud de la flota y tareas DBA.",
  adm: "Administración de la plataforma, inventario y log de ejecuciones.",
  aut: "Todos los permisos de ejecución y edición, en Capa Media y Base de Datos.",
};

function iniciales(n: string) {
  return n.split(" ").filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}

export default function Ingreso() {
  const qc = useQueryClient();
  const salud = useQuery({ queryKey: ["salud"], queryFn: () => api.get<Salud>("/api/salud") });
  const perfiles = useQuery({ queryKey: ["perfiles-demo"], queryFn: () => api.get<PerfilDemo[]>("/api/auth/demo/perfiles"),
                              retry: false });
  const previos = useQuery({ queryKey: ["usuarios-demo"], queryFn: () => api.get<UsuarioDemo[]>("/api/auth/demo/usuarios"),
                             retry: false });
  const [nombre, setNombre] = useState("");
  const [login, setLogin] = useState("");
  const [perfil, setPerfil] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function registrar(datos?: UsuarioDemo) {
    const cuerpo = datos ? { nombre: datos.nombre, login: datos.login, perfil: datos.perfil } : { nombre, login, perfil };
    if (!cuerpo.nombre.trim()) return setError("Escribe tu nombre.");
    if (!cuerpo.perfil) return setError("Elige un perfil.");
    setEnviando(true);
    setError("");
    try {
      await api.post("/api/auth/demo/registrar", cuerpo);
      await qc.invalidateQueries();
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setEnviando(false);
    }
  }

  const nombrePerfil = (id: string) => perfiles.data?.find((p) => p.id === id)?.nombre ?? id;
  const sinDemo = perfiles.isError;

  return (
    <div id="login">
      <div className="lg-box">
        <div className="lg-head">
          <div className="logo">OC</div>
          <div>
            <h1>Ops Central</h1>
            <p>Operación de Capa Media y Base de Datos en un solo lugar. Regístrate y elige tu perfil para entrar a la demo.</p>
          </div>
        </div>
        <div className="lg-wrap">
          <form className="lg-form" onSubmit={(e) => { e.preventDefault(); registrar(); }}>
            <h2>Registrar usuario</h2>
            <p className="muted small" style={{ margin: "6px 0 14px" }}>
              Demo sin contraseña: la autenticación corporativa todavía no está construida. Todo lo que ejecutes queda
              registrado en el log de ejecuciones con tu usuario.
            </p>
            <label className="lb" htmlFor="r-nombre">Nombre completo *</label>
            <input id="r-nombre" className="f" value={nombre} autoFocus maxLength={200} placeholder="María Pérez"
                   onChange={(e) => setNombre(e.target.value)} />
            <label className="lb mt" htmlFor="r-login">Usuario (opcional)</label>
            <input id="r-login" className="f" value={login} maxLength={60} placeholder="se genera desde el nombre"
                   onChange={(e) => setLogin(e.target.value.toLowerCase().trim())} />
            <label className="lb mt" htmlFor="r-perfil">Perfil *</label>
            <select id="r-perfil" className="f" value={perfil} onChange={(e) => setPerfil(e.target.value)}>
              <option value="">— elige tu perfil —</option>
              {perfiles.data?.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
            </select>
            {perfil && <p className="muted small" style={{ marginTop: 6 }}>{DESCRIPCION[perfil]}</p>}
            {sinDemo && <div className="warnbox mt">El registro de demo no está habilitado en este ambiente
              (proveedor de ingreso: {salud.data?.proveedor_ingreso ?? "…"}).</div>}
            {error && <div className="critbox mt">{error}</div>}
            <div className="btns mt">
              <button className="btn pri" type="submit" disabled={enviando || sinDemo}>Registrar e ingresar</button>
            </div>
            <div className="lg-foot">
              <span>Sesión: 30 min sin actividad · máximo 8 h</span>
              <span>Versión {salud.data?.version}</span>
            </div>
          </form>
          {previos.data && previos.data.length > 0 && (
            <div className="lg-demo">
              <h3>Usuarios registrados</h3>
              <p className="muted small">Entra de nuevo con el perfil que elegiste la última vez.</p>
              {previos.data.map((u) => (
                <button key={u.login} className="lg-u" disabled={enviando} onClick={() => registrar(u)}>
                  <span className="av">{iniciales(u.nombre)}</span>
                  <span className="grow">
                    <b>{u.nombre}</b> <span className="tag">{nombrePerfil(u.perfil)}</span>
                    <div className="muted small">{u.login}</div>
                  </span>
                  <span className="muted">→</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
