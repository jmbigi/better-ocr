#!/usr/bin/env python3
"""Buscador avanzado multi-motor + recetas de dominio (Playwright).

Busca una consulta en varios buscadores, detecta bloqueos/captchas y —si se
pide (--captcha)— resuelve el reCAPTCHA v2 de la pagina en la MISMA sesion
del navegador reutilizando el stack de captcha_web/captcha_ia (ancla ->
bframe con tiles -> RT-DETR -> clics -> VERIFY). El resto de bloqueos (slider
de Brave, turnstile de Ecosia, challenge de DDG, 403 de Mojeek, suspension de
Startpage) se reportan con su estado real: esta IP los muestra todos
verificados en vivo (leccion 20 hallazgo 8).

Las recetas de dominio anaden fuentes especializadas: cuitonline (busqueda de
CUIT/razon social por nombre) y dateas (reporta 404: la URL actual no existe,
verificado en vivo 2026-08-12).

Piezas puras (parsers, normalizacion, ranking, deteccion de bloqueos) son
testeables sin Playwright; el orquestador lo importa de forma perezosa.

Uso:
    python3 buscador.py "Permanencia Salud Srl" --captcha --salida /var/tmp/busq
    python3 buscador.py "YPF" --motores bing --recetas cuit
"""

import argparse
import base64
import html as html_mod
import json
import os
import re
import time
import urllib.parse

__all__ = [
    "MOTORES", "RECETAS", "normalizar_url", "extraer_resultados_bing",
    "extraer_resultados_google", "extraer_resultados_ddg",
    "extraer_resultados_cuitonline", "detectar_bloqueo", "detectar_recaptcha",
    "deduplicar", "armar_sintesis", "buscar_en_web", "buscar_recetas",
    "resolver_recaptcha_en_pagina",
]

# ---------------------------------------------------------------------------
# Definicion de motores y recetas
# ---------------------------------------------------------------------------

# "parser": None = el motor solo detecta bloqueo (su DOM de resultados no se
# pudo verificar en vivo desde esta IP, ver P0.2). "verificado": True solo
# cuando el parser corrio contra HTML real de resultados.
MOTORES = {
    "google": {
        "nombre": "Google",
        "url": "https://www.google.com/search?q={q}&hl=es&num=10",
        "parser": "google",
        "parser_verificado": False,  # esta IP solo muestra la pagina "sorry"
    },
    "bing": {
        "nombre": "Bing",
        "url": "https://www.bing.com/search?q={q}&setlang=es",
        "parser": "bing",
        "parser_verificado": True,   # HTML real capturado 2026-08-12
    },
    "brave": {
        "nombre": "Brave Search",
        "url": "https://search.brave.com/search?q={q}",
        "parser": None,
        "parser_verificado": False,  # aqui siempre captcha slider
    },
    "ddg": {
        "nombre": "DuckDuckGo",
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "parser": "ddg",
        "parser_verificado": False,  # aqui siempre challenge "select ducks"
    },
    "mojeek": {
        "nombre": "Mojeek",
        "url": "https://www.mojeek.com/search?q={q}",
        "parser": None,
        "parser_verificado": False,  # aqui siempre 403
    },
    "ecosia": {
        "nombre": "Ecosia",
        "url": "https://www.ecosia.org/search?q={q}",
        "parser": None,
        "parser_verificado": False,  # aqui siempre challenge turnstile
    },
    "startpage": {
        "nombre": "Startpage",
        "url": "https://www.startpage.com/sp/search?query={q}",
        "parser": None,
        "parser_verificado": False,  # aqui "conexion suspendida"
    },
}

# Recetas de dominio: fuentes especializadas con su propio parser y
# estructura de resultados (campos extra por receta, p. ej. "cuit").
RECETAS = {
    "cuit": {
        "nombre": "CUIT (CuitOnline + Dateas)",
        "fuentes": ["cuitonline", "dateas"],
    },
}

