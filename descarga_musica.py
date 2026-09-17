#!/usr/bin/env python3
"""Descarga de musica clasica gratuita y legal desde Internet Archive
y partituras PDF desde Mutopia Project.

Por que existe: Faristol necesita extractos de audio de dominio publico para
demostraciones y pruebas. Las fuentes principales (Musopen 403, Musiqpub
caido) requieren autenticacion o estan caidas. Internet Archive ofrece
grabaciones de alta calidad sin registro, con licencias public domain o CC.
Mutopia Project ofrece partituras PDF de dominio publico.

Fuentes verificadas en vivo el 2026-09-05:
- Internet Archive: Search API + metadata API funcionan, MP3s descargables.
  URL de descarga: https://{server}{dir}/{filename}
- Mutopia Project: PDFs descargables sin registro.
  URL: https://www.mutopiaproject.org/ftp/{path}/{name}-a4.pdf

Uso:
    python3 descarga_musica.py demo                 # 10 piezas + PDFs
    python3 descarga_musica.py demo --sin-partituras  # solo audio
    python3 descarga_musica.py buscar "Bach piano"  # busqueda libre
    python3 descarga_musica.py buscar "Vivaldi" --limite 5
    python3 descarga_musica.py partituras           # catalogo PDF por estilo
    python3 descarga_musica.py partituras --estilos Baroque,Classical
    python3 descarga_musica.py partituras --dry-run # solo listar
    python3 descarga_musica.py listar-fuentes       # fuentes disponibles
"""

import argparse
import html
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

__all__ = [
    "ARCHIVE_SEARCH_URL", "ARCHIVE_METADATA_URL", "MUTOPIA_FTP_BASE",
    "MUTOPIA_TABLE_URL", "DIR_MUSICA", "ESTILOS_MUTOPIA", "SLUG_ESTILO",
    "buscar_archive", "obtener_metadata", "archivos_mp3",
    "construir_url_descarga", "descargar_archivo", "descargar_lista",
    "descargar_partitura", "PIEZAS_DEMO",
    "parsear_pagina_mutopia", "buscar_catalogo_mutopia",
    "descargar_bytes", "descargar_zip_pdfs", "descargar_catalogo_mutopia",
]

# ---------------------------------------------------------------------------
# Constantes (URLs verificadas en vivo 2026-09-05)
# ---------------------------------------------------------------------------

ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA_URL = "https://archive.org/metadata/{identifier}"
MUTOPIA_FTP_BASE = "https://www.mutopiaproject.org/ftp"
# Listado HTML del catalogo de Mutopia (10 piezas por pagina, paginado con
# startat=1,11,21...). Verificado en vivo 2026-09-17.
MUTOPIA_TABLE_URL = "https://www.mutopiaproject.org/cgibin/make-table.cgi"

# Estilos de Mutopia (opciones reales del buscador avanzado, verificadas en
# vivo 2026-09-17). Gospel existe en el formulario pero no tiene piezas.
ESTILOS_MUTOPIA = [
    "Baroque", "Classical", "Romantic", "Modern", "Renaissance", "Folk",
    "Hymn", "Jazz", "March", "Popular / Dance", "Song", "Technique",
]

# Nombre de subcarpeta por estilo (minusculas, sin acentos ni espacios).
SLUG_ESTILO = {
    "Baroque": "barroco",
    "Classical": "clasico",
    "Romantic": "romantico",
    "Modern": "moderno",
    "Renaissance": "renacimiento",
    "Folk": "folk",
    "Hymn": "himnos",
    "Jazz": "jazz",
    "March": "marchas",
    "Popular / Dance": "popular_danza",
    "Song": "canciones",
    "Technique": "tecnica",
}

DIR_MUSICA = "musica"
RATE_LIMIT_SEG = 1.0          # 1 request/segundo (cortesia)
MAX_TAMANIO_MB = 500           # limite de seguridad por archivo

