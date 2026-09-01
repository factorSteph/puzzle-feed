"""Puzzle Feed — corrida del pipeline.

Ingesta IMAP y filtro por lista de admitidos, extracción de links y descarga de
artículos, y los pasos de criterio: elegir, resumir, deduplicar e hilar.

**Sin banderas de salida no escribe nada**: ni archivos, ni correo, ni Gmail.
Procesa, reporta en la terminal, y se va. Que escribir sea opt-in es
deliberado — abajo se explica por cada salida.

Uso:
    python pipeline/main.py                      # últimos 3 días
    python pipeline/main.py --dias 7             # una semana
    python pipeline/main.py --limite 4           # solo 4 correos
    python pipeline/main.py --por-correo 12      # más noticias por correo
    python pipeline/main.py --sin-llm            # solo ingesta, sin gastar modelo
    python pipeline/main.py --publicar --marcar  # escribir el feed y ordenar Gmail

## El gancho local

Si existe `pipeline/local.py`, se carga y se le pasa el feed ya armado. Está
en .gitignore: lo que cada quien haga con su propio feed no tiene por qué vivir
en un repositorio público. Sin ese archivo el pipeline funciona igual.

## Sobre los correos que fallan

Un correo cuyo paso de LLM falla no detiene la corrida: se registra la
incidencia y se sigue con el próximo. Ese correo NO se marca como procesado
(bloque 4), así que la corrida de pasado mañana lo reintenta gratis. La ventana
de `newer_than` lo saca del rango solo a los pocos días, así que tampoco se
reintenta para siempre.
"""

import argparse
import datetime
import importlib.util
import pathlib
import imaplib
import sys
from collections import defaultdict

import analisis
import articulos
import etiquetas
import gemini
import publicar
from config import ErrorDeConfiguracion, cargar_credenciales, cargar_fuentes
from cuerpos import extraer_candidatos, partes, traer_cuerpos
from ingesta import (
    buscar_sin_procesar,
    carpeta_todo_el_correo,
    conectar,
    traer_encabezados,
)

ETIQUETA_PROCESADO = etiquetas.ETIQUETA_PROCESADO
RUTA_FEED = "docs/feed.json"
ANCHO = 78


# ─────────────────────────────────────────────────────────────────────────────
# Ingesta (bloque 2)
# ─────────────────────────────────────────────────────────────────────────────


def quitar_copias(correos):
    """Descarta copias exactas del mismo envío.

    Dos causas observadas en datos reales:

    1. Gmail ignora los puntos en las direcciones: `nombre@gmail.com` y
       `nom.bre@gmail.com` son el mismo buzón. Algunos newsletters tienen al
       suscriptor apuntado con las dos grafías y mandan una copia por cada una.
    2. Otros remitentes sencillamente reenvían el mismo correo varias veces.

    En ambos casos, sin esto la misma pieza aparece repetida en el feed.

    Ojo: esto NO es la deduplicación entre fuentes del spec (§6, paso 6). Eso es
    "la misma noticia contada por Rundown y por TLDR" y necesita criterio
    semántico. Esto es "el mismo correo, dos veces" y se resuelve comparando.

    Devuelve (correos_únicos, cuántos_se_quitaron).
    """
    vistos = {}
    for correo in correos:
        # Mismo remitente + mismo asunto + mismo día = mismo envío.
        # Incluimos el día para no colapsar newsletters que reusan el asunto.
        dia = correo["fecha"].date() if correo["fecha"] else None
        clave = (correo["remitente"], correo["asunto"], dia)
        if clave not in vistos:
            vistos[clave] = correo

    return list(vistos.values()), len(correos) - len(vistos)


def clasificar(correos, fuentes, cursos, descartados):
    """Reparte cada correo en uno de cuatro grupos, sin descartar nada en silencio."""
    grupos = {
        "fuente": defaultdict(list),
        "curso": defaultdict(list),
        "descartado": defaultdict(list),
        "desconocido": defaultdict(list),
    }

    for correo in correos:
        remitente = correo["remitente"]
        if remitente in fuentes:
            grupos["fuente"][remitente].append(correo)
        elif remitente in cursos:
            grupos["curso"][remitente].append(correo)
        elif remitente in descartados:
            grupos["descartado"][remitente].append(correo)
        else:
            # Ni aprobado ni descartado: nunca lo vimos. Es la lista más útil
            # del reporte, porque muestra lo que a la lista de admitidos le falta.
            grupos["desconocido"][remitente].append(correo)

    return grupos


