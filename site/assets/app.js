// Oferta Única: aplicación de una sola página con rutas por hash
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");

// ---------- utilidades ----------
const nfs = {};
const nf = (d) => (nfs[d] ||= new Intl.NumberFormat("es-ES", { maximumFractionDigits: d, minimumFractionDigits: d, useGrouping: "always" }));
const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : nf(d).format(v));
const eur = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `${fmt(v / 1e9, 2)} mil M€`;
  if (a >= 1e6) return `${fmt(v / 1e6, 1)} M€`;
  return `${fmt(v)} €`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dateEs = (d) => (d ? new Date(d + "T12:00:00").toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" }) : "—");
// Las fichas de la Plataforma se guardan abreviadas ("~" + identificador)
const DEEPLINK = "https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=";
const fullUrl = (u) => (u && u.startsWith("~") ? DEEPLINK + u.slice(1) : u);
const cache = {};
async function get(path) {
  if (!(path in cache)) cache[path] = fetch(`data/${path}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  return cache[path];
}
async function shard(kind, key, n) {
  // mismo hash que build.py: md5 → primeros 8 hex → módulo
  const h = md5(key).slice(0, 8);
  const s = String(parseInt(h, 16) % n).padStart(3, "0");
  const d = await get(`${kind}/${s}.json`);
  return d?.[key] || null;
}

// ---------- tema ----------
const KEY = "ou-theme";
try { const t = localStorage.getItem(KEY); if (t) document.documentElement.dataset.theme = t; } catch {}
$("#themeBtn").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem(KEY, document.documentElement.dataset.theme); } catch {}
});

// ---------- textos comunes ----------
const PROV = { "01": "Araba/Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila", "06": "Badajoz", "07": "Illes Balears", "08": "Barcelona", "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón", "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña", "16": "Cuenca", "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa", "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida", "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia", "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra", "37": "Salamanca", "38": "S. C. de Tenerife", "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria", "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "Valencia", "47": "Valladolid", "48": "Bizkaia", "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla" };
const IND = {
  I1: { t: "Oferta única", d: "Lotes de procedimientos competitivos que recibieron una sola oferta.", u: "%", good: "Más ofertas suele significar más competencia y mejor precio." },
  I2: { t: "Negociado sin publicidad", d: "Parte del importe adjudicado (sin contar menores ni emergencias) por negociado sin publicidad.", u: "%", good: "Es un procedimiento excepcional: solo se permite en supuestos tasados (art. 168 LCSP)." },
  I3: { t: "Menores pegados al umbral", d: "Contratos menores entre el 95% y el 100% del límite legal, por cada uno entre el 80% y el 95%.", u: "", good: "En una distribución sin efecto frontera ronda 0,33." },
  I4: { t: "Menores recurrentes", d: "Parte del importe de menores que va a la misma empresa, en el mismo sector y año, con 3 o más contratos que juntos superan el límite.", u: "%", good: "Puede indicar necesidades estables que deberían licitarse (arts. 99.2 y 118 LCSP)." },
  I5: { t: "Concentración de proveedores", d: "Índice Herfindahl-Hirschman del importe por proveedor (0 = muy repartido, 10.000 = un solo proveedor).", u: "", good: "Por encima de 2.500 se considera alta concentración." },
  I6: { t: "Plazos cortos", d: "Licitaciones abiertas no armonizadas y sin urgencia con un plazo para presentar ofertas menor que el mínimo legal.", u: "%", good: "Plazos muy cortos dificultan que se presenten empresas." },
};
const DISCLAIMER = `<div class="disclaimer"><strong>Indicador de riesgo, no acusación.</strong> Son señales estadísticas calculadas automáticamente con datos oficiales de la Plataforma de Contratación del Sector Público. No acreditan irregularidad, infracción ni delito: pueden deberse a causas legítimas (mercados con pocos proveedores, exclusividad técnica, emergencias) o a errores en los datos de origen. Rige la presunción de inocencia. ¿Hay un error? <a href="#/privacidad">Solicita una rectificación</a>.</div>`;

// ---------- gráficos ----------
function lineChart(points, { h = 200, yMax = null, fmtY = (v) => fmt(v), refs = [] } = {}) {
  const pts = points.filter((p) => p[1] != null);
  if (pts.length < 2) return `<div class="empty">Sin datos suficientes.</div>`;
  const W = 900, m = { t: 14, r: 12, b: 26, l: 78 };
  const max = yMax ?? Math.max(...pts.map((p) => p[1])) * 1.15;
  const x = (i) => m.l + (i / (points.length - 1)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - v / max) * (h - m.t - m.b);
  let s = `<svg viewBox="0 0 ${W} ${h}" role="img"><g class="grid">`;
  for (let k = 0; k <= 4; k++) { const v = (max * k) / 4; s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${fmtY(v)}</text>`; }
  s += `</g>`;
  refs.forEach(([v, label, color]) => { s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${color}" stroke-dasharray="4 4"/><text x="${W - m.r}" y="${y(v) - 5}" text-anchor="end" style="fill:${color}">${label}</text>`; });
  const path = points.map((p, i) => (p[1] == null ? null : `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`)).filter(Boolean);
  s += `<polyline points="${path.join(" ")}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>`;
  points.forEach((p, i) => { if (p[1] != null) s += `<circle cx="${x(i)}" cy="${y(p[1])}" r="3" fill="var(--accent)"><title>${p[0]}: ${fmtY(p[1])}</title></circle>`; });
  const step = Math.ceil(points.length / 8);
  points.forEach((p, i) => { if (i % step === 0 || i === points.length - 1) s += `<text x="${x(i)}" y="${h - 6}" text-anchor="middle">${p[0]}</text>`; });
  return `<div class="chart">${s}</svg></div>`;
}

function gauge(label, es, ue, verde, rojo, suf = "%", max = 50) {
  const pos = (v) => `${Math.min(100, (v / max) * 100)}%`;
  return `<div class="card"><h3>${label}</h3>
    <div class="gauge"><div class="track" style="--g:${pos(verde)};--r:${pos(rojo)}">
      <div class="mark ue" style="left:${pos(ue)}"><span>UE ${fmt(ue)}${suf}</span></div>
      <div class="mark" style="left:${pos(es)}"><span>España ${fmt(es)}${suf}</span></div>
    </div></div>
    <div class="gauge-legend"><span>Bien ≤ ${verde}${suf}</span><span>Mal ${label.includes("días") ? ">" : ">"} ${rojo}${suf}</span></div></div>`;
}

function sortable(el, cols, rows, sortKey, desc = true) {
  let key = sortKey, dir = desc ? -1 : 1;
  const draw = () => {
    const s = [...rows].sort((a, b) => { const x = a[key], y = b[key]; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y, "es") : x - y) * dir; });
    el.innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "r" : ""}" data-k="${c.k}" tabindex="0" ${c.k === key ? `aria-sort="${dir < 0 ? "descending" : "ascending"}"` : ""}>${c.l}</th>`).join("")}</tr></thead><tbody>${s.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "r" : ""}">${c.r ? c.r(r) : esc(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    el.querySelectorAll("th").forEach((th) => { const go = () => { const k = th.dataset.k; dir = k === key ? -dir : -1; key = k; draw(); }; th.onclick = go; th.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go()); });
  };
  draw();
}

// ---------- vistas ----------
async function viewHome() {
  const R = await get("resumen.json");
  if (!R) return (app.innerHTML = `<div class="empty">No se pudieron cargar los datos.</div>`);
  const years = Object.keys(R.anual).sort();
  const last = R.anual[years.at(-1)], prevY = years.at(-2);
  const ult = years.filter((y) => R.anual[y].oferta_unica != null).at(-1);
  const ou = R.anual[ult]?.oferta_unica;
  const provs = Object.entries(R.provincias).filter(([p, v]) => PROV[p] && v.oferta_unica != null);
  const color = (v) => `color-mix(in srgb, var(--accent) ${Math.min(90, Math.max(8, (v - 15) * 3))}%, var(--paper))`;
  app.innerHTML = `
    <section class="hero">
      <div class="big1" aria-hidden="true">1</div>
      <div>
        <p class="eyebrow">Competencia en la contratación pública</p>
        <h1>¿Cuántas empresas compitieron por este contrato?</h1>
        <p class="lede">En ${ult}, el <strong>${fmt(ou, 1)}%</strong> de los lotes licitados en España con procedimientos competitivos recibieron <strong>una sola oferta</strong>. Busca tu ayuntamiento, ministerio, hospital o universidad y compáralo con órganos similares.</p>
      </div>
    </section>
    <div class="search"><input id="q" type="search" placeholder="Busca un órgano de contratación: Ayuntamiento de…" aria-label="Buscar órgano de contratación" autocomplete="off"></div>
    <ul class="results" id="res" hidden></ul>
    <div class="kpis" style="margin-top:20px">
      <div class="kpi"><b>${fmt(R.filas)}</b><span>adjudicaciones analizadas (${years[0]}–${years.at(-1)})</span></div>
      <div class="kpi"><b>${fmt(R.organos)}</b><span>órganos con contratos en los últimos 12 meses</span></div>
      <div class="kpi"><b>${eur(last.importe)}</b><span>adjudicado en ${years.at(-1)} (hasta ${dateEs(R.hasta)})</span></div>
      <div class="kpi"><b>${fmt(R.anual[ult]?.nsp, 1)}%</b><span>del importe por negociado sin publicidad (${ult})</span></div>
    </div>
    <h2>España frente a la Unión Europea</h2>
    <p class="muted small">Indicadores del Single Market Scoreboard de la Comisión Europea (datos de 2024, contratos publicados en el Diario Oficial de la UE). <a href="${R.ue.url}">Fuente</a>.</p>
    <div class="grid3">
      ${gauge("Oferta única", R.ue.oferta_unica.es, R.ue.oferta_unica.ue, R.ue.oferta_unica.verde, R.ue.oferta_unica.rojo)}
      ${gauge("Adjudicación sin convocatoria", R.ue.sin_convocatoria.es, R.ue.sin_convocatoria.ue, R.ue.sin_convocatoria.verde, R.ue.sin_convocatoria.rojo, "%", 20)}
      ${gauge("Días hasta adjudicar", R.ue.dias_decision.es, R.ue.dias_decision.ue, R.ue.dias_decision.verde, R.ue.dias_decision.rojo, "", 200)}
    </div>
    <h2>Oferta única mes a mes</h2>
    <div class="card">${lineChart(R.meses.map((m) => [m[0], m[3]]), { yMax: 60, fmtY: (v) => `${fmt(v)}%`, refs: [[20, "umbral UE (20%)", "var(--bad)"]] })}
    <p class="muted small">Lotes de procedimientos competitivos (abierto, abierto simplificado, restringido, con negociación y diálogo), sin acuerdos marco. Meses con al menos 50 lotes.</p></div>
    <h2>Por provincia del órgano</h2>
    <p class="muted small">Porcentaje de lotes con una sola oferta en los últimos 12 meses. Cuanto más intenso el color, más oferta única.</p>
    <div class="provgrid">${provs.sort((a, b) => b[1].oferta_unica - a[1].oferta_unica).map(([p, v]) => `<a href="#/organos?prov=${p}" style="background:${color(v.oferta_unica)}"><span>${esc(PROV[p])}</span><b>${fmt(v.oferta_unica, 1)}%</b><span>${fmt(v.lotes)} lotes · ${eur(v.importe)}</span></a>`).join("")}</div>
    <h2>Calidad de los datos</h2>
    <p class="muted">${R.calidad.sin_numero_ofertas_pct ? `El ${fmt(R.calidad.sin_numero_ofertas_pct, 1)}% de los lotes competitivos de los últimos 12 meses no publica cuántas ofertas recibió; esos lotes no cuentan en el indicador.` : "Todos los lotes competitivos de los últimos 12 meses publican cuántas ofertas recibieron."} Se han descartado ${fmt(R.calidad.importe_dudoso_n)} importes imposibles (por ejemplo, más de 20 veces el presupuesto), que sumaban ${eur(R.calidad.importe_dudoso_eur)}: son errores de carga en origen y no cuentan en los totales. En ${years.at(-1)}, ${fmt(last.fisicas_n)} adjudicaciones fueron a personas físicas (autónomos): se incluyen en los totales pero nunca se muestran con nombre.</p>`;
  bindSearch();
}

let orgIndex = null;
async function bindSearch(initialProv) {
  orgIndex ||= await get("organos/index.json");
  const q = $("#q"), res = $("#res");
  const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const run = () => {
    const t = norm(q.value.trim());
    if (t.length < 3) { res.hidden = true; return; }
    const words = t.split(/\s+/);
    const hits = orgIndex.filter((o) => { const n = norm(o[1] || ""); return words.every((w) => n.includes(w)); }).slice(0, 30);
    res.hidden = false;
    res.innerHTML = hits.length ? hits.map((o) => `<li><a href="#/organo/${encodeURIComponent(o[0])}"><span>${esc(o[1])}<br><span class="muted small">${esc(PROV[o[2]] || "")}</span></span><span class="small muted">${fmt(o[5])} contratos · ${eur(o[6])}</span></a></li>`).join("") : `<li class="empty">Sin resultados.</li>`;
  };
  q.addEventListener("input", run);
}

async function viewOrganos(params) {
  orgIndex ||= await get("organos/index.json");
  const prov = params.get("prov");
  app.innerHTML = `<p class="eyebrow">Órganos de contratación</p><h1>${prov ? esc(PROV[prov]) : "Todos los órganos"}</h1>
    <p class="lede">Ayuntamientos, consejerías, ministerios, hospitales, universidades y empresas públicas con adjudicaciones en los últimos 12 meses.${prov ? ` <a href="#/organos">Ver toda España</a>` : ""}</p>
    <div class="search"><input id="q" type="search" placeholder="Filtra por nombre" aria-label="Filtrar órganos"><select id="prov" aria-label="Provincia"><option value="">Todas las provincias</option>${Object.entries(PROV).sort((a, b) => a[1].localeCompare(b[1], "es")).map(([c, n]) => `<option value="${c}" ${c === prov ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
    <div class="table-wrap" id="tbl"></div><p class="muted small">Se muestran los 300 primeros por importe. El porcentaje de oferta única solo aparece con al menos 10 lotes competitivos.</p>`;
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const draw = () => {
    const t = norm($("#q").value), p = $("#prov").value;
    const rows = orgIndex.filter((o) => (!p || o[2] === p) && (!t || norm(o[1]).includes(t))).slice(0, 300)
      .map((o) => ({ k: o[0], n: o[1], prov: PROV[o[2]] || "", c: o[5], imp: o[6], ou: o[7] }));
    sortable($("#tbl"), [
      { k: "n", l: "Órgano", r: (r) => `<a href="#/organo/${encodeURIComponent(r.k)}">${esc(r.n)}</a>` },
      { k: "prov", l: "Provincia" },
      { k: "c", l: "Contratos", num: true, r: (r) => fmt(r.c) },
      { k: "imp", l: "Importe", num: true, r: (r) => eur(r.imp) },
      { k: "ou", l: "Oferta única", num: true, r: (r) => (r.ou == null ? "—" : `${fmt(r.ou, 1)}%`) },
    ], rows, "imp");
  };
  $("#q").addEventListener("input", draw);
  $("#prov").addEventListener("change", (e) => { location.hash = e.target.value ? `#/organos?prov=${e.target.value}` : "#/organos"; });
  draw();
}

function indCard(id, x, R) {
  const I = IND[id];
  if (!x || x.v == null) return `<div class="card ind"><h3>${I.t}</h3><p class="muted small">${I.d}</p><span class="pill">Sin datos</span></div>`;
  const val = id === "I3" ? fmt(x.v, 2) : id === "I5" ? fmt(x.v) : `${fmt(x.v, 1)}<small>%</small>`;
  let state;
  if (!x.suficiente) state = `<span class="pill">Pocos datos (n = ${fmt(x.n)})</span>`;
  else if (x.atipico) state = `<span class="pill bad">Atípico: percentil ${x.pct}</span>`;
  else state = `<span class="pill ok">En rango: percentil ${x.pct}</span>`;
  const extra = id === "I1" && x.ic ? `${fmt(x.k)} de ${fmt(x.n)} lotes · intervalo 95%: ${fmt(x.ic[0], 1)}–${fmt(x.ic[1], 1)}%`
    : id === "I2" ? `${eur(x.importe)} por negociado sin publicidad${x.emergencia ? ` · además ${eur(x.emergencia)} por emergencia` : ""}`
    : id === "I3" ? `${fmt(x.cerca)} menores entre el 95% y el 100% del límite; ${fmt(x.medio)} entre el 80% y el 95%`
    : id === "I4" ? `${fmt(x.parejas)} combinaciones empresa-sector-año con 3 o más menores por encima del límite`
    : id === "I5" ? `${fmt(x.proveedores)} proveedores · el primero se lleva el ${fmt(x.top1, 1)}% y los tres primeros el ${fmt(x.top3, 1)}%`
    : id === "I6" ? `${fmt(x.k)} de ${fmt(x.n)} licitaciones con plazo por debajo del mínimo` : "";
  return `<div class="card ind"><h3>${I.t}</h3><div class="v">${val}</div>${state}
    ${x.suficiente ? `<div class="pct-bar" title="Posición entre ${fmt(x.pares)} órganos similares"><b></b><i style="left:${x.pct}%"></i></div><span class="muted small">Posición entre ${fmt(x.pares)} órganos similares (la línea roja marca el percentil 90)</span>` : ""}
    <p class="small" style="margin:0">${extra}</p><p class="muted small" style="margin:0">${I.d} ${I.good}</p></div>`;
}

async function viewOrgano(key) {
  const [o, R] = await Promise.all([shard("organos", key, 128), get("resumen.json")]);
  if (!o) return (app.innerHTML = `<div class="empty">No encontramos ese órgano en los últimos 12 meses. <a href="#/organos">Volver</a></div>`);
  document.title = `${o.nombre} · Oferta Única`;
  const I = o.indicadores;
  app.innerHTML = `<p class="eyebrow">${esc(R.grupos[o.grupo] || "")} · ${esc(PROV[o.prov] || "")} · tamaño ${esc(o.tamano)}</p>
    <h1>${esc(o.nombre)}</h1>
    <div class="kpis"><div class="kpi"><b>${fmt(o.n)}</b><span>adjudicaciones en 12 meses</span></div><div class="kpi"><b>${eur(o.importe)}</b><span>importe adjudicado (sin IVA)</span></div>
    <div class="kpi"><b>${fmt(o.atipicos)} de 6</b><span>indicadores atípicos frente a órganos similares</span></div>
    <div class="kpi"><b>${o.calidad_sin_ofertas == null ? "—" : fmt(o.calidad_sin_ofertas, 1) + "%"}</b><span>lotes competitivos sin publicar el número de ofertas</span></div></div>
    ${DISCLAIMER}
    <h2>Indicadores de competencia</h2>
    <p class="muted small">Se compara con órganos del mismo tipo de administración y tamaño. «Atípico» significa percentil 90 o más y por encima del umbral de referencia. Periodo: ${dateEs(R.ventana_desde)} – ${dateEs(R.hasta)}.</p>
    <div class="grid3">${["I1", "I2", "I6", "I5", "I3", "I4"].map((i) => indCard(i, I[i], R)).join("")}</div>
    <div class="grid2">
      <div><h2>Principales empresas adjudicatarias</h2><div class="table-wrap" id="prov"></div>
      <p class="muted small">Solo personas jurídicas. ${o.anonimizado_importe ? `Además, ${eur(o.anonimizado_importe)} se adjudicaron a personas físicas o sin identificar, que no se muestran.` : ""}</p></div>
      <div><h2>Procedimientos</h2><div class="table-wrap" id="proc"></div></div>
    </div>
    <h2>Importe adjudicado por mes</h2><div class="card">${lineChart(o.meses, { fmtY: (v) => eur(v) })}</div>
    <h2>Últimas adjudicaciones</h2><div class="table-wrap" id="rec"></div>`;
  sortable($("#prov"), [
    { k: "nombre", l: "Empresa", r: (r) => `<a href="#/empresa/${encodeURIComponent(r.nif)}">${esc(r.nombre)}</a>` },
    { k: "importe", l: "Importe", num: true, r: (r) => eur(r.importe) },
    { k: "n", l: "Contratos", num: true, r: (r) => fmt(r.n) },
    { k: "unica", l: "Con oferta única", num: true, r: (r) => fmt(r.unica) },
  ], o.proveedores, "importe");
  sortable($("#proc"), [
    { k: 1, l: "Procedimiento" }, { k: 2, l: "Contratos", num: true, r: (r) => fmt(r[2]) }, { k: 3, l: "Importe", num: true, r: (r) => eur(r[3]) },
  ], o.procedimientos, 3);
  sortable($("#rec"), [
    { k: "f", l: "Fecha", r: (r) => dateEs(r.f) },
    { k: "o", l: "Objeto", r: (r) => (r.u ? `<a href="${esc(fullUrl(r.u))}" rel="noopener" target="_blank">${esc(r.o || "Sin descripción")}</a>` : esc(r.o)) },
    { k: "p", l: "Procedimiento" },
    { k: "of", l: "Ofertas", num: true, r: (r) => (r.of == null ? "—" : r.of === 1 ? `<span class="pill bad">1</span>` : fmt(r.of)) },
    { k: "i", l: "Importe", num: true, r: (r) => eur(r.i) },
    { k: "a", l: "Adjudicataria", r: (r) => (r.nif ? `<a href="#/empresa/${encodeURIComponent(r.nif)}">${esc(r.a)}</a>` : esc(r.a || "—")) },
  ], o.recientes, "f");
}

async function viewEmpresas() {
  const top = await get("empresas/top.json");
  app.innerHTML = `<p class="eyebrow">Adjudicatarias</p><h1>Empresas</h1>
    <p class="lede">Las 1.000 empresas con más importe adjudicado en los últimos 12 meses. Solo personas jurídicas: los autónomos y otras personas físicas no se muestran nunca.</p>
    <div class="search"><input id="q" type="search" placeholder="Busca una empresa por nombre o NIF" aria-label="Buscar empresa"></div>
    <ul class="results" id="res" hidden></ul>
    <div class="table-wrap" id="tbl" style="margin-top:16px"></div>`;
  sortable($("#tbl"), [
    { k: 1, l: "Empresa", r: (r) => `<a href="#/empresa/${encodeURIComponent(r[0])}">${esc(r[1])}</a>` },
    { k: 2, l: "Importe", num: true, r: (r) => eur(r[2]) },
    { k: 3, l: "Contratos", num: true, r: (r) => fmt(r[3]) },
    { k: 4, l: "Órganos", num: true, r: (r) => fmt(r[4]) },
    { k: 5, l: "% con oferta única", num: true, r: (r) => (r[5] == null ? "—" : `${fmt(r[5], 1)}%`) },
  ], top || [], 2);
  const all = await get("empresas/buscar.json");
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  $("#q").addEventListener("input", () => {
    const t = norm($("#q").value.trim());
    if (t.length < 3) return ($("#res").hidden = true);
    const hits = all.filter((e) => e[0].toLowerCase() === t || norm(e[1]).includes(t)).slice(0, 30);
    $("#res").hidden = false;
    $("#res").innerHTML = hits.map((e) => `<li><a href="#/empresa/${encodeURIComponent(e[0])}"><span>${esc(e[1])}</span><span class="muted small">${esc(e[0])}</span></a></li>`).join("") || `<li class="empty">Sin resultados.</li>`;
  });
}

async function viewEmpresa(nif) {
  const e = await shard("empresas", nif, 256);
  if (!e) return (app.innerHTML = `<div class="empty">No hay adjudicaciones de esa empresa en los últimos 12 meses. <a href="#/empresas">Volver</a></div>`);
  document.title = `${e.nombre} · Oferta Única`;
  app.innerHTML = `<p class="eyebrow">Empresa adjudicataria · NIF ${esc(nif)}</p><h1>${esc(e.nombre)}</h1>
    <div class="kpis"><div class="kpi"><b>${eur(e.importe)}</b><span>adjudicado en 12 meses (sin IVA)</span></div><div class="kpi"><b>${fmt(e.n)}</b><span>adjudicaciones</span></div>
    <div class="kpi"><b>${e.comp ? fmt((100 * e.unica) / e.comp, 1) + "%" : "—"}</b><span>de sus lotes competitivos con una sola oferta (${fmt(e.unica)} de ${fmt(e.comp)})</span></div></div>
    ${DISCLAIMER.replace("Indicador de riesgo, no acusación.", "Datos, no acusación.")}
    <div class="grid2"><div><h2>Órganos que la contratan</h2><div class="table-wrap" id="orgs"></div></div>
    <div><h2>Últimas adjudicaciones</h2><div class="table-wrap" id="rec"></div></div></div>`;
  sortable($("#orgs"), [
    { k: 1, l: "Órgano", r: (r) => `<a href="#/organo/${encodeURIComponent(r[0])}">${esc(r[1])}</a>` }, { k: 2, l: "Importe", num: true, r: (r) => eur(r[2]) },
  ], e.organos, 2);
  sortable($("#rec"), [
    { k: "f", l: "Fecha", r: (r) => dateEs(r.f) },
    { k: "o", l: "Objeto", r: (r) => (r.u ? `<a href="${esc(fullUrl(r.u))}" rel="noopener" target="_blank">${esc(r.o || "Sin descripción")}</a>` : esc(r.o)) },
    { k: "org", l: "Órgano", r: (r) => `<a href="#/organo/${encodeURIComponent(r.k)}">${esc(r.org)}</a>` },
    { k: "of", l: "Ofertas", num: true, r: (r) => (r.of == null ? "—" : r.of === 1 ? `<span class="pill bad">1</span>` : fmt(r.of)) },
    { k: "i", l: "Importe", num: true, r: (r) => eur(r.i) },
  ], e.recientes, "f");
}

async function viewStatic(name) {
  const html = await fetch(`${name}.html`).then((r) => r.text()).catch(() => "");
  app.innerHTML = html || `<div class="empty">Página no disponible.</div>`;
}

// ---------- rutas ----------
async function route() {
  const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  const params = new URLSearchParams(qs || "");
  document.querySelectorAll("nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.r === (parts[0] === "organo" ? "organos" : parts[0] === "empresa" ? "empresas" : parts[0])));
  document.title = "Oferta Única · Competencia en la contratación pública";
  app.innerHTML = `<p class="muted">Cargando…</p>`;
  window.scrollTo(0, 0);
  try {
    if (parts[0] === "organo") await viewOrgano(decodeURIComponent(parts.slice(1).join("/")));
    else if (parts[0] === "organos") await viewOrganos(params);
    else if (parts[0] === "empresa") await viewEmpresa(decodeURIComponent(parts[1]));
    else if (parts[0] === "empresas") await viewEmpresas();
    else if (parts[0] === "metodologia") await viewStatic("metodologia");
    else if (parts[0] === "privacidad") await viewStatic("privacidad");
    else await viewHome();
  } catch (e) {
    console.error(e);
    app.innerHTML = `<div class="empty">Algo ha fallado al mostrar esta página.</div>`;
  }
  const R = await get("resumen.json");
  if (R) $("#footDate").textContent = `Datos actualizados el ${dateEs(R.hasta)} · metodología v${R.version}.`;
}
window.addEventListener("hashchange", route);
route();

// ---------- md5 (para localizar el fichero de cada órgano o empresa) ----------
function md5(str) {
  const k = [], s = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  for (let i = 0; i < 64; i++) k[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0;
  const bytes = new TextEncoder().encode(str);
  const len = bytes.length, nBlocks = ((len + 8) >> 6) + 1, w = new Uint32Array(nBlocks * 16);
  for (let i = 0; i < len; i++) w[i >> 2] |= bytes[i] << ((i % 4) * 8);
  w[len >> 2] |= 0x80 << ((len % 4) * 8);
  w[nBlocks * 16 - 2] = (len * 8) >>> 0;
  w[nBlocks * 16 - 1] = Math.floor((len * 8) / 2 ** 32);
  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  for (let b = 0; b < nBlocks; b++) {
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F, g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      const tmp = D; D = C; C = B;
      const sum = (A + F + k[i] + w[b * 16 + g]) >>> 0;
      const r = s[(i >> 4) * 4 + (i % 4)];
      B = (B + ((sum << r) | (sum >>> (32 - r)))) >>> 0;
      A = tmp;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0; c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }
  return [a0, b0, c0, d0].map((x) => [0, 8, 16, 24].map((sh) => ((x >>> sh) & 255).toString(16).padStart(2, "0")).join("")).join("");
}
