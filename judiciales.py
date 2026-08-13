#!/usr/bin/env python3
"""Buscador de DEMANDAS JUDICIALES y senales de litigio (Argentina).

Fuentes (exploradas y verificadas en vivo):
  1. Boletin Oficial (boletinoficial.gob.ar): edictos de quiebras,
     concursos, remates, sociedades y declaraciones judiciales, buscables
     por nombre o CUIT. La búsqueda es AJAX con sesion: se navega a la home,
     se escribe en la busqueda rapida y se hace CLIC en el boton (Enter no
     dispara el JS). Parser VERIFICADO contra HTML real capturado el
     2026-08-13 (39 resultados para 'asistencia del sol'; estructura
     div.linea-aviso con p.item, p.item-detalle y h5.seccion-rubro).
  2. Dorks web multi-motor (buscador.py; parser de Bing verificado en esta
     IP): "X" juicio / fallo / demanda / sentencia / expediente / juzgado /
     CNAT, para empresas Y personas.

LIMITACIONES HONESTAS (P1.6, verificadas):
  - CNAT, SECLO y juzgados NO son buscables publicamente por nombre de
    parte: no existe base publica de demandas por razon social en Argentina.
  - PJN ConsultaExpedientes (que si consulta por partes) es una SPA Angular
    ofuscada sin API publica; IUS no responde; CuitOnline ya no publica
    "juicios" (hoy solo redirige a Deudas BCRA con clave fiscal).
  - La ausencia en estas fuentes NO prueba que no existan demandas: el
    chequeo real es el informe de antecedentes judiciales (CNAT/SECLO) o el
    certificado de reincidencia con el CUIT/DNI.
  - El BO indexa por PALABRA: 'asistencia del sol' devuelve 39 avisos que
    contienen 'asistencia' como palabra comun (no la empresa): el filtro de
    interes usa la frase completa o 2+ palabras significativas.

Uso:
    python3 judiciales.py "Asistencia del Sol" --salida /var/tmp/jud
    python3 judiciales.py "Perez Juan" --cuit 20-30111222-3 --motores bing
"""

import argparse
import json
import os
import re
import time
import unicodedata
import urllib.parse

from empresas import extraer_cuits_de_html, limpiar_sufijo_legal

__all__ = [
    "variantes_de_nombre", "armar_dorks_judiciales",
    "extraer_resultados_boletin_oficial", "buscar_boletin_oficial",
    "buscar_demandas", "buscar_judicial",
]

# ---------------------------------------------------------------------------
# Piezas puras
# ---------------------------------------------------------------------------

# Frases que delatan que el resultado es un edicto judicial (quiebra,
# concurso, remate, citacion) frente a otras publicaciones.
RE_HALLAZGO_LITIGIO = re.compile(
    r"quiebra|(?<!sin )concurso\b|remate|falencia|concursado|sindicatura|"
    r"declar[ae] judicial|c[íi]tase|citaci[óo]n|embargo|subasta|"
    r"apertura de concurso|verificaci[óo]n de cr[ée]ditos", re.I)


def variantes_de_nombre(nombre: str) -> list:
    """Variantes de busqueda: original y sin sufijo legal (empresa), o el
    nombre como se escribe (persona). Sin duplicados."""
    original = re.sub(r"\s+", " ", nombre.strip())
    sin = limpiar_sufijo_legal(original)
    variantes = []
    for v in (original, sin):
        if v and v not in variantes:
            variantes.append(v)
    return variantes


def armar_dorks_judiciales(nombre: str) -> list:
    """Dorks de litigio sobre el nombre (empresa o persona). El nombre se
    cita entre comillas para evitar ruido de palabras comunes."""
    base = f'"{nombre}"'
    return [
        f"{base} juicio",
        f"{base} fallo",
        f"{base} demanda",
        f"{base} sentencia",
        f'{base} expediente OR causa OR juzgado',
        f"{base} CNAT",
    ]


