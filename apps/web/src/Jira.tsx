// Flujo de Jira integrado (feedback de la demo): adjuntar la evidencia del snapshot al caso, comentar el
// resultado de una ejecución y mover el caso de estado, sin salir de Ops Central.
// Si Jira no está configurado la API responde 503 y se muestra el motivo.
import { useState } from "react";
import { api, mensajeError } from "./api";
import { useSesion } from "./comun";
import { SelectBuscable } from "./SelectBuscable";

const RE_JIRA = /^[A-Z][A-Z0-9_]+-\d+$/;
export const pareceJira = (s?: string | null) => Boolean(s && RE_JIRA.test(s.toUpperCase()));

function Transiciones({ clave }: { clave: string }) {
  const { avisar } = useSesion();
  const [ts, setTs] = useState<{ id: string; nombre: string; a: string }[] | null>(null);
  const [sel, setSel] = useState("");
  async function cargar() {
    try { setTs(await api.get(`/api/casos/${clave}/transiciones`)); } catch (x) { avisar(mensajeError(x)); }
  }
  async function mover() {
    try {
      await api.post(`/api/casos/${clave}/transicion`, { id: sel });
      avisar(`${clave} pasó a “${ts?.find((t) => t.id === sel)?.a ?? sel}”`);
      setTs(null); setSel("");
    } catch (x) { avisar(mensajeError(x)); }
  }
  if (!ts) return <button className="btn sm ghost" onClick={cargar}>Cambiar estado del caso…</button>;
  return (
    <div className="fila" style={{ marginTop: 6 }}>
      <div style={{ minWidth: 200, flex: 1 }}>
        <SelectBuscable value={sel} onChange={(e) => setSel(e.target.value)} placeholder="Transición">
          <option value="">— elegir transición —</option>
          {ts.map((t) => <option key={t.id} value={t.id}>{t.nombre} → {t.a}</option>)}
        </SelectBuscable>
      </div>
      <button className="btn sm" disabled={!sel} onClick={mover}>Aplicar</button>
    </div>
  );
}

/** Descargar el ZIP de evidencia del grupo de snapshot y, opcionalmente, adjuntarlo al caso de Jira. */
export function EvidenciaSnapshot({ base, grupo, soloLectura }: { base: string; grupo: string; soloLectura?: boolean }) {
  const { avisar } = useSesion();
  const [clave, setClave] = useState(pareceJira(grupo) ? grupo.toUpperCase() : "");
  const [enviando, setEnviando] = useState(false);
  const [hecho, setHecho] = useState<string | null>(null);
  const url = `/api/estado/bd/${encodeURIComponent(base)}/snapshot/${encodeURIComponent(grupo)}/evidencia`;
  async function adjuntar() {
    setEnviando(true);
    try {
      const r = await api.post<{ clave: string; adjunto: string }>(`/api/casos/${clave}/evidencia-snapshot`, { base, grupo });
      setHecho(`Adjuntado ${r.adjunto} y comentado en ${r.clave}`);
    } catch (x) { avisar(mensajeError(x)); } finally { setEnviando(false); }
  }
  return (
    <div className="card mt">
      <h3>Evidencia <span className="sub">ZIP con HTML legible, snapshots, comparación, ejecuciones y SHA-256</span></h3>
      <div className="fila">
        <a className="btn pri" href={url} download>⬇ Descargar evidencia</a>
        {!soloLectura && <>
          <input className="f" style={{ maxWidth: 170 }} value={clave} placeholder="Caso Jira (OPS-123)"
            onChange={(e) => setClave(e.target.value.toUpperCase().trim())} aria-label="Clave del caso de Jira" />
          <button className="btn" disabled={!pareceJira(clave) || enviando} onClick={adjuntar}>
            {enviando ? "Enviando…" : "Adjuntar a Jira"}
          </button>
        </>}
      </div>
      {hecho && <div className="info mt small">{hecho}</div>}
      {!soloLectura && pareceJira(clave) && <Transiciones clave={clave} />}
    </div>
  );
}

/** Comentar en el caso de Jira el resultado de una ejecución. */
export function ComentarEnJira({ ejecucionId, claveInicial }: { ejecucionId: string; claveInicial?: string | null }) {
  const { avisar } = useSesion();
  const [clave, setClave] = useState(pareceJira(claveInicial) ? claveInicial!.toUpperCase() : "");
  const [ok, setOk] = useState(false);
  async function comentar() {
    try { await api.post(`/api/casos/${clave}/comentar`, { ejecucion_id: ejecucionId }); setOk(true); avisar(`Comentado en ${clave}`); }
    catch (x) { avisar(mensajeError(x)); }
  }
  return (
    <div className="fila mt">
      <input className="f" style={{ maxWidth: 170 }} value={clave} placeholder="Caso Jira (OPS-123)"
        onChange={(e) => { setClave(e.target.value.toUpperCase().trim()); setOk(false); }} aria-label="Clave del caso de Jira" />
      <button className="btn sm" disabled={!pareceJira(clave) || ok} onClick={comentar}>{ok ? "Comentado ✓" : "Comentar resultado en Jira"}</button>
      {pareceJira(clave) && <Transiciones clave={clave} />}
    </div>
  );
}
