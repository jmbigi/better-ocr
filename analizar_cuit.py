#!/usr/bin/env python3
"""Analizador de CUIT/CUIL con inteligencia de ALGORITMO (reglas
deterministas, explicables y con nivel de confianza) + IA generativa
OPT-IN (VLM local, --vision) para el resumen en lenguaje natural.

Que determina (y con QUE evidencia real):
  1. PERSONA FISICA vs EMPRESA: por prefijo del CUIT segun AFIP (20/23/24/27
     = persona fisica; 30/33/34 = persona juridica; el resto no asignado).
     Se CRUZA con la razon social (sufijos legales, mutual, cooperativa) y
     la ficha de CuitOnline (CUIT vs CUIL): las contradicciones se reportan
     (P1.10), no se ocultan.
  2. EDAD ESTIMADA: el DNI que forma parte del CUIT NO permite calcular la
     edad (ninguna fuente publica). Solo se estima la DECADA aproximada de
     EMISION del documento por rango numerico (heuristica imprecisa, con
     advertencia explicita: NO es la edad, P1.10).
  3. TIPO DE EMPRESA: por la razon social (SRL/SA/SAS/SH/SAU/mutual/
     cooperativa/fundacion/asociacion/ONG).
  4. HISTORIA COMERCIAL: ficha de CuitOnline (fecha de inicio, actividad
     principal CLAE, empleados declarados, domicilio) — el parser de la
     ficha de EMPRESA se marca no verificado hasta capturar HTML real
     (la ficha de CUIL esta verificada; el index de CuitOnline hoy devuelve
     pocos resultados, verificado 2026-08-12).
  5. IMPUESTOS Y MONOTRIBUTO: NO existen padrones publicos de AFIP. Senales
     indirectas honestas: CUIT dado de alta vs solo CUIL ANSES (la ficha de
     CuitOnline lo distingue textualmente); deudas BCRA NO automatizables
     (clave fiscal). Se reporta como limitacion, nunca como dato.
  6. EMPLEOS: la ficha muestra empleados declarados (si los publica);
     el padron de empleadores de la Secretaria de Trabajo no fue localizado
     (URLs probadas 404 el 2026-08-12) -> limitacion declarada.
  7. JUICIOS/DEMANDAS: reutiliza judiciales.py (dorks web + Boletin
     Oficial si responde).
  8. RECOMENDADORES DE EMPRESAS: dorks de opiniones/resenas en los motores
     (Bing verificado): 'X' opiniones OR resenas, 'X' google maps, 'X'
     linkedin, 'X' recomendado. Sin APIs pagas (Yelp/Google Places no
     accesibles sin clave).

El perfil final es un sistema de reglas con scores y explicaciones: la IA
de algoritmo manda sobre el VLM (que solo resume lo que ya se verifico).

Uso:
    python3 analizar_cuit.py 20-12345678-9 --salida /var/tmp/perfil
    python3 analizar_cuit.py 30-71234567-8 --vision docbee --salida /var/tmp/perfil
"""

import argparse
import json
import os
import re
import time
import urllib.parse

from empresas import dominios_candidatos, rdap_dominio

__all__ = [
    "clasificar_cuit", "banda_emision_dni", "tipo_por_razon_social",
    "armar_dorks_recomendadores", "extraer_ficha_cuitonline",
    "perfil_por_reglas", "analizar_cuit",
]

# ---------------------------------------------------------------------------
# Reglas puras: clasificacion por algoritmo
# ---------------------------------------------------------------------------

# Prefijos de CUIT segun AFIP (regla del dominio TABLA-EMPRESAS-CUIT-TIPO:
# 20/23/24/25/26/27 = PERSONA FISICA; 30/33/34 = PERSONA JURIDICA).
# Los prefijos 21, 22, 28, 29, 31, 32, 35+ no se asignan hoy: se reportan
# como "otro" sin inventar (P0.2).
PREFIJOS_FISICA = {"20", "23", "24", "25", "26", "27"}
PREFIJOS_JURIDICA = {"30", "33", "34"}

RE_CUIT_VALIDO = re.compile(r"^(\d{2})-(\d{8})-(\d)$")

