// ======================================================================== CAPA MEDIA
// Pantallas replicadas de capa-media-ops (web/app.js): las opciones (servidores, instalaciones,
// dominios) salen de TODO su inventario (servidores.json) más su configuración de módulos, vía
// /api/cm/opciones. Lo que no tiene configuración detallada se marca y, en la demo (cmops en
// simulación), se ejecuta con una configuración estándar. No piden caso ni motivo: ambos son opcionales.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import Accion from "./Accion";
import { Cabecera, Cargando, CampoAmbiente, Clase, OpcionesActivo, Vacio, useCatalogo, useEnAmbiente } from "./comun";

type OpcionesCM = {
  orquestacion: { host: string; ambiente: string; configurado: boolean;
    instalaciones: { id: string; usuario_so: string; dominios: { id: string; nombre: string }[] }[] }[];
  usuarios_wl: { dominio: string; nombre: string; host: string; ambiente: string; configurado: boolean }[];
  frmweb: { host: string; ambiente: string; configurado: boolean; descripcion: string; managed_servers: string[] }[];
  fuente: string;
  ruta_cmops?: string;
  cmops_disponible?: boolean;
  aviso?: string;
};

/** Si capa-media-ops no está donde lo busca la API, se dice dónde lo buscó (las opciones salen del inventario). */
function AvisoCM({ o }: { o?: OpcionesCM }) {
  return o?.aviso ? <div className="warnbox">{o.aviso}</div> : null;
}

function useOpcionesCM() {
  // Un servidor que no está en el inventario de Ops Central queda como "SIN AMBIENTE" (se puede filtrar igual).
  const amb = (a: string) => a || "SIN AMBIENTE";
  return useQuery({
    queryKey: ["cm-opciones"], queryFn: () => api.get<OpcionesCM>("/api/cm/opciones"), staleTime: 60_000,
    select: (o): OpcionesCM => ({
      ...o,
      orquestacion: o.orquestacion.map((h) => ({ ...h, ambiente: amb(h.ambiente) })),
      usuarios_wl: o.usuarios_wl.map((d) => ({ ...d, ambiente: amb(d.ambiente) })),
      frmweb: o.frmweb.map((h) => ({ ...h, ambiente: amb(h.ambiente) })),
    }),
  });
}

/** Pestañas de acciones + formulario de la acción elegida (con los valores que fija la pantalla). */
function Acciones({ acciones, iniciales, listo, aviso }: {
  acciones: string[]; iniciales: (accion: string) => Record<string, unknown>; listo: (accion: string) => boolean; aviso: string;
}) {
  const cat = useCatalogo();
  const disponibles = acciones.filter((a) => cat.data?.some((c) => c.id === a));
  const [sel, setSel] = useState("");
  const actual = sel && disponibles.includes(sel) ? sel : disponibles[0];
  const acc = cat.data?.find((x) => x.id === actual);
  const ini = actual ? iniciales(actual) : {};
  return (
    <div className="card">
      <Cargando q={cat} />
      <div className="tabs">
        {disponibles.map((a) => {
          const c = cat.data!.find((x) => x.id === a)!;
          return <button key={a} className={actual === a ? "on" : ""} onClick={() => setSel(a)}>{c.nombre.split("(")[0]}</button>;
        })}
      </div>
      {actual && listo(actual) ? (
        <Accion key={actual + JSON.stringify(ini)} accion={actual} compacto iniciales={ini} ocultos={["host", "hosts", "dominios"]} />
      ) : <Vacio>{aviso}</Vacio>}
      {acc && <div className="fila mt small muted"><Clase c={acc.clase} /> <span className="mono-sm">{actual}</span></div>}
    </div>
  );
}

/** Aviso para lo que está en el inventario pero no en configuracion_modulos.json de capa-media-ops. */
function SinDetalle({ que }: { que: string }) {
  return (
    <p className="muted small" style={{ marginBottom: 0 }}>
      ⓘ {que} está en el inventario pero no tiene configuración detallada en capa-media-ops: en la demo (simulación) se
      ejecuta con una estándar. Para operarlo en real, primero “Recolectar” y generar su configuración.
    </p>
  );
}

function SinConfiguracion({ modulo, fuente }: { modulo: string; fuente?: string }) {
  return (
    <div className="card"><Vacio>No hay servidores para {modulo}: ni capa-media-ops ni el inventario de Ops Central tienen servidores de Capa
      Media. Fuente: <span className="mono-sm">{fuente ?? "configuracion_modulos.json"}</span>.</Vacio></div>
  );
}

