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

# Accion explicita de SKIP (variante "si no hay ninguna"): no hay clase
# objetivo que seleccionar.
RE_SKIP = re.compile(r"(?:click|press)\s+skip\b")

# Sufijos de instrucción colgante, con y sin espacio (el OCR pega el texto:
# "traffic lightsIf there are none..." -> recortar en "If").
SUFIJOS = [
    "click verify once there are none left",
    "click verify once there is none left",
    "click verify once there are no more",
    "click verify when there are none left",
    "click verify when there is none left",
    "click verify once you have selected all",
    "then click verify once there are none left",
    "then click verify",
    "click verify",
    "if there are none", "if there is none", "if none", "if you see none",
    "if there are no other", "that are shown", "that contain", "that have",
    "containing", "in the image", "in the picture", "shown below",
    "with the", "below", "shown",
]
RE_SUFIJO_PEGADO = re.compile(
    r"(?:if|when|once)\s*(?:there\s*(?:are|is)\s*)?(?:none|no|any)"
    r"|(?:then\s+)?click\s+verify\b", re.IGNORECASE)

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
    # "…bridges Then click verify once there are none left" -> queda "then"
    t = re.sub(r"\s+(?:and\s+)?then$", "", t, flags=re.I)
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


# Clases COCO multi-palabra que SI son clases validas (el resto de textos
# con espacios tras singularizar son instrucciones no reconocidas: el parser
# es ingles y una instruccion en otro idioma no debe producir basura — una
# clase imposible dejaria la seleccion vacia y VERIFY se ignoraria en
# silencio, hallazgo 2 de la leccion 20).
CLASES_MULTI_PALABRA = {
    "traffic light", "fire hydrant", "stop sign", "parking meter",
    "sports ball", "baseball bat", "baseball glove", "tennis racket",
    "wine glass", "hot dog", "potted plant", "dining table", "cell phone",
    "teddy bear", "hair drier", "remote control",
}


def parsear_instruccion(texto: str):
    """Extrae la clase objetivo de una instrucción reCAPTCHA v2.

    Devuelve la etiqueta canónica singular (str) o None si no hay clase.
    """
    if not texto or not texto.strip():
        return None
    t = texto.strip().lower()
    t = re.sub(r"\s+", " ", t)

    if RE_SKIP.search(t):
        return None

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
    # Textos con espacios que no son una clase COCO multi-palabra conocida:
    # instruccion en otro idioma o sin clase (parser ingles) -> None
    if " " in canonico and canonico not in CLASES_MULTI_PALABRA:
        return None
    # Palabras que no son clases (solo soporte de la frase, no el objeto)
    if canonico in {"image", "images", "picture", "pictures", "photo",
                    "photos", "tile", "tiles", "square", "squares", "here",
                    "this", "these", "those", "them", "all", "everything",
                    "none", "object", "objects", "her", "him", "it"}:
        return None
    return canonico


def aumentar_escala(imagen, factor: int = 2):
    """Upscale del tile con LANCZOS: los objetos pequenos de los retos
    (3x3/4x4) puntuan ~0.5-0.6 y con 2x el detector los ve mejor."""
    from PIL import Image as PILImage  # import perezoso (dependencia opcional)

    return imagen.resize((imagen.width * factor, imagen.height * factor),
                         resample=PILImage.LANCZOS)


def celdas_grid(imagen, n: int = 3, margen_frac: float = 0.06):
    """Divide la imagen en n x n celdas cuadradas con un margen interno por
    celda (excluye bordes/lineas de la cuadricula del reto).

    Devuelve lista de (fila, col, imagen_recortada).
    """
    assert n in (2, 3, 4, 5), f"cuadricula {n}x{n} no soportada"
    ancho, alto = imagen.size
    tam_celda = min(ancho, alto) // n
    margen = max(1, int(tam_celda * margen_frac))
    offset_x = (ancho - tam_celda * n) // 2
    offset_y = (alto - tam_celda * n) // 2
    celdas = []
    for fila in range(n):
        for col in range(n):
            x0 = offset_x + col * tam_celda
            y0 = offset_y + fila * tam_celda
            celdas.append((fila, col, imagen.crop(
                (x0 + margen, y0 + margen,
                 x0 + tam_celda - margen, y0 + tam_celda - margen))))
    return celdas


def clasificar_celda(detecciones: list, clase_objetivo: str,
                     umbral_objetivo: float = 0.45,
                     umbral_resto: float = 0.6) -> str:
    """Clasifica una celda: 'objetivo' | 'otra' | 'incierta'.

    - 'objetivo': alguna deteccion de la clase buscada con score >= umbral
      objetivo (los objetos pequenos de los retos puntuan ~0.5-0.6).
    - 'otra': hay detecciones de otras clases con score >= umbral resto.
    - 'incierta': sin detecciones que alcancen ningun umbral (tolerante a
      fallos del detector por celda: se re-evalua con VLM o se reintenta).
    """
    for det in detecciones:
        if (det.get("clase") == clase_objetivo
                and det.get("score", 0) >= umbral_objetivo):
            return "objetivo"
    for det in detecciones:
        if det.get("score", 0) >= umbral_resto:
            return "otra"
    return "incierta"