# Tipos societarios/entes por sufijo o palabra clave de la razon social.
# Case-insensitive: la razon social puede venir en minusculas.
TIPOS_RAZON = [
    (re.compile(r"SAU|S\.?\s*A\.?\s*U\.?", re.I), "S.A.U. (sociedad anonima unipersonal)"),
    (re.compile(r"S\.?\s*A\.?\s*S\.?", re.I), "SAS (sociedad por acciones simplificada)"),
    (re.compile(r"S\.?\s*A\.?\s*$", re.I), "SA (sociedad anonima)"),
    (re.compile(r"S\.?\s*R\.?\s*L\.?", re.I), "SRL (sociedad de responsabilidad limitada)"),
    (re.compile(r"S\.?\s*H\.?", re.I), "SH (sociedad de hecho)"),
    (re.compile(r"mutual", re.I), "Mutual"),
    (re.compile(r"cooperativa", re.I), "Cooperativa"),
    (re.compile(r"fundaci[oó]n", re.I), "Fundacion"),
    (re.compile(r"asociaci[oó]n (?:civil|sin fines|mutual)", re.I), "Asociacion civil"),
    (re.compile(r"consorcio", re.I), "Consorcio"),
]

# Rango numerico del DNI -> DECADA aproximada de EMISION del documento.
# Heuristica publica imprecisa (los rangos se solapan por renovaciones y
# re-emisiones): NUNCA es la edad del titular.
RANGOS_DNI = [
    (0, 1_000_000, "1940s-1950s"),
    (1_000_000, 3_000_000, "1950s-1960s"),
    (3_000_000, 5_000_000, "1960s"),
    (5_000_000, 8_000_000, "1970s"),
    (8_000_000, 12_000_000, "1980s"),
    (12_000_000, 16_000_000, "1990s"),
    (16_000_000, 20_000_000, "2000s"),
    (20_000_000, 30_000_000, "2010s"),
    (30_000_000, 45_000_000, "2020s"),
    (45_000_000, 10**10, "2020s (emisiones recientes)"),
]

ADVERTENCIA_DNI = ("El numero de DNI no permite calcular la EDAD: solo "
                   "estima la DECADA de emision del documento (heuristica "
                   "imprecisa, los rangos se solapan). La edad real solo "
                   "puede confirmarse con el titular.")


def clasificar_cuit(cuit: str) -> dict:
    """Clasifica persona fisica / persona juridica / otro por el prefijo
    del CUIT (regla AFIP). Devuelve tipo, confianza y la razon."""
    m = RE_CUIT_VALIDO.match((cuit or "").strip())
    if not m:
        return {"tipo": "invalido", "confianza": 0.0,
                "razon": "no matchea el formato XX-XXXXXXXX-X"}
    prefijo = m.group(1)
    if prefijo in PREFIJOS_FISICA:
        tipo = "persona_fisica"
    elif prefijo in PREFIJOS_JURIDICA:
        tipo = "persona_juridica"
    else:
        tipo = "otro"
    return {"tipo": tipo, "confianza": 0.95,
            "razon": f"prefijo {prefijo} segun AFIP"
                     if tipo != "otro"
                     else f"prefijo {prefijo} no asignado hoy"}


def banda_emision_dni(cuit: str) -> dict:
    """Decada aproximada de EMISION del DNI contenido en el CUIT (si es
    persona fisica). Con advertencia explicita: NO es la edad (P1.10)."""
    m = RE_CUIT_VALIDO.match((cuit or "").strip())
    if not m or m.group(1) not in PREFIJOS_FISICA:
        return {"aplica": False, "banda": "",
                "advertencia": "solo aplica a CUIT de persona fisica"}
    dni = int(m.group(2))
    for minimo, maximo, banda in RANGOS_DNI:
        if minimo <= dni < maximo:
            return {"aplica": True, "dni": str(dni), "banda": banda,
                    "advertencia": ADVERTENCIA_DNI}
    return {"aplica": True, "dni": str(dni), "banda": "",
            "advertencia": ADVERTENCIA_DNI}


def tipo_por_razon_social(razon: str) -> dict:
    """Tipo de empresa/ente por la razon social. Si no hay sufijo legal,
    no se inventa: {tipo: 'sin_tipo', sugerencia: ...}."""
    r = (razon or "").strip().lower()
    for patron, tipo in TIPOS_RAZON:
        if patron.search(r):
            return {"tipo": tipo, "confianza": 0.9,
                    "razon": f"razon social '{razon}'"}
    if r:
        return {"tipo": "sin_tipo", "confianza": 0.0,
                "razon": "sin sufijo legal en la razon social"}
    return {"tipo": "sin_razon_social", "confianza": 0.0,
            "razon": "no se obtuvo razon social"}


