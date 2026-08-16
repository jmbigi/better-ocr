#!/usr/bin/env python3
"""Auditoría visual y descripción de gráficos y charts.

Dos capas complementarias (patrón de revision.py):

  1. Determinista (PIL + numpy, sin modelos): detecta superposiciones de
     etiquetas, problemas de leyenda (ausente, cortada, sobre los datos),
     recortes/zoom excesivo, falta de nitidez, bajo contraste, ruido, baja
     resolución, y describe el tipo de gráfico y el número de series por
     colores de tinta.
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
from PIL import Image, ImageDraw, ImageFilter


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


def _tinta(gris: np.ndarray, umbral: float = 200.0) -> np.ndarray:
    """Máscara booleana de tinta: píxeles oscuros (texto/líneas/barras)."""
    return gris < umbral


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
    lienzo), como una barra o un área rellena.
    """
    solido = _mascara_solidos(mascara)
    excluir = np.zeros_like(mascara)
    area_img = mascara.shape[0] * mascara.shape[1]
    for b in _componentes(solido):
        if b["area"] >= 0.001 * area_img:
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
    bloques (barras, cajas).
    """
    area_img = ancho * alto
    texto = []
    for b in blobs:
        if not (0.00006 * area_img <= b["area"] <= 0.03 * area_img):
            continue
        if b["h"] <= 0 or b["w"] <= 0:
            continue
        ratio = b["w"] / b["h"]
        if 0.15 <= ratio <= 6.0:
            texto.append(b)
    return texto


def check_leyenda(gris, mascara, blobs, ancho, alto, series) -> list:
    """Leyenda: ausente con series, cortada por el borde o sobre los datos."""
    hallazgos = []
    texto = _blobs_texto(blobs, ancho, alto)
    # agrupar blobs de texto por proximidad (gap <= 2x la altura del mayor)
    grupos = []
    for t in texto:
        mejor = None
        for g in grupos:
            if _interseccion({"x": g["x"] - 4 * g["h"], "y": g["y"] - 2 * g["h"],
                              "w": g["w"] + 8 * g["h"], "h": g["h"] + 4 * g["h"]},
                             t) > 0:
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
    # candidatos a leyenda: grupos de >= 2 elementos en el perímetro (20%)
    margen_x = 0.2 * ancho
    margen_y = 0.2 * alto
    candidatas = []
    for g in grupos:
        if g["area"] < 2 * (0.00025 * ancho * alto):
            continue
        perimetral = (g["x"] <= margen_x or g["x"] + g["w"] >= ancho - margen_x
                      or g["y"] <= margen_y or g["y"] + g["h"] >= alto - margen_y)
        if perimetral:
            candidatas.append(g)
    if not candidatas:
        # sin leyenda visible en el perímetro: avisar si hay varias series
        if len(series) >= 2:
            hallazgos.append({
                "tipo": "leyenda", "severidad": "aviso",
                "mensaje": (f"{len(series)} series de colores sin leyenda visible "
                            f"en el perimetro"),
                "evidencia": {"series": len(series)},
            })
        return hallazgos
    caja = candidatas[0]
    # leyenda cortada por el borde de la imagen (recorte)
    pegado = (caja["x"] <= 1 or caja["y"] <= 1
              or caja["x"] + caja["w"] >= ancho - 1
              or caja["y"] + caja["h"] >= alto - 1)
    if pegado:
        hallazgos.append({
            "tipo": "leyenda", "severidad": "aviso",
            "mensaje": "leyenda pegada/cortada por el borde de la imagen",
            "evidencia": {"caja": [caja["x"], caja["y"], caja["w"], caja["h"]]},
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
                          "caja": [caja["x"], caja["y"], caja["w"], caja["h"]]},
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


def check_contraste(gris, mascara) -> list:
    """Contraste insuficiente entre tinta y fondo.

    La máscara laxa (gris < 250) captura también tintas pálidas: un
    elemento 245/255 sobre fondo blanco es poco contrastado aunque no sea
    "tinta oscura" en el sentido del umbral principal.
    """
    laxa = gris < 250
    if laxa.sum() == 0:
        return []
    c = _contraste(gris, laxa)
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
    # pastel: un blob grande casi circular (densidad alta, w≈h)
    for b in grandes:
        if b["w"] >= 0.25 * ancho and b["h"] >= 0.25 * alto:
            if abs(b["w"] - b["h"]) <= 0.15 * max(b["w"], b["h"]):
                return {"tipo": "pastel", "confianza": 0.7,
                        "claves": {"blob": [b["x"], b["y"], b["w"], b["h"]]}}
    # barras: >= 3 rectángulos alineados verticalmente (filas base parecidas)
    if len(rects) >= 3:
        bases = sorted(b["y"] + b["h"] for b in rects)
        mediana = bases[len(bases) // 2]
        alineadas = sum(1 for b in bases if abs(b - mediana) <= 0.12 * alto)
        verticales = sum(1 for b in rects if b["h"] > b["w"])
        if alineadas >= 3 and verticales >= 2:
            return {"tipo": "barras", "confianza": 0.8,
                    "claves": {"rectangulos": len(rects),
                               "alineadas": alineadas, "verticales": verticales}}
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
    mascara = _tinta(gris)
    blobs = _componentes(mascara)
    series = _colores_series(rgb, mascara)
    tipo = clasificar_tipo(blobs, ancho, alto, series)
    checks = [
        check_superposiciones(gris, mascara, blobs, ancho, alto),
        check_leyenda(gris, mascara, blobs, ancho, alto, series),
        check_zoom_cortes(gris, mascara, ancho, alto),
        check_nitidez(gris, mascara),
        check_contraste(gris, mascara),
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
        "series": series,
        "n_series": len(series),
        "hallazgos": hallazgos,
        "resumen": _resumen_determinista(tipo["tipo"], len(series), hallazgos),
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


def describir_determinista(imagen) -> dict:
    """Análisis determinista completo: gráfico único o grid de paneles.

    Si la imagen contiene un layout NxN (múltiples gráficos), analiza cada
    panel por separado + la alineación/estética del conjunto y genera
    sugerencias de mejora (disposición, alineación, legibilidad, UX).
    """
    img, gris, rgb = _cargar(imagen)
    ancho, alto = img.size
    mascara = _tinta(gris)
    resultado = _describir_simple(imagen)
    resultado["multi_panel"] = None
    layout = detectar_layout(mascara, ancho, alto)
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


def _resumen_determinista(tipo: str, n_series: int, hallazgos: list) -> str:
    lineas = [f"Grafico de tipo '{tipo}'"
              + (f" con {n_series} serie(s) de color" if n_series else "")
              + "."]
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
    "zoom": ("ajustar el encuadre: el contenido no debe tocar los bordes de "
             "la imagen ni quedar diminuto en el lienzo"),
    "nitidez": ("usar la imagen original a resolucion completa en vez de una "
                "re-escalada (el upscale suaviza el texto)"),
    "contraste": ("aumentar el contraste de texto y series contra el fondo "
                  "(WCAG: 4.5:1 texto, 3:1 objetos graficos)"),
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
    "leyenda (presente, legible, no tapa datos), zoom (recortes, contenido "
    "muy chico o muy grande), errores visuales (texto cortado, ejes rotos, "
    "valores que no coinciden), estetica general (alineacion, espacios, "
    "contraste). Si un aspecto no aplica, escribe 'Aspecto: N/A'. "
    "Responde solo con la descripcion y las lineas 'Aspecto: nota/10'."
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
        texto = (res or {}).get("respuesta", "") if isinstance(res, dict) else str(res)
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
    if resultado["series"]:
        lineas.append("- Colores: " + ", ".join(s["color"] for s in resultado["series"]))
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
    (etiquetas superpuestas, leyenda pegada al borde) para probar la
    auditoría sin archivos externos."""
    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    # barras
    for i, v in enumerate([120, 190, 80, 210, 150]):
        x0 = 120 + i * 150
        d.rectangle([x0, 600 - 40 - v, x0 + 90, 600 - 40], fill=(52, 101, 164))
    # etiquetas de valores superpuestas (mismo lugar)
    for i in range(5):
        x = 165 + i * 150
        d.text((x - 6, 320), "1.234", fill="black")
        d.text((x - 4, 322), "1.234", fill="black")
    # leyenda pegada al borde derecho
    d.rectangle([880, 120, 940, 260], outline="black")
    d.text((885, 130), "Serie A", fill="black")
    d.text((885, 180), "Serie B", fill="black")
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
