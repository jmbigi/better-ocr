#!/usr/bin/env python3
"""CLI de busqueda de EMPRESAS: razon social, CUIT, senales de actividad,
juicios y CORREOS/canales de contacto, reutilizando el motor multi-motor de
buscador.py (Playwright del python del sistema; piezas puras testeables sin
navegador).

Pasos por empresa (cada uno independiente; el fallo de uno no corta el resto):
  1. CuitOnline con variantes automaticas del nombre (limpieza de sufijos
     legales SRL/SA/SAS/SH) + Dateas (hoy reporta 404, verificado 2026-08-12).
  2. Web oficial si se conoce (--sitio): vigencia, titulo, CUITs, razon
     social declarada en el HTML y CORREOS — el home mas las paginas de
     contacto del mismo sitio (contacto/contact/escribinos...), con
     desofuscacion de 'info [at] dominio [dot] com'. Tambien detecta los
     canales que muchas pymes publican en lugar de correo: WhatsApp
     (wa.me) y redes sociales (Instagram/Facebook/LinkedIn/X).
  3. RDAP de NIC.AR (si --sitio): registrador y fechas del dominio, SIN
     datos personales del titular (P0.9).
  4. Busqueda web general (motores de buscador.py, default Bing) con la
     razon social + dorks de juicios ("X" juicio/fallo/demanda/sentencia).
     LIMITACION HONESTA (P1.6): los expedientes laborales argentinos (CNAT,
     SECLO, juzgados) no son buscables publicamente por razon social; la
     ausencia en buscadores NO prueba que no haya juicios.
  5. Dorks de correos: '"@dominio"' (correos indexados en cualquier pagina)
     y '"razon social" email OR correo OR contacto', con extraccion de los
     correos visibles en los snippets. La ausencia de correo publicado NO
     prueba que no exista: muchas pymes solo publican WhatsApp/formulario.

Uso:
    python3 empresas.py "Permanencia Salud" --sitio permanencia.com.ar
    python3 empresas.py "Asistencia del Sol" --salida /var/tmp/empresas
"""

import argparse
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

__all__ = [
    "variantes_de_nombre", "limpiar_sufijo_legal", "extraer_cuits_de_html",
    "extraer_razon_social_de_html", "extraer_emails_de_html",
    "extraer_whatsapp_de_html", "extraer_redes_de_html",
    "dominios_candidatos", "parsear_rdap", "rdap_dominio", "buscar_empresa",
]

# ---------------------------------------------------------------------------
# Piezas puras
# ---------------------------------------------------------------------------

# Sufijos legales ANCLADOS al final del nombre (la alternativa SAS antes que
# SA para que "SAS" no se consuma parcialmente; seguidos de separadores y
# fin de cadena). Sin el ancla, "Sa Salud Srl" perdia el "Sa" inicial.
SUFIJOS_LEGALES = re.compile(
    r"(s\.?\s*r\.?\s*l\.?|s\.?\s*a\.?\s*s\.?|s\.?\s*a\.?|s\.?\s*h\.?|"
    r"sociedad de responsabilidad limitada|sociedad anonima)\s*\.?\s*$",
    re.IGNORECASE)

# Prefijos validos de CUIT/CUIL/CUCDI de personas y empresas (AFIP).
RE_CUIT = re.compile(r"\b(?:20|23|24|27|30|33|34)-\d{8}-\d\b")

RE_PIE_COPYRIGHT = re.compile(
    r"©\s*(?:20\d{2}(?:\s*[-–]\s*20\d{2})?)\s*:?\s*([^<|•\n]{3,80})",
    re.IGNORECASE)

# Frases comunes de cierre del pie de pagina que no son parte de la razon
# social ("Permanencia Salud Srl. Todos los derechos reservados.").
RE_CIERRE_PIE = re.compile(
    r"\s+(?:todos los derechos|copyright|all rights|derechos reservados)"
    r"[\s\S]*$", re.IGNORECASE)

# Correos: forma estandar (mailto: y texto plano).
RE_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")

RE_MAILTO = re.compile(r'mailto:([^"\'\s?]+)', re.I)

# Ofuscacion comun contra bots: info [at] dominio [dot] com / [arroba][punto].
RE_EMAIL_OFUSCADO = re.compile(
    r"\b([\w.+-]+)\s*[\[\(](?:at|arroba|@)[\]\)]\s*"
    r"((?:[\w-]+\s*[\[\(](?:dot|punto)[\]\)]\s*)+[\w-]+)\b", re.I)

