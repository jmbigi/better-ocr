#!/usr/bin/env python3
"""RNS (Registro Nacional de Sociedades) OFFLINE: la base oficial de personas
juridicas argentinas (Ley 26.047, datos de ARCA/AFIP publicados por el
Ministerio de Justicia en datos.jus.gob.ar).

Por que existe: la busqueda web por nombre de empresa muere cuando los
motores bloquean la IP (leccion 20 hallazgo 8) o cuando CuitOnline no
indexa la empresa. El RNS es la fuente OFICIAL completa (sociedades +
asociaciones civiles/fundaciones/mutuales) y se puede consultar SIN red:
se descarga una vez, se indexa en SQLite FTS5 y las busquedas son locales.

Fuentes verificadas en vivo el 2026-08-14 (respuesta de la API CKAN de
datos.gob.ar y descargas de prueba): los ZIP anuales 2019-2026 de
datos.jus.gob.ar + el CSV de asociaciones sin fines de lucro 20260731.
Columnas reales del CSV: cuit, razon_social, fecha_hora_contrato_social,
tipo_societario, fecha_hora_actualizacion, numero_inscripcion,
dom_fiscal_* (provincia/localidad/calle/numero/...), dom_legal_*; el CSV
de asociaciones agrega actividad_* (con FILAS DUPLICADAS por actividad: el
indexador deduplica por cuit+razon_social).

Uso:
    python3 rns.py descargar            # baja sociedades+asociaciones 2026
    python3 rns.py descargar --todos    # todos los anos 2019-2026
    python3 rns.py indexar              # construye la base SQLite FTS5
    python3 rns.py buscar "Asistencia Mis Abuelos"   # busqueda local
    python3 rns.py auto "Permanencia Salud"          # descargar+indexar+buscar
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.request
import zipfile

__all__ = [
    "URLS_RNS", "BASE_DB", "DIR_CSV", "normalizar_razon",
    "armar_query_fts", "crear_base", "indexar_csv", "buscar",
    "descargar_zip", "descargar_base", "indexar_directorio",
]

# ---------------------------------------------------------------------------
# Fuentes oficiales (URLs verificadas contra la API CKAN de datos.gob.ar
# y descargas de prueba reales el 2026-08-14, P0.2)
# ---------------------------------------------------------------------------

_DATASET = ("https://datos.jus.gob.ar/dataset/"
            "ee83de85-4305-4c53-9a9f-fd3d15e42c36/resource/")

URLS_RNS = {
    "sociedades_2026": _DATASET + ("13a1a66f-9f49-4d2d-9582-7b4540ef1b83/"
                                   "download/registro-nacional-sociedades-2026.zip"),
    "asociaciones_2026": _DATASET + ("a0301845-8440-4640-9435-9d975b496cac/"
                                     "download/registro-nacional-sociedades-"
                                     "asociaciones-sin-fines-lucro-2026.zip"),
    "sociedades_2025": _DATASET + ("cf93b46f-ec0b-4956-bcaf-412cd4799eef/"
                                   "download/registro-nacional-sociedades-2025.zip"),
    "asociaciones_2025": _DATASET + ("e7ef4a8d-b1bd-412d-8886-e2b1e81c5fad/"
                                     "download/registro-nacional-sociedades-"
                                     "asociaciones-sin-fines-lucro-2025.zip"),
    "sociedades_2024": _DATASET + ("a3f34d54-72fc-4848-802e-d3307310b1e9/"
                                   "download/registro-nacional-sociedades-2024.zip"),
    "sociedades_2023": _DATASET + ("fa9253b2-50c5-43c2-80db-8095e0dcbc8f/"
                                   "download/registro-nacional-sociedades-2023.zip"),
    "sociedades_2022": _DATASET + ("1126bd77-449e-4cdd-a96c-41e8c414fa75/"
                                   "download/registro-nacional-sociedades-2022.zip"),
    "sociedades_2021": _DATASET + ("763036e5-d6a4-4249-9e8d-dc204db63f58/"
                                   "download/registro-nacional-sociedades-2021.zip"),
    "sociedades_2020": _DATASET + ("1b5f96fa-9399-4580-b97a-cd2739cfa8cd/"
                                   "download/registro-nacional-sociedades-2020.zip"),
    "sociedades_2019": _DATASET + ("ac42fc32-7d72-4be5-9d37-e1f796634e55/"
                                   "download/registro-nacional-sociedades-2019.zip"),
    "sociedades_2026_t3": _DATASET + ("1c5fa113-2da8-4b0f-b842-68d9026bba66/"
                                      "download/registro-nacional-sociedades-"
                                      "2026-tercer-bimestre.zip"),
}

# Recursos que se bajan por defecto (los mas recientes y completos).
RECURSOS_DEFECTO = ["sociedades_2026", "asociaciones_2026"]

# Descargas grandes: los ZIP anuales de sociedades pesan cientos de MB
# (la base completa 2026 ~ 897 MB segun medicion manual del 2026-08-13).
DESCARGA_TOTAL_MB_APROX = 2500

# Columnas de interes del CSV (las 22 primeras son identicas en sociedades
# y asociaciones, verificado en vivo; actividad_* solo en asociaciones).
COLUMNAS_INTERES = [
    "cuit", "razon_social", "tipo_societario",
    "fecha_hora_contrato_social", "numero_inscripcion",
    "dom_fiscal_provincia", "dom_fiscal_localidad",
]

DIR_CSV = "datos-rns"          # directorio local de CSVs extraidos
BASE_DB = "rns.db"             # base SQLite FTS5 local

# El dataset RNS publica el CUIT como 11 digitos SIN guiones
# ("30123456789", verificado en el CSV real), pero el resto del proyecto usa
# el formato "XX-XXXXXXXX-X": se acepta ambos y se normaliza a la salida.
_RE_CUIT_NUM = re.compile(r"^\d{11}$")
RE_CUIT = re.compile(r"^\d{2}-\d{8}-\d$")

# Sufijos legales (reutiliza el criterio de empresas.py: anclados al final).
_RE_SUFIJO = re.compile(
    r"\s+(?:s\.?\s*r\.?\s*l\.?|s\.?\s*a\.?\s*s\.?|s\.?\s*a\.?|s\.?\s*h\.?|"
    r"sociedad de responsabilidad limitada|sociedad anonima|"
    r"asociacion civil|cooperativa limitada|mutual)\s*\.?\s*$",
    re.IGNORECASE)


def normalizar_razon(razon: str) -> str:
    """Normaliza la razon social para comparar: MAYUSCULAS sin acentos ni
    puntuacion, espacios colapsados. El tokenizador FTS5 hace lo mismo
    (unicode61 remove_diacritics), aqui es para la comparacion exacta."""
    n = unicodedata.normalize("NFD", razon or "")
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^\w\s]", " ", n.upper())
    return re.sub(r"\s+", " ", n).strip()


def sin_sufijo_legal(razon: str) -> str:
    """Quita el sufijo legal del final ('PERMANENCIA SALUD SRL' ->
    'PERMANENCIA SALUD'). Para buscar 'Asistencia Mis Abuelos' aunque en el
    registro figure 'ASISTENCIA MIS ABUELOS SRL'."""
    return _RE_SUFIJO.sub("", razon.strip()).strip() or razon.strip()


def _tokens(nombre: str) -> list:
    """Tokens de la consulta: palabras sin acentos ni puntuacion."""
    return [t for t in normalizar_razon(nombre).split() if len(t) >= 3]


def armar_query_fts(variante: str) -> str:
    """Query FTS5 para la variante: tokens AND con prefijo (asterisco) solo
    cuando la palabra tiene 4+ letras (los prefijos de 3 letras como 'mis'
    generan ruido: 'mis' matchearia 'misionera')."""
    partes = []
    for t in _tokens(variante):
        partes.append(f'"{t}"*' if len(t) >= 4 else f'"{t}"')
    return " AND ".join(partes)


# ---------------------------------------------------------------------------
# Base SQLite FTS5
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS empresas (
    rowid INTEGER PRIMARY KEY,
    cuit TEXT,
    razon_social TEXT NOT NULL,
    tipo_societario TEXT,
    fecha_contrato TEXT,
    numero_inscripcion TEXT,
    dom_provincia TEXT,
    dom_localidad TEXT,
    origen TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_empresas ON empresas(cuit, razon_social);
CREATE VIRTUAL TABLE IF NOT EXISTS empresas_fts USING fts5(
    razon_social,
    content='empresas',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""


def crear_base(db_path: str) -> sqlite3.Connection:
    """Crea (si no existe) la base y devuelve la conexion. Sin datos."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _normalizar_cuit(cuit: str) -> str:
    """Normaliza el CUIT a 'XX-XXXXXXXX-X' (acepta 11 digitos sin guiones,
    que es como lo publica el dataset RNS). '' si no es valido."""
    c = (cuit or "").strip()
    if RE_CUIT.match(c):
        return c
    if _RE_CUIT_NUM.match(c):
        return f"{c[:2]}-{c[2:10]}-{c[10:]}"
    return ""


