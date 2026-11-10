/* Capa Media Ops - interfaz web (JavaScript sin dependencias) */
'use strict';

const S = { sesion: null, acciones: {}, orq: null, orqHost: null, usr: null, usrDom: null, frm: null, frmHost: null,
            estadoOrq: {}, credOrq: {}, orqInst: null, usuariosDom: {}, frmUltimo: {}, timers: [] };
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const h = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ------------------------------------------------------------------ iconos */
const I = {
  panel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  servidor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
  orq: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v6M12 16v6M4.9 4.9l4.2 4.2M14.9 14.9l4.2 4.2M2 12h6M16 12h6M4.9 19.1l4.2-4.2M14.9 9.1l4.2-4.2"/></svg>',
  usuarios: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5c1.9.7 3.1 2.6 3.5 5.5"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  trabajos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="17" r="3"/></svg>',
  escudo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3z"/></svg>',
  salir: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
  camara: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
  mas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  enchufe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 7V3M15 7V3M6 7h12v4a6 6 0 0 1-12 0zM12 17v4"/></svg>',
  lupa: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  descarga: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  rayo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
};

/* -------------------------------------------------------------------- API */
async function api(ruta, opciones = {}) {
  const o = { method: opciones.method || 'GET', credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
  if (opciones.body !== undefined) {
    o.body = JSON.stringify(opciones.body);
    o.headers['Content-Type'] = 'application/json';
  }
  if (o.method !== 'GET' && S.sesion) o.headers['X-CSRF-Token'] = S.sesion.csrf;
  const r = await fetch(ruta, o);
  let datos = {};
  try { datos = await r.json(); } catch (e) { datos = {}; }
  if (r.status === 401 && ruta !== '/api/login') { const habia = !!S.sesion; S.sesion = null; mostrarLogin(habia ? 'La sesion expiro. Ingrese nuevamente.' : ''); throw new Error('sesion'); }
  if (!r.ok) throw new Error(datos.error || ('Error HTTP ' + r.status));
  return datos;
}

function toast(msg, tipo) {
  const t = document.createElement('div');
  t.className = 'toast' + (tipo === 'error' ? ' error' : '');
  t.textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), tipo === 'error' ? 7000 : 4000);
}

function limpiarTimers() { S.timers.forEach(clearInterval); S.timers = []; }

/* ------------------------------------------------------------------ modal */
function modal({ titulo, cuerpo, botones }) {
  return new Promise(resolve => {
    const raiz = $('#modal-raiz');
    raiz.innerHTML = `<div class="modal-fondo"><div class="modal" role="dialog" aria-modal="true">
      <div class="modal-cab"><h2>${h(titulo)}</h2></div>
      <div class="modal-cuerpo">${cuerpo}</div>
      <div class="modal-pie">${botones.map((b, i) => `<button class="btn ${b.clase || ''}" data-i="${i}">${h(b.texto)}</button>`).join('')}</div>
    </div></div>`;
    const cerrar = (v) => { raiz.innerHTML = ''; document.removeEventListener('keydown', esc); resolve(v); };
    const esc = (e) => { if (e.key === 'Escape') cerrar(null); };
    document.addEventListener('keydown', esc);
    $$('.modal-pie .btn', raiz).forEach(b => b.addEventListener('click', () => {
      const def = botones[+b.dataset.i];
      if (def.validar && !def.validar(raiz)) return;
      cerrar(def.valor !== undefined ? (typeof def.valor === 'function' ? def.valor(raiz) : def.valor) : null);
    }));
    $('.modal-fondo', raiz).addEventListener('click', (e) => { if (e.target.classList.contains('modal-fondo')) cerrar(null); });
    const primero = $('input, select', raiz) || $('.modal-pie .btn:last-child', raiz);
    if (primero) primero.focus();
  });
}

function drawer(titulo, html) {
  cerrarDrawer();
  const d = document.createElement('div');
  d.id = 'drawer';
  d.innerHTML = `<div class="drawer-fondo"></div><aside class="drawer"><div class="drawer-cab"><h2>${h(titulo)}</h2>
    <button class="btn fantasma" id="drawer-cerrar" aria-label="Cerrar">${I.x}</button></div><div class="drawer-cuerpo">${html}</div></aside>`;
  document.body.appendChild(d);
  $('.drawer-fondo', d).addEventListener('click', cerrarDrawer);
  $('#drawer-cerrar', d).addEventListener('click', cerrarDrawer);
  return $('.drawer-cuerpo', d);
}
function cerrarDrawer() { const d = $('#drawer'); if (d) d.remove(); }

/* ---------------------------------------------------------- utilidades UI */
const ESTADOS = {
  en_cola: ['info', 'En cola'], ejecutando: ['info', 'Ejecutando'], exitoso: ['ok', 'Exitoso'],
  parcial: ['alerta', 'Parcial'], fallido: ['error', 'Fallido'], cancelado: ['alerta', 'Cancelado'],
  interrumpido: ['error', 'Interrumpido'],
};
function badgeEstado(e) {
  const [c, t] = ESTADOS[e] || ['', e];
  return `<span class="badge ${c}"><span class="punto ${e === 'ejecutando' ? 'girando' : ''}"></span>${h(t)}</span>`;
}
function badgeAmb(ambs, esProd) {
  return (ambs || []).map(a => `<span class="badge ${['prod', 'drp'].includes(a) ? 'prod' : 'prim'}">${h(a.toUpperCase())}</span>`).join(' ')
    || (esProd ? '<span class="badge prod">PROD</span>' : '');
}
const fecha = (s) => s ? s.replace('T', ' ').slice(0, 19) : '—';
const dur = (s) => s == null ? '—' : (s < 60 ? s.toFixed(1) + ' s' : Math.floor(s / 60) + ' min ' + Math.round(s % 60) + ' s');
function claseLinea(l) {
  if (/fin del trabajo -> EXITOSO/.test(l)) return 'l-ok';
  if (/ERROR|FALL|NO_RESUELVE|timed out|No route|refused|RESULT=ERROR/i.test(l)) return 'l-err';
  if (/\[WARN\]|ADVERTENCIA|AVISO|ALREADY|SALTADO|omitido/i.test(l)) return 'l-warn';
  if (/\[OK\]|RESULT=OK|STATE=RUNNING|exitosamente|CMOPS_OK| OK$|EXITOSO/.test(l)) return 'l-ok';
  if (/^\S+ (\[[^\]]+\] )?(====|----|>>)/.test(l)) return 'l-sep';
  if (/CMOPS:|\[SIMULACION\]/.test(l)) return 'l-cm';
  return '';
}
function lineasHtml(lineas) { return lineas.map(l => `<span class="${claseLinea(l)}">${h(l)}</span>`).join('\n'); }

/* ------------------------------------------------- ejecucion de acciones */
async function ejecutarAccion(accion, parametros, secretos, opciones = {}) {
  let prev;
  try {
    prev = await api('/api/trabajos/previsualizar', { method: 'POST', body: { accion, parametros, secretos } });
  } catch (e) { if (e.message !== 'sesion') toast(e.message, 'error'); return null; }
  if (!prev.permitido) {
    await modal({ titulo: 'Accion no permitida', botones: [{ texto: 'Entendido', clase: 'prim' }],
      cuerpo: `<div class="aviso error">Su rol <b>${h(S.sesion.rol)}</b> no puede ejecutar esta accion ${prev.es_prod ? 'en un ambiente <b>productivo</b>' : ''}.</div>
               <p class="muted peq">${h(S.sesion.descripcion_rol || '')}</p>` });
    return null;
  }
  let cambio = '';
  if (!prev.solo_lectura || opciones.confirmar) {
    const peligro = opciones.peligro || prev.es_prod;
    const r = await modal({
      titulo: opciones.titulo || 'Confirmar ejecucion',
      cuerpo: `${prev.es_prod ? '<div class="aviso prod"><b>Ambiente productivo (PROD/DRP).</b> La accion queda registrada con el numero de cambio.</div>' : ''}
        <p>${h(prev.resumen)}</p>
        <div><div class="muted peq">Pasos que se ejecutaran:</div><ul class="lista-mini">${prev.pasos.map(p => `<li>${h(p)}</li>`).join('')}</ul></div>
        ${opciones.extra || ''}
        ${prev.requiere_cambio || !prev.solo_lectura ? `<label class="campo">Numero de cambio (CAB/RFC)${prev.requiere_cambio ? ' — obligatorio' : ' — opcional'}
          <input type="text" id="m-cambio" maxlength="40" placeholder="Ej: CHG0012345" autocomplete="off"></label>` : ''}
        ${peligro && !prev.solo_lectura ? `<label class="check"><input type="checkbox" id="m-ok"> Confirmo que revise la lista y deseo continuar</label>` : ''}
        <div class="m-err aviso error oculto"></div>`,
      botones: [
        { texto: 'Cancelar', valor: null },
        { texto: opciones.textoBoton || 'Ejecutar', clase: peligro && !prev.solo_lectura ? 'peligro' : 'prim',
          validar: (raiz) => {
            const err = $('.m-err', raiz);
            const c = $('#m-cambio', raiz);
            if (prev.requiere_cambio && !(c && /^[A-Za-z0-9_\-/.]{3,40}$/.test(c.value.trim()))) { err.textContent = 'Indique un numero de cambio valido.'; err.classList.remove('oculto'); return false; }
            const ok = $('#m-ok', raiz);
            if (ok && !ok.checked) { err.textContent = 'Debe marcar la confirmacion.'; err.classList.remove('oculto'); return false; }
            return true;
          },
          valor: (raiz) => ({ cambio: ($('#m-cambio', raiz) || { value: '' }).value.trim() }) },
      ] });
    if (!r) return null;
    cambio = r.cambio;
  }
  try {
    const t = await api('/api/trabajos', { method: 'POST', body: { accion, parametros, secretos, cambio } });
    toast('Trabajo ' + t.id + ' iniciado');
    if (opciones.panel) seguirEnPanel(opciones.panel, t.id, opciones.alTerminar);
    else location.hash = '#/trabajos/' + t.id;
    return t;
  } catch (e) { if (e.message !== 'sesion') toast(e.message, 'error'); return null; }
}