# ─────────────────────────────────────────────────────────────────────────────
# Construcción del feed (bloque 3)
# ─────────────────────────────────────────────────────────────────────────────


def procedencia_del_correo(entrada_fuente):
    """Procedencia de las fuentes cuyo contenido viene dentro del correo.

    Vale igual para `autor` y para `compendio`: en ninguna de las dos hay un
    artículo que ir a buscar, y ninguna enlaza a cada pieza por separado, así
    que el sitio declarado en fuentes.yml es la única URL honesta que se le
    puede ofrecer al lector.
    """
    return {
        "url_original": entrada_fuente.get("sitio", ""),
        "procedencia_resumen": "cuerpo_del_correo",
        "motivo_fallback": None,
        "nivel": "principal",
    }


def armar_item(item_id, resumido, correo, entrada_fuente, procedencia):
    """Arma un item del feed a partir de lo que devolvió el modelo.

    Existe porque las tres rutas —agregador, autor y compendio— construían el
    mismo diccionario por separado, y agregar un campo obligaba a acordarse de
    los tres lugares. Pasó: al sumar `tema` había que tocar tres bloques
    idénticos, que es exactamente el momento en que conviene juntarlos.

    Lo que varía entre rutas viaja en `procedencia`: de dónde salió el texto,
    a qué URL apunta y si la pieza va al feed principal o a las rapiditas.
    """
    return {
        "id": item_id,
        "fuente": entrada_fuente["nombre"],
        # La categoría sale de fuentes.yml, no del modelo: ya está declarada
        # por remitente y así el dashboard agrupa parejo (D16).
        "categoria": entrada_fuente["categoria"],
        "titulo": resumido["titulo"],
        "resumen": resumido["resumen"],
        "por_que_importa": resumido.get("por_que_importa"),
        "dato_concreto": resumido.get("dato_concreto"),
        "como_aplicarlo": resumido.get("como_aplicarlo"),
        "tema": resumido.get("tema") or "otro",
        "confianza": resumido.get("confianza", "alta"),
        "fecha_original": correo["fecha"].date().isoformat() if correo["fecha"] else None,
        **procedencia,
    }


def procesar_correo_de_fuente(
    cliente, correo, mensaje, entrada_fuente, incidencias, por_correo
):
    """Saca los items de un correo: elegir links → bajar → resumir.

    Devuelve la lista de items. Si algo falla, devuelve lo que haya conseguido y
    deja el motivo en `incidencias` — nunca revienta la corrida.
    """
    nombre = entrada_fuente["nombre"]
    etiqueta = f"{nombre} · {_fecha_corta(correo)}"

    candidatos = extraer_candidatos(mensaje)
    if not candidatos:
        incidencias.append(f"{etiqueta}: no se encontró ningún link en el correo")
        return []

    print(f"    {len(candidatos)} link(s) candidatos…", flush=True)

    noticias, clasificados, aviso = analisis.elegir_links(
        cliente, nombre, correo["asunto"], candidatos, por_correo
    )
    if aviso:
        incidencias.append(f"{etiqueta}: {aviso}")
    if not noticias:
        print("    el modelo no encontró noticias en este correo")
        return []

    menores = sum(1 for c in clasificados if c["etiqueta"] == "noticia_menor")
    if menores:
        # No es un fallo, pero tampoco es invisible: son noticias reales que
        # quedaron fuera del corte. Si el número es grande y constante, el tope
        # está mal calibrado y hay que verlo (regla 4).
        print(f"    {menores} noticia(s) menor(es) fuera del corte de {por_correo}")

    if len(noticias) > por_correo:
        # El modelo se pasó del tope que le pedimos. Se dice y se recorta.
        incidencias.append(
            f"{etiqueta}: el modelo marcó {len(noticias)} noticias pese al tope "
            f"de {por_correo}; se procesan las primeras"
        )
        noticias = noticias[:por_correo]

    print(f"    {len(noticias)} noticia(s), bajando artículos…", flush=True)
    descargas = articulos.obtener_varios([n["url"] for n in noticias])

    # Cada artículo entra al resumen con el mejor texto que conseguimos: el del
    # artículo original si se pudo bajar (D7), o el blurb del correo si no,
    # SIEMPRE marcado con su procedencia y su motivo (spec §10).
    a_resumir, metadatos = [], {}
    for indice, (noticia, descarga) in enumerate(zip(noticias, descargas)):
        item_id = f'{correo["uid"]}-{indice}'
        if descarga["ok"]:
            texto, procedencia, motivo = descarga["texto"], "articulo", None
        else:
            texto = noticia.get("contexto") or noticia.get("titular") or ""
            procedencia, motivo = "blurb", descarga["motivo"]
            print(f"      · fallback a blurb ({motivo}): {noticia['url'][:56]}")

        a_resumir.append(
            {
                "id": item_id,
                "titulo": descarga.get("titulo") or noticia.get("titular") or "",
                "procedencia": procedencia,
                "texto": texto,
            }
        )
        metadatos[item_id] = {
            "url_original": descarga["url_final"],
            "procedencia_resumen": "articulo_completo" if descarga["ok"] else "solo_newsletter",
            "motivo_fallback": motivo,
            # El nivel decide dónde va la pieza en el dashboard. Lo que se
            # resumió del blurb no puede competir de igual a igual con lo que
            # se leyó completo: el newsletter ya lo editorializó y muchas veces
            # no trae el dato que hace útil la noticia. Va abajo, compacto,
            # pero va — desaparecerlo sería descartar en silencio.
            "nivel": "principal" if descarga["ok"] else "secundario",
        }

    resumidos, aviso = analisis.resumir(cliente, nombre, a_resumir)
    if aviso:
        incidencias.append(f"{etiqueta}: {aviso}")

    return [
        armar_item(r["id"], r, correo, entrada_fuente, metadatos[r["id"]])
        for r in resumidos
    ]


