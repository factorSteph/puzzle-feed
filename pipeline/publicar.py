"""Escritura de `feed.json`, el archivo público que lee el dashboard.

Este módulo tiene un solo trabajo delicado: **decidir qué es público**. Todo lo
que escriba acá termina en un repositorio público y servido por GitHub Pages, y
GitHub Pages no tiene servidor que decida quién ve qué: cualquiera puede abrir
el `.json` directo (spec.md §7).

## La lista de campos permitidos

Los campos que se publican están enumerados uno por uno en `CAMPOS_PUBLICOS`, y
todo lo que no esté ahí se descarta. Es al revés de lo intuitivo (sería más
corto enumerar lo que se excluye) y es a propósito: enumerando exclusiones, un
campo nuevo del pipeline se publicaría solo por olvidar agregarlo a la lista;
enumerando lo permitido, no sale hasta que alguien lo decida.

## Los identificadores del feed

El `id` interno de cada pieza es el UID del correo en el buzón, y los UID son
correlativos: publicar el número tal cual convierte cada feed en una medición
del volumen del buzón, y la serie de feeds en su historia. El contenido no dice
nada, pero la cuenta sí.

Por eso el archivo público lleva un identificador derivado, con una sal que vive
fuera del repositorio. Derivado y no aleatorio porque el tablero guarda el
progreso del rompecabezas con esta llave: si cambiara en cada corrida, cada feed
llegaría en blanco. Con sal porque un UID es un entero corto, y sin ella se
recupera probando.

## Las dos verificaciones de abajo

La enumeración de campos permitidos ya hace casi todo el trabajo, y la primera
verificación existe por si algún día alguien agrega una sección nueva al feed
(una que no pase por `_solo`) y se olvida de filtrarla.

La segunda mira el texto, y existe por una razón distinta. Cinco de cada veinte
resúmenes se construyen a partir del correo y no de un artículo bajado aparte,
y un correo lleva encima cosas que el artículo no tiene: el saludo al
suscriptor, el pie con su enlace de baja. Que el modelo las descarte es lo
normal, no una garantía. Mientras alguien lea la salida antes de publicarla, esa
diferencia no importa; en una corrida desatendida, esta verificación es quien
lee.
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

# Todo lo que se publica, campo por campo. Ver el docstring.
CAMPOS_PUBLICOS = (
    "id",
    "fuente",
    "titulo",
    "url_original",
    "resumen",
    "por_que_importa",
    "dato_concreto",
    "como_aplicarlo",
    "categoria",
    "tema",
    "nivel",
    "procedencia_resumen",
    "motivo_fallback",
    "confianza",
    "fecha_original",
    "duplicado_de",
)

CAMPOS_CURSO = ("id", "titulo", "fuente", "url", "fecha_evento", "gratis")
CAMPOS_HILO = ("titulo", "tipo", "que_dice_el_conjunto", "ids")

# Prefijos de nombre de campo que no pueden existir en el archivo público. Se
# comparan contra las CLAVES del árbol, nunca contra el texto: la primera
# versión de esto miraba el JSON entero como una cadena y abortó una corrida
# real porque una noticia sobre Instagram hablaba de "perfiles" de redes
# sociales. El contenido de un resumen puede decir cualquier palabra (es texto
# sobre el mundo); lo que no puede aparecer es un campo que lleve datos
# privados.
#
# Una extensión local puede sumar los suyos por su cuenta; acá van los genéricos.
PROHIBIDOS = ("privado", "local", "perfil", "interno", "secreto")

# Largo del identificador público, en caracteres hexadecimales. Con cuatro bytes
# hay cuatro mil millones de valores para las ~20 piezas de un feed; aun así la
# unicidad se comprueba abajo en vez de darse por hecha.
LARGO_ID = 8

# Un valor de query string más largo que esto no es un parámetro de navegación:
# es un identificador. Los enlaces que publica el pipeline son la dirección
# final del artículo, ya sin la cadena de rastreo, así que en una corrida sana
# esto no encuentra nada; está para el día en que una redirección no resuelva.
LARGO_DE_TOKEN = 20


class FugaDePrivacidad(Exception):
    """Algo privado llegó al archivo público. Se aborta antes de escribir."""


def identificador_publico(interno, sal):
    """Deriva el identificador que sale al archivo público.

    Estable entre corridas para la misma pieza —el tablero guarda el progreso
    con esta llave— y sin relación aparente con el UID de origen.
    """
    # La sal se reduce a una llave de largo fijo antes de usarla: blake2s
    # rechaza llaves de más de 32 bytes, y la sal la escribe una persona.
    llave = hashlib.blake2s(sal.encode("utf-8")).digest()
    digest = hashlib.blake2s(
        str(interno).encode("utf-8"), key=llave, digest_size=LARGO_ID // 2
    )
    return digest.hexdigest()


def armar(items, cursos, hilos, generado, sal, identificadores=()):
    """Arma la estructura pública. No escribe nada todavía."""
    publicos = [i for i in items if "es_duplicado_de" not in i]

    def publico(interno):
        return identificador_publico(interno, sal) if interno is not None else None

    feed = {
        "generado": generado,
        "items": [_solo(i, CAMPOS_PUBLICOS) for i in publicos],
        "hilos": [_solo(h, CAMPOS_HILO) for h in hilos],
        "cursos": [_solo(c, CAMPOS_CURSO) for c in cursos],
    }

    # Los identificadores viajan en cuatro lugares y tienen que quedar
    # consistentes entre sí: si uno solo se queda sin traducir, el tablero deja
    # de encontrar la pieza a la que apunta.
    for item in feed["items"]:
        item["id"] = publico(item["id"])
        item["duplicado_de"] = [publico(i) for i in item.get("duplicado_de") or []]
    for hilo in feed["hilos"]:
        hilo["ids"] = [publico(i) for i in hilo.get("ids") or []]
    for curso in feed["cursos"]:
        curso["id"] = publico(curso["id"])

    _verificar(feed, identificadores)
    return feed


def escribir(ruta, items, cursos, hilos, generado, sal, identificadores=()):
    """Escribe `feed.json`. Devuelve (ruta, cuántos_items).

    Si la verificación de privacidad falla, no se escribe nada y se levanta
    `FugaDePrivacidad`: es preferible una corrida rota a un archivo publicado
    con algo que no debía salir.
    """
    feed = armar(items, cursos, hilos, generado, sal, identificadores)

    destino = Path(ruta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(feed, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destino, len(feed["items"])


def _solo(origen, campos):
    """Copia únicamente los campos permitidos, en orden fijo."""
    return {campo: origen.get(campo) for campo in campos}


def _claves(nodo):
    """Todas las claves del árbol, a cualquier profundidad."""
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            yield str(clave).lower()
            yield from _claves(valor)
    elif isinstance(nodo, list):
        for elemento in nodo:
            yield from _claves(elemento)


def _textos(nodo):
    """Todos los valores de texto del árbol, a cualquier profundidad."""
    if isinstance(nodo, dict):
        for valor in nodo.values():
            yield from _textos(valor)
    elif isinstance(nodo, list):
        for elemento in nodo:
            yield from _textos(elemento)
    elif isinstance(nodo, str):
        yield nodo


def _verificar(feed, identificadores=()):
    """Última barrera antes de escribir. Tres preguntas, ninguna genérica.

    **Los nombres de los campos.** Un resumen puede legítimamente hablar de
    "perfiles de Instagram" o de "datos privados de los usuarios": es prosa
    sobre el mundo, y buscar esas palabras en el texto abortó una corrida real
    por una noticia sobre redes sociales. Lo que no puede existir es un campo
    que TRAIGA datos privados, y eso sí se detecta por su nombre.

    **Los identificadores propios.** Acá sí se mira el texto, pero contra una
    lista corta y literal que se recibe de afuera, no contra palabras comunes.
    Un nombre propio en un resumen no es prosa sobre el mundo.

    **Los enlaces.** Se publica la dirección final del artículo, ya sin la
    cadena de rastreo. Un valor largo colgando de un `?` significa que alguna
    redirección no resolvió y quedó el enlace del correo, que lleva encima a
    quién se le mandó.
    """
    # Coincidencia por subcadena y no exacta: `dato_privado` y `nota_interna`
    # tienen que caer igual que `privado` e `interno`. Un campo que lleve algo
    # que no debe salir rara vez se va a llamar exactamente como la palabra.
    encontrados = sorted(
        {c for c in _claves(feed) if any(p in c for p in PROHIBIDOS)}
    )
    if encontrados:
        raise FugaDePrivacidad(
            "El feed público lleva campos que no deben salir: "
            f"{', '.join(encontrados)}.\n"
            "No se escribió nada: revisá qué sección agregó ese campo "
            "(spec.md §7)."
        )

    ids = [i["id"] for i in feed["items"]]
    if len(set(ids)) != len(ids):
        raise FugaDePrivacidad(
            "Dos piezas quedaron con el mismo identificador público.\n"
            "No se escribió nada: el tablero las confundiría entre sí."
        )

    textos = list(_textos(feed))

    for texto in textos:
        for identificador in identificadores:
            if identificador and identificador.lower() in texto.lower():
                # El texto que disparó esto no se imprime: en una corrida
                # automática este mensaje va a un registro que se lee de lejos.
                raise FugaDePrivacidad(
                    "Un identificador propio aparece en el texto a publicar.\n"
                    "No se escribió nada. Revisá el feed con --json en un "
                    "archivo local para ver cuál es la pieza."
                )

    for texto in textos:
        if not texto.startswith(("http://", "https://")):
            continue
        partes = urlsplit(texto)
        if partes.username or partes.password:
            raise FugaDePrivacidad(
                f"Un enlace a publicar lleva credenciales: {partes.hostname}\n"
                "No se escribió nada."
            )
        for parametro, valor in parse_qsl(partes.query):
            if len(valor) >= LARGO_DE_TOKEN:
                raise FugaDePrivacidad(
                    f"Un enlace a publicar lleva un identificador colgando: "
                    f"{partes.hostname} (parámetro `{parametro}`)\n"
                    "No se escribió nada: lo más probable es que una "
                    "redirección no haya resuelto y ese enlace sea el del "
                    "correo, no el del artículo."
                )
