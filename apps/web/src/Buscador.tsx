// Buscador general del encabezado (como en demo-ops-central.html): pantallas, servidores, bases y dominios.
// Un activo abre el Inventario filtrado por él; una pantalla, esa pantalla.
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useInventario } from "./comun";

type Res = { k: string; tipo: string; sub: string; ir: string };
const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

export function Buscador({ pantallas }: { pantallas: { id: string; nombre: string }[] }) {
  const inv = useInventario();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [abierto, setAbierto] = useState(false);
  const [act, setAct] = useState(0);
  const ref = useRef<HTMLInputElement>(null);
  const puedeInv = pantallas.some((p) => p.id === "inventario");

  const res = useMemo<Res[]>(() => {
    const nq = norm(q.trim());
    if (!nq) return [];
    const out: Res[] = pantallas.filter((p) => norm(p.nombre).includes(nq))
      .map((p) => ({ k: p.nombre, tipo: "Pantalla", sub: "", ir: `/${p.id}` }));
    if (puedeInv && inv.data) {
      const grupos: [string, string, any[]][] = [["Base", "base", inv.data.bases], ["Servidor", "servidor", inv.data.servidores],
        ["Dominio", "dominio", inv.data.dominios]];
      for (const [tipo, t, xs] of grupos) {
        for (const x of xs) {
          const h = norm(`${x.id} ${x.alias ?? ""} ${x.ip ?? ""} ${x.zabbix_host ?? ""} ${x.servicio ?? ""}`);
          if (h.includes(nq)) out.push({ k: x.id, tipo, sub: [x.ambiente, x.servidor ?? x.ip].filter(Boolean).join(" · "),
            ir: `/inventario?tab=${t}&q=${encodeURIComponent(x.id)}` });
        }
      }
    }
    return out.slice(0, 12);
  }, [q, inv.data, pantallas, puedeInv]);

  function ir(r?: Res) {
    if (!r) return;
    nav(r.ir);
    setQ(""); setAbierto(false); ref.current?.blur();
  }

  return (
    <div className="search">
      <input ref={ref} value={q} placeholder="Buscar pantalla, servidor, base o dominio…" autoComplete="off"
        aria-label="Buscar en Ops Central" role="combobox" aria-expanded={abierto && res.length > 0}
        onChange={(e) => { setQ(e.target.value); setAbierto(true); setAct(0); }}
        onFocus={() => setAbierto(true)} onBlur={() => setTimeout(() => setAbierto(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setAct((a) => Math.min(a + 1, res.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setAct((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); ir(res[act]); }
          else if (e.key === "Escape") { setQ(""); setAbierto(false); }
        }} />
      {abierto && q.trim() && (
        <div className="sres" role="listbox">
          {res.length === 0 ? <div className="empty" style={{ padding: 12 }}>Sin resultados para “{q}”.</div> :
            res.map((r, i) => (
              <button key={r.tipo + r.k} role="option" aria-selected={i === act} style={i === act ? { background: "var(--sunk)" } : undefined}
                onMouseDown={(e) => { e.preventDefault(); ir(r); }} onMouseEnter={() => setAct(i)}>
                <span className="tag">{r.tipo}</span><b>{r.k}</b><span className="muted small">{r.sub}</span>
              </button>
            ))}
        </div>
      )}
    </div>
  );
}