# Piezas demo seleccionadas: grabaciones reales verificadas en Archive.org
# (identifiers obtenidos de la Search API, metadata confirmada).
# Partituras PDF verificadas en Mutopia Project (2026-09-05).
PIEZAS_DEMO = [
    {
        "titulo": "Goldberg Variations BWV 988 (completas)",
        "artista": "Wanda Landowska, clave",
        "identifier": "BachGoldbergVariations",
        "licencia": "public domain",
        "partitura": "BachJS/BWV988/bwv-988-aria/bwv-988-aria-a4.pdf",
    },
    {
        "titulo": "Violin Partita No. 3 BWV 1006 - Preludio",
        "artista": "Jaime Laredo, violin",
        "identifier": "jaime-laredo-04-violin-partita-no.-3-in-e-major-bwv-1006-iv.-menuet-i-ii",
        "licencia": "public domain",
        "archivo_idx": 0,   # primer MP3 del item
    },
    {
        "titulo": "Violin Partita No. 3 BWV 1006 - Gavotte en Rondeau",
        "artista": "Jaime Laredo, violin",
        "identifier": "jaime-laredo-04-violin-partita-no.-3-in-e-major-bwv-1006-iv.-menuet-i-ii",
        "licencia": "public domain",
        "archivo_idx": 2,   # tercer MP3 del item
    },
    {
        "titulo": "Violin Partita No. 3 BWV 1006 - Bourree",
        "artista": "Jaime Laredo, violin",
        "identifier": "jaime-laredo-04-violin-partita-no.-3-in-e-major-bwv-1006-iv.-menuet-i-ii",
        "licencia": "public domain",
        "archivo_idx": 4,   # quinto MP3 del item
    },
    {
        "titulo": "Brandenburg Concerto No. 1 BWV 1046",
        "artista": "J.S. Bach",
        "identifier": "jamendo-579247",
        "licencia": "CC BY-NC-ND 3.0",
    },
    {
        "titulo": "Piano Sonata No. 14 'Claro de Luna'",
        "artista": "Ludwig van Beethoven",
        "identifier": "MoonlightSonata_755",
        "licencia": "public domain",
        "partitura": "BeethovenLv/O27/moonlight/moonlight-a4.pdf",
    },
    {
        "titulo": "Las Cuatro Estaciones - Primavera RV 269",
        "artista": "Antonio Vivaldi",
        "identifier": "VivaldiSpring",
        "licencia": "public domain",
    },
    {
        "titulo": "Canon en Re Mayor",
        "artista": "Johann Pachelbel",
        "identifier": "jamendo-617470",
        "licencia": "CC BY-NC-ND 3.0",
    },
    {
        "titulo": "Gymnopedie No. 1",
        "artista": "Erik Satie",
        "identifier": "erik-satie-gymnopedie-no.-1_202211",
        "licencia": "public domain",
    },
    {
        "titulo": "Clair de Lune",
        "artista": "Claude Debussy",
        "identifier": "debussy-clair-de-lunemp-3j.cc",
        "licencia": "public domain",
        "partitura": "DebussyC/L75/debussy_Ste_Bergamesq_Clair/debussy_Ste_Bergamesq_Clair-a4.pdf",
    },
]

# ---------------------------------------------------------------------------
# Funciones de busqueda y descarga (stdlib puro, sin dependencias externas)
# ---------------------------------------------------------------------------


def buscar_archive(query, limite=10):
    """Busca audio clasico en Internet Archive via la Search API.

    Retorna lista de dicts con keys: identifier, title, creator, licenseurl.
    Verificada en vivo 2026-09-05: la API responde JSON con estos campos.
    """
    params = {
        "q": f'{query} mediatype:audio',
        "fl[]": ["identifier", "title", "creator", "licenseurl"],
        "rows": str(limite),
        "output": "json",
    }
    # urllib no maneja listas bien en params, construimos la URL manualmente
    fl = "&".join(f"fl%5B%5D={f}" for f in params["fl[]"])
    url = (f"{ARCHIVE_SEARCH_URL}?q={urllib.parse.quote(params['q'])}"
           f"&{fl}&rows={limite}&output=json")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "better-ocr/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", {}).get("docs", [])
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] Busqueda fallida: {e}", file=sys.stderr)
        return []