def extraer_resultados_boletin_oficial(html: str) -> list:
    """Resultados del Boletin Oficial desde el HTML de la busqueda rapida.
    VERIFICADO contra HTML real capturado el 2026-08-13 (busqueda
    'asistencia del sol': 39 resultados, estructura div.linea-aviso con
    p.item = denominacion, p.item-detalle > small = detalle, h5.seccion-
    rubro = rubro/seccion, a[href=/detalleAviso/...] = enlace)."""
    resultados = []
    zona = html
    m = re.search(r'<div id="subLayouyContentDiv".*', html, re.S)
    if m:
        zona = m.group(0)
    # seccion actual (el ultimo h5.seccion-rubro antes de cada aviso)
    seccion = ""
    partes = re.split(r'(<h5 class="seccion-rubro[^"]*"[^>]*>.*?</h5>)',
                      zona, flags=re.S)
    for parte in partes:
        hs = re.search(r'<h5 class="seccion-rubro[^"]*"[^>]*>(.*?)</h5>',
                       parte, re.S)
        if hs:
            seccion = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", hs.group(1))).strip()
            continue
        # cada aviso es <a href="/detalleAviso/..."><div class="linea-aviso">...
        # el enlace ENVUELVE al bloque (verificado en HTML real 2026-08-13)
        for m_aviso in re.finditer(
                r'<a href="(/detalleAviso/[^"]+)"[^>]*>.*?'
                r'<div class="linea-aviso">.*?(?=<a href="/detalleAviso/|$)',
                parte, re.S):
            bloque = m_aviso.group(0)
            p = re.search(r'<p class="item">(.*?)</p>', bloque, re.S)
            if not p:
                continue
            detalles = re.findall(r'<p class="item-detalle">(.*?)</p>',
                                  bloque, re.S)
            snippet = " ".join(
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", d)).strip()
                for d in detalles)
            fecha = re.search(r"(?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/20\d{2}",
                              bloque)
            titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.group(1))).strip()
            titulo = titulo.replace("&nbsp;", " ")
            snippet = snippet.replace("&nbsp;", " ")
            texto_bloque = titulo + " " + snippet + " " + seccion
            resultados.append({
                "titulo": titulo,
                "url": "https://www.boletinoficial.gob.ar" + m_aviso.group(1)
                       if m_aviso.group(1) else "",
                "fecha": fecha.group(0) if fecha else "",
                "seccion": seccion,
                "snippet": snippet[:300],
                "cuits": sorted(extraer_cuits_de_html(bloque)),
                "litigio": bool(RE_HALLAZGO_LITIGIO.search(texto_bloque)),
                "posicion": len(resultados) + 1,
            })
    return resultados


def _norm(s: str) -> str:
    """Normaliza para comparar: minusculas, sin acentos, espacios unicos."""
    n = unicodedata.normalize("NFD", s or "")
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", n.lower()).strip()


def _es_resultado_interesante(r: dict, nombre: str, cuit: str = "") -> bool:
    """Un resultado del BO es interesante si menciona el CUIT exacto, o la
    FRASE del nombre normalizado aparece completa, o al menos DOS palabras
    significativas del nombre coinciden (una sola palabra comun como
    'asistencia' no alcanza: el BO resalta la palabra buscada en todo el
    listado y genera falsos positivos, verificado 2026-08-13)."""
    texto = _norm(r.get("titulo", "") + " " + r.get("snippet", ""))
    if cuit and cuit.replace("-", "") in texto.replace("-", ""):
        return True
    if not nombre:
        return False
    frases = [_norm(v) for v in (nombre, limpiar_sufijo_legal(nombre))]
    for frase in frases:
        if frase and frase in texto:
            return True
    # palabras significativas: >= 3 letras y no stopwords ('del', 'sol' SI
    # cuenta; 'de', 'la', 'el', 'y' no). Coincidencia por PALABRA completa:
    # 'sol' no debe matchear dentro de 'solucion' (verificado 2026-08-13).
    STOP = {"de", "del", "la", "las", "el", "los", "un", "una", "y", "a",
            "o", "e", "en", "que", "por", "sa", "srl", "sas"}
    pal_nombre = {p for p in _norm(nombre).split()
                  if len(p) >= 3 and p not in STOP}
    if len(pal_nombre) >= 2:
        return sum(1 for p in pal_nombre
                   if re.search(rf"\b{re.escape(p)}\b", texto)) >= 2
    return False


# ---------------------------------------------------------------------------
# Orquestacion con Playwright (import perezoso)
# ---------------------------------------------------------------------------

