#!/usr/bin/env python3
"""Calcula indicadores y genera los JSON de la web a partir del estado SQLite.

Uso: python scripts/build.py --db work/state.sqlite --out site/data

Indicadores por órgano de contratación (ventana: últimos 12 meses por fecha de adjudicación):
  I1 Oferta única            lotes con 1 oferta / lotes con ≥1 oferta, procedimientos competitivos
  I2 Negociado sin publicidad importe NSP (sin emergencias) / importe no menor
  I3 Menores bajo el umbral   menores en [95%, 100%) del umbral / menores en [80%, 95%)
  I4 Menores recurrentes      % del importe en menores en parejas órgano-empresa-sector con suma anual > umbral
  I5 Concentración            índice Herfindahl-Hirschman (0-10.000) del importe por proveedor
  I6 Plazos cortos            % de licitaciones abiertas no armonizadas con plazo inferior al mínimo legal
  Q  Calidad de los datos     % de lotes competitivos sin número de ofertas
Cada órgano se compara con órganos del mismo tipo y tamaño (percentil). Ver METODOLOGIA.md.
"""
from __future__ import annotations

import argparse
import re
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

VERSION = "1.0"
COMPETITIVOS = {"1", "2", "4", "5", "9", "13"}
SIN_AM = {None, "", "0"}  # excluye establecimiento y derivados de acuerdos marco y SDA
PROC_TXT = {"1": "Abierto", "2": "Restringido", "3": "Negociado sin publicidad", "4": "Negociado con publicidad",
            "5": "Diálogo competitivo", "6": "Contrato menor", "7": "Derivado de acuerdo marco",
            "8": "Concurso de proyectos", "9": "Abierto simplificado", "10": "Asociación para la innovación",
            "12": "Sistema dinámico de adquisición", "13": "Licitación con negociación", "100": "Normas internas",
            "999": "Otros"}
TIPO_TXT = {"1": "Suministros", "2": "Servicios", "3": "Obras", "21": "Gestión de servicios públicos",
            "22": "Concesión de servicios", "31": "Concesión de obras públicas", "32": "Concesión de obras",
            "40": "Colaboración público-privada", "7": "Administrativo especial", "8": "Privado", "50": "Patrimonial"}
ADMIN_GRUPO = {"1": "estatal", "4": "estatal", "6": "estatal", "9": "estatal", "2": "autonomica", "7": "autonomica",
               "10": "autonomica", "3": "local", "8": "local", "11": "local", "5": "otras", "12": "otras"}
GRUPO_TXT = {"estatal": "Administración General del Estado y sus organismos", "autonomica": "Comunidades autónomas y sus organismos",
             "local": "Entidades locales y sus organismos", "otras": "Otras entidades del sector público", "?": "Sin clasificar"}
MIN_N = {"I1": 10, "I2": 10, "I3": 10, "I4": 20, "I5": 10, "I6": 10}
N_SHARDS_ORG, N_SHARDS_EMP = 128, 256


def shard(key: str, n: int) -> str:
    return f"{int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % n:03d}"


def wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * max(0, c - h), 1), round(100 * min(1, c + h), 1)]


DEEPLINK = "https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl="


def short_url(u: str | None) -> str | None:
    """Las fichas de la Plataforma se guardan solo con su identificador ("~" + idEvl)."""
    if u and u.startswith(DEEPLINK):
        return "~" + u[len(DEEPLINK):]
    return u


RE_NEXTGEN = re.compile(r"NEXT\s*GENERATION|PRTR|RECUPERACI[OÓ]N|RESILIENCIA|MRR|\bC\d{2}\.I\d{2}", re.I)
FONDOS_OTROS = ["FEDER", "FSE+", "FEADER", "FEMPA", "OFE"]


def categoria_fondos(codigos: str | None, texto: str | None) -> str:
    """Fondo que financia el contrato según la Plataforma: PRTR (Next Generation), otro fondo UE, sin fondos o sin dato."""
    cs = set((codigos or "").split("|")) - {""}
    if "PRTR" in cs or (texto and RE_NEXTGEN.search(texto)):
        return "PRTR"
    for f in FONDOS_OTROS:
        if f in cs:
            return f
    if "EU" in cs:
        return "UE sin especificar"
    if "NO-EU" in cs:
        return "Sin fondos UE"
    return "Sin dato"


