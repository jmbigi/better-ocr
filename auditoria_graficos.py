#!/usr/bin/env python3
"""Auditoría visual y descripción de gráficos y charts.

Dos capas complementarias (patrón de revision.py):

  1. Determinista (PIL + numpy, sin modelos): detecta superposiciones de
     etiquetas, audita la leyenda y describe TODOS sus elementos (posición,
     caja, entradas con marcador de color y etiqueta de texto, título;
     ausente con series, cortada, sobre los datos, entradas sin marcador,
     conteo vs series, colores sin correspondencia), recortes/zoom excesivo,
     falta de nitidez, bajo contraste, ruido, baja resolución, y describe el
     tipo de gráfico y el número de series por colores de tinta.
  2. Visión IA opt-in (--vision docbee|ollama): descripción e interpretación
     semántica (variables, tendencias, valores) + rúbrica de problemas.

El informe JSON combina ambas capas: los hallazgos deterministas mandan
sobre la percepción del VLM cuando se contradicen (patrón de revision.py).

Uso:
  python3 auditoria_graficos.py imagen.png [--vision docbee|ollama]
       [--salida json|md|txt] [--salida-archivo ruta] [--demo]

El análisis determinista es puro y testeable sin modelos.
"""

import argparse
import json
import os
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _fuente_demo(tamano: int = 16):
    """Fuente TTF del sistema para la demo (fallback: bitmap default de PIL)."""
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", tamano)
    except OSError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Utilidades de imagen (puras, testeables)
# ---------------------------------------------------------------------------

def _cargar(imagen) -> tuple:
    """Carga una imagen (ruta o PIL.Image) y devuelve (img, gris, rgb).

    El canal gris es float32 (0-255); rgb es uint8 (H, W, 3).
    """
    if isinstance(imagen, str):
        img = Image.open(imagen).convert("RGB")
    else:
        img = imagen.convert("RGB")
    gris = np.asarray(img.convert("L"), dtype=np.float32)
    rgb = np.asarray(img, dtype=np.uint8)
    return img, gris, rgb


def _tinta(gris: np.ndarray, umbral: float = 200.0,
           invertir: bool = False) -> np.ndarray:
    """Máscara booleana de tinta: píxeles que se distinguen del fondo.

    En modo claro (default) es tinta oscura (gris < umbral); con invertir=True
    (fondo oscuro, dark-mode) es tinta clara (gris > 255 - umbral), porque
    los datos y el texto son claros sobre el fondo oscuro.
    """
    if invertir:
        return gris > 255 - umbral
    return gris < umbral


def _es_modo_oscuro(rgb: np.ndarray) -> bool:
    """True si el fondo modal de la imagen es oscuro (dark-mode).

    Usa la luminancia relativa W3C del color modal: si el fondo es oscuro
    (< 0.5), la máscara de tinta debe invertirse (los datos son claros).
    """
    return _luminancia_w3c(_color_fondo(rgb)) < 0.5


def _integral(mascara: np.ndarray) -> np.ndarray:
    """Imagen integral de la máscara: suma de tinta en cualquier ventana O(1)."""
    return np.cumsum(np.cumsum(mascara.astype(np.int64), axis=0), axis=1)