def obtener_metadata(identifier):
    """Obtiene metadata completa de un item de Internet Archive.

    Retorna dict con keys: server, dir, files (lista), metadata.
    Verificada en vivo 2026-09-05: la API devuelve JSON con estos campos.
    """
    url = ARCHIVE_METADATA_URL.format(identifier=identifier)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "better-ocr/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] Metadata fallida para {identifier}: {e}",
              file=sys.stderr)
        return {}


def archivos_mp3(metadata):
    """Filtra archivos MP3 de la metadata de un item de Archive.org.

    Retorna lista de dicts con keys: nombre, size, length, url_descarga.
    Solo incluye formatos MP3 (VBR MP3, MP3), NO Ogg/Flac/AIFF.
    Verificada en vivo 2026-09-05: los campos son name, format, size,
    length, y la URL se construye con server+dir+name.
    """
    if not metadata or "files" not in metadata:
        return []
    server = metadata.get("server", "")
    directorio = metadata.get("dir", "")
    resultados = []
    for f in metadata.get("files", []):
        fmt = f.get("format", "")
        if fmt in ("VBR MP3", "MP3") or fmt.endswith("MP3"):
            nombre = f.get("name", "")
            size = int(f.get("size", 0))
            length = float(f.get("length", 0))
            url = f"https://{server}{directorio}/{urllib.parse.quote(nombre)}"
            resultados.append({
                "nombre": nombre,
                "size": size,
                "length": length,
                "url_descarga": url,
            })
    return resultados


def construir_url_descarga(metadata, archivo_idx=0):
    """Construye la URL de descarga para un archivo MP3 especifico.

    Si archivo_idx esta fuera de rango, usa el primer MP3 disponible.
    Retorna (url, nombre_archivo) o (None, None) si no hay MP3s.
    """
    mp3s = archivos_mp3(metadata)
    if not mp3s:
        return None, None
    idx = min(archivo_idx, len(mp3s) - 1)
    return mp3s[idx]["url_descarga"], mp3s[idx]["nombre"]