def procesar_correo_de_autor(cliente, correo, mensaje, entrada_fuente, incidencias):
    """Saca EL item de un correo cuyo contenido es el artículo mismo.

    Sin elegir links y sin descargar nada: el texto ya está acá. Cuesta una
    llamada en vez de dos, y trae el ensayo del día en vez de los links a los
    posts viejos que el correo arrastra en el pie.
    """
    nombre = entrada_fuente["nombre"]
    etiqueta = f"{nombre} · {_fecha_corta(correo)}"

    _html, texto = partes(mensaje)
    print(f"    boletín de autor · {len(texto)} caracteres de texto", flush=True)

    resumido, incidencia = analisis.resumir_ensayo(cliente, nombre, correo["asunto"], texto)
    if incidencia:
        incidencias.append(f"{etiqueta}: {incidencia}")
        return []

    return [
        armar_item(
            f'{correo["uid"]}-0', resumido, correo, entrada_fuente,
            procedencia_del_correo(entrada_fuente),
        )
    ]


def procesar_correo_compendio(cliente, correo, mensaje, entrada_fuente, incidencias, por_correo):
    """Saca varias noticias de un correo que las trae completas adentro."""
    nombre = entrada_fuente["nombre"]
    etiqueta = f"{nombre} · {_fecha_corta(correo)}"

    _html, texto = partes(mensaje)
    print(f"    compendio · {len(texto)} caracteres de texto", flush=True)

    noticias, aviso = analisis.resumir_compendio(
        cliente, nombre, correo["asunto"], texto, por_correo
    )
    if aviso:
        incidencias.append(f"{etiqueta}: {aviso}")

    print(f"    {len(noticias)} noticia(s)")
    return [
        armar_item(
            f'{correo["uid"]}-{indice}', noticia, correo, entrada_fuente,
            procedencia_del_correo(entrada_fuente),
        )
        for indice, noticia in enumerate(noticias)
    ]


def quitar_cursos_repetidos(cursos):
    """Un mismo evento anunciado en varios correos es un solo evento.

    Los cursos no pasan por la deduplicación semántica del LLM —esa mira
    noticias— así que sin esto el mismo taller aparece dos o tres veces:
    The Rundown Learn manda varios recordatorios del mismo evento y cada
    correo lo aporta de nuevo.

    No hace falta criterio para esto: mismo título y misma fecha es el mismo
    curso, y se resuelve comparando. Se conserva el primero, que es el que
    tiene el anuncio original.

    Devuelve (cursos_únicos, cuántos_se_quitaron).
    """
    vistos, unicos = set(), []
    for curso in cursos:
        clave = (" ".join(curso["titulo"].lower().split()), curso.get("fecha_evento"))
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(curso)
    return unicos, len(cursos) - len(unicos)


