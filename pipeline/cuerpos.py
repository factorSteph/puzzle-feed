"""Lectura del cuerpo de los correos y extracción de sus links.

Un newsletter trae entre 30 y 90 links, y solo unos 6 son noticias. El resto son
patrocinios, redes sociales, "ver en el navegador" y darse de baja.

Este módulo hace la parte MECÁNICA: sacar los links y descartar la basura evidente
por su forma. Elegir cuáles de los que quedan son noticias de verdad requiere
criterio y lo hace el LLM — ver spec.md §6, paso 4.

## Por qué preferimos la parte de texto plano

Un correo MIME trae dos versiones del mismo contenido: `text/html` (la bonita) y
`text/plain` (la de respaldo). Descubrimos mirando datos reales que **no traen los
mismos links**:

    text/html   →  links envueltos en rastreadores (link.mail.beehiiv.com/...)
    text/plain  →  las URLs REALES, sin envolver

Y eso importa mucho, porque los rastreadores de beehiiv están detrás del desafío
anti-bot de Cloudflare: devuelven 403 con "Just a moment...". Saltarse eso sería
evadir detección de bots, y no lo hacemos.

La parte de texto plano nos da el destino directo sin pelear con nadie. Cinco de
las siete fuentes dependen de esto.
"""

import email
import re
from email.header import decode_header, make_header

from lxml import html as lxml_html

CAMPOS_COMPLETO = "(UID BODY.PEEK[])"
_RE_UID = re.compile(rb"UID (\d+)")
_RE_URL = re.compile(r"https?://[^\s<>\"'\)\]]+")

# Dominios que solo redirigen. Si tenemos el destino directo, estos sobran.
RASTREADORES = (
    "beehiiv.com",            # bloqueado por Cloudflare, inservible
    "tldrnewsletter.com",
    "list-manage.com",
    "sendgrid.net",
    "mailchimpapp.com",
    "click.kit-mail3.com",    # este SÍ funciona: es el respaldo cuando no hay directo
)

# Links que no son artículos, reconocibles por su forma.
PATRONES_BASURA = re.compile(
    r"""
      ^mailto: | ^tel: | ^javascript: | ^\#
    | unsubscribe | /unsub | opt[-_]?out | preferences | /profile/ | update[-_]?profile
    | view[-_]?in[-_]?browser | /webversion | view-online
    | /subscribe(\?|$) | /signup | refer\. | /referral | advertise\. | /sponsor
    | typeform\.com | /survey | calendly\.com
    | (twitter|x)\.com/ | linkedin\.com/ | facebook\.com/ | instagram\.com/
    | threads\.net/ | youtube\.com/@ | tiktok\.com/ | whatsapp\.com/
    | \.(png|jpe?g|gif|svg|webp|ico|css|js)(\?|$)
    | /privacy | /terms | /legal
    """,
    re.IGNORECASE | re.VERBOSE,
)

ANCLAS_BASURA = re.compile(
    r"^(unsubscribe|darse de baja|ver en el navegador|view (in browser|online)|"
    r"read online|leer en l[ií]nea|web version|versi[oó]n web|"
    r"sign ?up|suscribirse|subscribe|registrarse|log ?in|iniciar sesi[oó]n|"
    r"advertise|anunciate|sponsor|patrocinar|partner with us|"
    r"share|compartir|forward|reenviar|tweet|post|"
    r"privacy|terms|legal|contact|contacto|"
    r"leer m[aá]s|read more|aqu[ií]|here|click here|learn more|ver m[aá]s|"
    r"\W*)$",
    re.IGNORECASE,
)


