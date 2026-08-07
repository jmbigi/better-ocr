#!/usr/bin/env python3
"""Servicio de resolución de reCAPTCHA v2 con el stack local (RT-DETR + VLM).

Modo REAL (default): Playwright con contexto desechable -> checkbox -> reto en
iframe bframe -> instrucción del DOM (fallback OCR) -> cuadrícula 3x3/4x4 ->
detección por celda (RT-DETR, vision.modo_objetos) -> clics JS en tiles ->
VERIFY/SKIP -> veredicto por estado del checkbox ancla.

Modo LOCAL (--local): demo sintética determinista 3x3, sin navegador.

Este módulo contiene SOLO las piezas puras (parser de instrucción, geometría
de cuadrícula, decisor por celda), testeables sin Playwright ni motores.

Reutiliza vision.modo_objetos (RT-DETR-L) para la detección real.
"""

import re

# Prefijos de instrucción (reCAPTCHA v2, inglés). Orden: más largo primero.
PREFIJOS = [
    "select all", "select every", "select each", "click all", "click every",
    "click each", "choose all", "choose every", "tap all", "tap every",
    "touch all", "touch every", "select", "click", "choose", "tap", "touch",
]

# Estructuras con condición: "if there are X, select them"
RE_CONDICION = re.compile(r"^(?:if|when)\s+(?:there\s+(?:are|is)\s+)?(.+?)\s*,\s*(?:select|click|choose|tap|touch)\s+(?:all|every|them|those)?\s*$")

# Sufijos de instrucción colgante, con y sin espacio (el OCR pega el texto:
# "traffic lightsIf there are none..." -> recortar en "If").
SUFIJOS = [
    "if there are none", "if there is none", "if none", "if you see none",
    "if there are no other", "that are shown", "that contain", "that have",
    "containing", "in the image", "in the picture", "shown below",
    "with the", "below", "shown",
]
RE_SUFIJO_PEGADO = re.compile(
    r"(?:if|when)\s*(?:there\s*(?:are|is)\s*)?(?:none|no|any)", re.IGNORECASE)

ARTICULOS = ["all of the", "all the", "every", "each", "all", "any",
             "the", "a", "an"]

# Plurales irregulares y formas compuestas -> etiqueta canónica singular
# (etiquetas COCO que devuelve RT-DETR). Las que ya son singulares o invarian-
# tes (stairs, sheep) no se tocan.
PLURALES = {
    "buses": "bus",
    "boxes": "box",
    "crosswalks": "crosswalk",
    "chimneys": "chimney",
    "motorcycles": "motorcycle",
    "traffic lights": "traffic light",
    "traffic light": "traffic light",
    "fire hydrants": "fire hydrant",
    "fire hydrant": "fire hydrant",
    "stop signs": "stop sign",
    "stop sign": "stop sign",
    "parking meters": "parking meter",
    "parking meter": "parking meter",
    "cars": "car",
    "bicycles": "bicycle",
    "bikes": "bicycle",
    "trucks": "truck",
    "trains": "train",
    "boats": "boat",
    "airplanes": "airplane",
    "planes": "airplane",
    "birds": "bird",
    "dogs": "dog",
    "cats": "cat",
    "horses": "horse",
    "sheep": "sheep",
    "cows": "cow",
    "elephants": "elephant",
    "bears": "bear",
    "zebras": "zebra",
    "giraffes": "giraffe",
    "crosswalk": "crosswalk",
    "stairs": "stairs",
    "taxi": "taxi",
    "taxis": "taxi",
    "fire hydrants": "fire hydrant",
    "hydrants": "fire hydrant",
    "people": "person",
    "persons": "person",
    "persons": "person",
}

# Palabras en plural regular: quitar la -s final (con excepciones).
EXCEPCIONES_REGULAR = {"bus", "gas", "class", "glass", "status"}


def recortar_sufijos(texto: str) -> str:
    """Quita la instrucción colgante al final ("if there are none...")."""
    t = texto.strip()
    for sufijo in SUFIJOS:
        if t.endswith(sufijo):
            t = t[: -len(sufijo)].rstrip(" ,;:-")
    # texto pegado sin espacio: "traffic lightsIf there are none"
    m = RE_SUFIJO_PEGADO.search(t)
    if m and m.start() > 0:
        t = t[: m.start()].rstrip(" ,;:-")
    return t.strip()


def singularizar(texto: str) -> str:
    """Plural/composición -> etiqueta canónica (bus, traffic light, ...)."""
    t = texto.strip()
    if t in PLURALES:
        return PLURALES[t]
    if t in EXCEPCIONES_REGULAR:
        return t
    # compuestos: "traffic lights" -> "traffic light"
    palabras = t.split()
    if len(palabras) > 1 and palabras[-1].endswith("s"):
        cand = " ".join(palabras[:-1] + [palabras[-1][:-1]])
        if cand in PLURALES.values() or cand in EXCEPCIONES_REGULAR:
            return cand
    # regular: "cars" -> "car"
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        return t[:-1]
    return t


def parsear_instruccion(texto: str):
    """Extrae la clase objetivo de una instrucción reCAPTCHA v2.

    Devuelve la etiqueta canónica singular (str) o None si no hay clase.
    """
    if not texto or not texto.strip():
        return None
    t = texto.strip().lower()
    t = re.sub(r"\s+", " ", t)

    m = RE_CONDICION.match(t)
    if m:
        t = m.group(1).strip()

    for prefijo in PREFIJOS:
        if t.startswith(prefijo):
            t = t[len(prefijo):].strip()
            break

    t = recortar_sufijos(t)
    t = t.strip(" ,;:.")

    for articulo in ARTICULOS:
        if t.startswith(articulo + " "):
            t = t[len(articulo):].strip()
            break

    t = recortar_sufijos(t)
    t = t.strip(" ,;:.")

    # "squares with cars" / "pictures of buses" / "photos with a car"
    # (variante con espacio inicial y sin él: el texto puede empezar justo
    # en el conector tras quitar el prefijo)
    for conector in (" squares with ", "squares with ",
                     " pictures of ", "pictures of ",
                     " pictures with ", "pictures with ",
                     " photos of ", "photos of ",
                     " photos with ", "photos with ",
                     " images of ", "images of ",
                     " images with ", "images with ",
                     " tiles with ", "tiles with ",
                     " with ", "with "):
        idx = t.find(conector)
        if idx >= 0:
            t = t[idx + len(conector):].strip()
            break

    # tras el conector puede quedar un artículo ("images with a bus" -> "a bus")
    for articulo in ARTICULOS:
        if t.startswith(articulo + " "):
            t = t[len(articulo):].strip()
            break

    t = recortar_sufijos(t).strip(" ,;:.")
    if not t:
        return None
    canonico = singularizar(t)
    # Palabras que no son clases (solo soporte de la frase, no el objeto)
    if canonico in {"image", "images", "picture", "pictures", "photo",
                    "photos", "tile", "tiles", "square", "squares", "here",
                    "this", "these", "those", "them", "all", "everything",
                    "none", "object", "objects", "her", "him", "it"}:
        return None
    return canonico