# WhatsApp como canal comercial (wa.me/numero, whatsapp.com/send?phone=).
RE_WHATSAPP = re.compile(
    r"(?:wa\.me/|(?:api\.)?whatsapp\.com/send\?phone=)(\d{8,15})", re.I)

# Perfiles de redes sociales publicados por el sitio (sin datos personales
# de terceros: solo los canales que la propia organizacion declara).
RE_REDES = re.compile(
    r"https?://(?:www\.)?("
    r"instagram\.com/[A-Za-z0-9_.]+"
    r"|facebook\.com/(?:profile\.php\?id=\d+|[A-Za-z0-9_.\-]+)"
    r"|(?:ar\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_.\-]+"
    r"|x\.com/[A-Za-z0-9_]+"
    r")", re.I)

# Enlaces a paginas de contacto (por href o por texto del enlace).
RE_LINK_CONTACTO = re.compile(
    r"contacto|contactanos|contactenos|contact us|escrib[ií]nos|consultas",
    re.I)


def limpiar_sufijo_legal(nombre: str) -> str:
    """Quita el sufijo legal del FINAL del nombre ('Permanencia Salud Srl'
    -> 'Permanencia Salud'). Devuelve el nombre limpio; si no hay sufijo,
    devuelve el nombre original."""
    n = re.sub(r"\s+", " ", nombre.strip())
    sin = SUFIJOS_LEGALES.sub("", n).strip()
    return sin or n


def variantes_de_nombre(nombre: str) -> list:
    """Variantes de busqueda ordenadas: nombre original y nombre sin sufijo
    legal, sin duplicados. CuitOnline indexa por el nombre como se declaro
    en AFIP, asi que probar ambas cubre el caso del nombre legal distinto al
    comercial (verificado: 'Permanencia Salud SRL' y 'Permanencia Salud'
    dan el mismo resultado de busqueda)."""
    original = re.sub(r"\s+", " ", nombre.strip())
    sin = limpiar_sufijo_legal(original)
    variantes = []
    for v in (original, sin):
        if v and v not in variantes:
            variantes.append(v)
    return variantes


def dominios_candidatos(nombre: str) -> list:
    """Dominios candidatos derivados del NOMBRE comercial: sin sufijo
    legal, sin acentos ni espacios, + TLDs comunes de Argentina (.com.ar,
    .com). Son SOLO candidatos: la existencia real y el titular se
    verifican despues con RDAP (P0.2: un dominio que existe con titular
    distinto se reporta como tal, no como asociado)."""
    base = limpiar_sufijo_legal(nombre)
    base = unicodedata.normalize("NFD", base)
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = re.sub(r"[^a-z0-9]+", "", base.lower())
    if len(base) < 4:
        return []
    return [f"{base}.com.ar", f"{base}.com"]


def extraer_cuits_de_html(html: str) -> set:
    """Todos los CUIT/CUIL (XX-XXXXXXXX-X) presentes en el HTML."""
    return set(RE_CUIT.findall(html or ""))


def extraer_razon_social_de_html(html: str, nombre_buscado: str = "") -> str:
    """Razon social declarada en el pie de pagina ('© 2025 <razon social>'),
    comun en las webs de empresas. Devuelve el texto tras el copyright,
    recortado por las frases de cierre tipicas, o '' si no hay pie."""
    m = RE_PIE_COPYRIGHT.search(html or "")
    if not m:
        return ""
    texto = RE_CIERRE_PIE.sub("", m.group(1))
    return re.sub(r"\s+", " ", texto).strip().rstrip(".,")