def traer_cuerpos(imap, uids, lote=25):
    """Baja el mensaje completo de cada UID. Sigue sin marcar nada como leído."""
    mensajes = {}
    for inicio in range(0, len(uids), lote):
        grupo = ",".join(uids[inicio : inicio + lote])

        tipo, datos = imap.uid("FETCH", grupo, CAMPOS_COMPLETO)
        if tipo != "OK":
            raise RuntimeError(f"No pude bajar los cuerpos del lote {inicio}: {datos}")

        for parte in datos:
            if not isinstance(parte, tuple):
                continue
            encontrado = _RE_UID.search(parte[0])
            if encontrado:
                mensajes[encontrado.group(1).decode()] = email.message_from_bytes(parte[1])

    return mensajes


def partes(mensaje):
    """Devuelve (html, texto_plano) del correo. Cualquiera puede venir vacío."""
    htmls, textos = [], []

    for parte in mensaje.walk():
        if parte.get_content_maintype() == "multipart" or parte.get_filename():
            continue
        contenido = _decodificar(parte)
        if not contenido:
            continue
        if parte.get_content_type() == "text/html":
            htmls.append(contenido)
        elif parte.get_content_type() == "text/plain":
            textos.append(contenido)

    return "\n".join(htmls), "\n".join(textos)


def _decodificar(parte):
    crudo = parte.get_payload(decode=True)
    if crudo is None:
        return ""
    juego = parte.get_content_charset() or "utf-8"
    try:
        return crudo.decode(juego, errors="replace")
    except LookupError:
        return crudo.decode("utf-8", errors="replace")


def extraer_candidatos(mensaje):
    """Devuelve los links que podrían ser noticias, listos para que el LLM elija.

    Cada candidato es un dict con:
        url       → la dirección
        contexto  → texto de ancla o la línea anterior, para que el LLM juzgue
        via       → "directo" si es la URL real, "rastreador" si hay que redirigir
    """
    html, texto = partes(mensaje)

    candidatos, vistas = [], set()

    # 1. Texto plano primero: ahí viven las URLs sin envolver.
    for url, contexto in _links_de_texto(texto):
        _agregar(candidatos, vistas, url, contexto)

    # 2. HTML después: aporta lo que el texto plano no tenga.
    for url, contexto in _links_de_html(html):
        _agregar(candidatos, vistas, url, contexto)

    # Los rastreadores solo sirven si no conseguimos ningún link directo.
    directos = [c for c in candidatos if c["via"] == "directo"]
    return directos if directos else candidatos


def _agregar(candidatos, vistas, url, contexto):
    url = url.strip().rstrip(".,);:")
    if not url.lower().startswith(("http://", "https://")):
        return
    if PATRONES_BASURA.search(url):
        return

    contexto = " ".join((contexto or "").split())[:160]
    if ANCLAS_BASURA.match(contexto):
        return

    clave = url.rstrip("/")
    if clave in vistas:
        return
    vistas.add(clave)

    es_rastreador = any(t in url.lower() for t in RASTREADORES)
    candidatos.append(
        {"url": url, "contexto": contexto, "via": "rastreador" if es_rastreador else "directo"}
    )


def _links_de_texto(texto):
    """En la versión de texto plano, la línea anterior suele ser el titular."""
    if not texto:
        return []

    lineas = texto.splitlines()
    resultado = []
    for i, linea in enumerate(lineas):
        for url in _RE_URL.findall(linea):
            antes = " ".join(l.strip() for l in lineas[max(0, i - 2) : i] if l.strip())
            propio = linea.replace(url, " ").strip()
            resultado.append((url, propio or antes))
    return resultado


def _links_de_html(contenido):
    if not contenido:
        return []
    try:
        arbol = lxml_html.fromstring(contenido)
    except (ValueError, lxml_html.etree.ParserError):
        # HTML tan roto que lxml no lo abre. Cero links, no un éxito disfrazado.
        return []

    return [
        (a.get("href") or "", a.text_content() or "") for a in arbol.iter("a") if a.get("href")
    ]


def asunto(mensaje):
    valor = mensaje.get("Subject")
    if not valor:
        return "(sin asunto)"
    try:
        return str(make_header(decode_header(valor)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return valor