# Indicadores de bloqueo por motor, verificados con HTML real de esta IP
# (2026-08-12). El orden importa: el primero que matchea define el estado.
BLOQUEOS = {
    "google": [
        ("captcha", re.compile(r"recaptcha|g-recaptcha|sorry|tr[áa]fico inusual",
                               re.I)),
    ],
    "bing": [
        ("sin_resultados", re.compile(r"no hay resultados para", re.I)),
    ],
    "brave": [
        ("captcha_slider", re.compile(r"captcha|control deslizante|slider", re.I)),
    ],
    "ddg": [
        ("challenge_ddg", re.compile(
            r"bots use DuckDuckGo|select all squares|challenge", re.I)),
    ],
    "mojeek": [
        ("http_403", re.compile(r"403 - Forbidden|forbidden", re.I)),
    ],
    "ecosia": [
        ("turnstile", re.compile(
            r"confirm you'?re not a robot|unusual traffic|turnstile", re.I)),
    ],
    "startpage": [
        ("suspendida", re.compile(
            r"conexi[óo]n suspendida|suspended|lamentamos las molestias", re.I)),
    ],
    "cuitonline": [
        ("sin_resultados", re.compile(r"no obtuvo resultados", re.I)),
    ],
    "dateas": [
        ("pagina_no_encontrada", re.compile(r"p[áa]gina no encontrada", re.I)),
    ],
}

# ---------------------------------------------------------------------------
# Utilidades puras
# ---------------------------------------------------------------------------

def _texto(bloque: str) -> str:
    """Limpia una porcion de HTML a texto plano: los tags se quitan SIN
    espacio de reemplazo (un <strong> cortado no debe inventar espacios;
    los espacios reales del HTML se conservan) y los espacios se colapsan."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", bloque)).strip()


def normalizar_url(url: str) -> str:
    """Limpia la URL para deduplicar: quita fragmento, utm_*, y resuelve el
    redirector de Bing (ck/a -> u= base64 del destino real)."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    # redirector de Bing: https://www.bing.com/ck/a?...&u=a1<base64>
    if "bing.com/ck/a" in url:
        m = re.search(r"[?&]u=([^&]+)", url)
        if m:
            try:
                enc = urllib.parse.unquote(m.group(1))
                if enc.startswith("a1"):
                    enc = enc[2:]
                if "://" in enc:
                    url = enc
                else:
                    destino = base64.urlsafe_b64decode(
                        enc + "=" * (-len(enc) % 4)).decode("utf-8", "replace")
                    if destino.startswith(("http://", "https://")):
                        url = destino
            except Exception:
                pass
    parts = urllib.parse.urlsplit(url)
    if not parts.netloc:
        return ""
    query = [p for p in parts.query.split("&")
             if p and not p.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                                    parts.path.rstrip("/"),
                                    "&".join(query), ""))


def deduplicar(resultados: list) -> list:
    """Fusiona resultados por URL normalizada; conserva la mejor posicion y
    las fuentes. Entrada: lista de dicts {motor, posicion, titulo, url,
    snippet}. Devuelve lista de dicts {titulo, url, snippet, fuentes,
    mejor_posicion}."""
    por_url = {}
    for r in resultados:
        if not r.get("url"):
            continue
        url = normalizar_url(r["url"])
        if not url:
            continue
        clave = url.lower()
        if clave not in por_url:
            por_url[clave] = {
                "titulo": r.get("titulo", ""),
                "url": url,
                "snippet": r.get("snippet", ""),
                "fuentes": [],
                "mejor_posicion": r.get("posicion", 99),
            }
        e = por_url[clave]
        if r["motor"] not in e["fuentes"]:
            e["fuentes"].append(r["motor"])
        e["mejor_posicion"] = min(e["mejor_posicion"], r.get("posicion", 99))
        if not e["titulo"]:
            e["titulo"] = r.get("titulo", "")
    return list(por_url.values())


def armar_sintesis(motores_estados: list, top: int = 10) -> list:
    """Ranking global: score = suma de 1/(posicion+2) por fuente + bonus
    por dominio conocido (cuitonline, dateas, afip). Entrada: lista de
    estados de motores (los que tengan "resultados")."""
    por_url = {}
    for m in motores_estados:
        for r in m.get("resultados", []):
            url = normalizar_url(r.get("url", ""))
            if not url:
                continue
            clave = url.lower()
            if clave not in por_url:
                por_url[clave] = {
                    "titulo": r.get("titulo", ""),
                    "url": url,
                    "snippet": r.get("snippet", ""),
                    "fuentes": [],
                    "score": 0.0,
                }
            e = por_url[clave]
            e["score"] += 1.0 / (r.get("posicion", 10) + 2)
            if m.get("motor") not in e["fuentes"]:
                e["fuentes"].append(m.get("motor"))
            dominio = urllib.parse.urlsplit(url).netloc
            if any(d in dominio for d in ("cuitonline", "dateas", "afip", "arca")):
                e["score"] += 0.5
    ordenados = sorted(por_url.values(), key=lambda e: (-e["score"], e["url"]))
    return ordenados[:top]