def extraer_emails_de_html(html: str) -> set:
    """Correos publicados en el HTML: enlaces mailto:, texto plano y formas
    ofuscadas 'info [at] dominio [dot] com'. Los bloques <script>/<style> se
    EXCLUYEN (ahi viven plantillas, analytics y direcciones falsas: P0.2, no
    inventar correos que el sitio no publica). Devuelve un set en minusculas."""
    if not html:
        return set()
    cuerpo = re.sub(r"<script\b[^>]*>.*?</script>", " ", html,
                    flags=re.S | re.I)
    cuerpo = re.sub(r"<style\b[^>]*>.*?</style>", " ", cuerpo,
                    flags=re.S | re.I)
    emails = set()
    for m in RE_MAILTO.finditer(cuerpo):
        destino = urllib.parse.unquote(m.group(1)).split("?")[0].strip()
        if RE_EMAIL.fullmatch(destino):
            emails.add(destino.lower())
    for m in RE_EMAIL.finditer(cuerpo):
        emails.add(m.group(0).lower())
    for m in RE_EMAIL_OFUSCADO.finditer(cuerpo):
        local, dominio = m.group(1), m.group(2)
        dominio = re.sub(r"\s*[\[\(](?:dot|punto)[\]\)]\s*", ".", dominio)
        completo = f"{local}@{dominio}".lower()
        if RE_EMAIL.fullmatch(completo):
            emails.add(completo)
    return emails


def extraer_whatsapp_de_html(html: str) -> set:
    """Numeros de WhatsApp de contacto que el sitio declara (wa.me/... o
    api.whatsapp.com/send?phone=...): el canal que muchas pymes publican en
    lugar de correo. P0.9: solo el canal comercial declarado, nunca numeros
    personales sueltos del HTML."""
    if not html:
        return set()
    return {m.group(1) for m in RE_WHATSAPP.finditer(html)}


def extraer_redes_de_html(html: str) -> list:
    """Perfiles de redes sociales que el sitio declara (Instagram, Facebook,
    LinkedIn, X), como URLs limpias sin query ni fragmento. Solo los canales
    propios de la organizacion (P0.9)."""
    if not html:
        return []
    url_limpia = {}
    for m in RE_REDES.finditer(html):
        parte = m.group(1)
        if parte.lower().startswith("facebook.com/tr"):
            continue  # pixel de tracking de Facebook, no un perfil
        # los parametros UTM solo estorban, pero el ?id= de profile.php
        # ES parte de la identidad del perfil (no se corta)
        if "profile.php" not in parte:
            parte = re.sub(r"[?#].*$", "", parte)
        url = "https://" + parte.rstrip("/")
        url_limpia[url.lower()] = url
    return sorted(url_limpia.values())


def parsear_rdap(datos: dict) -> dict:
    """Campos NO personales del JSON RDAP de NIC.AR: registrador, fechas y
    TIPO de titular. Los contactos (nombre, email, telefono) se EXCLUYEN a
    proposito SIEMPRE (P0.9, aunque el whois los publique).

    El titular puede aparecer como 'org' (persona juridica: nombre de la
    organizacion, dato comercial) o quedar oculto tras un handle numerico
    sin vcard (politica de NIC.AR verificada en vivo el 2026-08-12 con
    asistenciadelsol.com.ar: solo handle 2737...). Si solo hay 'fn', se
    reporta 'publicado_sin_org' SIN exponer el nombre (P0.9)."""
    res = {"registrador": "", "creado": "", "expira": "",
           "titular_tipo": "", "titular_org": ""}
    eventos = {e.get("eventAction", ""): e.get("eventDate", "")
               for e in datos.get("events", [])}
    res["creado"] = eventos.get("registration", "") or eventos.get("created", "")
    res["expira"] = eventos.get("expiration", "")
    for entidad in datos.get("entities", []):
        rol = " ".join(entidad.get("roles", []))
        vcard = entidad.get("vcardArray", [[], []])[1]
        campos = {}
        for item in vcard:
            if item and len(item) > 3:
                campos[item[0]] = item[3]
        if "registrar" in rol.lower():
            res["registrador"] = str(
                campos.get("org") or campos.get("fn")
                or entidad.get("handle", ""))
            continue
        if "registrant" in rol.lower():
            if campos.get("org"):
                res["titular_tipo"] = "persona_juridica"
                res["titular_org"] = str(campos["org"])
            elif campos.get("fn"):
                res["titular_tipo"] = "publicado_sin_org"
            elif entidad.get("handle"):
                res["titular_tipo"] = "no_publicado"
    return res


def rdap_dominio(dominio: str, timeout_s: float = 20.0) -> dict:
    """Consulta RDAP de NIC.AR para el dominio (sin datos personales).
    Devuelve {"ok": bool, ...} con registrador/fechas o el error."""
    dominio = dominio.lower().rstrip(".")
    if not dominio or "." not in dominio:
        return {"ok": False, "error": "dominio invalido"}
    try:
        req = urllib.request.Request(
            f"https://rdap.nic.ar/domain/{urllib.parse.quote(dominio)}",
            headers={"User-Agent": "better-ocr-empresas"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return {"ok": True, **parsear_rdap(json.loads(r.read().decode()))}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:90]}"}


