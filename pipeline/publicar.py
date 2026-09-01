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

## La verificación de abajo

La enumeración de campos permitidos ya hace casi todo el trabajo. La
verificación existe por si algún día alguien agrega una sección nueva al feed
(una que no pase por `_solo`) y se olvida de filtrarla.
"""

import json
from pathlib import Path

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
# Un gancho local puede sumar los suyos por su cuenta; acá van los genéricos.
PROHIBIDOS = ("privado", "local", "perfil", "interno", "secreto")


class FugaDePrivacidad(Exception):
    """Algo privado llegó al archivo público. Se aborta antes de escribir."""


def armar(items, cursos, hilos, generado):
    """Arma la estructura pública. No escribe nada todavía."""
    publicos = [i for i in items if "es_duplicado_de" not in i]

    feed = {
        "generado": generado,
        "items": [_solo(i, CAMPOS_PUBLICOS) for i in publicos],
        "hilos": [_solo(h, CAMPOS_HILO) for h in hilos],
        "cursos": [_solo(c, CAMPOS_CURSO) for c in cursos],
    }

    _verificar(feed)
    return feed


def escribir(ruta, items, cursos, hilos, generado):
    """Escribe `feed.json`. Devuelve (ruta, cuántos_items).

    Si la verificación de privacidad falla, no se escribe nada y se levanta
    `FugaDePrivacidad`: es preferible una corrida rota a un archivo publicado
    con algo que no debía salir.
    """
    feed = armar(items, cursos, hilos, generado)

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


def _verificar(feed):
    """Última barrera antes de escribir: ningún campo privado, a ninguna
    profundidad.

    Revisa las claves y no el texto. Un resumen puede legítimamente hablar de
    "perfiles de Instagram" o de "datos privados de los usuarios": es prosa
    sobre el mundo. Lo que no puede existir es un campo que TRAIGA datos
    privados, y eso sí se detecta por su nombre.

    `_solo` ya hace casi todo el trabajo con su lista de campos permitidos;
    esto está por si algún día alguien agrega una sección nueva al feed y se
    olvida de filtrarla.
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