# ---------------------------------------------------------------------------
# Parsers por motor (HTML -> resultados)
# ---------------------------------------------------------------------------

def extraer_resultados_bing(html: str) -> list:
    """Resultados de Bing: <li class="b_algo"> -> h2 > a[href], p. Verificado
    con HTML real capturado el 2026-08-12 (esta IP devuelve resultados de
    Bing, aunque con calidad degradada)."""
    resultados = []
    for bloque in re.findall(r'<li class="b_algo".*?</li>', html, re.S):
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', bloque, re.S)
        if not m:
            continue
        url = html_mod.unescape(m.group(1))
        titulo = _texto(m.group(2))
        p = re.search(r'<p[^>]*>(.*?)</p>', bloque, re.S)
        cite = re.search(r'<cite[^>]*>(.*?)</cite>', bloque, re.S)
        if not p and cite:
            p = cite
        snippet = _texto(p.group(1)) if p else ""
        url_real = normalizar_url(url) or _texto(cite.group(1)) if cite else normalizar_url(url)
        resultados.append({
            "posicion": len(resultados) + 1,
            "titulo": titulo,
            "url": url_real or url,
            "snippet": snippet,
        })
    return resultados


def extraer_resultados_google(html: str) -> list:
    """Resultados de Google: estructura estandar <div id="search"> con
    enlaces <a href> que envuelven <h3> y snippets en div.VwiC3b.
    NO VERIFICADO EN VIVO en esta IP (siempre pagina "sorry"): el parser se
    valida contra un fixture sintetico y queda marcado como no verificado
    hasta confirmarlo en una red sin bloqueo."""
    resultados = []
    zona = html
    m = re.search(r'<div id="search".*', html, re.S)
    if m:
        zona = m.group(0)
    for bloque in re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>', zona, re.S):
        url, titulo = bloque
        if "google.com/" in url and "/url?" not in url:
            continue
        resultados.append({
            "posicion": len(resultados) + 1,
            "titulo": _texto(titulo),
            "url": html_mod.unescape(url),
            "snippet": "",
        })
    # snippets de los primeros resultados (heuristica sobre los bloques g)
    bloques_g = re.findall(r'<div class="g".*?(?=<div class="g"|</div>\s*</div>\s*</div>\s*</body>)', zona, re.S)
    for i, bg in enumerate(bloques_g[:len(resultados)]):
        sm = re.search(r'<div class="VwiC3b[^"]*"[^>]*>(.*?)</div>', bg, re.S)
        if sm:
            resultados[i]["snippet"] = _texto(sm.group(1))
    return resultados


def extraer_resultados_ddg(html: str) -> list:
    """Resultados de DuckDuckGo (version html): div.result con a.result__a y
    a.result__snippet. NO VERIFICADO EN VIVO en esta IP (challenge "select
    all squares"): validado contra fixture sintetico."""
    resultados = []
    for bloque in re.findall(r'<div class="result[^"]*"[^>]*>.*?</div>\s*</div>', html, re.S):
        m = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', bloque, re.S)
        if not m:
            continue
        url = html_mod.unescape(m.group(1))
        titulo = _texto(m.group(2))
        s = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', bloque, re.S)
        snippet = _texto(s.group(1)) if s else ""
        resultados.append({
            "posicion": len(resultados) + 1,
            "titulo": titulo,
            "url": url,
            "snippet": snippet,
        })
    return resultados