def marcar_duplicados(items, grupos_dup):
    """Anota qué items cuentan la misma noticia, sin borrar ninguno.

    Marcar en vez de borrar es deliberado (spec §8): `duplicado_de` deja que el
    dashboard muestre que dos fuentes cubrieron lo mismo, que es información,
    en vez de esconder una de las dos. La que queda fuera del feed lleva
    `es_duplicado_de` y sigue existiendo en el archivo.
    """
    por_id = {item["id"]: item for item in items}

    for grupo in grupos_dup:
        secundarios = [i for i in grupo["ids"] if i != grupo["id_principal"]]
        principal = por_id.get(grupo["id_principal"])
        if principal is not None:
            principal["duplicado_de"] = secundarios
        for item_id in secundarios:
            if item_id in por_id:
                por_id[item_id]["es_duplicado_de"] = grupo["id_principal"]

    print(f"  {len(grupos_dup)} grupo(s) de duplicados")
    for grupo in grupos_dup:
        print(f'    {" + ".join(grupo["ids"])}  →  {grupo["por_que"]}')


def procesar_correo_de_curso(cliente, correo, mensaje, entrada_curso, incidencias):
    """Saca los cursos y eventos anunciados en un correo."""
    nombre = entrada_curso["nombre"]
    etiqueta = f"{nombre} · {_fecha_corta(correo)}"

    _html, texto = partes(mensaje)
    candidatos = extraer_candidatos(mensaje)

    cursos, aviso = analisis.extraer_cursos(
        cliente, nombre, correo["asunto"], texto, candidatos
    )
    if aviso:
        incidencias.append(f"{etiqueta}: {aviso}")

    # De qué correo salió cada curso. Hace falta para saber a cuál etiquetar
    # después; `publicar.py` no lo incluye en su lista de campos permitidos, así que no sale
    # al archivo público.
    for curso in cursos:
        curso["uid"] = correo["uid"]
    return cursos


def construir_feed(
    cliente, grupos, mensajes, fuentes, cursos_config,
    por_correo=analisis.NOTICIAS_POR_CORREO,
):
    """Corre los seis pasos del bloque 3 y devuelve el feed.

    Devuelve (items, cursos, hilos, incidencias).
    """
    items, cursos, incidencias = [], [], []

    titulo("PROCESANDO FUENTES")
    for remitente, correos in _por_volumen(grupos["fuente"]):
        for correo in sorted(correos, key=_clave_fecha):
            mensaje = mensajes.get(correo["uid"])
            if mensaje is None:
                incidencias.append(f'{remitente} uid {correo["uid"]}: no se pudo bajar el cuerpo')
                continue
            entrada = fuentes[remitente]
            print(f'\n  {entrada["nombre"]} · {_recortar(correo["asunto"], 48)}')
            if entrada.get("tipo") == "autor":
                items += procesar_correo_de_autor(
                    cliente, correo, mensaje, entrada, incidencias
                )
            elif entrada.get("tipo") == "compendio":
                items += procesar_correo_compendio(
                    cliente, correo, mensaje, entrada, incidencias, por_correo
                )
            else:
                items += procesar_correo_de_fuente(
                    cliente, correo, mensaje, entrada, incidencias, por_correo
                )

    titulo("PROCESANDO CURSOS Y EVENTOS")
    if not grupos["curso"]:
        print("  (ningún correo de la sección de cursos)")
    for remitente, correos in _por_volumen(grupos["curso"]):
        for correo in sorted(correos, key=_clave_fecha):
            mensaje = mensajes.get(correo["uid"])
            if mensaje is None:
                continue
            print(f'\n  {cursos_config[remitente]["nombre"]} · {_recortar(correo["asunto"], 48)}')
            nuevos = procesar_correo_de_curso(
                cliente, correo, mensaje, cursos_config[remitente], incidencias
            )
            print(f"    {len(nuevos)} curso(s)/evento(s)")
            cursos += nuevos

    cursos, repetidos = quitar_cursos_repetidos(cursos)
    if repetidos:
        # Nunca en silencio, ni siquiera cuando el descarte es obvio.
        print(f"\n  {repetidos} curso(s) repetido(s) en varios correos, unificado(s).")

    if not items:
        return items, cursos, [], incidencias

    titulo("DEDUPLICANDO ENTRE FUENTES")
    grupos_dup, aviso = analisis.deduplicar(cliente, items)
    if aviso:
        incidencias.append(f"deduplicación: {aviso}")
    marcar_duplicados(items, grupos_dup)

    # Los hilos solo miran los items principales: no tiene sentido volver a
    # procesar la misma noticia porque llegó por dos fuentes.
    unicos = [item for item in items if "es_duplicado_de" not in item]

    titulo("BUSCANDO HILOS ENTRE NOTICIAS")
    hilos, aviso = analisis.tejer_hilos(cliente, unicos)
    if aviso:
        incidencias.append(f"hilos: {aviso}")
    if not hilos:
        print("  (ninguno)")
    for hilo in hilos:
        print(f'  [{hilo["tipo"]}] {hilo["titulo"]}')
        print(f'      {hilo["que_dice_el_conjunto"]}')

    return items, cursos, hilos, incidencias