def _es_cuit_valido(cuit: str) -> bool:
    return bool(_normalizar_cuit(cuit))


def indexar_csv(conn: sqlite3.Connection, csv_path: str, origen: str) -> int:
    """Indexa un CSV del RNS en la base. Deduplica por identidad de la
    entidad: (razon_social normalizada, tipo, fecha de contrato, localidad
    fiscal) — el CSV repite filas por actividad y, en algunas entidades,
    una fila sin CUIT junto a otra con CUIT: se fusiona priorizando la que
    tenga CUIT. Devuelve la cantidad de filas insertadas."""
    entidades = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        lector = csv.DictReader(f)
        faltan = [c for c in COLUMNAS_INTERES if c not in (lector.fieldnames or [])]
        if faltan:
            raise ValueError(f"{os.path.basename(csv_path)}: faltan columnas "
                             f"{faltan}; cabecera real: {lector.fieldnames}")
        for fila in lector:
            razon = (fila.get("razon_social") or "").strip()
            if not razon:
                continue
            entidad = (
                _normalizar_cuit(fila.get("cuit") or ""),
                razon,
                (fila.get("tipo_societario") or "").strip(),
                (fila.get("fecha_hora_contrato_social") or "").strip()[:10],
                (fila.get("numero_inscripcion") or "").strip(),
                (fila.get("dom_fiscal_provincia") or "").strip(),
                (fila.get("dom_fiscal_localidad") or "").strip(),
                origen,
            )
            clave = (normalizar_razon(razon), entidad[2], entidad[3], entidad[6])
            if clave in entidades:
                # misma entidad: fusionar priorizando la fila con CUIT
                previa = entidades[clave]
                if not previa[0] and entidad[0]:
                    entidades[clave] = entidad
                continue
            entidades[clave] = entidad
    insertados = 0
    with conn:
        for fila in entidades.values():
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO empresas (cuit, razon_social, "
                    "tipo_societario, fecha_contrato, numero_inscripcion, "
                    "dom_provincia, dom_localidad, origen) VALUES (?,?,?,?,?,?,?,?)",
                    fila)
                if cur.rowcount:
                    conn.execute(
                        "INSERT INTO empresas_fts (rowid, razon_social) "
                        "VALUES (?,?)", (cur.lastrowid, fila[1]))
                    insertados += 1
            except sqlite3.Error:
                continue
    conn.commit()
    return insertados