def _densidad_en(integral: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """Fracción de tinta en la ventana [x, x+w) x [y, y+h) vía integral image."""
    if w <= 0 or h <= 0:
        return 0.0
    H, W = integral.shape
    x2 = min(x + w, W)
    y2 = min(y + h, H)
    x = max(x, 0)
    y = max(y, 0)
    if x2 <= x or y2 <= y:
        return 0.0
    a = integral[y2 - 1, x2 - 1] if (y2 > 0 and x2 > 0) else 0
    b = integral[y - 1, x2 - 1] if (y > 0 and x2 > 0) else 0
    c = integral[y2 - 1, x - 1] if (y2 > 0 and x > 0) else 0
    d = integral[y - 1, x - 1] if (y > 0 and x > 0) else 0
    return (a - b - c + d) / ((x2 - x) * (y2 - y))


def _maxima_densidad(mascara: np.ndarray, tam_ventana: int) -> tuple:
    """Máxima densidad local de tinta (ventanas en cuadrícula, stride 1/2).

    Devuelve (densidad_max, (x, y, w, h)) de la ventana más cargada.
    """
    H, W = mascara.shape
    tam = max(8, min(tam_ventana, W // 2, H // 2))
    if tam < 8:
        return 0.0, (0, 0, W, H)
    integral = _integral(mascara)
    paso = max(1, tam // 2)
    mejor = 0.0
    mejor_pos = (0, 0)
    for y in range(0, max(1, H - tam + 1), paso):
        for x in range(0, max(1, W - tam + 1), paso):
            d = _densidad_en(integral, x, y, tam, tam)
            if d > mejor:
                mejor = d
                mejor_pos = (x, y)
    return mejor, (mejor_pos[0], mejor_pos[1], tam, tam)


def _componentes(mascara: np.ndarray, min_area: int = 3) -> list:
    """Componentes conectados de la tinta (RLE + union-find).

    Devuelve lista de blobs [{"x", "y", "w", "h", "area", "densidad"}].
    """
    H, W = mascara.shape
    # Corridas horizontales por fila: cada corrida es un nodo del union-find
    filas = []
    for y in range(H):
        x = 0
        fila = []
        while x < W:
            if mascara[y, x]:
                x1 = x
                while x < W and mascara[y, x]:
                    x += 1
                fila.append((x1, x - 1))
            else:
                x += 1
        filas.append(fila)
    padre = list(range(sum(len(f) for f in filas)))
    n_actual = 0
    indices_fila = []
    for y, fila in enumerate(filas):
        idx = list(range(n_actual, n_actual + len(fila)))
        n_actual += len(fila)
        indices_fila.append(idx)
        # uniones dentro de la fila: corridas contiguas (separadas por 1 px)
        for i in range(1, len(fila)):
            if fila[i][0] <= fila[i - 1][1] + 1:
                padre[idx[i]] = idx[i - 1]
        # uniones con la fila anterior
        if y > 0 and fila:
            for i, (x1, x2) in enumerate(fila):
                for j, (ax1, ax2) in enumerate(filas[y - 1]):
                    if ax1 <= x2 + 1 and ax2 >= x1 - 1:
                        padre[idx[i]] = indices_fila[y - 1][j]

    def raiz(a):
        while padre[a] != a:
            padre[a] = padre[padre[a]]
            a = padre[a]
        return a

    # agregar datos por raíz
    grupos = {}
    for y, fila in enumerate(filas):
        for i, (x1, x2) in enumerate(fila):
            r = raiz(indices_fila[y][i])
            g = grupos.get(r)
            if g is None:
                g = {"x1": x2, "y1": y, "x2": x1, "y2": y, "n": 0}
                grupos[r] = g
            g["x1"] = min(g["x1"], x1)
            g["y1"] = min(g["y1"], y)
            g["x2"] = max(g["x2"], x2)
            g["y2"] = max(g["y2"], y)
            g["n"] += x2 - x1 + 1
    blobs = []
    for g in grupos.values():
        if g["n"] < min_area:
            continue
        w = g["x2"] - g["x1"] + 1
        h = g["y2"] - g["y1"] + 1
        blobs.append({
            "x": g["x1"], "y": g["y1"], "w": w, "h": h,
            "area": g["n"],
            "densidad": g["n"] / (w * h) if w * h else 0.0,
        })
    blobs.sort(key=lambda b: -b["area"])
    return blobs


def _interseccion(a: dict, b: dict) -> int:
    """Píxeles de solapamiento entre dos bboxes."""
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if dx <= 0 or dy <= 0:
        return 0
    return dx * dy


def _colores_series(rgb: np.ndarray, mascara: np.ndarray) -> list:
    """Colores dominantes de la tinta NO gris (series del gráfico).

    Cuantiza a 32 niveles por canal y devuelve los bins frecuentes
    (>= 0.8% de la tinta de color) ordenados por frecuencia.
    """
    pix = rgb[mascara]
    if len(pix) < 50:
        return []
    r, g, b = pix[:, 0].astype(int), pix[:, 1].astype(int), pix[:, 2].astype(int)
    sat = (np.maximum(r, np.maximum(g, b)) - np.minimum(r, np.minimum(g, b)))
    color = sat > 40
    if color.sum() < 50:
        return []
    q = (pix[color] // 32).astype(int)
    claves = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    unicos, conteos = np.unique(claves, return_counts=True)
    total = len(claves)
    orden = np.argsort(-conteos)
    series = []
    for i in orden:
        if conteos[i] < 0.008 * total:
            break
        c = unicos[i]
        series.append({
            "color": "#%02x%02x%02x" % ((c // 1024) * 32, (c // 32 % 32) * 32, (c % 32) * 32),
            "fraccion": round(float(conteos[i]) / total, 3),
        })
    return series[:6]


def _borrosidad(gris: np.ndarray) -> float:
    """Varianza del Laplaciano (nitidez): valores altos = nítida."""
    v = gris[1:-1, 1:-1]
    lap = (gris[:-2, 1:-1] + gris[2:, 1:-1] + gris[1:-1, :-2]
           + gris[1:-1, 2:] - 4 * v)
    return float(lap.var())


def _contraste(gris: np.ndarray, mascara: np.ndarray) -> float:
    """Diferencia media de luminancia fondo - tinta."""
    if mascara.sum() == 0:
        return 0.0
    fondo = gris[~mascara]
    tinta = gris[mascara]
    return float(fondo.mean() - tinta.mean()) if len(fondo) else 0.0


def _ruido(mascara: np.ndarray) -> float:
    """Fracción de píxeles de tinta aislados (vecindario 3x3 sin tinta)."""
    m = mascara.astype(np.uint8)
    vec = (np.convolve(m.ravel(), np.ones(9, dtype=np.uint8), mode="same")
           .reshape(m.shape))
    if m.sum() == 0:
        return 0.0
    return float((m * (vec == 1)).sum() / m.sum())


# ---------------------------------------------------------------------------
# Checks deterministas
# ---------------------------------------------------------------------------

def _mascara_solidos(mascara: np.ndarray, tam: int = 8) -> np.ndarray:
    """Píxeles dentro de zonas casi completamente rellenas (barras, áreas).

    Se computa por ventana local tam×tam: el interior de un rectángulo
    relleno da densidad 1.0; un eje (línea fina) da ~37% y queda fuera; los
    glifos de texto (~50%) también. Un blob mixto (eje + barra conectados)
    no engaña a esta máscara: el interior de la barra sigue siendo sólido.
    """
    H, W = mascara.shape
    solido = np.zeros((H, W), dtype=bool)
    if H <= tam or W <= tam:
        return solido
    integral = _integral(mascara)
    i_max, j_max = H - tam, W - tam
    filas = np.arange(i_max)[:, None]
    cols = np.arange(j_max)[None, :]
    # ventana [i, i+tam) x [j, j+tam): I(i+tam-1, j+tam-1) - I(i-1, j+tam-1)
    #   - I(i+tam-1, j-1) + I(i-1, j-1); el borde (i-1 < 0) resta 0
    a = integral[filas + tam - 1, cols + tam - 1]
    b = integral[np.maximum(filas - 1, 0), cols + tam - 1]
    c = integral[filas + tam - 1, np.maximum(cols - 1, 0)]
    d = integral[np.maximum(filas - 1, 0), np.maximum(cols - 1, 0)]
    dens = (a - b - c + d) / float(tam * tam)
    solido[:i_max, :j_max] = dens >= 0.85
    return solido


def _mascara_texto(mascara: np.ndarray) -> np.ndarray:
    """Tinta que NO pertenece a bloques sólidos extensos (barras, áreas).

    La densidad local de tinta solo indica superposición de ETIQUETAS sobre
    tinta tipo texto/glifo; los sólidos dan densidad 100% sin ser
    superposición (proportional ink: un rectángulo relleno es el dato).
    Los glifos engrosados también son "sólidos locales" pero pequeños: el
    filtro exige componentes sólidos de tamaño significativo (>= 0.1% del
    lienzo), como una barra o un área rellena. Los marcadores de leyenda
    (swatches ~10-20 px, densidad ~1.0 y proporciones cuadradas) tampoco
    son texto: se excluyen por su forma aunque no lleguen a ese tamaño.
    """
    solido = _mascara_solidos(mascara)
    excluir = np.zeros_like(mascara)
    area_img = mascara.shape[0] * mascara.shape[1]
    for b in _componentes(solido):
        if b["area"] >= 0.001 * area_img:
            excluir[b["y"]:b["y"] + b["h"], b["x"]:b["x"] + b["w"]] = True
            continue
        # swatch de leyenda: sólido pequeño con proporciones casi cuadradas
        if b["h"] > 0 and 0.8 <= b["w"] / b["h"] <= 1.25:
            excluir[b["y"]:b["y"] + b["h"], b["x"]:b["x"] + b["w"]] = True
    return mascara & ~excluir


def check_superposiciones(gris, mascara, blobs, ancho, alto) -> list:
    """Etiquetas superpuestas (densidad local de tinta tipo texto) y
    colisiones entre bboxes."""
    hallazgos = []
    texto = _mascara_texto(mascara)
    # la ventana de densidad se ajusta al tamaño del texto real (altura
    # mediana de los blobs de texto): un glifo normal da ~50% de densidad,
    # glifos engrosados/solapados superan 0.65
    blobs_txt = _blobs_texto(blobs, ancho, alto)
    if blobs_txt:
        alturas = np.array([b["h"] for b in blobs_txt])
        tam_ventana = max(8, int(np.median(alturas)))
    else:
        tam_ventana = max(40, min(ancho, alto) // 10)
    densidad, (x, y, w, h) = _maxima_densidad(texto, tam_ventana)
    # umbral: un glifo ocupa ~40-50% de su caja; solapamiento real -> > 60%
    if densidad > 0.6:
        hallazgos.append({
            "tipo": "superposicion", "severidad": "problema",
            "mensaje": (f"posible superposicion de etiquetas: densidad local "
                        f"de tinta {densidad:.0%} en ({x},{y}) {w}x{h}"),
            "evidencia": {"densidad_max": round(densidad, 3),
                          "ventana": [x, y, w, h]},
        })
    # colisiones de bboxes con solapamiento significativo
    colisiones = []
    for i in range(len(blobs)):
        for j in range(i + 1, len(blobs)):
            a, b = blobs[i], blobs[j]
            if a["area"] < 10 or b["area"] < 10:
                continue
            inter = _interseccion(a, b)
            menor = min(a["area"], b["area"])
            if inter > 0.35 * menor:
                colisiones.append((a, b, inter))
                break  # un blob puede estar en varias colisiones; no duplicar
        if len(colisiones) >= 3:
            break
    if colisiones:
        hallazgos.append({
            "tipo": "superposicion", "severidad": "aviso",
            "mensaje": f"{len(colisiones)} par(es) de elementos con bboxes solapados",
            "evidencia": {"colisiones": [
                [a["x"], a["y"], a["w"], a["h"], b["x"], b["y"], b["w"], b["h"]]
                for a, b, _ in colisiones]},
        })
    return hallazgos


def _blobs_texto(blobs, ancho, alto) -> list:
    """Blobs con aspecto de texto (área moderada, proporciones plausibles).

    El texto de los ejes/leyendas suele ocupar entre 0.03% y 3% del lienzo;
    por debajo del mínimo son ruido o glifos ilegibles, por encima son
    bloques (barras, cajas). Se excluyen los casi sólidos (densidad >= 0.85):
    son rectángulos rellenos (marcadores de color, puntos gruesos), no
    glifos — un glifo bitmap real no pasa del ~60%.
    """
    area_img = ancho * alto
    texto = []
    for b in blobs:
        if b.get("densidad", 0) >= 0.85:
            continue
        if not (0.00006 * area_img <= b["area"] <= 0.03 * area_img):
            continue
        if b["h"] <= 0 or b["w"] <= 0:
            continue
        ratio = b["w"] / b["h"]
        if 0.15 <= ratio <= 6.0:
            texto.append(b)
    return texto


def _grupos_texto(texto: list) -> list:
    """Agrupa blobs de texto por proximidad (gap <= 2x la altura del blob
    entrante, NO del grupo: el grupo crece y su expansión "puentea").

    Se excluyen los blobs de densidad muy baja (recuadros/outlines ~4% y
    ejes ~13%): no son etiquetas y, por ser alargados y altos, "puentean"
    distancias y fusionarían grupos separados en uno gigante.

    Además de la proximidad genérica, se agrupan textos APILADOS con solape
    horizontal (misma columna) y gap vertical <= 4x la altura del menor:
    es el espaciado típico de las entradas de una leyenda (~1.5x línea).
    """
    candidatos = [t for t in texto if t.get("densidad", 0) >= 0.15]
    grupos = []
    for t in candidatos:
        mejor = None
        for g in grupos:
            if _interseccion({"x": g["x"] - 4 * t["h"], "y": g["y"] - 2 * t["h"],
                              "w": g["w"] + 8 * t["h"], "h": g["h"] + 4 * t["h"]},
                             t) > 0:
                mejor = g
                break
            # apilados en la misma columna con separación de línea de leyenda
            if (t["x"] < g["x"] + g["w"] and g["x"] < t["x"] + t["w"]
                    and abs(t["y"] - (g["y"] + g["h"])) <= 4 * min(g["h"], t["h"])):
                mejor = g
                break
        if mejor is None:
            grupos.append(dict(t))
        else:
            x2 = max(mejor["x"] + mejor["w"], t["x"] + t["w"])
            y2 = max(mejor["y"] + mejor["h"], t["y"] + t["h"])
            mejor["x"] = min(mejor["x"], t["x"])
            mejor["y"] = min(mejor["y"], t["y"])
            mejor["w"] = x2 - mejor["x"]
            mejor["h"] = y2 - mejor["y"]
            mejor["area"] += t["area"]
    return grupos


def _candidatas_leyenda(grupos: list, ancho: int, alto: int) -> list:
    """Grupos de texto en el perímetro EXTERIOR (margen 12%) con área de una
    entrada mínima. El margen es más estricto que el genérico (20%): las
    etiquetas de valores cerca de los bordes (p. ej. una etiqueta encima de
    la última barra) no son leyenda, pero una leyenda sí está pegada al
    borde de la imagen."""
    margen_x = 0.12 * ancho
    margen_y = 0.12 * alto
    candidatas = []
    for g in grupos:
        if g["area"] < 0.00015 * ancho * alto:
            continue
        perimetral = (g["x"] <= margen_x or g["x"] + g["w"] >= ancho - margen_x
                      or g["y"] <= margen_y or g["y"] + g["h"] >= alto - margen_y)
        if perimetral:
            candidatas.append(g)
    return candidatas


def _posicion_leyenda(caja: dict, ancho: int, alto: int) -> str:
    """Posición de la caja de la leyenda: derecha/izquierda/arriba/abajo/interior."""
    cx = caja["x"] + caja["w"] / 2
    cy = caja["y"] + caja["h"] / 2
    if cx >= 0.6 * ancho:
        return "derecha"
    if cx <= 0.4 * ancho:
        return "izquierda"
    if cy >= 0.6 * alto:
        return "abajo"
    if cy <= 0.4 * alto:
        return "arriba"
    return "interior"


def _color_dominante(rgb: np.ndarray, x: int, y: int, w: int, h: int) -> str:
    """Hex cuantizado (bins de 32, como _colores_series) del color más frecuente."""
    r = rgb[y:y + h, x:x + w]
    if r.size == 0:
        return "#000000"
    q = (r.reshape(-1, 3) // 32).astype(int)
    claves = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    k = int(np.bincount(claves, minlength=32768).argmax())
    return "#%02x%02x%02x" % ((k // 1024) * 32, (k // 32 % 32) * 32, (k % 32) * 32)


def _swatches_en(rgb: np.ndarray, region: dict, ancho: int, alto: int) -> list:
    """Marcadores de color (swatches) dentro de una región expandida.

    Un marcador típico de leyenda es un rectángulo pequeño de color saturado
    (10-40 px de lado). Se detectan como componentes de la máscara de
    saturación con área moderada y proporciones de marcador; el texto de la
    etiqueta es gris/negro (no saturado) y queda fuera. La región se expande
    porque el marcador suele quedar a la IZQUIERDA del texto, fuera de la caja
    de tinta del grupo de etiquetas.
    """
    H, W = rgb.shape[:2]
    x0 = max(0, region["x"]); y0 = max(0, region["y"])
    x1 = min(W, region["x"] + region["w"]); y1 = min(H, region["y"] + region["h"])
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    sub = rgb[y0:y1, x0:x1].astype(int)
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    sat = (np.maximum(r, np.maximum(g, b)) - np.minimum(r, np.minimum(g, b))) > 60
    area_img = ancho * alto
    swatches = []
    for blob in _componentes(sat):
        if not (0.00015 * area_img <= blob["area"] <= 0.02 * area_img):
            continue
        if blob["h"] <= 0 or blob["w"] / blob["h"] > 6:
            continue
        if blob["w"] > 0.7 * (x1 - x0) or blob["h"] > 0.7 * (y1 - y0):
            continue
        sx = blob["x"] + x0
        sy = blob["y"] + y0
        swatches.append({
            "x": sx, "y": sy, "w": blob["w"], "h": blob["h"],
            "area": blob["area"],
            "color": _color_dominante(rgb, sx, sy, blob["w"], blob["h"]),
        })
    return swatches


def _fusionar_lineas(blobs: list) -> list:
    """Une glifos del mismo renglón en líneas de texto.

    El anti-aliasing de las fuentes reales deja píxeles grises entre letras
    que no pasan el umbral de tinta: cada glifo es un blob separado. Se
    fusionan los blobs con centro-y coincidente y gap horizontal pequeño,
    iterando hasta convergencia (el orden de los glifos puede dejar
    huecos intermedios que se cierran al crecer la línea).
    """
    lineas = [dict(b) for b in blobs]
    cambio = True
    while cambio:
        cambio = False
        lineas.sort(key=lambda l: (l["y"], l["x"]))
        i = 0
        while i < len(lineas):
            a = lineas[i]
            j = i + 1
            while j < len(lineas):
                b = lineas[j]
                dy = abs((a["y"] + a["h"] / 2) - (b["y"] + b["h"] / 2))
                if dy <= max(a["h"], b["h"]):
                    gap = b["x"] - (a["x"] + a["w"])
                    if gap <= 2 * max(a["h"], b["h"]):
                        x2 = max(a["x"] + a["w"], b["x"] + b["w"])
                        y2 = max(a["y"] + a["h"], b["y"] + b["h"])
                        a["x"] = min(a["x"], b["x"])
                        a["y"] = min(a["y"], b["y"])
                        a["w"] = x2 - a["x"]
                        a["h"] = y2 - a["y"]
                        a["area"] += b["area"]
                        lineas.pop(j)
                        cambio = True
                        continue
                j += 1
            i += 1
    return lineas


def _entradas_leyenda(texto: list, caja: dict, swatches: list) -> tuple:
    """Empareja marcadores de color con sus etiquetas de texto dentro de la caja.

    Devuelve (entradas, titulo). Cada entrada es
    {"color", "marcador" (bbox o None), "texto" (bbox)}. Los blobs de texto
    sin marcador son entradas sin color, salvo el más alto y centrado que
    se considera el título de la leyenda (si hay entradas con marcador).
    """
    # el recuadro que rodea la leyenda (outline) es un blob de densidad muy
    # baja (~4%): los glifos reales superan el 15%; así no cuenta como entrada
    dentro = [t for t in texto if _centro_en(t, caja) and t.get("densidad", 0) >= 0.15]
    # el anti-aliasing fragmenta el texto en glifos: se fusionan por renglón
    dentro = _fusionar_lineas(dentro)
    entradas = []
    usados = set()
    for s in swatches:
        mejor = None
        for i, t in enumerate(dentro):
            if i in usados:
                continue
            sy = s["y"] + s["h"] / 2
            ty = t["y"] + t["h"] / 2
            if abs(ty - sy) > max(t["h"], s["h"]) * 1.5:
                continue
            if t["x"] < s["x"] + s["w"]:
                continue
            if mejor is None or t["x"] < mejor[0]["x"]:
                mejor = (t, i)
        if mejor is None:
            continue
        t, i = mejor
        usados.add(i)
        entradas.append({
            "color": s["color"],
            "marcador": [s["x"], s["y"], s["w"], s["h"]],
            "texto": [t["x"], t["y"], t["w"], t["h"]],
        })
    sin_marcador = [t for i, t in enumerate(dentro) if i not in usados]
    titulo = None
    if entradas and sin_marcador:
        caja_cx = caja["x"] + caja["w"] / 2
        primer_entrada_y = min(e["texto"][1] for e in entradas)
        # el título es la línea (o líneas) por encima de la primera entrada,
        # centrada respecto a la caja; se une si quedó fragmentada en blobs
        arriba = [t for t in sin_marcador
                  if t["y"] + t["h"] <= primer_entrada_y
                  and abs(t["x"] + t["w"] / 2 - caja_cx) <= 0.3 * caja["w"]]
        if arriba:
            x0 = min(t["x"] for t in arriba)
            y0 = min(t["y"] for t in arriba)
            x1 = max(t["x"] + t["w"] for t in arriba)
            y1 = max(t["y"] + t["h"] for t in arriba)
            titulo = [x0, y0, x1 - x0, y1 - y0]
            sin_marcador = [t for t in sin_marcador if t not in arriba]
    for t in sin_marcador:
        entradas.append({"color": None, "marcador": None,
                         "texto": [t["x"], t["y"], t["w"], t["h"]]})
    entradas.sort(key=lambda e: e["texto"][1])
    return entradas, titulo


def _centro_en(t: dict, caja: dict) -> bool:
    cx = t["x"] + t["w"] / 2
    cy = t["y"] + t["h"] / 2
    return (caja["x"] <= cx <= caja["x"] + caja["w"]
            and caja["y"] <= cy <= caja["y"] + caja["h"])


def _seleccionar_leyenda(candidatas: list, texto: list, rgb: np.ndarray,
                         ancho: int, alto: int) -> tuple | None:
    """Elige la candidata más probable a ser leyenda (caja, swatches, n_blobs).

    Reglas (heurísticas, se reportan como estimación):
    - se descartan cajas que cubren > 30% del lienzo (grupo gigante: el eje
      con sus etiquetas no es una leyenda) o con proporciones de panel
      (w > 30% del ancho o h > 30% del alto: una fila de barras del grid no
      es una entrada de leyenda);
    - se priorizan candidatas con marcadores de color (más marcadores y
      caja más compacta);
    - sin marcadores, la más compacta con >= 2 blobs de texto (una leyenda
      de texto plano; un título de grid es un solo blob y no llega).
    """
    area_lienzo = ancho * alto
    evaluadas = []
    for caja in candidatas:
        if caja["w"] * caja["h"] > 0.3 * area_lienzo:
            continue
        if caja["w"] > 0.3 * ancho or caja["h"] > 0.3 * alto:
            continue
        alturas = [t["h"] for t in texto if _centro_en(t, caja)]
        pad = max(8, int(1.5 * (np.median(alturas) if alturas else caja["h"])))
        region = {"x": caja["x"] - pad, "y": caja["y"] - pad,
                  "w": caja["w"] + 2 * pad, "h": caja["h"] + 2 * pad}
        swatches = _swatches_en(rgb, region, ancho, alto)
        n_blobs = sum(1 for t in texto if _centro_en(t, caja))
        evaluadas.append((caja, swatches, n_blobs))
    if not evaluadas:
        return None
    con_marcador = [e for e in evaluadas if e[1]]
    if con_marcador:
        return max(con_marcador,
                   key=lambda e: (len(e[1]), -e[0]["w"] * e[0]["h"]))
    planas = [e for e in evaluadas if e[2] >= 2]
    if planas:
        return min(planas, key=lambda e: e[0]["w"] * e[0]["h"])
    return None


def describir_leyenda(rgb, blobs, ancho, alto) -> dict | None:
    """Descripción de la leyenda y sus elementos (si existe).

    Devuelve None si no hay leyenda detectable en el perímetro; si hay,
    dict con posición, caja, entradas (marcador + color + texto) y posible
    título. La descripción alimenta los checks de auditoría (check_leyenda).
    """
    texto = _blobs_texto(blobs, ancho, alto)
    candidatas = _candidatas_leyenda(_grupos_texto(texto), ancho, alto)
    if not candidatas:
        return None
    sel = _seleccionar_leyenda(candidatas, texto, rgb, ancho, alto)
    if sel is None:
        return None
    caja, swatches, _ = sel
    entradas, titulo = _entradas_leyenda(texto, caja, swatches)
    return {
        "posicion": _posicion_leyenda(caja, ancho, alto),
        "caja": [caja["x"], caja["y"], caja["w"], caja["h"]],
        "n_entradas": len(entradas),
        "titulo": titulo,
        "entradas": entradas,
    }


def check_leyenda(rgb, mascara, blobs, ancho, alto, series) -> list:
    """Audita la leyenda y todos sus elementos: ausente con series, cortada
    por el borde, sobre los datos, entradas sin marcador de color, número de
    entradas vs series detectadas y colores sin correspondencia."""
    hallazgos = []
    desc = describir_leyenda(rgb, blobs, ancho, alto)
    if desc is None:
        # sin leyenda visible en el perímetro: avisar si hay varias series
        if len(series) >= 2:
            hallazgos.append({
                "tipo": "leyenda", "severidad": "aviso",
                "mensaje": (f"{len(series)} series de colores sin leyenda visible "
                            f"en el perimetro"),
                "evidencia": {"series": len(series)},
            })
        return hallazgos
    caja = {"x": desc["caja"][0], "y": desc["caja"][1],
            "w": desc["caja"][2], "h": desc["caja"][3]}
    # leyenda cortada por el borde de la imagen (recorte)
    pegado = (caja["x"] <= 1 or caja["y"] <= 1
              or caja["x"] + caja["w"] >= ancho - 1
              or caja["y"] + caja["h"] >= alto - 1)
    if pegado:
        hallazgos.append({
            "tipo": "leyenda", "severidad": "aviso",
            "mensaje": "leyenda pegada/cortada por el borde de la imagen",
            "evidencia": {"caja": desc["caja"]},
        })
    # leyenda sobre los datos: densidad alta debajo de su caja
    densidad = _densidad_en(_integral(mascara), caja["x"], caja["y"],
                            caja["w"], caja["h"])
    if densidad > 0.35:
        hallazgos.append({
            "tipo": "leyenda", "severidad": "problema",
            "mensaje": ("leyenda superpuesta a la zona de datos "
                        f"(densidad de tinta {densidad:.0%} bajo la caja)"),
            "evidencia": {"densidad": round(densidad, 3),
                          "caja": desc["caja"]},
        })
    # entradas sin marcador de color: no se pueden asociar a ninguna serie
    sin_marcador = [e for e in desc["entradas"] if e["marcador"] is None]
    if sin_marcador:
        hallazgos.append({
            "tipo": "leyenda_marcador", "severidad": "aviso",
            "mensaje": (f"{len(sin_marcador)} entrada(s) de leyenda sin marcador "
                        f"de color (no se pueden asociar a una serie)"),
            "evidencia": {"entradas": [e["texto"] for e in sin_marcador]},
        })
    # conteo de entradas vs series detectadas
    n_series = len(series)
    n_entradas = desc["n_entradas"]
    if n_series >= 2 and n_entradas and n_entradas < n_series:
        hallazgos.append({
            "tipo": "leyenda_entradas", "severidad": "aviso",
            "mensaje": (f"leyenda con {n_entradas} entrada(s) para "
                        f"{n_series} series detectadas: faltan etiquetas"),
            "evidencia": {"n_entradas": n_entradas, "n_series": n_series},
        })
    elif n_series >= 2 and n_entradas > n_series:
        hallazgos.append({
            "tipo": "leyenda_entradas", "severidad": "aviso",
            "mensaje": (f"leyenda con {n_entradas} entrada(s) para "
                        f"{n_series} series detectadas: posibles series por "
                        f"debajo del umbral de tinta"),
            "evidencia": {"n_entradas": n_entradas, "n_series": n_series},
        })
    # colores de la leyenda sin correspondencia en las series detectadas
    colores_series = {s["color"] for s in series}
    fantasma = sorted({e["color"] for e in desc["entradas"]
                       if e["color"] and e["color"] not in colores_series})
    if fantasma:
        hallazgos.append({
            "tipo": "leyenda_color", "severidad": "aviso",
            "mensaje": (f"colores de la leyenda sin serie correspondiente en el "
                        f"grafico ({len(fantasma)}): " + ", ".join(fantasma)),
            "evidencia": {"colores": fantasma,
                          "series": sorted(colores_series)},
        })
    return hallazgos


def check_zoom_cortes(gris, mascara, ancho, alto) -> list:
    """Tinta en los bordes (recorte/zoom excesivo) y margen excesivo."""
    hallazgos = []
    if mascara.sum() == 0:
        return hallazgos  # imagen vacía: no es un recorte ni un zoom
    franja = max(4, min(ancho, alto) // 20)
    H, W = mascara.shape
    borde = np.zeros_like(mascara)
    borde[:franja, :] = True
    borde[-franja:, :] = True
    borde[:, :franja] = True
    borde[:, -franja:] = True
    tinta_borde = float(mascara[borde].sum())
    total_borde = float(borde.sum())
    if total_borde and tinta_borde / total_borde > 0.05:
        hallazgos.append({
            "tipo": "zoom", "severidad": "aviso",
            "mensaje": (f"tinta en los bordes de la imagen "
                        f"({tinta_borde / total_borde:.0%} de la franja): "
                        f"posible recorte o zoom excesivo"),
            "evidencia": {"fraccion_borde": round(tinta_borde / total_borde, 3)},
        })
    cobertura = float(mascara.mean())
    if cobertura < 0.005:
        hallazgos.append({
            "tipo": "zoom", "severidad": "aviso",
            "mensaje": (f"contenido muy pequeno en el lienzo "
                        f"(cobertura de tinta {cobertura:.2%}): margen o zoom "
                        f"insuficiente"),
            "evidencia": {"cobertura": round(cobertura, 5)},
        })
    return hallazgos


def check_nitidez(gris, mascara) -> list:
    """Imagen borrosa (posible upscale/zoom con suavizado).

    Sin tinta no hay nada que pueda estar borroso (una imagen vacía no es
    un gráfico borroso).
    """
    if mascara.sum() == 0:
        return []
    var = _borrosidad(gris)
    if var < 60:
        return [{
            "tipo": "nitidez", "severidad": "problema",
            "mensaje": f"imagen borrosa (varianza de Laplaciano {var:.1f} < 60)",
            "evidencia": {"varianza_lap": round(var, 1)},
        }]
    if var < 120:
        return [{
            "tipo": "nitidez", "severidad": "aviso",
            "mensaje": f"nitidez baja (varianza de Laplaciano {var:.1f})",
            "evidencia": {"varianza_lap": round(var, 1)},
        }]
    return []


def check_contraste(gris, mascara, invertir: bool = False) -> list:
    """Contraste insuficiente entre tinta y fondo.

    La máscara laxa captura también tintas pálidas: en modo claro es
    gris < 250 (un elemento 245/255 sobre fondo blanco es poco contrastado
    aunque no sea "tinta oscura"); en dark-mode se define respecto al FONDO
    MODAL del gris (gris > fondo + 25) para no capturar el propio fondo,
    que dejaría vacía la clase "fondo" y daría contraste 0.0 falso. La
    diferencia se mide en valor absoluto porque en dark-mode la tinta es
    MÁS clara que el fondo (la resta sale negativa).
    """
    if invertir:
        fondo_gris = float(np.bincount(
            np.clip(gris, 0, 255).astype(np.uint8).ravel()).argmax())
        # simétrico del claro (fondo - 5): captura tinta pálida justo sobre
        # el fondo sin tomar el propio fondo (daría contraste 0.0 falso)
        laxa = gris > fondo_gris + 8
    else:
        laxa = gris < 250
    if laxa.sum() == 0:
        return []
    c = abs(_contraste(gris, laxa))
    if c < 50:
        return [{
            "tipo": "contraste", "severidad": "problema",
            "mensaje": f"bajo contraste tinta/fondo ({c:.0f} de 255)",
            "evidencia": {"contraste": round(c, 1)},
        }]
    if c < 90:
        return [{
            "tipo": "contraste", "severidad": "aviso",
            "mensaje": f"contraste reducido tinta/fondo ({c:.0f} de 255)",
            "evidencia": {"contraste": round(c, 1)},
        }]
    return []


def check_texto_pequeno(blobs, ancho, alto) -> list:
    """Texto minúsculo (ilegible) y elementos diminutos."""
    hallazgos = []
    area_img = ancho * alto
    pequenos = [b for b in blobs if 0.0001 * area_img <= b["area"] < 0.0008 * area_img]
    if len(pequenos) >= 8:
        hallazgos.append({
            "tipo": "texto", "severidad": "aviso",
            "mensaje": (f"{len(pequenos)} elementos de texto muy pequenos "
                        f"(posible fuente ilegible)"),
            "evidencia": {"n": len(pequenos)},
        })
    return hallazgos


# ---------------------------------------------------------------------------
# Checks basados en las fuentes de docs/INVESTIGACION-VISUALIZACION.md:
# WCAG 1.4.3/1.4.11 (contraste), daltonismo (ColorBrewer/Wilke),
# pie <= 5 slices (Cleveland & McGill, data-to-viz) y max 4-5 series (SWD)
# ---------------------------------------------------------------------------

def _luminancia_w3c(rgb) -> float:
    """Luminancia relativa W3C (SC 1.4.3): canales sRGB linealizados."""
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def _ratio_wcag(l1: float, l2: float) -> float:
    """Ratio de contraste WCAG entre dos luminancias (1-21)."""
    a, b = sorted([l1, l2], reverse=True)
    return (a + 0.05) / (b + 0.05)


def _color_fondo(rgb: np.ndarray) -> tuple:
    """Color modal de la imagen (el fondo, típicamente blanco)."""
    q = (rgb // 32).astype(int)
    claves = q[:, :, 0] * 1024 + q[:, :, 1] * 32 + q[:, :, 2]
    k = int(np.bincount(claves.ravel(), minlength=32768).argmax())
    return (k // 1024) * 32, (k // 32 % 32) * 32, (k % 32) * 32


def _hex_a_rgb(color: str) -> tuple:
    h = color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def check_contraste_wcag(rgb, series) -> list:
    """Contraste WCAG de las series contra el fondo.

    Objetos gráficos (líneas, barras, slices) >= 3:1 (SC 1.4.11); el texto
    exige 4.5:1 (SC 1.4.3). Fórmula W3C de luminancia relativa (gamma).
    """
    hallazgos = []
    if not series:
        return hallazgos
    fondo = _color_fondo(rgb)
    l_fondo = _luminancia_w3c(fondo)
    bajos = []
    for s in series:
        ratio = _ratio_wcag(l_fondo, _luminancia_w3c(_hex_a_rgb(s["color"])))
        if ratio < 3.0:
            bajos.append({"color": s["color"], "ratio": round(ratio, 2)})
    if bajos:
        hallazgos.append({
            "tipo": "contraste_wcag", "severidad": "aviso",
            "mensaje": (f"{len(bajos)} serie(s) con contraste < 3:1 contra el "
                        f"fondo (WCAG SC 1.4.11): "
                        + ", ".join(f"{b['color']} {b['ratio']}:1" for b in bajos)),
            "evidencia": {"series": bajos,
                          "fondo": "#%02x%02x%02x" % fondo},
        })
    return hallazgos


# Matrices de simulación de daltonismo (Machado et al. 2009,
# "A Physiologically-based Model for Simulation of Color Vision Deficiency").
_PROTANOPIA = np.array([[0.152286, 1.052583, -0.204868],
                        [0.114503, 0.786281, 0.099216],
                        [-0.003882, -0.048116, 1.051998]])
_DEUTERANOPIA = np.array([[0.367322, 0.860646, -0.227968],
                          [0.280085, 0.672501, 0.047413],
                          [-0.011820, 0.042940, 0.968881]])


def _simular_daltonismo(color: tuple, matriz: np.ndarray) -> tuple:
    return tuple(np.clip(np.dot(matriz, np.asarray(color, dtype=float)),
                         0, 255).astype(int))


def check_daltonismo(series) -> list:
    """Pares de series indistinguibles bajo protanopía/deuteranopía.

    No distinguir por hue solo es el error de color más documentado (Wilke,
    color-pitfalls; ColorBrewer). La simulación aplica las matrices de
    Machado y compara las distancias RGB de los pares simulados.
    """
    hallazgos = []
    if len(series) < 2:
        return hallazgos
    cols = [_hex_a_rgb(s["color"]) for s in series]
    confusos = []
    for nombre, matriz in (("protanopia", _PROTANOPIA),
                           ("deuteranopia", _DEUTERANOPIA)):
        sim = [_simular_daltonismo(c, matriz) for c in cols]
        for i in range(len(sim)):
            for j in range(i + 1, len(sim)):
                d = float(np.linalg.norm(np.asarray(sim[i], dtype=float)
                                         - np.asarray(sim[j], dtype=float)))
                if d < 35.0:
                    confusos.append((series[i]["color"], series[j]["color"],
                                     nombre, round(d)))
    if confusos:
        hallazgos.append({
            "tipo": "daltonismo", "severidad": "aviso",
            "mensaje": ("pares de series indistinguibles para daltonismo: "
                        + "; ".join(f"{a}/{b} ({tipo}, d={d})"
                                    for a, b, tipo, d in confusos)),
            "evidencia": {"pares": [list(p) for p in confusos]},
        })
    return hallazgos


def check_pie_slices(series, tipo: str) -> list:
    """Pie con demasiados slices: comparar ángulos es impreciso (Cleveland
    & McGill 1984); data-to-viz recomienda <= 5 categorías."""
    if tipo != "pastel" or len(series) < 6:
        return []
    return [{
        "tipo": "pie_slices", "severidad": "aviso",
        "mensaje": (f"pastel con {len(series)} slices: comparar angulos con "
                    f"mas de ~5 categorias es impreciso (considerar barras)"),
        "evidencia": {"slices": len(series)},
    }]


def check_series_limit(series) -> list:
    """Más de 4-5 series: el lector no puede seguir más (spaghetti rule,
    SWD; data-to-viz)."""
    if len(series) < 6:
        return []
    return [{
        "tipo": "series", "severidad": "aviso",
        "mensaje": (f"{len(series)} series de color: mantener 4-5 maximo o "
                    f"dividir en small multiples"),
        "evidencia": {"n_series": len(series)},
    }]


def check_ruido(gris, mascara) -> list:
    """Ruido/artefactos: píxeles de tinta aislados."""
    r = _ruido(mascara)
    if r > 0.01:
        return [{
            "tipo": "ruido", "severidad": "aviso",
            "mensaje": f"posible ruido o artefactos ({(r * 100):.2f}% de la tinta aislada)",
            "evidencia": {"fraccion_aislada": round(r, 4)},
        }]
    return []


def check_resolucion(ancho) -> list:
    """Resolución baja (heurístico: ancho de la imagen)."""
    if ancho < 400:
        return [{
            "tipo": "resolucion", "severidad": "problema",
            "mensaje": f"resolucion muy baja (ancho {ancho} px)",
            "evidencia": {"ancho": ancho},
        }]
    if ancho < 640:
        return [{
            "tipo": "resolucion", "severidad": "aviso",
            "mensaje": f"resolucion baja (ancho {ancho} px)",
            "evidencia": {"ancho": ancho},
        }]
    return []


# ---------------------------------------------------------------------------
# Descripción determinista: tipo de gráfico y series
# ---------------------------------------------------------------------------

def _es_rectangular(b: dict, tolerancia: float = 0.85) -> bool:
    return b["w"] * b["h"] > 0 and b["densidad"] >= tolerancia


def _blobs_grandes(blobs, ancho, alto, n_max=12) -> list:
    """Blobs significativos (no microtexto) para clasificar el gráfico."""
    area_img = ancho * alto
    return [b for b in blobs if b["area"] >= 0.001 * area_img][:n_max]


def clasificar_tipo(blobs, ancho, alto, series) -> dict:
    """Clasificación heurística del tipo de gráfico.

    Devuelve {"tipo": ..., "confianza": ..., "claves": {...}}.
    Se reporta 'indeterminado' sin confianza en lugar de inventar.
    Señales usadas: (a) pastel/donut: un blob grande casi circular (w≈h),
    sólido (pastel, densidad alta) o anillo con hueco (donut, densidad
    baja); (b) barras: >= 3 rectángulos alineados sobre una base común —
    base inferior (verticales) o base lateral izquierda/derecha
    (horizontales); (c) línea: tinta continua y delgada a lo ancho;
    (d) scatter: muchos puntos pequeños dispersos sin blobs grandes.
    """
    area_img = ancho * alto
    grandes = _blobs_grandes(blobs, ancho, alto)
    # scatter: muchos puntos pequeños aislados y dispersos (vale aunque no
    # haya blobs grandes: un scatter puro no los tiene)
    puntos = [b for b in blobs
              if b["area"] < 0.0008 * area_img and b["area"] >= 8]
    if len(puntos) >= 15 and not grandes:
        return {"tipo": "scatter", "confianza": 0.6,
                "claves": {"puntos": len(puntos)}}
    if not grandes:
        return {"tipo": "indeterminado", "confianza": 0.0, "claves": {}}
    rects = [b for b in grandes if _es_rectangular(b)]
    # pastel/donut: un blob grande casi circular; la densidad distingue el
    # pastel sólido (~0.78, pi/4) del donut con hueco central (mucho menor)
    for b in grandes:
        if b["w"] >= 0.25 * ancho and b["h"] >= 0.25 * alto:
            if abs(b["w"] - b["h"]) <= 0.2 * max(b["w"], b["h"]):
                donut = b["densidad"] < 0.7
                return {"tipo": "pastel", "confianza": 0.7,
                        "claves": {"blob": [b["x"], b["y"], b["w"], b["h"]],
                                   "donut": donut,
                                   "densidad": round(b["densidad"], 3)}}
    # barras: >= 3 rectángulos alineados sobre una base común
    if len(rects) >= 3:
        # base INFERIOR alineada (barras verticales: y+h constante)
        bases = sorted(b["y"] + b["h"] for b in rects)
        mediana = bases[len(bases) // 2]
        alineadas = sum(1 for b in bases if abs(b - mediana) <= 0.12 * alto)
        verticales = sum(1 for b in rects if b["h"] > b["w"])
        if alineadas >= 3 and verticales >= 2:
            return {"tipo": "barras", "confianza": 0.8,
                    "claves": {"rectangulos": len(rects),
                               "alineadas": alineadas,
                               "orientacion": "vertical"}}
        # base LATERAL alineada (barras horizontales: x o x+w constante)
        izquierdas = sorted(b["x"] for b in rects)
        med_izq = izquierdas[len(izquierdas) // 2]
        alineadas_izq = sum(1 for b in izquierdas
                            if abs(b - med_izq) <= 0.12 * ancho)
        derechas = sorted(b["x"] + b["w"] for b in rects)
        med_der = derechas[len(derechas) // 2]
        alineadas_der = sum(1 for b in derechas
                            if abs(b - med_der) <= 0.12 * ancho)
        horizontales = sum(1 for b in rects if b["w"] >= b["h"])
        if (alineadas_izq >= 3 or alineadas_der >= 3) and horizontales >= 2:
            return {"tipo": "barras", "confianza": 0.8,
                    "claves": {"rectangulos": len(rects),
                               "alineadas": max(alineadas_izq, alineadas_der),
                               "orientacion": "horizontal"}}
    # línea: tinta continua y delgada a lo ancho (blob alargado)
    for b in grandes:
        if b["w"] >= 0.5 * ancho and b["h"] <= 0.15 * alto:
            return {"tipo": "linea", "confianza": 0.6,
                    "claves": {"blob": [b["x"], b["y"], b["w"], b["h"]]}}
    return {"tipo": "indeterminado", "confianza": 0.0,
            "claves": {"grandes": len(grandes), "rectangulares": len(rects)}}


def _describir_simple(imagen) -> dict:
    """Análisis determinista de UNA imagen como gráfico único (sin grid)."""
    img, gris, rgb = _cargar(imagen)
    ancho, alto = img.size
    dark = _es_modo_oscuro(rgb)
    mascara = _tinta(gris, invertir=dark)
    blobs = _componentes(mascara)
    series = _colores_series(rgb, mascara)
    tipo = clasificar_tipo(blobs, ancho, alto, series)
    leyenda = describir_leyenda(rgb, blobs, ancho, alto)
    checks = [
        check_superposiciones(gris, mascara, blobs, ancho, alto),
        check_leyenda(rgb, mascara, blobs, ancho, alto, series),
        check_zoom_cortes(gris, mascara, ancho, alto),
        check_nitidez(gris, mascara),
        check_contraste(gris, mascara, invertir=dark),
        check_contraste_wcag(rgb, series),
        check_daltonismo(series),
        check_pie_slices(series, tipo["tipo"]),
        check_series_limit(series),
        check_texto_pequeno(blobs, ancho, alto),
        check_ruido(gris, mascara),
        check_resolucion(ancho),
    ]
    hallazgos = [h for c in checks for h in c]
    return {
        "dimensiones": [ancho, alto],
        "tipo": tipo["tipo"],
        "confianza_tipo": tipo["confianza"],
        "claves_tipo": tipo["claves"],
        "modo_oscuro": dark,
        "series": series,
        "n_series": len(series),
        "leyenda": leyenda,
        "hallazgos": hallazgos,
        "resumen": _resumen_determinista(tipo["tipo"], len(series), hallazgos,
                                         leyenda, dark),
    }


# ---------------------------------------------------------------------------
# Layout multi-panel (grid NxN): detección por gutters, alineación y estética
# ---------------------------------------------------------------------------

def _bandas_vacias(perfil: np.ndarray, tam_min: int, umbral: float = 0.02) -> list:
    """Bandas contiguas con poca tinta (candidatas a gutter o margen).

    perfil: fracción de tinta por fila (o columna). Devuelve [(inicio, fin)].
    """
    baja = perfil < umbral
    bandas = []
    en = False
    inicio = 0
    for i, b in enumerate(baja):
        if b and not en:
            en = True
            inicio = i
        elif not b and en:
            if i - inicio >= tam_min:
                bandas.append((inicio, i))
            en = False
    if en and len(baja) - inicio >= tam_min:
        bandas.append((inicio, len(baja)))
    return bandas


def detectar_layout(mascara: np.ndarray, ancho: int, alto: int) -> dict:
    """Detecta un grid de paneles (layout NxN) por gutters de poca tinta.

    Heurística (se reporta como estimación): los gutters INTERIORES —franjas
    casi vacías que no tocan el borde— separan paneles; cada celda del grid
    debe contener algo de tinta. Un gráfico único con márgenes solo produce
    bandas de borde, no gutters interiores.

    Los perfiles se miden sobre el rango central (10-90%): así los huecos
    internos de un gráfico (p. ej. columnas entre barras) no cuentan como
    gutters, porque el eje X o las etiquetas los cruzan en la base.
    """
    H, W = mascara.shape
    tam_min = max(6, min(ancho, alto) // 100)
    # rango central 5-95%: incluye el eje X en la base de las barras, de modo
    # que los huecos internos de un gráfico único no parezcan gutters
    perfil_h = mascara[:, W // 20:19 * W // 20].mean(axis=1)
    perfil_v = mascara[H // 20:19 * H // 20, :].mean(axis=0)
    bandas_h = _bandas_vacias(perfil_h, tam_min, umbral=0.003)
    bandas_v = _bandas_vacias(perfil_v, tam_min, umbral=0.003)
    gut_h = [(a, b) for (a, b) in bandas_h if a > 0 and b < H]
    gut_v = [(a, b) for (a, b) in bandas_v if a > 0 and b < W]

    def _intervalos(bordes):
        """Intervalos entre bordes (incluye margen inicial/final)."""
        return [(bordes[i], bordes[i + 1])
                for i in range(len(bordes) - 1)]

    cortes_h = [0] + [b for _, b in gut_h] + [H]
    cortes_v = [0] + [b for _, b in gut_v] + [W]
    filas = _intervalos(cortes_h)
    cols = _intervalos(cortes_v)

    def _celdas():
        return [[mascara[y0:y1, x0:x1].mean()
                 for x0, x1 in cols] for y0, y1 in filas]

    # filas/cols espurias: sin contenido, o con proporción extrema (un
    # título de grid es una franja ancha y chata, no un panel)
    def _celda_ok(a, b, es_alto):
        dim = b - a
        if dim < 0.05 * (alto if es_alto else ancho):
            return False
        otro = ancho if es_alto else alto
        if es_alto and otro / dim > 8:
            return False
        if not es_alto and otro / dim > 8:
            return False
        return True

    def _filtrar(items, es_alto, umbral):
        matrix = _celdas()
        out = []
        for i, (a, b) in enumerate(items):
            if not _celda_ok(a, b, es_alto):
                continue
            contenido = (matrix[i] if es_alto
                         else [matrix[f][i] for f in range(len(filas))])
            if not contenido or max(contenido) < umbral:
                continue
            out.append((a, b))
        return out

    # las columnas se filtran primero: una celda espuria (p. ej. una leyenda
    # pegada) no debe "salvar" una fila entera al promediar su contenido
    matrix_full = _celdas()
    max_global = max(max(fila) for fila in matrix_full) if matrix_full else 0.0
    umbral = max(0.005, 0.15 * max_global)
    cols = _filtrar(cols, False, umbral)
    filas = _filtrar(filas, True, umbral)
    # recomponer celdas tras el filtro (los cortes originales siguen valiendo)
    celda_matrix = _celdas()
    n_filas, n_cols = len(filas), len(cols)
    if n_filas * n_cols < 2:
        return {"es_multi": False, "n_filas": 1, "n_cols": 1, "paneles": [],
                "gutters_h": [], "gutters_v": [], "tam_gutter_h": 0,
                "tam_gutter_v": 0, "margenes": {}}
    paneles = []
    for f, (y0, y1) in enumerate(filas):
        for c, (x0, x1) in enumerate(cols):
            paneles.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                            "fila": f, "col": c,
                            "cobertura": float(celda_matrix[f][c])})
    margen_arriba = bandas_h[0][1] if bandas_h and bandas_h[0][0] == 0 else 0
    margen_abajo = (H - bandas_h[-1][0]) if bandas_h and bandas_h[-1][1] == H else 0
    margen_izq = bandas_v[0][1] if bandas_v and bandas_v[0][0] == 0 else 0
    margen_der = (W - bandas_v[-1][0]) if bandas_v and bandas_v[-1][1] == W else 0
    return {
        "es_multi": True,
        "n_filas": n_filas,
        "n_cols": n_cols,
        "paneles": paneles,
        "gutters_h": [b - a for a, b in gut_h],
        "gutters_v": [b - a for a, b in gut_v],
        "tam_gutter_h": int(np.median([b - a for a, b in gut_h])) if gut_h else 0,
        "tam_gutter_v": int(np.median([b - a for a, b in gut_v])) if gut_v else 0,
        "margenes": {"arriba": margen_arriba, "abajo": margen_abajo,
                     "izq": margen_izq, "der": margen_der},
    }


def _eje_x_panel(mascara: np.ndarray, panel: dict) -> int | None:
    """Fila de la línea de eje X dentro del panel (heurística).

    Busca la fila más densa de tinta en el 40% inferior del panel (el eje o
    la base de las barras). Se usa para comparar alineación entre paneles.
    """
    y0, h = panel["y"], panel["h"]
    inicio = y0 + int(h * 0.6)
    fin = y0 + h
    if inicio >= fin:
        return None
    sub = mascara[inicio:fin, panel["x"]:panel["x"] + panel["w"]]
    filas = sub.sum(axis=1)
    if filas.max() == 0:
        return None
    return inicio + int(filas.argmax())


def _desviacion_relativa(valores: list) -> float:
    """Desviación estándar relativa a la mediana (0 si vacío/mediana 0)."""
    if len(valores) < 2:
        return 0.0
    med = float(np.median(valores))
    if med == 0:
        return 0.0
    return float(np.std(valores) / med)


def check_layout(mascara: np.ndarray, layout: dict, ancho: int, alto: int) -> list:
    """Alineación, uniformidad y estética de un grid de paneles.

    Basado en las buenas prácticas de small multiples (Tufte; Wilke,
    Fundamentals of Data Visualization): ejes alineados, gutters uniformes,
    paneles del mismo tamaño y títulos compartidos.
    """
    hallazgos = []
    paneles = layout["paneles"]
    # ejes X alineados dentro de cada fila
    ejes = {}
    for p in paneles:
        ejes.setdefault(p["fila"], []).append((p, _eje_x_panel(mascara, p)))
    for fila, items in ejes.items():
        posiciones = [e for _, e in items if e is not None]
        if len(posiciones) >= 2:
            desv = _desviacion_relativa(posiciones)
            if desv > 0.02:
                hallazgos.append({
                    "tipo": "alineacion_ejes", "severidad": "aviso",
                    "mensaje": (f"ejes X de la fila {fila + 1} desalineados "
                                f"(desviacion {desv:.1%} de sus posiciones)"),
                    "evidencia": {"fila": fila, "posiciones": posiciones,
                                  "desviacion": round(desv, 3)},
                })
    # gutters uniformes (por orientación; un grid 2x2 tiene UN gutter por
    # eje y la irregularidad solo es medible con 3+ paneles por eje)
    for nombre, gutters in (("horizontal", layout["gutters_h"]),
                            ("vertical", layout["gutters_v"])):
        if len(gutters) >= 2:
            med = float(np.median(gutters))
            irregulares = [g for g in gutters if abs(g - med) > 0.4 * med]
            if irregulares:
                hallazgos.append({
                    "tipo": "gutter_irregular", "severidad": "aviso",
                    "mensaje": (f"espaciado {nombre} entre paneles irregular "
                                f"({gutters} px; mediana {med:.0f} px)"),
                    "evidencia": {"orientacion": nombre, "gutters": gutters,
                                  "mediana": round(med, 1)},
                })
    # tamaños relativos: anchos por fila, altos por columna
    anchos = {}
    altos = {}
    for p in paneles:
        anchos.setdefault(p["fila"], []).append(p["w"])
        altos.setdefault(p["col"], []).append(p["h"])
    desv_anchos = max((_desviacion_relativa(v) for v in anchos.values()), default=0)
    desv_altos = max((_desviacion_relativa(v) for v in altos.values()), default=0)
    if desv_anchos > 0.05 or desv_altos > 0.05:
        hallazgos.append({
            "tipo": "tamanos_paneles", "severidad": "aviso",
            "mensaje": (f"paneles de tamanos desiguales (anchos por fila "
                        f"{desv_anchos:.0%}, altos por columna {desv_altos:.0%})"),
            "evidencia": {"desv_anchos": round(desv_anchos, 3),
                          "desv_altos": round(desv_altos, 3)},
        })
    # paneles vacíos o casi vacíos
    for p in paneles:
        if p["cobertura"] < 0.002:
            hallazgos.append({
                "tipo": "panel_vacio", "severidad": "problema",
                "mensaje": (f"panel ({p['fila'] + 1},{p['col'] + 1}) vacio o "
                            f"casi vacio (cobertura {p['cobertura']:.2%})"),
                "evidencia": {"fila": p["fila"], "col": p["col"],
                              "cobertura": round(p["cobertura"], 5)},
            })
    # título general: la franja superior de la imagen debe tener texto
    franja_titulo = mascara[:max(4, alto // 20), :]
    if float(franja_titulo.mean()) < 0.001:
        hallazgos.append({
            "tipo": "titulo", "severidad": "aviso",
            "mensaje": "sin titulo general que describa el conjunto de paneles",
            "evidencia": {"franja_titulo": float(franja_titulo.mean())},
        })
    # márgenes externos muy asimétricos
    m = layout.get("margenes", {})
    if m:
        if max(m.get("izq", 0), 1) > 3 * max(m.get("der", 0), 1) or \
           max(m.get("der", 0), 1) > 3 * max(m.get("izq", 0), 1):
            hallazgos.append({
                "tipo": "margenes", "severidad": "aviso",
                "mensaje": (f"margenes laterales muy asimetricos "
                            f"(izq {m['izq']} px, der {m['der']} px)"),
                "evidencia": m,
            })
    return hallazgos


def _analisis_panel(imagen, panel: dict) -> dict:
    """Análisis determinista de un recorte de panel (como gráfico único)."""
    img = imagen.crop((panel["x"], panel["y"],
                       panel["x"] + panel["w"], panel["y"] + panel["h"]))
    res = _describir_simple(img)
    res.pop("resumen", None)
    return res


def _excluir_leyenda(mascara: np.ndarray, leyenda: dict | None,
                     ancho: int, alto: int) -> np.ndarray:
    """Máscara para el layout sin la franja perimetral de la leyenda.

    Una leyenda perimetral no es un panel: su franja (incluida la banda
    vacía que la separa del contenido) se elimina para que el detector de
    grid no genere columnas/filas espurias. La leyenda interior (sobre los
    datos) no se excluye: ahí el layout no aplica.
    """
    if not leyenda:
        return mascara
    pos = leyenda["posicion"]
    if pos == "interior":
        return mascara
    x0, y0, w, h = leyenda["caja"]
    m = mascara.copy()
    if pos in ("derecha", "izquierda"):
        perfil = m.mean(axis=0)
        if pos == "derecha":
            ult = np.where(perfil[:x0] > 0)[0]
            corte = int(ult[-1]) + 1 if len(ult) else x0
            m[:, corte:] = False
        else:
            x1 = x0 + w
            prim = np.where(perfil[x1:] > 0)[0]
            corte = int(prim[0]) + x1 if len(prim) else x0
            m[:, :corte] = False
    else:
        perfil = m.mean(axis=1)
        if pos == "abajo":
            ult = np.where(perfil[:y0] > 0)[0]
            corte = int(ult[-1]) + 1 if len(ult) else y0
            m[corte:, :] = False
        else:
            y1 = y0 + h
            prim = np.where(perfil[y1:] > 0)[0]
            corte = int(prim[0]) + y1 if len(prim) else y0
            m[:corte, :] = False
    return m


def describir_determinista(imagen) -> dict:
    """Análisis determinista completo: gráfico único o grid de paneles.

    Si la imagen contiene un layout NxN (múltiples gráficos), analiza cada
    panel por separado + la alineación/estética del conjunto y genera
    sugerencias de mejora (disposición, alineación, legibilidad, UX).
    """
    img, gris, rgb = _cargar(imagen)
    ancho, alto = img.size
    resultado = _describir_simple(imagen)
    dark = resultado["modo_oscuro"]
    mascara = _tinta(gris, invertir=dark)
    resultado["multi_panel"] = None
    # la franja perimetral de la leyenda no es un panel: se excluye del grid
    layout = detectar_layout(_excluir_leyenda(mascara, resultado["leyenda"],
                                              ancho, alto), ancho, alto)
    if layout["es_multi"]:
        paneles = []
        for p in layout["paneles"]:
            analisis = _analisis_panel(img, p)
            paneles.append({
                "indice": len(paneles) + 1,
                "fila": p["fila"], "col": p["col"],
                "x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"],
                "cobertura": round(p["cobertura"], 4),
                "tipo": analisis["tipo"],
                "n_series": analisis["n_series"],
                "leyenda": analisis["leyenda"],
                "hallazgos": analisis["hallazgos"],
            })
        layout_hallazgos = check_layout(mascara, layout, ancho, alto)
        layout.pop("es_multi", None)
        layout.pop("paneles", None)
        resultado["multi_panel"] = {
            "n_filas": layout.pop("n_filas"),
            "n_cols": layout.pop("n_cols"),
            "gutters_h": layout.pop("gutters_h"),
            "gutters_v": layout.pop("gutters_v"),
            "tam_gutter_h": layout.pop("tam_gutter_h"),
            "tam_gutter_v": layout.pop("tam_gutter_v"),
            "margenes": layout.pop("margenes"),
            "paneles": paneles,
            "hallazgos_layout": layout_hallazgos,
        }
    resultado["sugerencias"] = _sugerencias(resultado)
    resultado["resumen"] = _resumen_completo(resultado)
    return resultado


def _resumen_determinista(tipo: str, n_series: int, hallazgos: list,
                          leyenda: dict | None = None,
                          dark: bool = False) -> str:
    lineas = [f"Grafico de tipo '{tipo}'"
              + (" en modo oscuro" if dark else "")
              + (f" con {n_series} serie(s) de color" if n_series else "")
              + "."]
    if leyenda:
        pos = leyenda["posicion"]
        n_entradas = leyenda["n_entradas"]
        lineas.append(f"Leyenda {'con titulo' if leyenda['titulo'] else 'sin titulo'}: "
                      f"{n_entradas} entrada(s) en el margen {pos}.")
    graves = [h for h in hallazgos if h["severidad"] == "problema"]
    avisos = [h for h in hallazgos if h["severidad"] == "aviso"]
    if graves:
        lineas.append(f"PROBLEMAS ({len(graves)}): " + "; ".join(h["mensaje"] for h in graves))
    if avisos:
        lineas.append(f"Avisos ({len(avisos)}): " + "; ".join(h["mensaje"] for h in avisos))
    if not hallazgos:
        lineas.append("Sin hallazgos deterministas de problemas visuales.")
    return " ".join(lineas)


# ---------------------------------------------------------------------------
# Sugerencias de mejora (disposición, alineación, legibilidad, UX)
# ---------------------------------------------------------------------------

# Cada hallazgo determinista tiene una sugerencia accionable basada en buenas
# prácticas documentadas (fuentes en docs/INVESTIGACION-VISUALIZACION.md):
# baseline cero y ejes alineados (SWD/FT), gutters uniformes y small multiples
# (Tufte/Wilke), contraste WCAG 1.4.3/1.4.11, máximo 4-5 series (SWD) y
# leyenda fuera del área de ploteo (data-to-viz).
_SUGERENCIAS_POR_TIPO = {
    "superposicion": ("separar las etiquetas superpuestas: rotar, reducir la "
                      "frecuencia de ticks o mostrar solo cada N valores"),
    "leyenda": ("revisar la leyenda: etiquetar las series (o agregar leyenda "
                "exterior) sin tapar los datos"),
    "leyenda_marcador": ("agregar marcador de color a cada entrada de la "
                         "leyenda: sin el swatch el lector no puede asociar "
                         "el texto a la serie"),
    "leyenda_entradas": ("completar la leyenda: una entrada por serie, con el "
                         "mismo nombre y color que la serie (o eliminar "
                         "entradas sin serie)"),
    "leyenda_color": ("revisar los colores de la leyenda: cada entrada debe "
                      "usar el color exacto de su serie en el grafico"),
    "zoom": ("ajustar el encuadre: el contenido no debe tocar los bordes de "
             "la imagen ni quedar diminuto en el lienzo"),
    "nitidez": ("usar la imagen original a resolucion completa en vez de una "
                "re-escalada (el upscale suaviza el texto)"),
    "contraste": ("aumentar el contraste de texto y series contra el fondo "
                  "(WCAG: 4.5:1 texto, 3:1 objetos graficos)"),
    "contraste_wcag": ("aumentar el contraste de las series contra el fondo "
                       "(WCAG SC 1.4.11: 3:1 para objetos graficos)"),
    "daltonismo": ("usar una paleta colorblind-safe (ColorBrewer) o "
                   "diferenciar por forma/patron ademas del color"),
    "pie_slices": ("reducir el numero de slices del pastel (~5 maximo) o "
                   "pasar a barras (comparar angulos es impreciso)"),
    "series": ("reducir a 4-5 series o dividir en small multiples "
               "(facetas alineadas)"),
    "texto": ("aumentar el tamano de fuente de las etiquetas (los defaults de "
              "los software suelen ser demasiado pequenos)"),
    "ruido": ("re-exportar sin artefactos (PNG, o JPEG con calidad alta)"),
    "resolucion": ("exportar el grafico a mayor resolucion"),
    "alineacion_ejes": ("alinear los ejes X de los paneles de la misma fila "
                        "(small multiples: misma escala y misma posicion)"),
    "gutter_irregular": ("uniformar el espaciado entre paneles (gutter "
                         "constante en filas y columnas)"),
    "tamanos_paneles": ("usar paneles del mismo tamano en cada fila/columna "
                        "salvo diseno intencional"),
    "panel_vacio": ("revisar o eliminar el panel sin contenido"),
    "titulo": ("agregar un titulo general que describa el conjunto y titulos "
               "por panel"),
    "margenes": ("equilibrar los margenes externos del grid"),
}


def _sugerencias(resultado: dict) -> list:
    """Sugerencias accionables a partir de los hallazgos (globales, de
    layout y por panel) + sugerencias de disposición según la forma del grid.
    """
    sugerencias = []
    vistos = set()
    hallazgos = list(resultado.get("hallazgos", []))
    multi = resultado.get("multi_panel") or {}
    hallazgos += multi.get("hallazgos_layout", [])
    for p in multi.get("paneles", []):
        for h in p.get("hallazgos", []):
            h = dict(h)
            h["panel"] = p["indice"]
            hallazgos.append(h)
    for h in hallazgos:
        base = _SUGERENCIAS_POR_TIPO.get(h["tipo"])
        if not base or h["tipo"] in vistos:
            continue
        vistos.add(h["tipo"])
        panel = f" (panel {h['panel']})" if h.get("panel") else ""
        sugerencias.append({
            "tipo": h["tipo"],
            "severidad": h["severidad"],
            "sugerencia": base + panel,
        })
    # disposición del grid: proporciones extremas sugieren otra distribución
    if multi:
        n_filas, n_cols = multi.get("n_filas", 1), multi.get("n_cols", 1)
        paneles = multi.get("paneles", [])
        if paneles:
            med_w = float(np.median([p["w"] for p in paneles]))
            med_h = float(np.median([p["h"] for p in paneles]))
            if med_h > 0 and med_w / med_h < 0.8:
                sugerencias.append({
                    "tipo": "disposicion", "severidad": "info",
                    "sugerencia": (f"los paneles son muy angostos "
                                   f"({n_filas}x{n_cols}): considerar mas "
                                   f"columnas o etiquetas apiladas"),
                })
            elif med_h > 0 and med_w / med_h > 2.5:
                sugerencias.append({
                    "tipo": "disposicion", "severidad": "info",
                    "sugerencia": (f"los paneles son muy achatados "
                                   f"({n_filas}x{n_cols}): considerar mas "
                                   f"filas para que las series se lean"),
                })
        if n_filas * n_cols >= 6:
            sugerencias.append({
                "tipo": "disposicion", "severidad": "info",
                "sugerencia": (f"{n_filas}x{n_cols} paneles: si el lector no "
                               f"necesita comparar todos a la vez, dividir "
                               f"en grupos mas pequenos"),
            })
    return sugerencias


def _resumen_completo(resultado: dict) -> str:
    lineas = [resultado["resumen"]]
    multi = resultado.get("multi_panel")
    if multi:
        lineas.append(f"Grid {multi['n_filas']}x{multi['n_cols']} de "
                      f"{len(multi['paneles'])} paneles.")
        graves = [h for h in multi["hallazgos_layout"]
                  if h["severidad"] == "problema"]
        if graves:
            lineas.append("PROBLEMAS del layout: "
                          + "; ".join(h["mensaje"] for h in graves))
    sug = resultado.get("sugerencias", [])
    if sug:
        lineas.append("Sugerencias: "
                      + "; ".join(s["sugerencia"] for s in sug[:4]))
    return " ".join(lineas)


# ---------------------------------------------------------------------------
# Visión IA opt-in (patrón de revision.py: motores de scripts/bateria_360.py)
# ---------------------------------------------------------------------------

PROMPT_VISION = (
    "Eres un auditor de graficos y charts. Describe esta imagen de un grafico "
    "en espanol: tipo de grafico, variables/ejes, series y su tendencia "
    "general, y valores destacados si se leen claramente. Luego evalua los "
    "siguientes aspectos de presentacion con formato 'Aspecto: nota/10' en "
    "lineas separadas: superposiciones (etiquetas/series que se solapan), "
    "leyenda (presente, legible, no tapa datos, una entrada por serie con su "
    "marcador de color), zoom (recortes, contenido muy chico o muy grande), "
    "errores visuales (texto cortado, ejes rotos, valores que no coinciden), "
    "estetica general (alineacion, espacios, contraste). Si un aspecto no "
    "aplica, escribe 'Aspecto: N/A'. Responde solo con la descripcion y las "
    "lineas 'Aspecto: nota/10'."
)


def _cargar_motores():
    """Import perezoso de los motores VLM validados (scripts/bateria_360.py)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "scripts"))
    try:
        from bateria_360 import run_docbee, run_ollama
        return run_docbee, run_ollama
    except Exception as exc:  # noqa: BLE001 - el error se reporta al llamador
        return None, f"scripts/bateria_360.py no importable: {exc}"


def _parsear_rubrica_vlm(texto: str) -> dict:
    """Notas por aspecto ('Aspecto: nota/10' o 'nota/10: Aspecto').

    Las líneas fuera de rúbrica cuentan como no_conformes (el VLM inventa
    dimensiones; patrón de revision._parsear_rubrica).
    """
    import re
    notas = {}
    fuera = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        m = re.match(r"^(.+?)\s*[:=]\s*(?:(\d{1,2})\s*/\s*10|N/A|n/a|na)\s*$", linea)
        if m:
            nota = None if m.group(2) is None else float(m.group(2))
            notas[m.group(1).strip().lower()] = nota
            continue
        m = re.match(r"^(?:(\d{1,2})\s*/\s*10|N/A|n/a|na)\s*[:=]\s*(.+)$", linea)
        if m:
            nota = None if m.group(1) is None else float(m.group(1))
            notas[m.group(2).strip().lower()] = nota
            continue
        fuera.append(linea)
    return {"notas": notas, "no_conformes": fuera}


def vision_ia(imagen, motor: str = "docbee", device: str = "gpu",
              host: str = "127.0.0.1", modelo: str = "gemma3:4b",
              timeout_s: float = 600.0) -> dict:
    """Describe la imagen con un VLM local (docbee u ollama).

    Devuelve {"ok": bool, "texto": ..., "rubrica": {...} | None, "error": ...}.
    """
    run_docbee, error = _cargar_motores()
    if run_docbee is None:
        return {"ok": False, "texto": "", "rubrica": None, "error": error}
    import tempfile
    import shutil
    if isinstance(imagen, str):
        png = imagen
    else:
        png = os.path.join(tempfile.mkdtemp(prefix="auditoria_vlm_"), "grafico.png")
        imagen.save(png)
    try:
        if motor == "docbee":
            res = run_docbee(png, PROMPT_VISION, device, timeout_s=timeout_s)
        else:
            res = run_ollama(png, PROMPT_VISION, host, modelo, timeout_s=timeout_s)
        if not isinstance(res, dict) or not res.get("ok"):
            error = res.get("error") if isinstance(res, dict) else str(res)
            return {"ok": False, "texto": "", "rubrica": None,
                    "error": error or f"motor {motor} sin respuesta"}
        texto = res.get("texto", "")
        return {"ok": True, "texto": texto,
                "rubrica": _parsear_rubrica_vlm(texto), "error": None}
    except Exception as exc:  # noqa: BLE001 - fallo del motor, reportado
        return {"ok": False, "texto": "", "rubrica": None,
                "error": f"motor {motor}: {exc}"}
    finally:
        if not isinstance(imagen, str):
            try:
                shutil.rmtree(os.path.dirname(png))
            except OSError:
                pass


def auditar(imagen, vision: str | None = None, device: str = "gpu",
            timeout_s: float = 600.0) -> dict:
    """Auditoría completa (determinista + VLM opcional) de un gráfico."""
    resultado = describir_determinista(imagen)
    resultado["vision"] = None
    if vision:
        v = vision_ia(imagen, motor=vision, device=device, timeout_s=timeout_s)
        resultado["vision"] = {
            "motor": vision,
            "ok": v["ok"],
            "texto": v["texto"],
            "rubrica": v["rubrica"],
            "error": v["error"],
        }
        if v["ok"] and v["rubrica"]:
            # la capa determinista manda sobre la visual en contradicciones:
            # se anota qué detectó el VLM como bajo en aspectos que el
            # determinista midió como correctos
            contradicciones = []
            notas = v["rubrica"]["notas"]
            if notas.get("zoom") is not None and notas["zoom"] >= 7:
                if any(h["tipo"] == "zoom" for h in resultado["hallazgos"]):
                    contradicciones.append("zoom")
            if notas.get("superposiciones") is not None and notas["superposiciones"] >= 7:
                if any(h["tipo"] == "superposicion" for h in resultado["hallazgos"]):
                    contradicciones.append("superposiciones")
            if notas.get("leyenda") is not None and notas["leyenda"] >= 7:
                if any(h["tipo"] == "leyenda" for h in resultado["hallazgos"]):
                    contradicciones.append("leyenda")
            resultado["vision"]["contradicciones"] = contradicciones
    resultado["resumen"] = _resumen_combinado(resultado)
    return resultado


def _resumen_combinado(resultado: dict) -> str:
    lineas = [resultado["resumen"]]
    vision = resultado.get("vision")
    if vision and vision.get("ok"):
        lineas.append("VLM: " + vision["texto"].splitlines()[0][:160]
                      if vision["texto"] else "VLM sin texto")
        if vision.get("contradicciones"):
            lineas.append("Contradicciones VLM/determinista (manda el determinista): "
                          + ", ".join(vision["contradicciones"]))
    elif vision and vision.get("error"):
        lineas.append(f"VLM no disponible: {vision['error']}")
    return " ".join(lineas)


# ---------------------------------------------------------------------------
# Salidas y CLI
# ---------------------------------------------------------------------------

def _a_markdown(resultado: dict) -> str:
    lineas = [f"# Auditoría de gráfico — {resultado['tipo']}",
              "",
              f"- Dimensiones: {resultado['dimensiones'][0]}x{resultado['dimensiones'][1]}",
              f"- Tipo detectado: {resultado['tipo']} "
              f"(confianza {resultado['confianza_tipo']})",
              f"- Series de color: {resultado['n_series']}"]
    if resultado.get("modo_oscuro"):
        lineas.append("- Modo oscuro: si")
    if resultado["series"]:
        lineas.append("- Colores: " + ", ".join(s["color"] for s in resultado["series"]))
    leyenda = resultado.get("leyenda")
    if leyenda:
        lineas += ["", "## Leyenda",
                   f"- Posicion: {leyenda['posicion']}",
                   f"- Caja: {leyenda['caja']}",
                   f"- Entradas: {leyenda['n_entradas']}",
                   (f"- Titulo: {leyenda['titulo']}"
                    if leyenda["titulo"] else "- Sin titulo")]
        for e in leyenda["entradas"]:
            marcador = e["marcador"] or "sin marcador"
            lineas.append(f"  - color={e['color'] or 'sin color'} marcador={marcador} "
                          f"texto={e['texto']}")
    hallazgos = resultado["hallazgos"]
    lineas += ["", f"## Hallazgos ({len(hallazgos)})", ""]
    if not hallazgos:
        lineas.append("Sin hallazgos deterministas.")
    for h in hallazgos:
        lineas.append(f"- **[{h['severidad'].upper()}]** {h['tipo']}: {h['mensaje']}")
    vision = resultado.get("vision")
    if vision and vision.get("ok"):
        lineas += ["", "## Visión IA", "", vision["texto"]]
        if vision.get("rubrica"):
            lineas += ["", "### Rúbrica"]
            for k, v in vision["rubrica"]["notas"].items():
                lineas.append(f"- {k}: {v if v is not None else 'N/A'}/10")
            if vision["rubrica"]["no_conformes"]:
                lineas += ["", "Líneas fuera de rúbrica (no conformes):"]
                lineas += [f"- {l}" for l in vision["rubrica"]["no_conformes"]]
    elif vision:
        lineas += ["", f"## Visión IA — no disponible: {vision.get('error')}"]
    return "\n".join(lineas) + "\n"


def generar_demo(ruta: str = "ejemplos/grafico_auditoria_demo.png") -> str:
    """Genera un gráfico de barras sintético con problemas a propósito
    (etiquetas superpuestas, entrada de leyenda sin marcador de color) para
    probar la auditoría sin archivos externos. La leyenda incluye título,
    entrada con marcador y entrada sin marcador (descripción de elementos)."""
    img = Image.new("RGB", (1000, 600), "white")
    d = ImageDraw.Draw(img)
    # barras
    for i, v in enumerate([120, 190, 80, 210, 150]):
        x0 = 130 + i * 160
        d.rectangle([x0, 600 - 40 - v, x0 + 90, 600 - 40], fill=(52, 101, 164))
    # etiquetas de valores superpuestas (mismo lugar)
    for i in range(5):
        x = 175 + i * 160
        d.text((x - 6, 320), "1.234", fill="black")
        d.text((x - 4, 322), "1.234", fill="black")
    # leyenda derecha: titulo + entrada con marcador + entrada sin marcador
    fuente = _fuente_demo(18)
    d.text((880, 95), "Ventas", fill="black", font=fuente)
    d.rectangle([878, 134, 896, 152], fill=(52, 101, 164))
    d.text((902, 132), "Serie A", fill="black", font=fuente)
    d.text((902, 170), "Serie B", fill="black", font=fuente)
    # eje
    d.line([(60, 560), (860, 560)], fill="black", width=3)
    img.save(ruta)
    return ruta


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auditoría visual y descripción de gráficos/charts")
    parser.add_argument("imagen", nargs="?", help="ruta a la imagen del gráfico")
    parser.add_argument("--vision", choices=("docbee", "ollama"), default=None,
                        help="descripción e interpretación con VLM local")
    parser.add_argument("--device", default="gpu",
                        help="device para docbee (default gpu; cpu con CUDA_VISIBLE_DEVICES='')")
    parser.add_argument("--salida", choices=("json", "md", "txt"), default="json")
    parser.add_argument("--salida-archivo", default="",
                        help="guardar el informe en esta ruta en vez de stdout")
    parser.add_argument("--demo", action="store_true",
                        help="generar ejemplos/grafico_auditoria_demo.png y auditar")
    args = parser.parse_args()

    if args.demo:
        ruta = generar_demo()
        print(f"Demo generada: {ruta}")
    elif not args.imagen:
        parser.error("falta la imagen (o usa --demo)")
    else:
        ruta = args.imagen

    resultado = auditar(ruta, vision=args.vision, device=args.device)
    if args.salida == "json":
        texto = json.dumps(resultado, ensure_ascii=False, indent=2) + "\n"
    elif args.salida == "md":
        texto = _a_markdown(resultado)
    else:
        texto = resultado["resumen"] + "\n"
        if args.vision and resultado.get("vision") and resultado["vision"].get("ok"):
            texto += "\n" + resultado["vision"]["texto"] + "\n"
    if args.salida_archivo:
        with open(args.salida_archivo, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"Informe guardado en {args.salida_archivo}")
    else:
        sys.stdout.write(texto)


if __name__ == "__main__":
    main()