def decidir_celdas(detecciones_por_celda: dict, clase_objetivo: str,
                   umbral_objetivo: float = 0.45,
                   umbral_resto: float = 0.6) -> dict:
    """Decide que celdas pulsar sobre el dict {(fila, col): [detecciones]}.

    Devuelve {"seleccion": [...], "descartadas": [...], "inciertas": [...]}.
    Las celdas sin deteccion no se descartan: quedan como inciertas para que
    el llamador decida (pasada VLM, reintento o no pulsarlas).
    """
    resultado = {"seleccion": [], "descartadas": [], "inciertas": []}
    for (fila, col), detecciones in detecciones_por_celda.items():
        estado = clasificar_celda(detecciones, clase_objetivo,
                                  umbral_objetivo, umbral_resto)
        resultado[{"objetivo": "seleccion",
                   "otra": "descartadas",
                   "incierta": "inciertas"}[estado]].append((fila, col))
    return resultado


def resolver(imagen, instruccion: str, detectar_celda, n: int = 3,
             umbral_objetivo: float = 0.45, umbral_resto: float = 0.6) -> dict:
    """Pipeline completo (sin navegador): instruccion -> celdas -> deteccion
    -> decision. `detectar_celda(celda_pil, fila, col)` devuelve la lista de
    detecciones {clase, score}. El detector se inyecta: stub en --local,
    vision.modo_objetos en modo real."""
    clase = parsear_instruccion(instruccion)
    if not clase:
        return {"ok": False,
                "error": f"instruccion no parseable: {instruccion!r}"}
    detecciones = {}
    for fila, col, celda in celdas_grid(imagen, n=n):
        detecciones[(fila, col)] = detectar_celda(aumentar_escala(celda), fila, col)
    decision = decidir_celdas(detecciones, clase,
                              umbral_objetivo, umbral_resto)
    return {"ok": True, "clase_objetivo": clase, "n": n,
            **decision, "celdas_total": n * n}


def stub_detector(clases_por_celda: dict, score: float = 0.9):
    """Detector deterministico para --local: lee el ground truth de la demo."""
    def detectar(_celda, fila, col):
        return [{"clase": c, "score": score}
                for c in clases_por_celda.get((fila, col), [])]
    return detectar


def generar_demo_local(n: int = 3, semilla: int = 7, salida: str = ""):
    """Genera una cuadricula sintetica determinista (sin navegador) con
    figuras por clase COCO y su ground truth. Devuelve el dict de la demo."""
    import random
    from PIL import Image, ImageDraw

    rng = random.Random(semilla)
    clases = ["bus", "car", "traffic light", "bicycle"]
    colores = {"bus": (0, 90, 180), "car": (200, 60, 60),
               "traffic light": (60, 60, 60), "bicycle": (0, 140, 90)}
    borde, tam = 4, 120
    im = Image.new("RGB", (tam * n + borde * (n + 1),
                           tam * n + borde * (n + 1)), (245, 245, 245))
    draw = ImageDraw.Draw(im)
    clases_por_celda = {}
    for fila in range(n):
        for col in range(n):
            x0 = borde + col * (tam + borde)
            y0 = borde + fila * (tam + borde)
            draw.rectangle([x0, y0, x0 + tam, y0 + tam],
                           outline=(150, 150, 150), width=2)
            if rng.random() < 0.35:
                clase = rng.choice(clases)
                cx = x0 + 30 + rng.randint(0, 40)
                cy = y0 + 40 + rng.randint(0, 30)
                draw.rounded_rectangle([cx, cy, cx + 50, cy + 30], radius=6,
                                       fill=colores[clase])
                clases_por_celda[(fila, col)] = [clase]
    ruta = salida or f"/var/tmp/demo_captcha_{n}x{n}_s{semilla}.png"
    im.save(ruta)
    return {"ruta": ruta, "n": n, "instruccion": "select all buses",
            "clases_por_celda": clases_por_celda}


def main() -> None:
    import argparse
    from PIL import Image

    parser = argparse.ArgumentParser(
        description="Resolucion de retos reCAPTCHA v2 con el stack local")
    parser.add_argument("--local", action="store_true",
                        help="demo sintetica determinista sin navegador")
    parser.add_argument("--n", type=int, default=3, choices=(3, 4),
                        help="tamano de la cuadricula (demo local)")
    parser.add_argument("--seed", type=int, default=7,
                        help="semilla de la demo local")
    parser.add_argument("--salida", default="",
                        help="ruta PNG de salida de la demo local")
    args = parser.parse_args()

    if args.local:
        demo = generar_demo_local(n=args.n, semilla=args.seed,
                                  salida=args.salida)
        res = resolver(Image.open(demo["ruta"]), demo["instruccion"],
                       stub_detector(demo["clases_por_celda"]), n=args.n)
        if not res["ok"]:
            print("FALLO:", res["error"])
            return
        esperadas = sorted((f, c) for (f, c), clases in demo["clases_por_celda"].items()
                           if "bus" in clases)
        acierto = sorted(res["seleccion"]) == esperadas
        print(f"demo: {demo['ruta']}")
        print(f"instruccion: {demo['instruccion']!r} -> clase: {res['clase_objetivo']}")
        print(f"seleccion: {sorted(res['seleccion'])} (esperado: {esperadas})")
        print(f"descartadas: {sorted(res['descartadas'])}")
        print(f"inciertas: {sorted(res['inciertas'])}")
        print("VEREDICTO:", "OK" if acierto else "MAL")
    else:
        parser.error("modo real aun no implementado; usa --local (C5 pendiente)")


if __name__ == "__main__":
    main()