def indexar_directorio(db_path: str, dir_csv: str = DIR_CSV) -> dict:
    """Indexa todos los CSVs del directorio. Devuelve {csv: insertadas}."""
    conn = crear_base(db_path)
    try:
        resumen = {}
        if not os.path.isdir(dir_csv):
            return resumen
        for nombre in sorted(os.listdir(dir_csv)):
            if not nombre.lower().endswith(".csv"):
                continue
            ruta = os.path.join(dir_csv, nombre)
            try:
                origen = "sociedades" if "asociaciones" not in nombre.lower() \
                    else "asociaciones"
                n = indexar_csv(conn, ruta, origen)
            except (ValueError, OSError, csv.Error) as exc:
                resumen[nombre] = f"error: {exc}"
                continue
            resumen[nombre] = n
        return resumen
    finally:
        conn.close()


def _coincidencia(razon: str, variantes: list) -> int:
    """Grado de coincidencia para ordenar: 2 = igual a alguna variante,
    1 = empieza con la variante o la contiene como subcadena de palabras
    completas (espacios conservados), 0 = solo comparte tokens sueltos."""
    n = normalizar_razon(razon)
    for v in variantes:
        nv = normalizar_razon(v)
        if n == nv:
            return 2
        if n.startswith(nv) or nv in n:
            return 1
    return 0


