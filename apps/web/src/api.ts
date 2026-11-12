// Cliente de la API. La interfaz solo habla con /api: nunca con bases ni servidores.
export class ErrorApi extends Error {
  constructor(public estado: number, public detalle: string, public tipo = "") {
    super(detalle);
  }
}

async function pedir<T>(metodo: string, ruta: string, cuerpo?: unknown): Promise<T> {
  const r = await fetch(ruta, {
    method: metodo,
    credentials: "same-origin",
    headers: {
      "X-Ops-Central": "1", // exigido por la API en toda petición que modifica (protección CSRF)
      ...(cuerpo !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
  });
  if (!r.ok) {
    let detalle = r.statusText;
    let tipo = "";
    try {
      const j = await r.json();
      detalle = j.detalle ?? j.detail ?? detalle;
      tipo = j.error ?? "";
    } catch {
      /* sin cuerpo JSON */
    }
    if (Array.isArray(detalle)) {
      // Errores de validación de FastAPI: [{loc, msg}, …]
      detalle = detalle.map((d: any) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
    } else if (typeof detalle !== "string") detalle = JSON.stringify(detalle);
    throw new ErrorApi(r.status, detalle, tipo);
  }
  return (await r.json()) as T;
}

export const api = {
  get: <T,>(ruta: string) => pedir<T>("GET", ruta),
  post: <T,>(ruta: string, cuerpo: unknown = {}) => pedir<T>("POST", ruta, cuerpo),
  put: <T,>(ruta: string, cuerpo: unknown = {}) => pedir<T>("PUT", ruta, cuerpo),
  del: <T,>(ruta: string) => pedir<T>("DELETE", ruta),
};

export function mensajeError(e: unknown): string {
  if (e instanceof ErrorApi) {
    if (e.estado === 401) return "Tu sesión venció. Vuelve a ingresar.";
    return e.detalle;
  }
  return e instanceof Error ? e.message : String(e);
}