def descargar_archivo(url, destino, tamanio_max_mb=MAX_TAMANIO_MB):
    """Descarga un archivo desde una URL con protecciones.

    - Verifica Content-Length antes de descargar (anti DoS).
    - Muestra progreso en stderr.
    - Retorna True si la descarga fue exitosa.
    """
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "better-ocr/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            # Verificar tamano
            total = resp.headers.get("Content-Length")
            if total:
                total_mb = int(total) / (1024 * 1024)
                if total_mb > tamanio_max_mb:
                    print(f"  [SKIP] Archivo demasiado grande: "
                          f"{total_mb:.1f} MB > {tamanio_max_mb} MB",
                          file=sys.stderr)
                    return False

            # Crear directorio destino
            os.makedirs(os.path.dirname(destino) if os.path.dirname(destino)
                        else ".", exist_ok=True)

            # Descargar con progreso
            bytes_descargados = 0
            with open(destino, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_descargados += len(chunk)
                    if total:
                        pct = bytes_descargados / int(total) * 100
                        print(f"\r  [{pct:5.1f}%] {bytes_descargados / 1024:.0f} KB",
                              end="", file=sys.stderr, flush=True)
            print(file=sys.stderr)  # nueva linea
            return True
    except (urllib.error.URLError, OSError) as e:
        print(f"  [ERROR] Descarga fallida: {e}", file=sys.stderr)
        return False


def descargar_partitura(partitura_path, destino):
    """Descarga una partitura PDF desde Mutopia Project.

    partitura_path: ruta relativa dentro del FTP de Mutopia
                    (ej: "BeethovenLv/O27/moonlight/moonlight-a4.pdf").
    Retorna True si la descarga fue exitosa.
    """
    if not partitura_path:
        return False
    url = f"{MUTOPIA_FTP_BASE}/{partitura_path}"
    print(f"  Partitura: {url}")
    return descargar_archivo(url, destino)


# ---------------------------------------------------------------------------
# Catalogo completo de Mutopia por estilo (partituras PDF)
# ---------------------------------------------------------------------------

_RE_RESULT_TABLE = re.compile(
    r'<table class="table-bordered result-table">(.*?)</table>', re.S)
_RE_TITULO_COMPOSITOR = re.compile(
    r'<tr><td>(.*?)</td>\s*<td>(.*?)</td>', re.S)
_RE_INSTRUMENTO = re.compile(r'<td>for\s+(.*?)</td>', re.S)
_RE_ESTILO = re.compile(
    r'<td>for\s+.*?</td>\s*<td>.*?</td>\s*<td>(.*?)</td>', re.S)
_RE_PIECE_ID = re.compile(r'piece-info\.cgi\?id=(\d+)')
_RE_PDF_A4 = re.compile(
    r'href="(https://www\.mutopiaproject\.org/ftp/[^"]+-a4\.pdf)"')
_RE_ZIP_A4 = re.compile(
    r'href="(https://www\.mutopiaproject\.org/ftp/[^"]+-a4-pdfs\.zip)"')


def _limpiar_texto(fragmento):
    """Quita tags HTML, desescapa entidades y normaliza espacios."""
    sin_tags = re.sub(r'<[^>]+>', '', fragmento)
    return re.sub(r'\s+', ' ', html.unescape(sin_tags)).strip()


def _nombre_archivo(texto, maximo=80):
    """Convierte un texto en nombre de archivo seguro (sin acentos).

    Mantiene letras, digitos, espacios (-> guion bajo) y guiones.
    """
    normalizado = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in normalizado
                          if not unicodedata.combining(c))
    limpio = re.sub(r'[^\w\s\-]', '', sin_acentos)
    return re.sub(r'\s+', '_', limpio).strip('_')[:maximo]


def parsear_pagina_mutopia(html_texto):
    """Parsea una pagina de make-table.cgi del catalogo de Mutopia.

    Retorna una lista de dicts con keys: id, titulo, compositor,
    instrumento, estilo, pdf (URL A4) y zip (URL del zip A4 con PDFs).
    pdf y zip son mutuamente excluyentes en la practica; al menos uno
    puede ser None. Verificada contra HTML real el 2026-09-17.
    """
    piezas = []
    for bloque in _RE_RESULT_TABLE.findall(html_texto):
        m_tc = _RE_TITULO_COMPOSITOR.search(bloque)
        if not m_tc:
            continue
        titulo = _limpiar_texto(m_tc.group(1))
        # El compositor puede venir "by X" o sin prefijo (p. ej. "Anonymous").
        compositor = re.sub(r'^by\s+', '',
                            _limpiar_texto(m_tc.group(2)))
        m_instr = _RE_INSTRUMENTO.search(bloque)
        m_estilo = _RE_ESTILO.search(bloque)
        m_id = _RE_PIECE_ID.search(bloque)
        m_pdf = _RE_PDF_A4.search(bloque)
        m_zip = _RE_ZIP_A4.search(bloque)
        if not m_id or not titulo:
            continue
        piezas.append({
            "id": int(m_id.group(1)),
            "titulo": titulo,
            "compositor": compositor,
            "instrumento": _limpiar_texto(m_instr.group(1)) if m_instr else "",
            "estilo": _limpiar_texto(m_estilo.group(1)) if m_estilo else "",
            "pdf": m_pdf.group(1) if m_pdf else None,
            "zip": m_zip.group(1) if m_zip else None,
        })
    return piezas