def buscar(db_path: str, nombre: str, limite: int = 20) -> list:
    """Busca por nombre en la base local. Devuelve lista de dicts
    {cuit, razon_social, tipo_societario, fecha_contrato,
    numero_inscripcion, dom_provincia, dom_localidad, origen,
    coincidencia}. Orden: coincidencia exacta > prefijo > FTS."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"base {db_path} no existe: ejecuta 'python3 rns.py descargar' "
            "y 'python3 rns.py indexar' primero (o 'rns.py auto')")
    variantes = [nombre, sin_sufijo_legal(nombre)]
    variantes = list(dict.fromkeys(variantes))
    conn = sqlite3.connect(db_path)
    try:
        filas = []
        for v in variantes:
            q = armar_query_fts(v)
            if not q:
                continue
            try:
                cur = conn.execute(
                    "SELECT e.cuit, e.razon_social, e.tipo_societario, "
                    "e.fecha_contrato, e.numero_inscripcion, e.dom_provincia, "
                    "e.dom_localidad, e.origen FROM empresas_fts f JOIN "
                    "empresas e ON e.rowid = f.rowid WHERE empresas_fts MATCH ? "
                    "ORDER BY bm25(empresas_fts) LIMIT ?", (q, limite * 3))
                filas += cur.fetchall()
            except sqlite3.OperationalError:
                continue
        vistos = {}
        for f in filas:
            clave = (f[0] or "") + "|" + f[1]
            if clave in vistos:
                continue
            vistos[clave] = f
        resultados = []
        for f in vistos.values():
            resultados.append({
                "cuit": f[0], "razon_social": f[1],
                "tipo_societario": f[2], "fecha_contrato": f[3],
                "numero_inscripcion": f[4], "dom_provincia": f[5],
                "dom_localidad": f[6], "origen": f[7],
                "coincidencia": _coincidencia(f[1], variantes),
            })
        resultados.sort(key=lambda r: (-r["coincidencia"], r["razon_social"]))
        return resultados[:limite]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Descarga y extraccion
# ---------------------------------------------------------------------------

def descargar_zip(url: str, destino: str, timeout_s: float = 600.0) -> str:
    """Descarga un ZIP a 'destino' y valida que sea un ZIP real. Devuelve la
    ruta. Error -> OSError/ValueError con el detalle real."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "better-ocr-rns/1.0 (verificacion de empresas)",
        "Accept": "application/zip,*/*"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        with open(destino, "wb") as f:
            while True:
                bloque = r.read(1 << 20)
                if not bloque:
                    break
                f.write(bloque)
    if not zipfile.is_zipfile(destino):
        raise ValueError(f"{destino}: no es un ZIP valido")
    return destino


def _extraer_zip(zip_path: str, dir_destino: str) -> list:
    """Extrae los CSVs del ZIP al directorio. Devuelve las rutas extraidas."""
    os.makedirs(dir_destino, exist_ok=True)
    extraidos = []
    with zipfile.ZipFile(zip_path) as z:
        for nombre in z.namelist():
            if not nombre.lower().endswith(".csv"):
                continue
            destino = os.path.join(dir_destino, os.path.basename(nombre))
            with z.open(nombre) as src, open(destino, "wb") as out:
                out.write(src.read())
            extraidos.append(destino)
    return extraidos


