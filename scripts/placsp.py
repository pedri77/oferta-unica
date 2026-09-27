#!/usr/bin/env python3
"""Parser de los datos abiertos de la Plataforma de Contratación del Sector Público (PLACSP).

Lee los ficheros ATOM/CODICE en streaming desde los ZIP mensuales o anuales y guarda
en SQLite una fila compacta por resultado adjudicado (lote × adjudicatario), con la
última versión de cada expediente. Solo usa la librería estándar.

Conjuntos soportados:
  643   licitaciones publicadas en PLACSP (sin menores)
  1143  contratos menores
  1044  agregación de plataformas autonómicas

Protección de datos: los adjudicatarios que son personas físicas (DNI/NIE, o NIF
enmascarado) se guardan anonimizados: sin NIF y sin nombre.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cbcx": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "cacx": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
    "at": "http://purl.org/atompub/tombstones/1.0",
}
T_ENTRY = "{%s}entry" % NS["a"]
T_DELETED = "{%s}deleted-entry" % NS["at"]
RESULTADO_ADJ = {"1", "2", "8", "9", "11"}

# ---------------------------------------------------------------- clasificación de NIF (RGPD)
RE_DNI = re.compile(r"^\d{8}[A-Z]$")
RE_NIE = re.compile(r"^[XYZ]\d{7}[A-Z]$")
RE_KLM = re.compile(r"^[KLM]\d{7}[A-Z]$")
RE_CIF = re.compile(r"^[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]$")
RE_UTE = re.compile(r"(^|[\s,(])U\.?\s?T\.?\s?E\.?($|[\s,)])|UNI[OÓ]N TEMPORAL")
# Comunidades de bienes (E) y sociedades civiles (J) suelen llevar nombres de personas
RE_PERSONAS_EJ = re.compile(r"^[EJ]\d{7}[0-9A-J]$")


def classify_nif(raw: str | None) -> tuple[str | None, str]:
    """Devuelve (nif normalizado, tipo): juridica, fisica, extranjero o desconocido."""
    if not raw:
        return None, "desconocido"
    n = re.sub(r"[\s.\-/]", "", raw.upper())
    if "*" in n:
        return None, "fisica"  # PLACSP ya enmascara algunas personas físicas
    if n.startswith("ES") and len(n) == 11:
        n = n[2:]
    if RE_CIF.match(n):
        return n, "juridica"
    if RE_DNI.match(n) or RE_NIE.match(n) or RE_KLM.match(n) or re.match(r"^\d{7,8}[A-Z]?$", n):
        return None, "fisica"
    return n, "extranjero" if re.match(r"^[A-Z]{2}[A-Z0-9]{5,}$", n) else "desconocido"


# ---------------------------------------------------------------- territorio
# Dos primeros dígitos del código postal = código INE de provincia
PROV_CCAA = {"01": "16", "02": "08", "03": "10", "04": "01", "05": "07", "06": "11", "07": "04", "08": "09",
             "09": "07", "10": "11", "11": "01", "12": "10", "13": "08", "14": "01", "15": "12", "16": "08",
             "17": "09", "18": "01", "19": "08", "20": "16", "21": "01", "22": "02", "23": "01", "24": "07",
             "25": "09", "26": "17", "27": "12", "28": "13", "29": "01", "30": "14", "31": "15", "32": "12",
             "33": "03", "34": "07", "35": "05", "36": "12", "37": "07", "38": "05", "39": "06", "40": "07",
             "41": "01", "42": "07", "43": "09", "44": "02", "45": "08", "46": "10", "47": "07", "48": "16",
             "49": "07", "50": "02", "51": "18", "52": "19"}
NUTS3_PROV = {"ES111": "15", "ES112": "27", "ES113": "32", "ES114": "36", "ES120": "33", "ES130": "39",
              "ES211": "01", "ES212": "20", "ES213": "48", "ES220": "31", "ES230": "26", "ES241": "22",
              "ES242": "44", "ES243": "50", "ES300": "28", "ES411": "05", "ES412": "09", "ES413": "24",
              "ES414": "34", "ES415": "37", "ES416": "40", "ES417": "42", "ES418": "47", "ES419": "49",
              "ES421": "02", "ES422": "13", "ES423": "16", "ES424": "19", "ES425": "45", "ES431": "06",
              "ES432": "10", "ES511": "08", "ES512": "17", "ES513": "25", "ES514": "43", "ES521": "03",
              "ES522": "12", "ES523": "46", "ES531": "07", "ES532": "07", "ES533": "07", "ES611": "04",
              "ES612": "11", "ES613": "14", "ES614": "18", "ES615": "21", "ES616": "23", "ES617": "29",
              "ES618": "41", "ES620": "30", "ES630": "51", "ES640": "52", "ES703": "38", "ES704": "35",
              "ES705": "35", "ES706": "38", "ES707": "38", "ES708": "35", "ES709": "38"}
NUTS2_CCAA = {"ES11": "12", "ES12": "03", "ES13": "06", "ES21": "16", "ES22": "15", "ES23": "17", "ES24": "02",
              "ES30": "13", "ES41": "07", "ES42": "08", "ES43": "11", "ES51": "09", "ES52": "10", "ES53": "04",
              "ES61": "01", "ES62": "14", "ES63": "18", "ES64": "19", "ES70": "05"}
# Plataformas autonómicas del conjunto 1044 (nombre del agente) -> CCAA
PLATAFORMA_CCAA = [("catalunya", "09"), ("generalitat de catalunya", "09"), ("madrid", "13"), ("euskadi", "16"),
                   ("vasco", "16"), ("andaluc", "01"), ("galicia", "12"), ("xunta", "12"), ("navarra", "15"),
                   ("rioja", "17"), ("canarias", "05"), ("valencia", "10"), ("balears", "04"), ("murcia", "14"),
                   ("aragón", "02"), ("aragon", "02"), ("asturias", "03"), ("cantabria", "06"),
                   ("castilla y león", "07"), ("castilla-la mancha", "08"), ("extremadura", "11")]


def txt(el, path):
    if el is None:
        return None
    x = el.find(path, NS)
    if x is None or x.text is None:
        return None
    return x.text.strip() or None


def num(el, path):
    t = txt(el, path)
    try:
        return float(t) if t is not None else None
    except ValueError:
        return None


def intval(el, path):
    t = txt(el, path)
    try:
        return int(float(t)) if t is not None else None
    except ValueError:
        return None


def to_utc(ts):
    if not ts:
        return ""
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ts


def detect_source(entry_id: str, fname: str) -> str:
    if "datosAbiertosMenores" in entry_id or "contratosMenores" in fname:
        return "1143"
    if "PlataformasAgregadas" in fname or "agregad" in entry_id.lower():
        return "1044"
    return "643"


def party_ids(party):
    out = {}
    if party is not None:
        for pid in party.findall("cac:PartyIdentification/cbc:ID", NS):
            out[pid.get("schemeName") or "ID"] = (pid.text or "").strip()
    return out


def publication_date(cfs):
    """Primera fecha de publicación del anuncio de licitación (DOC_CN) en el perfil del contratante."""
    dates = []
    for vni in cfs.findall("cacx:ValidNoticeInfo", NS):
        if txt(vni, "cbcx:NoticeTypeCode") != "DOC_CN":
            continue
        for aps in vni.findall("cacx:AdditionalPublicationStatus", NS):
            for d in aps.findall("cacx:AdditionalPublicationDocumentReference/cbc:IssueDate", NS):
                if d.text:
                    dates.append(d.text.strip()[:10])
    return min(dates) if dates else None


COLS = ("entry_id", "src", "url", "organo_key", "organo", "tipo_admin", "prov", "ccaa", "objeto", "tipo_contrato",
        "procedimiento", "sistema", "urgencia", "sara", "cpv", "presupuesto", "fecha_pub", "fin_ofertas",
        "fecha_adj", "n_ofertas", "importe", "importe_iva", "n_res_lote", "adj_nif", "adj_nombre", "adj_tipo",
        "pyme", "lote")


def row_key(eid: str, src: str) -> str:
    """Clave corta y única por expediente (los ids numéricos pueden repetirse entre conjuntos)."""
    return f"{src}:{eid.rsplit('/', 1)[-1]}"


def parse_entry(e, fname):
    eid = txt(e, "a:id") or ""
    upd = txt(e, "a:updated")
    cfs = e.find("cacx:ContractFolderStatus", NS)
    if cfs is None:
        return eid, upd, detect_source(eid, fname), []
    src = detect_source(eid, fname)
    link = e.find("a:link", NS)

    lcp = cfs.find("cacx:LocatedContractingParty", NS)
    party = lcp.find("cac:Party", NS) if lcp is not None else None
    ids = party_ids(party)
    plataforma = txt(party, "cac:AgentParty/cac:PartyName/cbc:Name")
    idplat = ids.get("ID_PLATAFORMA") or ids.get("ID_OC_PLAT")
    organo_key = ids.get("DIR3") or ids.get("NIF") or (f"P:{(plataforma or '')[:40]}:{idplat}" if idplat else None)
    cp = txt(party, "cac:PostalAddress/cbc:PostalZone") or ""
    pp = cfs.find("cac:ProcurementProject", NS)
    tp = cfs.find("cac:TenderingProcess", NS)
    nuts = txt(pp, "cac:RealizedLocation/cbc:CountrySubentityCode") or ""
    prov = cp[:2] if len(cp) == 5 and cp[:2] in PROV_CCAA else NUTS3_PROV.get(nuts[:5])
    ccaa = PROV_CCAA.get(prov) if prov else NUTS2_CCAA.get(nuts[:4])
    if not ccaa and plataforma:
        low = plataforma.lower()
        ccaa = next((c for k, c in PLATAFORMA_CCAA if k in low), None)
    proc = txt(tp, "cbc:ProcedureCode") or ("6" if src == "1143" else None)
    cpvs = [c.text.strip() for c in pp.findall("cac:RequiredCommodityClassification/cbc:ItemClassificationCode", NS)
            if c.text] if pp is not None else []

    base = {
        "entry_id": row_key(eid, src), "src": src, "url": link.get("href") if link is not None else None,
        "organo_key": organo_key, "organo": txt(party, "cac:PartyName/cbc:Name"),
        "tipo_admin": txt(lcp, "cbc:ContractingPartyTypeCode"), "prov": prov, "ccaa": ccaa,
        "objeto": (txt(pp, "cbc:Name") or "")[:180] or None, "tipo_contrato": txt(pp, "cbc:TypeCode"),
        "procedimiento": proc, "sistema": txt(tp, "cbc:ContractingSystemCode"), "urgencia": txt(tp, "cbc:UrgencyCode"),
        "sara": txt(tp, "cbc:OverThresholdIndicator"), "cpv": cpvs[0] if cpvs else None,
        "presupuesto": num(pp, "cac:BudgetAmount/cbc:TaxExclusiveAmount"),
        "fecha_pub": publication_date(cfs),
        "fin_ofertas": (txt(tp, "cac:TenderSubmissionDeadlinePeriod/cbc:EndDate") or "")[:10] or None,
    }
    lot_budget = {}
    for lot in cfs.findall("cac:ProcurementProjectLot", NS):
        lot_budget[txt(lot, "cbc:ID")] = num(lot, "cac:ProcurementProject/cac:BudgetAmount/cbc:TaxExclusiveAmount")

    trs = cfs.findall("cac:TenderResult", NS)
    per_lot = {}
    for tr in trs:
        k = txt(tr, "cac:AwardedTenderedProject/cbc:ProcurementProjectLotID")
        per_lot[k] = per_lot.get(k, 0) + 1
    rows = []
    for tr in trs:
        rc = txt(tr, "cbc:ResultCode")
        atp = tr.find("cac:AwardedTenderedProject", NS)
        lid = txt(atp, "cbc:ProcurementProjectLotID")
        w = tr.find("cac:WinningParty", NS)
        if w is None and rc not in RESULTADO_ADJ:
            continue
        wid = party_ids(w)
        scheme = "NIF" if "NIF" in wid else (next(iter(wid)) if wid else None)
        nif, kind = classify_nif(wid.get(scheme) if scheme else None)
        name = txt(w, "cac:PartyName/cbc:Name")
        if kind == "fisica":
            name = None
        elif scheme == "UTE" or (name and RE_UTE.search(name.upper())):
            kind = "ute"
        elif nif and RE_PERSONAS_EJ.match(nif):
            kind = "cb_sc"  # comunidad de bienes o sociedad civil: se trata como persona física
            name = None
        r = dict(base)
        if lid and lot_budget.get(lid) is not None:
            r["presupuesto"] = lot_budget[lid]
        r.update({
            "fecha_adj": (txt(tr, "cbc:AwardDate") or txt(tr, "cac:Contract/cbc:IssueDate") or "")[:10] or None,
            "n_ofertas": intval(tr, "cbc:ReceivedTenderQuantity"),
            "importe": num(atp, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"),
            "importe_iva": num(atp, "cac:LegalMonetaryTotal/cbc:PayableAmount"),
            "n_res_lote": per_lot.get(lid, 1), "adj_nif": nif if kind not in ("cb_sc",) else None,
            "adj_nombre": name, "adj_tipo": kind, "pyme": txt(tr, "cbc:SMEAwardedIndicator"), "lote": lid,
        })
        rows.append(tuple(r.get(c) for c in COLS))
    return eid, upd, src, rows


# ---------------------------------------------------------------- estado en SQLite
def open_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    # Carga masiva: sin diario ni fsync (si falla, se regenera desde los ZIP) y caché grande
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA cache_size=-400000")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, updated TEXT, src TEXT, k TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY, at TEXT)")
    db.execute(f"CREATE TABLE IF NOT EXISTS rows({', '.join(COLS)})")
    db.execute("CREATE INDEX IF NOT EXISTS rows_entry ON rows(entry_id)")
    return db


def iter_atoms(path: str):
    with zipfile.ZipFile(path) as z:
        for n in sorted(z.namelist()):
            if n.endswith(".atom"):
                with z.open(n) as fh:
                    yield n, fh


def ingest(path: str, db: sqlite3.Connection, log=sys.stderr) -> dict:
    """Añade un ZIP al estado. Solo reemplaza un expediente si la versión es más reciente."""
    st = {"entries": 0, "updated": 0, "tombstones": 0, "rows": 0, "errors": 0}
    cur = db.cursor()
    for fname, fh in iter_atoms(path):
        root = None
        try:
            for ev, el in ET.iterparse(fh, events=("start", "end")):
                if ev == "start":
                    root = root if root is not None else el
                    continue
                if el.tag == T_ENTRY:
                    eid, upd, src, rows = parse_entry(el, fname)
                    upd = to_utc(upd)
                    st["entries"] += 1
                    prev = cur.execute("SELECT updated FROM entries WHERE id=?", (eid,)).fetchone()
                    if prev is None or upd > prev[0]:
                        short = row_key(eid, src)
                        cur.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?)", (eid, upd, src, short))
                        cur.execute("DELETE FROM rows WHERE entry_id=?", (short,))
                        cur.executemany(f"INSERT INTO rows VALUES({','.join('?' * len(COLS))})", rows)
                        st["updated"] += 1
                        st["rows"] += len(rows)
                    root.clear()
                elif el.tag == T_DELETED:
                    ref, when = el.get("ref"), to_utc(el.get("when"))
                    cur.execute("INSERT INTO tombstones VALUES(?,?) ON CONFLICT(id) DO UPDATE SET at=excluded.at "
                                "WHERE excluded.at > tombstones.at", (ref, when))
                    st["tombstones"] += 1
                    root.clear()
        except ET.ParseError as ex:
            st["errors"] += 1
            print(f"[aviso] error de XML en {fname}: {ex}", file=log)
    db.commit()
    return st


if __name__ == "__main__":
    db = open_db(sys.argv[1])
    for p in sys.argv[2:]:
        print(p, ingest(p, db))
