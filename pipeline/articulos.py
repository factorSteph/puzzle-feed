"""Descarga del artículo original y extracción de su texto.

Por qué existe (spec.md D7): el blurb del newsletter ya viene editorializado.
Resumir un resumen sesgado duplica el sesgo. Queremos la fuente.

Esto NO necesita un agente: es `requests` + `trafilatura`, determinístico y gratis.
El juicio (cuáles links son noticias) lo pone el LLM antes de llamar acá.

Nada falla en silencio: cada intento devuelve su resultado o el motivo exacto por
el que no se pudo, y ese motivo termina visible en el dashboard como badge.
"""

import time

import requests
import trafilatura

# User-agent honesto: decimos quiénes somos en vez de fingir ser un navegador.
AGENTE = "PuzzleFeed/0.1 (agente personal de lectura de newsletters)"
TIEMPO_LIMITE = 15
PAUSA_ENTRE_PEDIDOS = 1.0  # cortesía: no martillamos ningún servidor
MINIMO_UTIL = 400          # menos caracteres que esto no es un artículo

# Señales de que llegamos a un muro de pago en vez de al texto.
SENALES_PAYWALL = (
    "subscribe to continue", "subscribers only", "create an account to read",
    "this article is for subscribers", "become a member to read",
    "suscríbete para continuar", "contenido exclusivo para suscriptores",
)


def obtener(url, sesion=None):
    """Baja un artículo y devuelve su texto, o explica por qué no se pudo.

    Devuelve un dict con:
        ok        → True si hay texto utilizable
        url_final → la URL después de seguir las redirecciones de tracking
        titulo    → título extraído del artículo, si lo hay
        texto     → el cuerpo limpio
        motivo    → por qué falló, cuando ok es False
    """
    sesion = sesion or requests.Session()
    resultado = {"ok": False, "url_final": url, "titulo": None, "texto": "", "motivo": None}

    try:
        # allow_redirects sigue la cadena de trackers (link.mail.beehiiv.com,
        # substack.com/redirect, etc.) hasta el artículo real.
        respuesta = sesion.get(
            url,
            headers={"User-Agent": AGENTE, "Accept-Language": "es,en;q=0.8"},
            timeout=TIEMPO_LIMITE,
            allow_redirects=True,
        )
    except requests.Timeout:
        resultado["motivo"] = "timeout"
        return resultado
    except requests.RequestException as error:
        resultado["motivo"] = f"error_de_red: {type(error).__name__}"
        return resultado

    resultado["url_final"] = respuesta.url

    if respuesta.status_code == 429:
        # Nos pidieron bajar el ritmo. Respetamos su Retry-After y reintentamos
        # una vez; si insiste, lo damos por perdido en vez de insistir nosotros.
        espera = min(_segundos_de_espera(respuesta), 30)
        time.sleep(espera)
        try:
            respuesta = sesion.get(
                url,
                headers={"User-Agent": AGENTE, "Accept-Language": "es,en;q=0.8"},
                timeout=TIEMPO_LIMITE,
                allow_redirects=True,
            )
            resultado["url_final"] = respuesta.url
        except requests.RequestException:
            resultado["motivo"] = "rate_limit_persistente"
            return resultado
        if respuesta.status_code == 429:
            resultado["motivo"] = "rate_limit_persistente"
            return resultado

    if respuesta.status_code == 403:
        # Cloudflare y compañía. Es un bloqueo, no un error nuestro.
        resultado["motivo"] = "bloqueado_por_el_sitio"
        return resultado
    if respuesta.status_code >= 400:
        resultado["motivo"] = f"http_{respuesta.status_code}"
        return resultado

    tipo = respuesta.headers.get("Content-Type", "")
    if "html" not in tipo.lower():
        resultado["motivo"] = f"no_es_html: {tipo.split(';')[0] or 'desconocido'}"
        return resultado

    texto = trafilatura.extract(
        respuesta.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )

    if not texto:
        resultado["motivo"] = "sin_texto_extraible"
        return resultado

    bajo = texto.lower()
    if len(texto) < MINIMO_UTIL and any(s in bajo for s in SENALES_PAYWALL):
        resultado["motivo"] = "paywall"
        return resultado
    if len(texto) < MINIMO_UTIL:
        resultado["motivo"] = f"muy_corto ({len(texto)} caracteres)"
        return resultado

    metadatos = trafilatura.extract_metadata(respuesta.text)
    resultado.update(
        ok=True,
        titulo=getattr(metadatos, "title", None),
        texto=texto,
    )
    return resultado


def obtener_varios(urls, pausa=PAUSA_ENTRE_PEDIDOS):
    """Baja varios artículos en serie, con pausa entre pedidos."""
    sesion = requests.Session()
    resultados = []
    for i, url in enumerate(urls):
        if i:
            time.sleep(pausa)
        resultados.append(obtener(url, sesion))
    return resultados


def _segundos_de_espera(respuesta):
    """Lee el encabezado Retry-After. Si no viene o es una fecha, usa 5 segundos."""
    valor = respuesta.headers.get("Retry-After", "")
    try:
        return max(1, int(valor))
    except (TypeError, ValueError):
        return 5
