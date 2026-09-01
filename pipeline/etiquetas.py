"""Etiquetado y archivado en Gmail.

Este es el único módulo del proyecto que ESCRIBE en el buzón. Todo lo demás
mira y reporta. Por eso vive aparte, se activa con una bandera explícita
(`--marcar`), y trae un modo de simulación que imprime exactamente lo que haría
sin tocar nada.

## Qué hace, y qué NO

Sobre cada correo que SÍ se procesó —el que aportó piezas al feed o cursos—:

  1. le aplica su etiqueta de categoría (`PuzzleFeed/AI-Tech`, …),
  2. le aplica `PuzzleFeed/Procesado`, que es la memoria del pipeline (D8),
  3. lo marca leído (D12),
  4. lo saca de la bandeja de entrada.

Sobre todo lo demás —descartados, desconocidos, y los correos cuyo paso de LLM
falló— **no hace nada**. Ni etiqueta, ni marca, ni mueve. La razón es D12 y
D17: el agente no los leyó ni los resumió, así que no puede afirmar que la
lectora ya los vio, y dejarlos sin la etiqueta es lo que hace que la corrida
siguiente los reintente.

## Por qué "mover a una carpeta" es quitar una etiqueta

Gmail no tiene carpetas: tiene etiquetas, y la bandeja de entrada es una de
ellas (`\\Inbox`). Archivar un correo es sacarle esa etiqueta. Por eso mover y
etiquetar son la misma operación acá, y por eso es reversible: volver a poner
`\\Inbox` devuelve el correo a la bandeja.

## Reversibilidad

Todo lo que toca queda marcado con `PuzzleFeed/Procesado`. Si algún día hay que
deshacerlo, esa etiqueta dice exactamente cuáles fueron —ningún otro correo del
buzón la tiene.
"""

import imaplib
import re

ETIQUETA_RAIZ = "PuzzleFeed"
ETIQUETA_PROCESADO = f"{ETIQUETA_RAIZ}/Procesado"
ETIQUETA_CURSOS = f"{ETIQUETA_RAIZ}/Cursos"

# Gmail admite espacios en las etiquetas, pero obligan a citar el nombre en
# cada comando IMAP y se rompen con cualquier descuido de escapado. Con
# guiones el nombre es una sola palabra y se lee igual de bien en la interfaz.
_NO_ALFANUMERICO = re.compile(r"[^\w]+", re.UNICODE)


def nombre_de_etiqueta(categoria):
    """Convierte "Mujeres y emprendimiento" en "PuzzleFeed/Mujeres-y-emprendimiento"."""
    limpio = _NO_ALFANUMERICO.sub("-", (categoria or "").strip()).strip("-")
    return f"{ETIQUETA_RAIZ}/{limpio or 'Sin-categoria'}"


def planificar(items, cursos_por_uid, correos_procesados):
    """Decide qué etiquetas lleva cada correo, sin tocar nada todavía.

    Separar el plan de la ejecución es lo que permite que `--simular` muestre
    exactamente lo mismo que se va a hacer: es el mismo plan, solo que no se
    ejecuta.

    `correos_procesados` es el conjunto de uids que produjeron algo. Un correo
    que no está ahí no se toca, aunque haya sido de una fuente conocida.

    Devuelve {uid: [etiquetas]} ordenado por uid, para que el reporte salga
    estable entre corridas.
    """
    plan = {}

    for item in items:
        uid = str(item["id"]).split("-")[0]
        if uid not in correos_procesados:
            continue
        etiquetas = plan.setdefault(uid, set())
        etiquetas.add(nombre_de_etiqueta(item["categoria"]))

    for uid in cursos_por_uid:
        if uid in correos_procesados:
            plan.setdefault(uid, set()).add(ETIQUETA_CURSOS)

    # Procesado va en todos: es la memoria que evita reprocesarlos (D8).
    for etiquetas in plan.values():
        etiquetas.add(ETIQUETA_PROCESADO)

    return {uid: sorted(plan[uid]) for uid in sorted(plan, key=int)}


def aplicar(imap, carpeta, plan, simular=True, archivar=True):
    """Ejecuta el plan sobre el buzón. Con `simular`, solo dice qué haría.

    `simular` viene en True a propósito: quien llame tiene que pedir
    explícitamente que se escriba de verdad. Es la única salvaguarda posible
    contra una corrida distraída sobre un buzón real.

    Devuelve (cuántos_correos_se_tocaron, incidencias).
    """
    if not plan:
        return 0, []

    if simular:
        return 0, []

    # Hasta acá el buzón se abrió en readonly. Para escribir hay que reabrirlo,
    # y que falle acá es preferible a que falle a mitad del recorrido.
    tipo, _ = imap.select(f'"{carpeta}"', readonly=False)
    if tipo != "OK":
        return 0, [f"no pude abrir {carpeta} para escritura; no se tocó nada"]

    tocados, incidencias = 0, []

    for uid, etiquetas in plan.items():
        try:
            citadas = " ".join(f'"{e}"' for e in etiquetas)
            _exigir(imap.uid("STORE", uid, "+X-GM-LABELS", f"({citadas})"),
                    f"uid {uid}: no pude aplicar las etiquetas")
            _exigir(imap.uid("STORE", uid, "+FLAGS", "(\\Seen)"),
                    f"uid {uid}: no pude marcarlo leído")
            if archivar:
                # Sacarlo de la bandeja. En Gmail la bandeja es una etiqueta
                # más, así que archivar es quitársela.
                #
                # Ojo con las barras: acá hace falta que al servidor le llegue
                # `(\Inbox)`, y en un literal de Python eso se escribe con dos.
                # Escribirlo con cuatro —que es lo que parecía correcto— le
                # manda `(\\Inbox)` y Gmail responde "Could not parse command".
                _exigir(imap.uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)"),
                        f"uid {uid}: no pude sacarlo de la bandeja")
            tocados += 1
        except (RuntimeError, imaplib.IMAP4.error) as error:
            # Un correo que falla no detiene a los demás: se anota y se sigue.
            #
            # `imaplib` a veces devuelve ("BAD", …) y a veces levanta su propia
            # excepción, según dónde se rompa el comando. Atrapar solo una de
            # las dos dejaba que la otra abortara la corrida a mitad de camino,
            # con unos correos tocados y otros no.
            incidencias.append(f"{type(error).__name__}: {error}")

    return tocados, incidencias


def _exigir(respuesta, mensaje):
    tipo, datos = respuesta
    if tipo != "OK":
        raise RuntimeError(f"{mensaje} ({datos})")


def describir(plan, correos_por_uid, archivar=True):
    """Texto legible del plan, para el reporte y para `--simular`."""
    if not plan:
        return ["  (ningún correo para marcar)"]

    lineas = []
    for uid, etiquetas in plan.items():
        correo = correos_por_uid.get(uid, {})
        asunto = " ".join((correo.get("asunto") or "(sin asunto)").split())[:52]
        lineas.append(f"  {asunto}")
        lineas.append(f"      etiquetas: {', '.join(etiquetas)}")
        lineas.append(
            "      se marca leído"
            + (" y sale de la bandeja de entrada" if archivar else "")
        )
    return lineas