def _fetch_tabla_mutopia(estilo, startat):
    """Descarga una pagina del listado de Mutopia para un estilo.

    Retorna el HTML como texto, o cadena vacia si la peticion falla
    (el error se reporta en stderr; nunca se inventa contenido).
    """
    params = {
        "startat": str(startat), "searchingfor": "", "Composer": "",
        "Instrument": "", "Style": estilo, "collection": "", "id": "",
        "solo": "", "recent": "", "timelength": "", "timeunit": "",
        "lilyversion": "", "preview": "",
    }
    url = f"{MUTOPIA_TABLE_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "better-ocr/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        print(f"[ERROR] Catalogo Mutopia fallo ({estilo}, startat={startat}): {e}",
              file=sys.stderr)
        return ""


def buscar_catalogo_mutopia(estilo, max_piezas=None, pausa=RATE_LIMIT_SEG):
    """Recorre las paginas del catalogo de Mutopia para un estilo.

    Retorna la lista completa de piezas (dicts de parsear_pagina_mutopia).
    Si una pagina falla, se detiene y devuelve lo recolectado (reportado).
    """
    piezas = []
    startat = 1
    while True:
        pagina = parsear_pagina_mutopia(_fetch_tabla_mutopia(estilo, startat))
        if not pagina:
            break
        piezas.extend(pagina)
        if max_piezas is not None and len(piezas) >= max_piezas:
            return piezas[:max_piezas]
        if len(pagina) < 10:      # ultima pagina
            break
        startat += 10
        time.sleep(pausa)
    return piezas


def descargar_bytes(url, tamanio_max_mb=MAX_TAMANIO_MB):
    """Descarga una URL a memoria (para zips de partituras).

    Verifica Content-Length y el tamano real antes de aceptar.
    Retorna los bytes, o None si falla o supera el limite.
    """
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "better-ocr/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = resp.headers.get("Content-Length")
            if total and int(total) / (1024 * 1024) > tamanio_max_mb:
                print(f"  [SKIP] Zip demasiado grande: "
                      f"{int(total) / (1024 * 1024):.1f} MB > {tamanio_max_mb} MB",
                      file=sys.stderr)
                return None
            datos = resp.read()
            if len(datos) / (1024 * 1024) > tamanio_max_mb:
                print(f"  [SKIP] Zip demasiado grande tras descargar",
                      file=sys.stderr)
                return None
            return datos
    except (urllib.error.URLError, OSError) as e:
        print(f"  [ERROR] Descarga fallida: {e}", file=sys.stderr)
        return None


def descargar_zip_pdfs(url, destino_dir):
    """Descarga un zip A4 de Mutopia y extrae sus PDFs en destino_dir.

    Cada PDF se guarda por su nombre base (anti path traversal).
    Retorna True si se extrajo al menos un PDF.
    """
    datos = descargar_bytes(url)
    if datos is None:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
            if not pdfs:
                print("  [SKIP] El zip no contiene PDFs", file=sys.stderr)
                return False
            os.makedirs(destino_dir, exist_ok=True)
            for nombre in pdfs:
                base = os.path.basename(nombre)
                if not base:
                    continue
                with z.open(nombre) as src, \
                        open(os.path.join(destino_dir, base), "wb") as dst:
                    shutil.copyfileobj(src, dst)
            return True
    except (zipfile.BadZipFile, OSError) as e:
        print(f"  [ERROR] Zip invalido: {e}", file=sys.stderr)
        return False