def armar_dorks_recomendadores(nombre: str) -> list:
    """Dorks para localizar RECOMENDADORES/opiniones de la empresa en los
    motores (Bing verificado): opiniones, resenas, mapas, linkedin."""
    base = f'"{nombre}"'
    return [f"{base} opiniones OR rese[ñn]as", f"{base} google maps",
            f"{base} linkedin", f"{base} recomendado"]


# ---------------------------------------------------------------------------
# Ficha de CuitOnline (parser contra HTML real)
# ---------------------------------------------------------------------------

def extraer_ficha_cuitonline(html: str, cuit: str = "") -> dict:
    """Campos de la ficha de detalle de CuitOnline.
    VERIFICADO contra HTML real capturado el 2026-08-12:
      - ficha de PERSONA FISICA (CUIT 27-12345678-9): 'Empleador: No',
        'Impuestos activos: GANANCIAS PERSONAS FISICAS / IVA / APORTES
        SEG.SOCIAL AUTONOMOS / IIBB CONVENIO', 'Provincia: X - Localidad: Y'.
        P0.9: el sexo/genero que publica la ficha NO se extrae.
      - ficha de CUIL sin CUIT (mutual): 'no posee CUIT ... posee CUIL'.
    La estructura de la ficha de EMPRESA (fecha de inicio, actividad CLAE,
    empleados) NO se pudo verificar el mismo dia (el index de CuitOnline
    devolvio pocos resultados y sin fichas de empresa): queda marcada
    parser_verificado: False hasta capturar HTML real (P0.2)."""
    ficha = {
        "parser_verificado": False,
        "razon_social": "",
        "tipo_documento": "",
        "posee_cuit": None,
        "posee_cuil": None,
        "detalle_estado": "",
        "empleador": "",
        "condicion": "",
        "impuestos_activos": [],
        "provincia_localidad": "",
        "fecha_inicio": "",
        "actividad": "",
        "empleados": "",
        "domicilio": "",
    }
    if not html:
        return ficha
    texto = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html,
                   flags=re.S | re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)

    # senales textuales verificadas en la ficha real (caso CUIL):
    # el orden importa: "no posee CUIT" tiene prioridad sobre "posee CUIT"
    if re.search(r"no posee CUIT dado de alta", texto, re.I):
        ficha["posee_cuit"] = False
    elif re.search(r"posee CUIT dado de alta", texto, re.I):
        ficha["posee_cuit"] = True
    if re.search(r"posee n[uú]mero de CUIL dado de alta|s[uí] posee", texto, re.I):
        ficha["posee_cuil"] = True

    # ficha de persona fisica VERIFICADA (CUIT 27-12345678-9, 2026-08-12):
    # "Empleador: No" · "Impuestos activos: ..." · "Provincia: X - Localidad: Y"
    m = re.search(r"Empleador:\s*(S[ií]|No)", texto, re.I)
    if m:
        ficha["empleador"] = "Si" if m.group(1).lower().startswith("s") else "No"
    m = re.search(r"Impuestos activos:\s*([^»|]{5,250})", texto, re.I)
    if m:
        # cada impuesto viene seguido de su fecha de alta [09/2019]
        impuestos = re.findall(
            r"([A-Z][A-Z0-9 .,ÁÉÍÓÚÑ]{3,50}?)\s*\[\d{2}/\d{4}\]",
            m.group(1), re.I)
        impuestos = [i.strip() for i in impuestos if i.strip()][:8]
        ficha["impuestos_activos"] = impuestos
        # condicion tributaria por los impuestos ACTIVOS publicados:
        # IVA + Ganancias + Autonomos => Responsable Inscripto (senal);
        # si la ficha menciona monotributo, esa es la condicion.
        if re.search(r"\bmonotributo\b", texto, re.I):
            ficha["condicion"] = "Monotributo"
        elif any("IVA" in i.upper() or "GANANCIAS" in i.upper()
                 for i in impuestos):
            ficha["condicion"] = "Responsable Inscripto (senal: impuestos activos)"
    m = re.search(r"Provincia:\s*([^-»]{2,50})[-–]\s*Localidad:\s*([^»|;]{2,60})",
                  texto)
    if m:
        ficha["provincia_localidad"] = re.sub(
            r"\s+", " ", f"{m.group(1).strip()} - {m.group(2).strip()}"
            .replace("&nbsp", " ")).strip()

    # la ficha encabeza con la denominacion y el numero (verificado en
    # vivo: el titulo real es 'NOMBRE (CUIL), Localidad - Cuit Online'):
    m = re.match(
        r"^([^()]{3,90}?)\s*\((\d{2}-\d{8}-\d)\)\s*,?.*?[-–]\s*Cuit Online",
        texto)
    if m:
        ficha["razon_social"] = m.group(1).strip()
        ficha["tipo_documento"] = m.group(2)
    # estructura de ficha de EMPRESA (NO verificada: se mantiene False)
    for etiqueta, campo in (("Fecha de inicio", "fecha_inicio"),
                            ("Actividad", "actividad"),
                            ("Empleados", "empleados"),
                            ("Domicilio", "domicilio")):
        m = re.search(etiqueta + r"[:.]?\s*([^;|]{3,80})", texto, re.I)
        if m:
            ficha[campo] = m.group(1).strip().rstrip(".,")
    return ficha