def extraer_resultados_cuitonline(html: str) -> list:
    """Resultados de CuitOnline (busqueda de razon social): div#searchResults
    -> div.hit con a.denominacion (href detalle/) y span.cuit (XX-XXXXXXXX-X).
    Verificado con HTML real (busquedas "ypf" y "permanencia salud",
    2026-08-12)."""
    resultados = []
    zona = html
    m = re.search(r'id="searchResults".*', html, re.S)
    if m:
        zona = m.group(0)
    for bloque in re.findall(r'<div class="hit".*?(?=<div class="hit"|$)', zona, re.S):
        a = re.search(r'<a[^>]*href="(detalle/[^"]+)"[^>]*class="denominacion"[^>]*>(.*?)</a>', bloque, re.S)
        if not a:
            continue
        href, nombre = a.group(1), _texto(a.group(2))
        cuit_m = re.search(r'<span class="cuit">([^<]+)</span>', bloque)
        url = "https://www.cuitonline.com/" + href if not href.startswith("http") else href
        resultados.append({
            "posicion": len(resultados) + 1,
            "titulo": nombre,
            "url": url,
            "snippet": f"CUIT/CUIL: {cuit_m.group(1).strip()}" if cuit_m else "",
            "cuit": cuit_m.group(1).strip() if cuit_m else "",
        })
    return resultados


def extraer_resultados_por_motor(motor: str, html: str) -> list:
    """Despacha al parser del motor; None si no hay parser implementado."""
    if motor == "bing":
        return extraer_resultados_bing(html)
    if motor == "google":
        return extraer_resultados_google(html)
    if motor == "ddg":
        return extraer_resultados_ddg(html)
    if motor == "cuitonline":
        return extraer_resultados_cuitonline(html)
    return []


# ---------------------------------------------------------------------------
# Deteccion de bloqueos y captchas (HTML)
# ---------------------------------------------------------------------------

def detectar_bloqueo(motor: str, html: str) -> str:
    """Estado de bloqueo del motor sobre su HTML: '' si no hay bloqueo,
    o el nombre del bloqueo detectado (BLOQUEOS). El orden importa."""
    for nombre, patron in BLOQUEOS.get(motor, []):
        if patron.search(html):
            return nombre
    return ""


def detectar_recaptcha(html: str) -> bool:
    """True si la pagina incluye un iframe de reCAPTCHA v2 (anchor), que es
    el caso del captcha del stack (Google 'sorry' incluido, verificado en
    vivo 2026-08-12: <iframe src=.../recaptcha/enterprise/anchor...>)."""
    return bool(re.search(r'iframe[^>]*src="[^"]*recaptcha[^"]*/anchor[^"]*"', html))


# ---------------------------------------------------------------------------
# Orquestacion con Playwright (import perezoso)
# ---------------------------------------------------------------------------

def _hay_iframe_recaptcha(pagina) -> bool:
    """True si la pagina tiene el iframe ancla de reCAPTCHA v2 (mismo
    criterio que captcha_web.resolver_web: URL con 'recaptcha' y '/anchor')."""
    try:
        for f in pagina.frames:
            url = f.url or ""
            if "recaptcha" in url and "/anchor" in url:
                return True
    except Exception:
        pass
    return False