// ------------------------------------------------------------------ Orquestación WebLogic
export function Orquestacion() {
  const op = useOpcionesCM();
  const enAmb = useEnAmbiente();
  const [host, setHost] = useState("");
  const [inst, setInst] = useState("");
  const [doms, setDoms] = useState<string[]>([]);
  const hosts = op.data?.orquestacion ?? [];
  const H = hosts.find((h) => h.host === host);
  // Por defecto, la primera instalación que tenga dominios (igual que capa-media-ops).
  const N = H && (H.instalaciones.find((i) => i.id === inst) ?? H.instalaciones.find((i) => i.dominios.length) ?? H.instalaciones[0]);
  const elegirHost = (h: string) => { setHost(h); setInst(""); setDoms([]); };
  const alternar = (d: string) => setDoms((p) => (p.includes(d) ? p.filter((x) => x !== d) : [...p, d]));

  return (
    <>
      <Cabecera titulo="Orquestación WebLogic"
        sub="Bajada y subida ordenada de dominios: Managed Servers vía Node Manager y AdminServer. Cada instalación se ejecuta con su propio usuario de SO." />
      <Cargando q={op} />
      <AvisoCM o={op.data} />
      {op.data && hosts.length === 0 && <SinConfiguracion modulo="orquestación" fuente={op.data.fuente} />}
      {hosts.length > 0 && (
        <div className="card">
          <div className="fgrid">
            <CampoAmbiente id="orq-amb" items={hosts} alCambiar={() => elegirHost("")} />
            <label className="lb">Servidor
              <select className="f" value={host} onChange={(e) => elegirHost(e.target.value)}>
                <option value="">— elegir —</option>
                <OpcionesActivo items={hosts.map((h) => ({ id: h.host, ambiente: h.ambiente }))} />
              </select>
            </label>
            {H && H.instalaciones.length > 1 && (
              <label className="lb">Instalación
                <select className="f" value={N?.id ?? ""} onChange={(e) => { setInst(e.target.value); setDoms([]); }}>
                  {H.instalaciones.map((i) => <option key={i.id} value={i.id}>{i.id} · usuario {i.usuario_so || "—"} · {i.dominios.length} dominio(s)</option>)}
                </select>
              </label>
            )}
          </div>
          {N && (
            <div className="mt">
              <div className="lb">Dominios {doms.length ? `(${doms.length} elegidos)` : "(ninguno marcado = todos los de la instalación)"}</div>
              {N.dominios.length === 0 ? <div className="muted small">Este servidor no tiene dominios en el inventario.</div> : (
                <div className="fila" style={{ flexWrap: "wrap", gap: 8 }}>
                  {N.dominios.map((d) => (
                    <label key={d.id} className="tag" title={d.id !== d.nombre ? `En cmops: ${d.id}` : undefined}
                           style={{ cursor: "pointer", display: "inline-flex", gap: 6, alignItems: "center" }}>
                      <input type="checkbox" checked={doms.includes(d.id)} onChange={() => alternar(d.id)} /> {d.nombre}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          {H && !H.configurado && <SinDetalle que={`El servidor ${H.host}`} />}
        </div>
      )}
      {hosts.length > 0 && (
        <Acciones
          acciones={["orq.status", "orq.verificar", "orq.inventory", "orq.stop", "orq.start", "orq.credenciales", "rec.recolectar", "sys.conectividad"]}
          iniciales={(a) => {
            if (a === "sys.conectividad" || a === "rec.recolectar") return { host };
            if (a === "orq.credenciales") return { host, instalacion: N?.id, dominio: doms[0] ?? "" };
            return { host, instalacion: N?.id, ...(doms.length ? { dominios: doms.join(",") } : {}) };
          }}
          listo={(a) => Boolean(H && enAmb(H.ambiente)) && (a !== "orq.credenciales" || doms.length === 1)}
          aviso={!H ? "Elige el servidor para continuar." : "Registrar credenciales se hace de a un dominio: marca exactamente uno."} />
      )}
    </>
  );
}

// ------------------------------------------------------------------ Usuarios WebLogic
type Fila = { accion: string; usuario: string; grupo: string };
const ACCIONES_USR = ["ELIMINAR", "ADMIN", "GRUPO"]; // CREAR necesita contraseña: no viaja en parámetros

export function UsuariosWL() {
  const op = useOpcionesCM();
  const enAmb = useEnAmbiente();
  const [dom, setDom] = useState("");
  const [filas, setFilas] = useState<Fila[]>([{ accion: "ADMIN", usuario: "", grupo: "" }]);
  const doms = op.data?.usuarios_wl ?? [];
  // Un mismo nombre de dominio puede estar en varios servidores (p. ej. PROD y DRP): se elige el par dominio + servidor.
  const clave = (d: { dominio: string; host: string }) => `${d.dominio}@${d.host}`;
  const D = doms.find((d) => clave(d) === dom);
  const validas = filas.filter((f) => f.usuario.trim()).map((f) => ({
    accion: f.accion, usuario: f.usuario.trim(), ...(f.grupo.trim() ? { grupo: f.grupo.trim() } : {}),
  }));
  const poner = (i: number, k: keyof Fila, v: string) => setFilas((p) => p.map((f, j) => (j === i ? { ...f, [k]: v } : f)));

  return (
    <>
      <Cabecera titulo="Usuarios WebLogic" sub="Listar, preparar el dominio y aplicar eliminaciones, administradores y grupos en el realm." />
      <Cargando q={op} />
      <AvisoCM o={op.data} />
      {op.data && doms.length === 0 && <SinConfiguracion modulo="usuarios WebLogic" fuente={op.data.fuente} />}
      {doms.length > 0 && (
        <div className="card">
          <div className="fgrid">
            <CampoAmbiente id="usr-amb" items={doms.map((d) => ({ ambiente: d.ambiente }))} alCambiar={() => setDom("")} />
            <label className="lb">Dominio WebLogic
              <select className="f" value={dom} onChange={(e) => setDom(e.target.value)}>
                <option value="">— elegir —</option>
                <OpcionesActivo items={doms.map((d) => ({ id: clave(d), ambiente: d.ambiente, etiqueta: `${d.nombre} · ${d.host}` }))} />
              </select>
            </label>
            {D && <div><div className="lb">Servidor</div><div className="mono-sm" style={{ paddingTop: 8 }}>{D.host}</div></div>}
          </div>
          {D && !D.configurado && <SinDetalle que={`El dominio ${D.nombre}`} />}
        </div>
      )}
      {doms.length > 0 && (
        <Acciones acciones={["usr.listar", "usr.preparar", "usr.aplicar"]}
          iniciales={(a) => ({ host: D?.host ?? "", dominio: D?.dominio ?? "", ...(a === "usr.aplicar" ? { acciones: validas } : {}) })}
          listo={(a) => Boolean(D && enAmb(D.ambiente)) && (a !== "usr.aplicar" || validas.length > 0)}
          aviso={!D ? "Elige el dominio para continuar." : "Agrega al menos una fila con usuario en la tabla de acciones."} />
      )}
      {D && (
        <div className="card">
          <h3>Acciones de usuarios <span className="sub">para “Crear / eliminar / admin / grupo”</span></h3>
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>Acción</th><th>Usuario</th><th>Grupo</th><th /></tr></thead>
              <tbody>
                {filas.map((f, i) => (
                  <tr key={i}>
                    <td><select className="f" value={f.accion} onChange={(e) => poner(i, "accion", e.target.value)}>
                      {ACCIONES_USR.map((x) => <option key={x}>{x}</option>)}</select></td>
                    <td><input className="f" value={f.usuario} maxLength={60} onChange={(e) => poner(i, "usuario", e.target.value)} /></td>
                    <td><input className="f" value={f.grupo} maxLength={60} disabled={f.accion !== "GRUPO"}
                               placeholder={f.accion === "ADMIN" ? "Administrators" : ""} onChange={(e) => poner(i, "grupo", e.target.value)} /></td>
                    <td><button className="btn sm" onClick={() => setFilas((p) => p.filter((_, j) => j !== i))} disabled={filas.length === 1}>Quitar</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="fila mt" style={{ gap: 10, flexWrap: "wrap" }}>
            <button className="btn sm" onClick={() => setFilas((p) => [...p, { accion: "ADMIN", usuario: "", grupo: "" }])}>+ Agregar fila</button>
            <span className="muted small">No se puede eliminar “weblogic”. CREAR no está disponible aquí porque exige contraseña, y Ops Central
              no guarda contraseñas en los parámetros de una ejecución.</span>
          </div>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Procesos frmweb
export function Frmweb() {
  const op = useOpcionesCM();
  const enAmb = useEnAmbiente();
  const [host, setHost] = useState("");
  const [lote, setLote] = useState("20");
  const hosts = op.data?.frmweb ?? [];
  const H = hosts.find((h) => h.host === host);
  return (
    <>
      <Cabecera titulo="Procesos frmweb" sub="Detectar procesos huérfanos (sin sesión) y mitigarlos por lotes." />
      <Cargando q={op} />
      <AvisoCM o={op.data} />
      {op.data && hosts.length === 0 && <SinConfiguracion modulo="frmweb" fuente={op.data.fuente} />}
      {hosts.length > 0 && (
        <div className="card">
          <div className="fgrid">
            <CampoAmbiente id="frm-amb" items={hosts} alCambiar={() => setHost("")} />
            <label className="lb">Servidor
              <select className="f" value={host} onChange={(e) => setHost(e.target.value)}>
                <option value="">— elegir —</option>
                <OpcionesActivo items={hosts.map((h) => ({ id: h.host, ambiente: h.ambiente }))} />
              </select>
            </label>
            <label className="lb">Tamaño de lote (mitigar)
              <select className="f" value={lote} onChange={(e) => setLote(e.target.value)}>
                <option value="10">10 (prudente)</option><option value="20">20 (por defecto)</option><option value="40">40</option>
              </select>
            </label>
          </div>
          {H && H.configurado && <p className="muted small" style={{ marginBottom: 0 }}>{H.descripcion} · Managed servers: {H.managed_servers.join(", ") || "—"}</p>}
          {H && !H.configurado && <SinDetalle que={`El servidor ${H.host}`} />}
        </div>
      )}
      {hosts.length > 0 && (
        <Acciones acciones={["frm.validar", "frm.mitigar"]}
          iniciales={(a) => ({ host, ...(a === "frm.mitigar" ? { lote: Number(lote) } : {}) })}
          listo={() => Boolean(H && enAmb(H.ambiente))} aviso="Elige el servidor para continuar." />
      )}
    </>
  );
}