def descargar_catalogo_mutopia(directorio=os.path.join(DIR_MUSICA, "partituras"),
                               estilos=None, dry_run=False,
                               pausa=RATE_LIMIT_SEG, max_por_estilo=None):
    """Descarga el catalogo de Mutopia en subcarpetas por estilo.

    Cada estilo se guarda en directorio/<slug>/. Las piezas con PDF unico
    van como archivo; las multi-parte (zip) se extraen en una subcarpeta.
    Retorna (descargadas, fallidas, omitidas).
    """
    if estilos is None:
        estilos = ESTILOS_MUTOPIA
    descargadas = 0
    fallidas = 0
    omitidas = 0
    for estilo in estilos:
        slug = SLUG_ESTILO.get(estilo, _nombre_archivo(estilo).lower())
        destino_estilo = os.path.join(directorio, slug)
        print(f"\n{'=' * 60}\nESTILO: {estilo} -> {destino_estilo}\n{'=' * 60}")
        piezas = buscar_catalogo_mutopia(estilo, max_por_estilo, pausa)
        print(f"Piezas en el catalogo: {len(piezas)}")
        for i, p in enumerate(piezas, 1):
            base = f"{p['id']:04d}_{_nombre_archivo(p['titulo'])}"
            comp = _nombre_archivo(p["compositor"])
            etiqueta = f"{base}_{comp}" if comp else base
            if p["pdf"]:
                destino = os.path.join(destino_estilo, f"{etiqueta}.pdf")
                if dry_run:
                    print(f"  [{i}/{len(piezas)}] {p['titulo']} -> {destino}")
                    descargadas += 1
                    continue
                print(f"  [{i}/{len(piezas)}] {p['titulo']}")
                if descargar_archivo(p["pdf"], destino):
                    descargadas += 1
                else:
                    fallidas += 1
            elif p["zip"]:
                destino = os.path.join(destino_estilo, etiqueta)
                if dry_run:
                    print(f"  [{i}/{len(piezas)}] {p['titulo']} (zip) -> {destino}/")
                    descargadas += 1
                    continue
                print(f"  [{i}/{len(piezas)}] {p['titulo']} (zip)")
                if descargar_zip_pdfs(p["zip"], destino):
                    descargadas += 1
                else:
                    fallidas += 1
            else:
                print(f"  [{i}/{len(piezas)}] [SKIP] sin PDF: {p['titulo']}",
                      file=sys.stderr)
                omitidas += 1
            if not dry_run:
                time.sleep(pausa)
    return descargadas, fallidas, omitidas


def descargar_lista(items, directorio=DIR_MUSICA, dry_run=False,
                    con_partituras=True):
    """Descarga una lista de items de Archive.org + PDFs de Mutopia.

    Cada item es un dict con keys最少: identifier, titulo.
    Si el item tiene 'archivo_idx', usa ese indice para selects un MP3
    especifico del item (para items con multiples pistas).
    Si el item tiene 'partitura' y con_partituras=True, descarga el PDF
    desde Mutopia Project.
    Retorna (exitosas, fallidas).
    """
    exitosas = 0
    fallidas = 0
    for i, item in enumerate(items, 1):
        identifier = item["identifier"]
        titulo = item.get("titulo", identifier)
        archivo_idx = item.get("archivo_idx", 0)
        partitura = item.get("partitura")
        print(f"\n[{i}/{len(items)}] {titulo}")
        print(f"  Identifier: {identifier}")

        # Obtener metadata
        time.sleep(RATE_LIMIT_SEG)
        metadata = obtener_metadata(identifier)
        if not metadata:
            print("  [SKIP] No se pudo obtener metadata")
            fallidas += 1
            continue

        # Obtener URL de descarga
        url, nombre = construir_url_descarga(metadata, archivo_idx)
        if not url:
            print("  [SKIP] No se encontraron archivos MP3")
            fallidas += 1
            continue

        print(f"  Archivo: {nombre}")
        print(f"  URL: {url}")

        if dry_run:
            print("  [DRY-RUN] No se descarga")
            if partitura and con_partituras:
                pdf_url = f"{MUTOPIA_FTP_BASE}/{partitura}"
                print(f"  Partitura: {pdf_url}")
            exitosas += 1
            continue

        # Construir nombre de destino
        nombre_limpio = re.sub(r'[^\w\s\-\.]', '_', titulo)
        nombre_limpio = re.sub(r'\s+', '_', nombre_limpio).strip('_')
        extension = os.path.splitext(nombre)[1] or ".mp3"
        destino = os.path.join(directorio, f"{i:02d}_{nombre_limpio}{extension}")

        # Descargar audio
        ok = descargar_archivo(url, destino)
        if ok:
            print(f"  OK -> {destino}")
            exitosas += 1
        else:
            fallidas += 1
            continue

        # Descargar partitura PDF si esta disponible
        if partitura and con_partituras:
            nombre_pdf = re.sub(r'[^\w\s\-\.]', '_', titulo)
            nombre_pdf = re.sub(r'\s+', '_', nombre_pdf).strip('_')
            destino_pdf = os.path.join(directorio,
                                       f"{i:02d}_{nombre_pdf}.pdf")
            if descargar_partitura(partitura, destino_pdf):
                print(f"  PDF -> {destino_pdf}")
            # La partitura es opcional: no cuenta como fallo

    return exitosas, fallidas