def resolver_recaptcha_en_pagina(pagina, max_intentos: int = 2,
                                 timeout_s: float = 150.0) -> dict:
    """Resuelve el reCAPTCHA v2 de la pagina actual EN LA MISMA sesion
    (las cookies del navegador quedan validadas para la busqueda).

    Reusa el stack completo de captcha_web (funciones importadas, no
    duplicadas): clic del ancla -> bframe con reto de tiles -> RT-DETR
    (subproceso del venv) -> clics JS -> VERIFY -> veredicto. El reto de la
    pagina 'sorry' de Google es reCAPTCHA v2 estandar (verificado en vivo:
    3x3, "Select all images with a bus")."""
    from captcha_web import (UMBRAL_RESTO, SELEC_CHECKBOX,
                             capturar_cuadricula, detectar_cuadricula_worker,
                             leer_instruccion, pulsar_skip, pulsar_tiles,
                             pulsar_verificar, tamano_cuadricula,
                             umbral_objetivo_para, veredicto)
    from captcha_ia import aumentar_escala, celdas_grid, resolver

    registro = []
    for intento in range(1, max_intentos + 1):
        ancla = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            for f in pagina.frames:
                url = f.url or ""
                if "recaptcha" in url and "/anchor" in url:
                    ancla = f
                    break
            if ancla:
                break
            time.sleep(0.5)
        if ancla is None:
            return {"ok": False, "error": "sin iframe ancla de reCAPTCHA",
                    "intentos": intento - 1}
        try:
            ancla.locator(SELEC_CHECKBOX).click(timeout=8000)
        except Exception as exc:
            return {"ok": False, "error": f"clic del ancla fallido: {exc}",
                    "intentos": intento}
        # el bframe (reto de tiles) puede tardar en montarse
        bframe = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            for f in pagina.frames:
                if "bframe" in (f.url or ""):
                    bframe = f
                    break
            if bframe:
                break
            time.sleep(0.5)
        if bframe is None:
            # sin bframe: el clic del ancla fue suficiente; veredicto directo
            resultado = veredicto(pagina, ancla)
            registro.append({"intento": intento, "veredicto": resultado,
                             "reto_tiles": False})
            if resultado == "ok":
                return {"ok": True, "veredicto": "ok", "intentos": intento,
                        "reto_tiles": False}
            continue
        # reto de tiles: mismo flujo que captcha_web.resolver_web
        instruccion = leer_instruccion(bframe)
        n = tamano_cuadricula(bframe)
        if n is None:
            continue
        try:
            imagen = capturar_cuadricula(bframe)
        except Exception:
            continue
        celdas_pil = [(f, c, aumentar_escala(celda))
                      for f, c, celda in celdas_grid(imagen, n=n)]
        detecciones = detectar_cuadricula_worker(imagen, n)
        es_variante_none = "skip" in instruccion.lower()
        if not any(detecciones.values()) and not es_variante_none:
            continue  # worker fallo o re-render: reintentar
        umbral = umbral_objetivo_para(n)

        def detectar_celda(_celda, fila, col):
            return detecciones.get((fila, col), [])

        res = resolver(imagen, instruccion, detectar_celda, n=n,
                       umbral_objetivo=umbral, umbral_resto=UMBRAL_RESTO)
        if not res["ok"] or (es_variante_none and not res["seleccion"]):
            pulsar_skip(bframe)
        else:
            pulsar_tiles(bframe, res["seleccion"], n)
            pulsar_verificar(bframe)
        resultado = veredicto(pagina, bframe)
        registro.append({"intento": intento, "veredicto": resultado,
                         "reto_tiles": True,
                         "instruccion": instruccion.replace("\n", " "),
                         "seleccion": sorted(res.get("seleccion", []))})
        if resultado == "ok":
            return {"ok": True, "veredicto": "ok", "intentos": intento,
                    "reto_tiles": True, "registro": registro}
    return {"ok": False, "error": "reto no resuelto", "intentos": max_intentos,
            "registro": registro}