# ─────────────────────────────────────────────────────────────────────────────
# Reportes
# ─────────────────────────────────────────────────────────────────────────────


def titulo(texto):
    print(f"\n{texto}")
    print("─" * ANCHO)


def reportar_ingesta(grupos, fuentes, cursos, descartados, detalle=False):
    """Qué entró y qué no. Ningún descarte queda sin motivo."""
    titulo("INGESTA")
    for etiqueta, clave in (
        ("Al feed", "fuente"),
        ("A cursos", "curso"),
        ("Descartados", "descartado"),
        ("Desconocidos", "desconocido"),
    ):
        cuantos = sum(len(cs) for cs in grupos[clave].values())
        print(f"  {etiqueta:<14} {cuantos:>4} correo(s)  de {len(grupos[clave])} remitente(s)")

    if not detalle:
        print("\n  (corré con --detalle para ver remitente por remitente)")
        return

    titulo("DESCARTADOS — cada uno con su motivo")
    if not grupos["descartado"]:
        print("  (ninguno)")
    por_motivo = defaultdict(list)
    for remitente, correos in grupos["descartado"].items():
        por_motivo[descartados[remitente]].append((remitente, len(correos)))
    for motivo in sorted(por_motivo):
        cuantos = sum(n for _, n in por_motivo[motivo])
        print(f"  {motivo}  ({cuantos})")
        for remitente, n in sorted(por_motivo[motivo], key=lambda x: -x[1]):
            print(f"      {n:>3}x  {remitente}")

    titulo("DESCONOCIDOS — no están en ninguna lista")
    if not grupos["desconocido"]:
        print("  (ninguno)")
    else:
        print("  Si alguno debería entrar al feed, agregalo a config/fuentes.yml.")
        print("  Si es ruido, agregalo a descartados_conocidos para dejarlo documentado.\n")
        for remitente, correos in _por_volumen(grupos["desconocido"]):
            print(f"  {len(correos):>3}x  {remitente}")
            print(f'          {_recortar(correos[0]["asunto"], 62)}')