def demo(directorio=DIR_MUSICA, dry_run=False, con_partituras=True):
    """Descarga las 10 piezas demo preseleccionadas + PDFs."""
    print("=" * 60)
    print("DESCARGANDO 10 PIEZAS CLASICAS GRATUITAS (DEMO)")
    if con_partituras:
        print("+ PARTITURAS PDF (Mutopia Project)")
    print("=" * 60)
    print(f"Destino: {directorio}/")
    print()
    exitosas, fallidas = descargar_lista(PIEZAS_DEMO, directorio, dry_run,
                                        con_partituras)
    print()
    print("=" * 60)
    print(f"RESUMEN: {exitosas}/{len(PIEZAS_DEMO)} descargadas exitosamente")
    if fallidas:
        print(f"Fallidas: {fallidas}")
    print("=" * 60)
    return exitosas, fallidas


def listar_fuentes():
    """Muestra las fuentes de musica gratuita disponibles."""
    print("FUENTES DE MUSICA GRATUITA VERIFICADAS")
    print("=" * 60)
    print()
    print("1. Internet Archive (archive.org) — AUDIO")
    print("   - Busqueda: API publica sin autenticacion")
    print("   - Formato: MP3, FLAC, Ogg Vorbis")
    print("   - Licencia: public domain, CC (varia por item)")
    print("   - Limite: sin limite de descargas")
    print("   - Estado: VERIFICADO OK (2026-09-05)")
    print()
    print("2. Mutopia Project (mutopiaproject.org) — PARTITURAS PDF")
    print("   - Formato: PDF (A4/Letter), LilyPond, MIDI")
    print("   - Licencia: Public Domain o CC")
    print("   - Descarga directa: sin registro")
    print("   - URL: {base}/{path}/{name}-a4.pdf".format(
        base=MUTOPIA_FTP_BASE, path="...", name="..."))
    print("   - Estado: VERIFICADO OK (2026-09-05)")
    print()
    print("Fuentes NO disponibles:")
    print("  - Musopen: 403 (requiere autenticacion)")
    print("  - Musiqpub: caido (connection refused)")
    print("  - IMSLP: sin API programatica para PDFs")
    print()
    print("Uso recomendado:")
    print("  python3 descarga_musica.py demo              # audio + PDFs")
    print("  python3 descarga_musica.py demo --sin-partituras  # solo audio")
    print("  python3 descarga_musica.py buscar \"Vivaldi\" --limite 5")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Descarga musica clasica gratuita y legal + partituras PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 descarga_musica.py demo                 # audio + PDFs
  python3 descarga_musica.py demo --dry-run       # solo mostrar URLs
  python3 descarga_musica.py demo --sin-partituras  # solo audio
  python3 descarga_musica.py buscar "Bach piano"  # busqueda libre
  python3 descarga_musica.py buscar "Vivaldi" --limite 5
  python3 descarga_musica.py partituras           # catalogo PDF por estilo
  python3 descarga_musica.py partituras --estilos Baroque --max-por-estilo 5
  python3 descarga_musica.py partituras --dry-run # solo listar
  python3 descarga_musica.py listar-fuentes       # fuentes disponibles
