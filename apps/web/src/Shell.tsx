import { useCallback, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { Sesion } from "./comun";
import type { Yo } from "./tipos";
import * as P from "./pantallas";

const ICONOS: Record<string, string> = {
  inicio: "⌂", ventana: "◷", incidentes: "!", inventario: "▤", alertas: "⚑", prevencion: "↗",
  orquestacion: "⇅", usuarioswl: "☺", frmweb: "⚙", snapshot: "◫", bloqueos: "⛓", salud: "♥", tareas: "☰",
  reportes: "▦", aprobaciones: "✓", ejecuciones: "▶", admin: "⚿",
};

const PANTALLAS: Record<string, () => JSX.Element> = {
  inicio: P.Inicio, inventario: P.InventarioP, alertas: P.Alertas, prevencion: P.Prevencion,
  orquestacion: P.Orquestacion, usuarioswl: P.UsuariosWL, frmweb: P.Frmweb, snapshot: P.Snapshot,
  bloqueos: P.Bloqueos, salud: P.Salud, tareas: P.Tareas, aprobaciones: P.Aprobaciones,
  ejecuciones: P.Ejecuciones, admin: P.Admin, ventana: P.Ventana, incidentes: P.Incidentes, reportes: P.Reportes,
};

const PERFIL: Record<string, string> = { dba: "DBA", cm: "Capa Media", lid: "Líder", con: "Consulta", adm: "Administrador",
  aut: "Automatizador" };

export default function Shell({ yo }: { yo: Yo }) {
  const qc = useQueryClient();
  const loc = useLocation();
  const [toasts, setToasts] = useState<{ id: number; t: string }[]>([]);
  const [abierto, setAbierto] = useState(false);
  const inicio = useQuery({ queryKey: ["inicio"], queryFn: () => api.get<any>("/api/inicio"), refetchInterval: 30_000 });

  const avisar = useCallback((t: string) => {
    const id = Date.now() + Math.random();
    setToasts((p) => [...p, { id, t }]);
    setTimeout(() => setToasts((p) => p.filter((x) => x.id !== id)), 4500);
  }, []);

  const permitidas = useMemo(() => {
    const m = new Map<string, boolean>();
    yo.menu.forEach((g) => g.pantallas.forEach((p) => m.set(p.id, p.solo_lectura)));
    return m;
  }, [yo]);
  const soloLectura = useCallback((id: string) => permitidas.get(id) ?? true, [permitidas]);

  async function salir() {
    await api.post("/api/auth/salir").catch(() => undefined);
    qc.clear();
    window.location.href = "/";
  }

  const pendientes = inicio.data?.kpis?.pendientes_aprobacion ?? 0;
  const actual = loc.pathname.split("/")[1] || "inicio";
  const nombreActual = yo.menu.flatMap((g) => g.pantallas).find((p) => p.id === actual)?.nombre ?? "";
  const iniciales = yo.nombre.split(" ").slice(0, 2).map((x) => x[0]).join("").toUpperCase();

  return (
    <Sesion.Provider value={{ yo, avisar, soloLectura }}>
      <div id="app">
        <aside className={"side" + (abierto ? " open" : "")} onClick={() => setAbierto(false)}>
          <div className="brand">
            <div className="logo">OC</div>
            <div><b>Ops Central</b><small>Capa Media · Base de Datos</small></div>
          </div>
          {yo.menu.map((g) => (
            <div key={g.grupo}>
              <div className="grp">{g.grupo}</div>
              {g.pantallas.map((p) => (
                <NavLink key={p.id} to={`/${p.id}`} className={({ isActive }) => "nav-it" + (isActive ? " on" : "")}>
                  <span className="ic" aria-hidden>{ICONOS[p.id] ?? "•"}</span>
                  {p.nombre}
                  {p.id === "aprobaciones" && pendientes > 0 ? <span className="cnt">{pendientes}</span>
                    : p.solo_lectura && <span className="ro">solo ver</span>}
                </NavLink>
              ))}
            </div>
          ))}
          <div className="side-foot">Perfil: {yo.perfiles.map((p) => PERFIL[p] ?? p).join(" + ")}</div>
        </aside>
        <div className="main">
          <header className="top">
            <button className="menu-btn" aria-label="Menú" onClick={() => setAbierto((x) => !x)}>☰</button>
            <b>{nombreActual}</b>
            <span className="sp" />
            {yo.mfa_reciente && <span className="tag t-ok" title="Segundo factor confirmado recientemente">MFA</span>}
            <div className="user">
              <span className="av">{iniciales}</span>
              <span className="nm">{yo.nombre}<br /><small>{yo.login} · {yo.perfiles.map((p) => PERFIL[p] ?? p).join(", ")}</small></span>
            </div>
            <button className="btn ghost sm" onClick={salir}>Salir</button>
          </header>
          <main className="content">
            <Routes>
              <Route path="/" element={<Navigate to="/inicio" replace />} />
              {[...permitidas.keys()].map((id) => {
                const C = PANTALLAS[id];
                return C ? <Route key={id} path={`/${id}/*`} element={<C />} /> : null;
              })}
              <Route path="*" element={<div className="empty">Esta pantalla no existe o tu perfil no tiene acceso.</div>} />
            </Routes>
          </main>
        </div>
        <div className="toasts">{toasts.map((t) => <div key={t.id} className="toast">{t.t}</div>)}</div>
      </div>
    </Sesion.Provider>
  );
}