# ---------------------------------------------------------------------------
# Orquestacion (Playwright perezoso + buscador.py)
# ---------------------------------------------------------------------------

def _links_de_contacto(pagina, netloc: str, max_enlaces: int = 3) -> list:
    """Enlaces del sitio que apuntan a paginas de contacto (mismo dominio,
    nunca terceros): por href o por el texto del enlace. Devuelve URLs
    absolutas normalizadas (sin query ni fragmento), sin duplicados."""
    netloc = netloc.lower().replace("www.", "")
    enlaces = set()
    try:
        for a in pagina.locator("a[href]").all():
            try:
                texto = (a.inner_text() or "").strip().lower()[:60]
                href = (a.get_attribute("href") or "").strip()
            except Exception:
                continue
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            if not RE_LINK_CONTACTO.search(texto + " " + href.lower()):
                continue
            try:
                url = urllib.parse.urljoin(pagina.url, href)
                partes = urllib.parse.urlsplit(url)
                if partes.netloc.lower().replace("www.", "") != netloc:
                    continue
                enlaces.add(urllib.parse.urlunsplit(
                    (partes.scheme, partes.netloc, partes.path.rstrip("/"),
                     "", "")))
            except Exception:
                continue
    except Exception:
        pass
    return sorted(enlaces)[:max_enlaces]


def _verificar_sitio(pagina, sitio: str, timeout_s: float = 45.0,
                     reintentos: int = 2) -> dict:
    """Visita la web oficial: vigencia, titulo, CUITs y razon social del
    HTML. Ademas navega a las paginas de contacto del mismo sitio y extrae
    los CORREOS publicados (mailto:, texto plano, ofuscados) y los canales
    alternativos (WhatsApp, redes) que muchas pymes publican en lugar de
    correo. Reintenta la navegacion (la red de esta maquina es intermitente,
    leccion 20: ERR_NETWORK_CHANGED y timeouts transitorios). Devuelve el
    estado con los hallazgos (los HTML crudos se guardan en --salida por el
    llamador: _html = home, _html_contactos = paginas de contacto)."""
    url = sitio if "://" in sitio else "https://" + sitio
    error = ""
    for _ in range(reintentos + 1):
        try:
            pagina.goto(url, timeout=timeout_s * 1000,
                        wait_until="domcontentloaded")
            pagina.wait_for_timeout(2500)
            error = ""
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:90]}"
            time.sleep(2)
    if error:
        return {"ok": False, "error": error}
    try:
        html = pagina.content()
    except Exception:
        return {"ok": False, "error": "no se pudo leer el contenido"}
    titulo = ""
    try:
        titulo = (pagina.title() or "").strip()
    except Exception:
        pass
    netloc = urllib.parse.urlsplit(pagina.url).netloc

    # paginas de contacto del mismo sitio: alli suelen estar los correos
    contactos = []
    html_contactos = []
    try:
        for c_url in _links_de_contacto(pagina, netloc):
            pagina.goto(c_url, timeout=timeout_s * 1000,
                        wait_until="domcontentloaded")
            pagina.wait_for_timeout(1500)
            c_html = pagina.content()
            html_contactos.append(c_html)
            contactos.append({"url": c_url,
                              "emails": sorted(extraer_emails_de_html(c_html))})
    except Exception:
        pass

    todo_html = html + "\n".join(html_contactos)
    return {
        "ok": True,
        "url": pagina.url,
        "titulo": titulo,
        "cuits": sorted(extraer_cuits_de_html(todo_html)),
        "razon_social": extraer_razon_social_de_html(todo_html),
        "emails": sorted(extraer_emails_de_html(todo_html)),
        "whatsapp": sorted(extraer_whatsapp_de_html(todo_html)),
        "redes": extraer_redes_de_html(todo_html),
        "paginas_contacto": contactos,
        "_html": html,
        "_html_contactos": html_contactos,
    }


def _armar_juicios(consulta: str) -> list:
    """Consultas de dorks de juicios sobre la razon social."""
    base = f'"{consulta}"'
    return [f"{base} juicio", f"{base} fallo", f"{base} demanda",
            f"{base} sentencia"]