# Contratos de inteligencia artificial: se detectan por el objeto del contrato (el CPV no distingue la IA)
RE_IA = re.compile(r"INTELIGENCIA\s+ARTIFICIAL|\bI\.?A\.?\s+GENERATIVA|MACHINE\s+LEARNING|APRENDIZAJE\s+(?:AUTOM[AÁ]TICO|PROFUNDO)|DEEP\s+LEARNING|"
                   r"CHAT\s*BOT|ASISTENTES?\s+VIRTUAL|CHAT\s*GPT|\bGPT\b|COPILOT|\bLLMS?\b|MODELOS?\s+(?:DE\s+)?LENGUAJE|VISI[OÓ]N\s+ARTIFICIAL|"
                   r"RECONOCIMIENTO\s+FACIAL|PROCESAMIENTO\s+DEL?\s+LENGUAJE\s+NATURAL|REDES\s+NEURONALES|ANAL[IÍ]TICA\s+PREDICTIVA", re.I)
RE_IA_SIGLA = re.compile(r"\bIA\b")  # la sigla, solo en mayúsculas
IA_CAT = [
    ("Formación", re.compile(r"FORMACI|CURSO|TALLER|JORNADA|CAPACITACI|PONENCIA|CONFERENCIA|SEMINARIO|CHARLA|WEBINAR|M[AÁ]STER|DIPLOMA|LIBRO", re.I)),
    ("Licencias y suscripciones", re.compile(r"LICENCIA|SUSCRIPCI|SUBSCRIPCI|ABONO|RENOVACI|CHAT\s*GPT|\bGPT\b|COPILOT|PLUS\b|\bPRO\b", re.I)),
    ("Asistentes y chatbots", re.compile(r"CHAT\s*BOT|ASISTENTES?\s+VIRTUAL|ASISTENTE\s+CONVERSACIONAL|AGENTE\s+CONVERSACIONAL", re.I)),
    ("Consultoría, estudios y estrategia", re.compile(r"CONSULTOR|ASISTENCIA\s+T[EÉ]CNICA|ESTRATEGIA|ESTUDIO|INFORME|AN[AÁ]LISIS|OFICINA\s+T[EÉ]CNICA|ASESORAMIENTO|AUDITOR", re.I)),
    ("Desarrollo e implantación", re.compile(r"DESARROLL|IMPLANTACI|IMPLEMENTACI|PLATAFORMA|SOLUCI[OÓ]N|SISTEMA|HERRAMIENTA|APLICACI|SOFTWARE|MODELO|PROYECTO|MANTENIMIENTO|SERVICIO|SUMINISTRO|ADQUISICI|EVOLUTIV|PILOTO|INFRAESTRUCTURA|SERVIDOR|GPU", re.I)),
]


def es_ia(objeto: str | None) -> bool:
    return bool(objeto) and bool(RE_IA.search(objeto) or RE_IA_SIGLA.search(objeto))


def categoria_ia(objeto: str) -> str:
    for nombre, rx in IA_CAT:
        if rx.search(objeto):
            return nombre
    return "Otros"


def umbral(tipo: str | None) -> int:
    return 40000 if tipo == "3" else 15000


def dias(a: str | None, b: str | None):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def plazo_bajo_minimo(r) -> bool | None:
    """Plazo inferior al mínimo legal (LCSP arts. 156 y 159) en licitaciones abiertas no armonizadas y sin urgencia.

    Abierto no SARA: 15 días (26 en obras). Abierto simplificado: 10 días como mínimo absoluto
    (el abreviado del art. 159.6 permite 10 hábiles, que nunca son menos de 10 naturales).
    """
    if r["sara"] == "true" or r["urgencia"] in ("2", "3"):
        return None
    d = dias(r["fecha_pub"], r["fin_ofertas"])
    if d is None or d < 0 or d > 365:
        return None
    if r["procedimiento"] == "1":
        return d < (26 if r["tipo_contrato"] == "3" else 15)
    if r["procedimiento"] == "9":
        return d < 10
    return None


