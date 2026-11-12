// Inventario con CRUD (feedback de la demo). Los cambios se guardan como ajustes sobre el archivo
// generado (config/inventario.json): regenerarlo no los pierde y cada uno queda auditado.
// Por ahora puede editar cualquier perfil que vea el inventario ([inventario] editores vacío).
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, mensajeError } from "./api";
import { Cabecera, CampoAmbiente, Cargando, Modal, Vacio, useEnAmbiente, useInventario, useSesion } from "./comun";
import { SelectBuscable } from "./SelectBuscable";

type Tipo = "servidor" | "dominio" | "base";
type Campo = { campo: string; etiqueta: string; tipo: string; obligatorio: boolean };
type Esquema = { campos: Record<Tipo, Campo[]>; opciones: Record<string, string[]>; editable: boolean };
const TABS: [Tipo, string, "servidores" | "dominios" | "bases"][] = [
  ["servidor", "Servidores", "servidores"], ["dominio", "Dominios", "dominios"], ["base", "Bases", "bases"]];
const COLUMNAS: Record<Tipo, [string, string][]> = {
  servidor: [["id", "Servidor"], ["ip", "IP"], ["ambiente", "Ambiente"], ["equipo", "Equipo"], ["zabbix_host", "Host Zabbix"], ["relacionados", "Relacionados"]],
  dominio: [["id", "Dominio"], ["servidores", "Servidores"], ["ambiente", "Ambiente"], ["relacionados", "Relacionados"]],
  base: [["id", "Base"], ["alias", "Alias"], ["servidor", "Servidor"], ["ambiente", "Ambiente"], ["criticidad", "Criticidad"], ["zabbix_host", "Host Zabbix"], ["dsn", "DSN"]],
};
const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const texto = (v: unknown) => Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v);

