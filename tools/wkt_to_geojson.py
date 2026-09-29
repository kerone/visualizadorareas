"""Convierte el CSV de polígonos de Correos (columnas Code, Geom en WKT) en un
GeoJSON por provincia (data/cp/<PROVINCIA>.geojson) con contornos simplificados.

Desde la raíz del repo: python3 tools/wkt_to_geojson.py <export.csv o .zip> data/cp
(sube el tope de campo del CSV porque las geometrías superan los 128 KB)

Uso: python3 wkt_to_geojson.py <csv o zip> <carpeta de salida> [tolerancia_grados]
Tolerancia por defecto 0.00015° (~15 m): suficiente para pintar y ~10x más ligero.
"""
import csv, io, json, os, re, sys, zipfile
csv.field_size_limit(sys.maxsize)
from collections import defaultdict

PROV = {'01':'ALAVA','02':'ALBACETE','03':'ALICANTE','04':'ALMERIA','05':'AVILA','06':'BADAJOZ','07':'BALEARES','08':'BARCELONA','09':'BURGOS','10':'CACERES',
 '11':'CADIZ','12':'CASTELLON','13':'CIUDAD_REAL','14':'CORDOBA','15':'CORUNA','16':'CUENCA','17':'GERONA','18':'GRANADA','19':'GUADALAJARA','20':'GUIPUZCOA',
 '21':'HUELVA','22':'HUESCA','23':'JAEN','24':'LEON','25':'LERIDA','26':'RIOJA','27':'LUGO','28':'MADRID','29':'MALAGA','30':'MURCIA','31':'NAVARRA','32':'ORENSE',
 '33':'ASTURIAS','34':'PALENCIA','35':'LAS_PALMAS','36':'PONTEVEDRA','37':'SALAMANCA','38':'TENERIFE','39':'CANTABRIA','40':'SEGOVIA','41':'SEVILLA','42':'SORIA',
 '43':'TARRAGONA','44':'TERUEL','45':'TOLEDO','46':'VALENCIA','47':'VALLADOLID','48':'VIZCAYA','49':'ZAMORA','50':'ZARAGOZA','51':'CEUTA','52':'MELILLA'}

def parse_wkt(wkt):
    """Devuelve lista de polígonos; cada polígono = lista de anillos; anillo = [[lon,lat],...]"""
    wkt = wkt.strip()
    m = re.match(r'^(MULTIPOLYGON|POLYGON)\s*(.*)$', wkt, re.S | re.I)
    if not m: return []
    kind, body = m.group(1).upper(), m.group(2)
    polys = []
    if kind == 'POLYGON':
        polys = [body]
    else:
        depth, start, cur = 0, None, []
        for i, ch in enumerate(body):
            if ch == '(':
                depth += 1
                if depth == 2: start = i
            elif ch == ')':
                if depth == 2: cur.append(body[start:i+1])
                depth -= 1
        polys = cur
    out = []
    for p in polys:
        rings = []
        for ring in re.findall(r'\(([^()]+)\)', p):
            pts = []
            for pair in ring.split(','):
                xy = pair.split()
                if len(xy) >= 2: pts.append([float(xy[0]), float(xy[1])])
            if len({(x, y) for x, y in pts}) >= 3 and len(pts) >= 4: rings.append(pts)  # anillos con área
        if rings: out.append(rings)
    return out

def simplify(pts, tol):
    """Douglas-Peucker iterativo; mantiene el anillo cerrado."""
    if len(pts) <= 4 or tol <= 0: return pts
    closed = pts[0] == pts[-1]
    if closed: pts = pts[:-1]
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        if b - a < 2: continue
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, bi = 0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L2 == 0: d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
                d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
            if d2 > best: best, bi = d2, i
        if best > tol2:
            keep[bi] = True; stack.append((a, bi)); stack.append((bi, b))
    res = [p for p, k in zip(pts, keep) if k]
    if len(res) < 3: res = pts[:3] if len(pts) >= 3 else pts
    res = [[round(x, 5), round(y, 5)] for x, y in res]
    if res[0] != res[-1]: res.append(res[0])
    return res

def rows_from(path):
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith('.csv'))
            with z.open(name) as f:
                yield from csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
    else:
        with open(path, encoding='utf-8-sig', newline='') as f:
            yield from csv.DictReader(f)

def main():
    src, out = sys.argv[1], sys.argv[2]
    tol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.00015
    os.makedirs(out, exist_ok=True)
    by_prov = defaultdict(list); n = skipped = 0; pts_in = pts_out = 0
    for r in rows_from(src):
        code = (r.get('Code') or r.get('code') or '').strip()
        if not (code.isdigit() and len(code) == 5): skipped += 1; continue  # solo CP de 5 dígitos
        geom = r.get('Geom') or r.get('geom') or ''
        prov = PROV.get(code[:2])
        polys = parse_wkt(geom)
        if not prov or not polys: skipped += 1; continue
        simp = []
        for rings in polys:
            rr = []
            for ring in rings:
                pts_in += len(ring); s = simplify(ring, tol); pts_out += len(s); rr.append(s)
            simp.append(rr)
        geometry = {'type': 'Polygon', 'coordinates': simp[0]} if len(simp) == 1 else {'type': 'MultiPolygon', 'coordinates': simp}
        props = {'COD_POSTAL': code}
        parent = (r.get('Parent Service Area Code') or '').strip()
        if parent: props['parent'] = parent
        by_prov[prov].append({'type': 'Feature', 'properties': props, 'geometry': geometry})
        n += 1
    total = 0
    for prov, feats in sorted(by_prov.items()):
        p = os.path.join(out, prov + '.geojson')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'type': 'FeatureCollection', 'features': feats}, f, separators=(',', ':'))
        total += os.path.getsize(p)
    print(f'{n} CPs en {len(by_prov)} provincias, {skipped} filas descartadas; puntos {pts_in} → {pts_out}; {total/1e6:.1f} MB en {out}')

if __name__ == '__main__':
    main()