def descargar_base(dir_zip: str = DIR_CSV, recursos: list = None,
                   todos: bool = False, timeout_s: float = 600.0) -> list:
    """Descarga los ZIP de la base oficial y extrae los CSVs al directorio.
    Devuelve la lista de CSVs extraidos. Las descargas de sociedades pesan
    cientos de MB (P2.5): avisar antes de ejecutar."""
    if recursos is None:
        recursos = list(URLS_RNS.keys()) if todos else RECURSOS_DEFECTO
    os.makedirs(dir_zip, exist_ok=True)
    csvs = []
    for clave in recursos:
        url = URLS_RNS.get(clave)
        if not url:
            continue
        nombre_zip = os.path.join(dir_zip, f"{clave}.zip")
        if not os.path.exists(nombre_zip):
            print(f"descargando {clave} ...")
            t0 = time.monotonic()
            descargar_zip(url, nombre_zip, timeout_s=timeout_s)
            print(f"  {clave}: ok ({time.time() - t0:.0f}s descarga)")
        else:
            print(f"{clave}: ya descargado ({nombre_zip})")
        csvs += _extraer_zip(nombre_zip, dir_zip)
    return csvs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir_resultado(r: dict) -> None:
    exacta = " EXACTA" if r["coincidencia"] == 2 else (
        " prefijo" if r["coincidencia"] == 1 else "")
    print(f"  {r['razon_social']}{exacta}")
    print(f"    CUIT: {r['cuit'] or 'no publicado'} | "
          f"tipo: {r['tipo_societario'] or 'n/d'}")
    if r["fecha_contrato"]:
        print(f"    contrato social: {r['fecha_contrato']}")
    if r["dom_provincia"] or r["dom_localidad"]:
        print(f"    domicilio fiscal: {r['dom_localidad']}, "
              f"{r['dom_provincia']}".replace(" ,", ","))
    print(f"    fuente: RNS ({r['origen']})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Registro Nacional de Sociedades OFFLINE: descarga la "
                    "base oficial (datos.jus.gob.ar), la indexa en SQLite "
                    "FTS5 y busca por nombre SIN buscadores web ni captchas")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_desc = sub.add_parser("descargar", help="bajar los ZIP oficiales y "
                             "extraer los CSVs")
    p_desc.add_argument("--todos", action="store_true",
                        help="todos los recursos 2019-2026 (~2.5 GB); "
                             "default: sociedades+asociaciones 2026")
    p_desc.add_argument("--dir", default=DIR_CSV,
                        help="directorio destino (default: datos-rns/)")

    p_idx = sub.add_parser("indexar", help="construir la base SQLite FTS5 "
                            "desde los CSVs del directorio")
    p_idx.add_argument("--dir", default=DIR_CSV)
    p_idx.add_argument("--db", default=BASE_DB)

    p_bus = sub.add_parser("buscar", help="buscar por razon social/nombre")
    p_bus.add_argument("nombre", help="razon social o nombre comercial")
    p_bus.add_argument("--db", default=BASE_DB)
    p_bus.add_argument("--limite", type=int, default=20)
    p_bus.add_argument("--json", action="store_true",
                       help="salida JSON (para integrar en otras herramientas)")

    p_auto = sub.add_parser("auto", help="descargar + indexar + buscar")
    p_auto.add_argument("nombre")
    p_auto.add_argument("--db", default=BASE_DB)
    p_auto.add_argument("--dir", default=DIR_CSV)
    p_auto.add_argument("--limite", type=int, default=20)
    args = parser.parse_args()

    if args.comando == "descargar":
        csvs = descargar_base(dir_zip=args.dir, todos=args.todos)
        print(f"[OK] {len(csvs)} CSV extraidos en {args.dir}/")
        for c in sorted(csvs):
            print(f"  - {c}")
        print("\nSiguiente paso: python3 rns.py indexar --dir " + args.dir)
    elif args.comando == "indexar":
        resumen = indexar_directorio(args.db, args.dir)
        for nombre, n in resumen.items():
            estado = f"{n} filas indexadas" if isinstance(n, int) else n
            print(f"  {nombre}: {estado}")
        conn = sqlite3.connect(args.db)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM empresas").fetchone()[0]
        finally:
            conn.close()
        print(f"[OK] Base {args.db}: {total} empresas indexadas")
    elif args.comando == "buscar":
        try:
            resultados = buscar(args.db, args.nombre, limite=args.limite)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(resultados, ensure_ascii=False, indent=2))
        else:
            print(f"== RNS: '{args.nombre}' -> {len(resultados)} resultado(s)")
            for r in resultados:
                _imprimir_resultado(r)
            if not resultados:
                print("  (sin coincidencias: no consta en el RNS como "
                      "sociedad ni asociacion; ver limitaciones en "
                      "docs/LECCIONES-APRENDIDAS.md)")
    elif args.comando == "auto":
        csvs = descargar_base(dir_zip=args.dir)
        resumen = indexar_directorio(args.db, args.dir)
        print(f"[OK] {len(csvs)} CSV, base {args.db}: "
              f"{sum(n for n in resumen.values() if isinstance(n, int))} filas")
        resultados = buscar(args.db, args.nombre, limite=args.limite)
        print(f"== RNS: '{args.nombre}' -> {len(resultados)} resultado(s)")
        for r in resultados:
            _imprimir_resultado(r)


if __name__ == "__main__":
    main()
