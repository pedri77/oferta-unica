#!/usr/bin/env python3
"""Descarga los ZIP de PLACSP que hayan cambiado y los incorpora al estado SQLite.

Uso:
  python scripts/fetch.py --db work/state.sqlite --desde 2025-01          # meses desde enero de 2025
  python scripts/fetch.py --db work/state.sqlite --anual 2023 2024        # años completos (histórico)

Detalles del servidor de Hacienda (comprobados en septiembre de 2026):
- Bloquea las peticiones HEAD: se usa GET con Range 0-0 para leer el ETag.
- Un fichero inexistente responde 200 con una página HTML: se valida la firma ZIP.
- Regenera meses ya cerrados: si cambia el ETag se vuelve a procesar.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from placsp import ingest, open_db  # noqa: E402

BASE = "https://contrataciondelsectorpublico.gob.es/sindicacion/"
FEEDS = {
    "643": "sindicacion_643/licitacionesPerfilesContratanteCompleto3_{p}.zip",
    "1143": "sindicacion_1143/contratosMenoresPerfilesContratantes_{p}.zip",
    "1044": "sindicacion_1044/PlataformasAgregadasSinMenores_{p}.zip",
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def request(url: str, headers: dict | None = None):
    h = {"User-Agent": UA, **(headers or {})}
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120)


def remote_version(url: str) -> str | None:
    """ETag o tamaño del fichero sin descargarlo. None si no existe."""
    for i in range(3):
        try:
            with request(url, {"Range": "bytes=0-3"}) as r:
                head = r.read(4)
                if head[:2] != b"PK":
                    return None
                return r.headers.get("ETag") or r.headers.get("Content-Range") or r.headers.get("Last-Modified")
        except Exception as e:  # red inestable: reintenta
            if i == 2:
                print(f"[aviso] {url}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    return None


def download(url: str, dest: str) -> bool:
    for i in range(4):
        try:
            with request(url) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            with open(dest, "rb") as f:
                if f.read(2) == b"PK":
                    return True
        except Exception as e:
            print(f"[aviso] descarga {url} intento {i + 1}: {e}", file=sys.stderr)
            time.sleep(15 * (i + 1))
    return False


def periods(desde: str) -> list[str]:
    y, m = map(int, desde.split("-"))
    today = date.today()
    out = []
    while (y, m) <= (today.year, today.month):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--manifest", default=None, help="por defecto, junto a la base de datos")
    ap.add_argument("--desde", default=None, help="AAAA-MM: ZIP mensuales desde ese mes")
    ap.add_argument("--anual", nargs="*", default=[], help="años completos con el ZIP anual")
    ap.add_argument("--feeds", nargs="*", default=list(FEEDS))
    a = ap.parse_args()

    manifest_path = Path(a.manifest or Path(a.db).with_name("manifest.json"))
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    db = open_db(a.db)
    todo = [(f, y) for y in a.anual for f in a.feeds] + [(f, p) for p in (periods(a.desde) if a.desde else []) for f in a.feeds]
    changed = 0
    for feed, per in todo:
        url = BASE + FEEDS[feed].format(p=per)
        ver = remote_version(url)
        if ver is None:
            print(f"{feed} {per}: no disponible")
            continue
        if manifest.get(url) == ver:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.zip")
            t0 = time.time()
            if not download(url, dest):
                print(f"{feed} {per}: fallo de descarga", file=sys.stderr)
                continue
            size = os.path.getsize(dest) / 1e6
            st = ingest(dest, db)
        manifest[url] = ver
        manifest_path.write_text(json.dumps(manifest, indent=1))
        changed += 1
        print(f"{feed} {per}: {size:.0f} MB, {st['updated']} expedientes nuevos o cambiados, {st['rows']} filas, "
              f"{time.time() - t0:.0f} s", flush=True)
    print(f"ficheros procesados: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