def _misma_org(org: str, razon: str) -> bool:
    """La organizacion titular del dominio coincide con la razon social si
    comparten al menos una palabra significativa (>= 4 letras, sin sufijos
    legales). Evita falsos positivos de nombres genericos cortos."""
    def pal(s):
        return {p for p in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
                if len(p) >= 4 and p not in ("srl", "sa", "sas", "sh")}
    return bool(pal(org) & pal(razon))


def perfil_por_reglas(ficha: dict, clasif: dict, banda: dict,
                      tipo_razon: dict, senales: dict) -> dict:
    """Reglas cruzadas -> perfil con scores, confianza y explicacion."""
    perfil = {"clasificacion": clasif, "banda_dni": banda,
              "tipo_empresa": tipo_razon}
    notas = []

    # 1) persona fisica o juridica (prefijo) vs razon social (contradiccion)
    if clasif["tipo"] == "persona_juridica" and tipo_razon["tipo"] == "sin_tipo" \
            and ficha.get("razon_social"):
        notas.append("CUIT juridico (30/33/34) con razon social sin sufijo "
                     "legal: puede ser ente, mutual o razon comercial.")
    if clasif["tipo"] == "persona_fisica" and tipo_razon["tipo"] != "sin_tipo" \
            and ficha.get("razon_social"):
        notas.append("CONTRADICCION (P1.10): prefijo de persona fisica pero "
                     "razon social con tipo societario; verificar el dato.")

    # 2) actividad/empleo: solo senales honestas
    senales_actividad = []
    if ficha.get("posee_cuit") is False:
        senales_actividad.append("sin CUIT dado de alta en AFIP (solo CUIL "
                                 "ANSES): sin actividad registrada declarada")
    if ficha.get("fecha_inicio"):
        senales_actividad.append(f"fecha de inicio: {ficha['fecha_inicio']}")
    if ficha.get("actividad"):
        senales_actividad.append(f"actividad: {ficha['actividad']}")
    if ficha.get("empleados"):
        senales_actividad.append(f"empleados declarados: {ficha['empleados']}")
    if senales.get("web_oficial"):
        senales_actividad.append("web oficial activa")
    if senales.get("emails"):
        senales_actividad.append(f"correo publicado: {senales['emails'][0]}")
    for s in senales.get("whatsapp", [])[:1]:
        senales_actividad.append(f"WhatsApp {s}")

    # 2b) dominios asociados al nombre (titular del DNS, sin datos
    # personales P0.9): si el titular declarado es la ORGANIZACION con la
    # razon social, es senal de asociacion fuerte; si el dominio existe con
    # otro titular, se reporta la diferencia (P1.10).
    for r in senales.get("dominios", []):
        org = r.get("titular_org", "")
        if org and ficha.get("razon_social") and _misma_org(
                org, ficha.get("razon_social")):
            senales_actividad.append(
                f"dominio {r['dominio']} registrado a nombre de la propia "
                f"organizacion (RDAP)")
        elif r.get("titular_tipo") == "no_publicado":
            senales_actividad.append(
                f"dominio {r['dominio']} registrado; titular no publicado "
                "(handle, politica de NIC.AR)")
        elif r.get("ok"):
            senales_actividad.append(
                f"dominio {r['dominio']} registrado; titular no coincide "
                "con la razon social (verificar asociacion)")

    # 3) riesgo: juicios con hallazgos
    riesgo = []
    hallazgos = senales.get("judiciales", [])
    if hallazgos:
        riesgo.append(f"{len(hallazgos)} resultado(s) judiciales que "
                      "mencionan el nombre/CUIT")
    elif senales.get("dorks_ok"):
        riesgo.append("busqueda de juicios sin resultados (ausencia NO "
                      "prueba nada)")

    # 4) recomendadores
    recom = senales.get("recomendadores", [])
    perfil["recomendadores"] = recom
    if recom:
        senales_actividad.append(f"{len(recom)} menciones de opiniones/"
                                 "resenas en buscadores")

    perfil["senales"] = senales_actividad
    perfil["riesgo"] = riesgo
    perfil["notas"] = notas
    perfil["confianza_global"] = round(
        0.5 + 0.3 * int(clasif["tipo"] not in ("otro", "invalido"))
        + 0.2 * int(bool(ficha.get("razon_social"))), 2)
    return perfil


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------