def percentile_rank(values: list[float], v: float) -> int:
    if not values:
        return None
    below = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    return round(100 * (below + 0.5 * equal) / len(values))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hasta", default=None, help="fecha de corte AAAA-MM-DD (por defecto hoy)")
    ap.add_argument("--desde", default="2023-01-01", help="primera fecha de adjudicación cubierta por los datos cargados")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "organos").mkdir(parents=True, exist_ok=True)
    (out / "empresas").mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(a.db)
    db.row_factory = sqlite3.Row
    hasta = date.fromisoformat(a.hasta) if a.hasta else date.today()
    desde12 = (hasta - timedelta(days=365)).isoformat()
    hasta_s = hasta.isoformat()

    # Filas vigentes: excluye expedientes anulados después de su última versión
    q = """SELECT r.* FROM rows r JOIN entries e ON e.k = r.entry_id
           LEFT JOIN tombstones t ON t.id = e.id
           WHERE (t.id IS NULL OR t.at < e.updated) AND r.fecha_adj >= ? AND r.fecha_adj <= ?"""

    org = defaultdict(lambda: {"lotes": {}, "nsp": 0.0, "nsp_emerg": 0.0, "nomenor": 0.0, "menores": [],
                               "prov_imp": Counter(), "proc": Counter(), "proc_imp": Counter(), "cpv": Counter(),
                               "meses": Counter(), "recientes": [], "plazo": [0, 0], "importe": 0.0, "n": 0,
                               "sin_ofertas": 0, "comp": 0, "prov_n": Counter(), "prov_unica": Counter()})
    org_meta = {}
    emp = defaultdict(lambda: {"importe": 0.0, "n": 0, "unica": 0, "comp": 0, "organos": Counter(), "recientes": []})
    emp_name = {}
    nat_year = defaultdict(lambda: {"lotes": 0, "unica": 0, "nsp": 0.0, "nomenor": 0.0, "nsp_n": 0, "nomenor_n": 0, "importe": 0.0, "n": 0,
                                    "menores_imp": 0.0, "menores_n": 0, "fisicas_n": 0, "fisicas_imp": 0.0})
    nat_mes = defaultdict(lambda: {"n": 0, "importe": 0.0, "lotes": 0, "unica": 0})
    prov_stats = defaultdict(lambda: {"lotes": 0, "unica": 0, "nsp": 0.0, "nomenor": 0.0, "importe": 0.0, "n": 0})
    calidad = Counter()
    seen_lotes = set()  # un lote con varios adjudicatarios cuenta una sola vez
    # Fondos europeos (todo el periodo, no solo 12 meses)
    fondos_anual = defaultdict(lambda: defaultdict(lambda: {"n": 0, "importe": 0.0}))
    fondos_mes = defaultdict(lambda: {"n": 0, "importe": 0.0})
    fondos_comp = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # año -> grupo -> [lotes, oferta única]
    fondos_prov = defaultdict(lambda: {"n": 0, "importe": 0.0})
    fondos_org = defaultdict(lambda: {"n": 0, "importe": 0.0, "lotes": 0, "unica": 0})
    fondos_emp = defaultdict(lambda: {"n": 0, "importe": 0.0, "organos": set()})
    fondos_cpv = Counter()
    fondos_proc = Counter()
    # Inteligencia artificial (todo el periodo)
    ia_anual = defaultdict(lambda: {"n": 0, "importe": 0.0, "menores_n": 0, "menores_imp": 0.0})
    ia_mes = defaultdict(lambda: {"n": 0, "importe": 0.0})
    ia_comp = defaultdict(lambda: [0, 0])
    ia_cat = defaultdict(lambda: {"n": 0, "importe": 0.0})
    ia_grupo = defaultdict(lambda: {"n": 0, "importe": 0.0})
    ia_prov = defaultdict(lambda: {"n": 0, "importe": 0.0})
    ia_org = defaultdict(lambda: {"n": 0, "importe": 0.0, "lotes": 0, "unica": 0})
    ia_emp = defaultdict(lambda: {"n": 0, "importe": 0.0, "organos": set()})
    ia_proc = Counter()
    ia_prtr = {"n": 0, "importe": 0.0}
    ia_marcas = Counter()
    ia_top = []  # (importe, fila)
    ia_lotes = set()
    ia_vistos = set()
    tiene_fondos = "fondos" in [c[1] for c in db.execute("PRAGMA table_info(rows)")]
    n_rows = 0

    for r in db.execute(q, (a.desde, hasta_s)):
        n_rows += 1
        # Varios órganos pueden compartir código DIR3 o NIF (p. ej. alcaldía y patronatos de un ayuntamiento):
        # cada órgano se identifica por su código y el nombre con el que publica
        nombre = (r["organo"] or "").strip()
        base_k = r["organo_key"] or "sin-id"
        k = f"{base_k}~{hashlib.md5(nombre.lower().encode()).hexdigest()[:6]}"
        y = r["fecha_adj"][:4]
        mes = r["fecha_adj"][:7]
        en12 = r["fecha_adj"] >= desde12
        menor = r["src"] == "1143" or r["procedimiento"] == "6"
        imp = r["importe"]
        if imp is None and r["importe_iva"] is not None:
            imp = r["importe_iva"] / 1.21
        # Importes imposibles (errores de carga en origen): no suman y se cuentan como problema de calidad
        pres = r["presupuesto"]
        if imp is not None and ((pres and imp > 20 * pres and imp > 1e6) or (menor and imp > 2 * umbral(r["tipo_contrato"]))):
            calidad["importe_dudoso"] += 1
            calidad["importe_dudoso_eur"] += imp
            imp = None
        imp_eff = (imp or 0.0) / (r["n_res_lote"] or 1)
        gasto = 0.0 if r["sistema"] == "1" else imp_eff  # el establecimiento de un acuerdo marco es un techo, no gasto
        comp = (not menor) and r["procedimiento"] in COMPETITIVOS and r["sistema"] in SIN_AM
        lote_id = f"{r['entry_id']}|{r['lote'] or ''}"
        fis = r["adj_tipo"] in ("fisica", "cb_sc")

        # nacional por año y mes
        ny = nat_year[y]
        ny["n"] += 1
        ny["importe"] += gasto
        if fis:
            ny["fisicas_n"] += 1
            ny["fisicas_imp"] += gasto
        if menor:
            ny["menores_n"] += 1
            ny["menores_imp"] += gasto
        else:
            ny["nomenor"] += gasto
            ny["nomenor_n"] += 1
            if r["procedimiento"] == "3" and r["urgencia"] != "3":
                ny["nsp"] += gasto
                ny["nsp_n"] += 1
        nm = nat_mes[mes]
        nm["n"] += 1
        nm["importe"] += gasto

        if r["organo"]:
            org_meta[k] = (r["organo"], r["tipo_admin"], r["prov"], r["ccaa"])
        nuevo_lote = comp and r["n_ofertas"] and lote_id not in seen_lotes
        if nuevo_lote:
            seen_lotes.add(lote_id)
            ny["lotes"] += 1
            ny["unica"] += r["n_ofertas"] == 1
            nm["lotes"] += 1
            nm["unica"] += r["n_ofertas"] == 1
        if tiene_fondos:
            cat = categoria_fondos(r["fondos"], r["fondos_txt"])
            fa = fondos_anual[y][cat]
            fa["n"] += 1
            fa["importe"] += gasto
            grupo = "PRTR" if cat == "PRTR" else "Sin fondos UE" if cat == "Sin fondos UE" else "Otros fondos UE" if cat != "Sin dato" else None
            if nuevo_lote and grupo:
                fc = fondos_comp[y][grupo]
                fc[0] += 1
                fc[1] += r["n_ofertas"] == 1
            if cat == "PRTR":
                fondos_mes[mes]["n"] += 1
                fondos_mes[mes]["importe"] += gasto
                fp = fondos_prov[r["prov"] or "??"]
                fp["n"] += 1
                fp["importe"] += gasto
                fo = fondos_org[k]
                fo["n"] += 1
                fo["importe"] += gasto
                if nuevo_lote:
                    fo["lotes"] += 1
                    fo["unica"] += r["n_ofertas"] == 1
                if r["adj_nif"] and not fis:
                    fe = fondos_emp[r["adj_nif"]]
                    fe["n"] += 1
                    fe["importe"] += gasto
                    fe["organos"].add(k)
                    if r["adj_nombre"]:
                        emp_name.setdefault(r["adj_nif"], r["adj_nombre"])
                if r["cpv"]:
                    fondos_cpv[r["cpv"][:2]] += gasto
                fondos_proc[r["procedimiento"] or "?"] += 1
        ia_dup = False
        if es_ia(r["objeto"]) and gasto >= 50000:
            # un mismo contrato publicado dos veces por órganos distintos (p. ej. tras reestructurar un ministerio)
            clave = (re.sub(r"\W+", "", r["objeto"].lower())[:150], round(gasto), r["adj_nif"])
            ia_dup = clave in ia_vistos
            ia_vistos.add(clave)
        if es_ia(r["objeto"]) and not ia_dup:
            ob = r["objeto"]
            ia = ia_anual[y]
            ia["n"] += 1
            ia["importe"] += gasto
            if menor:
                ia["menores_n"] += 1
                ia["menores_imp"] += gasto
            ia_mes[mes]["n"] += 1
            ia_mes[mes]["importe"] += gasto
            c_ = ia_cat[categoria_ia(ob)]
            c_["n"] += 1
            c_["importe"] += gasto
            g_ = ia_grupo[ADMIN_GRUPO.get(r["tipo_admin"] or "", "?")]
            g_["n"] += 1
            g_["importe"] += gasto
            pv = ia_prov[r["prov"] or "??"]
            pv["n"] += 1
            pv["importe"] += gasto
            io = ia_org[k]
            io["n"] += 1
            io["importe"] += gasto
            ia_proc["Menor" if menor else PROC_TXT.get(r["procedimiento"] or "", "Otro")] += 1
            if nuevo_lote and lote_id not in ia_lotes:
                ia_lotes.add(lote_id)
                ia_comp[y][0] += 1
                ia_comp[y][1] += r["n_ofertas"] == 1
                io["lotes"] += 1
                io["unica"] += r["n_ofertas"] == 1
            if r["adj_nif"] and not fis:
                ie = ia_emp[r["adj_nif"]]
                ie["n"] += 1
                ie["importe"] += gasto
                ie["organos"].add(k)
                if r["adj_nombre"]:
                    emp_name.setdefault(r["adj_nif"], r["adj_nombre"])
            if tiene_fondos and categoria_fondos(r["fondos"], r["fondos_txt"]) == "PRTR":
                ia_prtr["n"] += 1
                ia_prtr["importe"] += gasto
            for marca, rx in (("ChatGPT", r"CHAT\s*GPT|\bGPT\b|OPENAI"), ("Copilot", r"COPILOT"), ("Gemini", r"GEMINI"), ("Claude", r"\bCLAUDE\b|ANTHROPIC")):
                if re.search(rx, ob, re.I):
                    ia_marcas[marca] += 1
            if gasto >= 100000:
                ia_top.append([round(gasto), r["fecha_adj"], ob[:220], k, r["organo"], None if fis else r["adj_nombre"],
                               None if fis else r["adj_nif"], r["n_ofertas"], "Menor" if menor else PROC_TXT.get(r["procedimiento"] or "", "Otro"), short_url(r["url"])])
        if not en12:
            continue

        o = org[k]
        o["n"] += 1
        o["importe"] += gasto
        o["proc"][r["procedimiento"] or "?"] += 1
        o["proc_imp"][r["procedimiento"] or "?"] += gasto
        if r["cpv"]:
            o["cpv"][r["cpv"][:2]] += gasto
        o["meses"][mes] += gasto
        prov_key = r["adj_nif"] if (r["adj_nif"] and not fis) else f"anon:{r['entry_id']}:{r['lote']}"
        o["prov_imp"][prov_key] += gasto
        o["prov_n"][prov_key] += 1
        ps = prov_stats[r["prov"] or "??"]
        ps["n"] += 1
        ps["importe"] += gasto

        if comp:
            o["comp"] += 1
            calidad["comp"] += 1
            if not r["n_ofertas"]:
                o["sin_ofertas"] += 1
                calidad["comp_sin_ofertas"] += 1
            elif lote_id not in o["lotes"]:
                o["lotes"][lote_id] = r["n_ofertas"]
                ps["lotes"] += 1
                ps["unica"] += r["n_ofertas"] == 1
                if r["n_ofertas"] == 1:
                    o["prov_unica"][prov_key] += 1
            b = plazo_bajo_minimo(r)
            if b is not None:
                o["plazo"][0] += 1
                o["plazo"][1] += b
        if not menor:
            o["nomenor"] += gasto
            ps["nomenor"] += gasto
            if r["procedimiento"] == "3":
                if r["urgencia"] == "3":
                    o["nsp_emerg"] += gasto
                else:
                    o["nsp"] += gasto
                    ps["nsp"] += gasto
        else:
            o["menores"].append((imp, r["tipo_contrato"], r["adj_nif"] if not fis else None, (r["cpv"] or "")[:2], y))

        o["recientes"].append((r["fecha_adj"], r["objeto"], gasto, r["n_ofertas"], r["procedimiento"],
                               r["adj_nombre"] or ("Persona física (anonimizado)" if fis else None), short_url(r["url"]),
                               r["adj_nif"] if not fis else None))
        if len(o["recientes"]) > 400:
            o["recientes"].sort(reverse=True)
            del o["recientes"][60:]

        # empresas (solo personas jurídicas, UTE y extranjeras)
        if r["adj_nif"] and not fis:
            e = emp[r["adj_nif"]]
            if r["adj_nombre"]:
                emp_name[r["adj_nif"]] = r["adj_nombre"]
            e["importe"] += gasto
            e["n"] += 1
            e["organos"][k] += gasto
            if comp and r["n_ofertas"]:
                e["comp"] += 1
                e["unica"] += r["n_ofertas"] == 1
            e["recientes"].append((r["fecha_adj"], (r["objeto"] or "")[:110], gasto, r["n_ofertas"], k, short_url(r["url"])))
            if len(e["recientes"]) > 200:
                e["recientes"].sort(reverse=True)
                del e["recientes"][40:]

    print(f"filas leídas: {n_rows}; órganos con actividad en 12 meses: {len(org)}; empresas: {len(emp)}", file=sys.stderr)

    # ---------------- indicadores por órgano
    ind = {}
    for k, o in org.items():
        lotes = list(o["lotes"].values())
        n1 = len(lotes)
        u1 = sum(1 for x in lotes if x == 1)
        res = {"I1": None, "I2": None, "I3": None, "I4": None, "I5": None, "I6": None}
        res["I1"] = {"v": round(100 * u1 / n1, 1), "n": n1, "k": u1, "ic": wilson(u1, n1)} if n1 else None
        nomen_n = sum(c for p, c in o["proc"].items() if p != "6")
        if o["nomenor"] > 0:
            res["I2"] = {"v": round(100 * o["nsp"] / o["nomenor"], 1), "n": nomen_n, "importe": round(o["nsp"]),
                         "emergencia": round(o["nsp_emerg"])}
        cerca = medio = 0
        for imp, tipo, *_ in o["menores"]:
            if imp is None:
                continue
            u = umbral(tipo)
            if 0.95 * u <= imp < u:
                cerca += 1
            elif 0.80 * u <= imp < 0.95 * u:
                medio += 1
        if cerca + medio:
            res["I3"] = {"v": round(cerca / medio, 2) if medio else None, "n": cerca + medio, "cerca": cerca, "medio": medio,
                         "pct_cerca": round(100 * cerca / len(o["menores"]), 1)}
        parejas = defaultdict(lambda: [0.0, 0, None])
        tot_men = 0.0
        for imp, tipo, nif, cpv2, yy in o["menores"]:
            tot_men += imp or 0
            if nif:
                p = parejas[(nif, cpv2, yy)]
                p[0] += imp or 0
                p[1] += 1
                p[2] = tipo
        rec = sum(p[0] for p in parejas.values() if p[1] >= 3 and p[0] > umbral(p[2]))
        if o["menores"]:
            res["I4"] = {"v": round(100 * rec / tot_men, 1) if tot_men else 0.0, "n": len(o["menores"]),
                         "parejas": sum(1 for p in parejas.values() if p[1] >= 3 and p[0] > umbral(p[2]))}
        tot = sum(o["prov_imp"].values())
        if tot > 0:
            shares = [100 * v / tot for v in o["prov_imp"].values()]
            top = sorted(shares, reverse=True)
            res["I5"] = {"v": round(sum(s * s for s in shares)), "n": o["n"], "proveedores": len(shares),
                         "top1": round(top[0], 1), "top3": round(sum(top[:3]), 1)}
        if o["plazo"][0]:
            res["I6"] = {"v": round(100 * o["plazo"][1] / o["plazo"][0], 1), "n": o["plazo"][0], "k": o["plazo"][1]}
        q_ = round(100 * o["sin_ofertas"] / o["comp"], 1) if o["comp"] else None
        name, tadm, prov, ccaa = org_meta.get(k, (k, None, None, None))
        size = "grande" if o["n"] >= 500 else "mediano" if o["n"] >= 50 else "pequeño"
        ind[k] = {"res": res, "q": q_, "grupo": ADMIN_GRUPO.get(tadm or "", "?"), "size": size}

    # ---------------- percentiles entre pares (mismo tipo de administración y tamaño)
    peers = defaultdict(lambda: defaultdict(list))
    for k, v in ind.items():
        for i, x in v["res"].items():
            if x and x.get("v") is not None and x["n"] >= MIN_N[i]:
                peers[(v["grupo"], v["size"])][i].append(x["v"])
    UMBRAL_SENAL = {"I1": 20, "I2": 10, "I3": 0.5, "I4": 25, "I5": 2500, "I6": 5}
    for k, v in ind.items():
        flags = 0
        for i, x in v["res"].items():
            if not x or x.get("v") is None:
                continue
            ok = x["n"] >= MIN_N[i]
            x["suficiente"] = ok
            if ok:
                vals = peers[(v["grupo"], v["size"])][i]
                x["pct"] = percentile_rank(vals, x["v"])
                x["pares"] = len(vals)
                x["atipico"] = bool(x["pct"] is not None and x["pct"] >= 90 and x["v"] > UMBRAL_SENAL[i])
                flags += x["atipico"]
        v["atipicos"] = flags

    # ---------------- salida: índice de órganos, fichas por shards
    index = []
    shards = defaultdict(dict)
    for k, o in org.items():
        name, tadm, prov, ccaa = org_meta.get(k, (k, None, None, None))
        v = ind[k]
        i1 = v["res"]["I1"]
        index.append([k, name, prov, ccaa, v["grupo"], o["n"], round(o["importe"]),
                      i1["v"] if i1 and i1.get("suficiente") else None, v["atipicos"]])
        top_prov = []
        for pk, imp in o["prov_imp"].most_common(12):
            anon = pk.startswith("anon:")
            top_prov.append({"nif": None if anon else pk, "nombre": "Personas físicas y otros (anonimizado)" if anon else emp_name.get(pk, pk),
                             "importe": round(imp), "n": o["prov_n"][pk], "unica": o["prov_unica"][pk]})
        # agrupa anónimos en una sola línea
        anon_imp = sum(imp for pk, imp in o["prov_imp"].items() if pk.startswith("anon:"))
        top_prov = [p for p in top_prov if p["nif"]][:10]
        rec = sorted(o["recientes"], reverse=True)[:25]
        shards[shard(k, N_SHARDS_ORG)][k] = {
            "nombre": name, "tipo_admin": tadm, "grupo": v["grupo"], "tamano": v["size"], "prov": prov, "ccaa": ccaa,
            "n": o["n"], "importe": round(o["importe"]), "indicadores": v["res"], "calidad_sin_ofertas": v["q"],
            "atipicos": v["atipicos"],
            "procedimientos": [[p, PROC_TXT.get(p, p), c, round(o["proc_imp"][p])] for p, c in o["proc"].most_common()],
            "cpv": [[c, round(x)] for c, x in o["cpv"].most_common(8)],
            "meses": sorted([m, round(x)] for m, x in o["meses"].items()),
            "proveedores": top_prov, "anonimizado_importe": round(anon_imp),
            "recientes": [{"f": f, "o": ob, "i": round(i), "of": no, "p": PROC_TXT.get(p, p), "a": ad, "nif": nif, "u": u}
                          for f, ob, i, no, p, ad, u, nif in rec],
        }
    index.sort(key=lambda x: -x[6])
    (out / "organos" / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for s, d in shards.items():
        (out / "organos" / f"{s}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------------- empresas
    eshards = defaultdict(dict)
    eindex = []
    for nif, e in emp.items():
        eindex.append([nif, emp_name.get(nif, nif), round(e["importe"]), e["n"], len(e["organos"]),
                       round(100 * e["unica"] / e["comp"], 1) if e["comp"] >= 5 else None])
        eshards[shard(nif, N_SHARDS_EMP)][nif] = {
            "nombre": emp_name.get(nif, nif), "importe": round(e["importe"]), "n": e["n"], "comp": e["comp"], "unica": e["unica"],
            "organos": [[kk, org_meta.get(kk, (kk,))[0], round(x)] for kk, x in e["organos"].most_common(10)],
            "recientes": [{"f": f, "o": ob, "i": round(i), "of": no, "org": org_meta.get(kk, (kk,))[0], "k": kk, "u": u}
                          for f, ob, i, no, kk, u in sorted(e["recientes"], reverse=True)[:10]],
        }
    eindex.sort(key=lambda x: -x[2])
    (out / "empresas" / "top.json").write_text(json.dumps(eindex[:1000], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "empresas" / "buscar.json").write_text(json.dumps([[x[0], x[1]] for x in eindex if x[2] >= 50000], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for s, d in eshards.items():
        (out / "empresas" / f"{s}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------------- fondos europeos
    if tiene_fondos:
        pct = lambda c: round(100 * c[1] / c[0], 1) if c[0] >= 30 else None
        fondos = {
            "anual": {yy: {cat: {"n": v["n"], "importe": round(v["importe"])} for cat, v in cats.items()} for yy, cats in sorted(fondos_anual.items())},
            "meses": sorted([m, v["n"], round(v["importe"])] for m, v in fondos_mes.items()),
            "competencia": {yy: {g: {"lotes": c[0], "oferta_unica": pct(c)} for g, c in gs.items()} for yy, gs in sorted(fondos_comp.items())},
            "provincias": {p_: {"n": v["n"], "importe": round(v["importe"])} for p_, v in fondos_prov.items()},
            "organos": [[kk, org_meta.get(kk, (kk,))[0], org_meta.get(kk, (None, None, None))[2] if kk in org_meta else None, v["n"], round(v["importe"]),
                         round(100 * v["unica"] / v["lotes"], 1) if v["lotes"] >= 10 else None, v["lotes"]]
                        for kk, v in sorted(fondos_org.items(), key=lambda x: -x[1]["importe"])[:300]],
            "empresas": [[nif, emp_name.get(nif, nif), v["n"], round(v["importe"]), len(v["organos"])]
                         for nif, v in sorted(fondos_emp.items(), key=lambda x: -x[1]["importe"])[:300]],
            "cpv": [[c, round(x)] for c, x in fondos_cpv.most_common(12)],
            "procedimientos": [[p_, PROC_TXT.get(p_, p_), c] for p_, c in fondos_proc.most_common()],
        }
        (out / "fondos.json").write_text(json.dumps(fondos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------------- inteligencia artificial
    pct_ia = lambda c: round(100 * c[1] / c[0], 1) if c[0] >= 30 else None
    ia_out = {
        "anual": {yy: {"n": v["n"], "importe": round(v["importe"]), "menores_n": v["menores_n"], "menores_imp": round(v["menores_imp"]),
                       "total_n": nat_year[yy]["n"], "total_imp": round(nat_year[yy]["importe"]),
                       "oferta_unica": pct_ia(ia_comp[yy]), "lotes": ia_comp[yy][0],
                       "oferta_unica_total": pct_ia([nat_year[yy]["lotes"], nat_year[yy]["unica"]])}
                  for yy, v in sorted(ia_anual.items())},
        "meses": sorted([m, v["n"], round(v["importe"])] for m, v in ia_mes.items()),
        "categorias": sorted([[c, v["n"], round(v["importe"])] for c, v in ia_cat.items()], key=lambda x: -x[2]),
        "grupos": sorted([[GRUPO_TXT.get(g, g), v["n"], round(v["importe"])] for g, v in ia_grupo.items()], key=lambda x: -x[2]),
        "provincias": {p_: {"n": v["n"], "importe": round(v["importe"])} for p_, v in ia_prov.items()},
        "organos": [[kk, org_meta.get(kk, (kk,))[0], org_meta[kk][2] if kk in org_meta else None, v["n"], round(v["importe"]),
                     round(100 * v["unica"] / v["lotes"], 1) if v["lotes"] >= 5 else None, v["lotes"]]
                    for kk, v in sorted(ia_org.items(), key=lambda x: -x[1]["importe"])[:300]],
        "organos_n": len(ia_org),
        "empresas": [[nif, emp_name.get(nif, nif), v["n"], round(v["importe"]), len(v["organos"])]
                     for nif, v in sorted(ia_emp.items(), key=lambda x: -x[1]["importe"])[:300]],
        "procedimientos": ia_proc.most_common(),
        "prtr": {"n": ia_prtr["n"], "importe": round(ia_prtr["importe"])},
        "marcas": ia_marcas.most_common(),
        "mayores": sorted(ia_top, key=lambda x: -x[0])[:100],
    }
    (out / "ia.json").write_text(json.dumps(ia_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------------- resumen nacional
    years = sorted(nat_year)
    resumen = {
        "version": VERSION, "generado": datetime.now().strftime("%Y-%m-%dT%H:%M"), "hasta": hasta_s, "ventana_desde": desde12,
        "filas": n_rows, "organos": len(org), "empresas": len(emp),
        "anual": {y: {"lotes": v["lotes"], "oferta_unica": round(100 * v["unica"] / v["lotes"], 1) if v["lotes"] else None,
                      "nsp": round(100 * v["nsp"] / v["nomenor"], 1) if v["nomenor"] else None,
                      "nsp_n": round(100 * v["nsp_n"] / v["nomenor_n"], 1) if v["nomenor_n"] else None,
                      "importe": round(v["importe"]), "n": v["n"], "menores_n": v["menores_n"], "menores_imp": round(v["menores_imp"]),
                      "fisicas_n": v["fisicas_n"], "fisicas_imp": round(v["fisicas_imp"])} for y, v in nat_year.items()},
        "meses": sorted([m, v["n"], round(v["importe"]), round(100 * v["unica"] / v["lotes"], 1) if v["lotes"] >= 50 else None]
                        for m, v in nat_mes.items() if m >= "2020-01"),
        "provincias": {p: {"lotes": v["lotes"], "oferta_unica": round(100 * v["unica"] / v["lotes"], 1) if v["lotes"] >= 30 else None,
                           "nsp": round(100 * v["nsp"] / v["nomenor"], 1) if v["nomenor"] else None,
                           "importe": round(v["importe"]), "n": v["n"]} for p, v in prov_stats.items()},
        "calidad": {"lotes_competitivos": calidad["comp"],
                    "sin_numero_ofertas_pct": round(100 * calidad["comp_sin_ofertas"] / calidad["comp"], 1) if calidad["comp"] else None,
                    "importe_dudoso_n": calidad["importe_dudoso"], "importe_dudoso_eur": round(calidad["importe_dudoso_eur"])},
        "atipicos": Counter(v["atipicos"] for v in ind.values()),
        "grupos": GRUPO_TXT,
        "umbral_senal": UMBRAL_SENAL, "min_n": MIN_N,
        "ue": {"fuente": "Comisión Europea, Single Market Scoreboard (datos 2024, contratos publicados en TED)",
               "url": "https://single-market-scoreboard.ec.europa.eu/business-framework-conditions/public-procurement_en",
               "oferta_unica": {"es": 33, "ue": 28, "verde": 10, "rojo": 20},
               "sin_convocatoria": {"es": 8, "ue": 6, "verde": 5, "rojo": 10},
               "dias_decision": {"es": 110, "ue": 74, "verde": 120, "rojo": 120}},
    }
    (out / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({k: resumen[k] for k in ("filas", "organos", "empresas", "calidad")}, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