""",
    )
    sub = parser.add_subparsers(dest="comando", help="Comando a ejecutar")

    # demo
    p_demo = sub.add_parser("demo", help="Descarga 10 piezas clasica demo")
    p_demo.add_argument("--directorio", default=DIR_MUSICA,
                        help="Directorio destino (default: musica/)")
    p_demo.add_argument("--dry-run", action="store_true",
                        help="Solo muestra URLs, no descarga")
    p_demo.add_argument("--sin-partituras", action="store_true",
                        help="No descargar partituras PDF de Mutopia")

    # buscar
    p_buscar = sub.add_parser("buscar", help="Busca y descarga por consulta")
    p_buscar.add_argument("query", help="Consulta de busqueda (ej: 'Bach piano')")
    p_buscar.add_argument("--limite", type=int, default=10,
                          help="Maximo de resultados (default: 10)")
    p_buscar.add_argument("--directorio", default=DIR_MUSICA,
                          help="Directorio destino (default: musica/)")
    p_buscar.add_argument("--dry-run", action="store_true",
                          help="Solo muestra URLs, no descarga")

    # partituras
    p_part = sub.add_parser(
        "partituras",
        help="Descarga el catalogo completo de partituras PDF de Mutopia "
             "en subcarpetas por estilo")
    p_part.add_argument("--directorio", default=os.path.join(DIR_MUSICA, "partituras"),
                        help="Directorio destino (default: musica/partituras/)")
    p_part.add_argument("--estilos", default="todos",
                        help="Estilos separados por coma o 'todos' "
                             f"(validos: {', '.join(ESTILOS_MUTOPIA)})")
    p_part.add_argument("--dry-run", action="store_true",
                        help="Solo muestra las piezas, no descarga")
    p_part.add_argument("--pausa", type=float, default=RATE_LIMIT_SEG,
                        help="Segundos entre peticiones (default: 1.0)")
    p_part.add_argument("--max-por-estilo", type=int, default=None,
                        help="Limite de piezas por estilo (default: sin limite)")

    # listar-fuentes
    sub.add_parser("listar-fuentes", help="Muestra fuentes disponibles")

    args = parser.parse_args()

    if args.comando == "demo":
        con_partituras = not args.sin_partituras
        demo(args.directorio, args.dry_run, con_partituras)
    elif args.comando == "buscar":
        print(f"Buscando: '{args.query}' en Internet Archive...")
        resultados = buscar_archive(args.query, args.limite)
        if not resultados:
            print("No se encontraron resultados.")
            sys.exit(1)
        print(f"Encontrados: {len(resultados)} items")
        # Convertir resultados al formato de descargar_lista
        items = []
        for r in resultados:
            items.append({
                "titulo": r.get("title", r.get("identifier", "??")),
                "identifier": r["identifier"],
                "artista": r.get("creator", "Desconocido"),
                "licencia": r.get("licenseurl", "verificar"),
            })
        exitosas, fallidas = descargar_lista(items, args.directorio,
                                            args.dry_run)
        print(f"\nRESUMEN: {exitosas}/{len(items)} descargadas")
    elif args.comando == "partituras":
        if args.estilos.strip().lower() == "todos":
            estilos = list(ESTILOS_MUTOPIA)
        else:
            estilos = [e.strip() for e in args.estilos.split(",") if e.strip()]
            invalidos = [e for e in estilos if e not in ESTILOS_MUTOPIA]
            if invalidos:
                print(f"Estilos invalidos: {invalidos}\n"
                      f"Validos: {', '.join(ESTILOS_MUTOPIA)}", file=sys.stderr)
                sys.exit(1)
        descargadas, fallidas, omitidas = descargar_catalogo_mutopia(
            args.directorio, estilos, args.dry_run, args.pausa,
            args.max_por_estilo)
        print(f"\n{'=' * 60}")
        print(f"RESUMEN: {descargadas} descargadas, {fallidas} fallidas, "
              f"{omitidas} omitidas")
        print("=" * 60)
    elif args.comando == "listar-fuentes":
        listar_fuentes()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