def buscar_boletin_oficial(consulta: str, headed: bool = False,
                           salida_dir: str = "", reintentos: int = 2,
                           timeout_s: float = 60.0,
                           max_resultados: int = 30) -> dict:
    """Busca en el Boletin Oficial (edictos y publicaciones) con el flujo
    real del navegador: home -> busqueda rapida -> CLIC en el boton (Enter
    no dispara el AJAX) -> espera -> resultados. Devuelve {"ok", ...} con
    los resultados, el estado de verificacion del parser y el HTML crudo
    para depuracion (P0.1)."""
    from playwright.sync_api import sync_playwright
    estado = {"fuente": "boletin_oficial", "consulta": consulta,
              "estado": "", "parser_verificado": False, "resultados": [],
              "tiempo_s": 0.0}
    t0 = time.monotonic()
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        contexto = navegador.new_context(locale="es-AR",
                                         viewport={"width": 1280,
                                                   "height": 900})
        pagina = contexto.new_page()
        try:
            error = ""
            for _ in range(reintentos + 1):
                try:
                    pagina.goto("https://www.boletinoficial.gob.ar/",
                                timeout=45000, wait_until="domcontentloaded")
                    pagina.wait_for_timeout(2500)
                    error = ""
                    break
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:90]}"
                    time.sleep(3)
            if error:
                estado["estado"] = "error"
                estado["bloqueo"] = error
                return estado
            try:
                pagina.fill("#rapidaInput", consulta)
                pagina.click("#busquedaRapidaButton")
            except Exception as exc:
                estado["estado"] = "error"
                estado["bloqueo"] = f"{type(exc).__name__}: {str(exc)[:90]}"
                return estado
            # el AJAX puede tardar; esperamos la carga y un margen extra
            pagina.wait_for_timeout(12000)
            html = pagina.content()
            if salida_dir:
                os.makedirs(salida_dir, exist_ok=True)
                try:
                    with open(os.path.join(salida_dir, "boletin.html"),
                              "w", encoding="utf-8") as f:
                        f.write(html)
                except OSError:
                    pass
            estado["url_resultados"] = pagina.url
            resultados = extraer_resultados_boletin_oficial(html)
            estado["resultados"] = resultados[:max_resultados]
            estado["estado"] = "ok"
            estado["parser_verificado"] = bool(resultados)
        finally:
            navegador.close()
    estado["tiempo_s"] = round(time.monotonic() - t0, 1)
    return estado


def buscar_demandas(nombre: str, motores: list = None, captcha: bool = False,
                    headed: bool = False, salida_dir: str = "",
                    locale: str = "es-AR") -> dict:
    """Dorks de litigio sobre el nombre (empresa o persona) con los motores
    de buscador.py (default Bing, parser verificado en esta IP)."""
    from buscador import buscar_en_web
    if motores is None:
        motores = ["bing"]
    dorks = []
    for consulta in armar_dorks_judiciales(nombre):
        estados = buscar_en_web(consulta, motores=motores, captcha=captcha,
                                headed=headed, salida_dir=salida_dir,
                                locale=locale)
        dorks.append({"consulta": consulta, "motores": estados})
    return {"dorks": dorks}


def buscar_judicial(nombre: str, cuit: str = "", motores: list = None,
                    captcha: bool = False, headed: bool = False,
                    salida_dir: str = "", locale: str = "es-AR",
                    con_bo: bool = True, con_dorks: bool = True) -> dict:
    """Ciclo completo: Boletín Oficial (si con_bo) + dorks web (si
    con_dorks). Cada fuente es independiente: el fallo de una no corta las
    demás. Devuelve el informe con sintesis y limitaciones honestas."""
    informe = {
        "nombre": nombre,
        "cuit": cuit,
        "variantes": variantes_de_nombre(nombre),
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "boletin_oficial": None,
        "demandas_web": None,
        "sintesis": {},
    }
    if con_bo:
        informe["boletin_oficial"] = buscar_boletin_oficial(
            nombre, headed=headed, salida_dir=salida_dir)
        for r in informe["boletin_oficial"].get("resultados", []):
            r["_interesante"] = _es_resultado_interesante(r, nombre, cuit)
    if con_dorks:
        informe["demandas_web"] = buscar_demandas(
            nombre, motores=motores, captcha=captcha, headed=headed,
            salida_dir=salida_dir, locale=locale)
    informe["sintesis"] = _sintetizar(informe, nombre, cuit)
    return informe