def buscar_ficha_cuitonline(cuit: str, headed: bool = False,
                            salida_dir: str = "") -> dict:
    """Ficha de CuitOnline por CUIT: search/{cuit} -> primer detalle ->
    ficha. Con reintentos (red intermitente, leccion 20)."""
    from playwright.sync_api import sync_playwright
    estado = {"fuente": "cuitonline_ficha", "cuit": cuit, "estado": "",
              "ficha": None, "tiempo_s": 0.0}
    t0 = time.monotonic()
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not headed)
        contexto = navegador.new_context(locale="es-AR",
                                         viewport={"width": 1280,
                                                   "height": 900})
        pagina = contexto.new_page()
        try:
            error = ""
            for _ in range(3):
                try:
                    pagina.goto(f"https://www.cuitonline.com/search/"
                                f"{urllib.parse.quote(cuit)}",
                                timeout=45000, wait_until="domcontentloaded")
                    pagina.wait_for_timeout(4000)
                    error = ""
                    break
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:90]}"
                    time.sleep(2)
            if error:
                estado["estado"] = "error"
                estado["bloqueo"] = error
                return estado
            html_busqueda = pagina.content()
            m = re.search(r'href="(detalle/[^"]+)"', html_busqueda)
            if not m:
                estado["estado"] = "sin_ficha"
                estado["bloqueo"] = "el CUIT no aparece en CuitOnline"
                return estado
            pagina.goto("https://www.cuitonline.com/" + m.group(1),
                        timeout=45000, wait_until="domcontentloaded")
            pagina.wait_for_timeout(4000)
            html = pagina.content()
            if salida_dir:
                os.makedirs(salida_dir, exist_ok=True)
                try:
                    with open(os.path.join(salida_dir, "ficha_cuitonline.html"),
                              "w", encoding="utf-8") as f:
                        f.write(html)
                except OSError:
                    pass
            ficha = extraer_ficha_cuitonline(html, cuit)
            estado["ficha"] = ficha
            estado["estado"] = "ok"
        finally:
            navegador.close()
    estado["tiempo_s"] = round(time.monotonic() - t0, 1)
    return estado


