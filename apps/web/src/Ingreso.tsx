import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";

type Salud = { ok: boolean; simulacion: boolean; proveedor_ingreso: string; version: string };

// Usuarios de prueba (solo con auth.proveedor = "dev"; en QA/PROD se entra con SSO corporativo).
const DEMO = [
  ["dba.demo", "María DBA", "GG_OPS_DBA", "Snapshot, bloqueos, salud y tareas DBA"],
  ["dba2.demo", "Diego DBA 2", "GG_OPS_DBA", "Segundo DBA, para aprobar"],
  ["cm.demo", "Carlos Capa Media", "GG_OPS_CM", "Orquestación WebLogic, usuarios WL y frmweb"],
  ["cm2.demo", "Clara Capa Media 2", "GG_OPS_CM", "Segunda persona de Capa Media, para aprobar"],
  ["lider.demo", "Laura Líder", "GG_OPS_LIDER", "Aprueba ambos equipos y ve todo"],
  ["consulta.demo", "Camilo Consulta", "GG_OPS_CONSULTA", "Mesa de servicio: solo lectura"],
];

function iniciales(n: string) {
  return n.split(" ").slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}

export default function Ingreso() {
  const qc = useQueryClient();
  const salud = useQuery({ queryKey: ["salud"], queryFn: () => api.get<Salud>("/api/salud") });
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function entrarDev(login: string) {
    setEnviando(true);
    setError("");
    try {
      await api.post("/api/auth/dev/ingresar", { login });
      await qc.invalidateQueries();
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setEnviando(false);
    }
  }

  const prov = salud.data?.proveedor_ingreso;
  return (
    <div id="login">
      <div className="lg-box">
        <div className="lg-head">
          <div className="logo">OC</div>
          <div>
            <h1>Ops Central</h1>
            <p>Operación de Capa Media y Base de Datos en un solo lugar. Ingresa con tu cuenta corporativa: tu perfil
              lo determinan tus grupos de directorio.</p>
          </div>
        </div>
        <div className="lg-wrap">
          <div className="lg-form">
            <h2>Ingresar</h2>
            <p className="muted small" style={{ margin: "6px 0 14px" }}>
              Se usa el inicio de sesión único de la organización, con segundo factor. Ops Central no guarda contraseñas.
            </p>
            <a className="btn pri" href="/api/auth/oidc/ingresar" aria-disabled={prov !== "oidc"}
               style={prov !== "oidc" ? { pointerEvents: "none", opacity: 0.45 } : undefined}>
              Ingresar con la cuenta corporativa
            </a>
            {prov !== "oidc" && (
              <p className="muted small" style={{ marginTop: 10 }}>
                El SSO todavía no está configurado en este ambiente (proveedor: {prov ?? "…"}).
              </p>
            )}
            {error && <div className="critbox mt">{error}</div>}
            <div className="lg-foot">
              <span>Sesión: 30 min sin actividad · máximo 8 h</span>
              <span>Versión {salud.data?.version}</span>
            </div>
          </div>
          {prov === "dev" && (
            <div className="lg-demo">
              <h3>Usuarios de desarrollo</h3>
              <p className="muted small">Solo existen en modo desarrollo. Cada uno entra con el grupo de AD indicado.</p>
              {DEMO.map(([login, nombre, grupo, desc]) => (
                <button key={login} className="lg-u" disabled={enviando} onClick={() => entrarDev(login)}>
                  <span className="av">{iniciales(nombre)}</span>
                  <span className="grow">
                    <b>{nombre}</b> <span className="tag">{grupo}</span>
                    <div className="muted small">{desc}</div>
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