def _armar_busquedas_contacto(nombre: str, dominio: str = "") -> list:
    """Consultas para localizar CORREOS publicados de la empresa. El dork
    '"@dominio"' encuentra correos que hayan quedado indexados por el
    buscador en CUALQUIER pagina (no solo la web oficial)."""
    consultas = []
    if dominio:
        consultas.append(f'"@{dominio}"')
    consultas.append(f'"{nombre}" email OR correo OR contacto')
    return consultas


def _armar_recomendadores(nombre: str) -> list:
    """Consultas para localizar RECOMENDADORES/opiniones de la empresa en
    los motores (sin APIs pagas: los buscadores indexan resenas, mapas y
    perfiles)."""
    base = f'"{nombre}"'
    return [f"{base} opiniones OR rese[ñn]as", f"{base} google maps"]


def buscar_empresa(nombre: str, sitio: str = "", motores: list = None,
                   captcha: bool = False, headed: bool = False,
                   salida_dir: str = "", locale: str = "es-AR",
                   con_juicios: bool = True, con_correos: bool = True,
                   con_recomendadores: bool = True) -> dict:
    """Ciclo completo de verificacion de una empresa. Devuelve el informe
    con secciones: variantes, cuitonline, dateas, sitio_oficial, rdap,
    web_general, juicios, correos_web, recomendadores, sintesis. Cada paso
    es independiente."""
    from buscador import (buscar_en_web, buscar_recetas,
                          extraer_resultados_cuitonline, detectar_bloqueo)

    if motores is None:
        motores = ["bing"]

    dominio = ""
    if sitio:
        dominio = urllib.parse.urlsplit(
            sitio if "://" in sitio else "https://" + sitio).netloc
        if dominio.startswith("www."):
            dominio = dominio[4:]

    informe = {
        "empresa": nombre,
        "variantes": variantes_de_nombre(nombre),
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cuitonline": [],
        "dateas": None,
        "sitio_oficial": None,
        "rdap": None,
        "rdap_candidatos": [],
        "web_general": [],
        "juicios": [],
        "correos_web": [],
        "recomendadores": [],
        "sintesis": {},
    }

    # 1) CuitOnline con variantes + Dateas (via recetas de buscador.py).
    # La red de esta maquina es intermitente (leccion 20: ERR_NETWORK_CHANGED
    # transitorio): se reintenta cada variante antes de reportar error.
    for variante in informe["variantes"]:
        estados = []
        for _ in range(3):
            estados = buscar_recetas(variante, recetas=["cuit"],
                                     headed=headed, salida_dir=salida_dir,
                                     locale=locale)
            if all(e.get("estado") != "error" for e in estados):
                break
            time.sleep(2)
        for e in estados:
            if e["fuente"] == "cuitonline":
                informe["cuitonline"].append({"consulta": variante, **e})
            elif e["fuente"] == "dateas" and informe["dateas"] is None:
                informe["dateas"] = e

    # 2) Web oficial
    if sitio:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(headless=not headed)
            contexto = navegador.new_context(locale=locale,
                                             viewport={"width": 1280,
                                                       "height": 900})
            pagina = contexto.new_page()
            try:
                sit = _verificar_sitio(pagina, sitio)
                html = sit.pop("_html", "")
                html_contactos = sit.pop("_html_contactos", [])
                if salida_dir:
                    os.makedirs(salida_dir, exist_ok=True)
                    try:
                        with open(os.path.join(salida_dir, "sitio.html"),
                                  "w", encoding="utf-8") as f:
                            f.write(html)
                        for i, c_html in enumerate(html_contactos):
                            with open(os.path.join(
                                    salida_dir, f"sitio_contacto_{i}.html"),
                                    "w", encoding="utf-8") as f:
                                f.write(c_html)
                    except OSError:
                        pass
                informe["sitio_oficial"] = sit
                # 3) RDAP del dominio de la web oficial (sin el www: el
                # subdominio www de nic.ar es un host distinto del dominio)
                if not dominio:
                    dominio = urllib.parse.urlsplit(
                        sit.get("url") or sitio).netloc
                    if dominio.startswith("www."):
                        dominio = dominio[4:]
                if dominio:
                    informe["rdap"] = rdap_dominio(dominio)
            finally:
                navegador.close()

    # 3b) SIN --sitio: si el nombre esta claramente asociado a un dominio
    # (candidato derivado del nombre comercial), consultar el titular RDAP
    # de los candidatos que EXISTAN (P0.2: se reporta lo verificado).
    if not sitio:
        for candidato in dominios_candidatos(nombre):
            r = rdap_dominio(candidato)
            if r.get("ok"):
                r["dominio"] = candidato
                informe["rdap_candidatos"].append(r)

    # 4) Busqueda web general con la razon social
    informe["web_general"] = buscar_en_web(
        nombre, motores=motores, captcha=captcha, headed=headed,
        salida_dir=salida_dir, locale=locale)

    # 5) Dorks de juicios
    if con_juicios:
        for consulta in _armar_juicios(informe["variantes"][0]):
            estados = buscar_en_web(consulta, motores=motores,
                                    captcha=False, headed=headed,
                                    salida_dir=salida_dir, locale=locale)
            informe["juicios"].append({"consulta": consulta,
                                       "motores": estados})

    # 6) Dorks de correos: '@dominio' + 'nombre email/correo/contacto'.
    # Los correos se extraen de los snippets de los resultados (los motores
    # indexan el contacto aunque no este en la web oficial).
    if con_correos:
        for consulta in _armar_busquedas_contacto(
                informe["variantes"][0], dominio):
            estados = buscar_en_web(consulta, motores=motores,
                                    captcha=False, headed=headed,
                                    salida_dir=salida_dir, locale=locale)
            emails = []
            for m in estados:
                for r in m.get("resultados", []):
                    emails += extraer_emails_de_html(r.get("snippet", ""))
            informe["correos_web"].append({
                "consulta": consulta,
                "motores": estados,
                "emails": sorted(set(emails)),
            })

    # 6b) Dorks de RECOMENDADORES: opiniones/resenas y mapas indexados
    # (sin APIs pagas; los buscadores indexan resenas, mapas y perfiles).
    if con_recomendadores:
        for consulta in _armar_recomendadores(informe["variantes"][0]):
            estados = buscar_en_web(consulta, motores=motores,
                                    captcha=False, headed=headed,
                                    salida_dir=salida_dir, locale=locale)
            informe["recomendadores"].append({
                "consulta": consulta,
                "motores": estados,
            })

    # 7) Sintesis
    informe["sintesis"] = _sintetizar(informe)
    return informe