def analizar_cuit(cuit: str, motores: list = None, captcha: bool = False,
                  headed: bool = False, salida_dir: str = "",
                  locale: str = "es-AR", con_dorks: bool = True,
                  con_recomendadores: bool = True, con_judiciales: bool = True,
                  vision: str = "") -> dict:
    """Ciclo completo de analisis de un CUIT/CUIL. Cada fuente es
    independiente. Devuelve el informe con perfil y limitaciones."""
    if motores is None:
        motores = ["bing"]

    informe = {
        "cuit": cuit,
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clasificacion": clasificar_cuit(cuit),
        "banda_dni": banda_emision_dni(cuit),
        "cuitonline": None,
        "dominio_titular": [],
        "judiciales": None,
        "recomendadores": [],
        "perfil": {},
        "resumen_ia": "",
        "limitaciones": [],
    }

    ficha = {}
    razon = ""
    if clasificar_cuit(cuit)["tipo"] != "invalido":
        res = buscar_ficha_cuitonline(cuit, headed=headed,
                                      salida_dir=salida_dir)
        informe["cuitonline"] = res
        if res.get("ficha"):
            ficha = res["ficha"]
            razon = ficha.get("razon_social", "")

    nombre = razon or cuit
    senales = {"web_oficial": False, "emails": [], "whatsapp": [],
               "judiciales": [], "dorks_ok": False, "recomendadores": [],
               "dominios": []}

    # Titular DNS: si el nombre esta claramente asociado a dominios
    # (candidatos derivados de la razon social), consultar RDAP NIC.AR
    # (sin datos personales del titular, P0.9; verificado: NIC.AR oculta el
    # titular tras un handle numerico en asistenciadelsol.com.ar).
    if razon:
        for candidato in dominios_candidatos(razon):
            r = rdap_dominio(candidato)
            if r.get("ok"):
                r["dominio"] = candidato
                informe.setdefault("dominio_titular", []).append(r)
                senales["dominios"].append(r)

    if con_judiciales:
        from judiciales import buscar_judicial
        jud = buscar_judicial(nombre, cuit=cuit, motores=motores,
                              captcha=captcha, headed=headed,
                              salida_dir=salida_dir, locale=locale,
                              con_bo=False, con_dorks=True)
        informe["judiciales"] = jud
        senales["dorks_ok"] = bool(
            jud.get("demandas_web", {}).get("dorks"))
        hallazgos = [h for h in jud.get("sintesis", {}).get("hallazgos", [])
                     if "dork" in h and "0 resultados" not in h]
        senales["judiciales"] = hallazgos

    if con_recomendadores:
        from buscador import buscar_en_web
        for consulta in armar_dorks_recomendadores(nombre):
            estados = buscar_en_web(consulta, motores=motores,
                                    captcha=captcha, headed=headed,
                                    salida_dir=salida_dir, locale=locale)
            total = sum(len(m.get("resultados", [])) for m in estados)
            informe["recomendadores"].append({
                "consulta": consulta, "motores": estados,
                "total_resultados": total})
            senales["recomendadores"].extend(
                m.get("resultados", [])[:2] for m in estados
                if m.get("resultados"))

    perfil = perfil_por_reglas(ficha, informe["clasificacion"],
                                informe["banda_dni"],
                                tipo_por_razon_social(razon), senales)
    informe["perfil"] = perfil

    informe["limitaciones"] = [
        "La EDAD no es derivable del DNI/CUIT (solo se estima la decada de "
        "emision del documento; heuristica imprecisa).",
        "No existen padrones publicos de AFIP (impuestos, monotributo, "
        "padron de empleadores): las senales de actividad son indirectas "
        "y la ausencia NO prueba nada.",
        "Deudas BCRA requiere clave fiscal (no automatizable); el index de "
        "CuitOnline devolvio pocos resultados el 2026-08-12 (ficha de "
        "empresa pendiente de verificacion del parser).",
        "La ausencia de juicios en buscadores NO prueba que no existan: "
        "chequeo real = informe de antecedentes judiciales (CNAT/SECLO).",
    ]

    if vision:
        informe["resumen_ia"] = _resumen_con_vlm(
            vision, cuit, perfil, informe["limitaciones"])
        informe["resumen_ia_vlm"] = vision
    return informe


