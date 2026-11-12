// Ventana de chat del asistente operativo (demo-ops-central.html, panel lateral "Asistente").
// El motor todavía no está conectado: la API responde estado "no_conectado" y registra la pregunta.
// Cuando se conecte, esta ventana ya muestra fuentes, herramientas usadas y acciones sugeridas.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, mensajeError } from "./api";

type AccionSug = { etiqueta: string; tipo: "ir" | "accion"; destino: string };
type RespuestaAsistente = {
  id: string; estado: "no_conectado" | "ok" | "error"; texto: string;
  fuentes: { documento: string; seccion?: string; version?: string }[]; herramientas: string[]; acciones: AccionSug[];
};
type EstadoAsistente = { conectado: boolean; motor: string | null; max_caracteres: number; sugerencias: string[]; mensaje: string | null };
type Msg = { quien: "u" | "a"; texto: string; r?: RespuestaAsistente; error?: boolean };

export function Chat({ abierto, cerrar, pantalla, nombrePantalla, perfil, nombre, permitidas }: {
  abierto: boolean; cerrar: () => void; pantalla: string; nombrePantalla: string; perfil: string; nombre: string;
  permitidas: Set<string>;
}) {
  const nav = useNavigate();
  const est = useQuery({ queryKey: ["asistente-estado"], queryFn: () => api.get<EstadoAsistente>("/api/asistente/estado"),
    staleTime: 300_000 });
  const saludo = (): Msg => ({ quien: "a", texto: `Hola, ${nombre.split(" ")[0]}. Respondo con los runbooks y el historial de incidentes y cambios de Capa Media y Base de Datos, y consulto datos con herramientas de solo lectura. Si sugiero una acción, te llevo a su formulario: nunca ejecuto cambios.` });
  const [msgs, setMsgs] = useState<Msg[]>(() => [saludo()]);
  const [texto, setTexto] = useState("");
  const [valorados, setValorados] = useState<Record<string, string>>({});
  const cuerpo = useRef<HTMLDivElement>(null);
  const entrada = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (abierto) setTimeout(() => entrada.current?.focus(), 200); }, [abierto]);
  useEffect(() => { cuerpo.current?.scrollTo({ top: cuerpo.current.scrollHeight }); }, [msgs]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && abierto) cerrar(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [abierto, cerrar]);

  const preguntar = useMutation({
    mutationFn: (pregunta: string) => api.post<RespuestaAsistente>("/api/asistente/preguntar", { pregunta, contexto: { pantalla } }),
    onSuccess: (r) => setMsgs((m) => [...m, { quien: "a", texto: r.texto, r }]),
    onError: (e) => setMsgs((m) => [...m, { quien: "a", texto: mensajeError(e), error: true }]),
  });

  function enviar(q?: string) {
    const p = (q ?? texto).trim();
    if (!p || preguntar.isPending) return;
    setTexto("");
    setMsgs((m) => [...m, { quien: "u", texto: p }]);
    preguntar.mutate(p);
  }

  function valorar(id: string, util: boolean) {
    setValorados((v) => ({ ...v, [id]: util ? "Gracias, quedó registrado." : "Registrado: se revisará el runbook." }));
    api.post("/api/asistente/valorar", { respuesta_id: id, util }).catch(() => undefined);
  }

  function actuar(a: AccionSug) {
    if (a.tipo === "ir" && permitidas.has(a.destino)) {
      nav(`/${a.destino}`);
      if (window.innerWidth < 1200) cerrar();
    }
  }

  const max = est.data?.max_caracteres ?? 2000;
  return (
    <aside className={"chat" + (abierto ? " open" : "")} aria-label="Asistente" aria-hidden={!abierto}>
      <div className="ch-head">
        <div className="logo" style={{ width: 30, height: 30 }}>✦</div>
        <div style={{ flex: 1 }}>
          <b>Asistente operativo</b>
          <small>Runbooks · incidentes y cambios · solo lectura</small>
        </div>
        <button className="btn ghost sm" title="Nueva conversación" onClick={() => { setMsgs([saludo()]); setValorados({}); }}>↺</button>
        <button className="btn ghost sm" aria-label="Cerrar" onClick={cerrar}>✕</button>
      </div>
      <div className="ch-ctx">
        Contexto: <span className="tag t-acc">{perfil}</span>
        {nombrePantalla && <span className="tag">pantalla: {nombrePantalla}</span>}
        {est.data && !est.data.conectado && <span className="tag t-warn" title={est.data.mensaje ?? ""}>sin conectar</span>}
      </div>
      <div className="ch-body" ref={cuerpo}>
        {est.data && !est.data.conectado && (
          <div className="warnbox small">{est.data.mensaje}</div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.quien}`} style={m.error ? { borderColor: "var(--crit)" } : undefined}>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.texto}</div>
            {m.r && m.r.herramientas.length > 0 && (
              <div style={{ marginTop: 8 }}>{m.r.herramientas.map((h) => <span key={h} className="tool">⚙ {h}</span>)}</div>
            )}
            {m.r && m.r.fuentes.length > 0 && (
              <div className="src">{m.r.fuentes.map((f, j) => (
                <div key={j}>📄 <code>{f.documento}</code>{f.seccion && ` · ${f.seccion}`}{f.version && <> · <code>{f.version}</code></>}</div>
              ))}</div>
            )}
            {m.r && m.r.acciones.filter((a) => a.tipo !== "ir" || permitidas.has(a.destino)).length > 0 && (
              <div className="btns" style={{ marginTop: 8 }}>
                {m.r.acciones.filter((a) => a.tipo !== "ir" || permitidas.has(a.destino)).map((a) => (
                  <button key={a.destino} className="btn sm" onClick={() => actuar(a)}>{a.etiqueta}</button>
                ))}
              </div>
            )}
            {m.r && m.r.estado === "ok" && (
              <div className="fb">
                {valorados[m.r.id] ? <span className="small muted">{valorados[m.r.id]}</span> : <>
                  <button onClick={() => valorar(m.r!.id, true)}>👍 Útil</button>
                  <button onClick={() => valorar(m.r!.id, false)}>👎 No sirvió</button>
                </>}
              </div>
            )}
          </div>
        ))}
        {preguntar.isPending && (
          <div className="msg a thinking"><span>Buscando en runbooks e incidentes…</span></div>
        )}
      </div>
      {(est.data?.sugerencias.length ?? 0) > 0 && msgs.length <= 1 && (
        <div className="ch-sug">
          {est.data!.sugerencias.map((s) => <button key={s} onClick={() => enviar(s)}>{s}</button>)}
        </div>
      )}
      <div className="ch-in">
        <textarea ref={entrada} value={texto} maxLength={max} aria-label="Pregunta para el asistente"
          placeholder="Pregunta por un error ORA-, un dominio, un cambio…"
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar(); } }} />
        <button className="btn pri" disabled={!texto.trim() || preguntar.isPending} onClick={() => enviar()}>Enviar</button>
      </div>
    </aside>
  );
}