def _sintetizar(informe: dict, nombre: str, cuit: str = "") -> dict:
    """Resumen legible: hallazgos positivos de cada fuente y limitaciones
    honestas (la ausencia NO prueba nada)."""
    hallazgos = []
    bo = informe.get("boletin_oficial") or {}
    interesantes = [r for r in bo.get("resultados", []) if r.get("_interesante")]
    if bo.get("estado") == "ok":
        hallazgos.append(
            f"Boletin Oficial: {len(bo.get('resultados', []))} resultados "
            f"({len(interesantes)} mencionan el nombre/CUIT); parser "
            + ("verificado" if bo.get("parser_verificado") else "NO verificado"))
        for r in interesantes[:8]:
            hallazgos.append(f"  BO: [{r.get('fecha', '')}] "
                             f"{r['titulo'][:80]}"
                             + (" [litigio]" if r.get("litigio") else ""))
    elif bo:
        hallazgos.append(f"Boletin Oficial: {bo.get('estado')} "
                         f"({bo.get('bloqueo', '')[:80]})")
    dw = informe.get("demandas_web") or {}
    total_dorks = 0
    for d in dw.get("dorks", []):
        n = sum(len(m.get("resultados", [])) for m in d.get("motores", []))
        total_dorks += n
        if n:
            hallazgos.append(f"  dork '{d['consulta'][:50]}': {n} resultados")
    if dw:
        hallazgos.insert(
            -1 if total_dorks else len(hallazgos),
            f"Dorks web: {total_dorks} resultados en total"
            if total_dorks else "Dorks web: sin resultados (ausencia NO prueba nada)")

    return {
        "hallazgos": hallazgos,
        "fuentes_consultadas": {
            "boletin_oficial": bo.get("estado", "no consultado"),
            "dorks_web": "ok" if dw else "no consultado",
        },
        "limitaciones": [
            "CNAT, SECLO y juzgados no son buscables publicamente por nombre "
            "de parte: no existe base publica de demandas por razon social.",
            "PJN ConsultaExpedientes (SPA Angular sin API) e IUS no son "
            "automatizables; CuitOnline ya no publica 'juicios'.",
            "La ausencia en estas fuentes NO prueba que no existan demandas: "
            "el chequeo real es el informe de antecedentes judiciales "
            "(CNAT/SECLO) o el certificado de reincidencia con CUIT/DNI.",
            "El BO indexa por palabra: los resultados pueden contener la "
            "palabra buscada sin ser la empresa (el filtro de interes exige "
            "la frase completa o 2+ palabras significativas).",
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resumen_consola(informe: dict) -> None:
    print(f"== Busqueda judicial: {informe['nombre']}"
          + (f" (CUIT {informe['cuit']})" if informe.get("cuit") else ""))
    print(f"   Variantes: {', '.join(informe['variantes'])}")
    bo = informe.get("boletin_oficial")
    if bo:
        print("\n== Boletin Oficial")
        print(f"  [{bo.get('estado')}] {bo.get('tiempo_s', 0)}s "
              f"{bo.get('bloqueo', '')}"
              + (" (parser no verificado aun)" if not bo.get("parser_verificado")
                 and bo.get("resultados") else ""))
        for r in bo.get("resultados", [])[:10]:
            marca = " *" if r.get("_interesante") else ""
            print(f"  {r.get('posicion', '')}. [{r.get('fecha', '')}] "
                  f"{r['titulo'][:70]}{marca}")
            if r.get("cuits"):
                print(f"      CUITs: {', '.join(r['cuits'])}")
    dw = informe.get("demandas_web")
    if dw:
        print("\n== Dorks web (juicios)")
        for d in dw["dorks"]:
            n = sum(len(m.get("resultados", [])) for m in d["motores"])
            print(f"  {d['consulta'][:55]:<57} -> {n} resultados")
            for m in d["motores"][:2]:
                if m.get("estado") in ("ok", "resuelto"):
                    for r in m.get("resultados", [])[:2]:
                        print(f"      {r['titulo'][:65]}")
                        print(f"         {r['url'][:80]}")
    sin = informe["sintesis"]
    print("\n== Sintesis")
    for h in sin["hallazgos"]:
        print(f"  {h}")
    for lim in sin["limitaciones"]:
        print(f"  ! {lim}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Buscador de demandas judiciales y senales de litigio "
                    "(Boletin Oficial + dorks web multi-motor)")
    parser.add_argument("nombre",
                        help="razon social o nombre de la persona")
    parser.add_argument("--cuit", default="",
                        help="CUIT/CUIL de la persona o empresa (opcional, "
                             "mejora el filtrado de edictos)")
    parser.add_argument("--motores", default="",
                        help="motores de busqueda web separados por comas "
                             "(default: bing)")
    parser.add_argument("--captcha", action="store_true",
                        help="intentar resolver reCAPTCHA v2 en los motores")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar HTML crudos y el "
                             "informe JSON")
    parser.add_argument("--sin-bo", action="store_true",
                        help="omitir la consulta al Boletin Oficial")
    parser.add_argument("--sin-dorks", action="store_true",
                        help="omitir los dorks web de juicios")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador (es-AR, en-US...)")
    args = parser.parse_args()

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None
    informe = buscar_judicial(
        args.nombre, cuit=args.cuit, motores=motores, captcha=args.captcha,
        headed=args.headed, salida_dir=args.salida, locale=args.locale,
        con_bo=not args.sin_bo, con_dorks=not args.sin_dorks)

    if args.salida:
        os.makedirs(args.salida, exist_ok=True)
        with open(os.path.join(args.salida, "resultado.json"), "w",
                  encoding="utf-8") as f:
            json.dump(informe, f, ensure_ascii=False, indent=2)
    _resumen_consola(informe)
    if args.salida:
        print(f"\n[OK] Informe completo: {args.salida}/resultado.json")


if __name__ == "__main__":
    main()