def _resumen_con_vlm(vision: str, cuit: str, perfil: dict,
                     limitaciones: list) -> str:
    """Resumen en lenguaje natural del perfil con el VLM local (opt-in).
    El VLM solo redacta sobre los datos YA verificados por las reglas: la
    IA de algoritmo manda (P1.15: nada de lo que diga el modelo se entrega
    sin la capa determinista)."""
    import sys
    sys.path.insert(0, "scripts")
    try:
        if vision == "docbee":
            from bateria_360 import run_docbee
            fn = run_docbee
        else:
            from bateria_360 import run_ollama
            fn = run_ollama
    except Exception as exc:
        return f"VLM no disponible: {type(exc).__name__}: {str(exc)[:80]}"
    prompt = (
        "Resumi en 5 lineas maximo este perfil de analisis de CUIT "
        f"{cuit}. No inventes datos: usa solo lo que esta escrito.\n"
        f"Perfil: {json.dumps(perfil, ensure_ascii=False)}")
    try:
        return fn(prompt, max_pixels=500_000)[:800]
    except Exception as exc:
        return f"VLM fallo: {type(exc).__name__}: {str(exc)[:80]}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resumen_consola(informe: dict) -> None:
    c = informe["clasificacion"]
    print(f"== Analisis de CUIT {informe['cuit']}")
    print(f"   Clasificacion: {c['tipo']} (confianza {c['confianza']}) — {c['razon']}")
    banda = informe["banda_dni"]
    if banda.get("aplica"):
        print(f"   DNI {banda['dni']}: emision estimada {banda['banda'] or 'n/d'}")
        print(f"   ! {banda['advertencia']}")
    co = informe["cuitonline"]
    if co:
        print("\n== CuitOnline")
        if co.get("estado") == "ok" and co.get("ficha"):
            f = co["ficha"]
            print(f"  ficha: {f['razon_social'] or '?'} "
                  f"({f['tipo_documento'] or '?'})")
            print(f"  posee CUIT: {f['posee_cuit']} | posee CUIL: {f['posee_cuil']}")
            if f["fecha_inicio"]:
                print(f"  fecha de inicio: {f['fecha_inicio']}")
            if f["actividad"]:
                print(f"  actividad: {f['actividad']}")
            if f["empleados"]:
                print(f"  empleados declarados: {f['empleados']}")
            print("  (parser de ficha de empresa: "
                  + ("verificado" if f["parser_verificado"]
                     else "NO verificado aun") + ")")
        else:
            print(f"  {co.get('estado')} — {co.get('bloqueo', '')[:80]}")
    print("\n== Titular de dominios asociados (RDAP, sin datos personales)")
    for r in informe["dominio_titular"]:
        t = r.get("titular_tipo", "")
        if t == "persona_juridica" and r.get("titular_org"):
            print(f"  {r['dominio']}: persona juridica — {r['titular_org']}")
        elif t == "no_publicado":
            print(f"  {r['dominio']}: registrado; titular NO publicado "
                  "(handle numerico, politica de NIC.AR)")
        elif t == "publicado_sin_org":
            print(f"  {r['dominio']}: registrado; titular publicado sin org "
                  "(nombre no expuesto, P0.9)")
        else:
            print(f"  {r['dominio']}: registrado (creado {r['creado'][:10]})")
    if not informe["dominio_titular"]:
        print("  sin dominios candidatos registrados")
    jud = informe["judiciales"]
    if jud:
        print("\n== Judiciales (dorks web)")
        for h in jud.get("sintesis", {}).get("hallazgos", [])[:6]:
            print(f"  {h[:110]}")
    print("\n== Recomendadores (dorks de opiniones)")
    for r in informe["recomendadores"]:
        print(f"  {r['consulta'][:55]:<57} -> {r['total_resultados']} resultados")
    print("\n== Perfil (IA de reglas)")
    p = informe["perfil"]
    print(f"  Tipo de empresa: {p['tipo_empresa']['tipo']} "
          f"({p['tipo_empresa']['razon']})")
    print(f"  Confianza global: {p['confianza_global']}")
    for s in p["senales"]:
        print(f"  + {s}")
    for r in p["riesgo"]:
        print(f"  ! {r}")
    for n in p["notas"]:
        print(f"  ? {n}")
    if informe.get("resumen_ia"):
        vlm = informe.get("resumen_ia_vlm", "VLM")
        print(f"\n== Resumen IA ({vlm}):")
        print(f"  {informe['resumen_ia'][:600]}")
    for lim in informe["limitaciones"]:
        print(f"  ! {lim}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analizador de CUIT/CUIL con IA de algoritmo: persona "
                    "fisica/juridica, edad estimada, tipo de empresa, "
                    "historia comercial, juicios y recomendadores")
    parser.add_argument("cuit", help="CUIT/CUIL con formato XX-XXXXXXXX-X")
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
    parser.add_argument("--sin-dorks", action="store_true",
                        help="omitir judiciales y recomendadores (solo perfil)")
    parser.add_argument("--vision", default="",
                        choices=["", "docbee", "ollama"],
                        help="resumen en lenguaje natural con el VLM local "
                             "(opt-in; la capa de reglas manda)")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador (es-AR, en-US...)")
    args = parser.parse_args()

    if not re.match(r"^\d{2}-\d{8}-\d$", args.cuit):
        print(f"[ERROR] CUIT invalido: {args.cuit} (formato XX-XXXXXXXX-X)")
        return

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None
    informe = analizar_cuit(
        args.cuit, motores=motores, captcha=args.captcha, headed=args.headed,
        salida_dir=args.salida, locale=args.locale,
        con_dorks=not args.sin_dorks, con_recomendadores=not args.sin_dorks,
        con_judiciales=not args.sin_dorks, vision=args.vision)

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
