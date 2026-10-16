#!/usr/bin/env python3
"""Búsqueda académica sin navegador ni API key: arXiv y Crossref.

Complementa a `buscador.py` (motores web con Playwright) con dos APIs públicas
de literatura científica. Devuelve el MISMO formato de resultado que los motores
web — ``{motor, posicion, titulo, url, snippet, anio, doi}`` — para que
``buscador.deduplicar()`` y ``buscador.armar_sintesis()`` puedan mezclar ambos
mundos sin adaptadores.

APIs (verificadas en vivo el 2026-10-16):
- arXiv: Atom oficial en ``https://export.arxiv.org/api/query``. Ojo: ``http://``
  responde 301, se usa https directamente.
- Crossref: REST en ``https://api.crossref.org/works``. El estado va en la RAÍZ
  (``status``), no dentro de ``message``.

- Solo stdlib (urllib + ElementTree); sin red al importar.
- Los errores de red o de formato se ELEVAN con contexto (P1.19): nunca se
  devuelve una lista vacía silenciosa.

Uso:
    python3 academico.py "extracción de datos de gráficos"
    python3 academico.py "..." --fuente arxiv --max 3 --json
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
USER_AGENT = "better-ocr/1.0 (+https://github.com/jmbigi/better-ocr)"
TIMEOUT = 30
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _limpiar(texto: str) -> str:
    """Colapsa espacios: las respuestas traen saltos y sangría."""
    return re.sub(r"\s+", " ", texto or "").strip()


def _sin_etiquetas(texto: str) -> str:
    """Quita el marcado JATS/HTML que Crossref devuelve en `abstract`."""
    return _limpiar(re.sub(r"<[^>]+>", " ", texto or ""))


def _pedir(url: str, timeout: int = TIMEOUT) -> bytes:
    """GET con User-Agent propio; eleva RuntimeError con contexto si falla."""
    peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.read()
    except Exception as exc:  # noqa: BLE001 - se reeleva con contexto (P1.19)
        raise RuntimeError(
            f"Fallo consultando {url}: {type(exc).__name__}: {exc}") from exc


def parsear_arxiv(xml: bytes, max_resultados: int = 5) -> list:
    """Convierte la respuesta Atom de arXiv al formato de resultado común."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError(f"Respuesta de arXiv no es XML válido: {exc}") from exc

    resultados = []
    entradas = raiz.findall(f"{ATOM}entry")[:max_resultados]
    for posicion, entrada in enumerate(entradas, start=1):
        publicado = _limpiar(entrada.findtext(f"{ATOM}published", ""))
        autores = [_limpiar(a.findtext(f"{ATOM}name", ""))
                   for a in entrada.findall(f"{ATOM}author")]
        resultados.append({
            "motor": "arxiv",
            "posicion": posicion,
            "titulo": _limpiar(entrada.findtext(f"{ATOM}title", "")),
            "url": _limpiar(entrada.findtext(f"{ATOM}id", "")),
            "snippet": _limpiar(entrada.findtext(f"{ATOM}summary", "")),
            "anio": publicado[:4],
            "doi": _limpiar(entrada.findtext(f"{ARXIV_NS}doi", "")),
            "autores": [a for a in autores if a][:3],
        })
    return resultados


def parsear_crossref(datos: bytes, max_resultados: int = 5) -> list:
    """Convierte la respuesta JSON de Crossref al formato de resultado común."""
    try:
        payload = json.loads(datos)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respuesta de Crossref no es JSON válido: {exc}") from exc

    if payload.get("status") != "ok":
        raise RuntimeError(f"Crossref devolvió status={payload.get('status')!r}")

    items = payload.get("message", {}).get("items", [])[:max_resultados]
    resultados = []
    for posicion, item in enumerate(items, start=1):
        doi = _limpiar(item.get("DOI", ""))
        titulos = item.get("title") or []
        contenedores = item.get("container-title") or []
        resumen = _sin_etiquetas(item.get("abstract", ""))
        if not resumen and contenedores:
            resumen = _limpiar(contenedores[0])
        fechas = (item.get("issued") or {}).get("date-parts") or []
        anio = str(fechas[0][0]) if fechas and fechas[0] and fechas[0][0] else ""
        resultados.append({
            "motor": "crossref",
            "posicion": posicion,
            "titulo": _limpiar(titulos[0] if titulos else ""),
            "url": f"https://doi.org/{doi}" if doi else "",
            "snippet": resumen,
            "anio": anio,
            "doi": doi,
        })
    return resultados


def buscar_arxiv(consulta: str, max_resultados: int = 5,
                 timeout: int = TIMEOUT) -> list:
    """Busca en arXiv (título o resumen) y devuelve resultados comunes."""
    if not consulta or not consulta.strip():
        raise ValueError("La consulta no puede estar vacía")
    termino = urllib.parse.quote(f'ti:"{consulta}" OR abs:"{consulta}"')
    url = (f"{ARXIV_API}?search_query={termino}"
           f"&max_results={int(max_resultados)}&sortBy=relevance")
    return parsear_arxiv(_pedir(url, timeout), max_resultados)


def buscar_crossref(consulta: str, max_resultados: int = 5,
                    timeout: int = TIMEOUT) -> list:
    """Busca en Crossref por referencia bibliográfica."""
    if not consulta or not consulta.strip():
        raise ValueError("La consulta no puede estar vacía")
    url = (f"{CROSSREF_API}?query.bibliographic={urllib.parse.quote(consulta)}"
           f"&rows={int(max_resultados)}"
           "&select=DOI,title,abstract,container-title,issued")
    return parsear_crossref(_pedir(url, timeout), max_resultados)


def buscar_academico(consulta: str, fuentes=("arxiv", "crossref"),
                     max_resultados: int = 5, timeout: int = TIMEOUT) -> list:
    """Consulta las fuentes pedidas y concatena sus resultados."""
    resultados = []
    for fuente in fuentes:
        if fuente == "arxiv":
            resultados.extend(buscar_arxiv(consulta, max_resultados, timeout))
        elif fuente == "crossref":
            resultados.extend(buscar_crossref(consulta, max_resultados, timeout))
        else:
            raise ValueError(f"Fuente desconocida: {fuente!r} (arxiv, crossref)")
    return resultados


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Búsqueda académica (arXiv y Crossref), sin navegador ni key.")
    parser.add_argument("consulta", help="tema o cita a buscar")
    parser.add_argument("--fuente", choices=["arxiv", "crossref", "ambos"],
                        default="ambos")
    parser.add_argument("--max", type=int, default=5,
                        help="máximo de resultados POR fuente (por defecto 5)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    fuentes = ("arxiv", "crossref") if args.fuente == "ambos" else (args.fuente,)
    try:
        resultados = buscar_academico(args.consulta, fuentes, args.max, args.timeout)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
        return 0

    if not resultados:
        print("Sin resultados.")
        return 0
    for r in resultados:
        print(f"{r['posicion']:>2}. [{r['motor']}] {r['titulo']} ({r['anio']})")
        print(f"    {r['url']}")
        print(f"    {r['snippet'][:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