def buscar_en_web(consulta: str, motores: list = None, captcha: bool = False,
                  headed: bool = False, timeout_motor: float = 60.0,
                  salida_dir: str = "", max_intentos_captcha: int = 2,
                  locale: str = "es-AR") -> list:
    """Busca la consulta en cada motor con un navegador Playwright (import
    perezoso: solo python del sistema).

    Por motor devuelve {"motor", "url", "estado" (ok|captcha|bloqueado|
    error), "bloqueo", "resultados", "parser_verificado", "tiempo_s"}.
    - "captcha": se detecto reCAPTCHA v2; si captcha=True se intenta resolver
      en la misma sesion y se re-navega (hasta max_intentos_captcha veces);
      si sigue el captcha o captcha=False, el estado queda "captcha".
    - "bloqueado": bloqueo no resoluble por el stack (slider, turnstile,
      challenge DDG, 403, suspension) o "sin_resultados" del propio motor.
    - La sesion es compartida: resolver un captcha de un motor no rearma el
      de los demas (cookies por dominio)."""
    from playwright.sync_api import sync_playwright

    if motores is None:
        motores = list(MOTORES.keys())
    motores = [m for m in motores if m in MOTORES]
    estados = []
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        contexto = navegador.new_context(locale=locale,
                                         viewport={"width": 1280,
                                                   "height": 900})
        pagina = contexto.new_page()
        try:
            for motor in motores:
                info = MOTORES[motor]
                url = info["url"].format(q=urllib.parse.quote(consulta))
                t0 = time.monotonic()
                estado = {"motor": motor, "url": url, "estado": "",
                          "bloqueo": "", "resultados": [],
                          "parser_verificado": info["parser_verificado"],
                          "tiempo_s": 0.0}
                try:
                    pagina.goto(url, timeout=30000,
                                wait_until="domcontentloaded")
                    pagina.wait_for_timeout(2500)
                    html = pagina.content()
                    if _hay_iframe_recaptcha(pagina):
                        if captcha:
                            res = resolver_recaptcha_en_pagina(
                                pagina, max_intentos=max_intentos_captcha)
                            estado["captcha_intentos"] = res
                            if res.get("ok"):
                                pagina.goto(url, timeout=30000,
                                            wait_until="domcontentloaded")
                                pagina.wait_for_timeout(2500)
                                html = pagina.content()
                                estado["estado"] = "resuelto"
                            else:
                                estado["estado"] = "captcha"
                        else:
                            estado["estado"] = "captcha"
                    if not estado["estado"]:
                        bloqueo = detectar_bloqueo(motor, html)
                        if bloqueo:
                            estado["estado"] = "bloqueado"
                            estado["bloqueo"] = bloqueo
                        else:
                            estado["estado"] = "ok"
                    if info["parser"] and estado["estado"] == "ok":
                        estado["resultados"] = extraer_resultados_por_motor(
                            motor, html)
                        if motor == "google":
                            estado["parser_verificado"] = False
                    elif estado["estado"] == "resuelto" and info["parser"]:
                        estado["resultados"] = extraer_resultados_por_motor(
                            motor, html)
                    if salida_dir:
                        os.makedirs(salida_dir, exist_ok=True)
                        try:
                            with open(os.path.join(
                                    salida_dir, f"{motor}.html"), "w",
                                    encoding="utf-8") as f:
                                f.write(html)
                        except OSError:
                            pass
                except Exception as exc:
                    estado["estado"] = "error"
                    estado["bloqueo"] = f"{type(exc).__name__}: {str(exc)[:100]}"
                estado["tiempo_s"] = round(time.monotonic() - t0, 1)
                estados.append(estado)
        finally:
            navegador.close()
    return estados


