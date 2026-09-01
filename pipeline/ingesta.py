"""Lectura de Gmail por IMAP.

Este módulo NO modifica nada del buzón. Tres garantías concretas:

  1. Abre el buzón en modo `readonly`: el servidor rechaza cualquier escritura,
     aunque el código tuviera un bug.
  2. Usa `BODY.PEEK` en vez de `BODY`: leer un correo NO lo marca como leído.
     Tu señal de qué leíste vos es tuya y el agente no la pisa.
  3. No aplica ni quita etiquetas, no borra, no mueve.

El etiquetado con PuzzleFeed/Procesado llega en un bloque posterior, cuando ya
hayamos comprobado que la ingesta trae lo correcto.
"""

import email
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

SERVIDOR = "imap.gmail.com"
PUERTO = 993

# Gmail expone un buzón virtual con TODO el correo: bandeja, archivado y etiquetado.
# Su nombre cambia según el idioma de la cuenta ("[Gmail]/All Mail", "[Gmail]/Todos"),
# así que lo buscamos por su flag estándar \All en vez de escribir el nombre a mano.
FLAG_TODO_EL_CORREO = rb"\All"

CAMPOS = "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
_RE_UID = re.compile(rb"UID (\d+)")


def conectar(usuario, password):
    """Abre una sesión IMAP con Gmail."""
    imap = imaplib.IMAP4_SSL(SERVIDOR, PUERTO)
    try:
        imap.login(usuario, password)
    except imaplib.IMAP4.error as error:
        raise RuntimeError(
            "Gmail rechazó las credenciales. Revisá que:\n"
            "  1. GMAIL_APP_PASSWORD sea un app password de 16 caracteres,\n"
            "     no tu contraseña normal de Gmail\n"
            "  2. la cuenta tenga verificación en 2 pasos activa\n"
            "  3. IMAP esté habilitado en Gmail → Configuración → Reenvío y POP/IMAP\n"
            f"Respuesta del servidor: {error}"
        ) from error
    return imap


def carpeta_todo_el_correo(imap):
    """Encuentra el buzón de todo el correo sin depender del idioma de la cuenta."""
    tipo, carpetas = imap.list()
    if tipo != "OK":
        raise RuntimeError("No pude listar las carpetas IMAP de la cuenta.")

    for linea in carpetas:
        if FLAG_TODO_EL_CORREO in linea:
            # Formato de la línea: (\All \HasNoChildren) "/" "[Gmail]/All Mail"
            return linea.decode().rsplit(' "', 1)[-1].strip('"')

    raise RuntimeError(
        "No encontré el buzón de todo el correo. Revisá que IMAP esté habilitado "
        "en Gmail → Configuración → Reenvío y correo POP/IMAP."
    )


def buscar_sin_procesar(imap, carpeta, dias, etiqueta_procesado):
    """Devuelve los UIDs de correos recientes que el pipeline todavía no tocó."""
    tipo, _ = imap.select(f'"{carpeta}"', readonly=True)
    if tipo != "OK":
        raise RuntimeError(f"No pude abrir la carpeta {carpeta}.")

    # X-GM-RAW deja usar la sintaxis de búsqueda de Gmail tal cual, que es mucho
    # más expresiva que la de IMAP a secas.
    consulta = f"newer_than:{dias}d -label:{etiqueta_procesado} -in:trash -in:spam"
    tipo, datos = imap.uid("SEARCH", "X-GM-RAW", f'"{consulta}"')
    if tipo != "OK":
        raise RuntimeError(f"La búsqueda en Gmail falló: {datos}")

    return datos[0].split()


def traer_encabezados(imap, uids, lote=100):
    """Trae remitente, asunto y fecha. No baja los cuerpos: acá solo clasificamos."""
    correos = []
    for inicio in range(0, len(uids), lote):
        grupo = b",".join(uids[inicio: inicio + lote]).decode()

        tipo, datos = imap.uid("FETCH", grupo, CAMPOS)
        if tipo != "OK":
            raise RuntimeError(f"No pude traer los encabezados del lote {inicio}: {datos}")

        for parte in datos:
            if not isinstance(parte, tuple):
                continue
            correos.append(_leer_encabezado(parte[0], parte[1]))

    return correos


def _leer_encabezado(prefijo, crudo):
    mensaje = email.message_from_bytes(crudo)
    _, direccion = parseaddr(mensaje.get("From", ""))
    encontrado = _RE_UID.search(prefijo)

    return {
        "uid": encontrado.group(1).decode() if encontrado else None,
        "remitente": direccion.strip().lower(),
        "asunto": _texto(mensaje.get("Subject")),
        "fecha": _fecha(mensaje.get("Date")),
    }


def _texto(valor):
    """Decodifica encabezados MIME (=?utf-8?B?...?=) a texto legible."""
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except (UnicodeDecodeError, LookupError, ValueError):
        # Un asunto malformado no debe matar la corrida. Devolvemos el crudo,
        # que se va a ver raro en el reporte: visible, no silencioso.
        return valor


def _fecha(valor):
    if not valor:
        return None
    try:
        return parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