def reportar_feed(items, cursos, hilos, incidencias):
    """El feed tal como lo vería el dashboard: esto es lo que se publica."""
    titulo("EL FEED  (esto es lo que sería público)")
    unicos = [i for i in items if "es_duplicado_de" not in i]
    if not unicos:
        print("  (vacío)")

    principales = [i for i in unicos if i.get("nivel") != "secundario"]
    secundarios = [i for i in unicos if i.get("nivel") == "secundario"]

    por_categoria = defaultdict(list)
    for item in principales:
        por_categoria[item["categoria"]].append(item)

    for categoria in sorted(por_categoria):
        print(f"\n  ── {categoria} ──")
        for item in por_categoria[categoria]:
            insignia = ""
            if item["procedencia_resumen"] == "cuerpo_del_correo":
                # No es una advertencia: es la procedencia ideal. El texto vino
                # completo y de primera mano, sin intermediarios ni recortes.
                insignia = "  ✓ ensayo completo"
            elif item.get("confianza") == "baja":
                insignia = "  ⚠ texto parcial"

            print(f'\n  · {item["titulo"]}')
            print(f'    {item["fuente"]}  ·  {item["fecha_original"] or "sin fecha"}{insignia}')
            if item.get("duplicado_de"):
                print(f'    también lo cubrieron: {len(item["duplicado_de"])} fuente(s) más')
            print(f'    {item["resumen"]}')
            if item.get("por_que_importa"):
                print(f'    POR QUÉ IMPORTA: {item["por_que_importa"]}')
            if item.get("dato_concreto"):
                print(f'    EL DATO: {item["dato_concreto"]}')
            if item.get("como_aplicarlo"):
                print(f'    CÓMO EMPEZAR: {item["como_aplicarlo"]}')
            print(f'    {item["url_original"][:72]}')

    if secundarios:
        titulo("TAMBIÉN SALIÓ ESTO  (no se pudo leer el artículo original)")
        print("  Resumido del correo, que ya viene editorializado. Va aparte para")
        print("  no competir con lo que sí se leyó completo — pero va, no se borra.\n")
        for item in secundarios:
            print(f'  · {item["titulo"]}')
            print(f'      {item["fuente"]} · {item["motivo_fallback"]}')
            if item.get("por_que_importa"):
                print(f'      {item["por_que_importa"]}')

    titulo("FREE COURSES COMING UP NEXT")
    if not cursos:
        print("  (ninguno)")
    for curso in cursos:
        precio = {True: "gratis", False: "pago", None: "no dice"}[curso["gratis"]]
        print(f'  · {curso["titulo"]}')
        print(f'    {curso["fuente"]}  ·  {curso["fecha_evento"] or "sin fecha"}  ·  {precio}')
        print(f'    {curso["url"][:72]}')

    if hilos:
        titulo("PIEZAS QUE SE CONECTAN")
        for hilo in hilos:
            print(f'\n  [{hilo["tipo"]}] {hilo["titulo"]}')
            print(f'    {hilo["que_dice_el_conjunto"]}')
            for item_id in hilo["ids"]:
                encontrado = next((i for i in items if i["id"] == item_id), None)
                if encontrado:
                    print(f'      · {_recortar(encontrado["titulo"], 66)}')

    titulo("INCIDENCIAS  (ningún fallo en silencio)")
    if not incidencias:
        print("  (ninguna)")
    for incidencia in incidencias:
        print(f"  · {incidencia}")


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliares
# ─────────────────────────────────────────────────────────────────────────────


def _por_volumen(grupo):
    return sorted(grupo.items(), key=lambda par: (-len(par[1]), par[0]))


def _clave_fecha(correo):
    fecha = correo["fecha"]
    return (fecha is None, fecha.timestamp() if fecha else 0)


def _fecha_corta(correo):
    return correo["fecha"].strftime("%d-%b %H:%M") if correo["fecha"] else "   ?      "


def _recortar(texto, largo=58):
    texto = " ".join((texto or "(sin asunto)").split())
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def _limitar(grupos, limite):
    """Se queda con los `limite` correos más recientes de fuentes y de cursos.

    Existe por el backlog: con 300 correos sin leer, una primera corrida
    completa tardaría horas y gastaría cuota para nada.
    """
    if not limite:
        return grupos, 0

    quitados = 0
    for clave in ("fuente", "curso"):
        todos = [(r, c) for r, correos in grupos[clave].items() for c in correos]
        todos.sort(key=lambda par: _clave_fecha(par[1]), reverse=True)
        recortado = defaultdict(list)
        for remitente, correo in todos[:limite]:
            recortado[remitente].append(correo)
        quitados += len(todos) - sum(len(c) for c in recortado.values())
        grupos[clave] = recortado

    return grupos, quitados


# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Puzzle Feed")
    parser.add_argument("--dias", type=int, default=3, help="cuántos días hacia atrás mirar")
    parser.add_argument(
        "--limite", type=int, default=0,
        help="máximo de correos a procesar por sección (0 = todos)",
    )
    parser.add_argument(
        "--por-correo", type=int, default=analisis.NOTICIAS_POR_CORREO,
        help=(
            f"cuántas noticias tomar de cada correo (default {analisis.NOTICIAS_POR_CORREO}). "
            "Subilo para vaciar backlog, bajalo si el feed te queda largo"
        ),
    )
    parser.add_argument("--sin-llm", action="store_true", help="solo ingesta, sin gastar modelo")
    parser.add_argument("--detalle", action="store_true", help="reporte de ingesta completo")
    parser.add_argument("--json", metavar="ARCHIVO", help="volcar el feed a un archivo")

    salidas = parser.add_argument_group(
        "salidas",
        "Sin ninguna de estas, la corrida solo mira y reporta: no escribe "
        "archivos, no manda correo y no toca Gmail.",
    )
    salidas.add_argument(
        "--publicar", nargs="?", const=RUTA_FEED, metavar="RUTA",
        help=f"escribir el feed público (default {RUTA_FEED})",
    )
    salidas.add_argument(
        "--local", action="store_true",
        help="ejecutar el gancho local (pipeline/local.py), si existe",
    )
    salidas.add_argument(
        "--marcar", action="store_true",
        help="etiquetar en Gmail, marcar leídos y archivar los correos procesados",
    )
    salidas.add_argument(
        "--simular", action="store_true",
        help="con --marcar o --local: mostrar qué haría, sin hacerlo",
    )
    args = parser.parse_args()

    if args.simular and not (args.marcar or args.local):
        print(
            "--simular no hace nada por su cuenta: acompañalo de --marcar o --local.",
            file=sys.stderr,
        )
        return 1

    try:
        usuario, password = cargar_credenciales()
        fuentes, cursos_config, descartados = cargar_fuentes()
    except ErrorDeConfiguracion as error:
        print(f"\nConfiguración incompleta:\n{error}\n", file=sys.stderr)
        return 1

    print(f"Conectando a Gmail como {usuario}…")
    imap = conectar(usuario, password)
    try:
        carpeta = carpeta_todo_el_correo(imap)
        uids = buscar_sin_procesar(imap, carpeta, args.dias, ETIQUETA_PROCESADO)
        print(f"{len(uids)} correo(s) sin procesar en los últimos {args.dias} día(s).")
        if not uids:
            print("Nada que hacer.")
            return 0

        correos = traer_encabezados(imap, uids)
        correos, copias = quitar_copias(correos)
        if copias:
            # Nunca descartar en silencio: si se quitó algo, se dice.
            print(f"{copias} copia(s) duplicada(s) descartada(s) (doble suscripción).")

        grupos = clasificar(correos, fuentes, cursos_config, descartados)
        reportar_ingesta(grupos, fuentes, cursos_config, descartados, args.detalle)

        if args.sin_llm:
            return 0

        grupos, quitados = _limitar(grupos, args.limite)
        if quitados:
            print(f"\n  --limite {args.limite}: se dejan fuera {quitados} correo(s) más viejos.")

        interesantes = [
            correo["uid"]
            for clave in ("fuente", "curso")
            for correos_del_remitente in grupos[clave].values()
            for correo in correos_del_remitente
        ]
        if not interesantes:
            print("\nNingún correo de la lista de admitidos en este rango.")
            return 0

        print(f"\nBajando el cuerpo de {len(interesantes)} correo(s)…", flush=True)
        mensajes = traer_cuerpos(imap, interesantes)
    finally:
        # Cerrar la sesión siempre, aunque algo haya explotado en el medio.
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    try:
        cliente = gemini.Cliente()
    except gemini.ErrorDeConfiguracion as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 1

    items, cursos, hilos, incidencias = construir_feed(
        cliente, grupos, mensajes, fuentes, cursos_config, por_correo=args.por_correo,
    )

    reportar_feed(items, cursos, hilos, incidencias)

    titulo("COSTO DE LA CORRIDA")
    print(f"  {cliente.resumen()}")

    if args.json:
        _volcar(args.json, items, cursos, hilos, incidencias)

    return publicar_salidas(args, usuario, password, correos, items, cursos, hilos, cliente)


def publicar_salidas(args, usuario, password, correos, items, cursos, hilos, cliente):
    """Escribe el feed, corre el gancho local y marca en Gmail — lo pedido.

    Va en este orden a propósito, de menos a más irreversible: primero el
    archivo, que se puede borrar; después lo que el gancho local haga, que
    puede salir de la máquina; y al final el buzón. Si algo falla antes, lo de
    después no pasa.
    """
    fecha_feed = _fecha_del_feed(items)

    if args.publicar:
        titulo("FEED PÚBLICO")
        try:
            destino, cuantos = publicar.escribir(
                args.publicar, items, cursos, hilos, fecha_feed
            )
            print(f"  {cuantos} item(s) en {destino}")
        except publicar.FugaDePrivacidad as error:
            print(f"\n  ABORTADO — {error}\n", file=sys.stderr)
            return 1

    if args.local:
        titulo("GANCHO LOCAL" + ("  (simulación)" if args.simular else ""))
        correr_gancho_local(args, usuario, password, items, hilos, fecha_feed, cliente)

    if args.marcar:
        return marcar_en_gmail(args, usuario, password, correos, items, cursos)

    return 0


