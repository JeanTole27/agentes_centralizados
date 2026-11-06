import { useQuery } from "@tanstack/react-query";
import { api, ErrorApi } from "./api";
import type { Yo } from "./tipos";
import Ingreso from "./Ingreso";
import Shell from "./Shell";

export default function App() {
  const yo = useQuery({ queryKey: ["yo"], queryFn: () => api.get<Yo>("/api/auth/yo"), refetchInterval: 120_000 });
  if (yo.isLoading) return <div className="carga">Cargando…</div>;
  if (yo.error instanceof ErrorApi && yo.error.estado === 401) return <Ingreso />;
  if (yo.error) return <div className="carga">No se pudo conectar con Ops Central: {String(yo.error)}</div>;
  return <Shell yo={yo.data!} />;
}