/* Sigue un trabajo dentro de un contenedor de la pagina actual. */
function seguirEnPanel(cont, id, alTerminar) {
  cont.innerHTML = `<div class="tarjeta"><div class="tarjeta-cab"><h2>Ejecucion en curso</h2>
    <div class="acciones"><span class="estado-t"></span><a class="btn chico" href="#/trabajos/${h(id)}">Ver detalle</a></div></div>
    <div class="consola" tabindex="0"></div></div>`;
  const consola = $('.consola', cont);
  let desde = 0, todas = [];
  const tick = async () => {
    try {
      const r = await api('/api/trabajos/' + id + '?desde=' + desde);
      desde = r.total;
      if (r.lineas.length) {
        todas = todas.concat(r.lineas);
        const abajo = consola.scrollHeight - consola.scrollTop - consola.clientHeight < 40;
        consola.insertAdjacentHTML('beforeend', (consola.innerHTML ? '\n' : '') + lineasHtml(r.lineas));
        if (abajo) consola.scrollTop = consola.scrollHeight;
      }
      $('.estado-t', cont).innerHTML = badgeEstado(r.meta.estado);
      if (!['en_cola', 'ejecutando'].includes(r.meta.estado)) {
        clearInterval(timer);
        if (alTerminar) alTerminar(r.meta, todas);
      }
    } catch (e) { clearInterval(timer); }
  };
  const timer = setInterval(tick, 900);
  S.timers.push(timer);
  tick();
}

/* ---------------------------------------------------------------- parsers */
function parsearStatus(lineas) {
  const out = {}; let dom = null;
  for (const l of lineas) {
    let m = l.match(/=+ ([A-Za-z0-9_.\-]+) =+/);
    if (m && !/ (STOP|START) /.test(l)) { dom = m[1]; out[dom] = { admin: null, servers: {} }; continue; }
    if (!dom) continue;
    m = l.match(/AdminServer\(([^)]+)\): (\S+)/);
    if (m) { out[dom].admin = m[2]; continue; }
    m = l.match(/SERVER=(\S+) STATE=(\S+)/);
    if (m) out[dom].servers[m[1]] = m[2];
  }
  return out;
}
function parsearUsuarios(lineas) {
  const out = [];
  for (const l of lineas) {
    const m = l.match(/\s(\d+)\.\s+(\S+)\s+-->\s+(.*)$/);
    if (m) out.push({ usuario: m[2], grupos: m[3] });
  }
  return out;
}

/* ================================================================ LOGIN */
function mostrarLogin(msg) {
  limpiarTimers(); cerrarDrawer();
  $('#app').className = '';
  $('#app').innerHTML = `<div class="login"><div class="tarjeta">
    <div class="marca"><img src="icono.svg" alt=""><div><b>Capa Media Ops</b><span>Operacion centralizada de middleware</span></div></div>
    <form id="f-login" autocomplete="on">
      ${msg ? `<div class="aviso info">${h(msg)}</div>` : ''}
      <label class="campo">Usuario<input type="text" name="usuario" autocomplete="username" required></label>
      <label class="campo">Clave<input type="password" name="clave" autocomplete="current-password" required></label>
      <div class="l-err aviso error oculto"></div>
      <button class="btn prim" type="submit">Ingresar</button>
      <p class="tenue peq">Acceso restringido. Todas las acciones quedan auditadas.</p>
    </form></div></div>`;
  $('#f-login').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      S.sesion = await api('/api/login', { method: 'POST', body: { usuario: f.usuario.value.trim(), clave: f.clave.value } });
      await arrancar();
    } catch (err) {
      const el = $('.l-err'); el.textContent = err.message; el.classList.remove('oculto');
    }
  });
  $('#f-login input').focus();
}

/* ================================================================ SHELL */
const NAV = [
  ['General'],
  ['panel', 'Panel', I.panel],
  ['inventario', 'Inventario', I.servidor],
  ['Operacion'],
  ['orquestacion', 'Orquestacion WebLogic', I.orq],
  ['usuarios', 'Usuarios WebLogic', I.usuarios],
  ['frmweb', 'Forms: frmweb huerfanos', I.cpu],
  ['Control'],
  ['trabajos', 'Ejecuciones', I.trabajos],
  ['admin', 'Administracion', I.escudo, 'admin'],
];

function pintarShell() {
  const s = S.sesion;
  $('#app').className = '';
  $('#app').innerHTML = `<div class="shell">
    <aside class="lateral" id="lateral">
      <div class="marca"><img src="icono.svg" alt=""><div><b>Capa Media Ops</b><span>v${h(s.version)} · Operacion central</span></div></div>
      <nav class="nav">${NAV.map(n => n.length === 1 ? `<div class="grupo">${h(n[0])}</div>`
        : (n[3] && s.rol !== n[3] ? '' : `<a href="#/${n[0]}" data-pag="${n[0]}">${n[2]}<span>${h(n[1])}</span></a>`)).join('')}</nav>
      <div class="pie-lateral">${s.simulacion ? 'Modo SIMULACION' : 'Modo real (SSH)'}</div>
    </aside>
    <div class="principal">
      <header class="barra">
        <button class="btn fantasma menu-movil" id="btn-menu" aria-label="Menu">${I.menu}</button>
        ${s.simulacion ? '<span class="badge alerta">SIMULACION</span>' : '<span class="badge ok">CONECTADO</span>'}
        <div class="espacio"></div>
        <div class="usuario-chip"><div class="avatar">${h((s.nombre || s.usuario).slice(0, 2).toUpperCase())}</div>
          <div class="nombre"><div>${h(s.nombre)}</div><div class="tenue peq">${h(s.usuario)} · rol ${h(s.rol)}</div></div></div>
        <button class="btn chico" id="btn-salir">${I.salir}Salir</button>
      </header>
      <main class="contenido" id="pagina"></main>
    </div></div>`;
  $('#btn-salir').addEventListener('click', async () => { try { await api('/api/logout', { method: 'POST' }); } catch (e) { /* */ } S.sesion = null; mostrarLogin('Sesion cerrada.'); });
  $('#btn-menu').addEventListener('click', () => $('#lateral').classList.toggle('abierto'));
}

async function arrancar() {
  S.acciones = await api('/api/acciones');
  pintarShell();
  if (!location.hash || location.hash === '#') { location.hash = '#/panel'; return; }  // hashchange enruta
  enrutar();
}