def correr_gancho_local(args, usuario, password, items, hilos, fecha_feed, cliente):
    """Corre `pipeline/local.py` si existe, y si no, sigue de largo.

    Es el punto de extensión del pipeline: recibe el feed ya armado y hace lo
    que quiera con él. Ese archivo está en .gitignore porque lo que cada quien
    haga con su propio feed no tiene por qué vivir en un repositorio público.

    Lo que imprima sale solo en la terminal de quien corre el pipeline. Nada de
    esto toca `feed.json` — el feed público ya se escribió antes, con su propia
    enumeración de campos permitidos.
    """
    ruta = pathlib.Path(__file__).with_name("local.py")
    if not ruta.exists():
        print("\n  (no hay gancho local: pipeline/local.py no existe)")
        return

    especificacion = importlib.util.spec_from_file_location("local", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    try:
        especificacion.loader.exec_module(modulo)
        lineas, incidencias = modulo.procesar(
            cliente,
            [i for i in items if "es_duplicado_de" not in i],
            hilos, fecha_feed, usuario, password,
            enviar_correo=True, simular=args.simular,
        )
    except Exception as error:
        # Un gancho roto no puede tumbar la corrida: para cuando llega acá, el
        # feed ya está escrito y Gmail todavía no se tocó.
        print(f"  el gancho local falló: {type(error).__name__}: {error}", file=sys.stderr)
        return

    for linea in lineas:
        print(linea)
    for incidencia in incidencias:
        print(f"  · {incidencia}", file=sys.stderr)


def marcar_en_gmail(args, usuario, password, correos, items, cursos):
    """Etiqueta, marca leídos y archiva los correos que sí se procesaron.

    Se reconecta en vez de reusar la sesión de la ingesta: entre una cosa y
    otra pasaron varios minutos de llamadas al modelo, y una conexión IMAP
    ociosa tanto rato se cae sola.
    """
    procesados = {str(i["id"]).split("-")[0] for i in items}
    cursos_por_uid = {c["uid"] for c in cursos if c.get("uid")}
    procesados |= cursos_por_uid

    plan = etiquetas.planificar(items, cursos_por_uid, procesados)
    por_uid = {c["uid"]: c for c in correos}

    titulo("GMAIL" + ("  (simulación — no se toca nada)" if args.simular else ""))
    for linea in etiquetas.describir(plan, por_uid):
        print(linea)

    if not plan:
        return 0

    intactos = len(correos) - len(plan)
    print(f"\n  {len(plan)} correo(s) a marcar · {intactos} quedan intactos")
    print("  Lo descartado, lo desconocido y lo que falló no se toca (D12, D17).")

    if args.simular:
        print("\n  Simulación: no se escribió nada. Sacá --simular para hacerlo de verdad.")
        return 0

    imap = conectar(usuario, password)
    try:
        carpeta = carpeta_todo_el_correo(imap)
        tocados, fallos = etiquetas.aplicar(imap, carpeta, plan, simular=False)
        print(f"\n  {tocados} correo(s) etiquetado(s), marcado(s) leído(s) y archivado(s).")
        for fallo in fallos:
            print(f"  · {fallo}", file=sys.stderr)
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    return 0


def _fecha_del_feed(items):
    """La fecha más reciente del feed, que es la que lo identifica."""
    fechas = sorted(i["fecha_original"] for i in items if i.get("fecha_original"))
    return fechas[-1] if fechas else datetime.date.today().isoformat()


def _volcar(ruta, items, cursos, hilos, incidencias):
    """Vuelca el feed a disco para poder mirarlo con calma. Solo depuración."""
    with open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(
            analisis.volcar(
                {
                    "items": items,
                    "cursos": cursos,
                    "hilos": hilos,
                    "incidencias": incidencias,
                }
            )
        )
    print(f"\n  Feed volcado en {ruta}")


if __name__ == "__main__":
    sys.exit(main())