def _sintetizar(informe: dict) -> dict:
    """Resumen legible del informe: CUIT encontrado (primera aparicion en
    cuitonline/sitio), senales de actividad y limitaciones honestas."""
    cuits = []
    for e in informe["cuitonline"]:
        for r in e.get("resultados", []):
            if r.get("cuit"):
                cuits.append({"cuit": r["cuit"], "razon_social": r["titulo"],
                              "fuente": f"cuitonline ({e['consulta']})"})
    if informe["sitio_oficial"] and informe["sitio_oficial"].get("cuits"):
        for c in informe["sitio_oficial"]["cuits"]:
            cuits.append({"cuit": c, "razon_social": "",
                          "fuente": "sitio oficial"})
    razon_social_sitio = ""
    if informe["sitio_oficial"]:
        razon_social_sitio = informe["sitio_oficial"].get("razon_social", "")

    # Correos publicados (web oficial + dorks web) y canales alternativos
    # (WhatsApp/redes que muchas pymes publican en lugar de correo).
    emails = []
    if informe["sitio_oficial"]:
        for e in informe["sitio_oficial"].get("emails", []):
            emails.append({"email": e, "fuente": "sitio oficial"})
    for c in informe["correos_web"]:
        for e in c.get("emails", []):
            if not any(x["email"] == e for x in emails):
                emails.append({"email": e, "fuente": f"web ({c['consulta']})"})
    canales = []
    s = informe["sitio_oficial"] or {}
    if s.get("emails"):
        canales.append("correo publicado en la web oficial")
    for wa in s.get("whatsapp", []):
        canales.append(f"WhatsApp {wa}")
    for red in s.get("redes", []):
        canales.append(f"red social {red}")

    senales = []
    if informe["cuitonline"] and any(
            e.get("resultados") for e in informe["cuitonline"]):
        senales.append("indexada en CuitOnline")
    if informe["sitio_oficial"] and informe["sitio_oficial"].get("ok"):
        senales.append("web oficial activa")
    if informe["rdap"] and informe["rdap"].get("ok"):
        senales.append("dominio registrado (RDAP NIC.AR)")
    for rc in informe.get("rdap_candidatos", []):
        senales.append(f"dominio candidato registrado: {rc['dominio']}")
    web_ok = [m for m in informe["web_general"]
              if m.get("estado") == "ok" and m.get("resultados")]
    if web_ok:
        senales.append("resultados en busqueda web general")
    recom = [r for r in informe.get("recomendadores", [])
             if sum(len(m.get("resultados", [])) for m in r["motores"])]
    if recom:
        senales.append(f"{len(recom)} dork(s) con opiniones/resenas en buscadores")

    return {
        "cuits": cuits,
        "razon_social_declarada_en_web": razon_social_sitio,
        "emails": emails,
        "canales_contacto": canales,
        "recomendadores": recom,
        "senales_actividad": senales,
        "limitaciones": [
            "CuitOnline no encontrada no prueba que el nombre legal difiera: "
            "puede no estar indexada. El CUIT exacto debe pedirse por escrito "
            "a la empresa.",
            "La ausencia de correo publicado NO prueba que la empresa no "
            "tenga correo: muchas pymes solo publican WhatsApp, redes o "
            "formulario (p. ej. landing pages). El correo oficial debe "
            "pedirse por escrito.",
            "Los expedientes judiciales laborales argentinos (CNAT, SECLO, "
            "juzgados) no son buscables publicamente por razon social: la "
            "ausencia en buscadores NO prueba que no haya juicios. Chequeo "
            "real: informe de antecedentes judiciales con el CUIT exacto.",
            "Las opiniones/resenas en buscadores son menciones indexadas "
            "(sin APIs de reseñas pagas): no constituyen una valoracion "
            "verificada de terceros.",
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir_titular(rdap: dict) -> None:
    """Titular del dominio segun RDAP, SIN datos personales (P0.9): solo el
    tipo y, si es persona juridica, el nombre de la ORGANIZACION."""
    t = rdap.get("titular_tipo", "")
    if t == "persona_juridica" and rdap.get("titular_org"):
        print(f"  titular: persona juridica — {rdap['titular_org']}")
    elif t == "persona_juridica":
        print("  titular: persona juridica")
    elif t == "no_publicado":
        print("  titular: no publicado (handle numerico, politica de NIC.AR)")
    elif t == "publicado_sin_org":
        print("  titular: publicado sin org (no se expone el nombre, P0.9)")
    elif t:
        print(f"  titular: {t}")
    else:
        print("  titular: n/d")


def _resumen_consola(informe: dict) -> None:
    print(f"== Empresa: {informe['empresa']}")
    print(f"   Variantes: {', '.join(informe['variantes'])}")
    print("\n== CuitOnline")
    for e in informe["cuitonline"]:
        n = len(e.get("resultados", []))
        extra = f" ({e.get('bloqueo', '')})" if e.get("bloqueo") else ""
        print(f"  [{e['consulta']:<28}] {e['estado']:<10} {n} resultados{extra}")
        for r in e.get("resultados", [])[:3]:
            print(f"      {r['titulo'][:55]} — {r.get('cuit', '')}")
    print("\n== Dateas")
    d = informe["dateas"]
    if d:
        extra = f" ({d.get('bloqueo', '')})" if d.get("bloqueo") else ""
        print(f"  [{d['estado']}] {d['tiempo_s']}s{extra}")
    print("\n== Sitio oficial")
    s = informe["sitio_oficial"]
    if s:
        if s.get("ok"):
            print(f"  activa — {s['titulo'][:70]}")
            if s.get("razon_social"):
                print(f"  razon social declarada: {s['razon_social']}")
            if s.get("cuits"):
                print(f"  CUITs en la web: {', '.join(s['cuits'])}")
            else:
                print("  sin CUIT publicado en el HTML")
        else:
            print(f"  no accesible — {s.get('error', '')[:70]}")
    else:
        print("  (sin --sitio)")
    print("\n== RDAP (titular del dominio, sin datos personales P0.9)")
    r = informe["rdap"]
    if r:
        if r.get("ok"):
            print(f"  registrador: {r['registrador'] or 'n/d'} | "
                  f"creado: {r['creado'][:10]}")
            _imprimir_titular(r)
        else:
            print(f"  {r.get('error', '')[:70]}")
    else:
        print("  (sin --sitio)")
    for rc in informe["rdap_candidatos"]:
        print(f"  candidato {rc['dominio']}: registrado "
              f"(creado {rc['creado'][:10]})")
        _imprimir_titular(rc)
    print("\n== Busqueda web general")
    for m in informe["web_general"]:
        extra = f" ({m.get('bloqueo', '')})" if m.get("bloqueo") else ""
        print(f"  [{m['motor']:<10}] {m['estado']:<10} "
              f"{len(m.get('resultados', []))} resultados{extra}")
    print("\n== Juicios (dorks; limitado, ver informe)")
    for j in informe["juicios"]:
        n = sum(len(m.get("resultados", [])) for m in j["motores"])
        print(f"  {j['consulta'][:55]:<57} -> {n} resultados en total")
    print("\n== Correos (dorks web; limitado, ver informe)")
    for c in informe["correos_web"]:
        n = sum(len(m.get("resultados", [])) for m in c["motores"])
        print(f"  {c['consulta'][:55]:<57} -> {n} resultados, "
              f"{len(c['emails'])} correos en snippets")
        for e in c["emails"][:5]:
            print(f"      {e}")
    print("\n== Recomendadores (dorks de opiniones)")
    for r in informe["recomendadores"]:
        n = sum(len(m.get("resultados", [])) for m in r["motores"])
        print(f"  {r['consulta'][:55]:<57} -> {n} resultados")
    sin = informe["sintesis"]
    print("\n== Sintesis")
    for c in sin["cuits"]:
        print(f"  CUIT: {c['cuit']} ({c['razon_social'] or c['fuente']})")
    if not sin["cuits"]:
        print("  CUIT: NO ENCONTRADO")
    if sin["razon_social_declarada_en_web"]:
        print(f"  Razon social (pie de web): {sin['razon_social_declarada_en_web']}")
    print("  Correos:")
    if sin["emails"]:
        for e in sin["emails"]:
            print(f"    {e['email']}  ({e['fuente']})")
    else:
        print("    NO PUBLICADOS (ver limitaciones)")
    if sin["canales_contacto"]:
        print("  Canales de contacto:")
        for canal in sin["canales_contacto"]:
            print(f"    {canal}")
    if sin["senales_actividad"]:
        print("  Senales: " + "; ".join(sin["senales_actividad"]))
    for lim in sin["limitaciones"]:
        print(f"  ! {lim}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Busqueda de empresas: CUIT, razon social, correos y "
                    "canales de contacto, senales de actividad y juicios "
                    "(multi-motor + CuitOnline + RDAP)")
    parser.add_argument("empresa", help="razon social o nombre comercial")
    parser.add_argument("--sitio", default="",
                        help="web oficial conocida (p. ej. permanencia.com.ar)")
    parser.add_argument("--motores", default="",
                        help="motores de busqueda web general separados por "
                             "comas (default: bing)")
    parser.add_argument("--captcha", action="store_true",
                        help="intentar resolver reCAPTCHA v2 con el stack "
                             "local en la busqueda web general")
    parser.add_argument("--headed", action="store_true",
                        help="navegador visible (default: headless)")
    parser.add_argument("--salida", default="",
                        help="directorio para guardar HTML crudos y el "
                             "informe JSON")
    parser.add_argument("--sin-juicios", action="store_true",
                        help="omitir los dorks de juicios (mas rapido)")
    parser.add_argument("--sin-correos", action="store_true",
                        help="omitir los dorks de correos (mas rapido)")
    parser.add_argument("--sin-recomendadores", action="store_true",
                        help="omitir los dorks de opiniones/resenas")
    parser.add_argument("--locale", default="es-AR",
                        help="locale del navegador (es-AR, en-US...)")
    args = parser.parse_args()

    motores = [m.strip() for m in args.motores.split(",") if m.strip()] or None
    informe = buscar_empresa(
        args.empresa, sitio=args.sitio, motores=motores,
        captcha=args.captcha, headed=args.headed, salida_dir=args.salida,
        locale=args.locale, con_juicios=not args.sin_juicios,
        con_correos=not args.sin_correos,
        con_recomendadores=not args.sin_recomendadores)

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