const PAGINAS = {};
function enrutar() {
  if (!S.sesion) return;
  limpiarTimers(); cerrarDrawer();
  const partes = (location.hash.replace(/^#\/?/, '') || 'panel').split('/');
  const pag = PAGINAS[partes[0]] ? partes[0] : 'panel';
  $$('.nav a').forEach(a => a.classList.toggle('activo', a.dataset.pag === pag));
  $('#lateral').classList.remove('abierto');
  const cont = $('#pagina');
  cont.innerHTML = '<div class="vacio">Cargando…</div>';
  PAGINAS[pag](cont, partes.slice(1)).catch(e => { if (e.message !== 'sesion') cont.innerHTML = `<div class="aviso error">${h(e.message)}</div>`; });
}
window.addEventListener('hashchange', enrutar);

function cabecera(titulo, desc, acciones) {
  return `<div class="cabecera"><div><h1>${h(titulo)}</h1>${desc ? `<p>${desc}</p>` : ''}</div>${acciones ? `<div class="acciones">${acciones}</div>` : ''}</div>
    ${S.sesion.simulacion ? `<div class="banner-sim">${I.rayo}<span><b>Modo simulacion:</b> las acciones NO se conectan a los servidores; las salidas son de ejemplo.</span></div>` : ''}`;
}

function tablaTrabajos(lista, compacta) {
  if (!lista.length) return '<div class="vacio"><b>Sin ejecuciones</b>Las acciones que se lancen apareceran aqui.</div>';
  return `<div class="tabla-env"><table><thead><tr><th>Fecha</th><th>Accion</th>${compacta ? '' : '<th>Detalle</th>'}<th>Usuario</th>${compacta ? '' : '<th>Cambio</th>'}<th>Estado</th><th class="num">Duracion</th></tr></thead><tbody>
    ${lista.map(t => `<tr class="clic" data-id="${h(t.id)}"><td class="mono">${h(fecha(t.creado))}</td>
      <td>${h(t.nombre)} ${t.es_prod ? '<span class="badge prod">PROD</span>' : ''} ${t.simulacion ? '<span class="badge">sim</span>' : ''}</td>
      ${compacta ? '' : `<td class="muted">${h(t.resumen)}</td>`}<td>${h(t.usuario)}</td>${compacta ? '' : `<td class="mono">${h(t.cambio || '—')}</td>`}
      <td>${badgeEstado(t.estado)}</td><td class="num">${h(dur(t.duracion_s))}</td></tr>`).join('')}
    </tbody></table></div>`;
}
function enlazarFilasTrabajos(cont) {
  $$('tr[data-id]', cont).forEach(tr => tr.addEventListener('click', () => { location.hash = '#/trabajos/' + tr.dataset.id; }));
}

/* ================================================================ PANEL */
PAGINAS.panel = async (cont) => {
  const r = await api('/api/resumen');
  const inv = r.inventario;
  const ambs = Object.entries(inv.por_ambiente).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...ambs.map(a => a[1]));
  cont.innerHTML = cabecera('Panel', 'Vista general de la operacion centralizada de Capa Media.') + `
    <div class="rejilla r-4">
      <div class="tarjeta kpi"><div class="etq">Servidores inventariados</div><div class="val">${inv.servidores}</div><div class="sub">${inv.dominios_inventario} dominios en la linea base</div></div>
      <div class="tarjeta kpi"><div class="etq">Dominios WebLogic orquestados</div><div class="val">${inv.orq_dominios}</div><div class="sub">${inv.orq_managed_servers} managed servers en ${inv.orq_hosts} host(s)</div></div>
      <div class="tarjeta kpi"><div class="etq">Ejecuciones hoy</div><div class="val">${r.trabajos_hoy}</div><div class="sub">${r.en_ejecucion} en curso</div></div>
      <div class="tarjeta kpi"><div class="etq">Con error hoy</div><div class="val">${r.fallidos_hoy}</div><div class="sub">fallidas o parciales</div></div>
    </div>
    <div class="rejilla r-21">
      <div class="tarjeta"><div class="tarjeta-cab"><h2>Ultimas ejecuciones</h2><a class="btn chico" href="#/trabajos">Ver todas</a></div><div id="p-ult">${tablaTrabajos(r.ultimos, true)}</div></div>
      <div class="tarjeta"><div class="tarjeta-cab"><h2>Servidores por ambiente</h2><a class="btn chico" href="#/inventario">Inventario</a></div>
        <div class="barras">${ambs.map(([a, n]) => `<div class="barra-fila"><span>${h(a.toUpperCase())}</span><div class="barra-pista"><div class="barra-relleno ${['prod', 'drp'].includes(a) ? 'prod' : ''}" data-pct="${(100 * n / max).toFixed(1)}"></div></div><span class="num">${n}</span></div>`).join('')}</div>
        <p class="tenue peq">Origen: ${h(inv.meta.origen || '—')} · importado ${h(fecha(inv.meta.generado))} · ${inv.hechos} servidor(es) con informacion recolectada.</p>
      </div>
    </div>
    <h2 class="sep-mod">Modulos</h2>
    <div class="rejilla r-4">
      ${[['orquestacion', I.orq, 'Orquestacion WebLogic', 'Estado, snapshot, bajada y subida ordenada de dominios (Node Manager + AdminServer).', inv.orq_dominios + ' dominios'],
         ['usuarios', I.usuarios, 'Usuarios WebLogic', 'Crear, eliminar, dar rol admin o agregar a grupos via WLST, con evidencia.', inv.usuarios_wl_dominios + ' dominio(s)'],
         ['frmweb', I.cpu, 'Forms: frmweb huerfanos', 'Deteccion (dry-run) y mitigacion controlada por lotes de procesos frmweb en bucle.', inv.frmweb_hosts + ' host(s)'],
         ['inventario', I.servidor, 'Inventario y recoleccion', 'Linea base de servidores y recoleccion de informacion por SSH (solo lectura).', inv.servidores + ' servidores']]
        .map(([k, ico, t, d, n]) => `<a class="tarjeta modulo-card" href="#/${k}"><div class="ico">${ico}</div><h3>${h(t)}</h3><p>${h(d)}</p><span class="badge prim">${h(n)}</span></a>`).join('')}
    </div>`;
  $$('.barra-relleno', cont).forEach(b => requestAnimationFrame(() => { b.style.width = b.dataset.pct + '%'; }));
  enlazarFilasTrabajos(cont);
  const rej = $$('.rejilla', cont); rej[1].style.marginTop = '16px';
  const sep = $('.sep-mod', cont); sep.style.margin = '24px 0 12px';
};

/* ============================================================ INVENTARIO */
PAGINAS.inventario = async (cont) => {
  const r = await api('/api/inventario');
  const todos = r.servidores;
  const ambientes = [...new Set(todos.flatMap(s => s.ambientes))].sort();
  const sel = new Set();
  cont.innerHTML = cabecera('Inventario de servidores', 'Linea base importada desde el Excel. La recoleccion por SSH es solo lectura y alimenta la ficha de cada servidor.',
    `<button class="btn" id="i-con" disabled>${I.enchufe}Probar conexion</button><button class="btn prim" id="i-rec" disabled>${I.lupa}Recolectar informacion</button>`) + `
    ${(r.meta.avisos || []).length ? `<div class="aviso info peq i-avisos">${r.meta.avisos.length} aviso(s) de calidad del inventario: ${h(r.meta.avisos.slice(0, 3).join(' · '))}${r.meta.avisos.length > 3 ? '…' : ''}</div>` : ''}
    <div class="tarjeta">
      <div class="filtros">
        <label class="campo crece">Buscar<input type="search" id="i-q" placeholder="Hostname, IP o dominio"></label>
        <label class="campo">Ambiente<select id="i-amb"><option value="">Todos</option>${ambientes.map(a => `<option>${h(a)}</option>`).join('')}</select></label>
        <label class="campo">Modulo<select id="i-mod"><option value="">Todos</option><option value="orquestacion">Orquestacion</option><option value="usuarios_wl">Usuarios WL</option><option value="frmweb">frmweb</option></select></label>
      </div>
      <div class="muted peq" id="i-cuenta"></div>
      <div class="tabla-env"><table><thead><tr><th><input type="checkbox" id="i-todos" aria-label="Seleccionar todos"></th><th>Hostname</th><th>IP</th><th>Ambiente</th><th>Dominios</th><th>Modulos</th><th>Recolectado</th></tr></thead><tbody id="i-cuerpo"></tbody></table></div>
    </div>`;
  const av = $('.i-avisos', cont); if (av) av.style.marginBottom = '14px';
  const filtrar = () => {
    const q = $('#i-q').value.trim().toLowerCase(), a = $('#i-amb').value, m = $('#i-mod').value;
    return todos.filter(s => (!a || s.ambientes.includes(a)) && (!m || s.modulos.includes(m)) &&
      (!q || s.hostname.toLowerCase().includes(q) || (s.ip || '').includes(q) || s.dominios.some(d => d.toLowerCase().includes(q))));
  };
  const pintar = () => {
    const lst = filtrar();
    $('#i-cuenta').textContent = `${lst.length} de ${todos.length} servidores · ${sel.size} seleccionado(s)`;
    $('#i-cuerpo').innerHTML = lst.map(s => `<tr class="clic" data-h="${h(s.hostname)}">
      <td><input type="checkbox" class="i-sel" data-h="${h(s.hostname)}" ${sel.has(s.hostname) ? 'checked' : ''} aria-label="Seleccionar"></td>
      <td><b>${h(s.hostname)}</b></td><td class="mono">${h(s.ip)}${s.ips_alternas.length ? ` <span class="tenue">+${s.ips_alternas.length}</span>` : ''}</td>
      <td>${badgeAmb(s.ambientes, s.es_prod)}</td>
      <td class="muted">${h(s.dominios.slice(0, 3).join(', '))}${s.dominios.length > 3 ? ` <span class="tenue">+${s.dominios.length - 3}</span>` : ''}</td>
      <td>${s.modulos.map(m => `<span class="badge info">${h(m)}</span>`).join(' ')}</td>
      <td class="peq">${s.recolectado ? h(fecha(s.recolectado)) : '<span class="tenue">—</span>'}</td></tr>`).join('') ||
      '<tr><td colspan="7"><div class="vacio">Sin resultados para el filtro.</div></td></tr>';
    $('#i-rec').disabled = $('#i-con').disabled = sel.size === 0;
  };
  ['#i-q', '#i-amb', '#i-mod'].forEach(id => $(id).addEventListener('input', pintar));
  $('#i-todos').addEventListener('change', (e) => { filtrar().forEach(s => e.target.checked ? sel.add(s.hostname) : sel.delete(s.hostname)); pintar(); });
  $('#i-cuerpo').addEventListener('click', (e) => {
    const cb = e.target.closest('.i-sel');
    if (cb) { cb.checked ? sel.add(cb.dataset.h) : sel.delete(cb.dataset.h); pintar(); return; }
    const tr = e.target.closest('tr[data-h]');
    if (tr) fichaServidor(tr.dataset.h);
  });
  $('#i-rec').addEventListener('click', () => ejecutarAccion('rec.recolectar', { hosts: [...sel] }, {}));
  $('#i-con').addEventListener('click', () => ejecutarAccion('sys.conectividad', { hosts: [...sel] }, {}));
  pintar();
};

async function fichaServidor(host) {
  const d = await api('/api/inventario/' + encodeURIComponent(host));
  const f = d.hechos;
  const cuerpo = drawer(host, `
    <div class="acciones">${badgeAmb(d.ambientes, d.es_prod)} ${d.modulos.map(m => `<span class="badge info">${h(m)}</span>`).join(' ')}</div>
    <dl class="dl f-dl"><dt>IP</dt><dd class="mono">${h(d.ip)}${(d.ips_alternas || []).length ? ' (alternas: ' + h(d.ips_alternas.join(', ')) + ')' : ''}</dd>
      <dt>Puerto SSH</dt><dd>${h(d.ssh_port)}</dd>
      <dt>Usuario SSH</dt><dd class="mono">${h(d.ssh_usuario || 'cuenta de servicio global')}</dd>
      <dt>Dominios</dt><dd>${(d.dominios || []).map(x => `<span class="chip">${h(x.nombre)}</span>`).join(' ') || '—'}</dd></dl>
    <div class="acciones f-acc"><button class="btn" id="f-con">${I.enchufe}Probar conexion</button><button class="btn prim" id="f-rec">${I.lupa}Recolectar informacion</button></div>
    <h3 class="f-h">Informacion recolectada</h3>
    ${f ? `<p class="tenue peq">Recolectado ${h(fecha(f._recolectado))} · trabajo <a href="#/trabajos/${h(f._trabajo)}">${h(f._trabajo)}</a></p>
      <dl class="dl"><dt>Sistema operativo</dt><dd>${h(f.so)}</dd><dt>Kernel</dt><dd class="mono">${h(f.kernel)}</dd>
        <dt>CPU</dt><dd>${h(f.cpu_nucleos)} nucleos · ${h(f.cpu_modelo)}</dd>
        <dt>Memoria</dt><dd>${h(f.ram_usada_gb)} / ${h(f.ram_total_gb)} GB usados · swap ${h(f.swap_usada_gb)}/${h(f.swap_total_gb)} GB</dd>
        <dt>Uptime</dt><dd>${h(f.uptime_dias)} dias</dd>
        <dt>Limites</dt><dd class="mono peq">nofile ${h(f.limites?.nofile_soft)}/${h(f.limites?.nofile_hard)} · nproc ${h(f.limites?.nproc_soft)}/${h(f.limites?.nproc_hard)}</dd>
        <dt>Java</dt><dd>${(f.java || []).map(j => `<span class="chip">${h(j)}</span>`).join(' ') || '—'}</dd>
        <dt>WebLogic</dt><dd>${(f.weblogic || []).map(w => `<span class="chip" title="${h(w.ruta || '')}">${h(w.dominio)} ${h(w.version || '')}${w.propietario ? ' · ' + h(w.propietario) : ''}</span>`).join(' ') || '—'}</dd>
        <dt>DNS BD</dt><dd>${Object.entries(f.dns || {}).map(([k, v]) => `<span class="chip ${v === 'NO_RESUELVE' ? 'error' : ''}">${h(k)}: ${h(v)}</span>`).join(' ')}</dd></dl>
      <h3 class="f-h">Discos</h3>
      <table><thead><tr><th>Montaje</th><th>Tamano</th><th class="num">Uso</th></tr></thead><tbody>
      ${(f.discos || []).map(x => `<tr><td class="mono">${h(x.montaje)}</td><td>${h(x.tamano)}</td><td class="num"><span class="badge ${x.uso_pct >= 85 ? 'error' : x.uso_pct >= 70 ? 'alerta' : 'ok'}">${h(x.uso_pct)}%</span></td></tr>`).join('')}</tbody></table>`
    : '<div class="vacio"><b>Sin informacion recolectada</b>Use "Recolectar informacion" para obtener SO, CPU, memoria, discos, Java y dominios WebLogic.</div>'}`);
  $$('.f-h', cuerpo).forEach(x => { x.style.marginTop = '20px'; });
  $('.f-dl', cuerpo).style.margin = '14px 0';
  $('#f-con', cuerpo).addEventListener('click', () => { cerrarDrawer(); ejecutarAccion('sys.conectividad', { hosts: [host] }, {}); });
  $('#f-rec', cuerpo).addEventListener('click', () => { cerrarDrawer(); ejecutarAccion('rec.recolectar', { hosts: [host] }, {}); });
}

/* ========================================================= ORQUESTACION */
async function ultimoTrabajo(modulo, filtro) {
  const r = await api('/api/trabajos?modulo=' + modulo + '&limite=100');
  const t = r.trabajos.find(filtro);
  if (!t) return null;
  return api('/api/trabajos/' + t.id);
}

function parsearCred(lineas) {
  const out = {};
  for (const l of lineas) {
    const m = l.match(/DOMINIO=(\S+) DIR=(\S+) PROPIETARIO=(\S+) COINCIDE=(\S+) NM_CRED=(\S+) ADMIN_CRED=(\S+)/);
    if (m) out[m[1]] = { dir: m[2], propietario: m[3], coincide: m[4], nm: m[5], admin: m[6] };
  }
  return out;
}

PAGINAS.orquestacion = async (cont) => {
  if (!S.orq) S.orq = (await api('/api/orquestacion')).hosts;
  const hosts = S.orq;
  if (!hosts.length) { cont.innerHTML = cabecera('Orquestacion WebLogic') + '<div class="tarjeta vacio"><b>Sin hosts configurados</b>Agregue hosts en inventario/configuracion_modulos.json o use bin/cm importar-wlconfig / proponer-orquestacion.</div>'; return; }
  if (!S.orqHost || !hosts.find(x => x.host === S.orqHost)) S.orqHost = hosts[0].host;
  const H = hosts.find(x => x.host === S.orqHost);
  if (!S.orqInst || !H.instalaciones.find(i => i.id === S.orqInst)) S.orqInst = (H.instalaciones.find(i => i.dominios.length) || H.instalaciones[0]).id;
  const N = H.instalaciones.find(i => i.id === S.orqInst);
  const clave = H.host + '/' + N.id;
  const est = S.estadoOrq[clave];
  const cred = S.credOrq[clave];
  const nmHost = N.nm_host || '';
  const credBadge = (v) => v ? `<span class="badge ${v === 'OK' ? 'ok' : v === 'FALTA' ? 'error' : 'alerta'}">${h(v)}</span>` : '<span class="tenue">—</span>';
  cont.innerHTML = cabecera('Orquestacion WebLogic', 'Bajada y subida ordenada de dominios: Managed Servers via Node Manager y AdminServer. Cada instalacion se ejecuta con su propio usuario de SO y cada dominio con sus propias credenciales cifradas.') + `
    <div class="seleccion-host">${hosts.map(x => { const doms = x.instalaciones.reduce((a, i) => a + i.dominios.length, 0);
      const ms = x.instalaciones.reduce((a, i) => a + i.dominios.reduce((b, d) => b + d.servers.length, 0), 0);
      return `<button class="host-btn ${x.host === H.host ? 'activo' : ''}" data-h="${h(x.host)}"><b>${h(x.host)}</b>
      <span class="muted peq">${x.instalaciones.length} instalacion(es) · ${doms} dominios · ${ms} MS</span> ${x.es_prod ? '<span class="badge prod">PROD</span>' : '<span class="badge prim">NO PROD</span>'}</button>`; }).join('')}</div>
    <div class="seleccion-host o-inst">${H.instalaciones.map(i => `<button class="host-btn inst-btn ${i.id === N.id ? 'activo' : ''}" data-i="${h(i.id)}">
      <b>Instalacion ${h(i.id)}</b><span class="muted peq mono">usuario SO ${h(i.usuario_so || 'SIN DEFINIR')} · ${i.dominios.length} dominios</span></button>`).join('')}</div>
    <div class="rejilla">
      <div class="tarjeta">
        <h2>Acciones</h2>
        <div class="o-acc-grid">
        <div>
        <dl class="dl o-dl"><dt>Usuario SSH</dt><dd class="mono">${h(H.ssh_usuario || '—')}</dd><dt>Usuario SO (sudo)</dt><dd class="mono">${h(N.usuario_so || 'SIN DEFINIR')}</dd>
          <dt>IP SSH</dt><dd class="mono">${h(H.ip)}</dd><dt>NM host</dt><dd class="mono">${h(nmHost || '—')}</dd><dt>Ruta</dt><dd class="mono peq">${h(N.ruta || '—')}</dd></dl>
        ${H.nota ? `<div class="aviso info peq o-nota">${h(H.nota)}</div>` : ''}
        ${N.nota ? `<div class="aviso info peq o-nota">${h(N.nota)}</div>` : ''}
        </div>
        <div>
        <div class="acciones o-acc"><button class="btn" id="o-verif">${I.lupa}Verificar credenciales</button><button class="btn" id="o-status">${I.refresh}Consultar estado</button></div>
        <div class="acciones o-acc"><button class="btn" id="o-inv">${I.camara}Tomar snapshot</button></div>
        <div class="acciones o-acc"><button class="btn exito" id="o-start">${I.play}Subir</button><button class="btn peligro" id="o-stop">${I.stop}Bajar</button></div>
        </div>
        <div class="o-opc">
          <label class="check"><input type="checkbox" id="o-par"> Ejecutar dominios en paralelo</label>
          <label class="check"><input type="checkbox" id="o-nm"> Al bajar: detener tambien Node Manager (apagado total)</label>
          <label class="campo">Timeout por servidor (s)<input type="number" id="o-tim" min="30" max="1800" placeholder="120 al bajar / 300 al subir"></label>
        </div>
        </div>
      </div>
      <div class="tarjeta">
        <div class="tarjeta-cab"><h2>Dominios de ${h(N.id)} en ${h(H.host)}</h2><span class="muted peq">${est ? 'Estado de ' + h(fecha(est.fecha)) : 'Sin estado consultado'}${cred ? ' · credenciales verificadas ' + h(fecha(cred.fecha)) : ''}</span></div>
        ${N.dominios.length ? `<div class="tabla-env"><table><thead><tr><th><input type="checkbox" id="o-todos" checked aria-label="Todos"></th><th>Dominio</th><th>Propietario SO</th><th>Cred. NM</th><th>Cred. consola</th><th>AdminServer</th><th>Managed Servers</th><th></th></tr></thead><tbody>
        ${N.dominios.map(d => {
          const e = est && est.dominios[d.nombre];
          const c = cred && cred.dominios[d.nombre];
          const adm = e && e.admin;
          return `<tr class="dominio-fila"><td><input type="checkbox" class="o-dom" value="${h(d.nombre)}" checked aria-label="${h(d.nombre)}"></td>
            <td><b>${h(d.nombre)}</b><div class="tenue peq mono">${h((d.admin_url || 'admin_url sin definir').replace('${NM_HOST}', nmHost))} · NM ${h(d.nm_port || '?')}</div></td>
            <td>${c ? `<span class="badge ${c.coincide === 'SI' ? 'ok' : 'error'}" title="${c.coincide === 'SI' ? 'Coincide con el usuario de la instalacion' : 'NO coincide con ' + h(N.usuario_so)}">${h(c.propietario)}</span>` : '<span class="tenue">—</span>'}</td>
            <td>${credBadge(c && c.nm)}</td><td>${credBadge(c && c.admin)}</td>
            <td>${adm ? `<span class="badge ${adm === 'RUNNING' ? 'ok' : 'error'}">${h(adm)}</span>` : '<span class="tenue">—</span>'}</td>
            <td><div class="chips">${d.servers.map(s => { const st = e && e.servers[s]; return `<span class="chip ${st === 'RUNNING' ? 'ok' : st ? (/SHUTDOWN|FAILED|UNKNOWN/.test(st) ? 'error' : 'alerta') : ''}" title="${h(st || 'sin dato')}">${h(s)}</span>`; }).join('')}</div></td>
            <td><button class="btn o-cred" data-d="${h(d.nombre)}" title="Registrar credenciales cifradas de este dominio">Credenciales</button></td></tr>`;
        }).join('')}</tbody></table></div>`
        : `<div class="vacio"><b>Instalacion sin dominios configurados</b>Recolecte informacion del host (Inventario) y ejecute <span class="mono">bin/cm proponer-orquestacion ${h(H.host)}</span> para descubrir los dominios y su propietario.</div>`}
      </div>
    </div>
    <div id="o-panel"></div>`;
  $('.o-dl', cont).style.margin = '0 0 12px';
  $$('.o-nota', cont).forEach(n => { n.style.marginBottom = '12px'; });
  $('.o-inst', cont).style.marginTop = '-6px';
  $$('.o-acc', cont).forEach(x => { x.style.marginBottom = '10px'; });
  const opc = $('.o-opc', cont); Object.assign(opc.style, { display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '14px' });
  $('#o-panel', cont).style.marginTop = '16px';
  $$('.host-btn[data-h]', cont).forEach(b => b.addEventListener('click', () => { S.orqHost = b.dataset.h; S.orqInst = null; enrutar(); }));
  $$('.inst-btn', cont).forEach(b => b.addEventListener('click', () => { S.orqInst = b.dataset.i; enrutar(); }));
  const todos = $('#o-todos'); if (todos) todos.addEventListener('change', (e) => $$('.o-dom').forEach(c => { c.checked = e.target.checked; }));
  const base = () => ({ host: H.host, instalacion: N.id });
  const params = () => Object.assign(base(), { dominios: $$('.o-dom').filter(c => c.checked).map(c => c.value),
    paralelo: $('#o-par').checked, bajar_nm: $('#o-nm').checked, timeout: $('#o-tim').value || undefined });
  const conservarPanel = () => { const panel = $('#o-panel').innerHTML; enrutar(); setTimeout(() => { const p = $('#o-panel'); if (p) p.innerHTML = panel; }, 50); };
  const alTerminar = (meta, lineas) => {
    if (meta.accion === 'orq.status' && meta.estado !== 'fallido') {
      S.estadoOrq[clave] = { fecha: meta.fin, dominios: Object.assign({}, (S.estadoOrq[clave] || {}).dominios, parsearStatus(lineas)) };
      conservarPanel();
    } else if (meta.accion === 'orq.verificar' && meta.estado !== 'fallido') {
      S.credOrq[clave] = { fecha: meta.fin, dominios: Object.assign({}, (S.credOrq[clave] || {}).dominios, parsearCred(lineas)) };
      conservarPanel();
    } else if (meta.accion === 'orq.credenciales') {
      toast(meta.estado === 'exitoso' ? 'Credenciales registradas. Use "Verificar credenciales" para refrescar.' : 'No se pudieron registrar las credenciales (' + meta.estado + ')', meta.estado === 'exitoso' ? '' : 'error');
    } else toast('Finalizo: ' + meta.nombre + ' (' + meta.estado + '). Consulte el estado para refrescar.');
  };
  const correr = (accion, extra) => {
    const p = params();
    if (!p.dominios.length) { toast('Seleccione al menos un dominio', 'error'); return; }
    ejecutarAccion(accion, p, {}, Object.assign({ panel: $('#o-panel'), alTerminar }, extra));
  };
  const sinDominios = !N.dominios.length;
  ['#o-verif', '#o-status', '#o-inv', '#o-start', '#o-stop'].forEach(id => { $(id).disabled = sinDominios; });
  $('#o-verif').addEventListener('click', () => correr('orq.verificar'));
  $('#o-status').addEventListener('click', () => correr('orq.status'));
  $('#o-inv').addEventListener('click', () => correr('orq.inventory'));
  $('#o-start').addEventListener('click', () => correr('orq.start', { titulo: 'Confirmar SUBIDA de dominios', textoBoton: 'Subir dominios' }));
  $('#o-stop').addEventListener('click', () => correr('orq.stop', { titulo: 'Confirmar BAJADA de dominios', textoBoton: 'Bajar dominios', peligro: true,
    extra: '<div class="aviso info peq">Se tomara un snapshot automatico antes de bajar, para que "Subir" restaure los mismos Managed Servers.</div>' }));
  $$('.o-cred', cont).forEach(b => b.addEventListener('click', async () => {
    const dom = b.dataset.d;
    const r = await modal({ titulo: 'Credenciales del dominio ' + dom,
      cuerpo: `<p class="muted">Genera en el servidor, como <span class="mono">${h(N.usuario_so)}</span>, los archivos cifrados <span class="mono">nmsecure/*.secure</span> (storeUserConfig). Se hace una vez por dominio o al rotar la clave; cada dominio puede tener usuarios distintos.</p>
        <label class="campo">Usuario Node Manager<input type="text" id="c-nu" value="${h(N.nm_usuario || '')}" autocomplete="off" placeholder="usuario NM de este dominio"></label>
        <label class="campo">Clave Node Manager<input type="password" id="c-np" autocomplete="new-password"></label>
        <label class="campo">Usuario consola (opcional)<input type="text" id="c-au" value="${h(N.admin_usuario || '')}" autocomplete="off" placeholder="vacio = no registrar"></label>
        <label class="campo">Clave consola<input type="password" id="c-ap" autocomplete="new-password"></label>
        <p class="tenue peq">Las claves viajan solo por SSH (stdin) al servidor; no se guardan en el nodo central, ni en la evidencia ni en la auditoria.</p>`,
      botones: [{ texto: 'Cancelar' }, { texto: 'Registrar', clase: 'prim',
        validar: (x) => { const ok = $('#c-nu', x).value.trim() && $('#c-np', x).value && (!$('#c-au', x).value.trim() || $('#c-ap', x).value);
          if (!ok) toast('Indique usuario y clave del Node Manager (y la clave de consola si indica usuario)', 'error'); return !!ok; },
        valor: (x) => ({ nm_usuario: $('#c-nu', x).value.trim(), nm_password: $('#c-np', x).value,
          admin_usuario: $('#c-au', x).value.trim(), admin_password: $('#c-ap', x).value }) }] });
    if (r) ejecutarAccion('orq.credenciales', Object.assign(base(), { dominio: dom }), r, { panel: $('#o-panel'), alTerminar,
      titulo: 'Confirmar registro de credenciales', textoBoton: 'Registrar' });
  }));
  if (!est && !sinDominios) {
    const t = await ultimoTrabajo('orquestacion', x => x.accion === 'orq.status' && x.parametros.host === H.host && (x.parametros.instalacion || N.id) === N.id && ['exitoso', 'parcial'].includes(x.estado));
    if (t) { S.estadoOrq[clave] = { fecha: t.meta.fin, dominios: parsearStatus(t.lineas) }; enrutar(); return; }
  }
  if (!cred && !sinDominios) {
    const t = await ultimoTrabajo('orquestacion', x => x.accion === 'orq.verificar' && x.parametros.host === H.host && x.parametros.instalacion === N.id && x.estado !== 'fallido');
    if (t) { S.credOrq[clave] = { fecha: t.meta.fin, dominios: parsearCred(t.lineas) }; enrutar(); }
  }
};

/* ============================================================== USUARIOS */
PAGINAS.usuarios = async (cont) => {
  if (!S.usr) S.usr = (await api('/api/usuarios-wl')).dominios;
  const doms = S.usr;
  if (!doms.length) { cont.innerHTML = cabecera('Usuarios WebLogic') + '<div class="tarjeta vacio"><b>Sin dominios configurados</b>Agregue dominios en inventario/configuracion_modulos.json (usuarios_wl).</div>'; return; }
  if (!S.usrDom || !doms.find(d => d.id === S.usrDom)) S.usrDom = doms[0].id;
  const D = doms.find(d => d.id === S.usrDom);
  const lista = S.usuariosDom[D.id];
  cont.innerHTML = cabecera('Usuarios WebLogic', 'Gestion de usuarios del realm via WLST con credenciales cifradas. Las claves se envian por un canal cifrado y no se guardan en disco ni en los registros.') + `
    <div class="seleccion-host">${doms.map(d => `<button class="host-btn ${d.id === D.id ? 'activo' : ''}" data-d="${h(d.id)}"><b>${h(d.id)}</b><span class="muted peq">${h(d.host)} · ${h(d.admin_ip)}:${h(d.admin_puerto)}</span> ${d.es_prod ? '<span class="badge prod">PROD</span>' : '<span class="badge prim">NO PROD</span>'}</button>`).join('')}</div>
    <div class="rejilla r-21">
      <div class="tarjeta">
        <div class="tarjeta-cab"><h2>Acciones a aplicar</h2><button class="btn chico" id="u-add">${I.mas}Agregar fila</button></div>
        <div class="tabla-env"><table><thead><tr><th>Accion</th><th>Usuario</th><th>Password</th><th>Descripcion</th><th>Grupo</th><th></th></tr></thead><tbody id="u-filas"></tbody></table></div>
        <div class="acciones u-pie"><button class="btn prim" id="u-aplicar">${I.play}Aplicar acciones</button><span class="tenue peq">CREAR exige password de 8+ caracteres distinto al usuario. No se puede eliminar "weblogic".</span></div>
      </div>
      <div class="tarjeta">
        <h2>Dominio ${h(D.id)}</h2>
        <dl class="dl u-dl"><dt>Host</dt><dd>${h(D.host)}</dd><dt>AdminServer</dt><dd class="mono">t3s://${h(D.admin_ip)}:${h(D.admin_puerto)}</dd><dt>Realm</dt><dd>${h(D.realm)}</dd><dt>TLS</dt><dd class="mono peq">${h(D.tls_protocolos)}</dd><dt>Usuario SO</dt><dd class="mono">${h(D.usuario_so)}</dd></dl>
        ${D.nota ? `<div class="aviso info peq u-nota">${h(D.nota)}</div>` : ''}
        <div class="acciones"><button class="btn" id="u-listar">${I.refresh}Listar usuarios</button><button class="btn" id="u-prep">${I.escudo}Preparar dominio</button></div>
        <h3 class="u-h">Usuarios del realm</h3>
        <div id="u-lista">${lista ? `<p class="tenue peq">Listado de ${h(fecha(lista.fecha))}</p><table><thead><tr><th>Usuario</th><th>Grupos</th></tr></thead><tbody>${lista.usuarios.map(u => `<tr><td class="mono">${h(u.usuario)}</td><td>${h(u.grupos).split(', ').map(g => `<span class="chip ${g === 'Administrators' ? 'alerta' : ''}">${g}</span>`).join(' ')}</td></tr>`).join('')}</tbody></table>`
          : '<p class="muted peq">Use "Listar usuarios" para consultar el realm.</p>'}</div>
      </div>
    </div>
    <div id="u-panel"></div>`;
  $('.u-dl', cont).style.margin = '0 0 12px';
  const un = $('.u-nota', cont); if (un) un.style.marginBottom = '12px';
  $('.u-h', cont).style.marginTop = '18px';
  $('.u-pie', cont).style.marginTop = '14px';
  $('#u-panel', cont).style.marginTop = '16px';
  $$('.host-btn', cont).forEach(b => b.addEventListener('click', () => { S.usrDom = b.dataset.d; enrutar(); }));
  const tbody = $('#u-filas');
  const fila = () => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><select class="u-acc"><option>CREAR</option><option>ELIMINAR</option><option>ADMIN</option><option>GRUPO</option></select></td>
      <td><input type="text" class="u-usr" maxlength="64" placeholder="usuario"></td><td><input type="password" class="u-pwd" autocomplete="new-password" placeholder="solo CREAR"></td>
      <td><input type="text" class="u-desc" maxlength="120" placeholder="opcional"></td><td><input type="text" class="u-grp" maxlength="64" placeholder="p.ej. Monitors"></td>
      <td><button class="btn fantasma chico u-del" aria-label="Quitar">${I.x}</button></td>`;
    $('.u-del', tr).addEventListener('click', () => { tr.remove(); if (!tbody.children.length) fila(); });
    $('.u-acc', tr).addEventListener('change', (e) => { const c = e.target.value === 'CREAR'; $('.u-pwd', tr).disabled = !c; if (!c) $('.u-pwd', tr).value = ''; });
    tbody.appendChild(tr);
  };
  fila();
  $('#u-add').addEventListener('click', fila);
  const alListar = (meta, lineas) => {
    if (meta.estado === 'exitoso' || meta.estado === 'parcial') {
      const us = parsearUsuarios(lineas);
      if (us.length) { S.usuariosDom[D.id] = { fecha: meta.fin, usuarios: us }; const p = $('#u-panel').innerHTML; enrutar(); setTimeout(() => { const x = $('#u-panel'); if (x) x.innerHTML = p; }, 50); }
    } else toast('La ejecucion termino con estado ' + meta.estado, 'error');
  };
  $('#u-listar').addEventListener('click', () => ejecutarAccion('usr.listar', { dominio: D.id }, {}, { panel: $('#u-panel'), alTerminar: alListar }));
  $('#u-prep').addEventListener('click', async () => {
    const r = await modal({ titulo: 'Preparar dominio ' + D.id,
      cuerpo: `<p class="muted">Extrae el certificado del AdminServer, crea el truststore y genera las credenciales cifradas (storeUserConfig). Se hace una vez por dominio o al rotar la clave.</p>
        <label class="campo">Usuario administrador de consola<input type="text" id="p-u" value="${h(D.admin_usuario || '')}" autocomplete="off" placeholder="usuario admin de este dominio"></label>
        <label class="campo">Clave del administrador<input type="password" id="p-p" autocomplete="off"></label>
        <p class="tenue peq">La clave solo viaja por SSH hacia el servidor; no se guarda en el nodo central.</p>`,
      botones: [{ texto: 'Cancelar' }, { texto: 'Continuar', clase: 'prim', validar: (x) => !!($('#p-u', x).value.trim() && $('#p-p', x).value),
        valor: (x) => ({ admin_usuario: $('#p-u', x).value.trim(), admin_password: $('#p-p', x).value }) }] });
    if (r) ejecutarAccion('usr.preparar', { dominio: D.id }, r, { panel: $('#u-panel') });
  });
  $('#u-aplicar').addEventListener('click', () => {
    const acciones = $$('#u-filas tr').map(tr => ({ accion: $('.u-acc', tr).value, usuario: $('.u-usr', tr).value.trim(),
      password: $('.u-pwd', tr).value, descripcion: $('.u-desc', tr).value.trim(), grupo: $('.u-grp', tr).value.trim() })).filter(a => a.usuario);
    if (!acciones.length) { toast('Agregue al menos una accion con usuario', 'error'); return; }
    ejecutarAccion('usr.aplicar', { dominio: D.id }, { acciones }, { panel: $('#u-panel'), alTerminar: alListar,
      extra: `<table><thead><tr><th>Accion</th><th>Usuario</th><th>Grupo</th></tr></thead><tbody>${acciones.map(a => `<tr><td>${h(a.accion)}</td><td class="mono">${h(a.usuario)}</td><td>${h(a.grupo || (a.accion === 'ADMIN' ? 'Administrators' : ''))}</td></tr>`).join('')}</tbody></table>` })
      .then(t => { if (t) $$('.u-pwd').forEach(i => { i.value = ''; }); });
  });
};

/* ================================================================ FRMWEB */
PAGINAS.frmweb = async (cont) => {
  if (!S.frm) S.frm = (await api('/api/frmweb')).hosts;
  const hosts = S.frm;
  if (!hosts.length) { cont.innerHTML = cabecera('Forms: frmweb huerfanos') + '<div class="tarjeta vacio"><b>Sin hosts configurados</b></div>'; return; }
  if (!S.frmHost || !hosts.find(x => x.host === S.frmHost)) S.frmHost = hosts[0].host;
  const H = hosts.find(x => x.host === S.frmHost);
  cont.innerHTML = cabecera('Forms: frmweb huerfanos', 'Mitigacion temporal del alto consumo de CPU por procesos frmweb en bucle sin sesion, sin reiniciar los managed servers. Solo se terminan procesos en estado R y sin conexiones ESTABLISHED.') + `
    <div class="seleccion-host">${hosts.map(x => `<button class="host-btn ${x.host === H.host ? 'activo' : ''}" data-h="${h(x.host)}"><b>${h(x.host)}</b><span class="muted peq">${h(x.descripcion || '')}</span> ${x.es_prod ? '<span class="badge prod">PROD</span>' : ''}</button>`).join('')}</div>
    <div class="rejilla r-2">
      <div class="tarjeta"><h2>1. Validar (dry-run)</h2><p class="muted">Cuenta los procesos huerfanos por managed server sin terminar ninguno. Es de solo lectura.</p>
        <button class="btn prim" id="f-val">${I.lupa}Validar ahora</button></div>
      <div class="tarjeta"><h2>2. Mitigar</h2><p class="muted">Envia SIGTERM por lotes con pausa de 15 s y un barrido final con SIGKILL solo a los que sigan siendo huerfanos.</p>
        <div class="acciones"><label class="campo">Tamano de lote<select id="f-lote"><option value="10">10 (prudente)</option><option value="20" selected>20 (por defecto)</option><option value="40">40</option></select></label>
        <button class="btn peligro f-mit-b" id="f-mit">${I.stop}Mitigar</button></div></div>
    </div>
    <div class="tarjeta f-nota"><h3>Pendiente para la causa raiz</h3><ul class="lista-mini muted"><li>Configurar FORMS_TIMEOUT / heartBeat / sessionTimeout en formsweb.cfg</li><li>Revisar el modulo MIG de TRON (posible LOOP sin salida)</li><li>Reinicio rolling de ${h((H.managed_servers || []).join(', '))} en ventana de mantenimiento</li></ul></div>
    <div id="f-panel"></div>`;
  $('.f-nota', cont).style.marginTop = '16px';
  $('.f-mit-b', cont).style.alignSelf = 'flex-end';
  $('#f-panel', cont).style.marginTop = '16px';
  $$('.host-btn', cont).forEach(b => b.addEventListener('click', () => { S.frmHost = b.dataset.h; enrutar(); }));
  $('#f-val').addEventListener('click', () => ejecutarAccion('frm.validar', { host: H.host }, {}, { panel: $('#f-panel') }));
  $('#f-mit').addEventListener('click', () => ejecutarAccion('frm.mitigar', { host: H.host, lote: $('#f-lote').value }, {}, { panel: $('#f-panel'), peligro: true,
    titulo: 'Confirmar mitigacion en ' + H.host, textoBoton: 'Mitigar huerfanos',
    extra: '<div class="aviso info peq">Recomendado: ejecute primero la validacion (dry-run). Los procesos con conexion activa no se tocan.</div>' }));
};

/* ============================================================== TRABAJOS */
PAGINAS.trabajos = async (cont, args) => {
  if (args[0]) return detalleTrabajo(cont, args[0]);
  cont.innerHTML = cabecera('Ejecuciones', 'Historial de todas las acciones, con su evidencia completa.') + `
    <div class="tarjeta"><div class="filtros">
      <label class="campo">Estado<select id="t-est"><option value="">Todos</option>${Object.entries(ESTADOS).map(([k, v]) => `<option value="${k}">${v[1]}</option>`).join('')}</select></label>
      <label class="campo">Modulo<select id="t-mod"><option value="">Todos</option><option value="orquestacion">Orquestacion</option><option value="usuarios_wl">Usuarios WL</option><option value="frmweb">frmweb</option><option value="recoleccion">Recoleccion</option><option value="sistema">Sistema</option></select></label>
      <label class="check"><input type="checkbox" id="t-mios"> Solo mis ejecuciones</label>
    </div><div id="t-lista"></div></div>`;
  const cargar = async () => {
    const qs = new URLSearchParams({ limite: 300 });
    if ($('#t-est').value) qs.set('estado', $('#t-est').value);
    if ($('#t-mod').value) qs.set('modulo', $('#t-mod').value);
    if ($('#t-mios').checked) qs.set('usuario', S.sesion.usuario);
    const r = await api('/api/trabajos?' + qs);
    const l = $('#t-lista'); if (!l) return;
    l.innerHTML = tablaTrabajos(r.trabajos, false);
    enlazarFilasTrabajos(l);
  };
  ['#t-est', '#t-mod', '#t-mios'].forEach(id => $(id).addEventListener('change', cargar));
  await cargar();
  S.timers.push(setInterval(cargar, 4000));
};

async function detalleTrabajo(cont, id) {
  const r = await api('/api/trabajos/' + encodeURIComponent(id));
  const m = r.meta;
  cont.innerHTML = cabecera(m.nombre, h(m.resumen), `<a class="btn" href="#/trabajos">Volver</a><a class="btn" href="/api/trabajos/${h(m.id)}/log">${I.descarga}Descargar log</a>
     <button class="btn peligro oculto" id="d-cancel">${I.x}Cancelar</button>`) + `
    <div class="rejilla r-21">
      <div class="tarjeta"><div class="tarjeta-cab"><h2>Salida</h2><label class="check peq"><input type="checkbox" id="d-auto" checked> Seguir el final</label></div>
        <div class="consola" id="d-consola" tabindex="0"></div></div>
      <div class="tarjeta"><div class="tarjeta-cab"><h2>Trabajo</h2><span id="d-estado"></span></div>
        <dl class="dl"><dt>ID</dt><dd class="mono">${h(m.id)}</dd><dt>Accion</dt><dd class="mono">${h(m.accion)}</dd>
          <dt>Usuario</dt><dd>${h(m.usuario)} <span class="tenue">(${h(m.rol)})</span></dd><dt>Cambio</dt><dd class="mono">${h(m.cambio || '—')}</dd>
          <dt>Ambiente</dt><dd>${m.es_prod ? '<span class="badge prod">PROD / DRP</span>' : '<span class="badge prim">No productivo</span>'} ${m.simulacion ? '<span class="badge alerta">simulacion</span>' : ''}</dd>
          <dt>Creado</dt><dd>${h(fecha(m.creado))}</dd><dt>Duracion</dt><dd id="d-dur">${h(dur(m.duracion_s))}</dd>
          <dt>Hosts</dt><dd>${m.hosts.map(x => `<span class="chip">${h(x)}</span>`).join(' ')}</dd></dl>
        <h3 class="d-h">Pasos</h3><div class="pasos" id="d-pasos"></div>
        <h3 class="d-h">Parametros</h3><pre class="consola d-par">${h(JSON.stringify(m.parametros, null, 2))}</pre>
      </div>
    </div>`;
  $$('.d-h', cont).forEach(x => { x.style.marginTop = '18px'; });
  Object.assign($('.d-par', cont).style, { height: 'auto', maxHeight: '220px' });
  const consola = $('#d-consola');
  let desde = 0;
  const pintarMeta = (mm) => {
    $('#d-estado').innerHTML = badgeEstado(mm.estado);
    $('#d-dur').textContent = dur(mm.duracion_s);
    $('#d-pasos').innerHTML = mm.pasos.map(p => `<div class="paso"><span class="badge ${p.estado === 'ok' ? 'ok' : p.estado === 'error' ? 'error' : p.estado === 'ejecutando' ? 'info' : ''}">${h(p.estado)}</span><span class="t" title="${h(p.titulo)}">${h(p.titulo)}</span>${p.rc != null ? `<span class="tenue mono">rc=${h(p.rc)}</span>` : ''}</div>`).join('');
    const enCurso = ['en_cola', 'ejecutando'].includes(mm.estado);
    $('#d-cancel').classList.toggle('oculto', !(enCurso && (S.sesion.rol === 'admin' || S.sesion.usuario === mm.usuario)));
    return enCurso;
  };
  const agregar = (lineas) => {
    if (!lineas.length) return;
    consola.insertAdjacentHTML('beforeend', (consola.innerHTML ? '\n' : '') + lineasHtml(lineas));
    if ($('#d-auto').checked) consola.scrollTop = consola.scrollHeight;
  };
  agregar(r.lineas); desde = r.total;
  if (pintarMeta(m)) {
    const timer = setInterval(async () => {
      try {
        const x = await api('/api/trabajos/' + encodeURIComponent(id) + '?desde=' + desde);
        agregar(x.lineas); desde = x.total;
        if (!pintarMeta(x.meta)) clearInterval(timer);
      } catch (e) { clearInterval(timer); }
    }, 900);
    S.timers.push(timer);
  }
  $('#d-cancel').addEventListener('click', async () => {
    const ok = await modal({ titulo: 'Cancelar trabajo', cuerpo: '<p>Se terminara la sesion SSH en curso. Los pasos ya ejecutados no se revierten.</p>', botones: [{ texto: 'No' }, { texto: 'Cancelar trabajo', clase: 'peligro', valor: true }] });
    if (ok) { try { await api('/api/trabajos/' + id + '/cancelar', { method: 'POST' }); toast('Cancelacion solicitada'); } catch (e) { toast(e.message, 'error'); } }
  });
}

/* ========================================================= ADMINISTRACION */
PAGINAS.admin = async (cont) => {
  if (S.sesion.rol !== 'admin') { cont.innerHTML = '<div class="aviso error">Solo administradores.</div>'; return; }
  const [u, a] = await Promise.all([api('/api/usuarios-app'), api('/api/auditoria?n=300')]);
  cont.innerHTML = cabecera('Administracion', 'Usuarios de la plataforma, roles y registro de auditoria.', `<button class="btn" id="a-rec">${I.refresh}Recargar inventario</button>`) + `
    <div class="rejilla r-2">
      <div class="tarjeta"><h2>Usuarios de la plataforma</h2>
        <table><thead><tr><th>Usuario</th><th>Nombre</th><th>Rol</th></tr></thead><tbody>${u.usuarios.map(x => `<tr><td class="mono">${h(x.usuario)}</td><td>${h(x.nombre)}</td><td><span class="badge ${x.rol === 'admin' ? 'prod' : x.rol === 'operador' ? 'prim' : ''}">${h(x.rol)}</span></td></tr>`).join('')}</tbody></table>
        <p class="tenue peq">Alta o cambio de clave desde el servidor: <code>bin/cm usuario-app crear &lt;usuario&gt; --rol &lt;rol&gt;</code></p></div>
      <div class="tarjeta"><h2>Roles</h2><dl class="dl">${Object.entries(u.roles).map(([k, v]) => `<dt><span class="badge">${h(k)}</span></dt><dd>${h(v)}</dd>`).join('')}</dl>
        <p class="tenue peq">En PROD/DRP toda accion con cambios exige numero de cambio y queda auditada.</p></div>
    </div>
    <div class="tarjeta a-aud"><div class="tarjeta-cab"><h2>Auditoria</h2><span class="muted peq">ultimos ${a.eventos.length} eventos</span></div>
      <div class="tabla-env"><table><thead><tr><th>Fecha</th><th>Evento</th><th>Usuario</th><th>Detalle</th></tr></thead><tbody>
      ${a.eventos.map(e => { const { fecha: f, evento, usuario, ...resto } = e; return `<tr><td class="mono peq">${h(fecha(f))}</td><td><span class="badge ${/fallido|bloqueado|cancel/.test(evento) ? 'error' : /login|logout/.test(evento) ? '' : 'info'}">${h(evento)}</span></td><td>${h(usuario || '')}</td><td class="mono peq">${h(JSON.stringify(resto)).slice(0, 220)}</td></tr>`; }).join('')}
      </tbody></table></div></div>`;
  $('.a-aud', cont).style.marginTop = '16px';
  $('#a-rec').addEventListener('click', async () => {
    try { await api('/api/inventario/recargar', { method: 'POST' }); S.orq = S.usr = S.frm = null; toast('Inventario recargado'); } catch (e) { toast(e.message, 'error'); }
  });
};

/* ================================================================ INICIO */
(async () => {
  try {
    S.sesion = await api('/api/sesion');
    await arrancar();
  } catch (e) {
    if (e.message !== 'sesion') mostrarLogin();
  }
})();