def buscar_recetas(consulta: str, recetas: list = None, headed: bool = False,
                   timeout_receta: float = 60.0, salida_dir: str = "",
                   locale: str = "es-AR") -> list:
    """Ejecuta las recetas de dominio (por ahora "cuit"): cuitonline
    (search/{q}) y dateas (consulta_cuit?q=). Devuelve estados por fuente
    con estructura {"receta", "fuente", "url", "estado", "resultados"}."""
    from playwright.sync_api import sync_playwright

    if recetas is None:
        recetas = ["cuit"]
    fuentes = []
    for r in recetas:
        if r in RECETAS:
            fuentes += RECETAS[r]["fuentes"]
    estados = []
    urls = {
        "cuitonline": "https://www.cuitonline.com/search/{q}",
        "dateas": "https://www.dateas.com/es/consulta_cuit?q={q}",
    }
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        contexto = navegador.new_context(locale=locale,
                                         viewport={"width": 1280,
                                                   "height": 900})
        pagina = contexto.new_page()
        try:
            for fuente in fuentes:
                if fuente not in urls:
                    continue
                url = urls[fuente].format(q=urllib.parse.quote(consulta))
                t0 = time.monotonic()
                estado = {"receta": "cuit", "fuente": fuente, "url": url,
                          "estado": "", "resultados": [], "tiempo_s": 0.0}
                try:
                    pagina.goto(url, timeout=45000,
                                wait_until="domcontentloaded")
                    pagina.wait_for_timeout(3000)
                    html = pagina.content()
                    if salida_dir:
                        os.makedirs(salida_dir, exist_ok=True)
                        try:
                            with open(os.path.join(
                                    salida_dir, f"receta_{fuente}.html"),
                                    "w", encoding="utf-8") as f:
                                f.write(html)
                        except OSError:
                            pass
                    if detectar_bloqueo(fuente, html) == "pagina_no_encontrada":
                        estado["estado"] = "bloqueado"
                        estado["bloqueo"] = "pagina_no_encontrada"
                    elif detectar_bloqueo(fuente, html) == "sin_resultados":
                        estado["estado"] = "ok"
                        estado["bloqueo"] = "sin_resultados"
                    else:
                        estado["estado"] = "ok"
                    if fuente == "cuitonline":
                        estado["resultados"] = extraer_resultados_cuitonline(html)
                except Exception as exc:
                    estado["estado"] = "error"
                    estado["bloqueo"] = f"{type(exc).__name__}: {str(exc)[:100]}"
                estado["tiempo_s"] = round(time.monotonic() - t0, 1)
                estados.append(estado)
        finally:
            navegador.close()
    return estados


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resumen_consola(consulta: str, estados_motores: list,
                     estados_recetas: list) -> None:
    print(f"== Consulta: {consulta}")
    print("\n== Motores")
    for m in estados_motores:
        linea = f"  [{m['motor']:<10}] {m['estado']:<10}"
        if m.get("bloqueo"):
            linea += f" ({m['bloqueo']})"
        linea += f" {m['tiempo_s']}s"
        if m.get("captcha_intentos"):
            linea += f" captcha={m['captcha_intentos'].get('veredicto', 'no')}"
        print(linea)
        if m["estado"] in ("ok", "resuelto"):
            for r in m["resultados"][:5]:
                print(f"      {r['posicion']}. {r['titulo'][:60]}")
                print(f"         {r['url'][:80]}")
    print("\n== Recetas")
    for r in estados_recetas:
        print(f"  [{r['fuente']:<12}] {r['estado']:<10} {r['tiempo_s']}s"
              + (f" ({r['bloqueo']})" if r.get("bloqueo") else ""))
        for res in r["resultados"]:
            print(f"      {res['titulo'][:60]} — {res.get('cuit', res['url'][:50])}")
    sintesis = armar_sintesis(estados_motores)
    if sintesis:
        print("\n== Mejores candidatos (ranking multi-motor)")
        for i, e in enumerate(sintesis, 1):
            print(f"  {i}. [{','.join(e['fuentes'])}] {e['titulo'][:60]}")
            print(f"     {e['url'][:90]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Buscador avanzado multi-motor (Playwright) con "
                    "deteccion/resolucion de captchas y recetas de dominio")
    parser.add_argument("consulta", help="texto a buscar")
    parser.add_argument("--motores", default="",
                        help="lista separada por comas (google,bing,...); "
                             "default: todos los soportados")
    parser.add_argument("--recetas", default="",
                        help="recetas de dominio separadas por comas (cuit)")
    parser.add_argument("--captcha", action="store_true",
                        help="resolver reCAPTCHA v2 con el stack local "
                             "(captcha_web: RT-DETR + clics, misma sesion); "
                             "sin este flag el captcha solo se reporta")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar HTML crudo de cada "
                             "motor/receta y el JSON final")
    parser.add_argument("--timeout-motor", type=float, default=60.0,
                        help="tiempo maximo por motor en segundos")
    parser.add_argument("--max-intentos-captcha", type=int, default=2,
                        help="reintentos de resolucion de captcha por motor")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador (es-AR, en-US...)")
    args = parser.parse_args()

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None
    recetas = [r.strip() for r in args.recetas.split(",") if r.strip()] or None

    estados_motores = buscar_en_web(
        args.consulta, motores=motores, captcha=args.captcha,
        headed=args.headed, timeout_motor=args.timeout_motor,
        salida_dir=args.salida,
        max_intentos_captcha=args.max_intentos_captcha, locale=args.locale)
    estados_recetas = buscar_recetas(args.consulta, recetas=recetas,
                                     headed=args.headed,
                                     salida_dir=args.salida,
                                     locale=args.locale)

    informe = {
        "consulta": args.consulta,
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "motores": estados_motores,
        "recetas": estados_recetas,
        "sintesis": armar_sintesis(estados_motores),
    }
    if args.salida:
        os.makedirs(args.salida, exist_ok=True)
        with open(os.path.join(args.salida, "resultado.json"), "w",
                  encoding="utf-8") as f:
            json.dump(informe, f, ensure_ascii=False, indent=2)
    _resumen_consola(args.consulta, estados_motores, estados_recetas)
    if args.salida:
        print(f"\n[OK] Informe completo: {args.salida}/resultado.json")


if __name__ == "__main__":
    main()