export function InventarioP() {
  const inv = useInventario();
  const qc = useQueryClient();
  const { avisar } = useSesion();
  const enAmb = useEnAmbiente();
  const esq = useQuery({ queryKey: ["inventario-esquema"], queryFn: () => api.get<Esquema>("/api/inventario/esquema"), staleTime: 300_000 });
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Tipo>((["servidor", "dominio", "base"].includes(params.get("tab") ?? "") ? params.get("tab") : "servidor") as Tipo);
  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => {          // el buscador del encabezado vuelve a esta pantalla con otro activo
    const t = params.get("tab"), b = params.get("q");
    if (t && ["servidor", "dominio", "base"].includes(t)) setTab(t as Tipo);
    if (b != null) setQ(b);
  }, [params]);
  const [form, setForm] = useState<{ tipo: Tipo; fila?: any } | null>(null);
  const editable = Boolean(inv.data && (inv.data as any).editable);
  const lista = TABS.find((t) => t[0] === tab)![2];

  const filas = useMemo(() => {
    const todas = ((inv.data?.[lista] ?? []) as any[]).filter((x) => enAmb(x.ambiente));
    if (!q) return todas;
    const nq = norm(q);
    return todas.filter((x) => norm(Object.values(x).map(texto).join(" ")).includes(nq));
  }, [inv.data, lista, q, enAmb]);
  const cuenta = (l: "servidores" | "dominios" | "bases") => ((inv.data?.[l] ?? []) as any[]).filter((x) => enAmb(x.ambiente)).length;

  const recargar = () => { qc.invalidateQueries({ queryKey: ["inventario"] }); };
  async function baja(f: any) {
    if (!window.confirm(`¿Dar de baja ${tab} ${f.id}? Deja de aparecer en Ops Central (se puede restaurar).`)) return;
    try { await api.del(`/api/inventario/${tab}/${encodeURIComponent(f.id)}`); avisar(`${f.id} dado de baja`); recargar(); }
    catch (x) { avisar(mensajeError(x)); }
  }
  async function restaurar(f: any) {
    const que = f._ajuste === "alta" ? "Se eliminará el alta manual." : "Vuelve a los datos del archivo generado.";
    if (!window.confirm(`¿Restaurar ${f.id}? ${que}`)) return;
    try { await api.post(`/api/inventario/${tab}/${encodeURIComponent(f.id)}/restaurar`); avisar(`${f.id} restaurado`); recargar(); }
    catch (x) { avisar(mensajeError(x)); }
  }
  async function vincular() {
    try { await api.post("/api/inventario/vincular-zabbix"); avisar("Vinculación con Zabbix en cola: se buscan las IPs de los servidores sin host de Zabbix"); }
    catch (x) { avisar(mensajeError(x)); }
  }

  return (
    <>
      <Cabecera titulo="Inventario" sub="Servidores, dominios WebLogic y bases, con sus relaciones. Los cambios hechos aquí se guardan como ajustes y quedan auditados.">
        {editable && <div className="btns">
          <button className="btn" onClick={vincular} title="Completa el host de Zabbix de los servidores buscando su IP en Zabbix">Vincular con Zabbix</button>
          <button className="btn pri" onClick={() => setForm({ tipo: tab })}>＋ Agregar {tab}</button>
        </div>}
      </Cabecera>
      <div className="card">
        <div className="fgrid" style={{ marginBottom: 12 }}>
          <CampoAmbiente id="inv-amb" />
          <label className="lb">Buscar
            <input className="f" value={q} placeholder="Nombre, IP, servidor, host de Zabbix…" onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        <div className="tabs">
          {TABS.map(([t, nombre, l]) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {nombre} {inv.data && <span className="muted">({cuenta(l)})</span>}
            </button>
          ))}
        </div>
        <Cargando q={inv} />
        {inv.data && (filas.length === 0 ? <Vacio>Sin registros{q ? ` para “${q}”` : ""}.</Vacio> : (
          <div className="tbl-wrap">
            <table>
              <thead><tr>{COLUMNAS[tab].map(([, e]) => <th key={e}>{e}</th>)}<th></th>{editable && <th></th>}</tr></thead>
              <tbody>
                {filas.slice(0, 500).map((f) => (
                  <tr key={f.id}>
                    {COLUMNAS[tab].map(([c]) => <td key={c} className={c === "id" ? "nw" : ""}>{c === "id" ? <b>{f.id}</b> : texto(f[c]) || <span className="muted">—</span>}</td>)}
                    <td>{f._ajuste && <span className={"tag " + (f._ajuste === "alta" ? "t-ok" : "t-acc")}>{f._ajuste === "alta" ? "agregado" : "editado"}</span>}</td>
                    {editable && (
                      <td className="nw">
                        <button className="btn sm ghost" onClick={() => setForm({ tipo: tab, fila: f })}>Editar</button>
                        {f._ajuste && <button className="btn sm ghost" onClick={() => restaurar(f)}>Restaurar</button>}
                        <button className="btn sm ghost" onClick={() => baja(f)} title="Dar de baja">✕</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {filas.length > 500 && <p className="muted small">Se muestran 500 de {filas.length}: usa la búsqueda para acotar.</p>}
          </div>
        ))}
      </div>
      {form && esq.data && <FormActivo esq={esq.data} tipo={form.tipo} fila={form.fila} cerrar={() => setForm(null)}
        alGuardar={(id) => { setForm(null); avisar(`${id} guardado`); recargar(); }} />}
    </>
  );
}

function FormActivo({ esq, tipo, fila, cerrar, alGuardar }: {
  esq: Esquema; tipo: Tipo; fila?: any; cerrar: () => void; alGuardar: (id: string) => void;
}) {
  const inv = useInventario();
  const alta = !fila;
  const [id, setId] = useState("");
  const [val, setVal] = useState<Record<string, string>>(() => {
    const v: Record<string, string> = {};
    esq.campos[tipo].forEach((c) => { v[c.campo] = texto(fila?.[c.campo]); });
    return v;
  });
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);
  const poner = (c: string, x: string) => setVal((p) => ({ ...p, [c]: x }));

  async function guardar() {
    setError(""); setGuardando(true);
    // en edición solo se envían los campos que cambiaron
    const datos: Record<string, string> = {};
    esq.campos[tipo].forEach((c) => { if (alta || val[c.campo] !== texto(fila?.[c.campo])) datos[c.campo] = val[c.campo]; });
    try {
      if (alta) { const r = await api.post<any>(`/api/inventario/${tipo}`, { id, datos }); alGuardar(r.id ?? id); }
      else { await api.put(`/api/inventario/${tipo}/${encodeURIComponent(fila.id)}`, { datos }); alGuardar(fila.id); }
    } catch (x) { setError(mensajeError(x)); } finally { setGuardando(false); }
  }

  function control(c: Campo) {
    const v = val[c.campo] ?? "";
    if (esq.opciones[c.tipo]) {
      return (
        <SelectBuscable value={v} onChange={(e) => poner(c.campo, e.target.value)}>
          <option value="">— elegir —</option>
          {esq.opciones[c.tipo].map((o) => <option key={o} value={o}>{o}</option>)}
        </SelectBuscable>
      );
    }
    if (c.tipo === "servidor") {
      return (
        <SelectBuscable value={v} onChange={(e) => poner(c.campo, e.target.value)}>
          <option value="">— sin servidor —</option>
          {(inv.data?.servidores ?? []).map((s) => <option key={s.id} value={s.id}>{s.id} · {s.ambiente}</option>)}
        </SelectBuscable>
      );
    }
    return <input className="f" type={c.tipo === "entero" ? "number" : "text"} value={v}
      placeholder={c.tipo === "lista_servidores" ? "SRV1, SRV2" : ""} onChange={(e) => poner(c.campo, e.target.value)} />;
  }

  return (
    <Modal titulo={alta ? `Agregar ${tipo}` : `Editar ${tipo} ${fila.id}`} cerrar={cerrar}
      pie={<><button className="btn" onClick={cerrar}>Cancelar</button>
        <button className="btn pri" disabled={guardando || (alta && !id.trim())} onClick={guardar}>{guardando ? "Guardando…" : "Guardar"}</button></>}>
      {!alta && fila._ajuste && <div className="info small">Este activo ya tiene ajustes. “Restaurar” en la lista lo devuelve a los datos del archivo generado.</div>}
      <div className="fgrid mt">
        {alta && (
          <label className="lb">Id *
            <input className="f" value={id} autoFocus onChange={(e) => setId(e.target.value)} placeholder={tipo === "base" ? "p. ej. CORQA" : ""} />
          </label>
        )}
        {esq.campos[tipo].map((c) => (
          <label key={c.campo} className="lb">{c.etiqueta}{c.obligatorio ? " *" : ""}{control(c)}</label>
        ))}
      </div>
      {error && <div className="critbox mt">{error}</div>}
      <p className="muted small mt">El id no se puede cambiar: para renombrar, da de baja y agrega uno nuevo. Todo cambio queda en la auditoría.</p>
    </Modal>
  );
}
