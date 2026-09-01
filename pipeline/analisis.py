"""Los pasos que necesitan criterio: elegir, resumir, deduplicar e hilar.

Acá viven los prompts. `gemini.py` los manda; este módulo decide qué dicen.

## Qué se agrupa y por qué

spec.md §14 lo midió: una llamada tarda del orden de segundos, no de
milisegundos. Una llamada por item (24 items × 4 pasos = 96 llamadas) daría una
corrida absurda. Entonces:

    elegir links   una llamada POR CORREO   (los ~40 links de esa edición)
    resumir        una llamada POR CORREO   (los artículos que se bajaron)
    deduplicar     UNA llamada por corrida  (necesita ver todo junto)
    hilos          UNA llamada por corrida  (idem)
    cursos         una llamada POR CORREO

## Por qué resumir va ANTES de deduplicar

spec.md §6 los lista al revés (dedup en 6, resumen en 7). Se invirtió a
propósito y con acuerdo: como el resumen ya viaja agrupado por correo, resumir
un item que después resulta duplicado no cuesta ni una llamada extra: va en el
mismo lote. Y deduplicar comparando resúmenes reales es mucho más preciso que
comparando textos de ancla, que en TLDR son cosas como "OpenAI's new model".

## Por qué los hilos son una llamada aparte y no viajan con el dedup

Los dos comparan todos los items entre sí, así que la tentación es fusionarlos.
No se hace porque el costo de equivocarse es distinto: un dedup mal hecho
BORRA una noticia del feed, un hilo mal puesto es cosmético. Meterlos en el
mismo prompt hace que compitan por la atención del modelo en la tarea donde el
error es caro.

## Sobre las alucinaciones de identificadores

El modelo nunca inventa ids: se le pasan y los devuelve. Aun así puede
devolver uno que no existe, y por eso cada respuesta se valida contra el
conjunto que se envió. Lo que no cuadra se descarta DICIÉNDOLO (regla 4), no
en silencio.
"""

import json

# Cuánto texto de cada artículo se le manda al modelo. Las notas están escritas
# en pirámide invertida: lo que importa está arriba. Seis artículos así son
# ~8k tokens de entrada, que entra cómodo.
LARGO_ARTICULO = 5000

# Baja para las tareas de criterio, donde queremos consistencia entre corridas.
TEMPERATURA_CRITERIO = 0.2
TEMPERATURA_HILOS = 0.4

# Para tareas donde queremos que se le ocurran cosas y no la respuesta más
# probable. Medido: a 0.9 el modelo empieza a escribir sin tildes ("nomina",
# "informacion"); a 0.7 mantiene la ortografía sin volverse predecible.
TEMPERATURA_CREATIVA = 0.7

# Los temas parten cada categoría por dentro. Hacen falta porque la categoría
# viene de la fuente (D16) y cinco de las seis fuentes son de tecnología: sin
# esto, 64 de 66 piezas caen en "AI & Tech" y el agrupamiento no agrupa nada.
#
# La lista es cerrada a propósito: si el modelo inventa temas, cada corrida
# arma secciones distintas y el dashboard deja de ser reconocible.
TEMAS = {
    "regulacion": "Regulación y cumplimiento",
    "agentes": "Agentes y automatización",
    "modelos": "Modelos y capacidades",
    "herramientas": "Herramientas",
    "seguridad": "Seguridad y riesgos",
    "negocio": "Negocio e industria",
    "trabajo": "Trabajo y personas",
    "otro": "Otros",
}

ETIQUETAS_DE_LINK = (
    "noticia",
    "noticia_menor",
    "autopromocion",
    "patrocinio",
    "herramienta",
    "navegacion",
    "otro",
)

# Cuántas noticias se le piden por correo. Medido contra el buzón real: las
# fuentes son cinco newsletters diarias y cada edición trae entre 10 y 18
# noticias legítimas. Sin un tope, una corrida de 2 días da ~70 items y el
# criterio de éxito del spec (§13: revisarlo en menos de 10 minutos) se rompe.
#
# Seis por correo × ~6 correos cada 2 días ≈ 24 items, que es el volumen que
# spec.md §4 asume. Se sube desde la línea de comandos para vaciar backlog.
NOTICIAS_POR_CORREO = 6


# ─────────────────────────────────────────────────────────────────────────────
# 1. Elegir cuáles links son noticias
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_ELEGIR = """\
Sos un editor. Recibís todos los links de una edición de newsletter y elegís
las {maximo} noticias MÁS IMPORTANTES. No las que son noticia: las que más
importan.

Esa distinción es todo el trabajo. Un newsletter de tecnología trae entre 10 y
18 noticias legítimas por edición. Si las marcás todas, no filtraste nada y la
lectora abandona el feed en la tercera corrida.

Clasificá CADA link, sin saltarte ninguno:

  noticia         una de las {maximo} más importantes de esta edición
  noticia_menor   es una noticia real, pero no entró en las {maximo}
  autopromocion   contenido del propio newsletter: su curso, su comunidad,
                  su podcast, su programa de referidos
  patrocinio      publicidad de un tercero que le pagó al newsletter
  herramienta     página de producto o de descarga, sin una nota que la explique
  navegacion      archivo, índice, "ver online", perfil, ajustes
  otro            no encaja en ninguna de arriba

Marcá `noticia` A LO SUMO {maximo} veces. Si la edición trae menos que eso,
marcá menos: no rellenes para llegar al número.

Qué hace importante a una pieza, en este orden:

  1. Cambia cómo se hace un trabajo: automatiza algo que era manual, mueve un
     proceso, obliga a cumplir un requisito nuevo. Sobre todo si toca
     operaciones, recursos humanos, nómina, cumplimiento o auditoría.
  2. Enseña algo sobre cómo se trabaja de verdad: un ensayo, un análisis o un
     experimento con una tesis propia sobre cómo la gente usa estas
     herramientas, qué falla, qué no se dice. Aunque no reporte ningún hecho
     nuevo. Estas piezas son tan importantes como los lanzamientos, a veces
     más: son las que dejan pensando.
  3. Es un hecho consumado y verificable (se lanzó, se aprobó, se midió) y no
     el anuncio de que algo va a pasar algún día.
  4. Trae números, no adjetivos.

  5. Regula, restringe o define qué se puede hacer con estas herramientas:
     una ley, una obligación de cumplimiento, un fallo que sienta precedente,
     una demanda por cómo se entrenó un modelo. Esto ES importante aunque no
     lance ningún producto: cambia el marco en el que todos trabajan, y las
     consecuencias caen sobre quien tiene que documentar, auditar y responder.

Qué NO la hace importante, aunque el titular sea grande:

  · que la empresa sea famosa, o que el titular exagere
  · movimientos de ejecutivos: quién entra, quién sale, quién asciende
  · contratos de venta a gobiernos y posicionamiento geopolítico entre
    proveedores, cuando la nota es sobre quién le vendió a quién
  · rondas de inversión y valuaciones
  · que salga en varias newsletters a la vez

Ojo con la diferencia: "el Pentágono le compró IA a tal proveedor" es un
contrato y no cambia el trabajo de nadie. "Un tribunal define que entrenar
con obras protegidas requiere licencia" es regulación y lo cambia todo. La
segunda entra siempre.

Un ensayo bien pensado sobre cómo la gente usa la IA en su trabajo vale más
que tres notas sobre quién le vendió qué a quién.

La diferencia entre `autopromocion` y `patrocinio` importa: la primera es el
propio newsletter vendiéndote algo suyo, la segunda es un anunciante externo.

Para los que marques `noticia`, escribí el titular tal como se entiende del
contexto que te dieron. NO inventes titulares: si el contexto no alcanza para
saber de qué trata, marcalo `otro` con motivo "contexto insuficiente".

Ante la duda entre `noticia` y cualquier otra cosa, elegí la otra. Un artículo
perdido reaparece mañana en otra newsletter; el ruido mata el objetivo entero.
"""

ESQUEMA_ELEGIR = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer", "description": "el número que se te dio"},
                    "etiqueta": {"type": "string", "enum": list(ETIQUETAS_DE_LINK)},
                    "titular": {
                        "type": "string",
                        "nullable": True,
                        "description": "solo para etiqueta noticia",
                    },
                    "motivo": {
                        "type": "string",
                        "nullable": True,
                        "description": "solo para etiqueta otro",
                    },
                },
                "required": ["indice", "etiqueta"],
                "propertyOrdering": ["indice", "etiqueta", "titular", "motivo"],
            },
        }
    },
    "required": ["links"],
}


def elegir_links(cliente, fuente, asunto, candidatos, maximo=NOTICIAS_POR_CORREO):
    """Elige las `maximo` noticias más importantes del correo.

    Le pedimos al modelo que JERARQUICE, no que clasifique. Antes marcaba todo
    lo que era noticia (10 a 18 por edición) y el orquestador cortaba las
    primeras N por orden de aparición en el correo, que es un criterio bobo: la
    posición de un link no dice nada sobre su importancia.

    Lo que queda fuera sale como `noticia_menor` y no desaparece: el reporte
    puede decir "era noticia pero no entró", que es distinto de no mencionarla.

    Devuelve (noticias, clasificados, incidencia). `clasificados` trae TODOS los
    links con su etiqueta, no solo los elegidos: es lo que permite que el
    reporte diga por qué se descartó cada uno en vez de "el modelo no lo eligió".
    """
    if not candidatos:
        return [], [], None

    lineas = [f"Fuente: {fuente}", f"Asunto: {asunto}", "", "Links:"]
    for i, candidato in enumerate(candidatos):
        contexto = candidato.get("contexto") or "(sin texto de ancla)"
        lineas.append(f'{i}. {candidato["url"]}')
        lineas.append(f'   texto: "{contexto}"')

    datos, incidencia = cliente.llamar(
        INSTRUCCION_ELEGIR.format(maximo=maximo),
        "\n".join(lineas),
        ESQUEMA_ELEGIR,
        f"elegir · {fuente}",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return [], [], incidencia

    noticias, clasificados, invalidos = [], [], 0
    for fila in datos.get("links", []):
        indice = fila.get("indice")
        if not isinstance(indice, int) or not 0 <= indice < len(candidatos):
            # El modelo se inventó un número de link. Se cuenta y se avisa.
            invalidos += 1
            continue

        candidato = candidatos[indice]
        entrada = {
            "url": candidato["url"],
            "contexto": candidato.get("contexto", ""),
            "via": candidato.get("via", "directo"),
            "etiqueta": fila.get("etiqueta", "otro"),
            "titular": fila.get("titular"),
            "motivo": fila.get("motivo"),
        }
        clasificados.append(entrada)
        if entrada["etiqueta"] == "noticia":
            noticias.append(entrada)

    aviso = None
    if invalidos:
        aviso = f"el modelo devolvió {invalidos} índice(s) de link que no existen"

    faltantes = len(candidatos) - len(clasificados) - invalidos
    if faltantes > 0:
        # No clasificó todos. No es fatal, pero rompe la promesa de que ningún
        # link se descarta sin motivo, así que se dice.
        extra = f"{faltantes} link(s) quedaron sin clasificar"
        aviso = f"{aviso}; {extra}" if aviso else extra

    return noticias, clasificados, aviso


# ─────────────────────────────────────────────────────────────────────────────
# 2. Resumir y clasificar
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_RESUMIR = """\
Escribís para alguien que NO viene siguiendo el tema. Puede haber estado
desconectada dos semanas. No sabe qué empresa es cuál, no vio la noticia
anterior, y no tiene por qué.

Tu vara es esta: si después de leer tu resumen la lectora tiene que abrir el
link para entender de qué se trata, fallaste. El link está ahí para
profundizar, no para completar lo que vos no dijiste.

Escribís en español, aunque el artículo esté en inglés.

Por cada artículo devolvés cuatro cosas:

──────────────────────────────────────────────────────────────────────────
resumen: tres o cuatro frases. Qué pasó, con todo lo necesario para
          entenderlo sin contexto previo.

  IDENTIFICÁ A CADA ACTOR la primera vez que lo nombrás. No "Tencent
  presentó un modelo": "Tencent, el gigante tecnológico chino dueño de
  WeChat, presentó un modelo". No "Vercel publicó un archivo": "Vercel, la
  empresa de infraestructura donde mucha gente aloja sus sitios web,
  publicó...". Si no sabés qué es la empresa, decí lo que sí sabés de ella
  por el artículo.

  DEJÁ CLARO QUIÉN LE HIZO QUÉ A QUIÉN. Un titular como "OpenAI cuts out
  Cursor after SpaceX acquisition" se puede leer al revés. Si tu resumen
  dice "OpenAI eliminó la integración tras la adquisición de SpaceX", quien
  lo lea va a entender que OpenAI compró SpaceX, que es un disparate. Se
  escribe: "SpaceX compró Cursor, el editor de código con IA. OpenAI, que
  compite con SpaceX, respondió cortándole el acceso a sus modelos."

  Prohibidos: "revolucionario", "cambia las reglas del juego", "game
  changer", y cualquier adjetivo que el artículo no sostenga con un hecho.
  No copies frases del original: reformulá.

──────────────────────────────────────────────────────────────────────────
por_que_importa. UNA frase. Qué cambia en el mundo real porque esto pasó.

  No repitas el resumen con otras palabras. Respondé "¿y entonces qué?".
  Si la respuesta honesta es "casi nada", escribí eso: "es un lanzamiento
  más en una semana con varios; importa solo si ya usás esa herramienta".
  Preferimos que lo digas a que infles algo vacío.

──────────────────────────────────────────────────────────────────────────
dato_concreto: el hecho específico que hace útil la noticia, o null.

  Es lo que alguien anotaría. El número, el nombre exacto, el cambio puntual.

  Si el titular dice "un solo cambio de configuración evita que el modelo
  se sobrepase", el dato_concreto ES CUÁL ES ESE CAMBIO. Un resumen que
  dice "existe un cambio de configuración que lo arregla" sin decir cuál no
  sirve para nada y es peor que no publicar la noticia.

  Poné null solo si el texto de verdad no lo trae. No inventes jamás: un
  dato inventado acá es peor que un null.

──────────────────────────────────────────────────────────────────────────
como_aplicarlo. UNA o dos frases, o null.

  El primer paso concreto para alguien que quiera usar esto, hoy. Dónde se
  empieza, qué hace falta tener, qué se instala o se lee.

  Va a publicarse en un sitio público, así que se escribe sobre la noticia
  y para cualquiera: "para probarlo hace falta X, se instala con Y". NUNCA
  sobre la lectora: nada de "vos podrías usar esto en tu proyecto".

  null cuando la noticia no es aplicable: una regulación que entra en
  vigencia en dos años, un anuncio de algo que todavía no salió. No fuerces
  un paso donde no hay ninguno.
──────────────────────────────────────────────────────────────────────────

──────────────────────────────────────────────────────────────────────────
tema: exactamente uno de estos ocho. Elegí por lo que la noticia HACE, no
       por quién la protagoniza:

  regulacion    leyes, fallos judiciales, obligaciones de cumplimiento,
                auditoría, privacidad, derechos de autor
  agentes       agentes autónomos, automatización de procesos y tareas
  modelos       lanzamientos de modelos, capacidades, evaluaciones
  herramientas  productos, editores, plataformas, integraciones
  seguridad     incidentes, vulnerabilidades, ataques, riesgos técnicos
  negocio       adquisiciones, inversión, mercado, precios, movimientos
                de empresas
  trabajo       cómo cambia el trabajo de la gente, empleo, equipos,
                contratación, ensayos sobre cómo se trabaja de verdad
  otro          no encaja en ninguno de arriba

  Una demanda de discográficas contra una empresa de IA por cómo entrenó su
  modelo es `regulacion`, no `negocio`: lo que está en juego es qué se puede
  entrenar con qué. Un agente que resuelve verificaciones laborales es
  `agentes` aunque lo venda una empresa famosa.

──────────────────────────────────────────────────────────────────────────
  confianza   alta  el texto alcanzaba para entender la noticia
              baja  era un fragmento, un teaser, o estaba cortado

TODO ESTO SE PUBLICA EN UN SITIO PÚBLICO. Se escribe sobre la noticia, nunca
sobre la lectora. "Esto automatiza la revisión manual de solicitudes" está
bien. "Esto te sirve por tu experiencia en RRHH" no va acá.

Si te pasan un texto marcado como `blurb`, viene del correo y ya está
editorializado. Resumilo igual, con confianza `baja` siempre, y sé
especialmente honesto: si el blurb no trae el dato que haría útil la noticia,
poné dato_concreto en null. No rellenes con generalidades para disimular que
no había información.

UN AVISO SOBRE TEXTOS QUE TRAEN VARIAS NOTICIAS. A veces el link no lleva a un
artículo sino a la edición completa del newsletter, y el texto que recibís
mezcla cuatro o cinco noticias sin relación: un lanzamiento de hardware, una
salida a bolsa, un telescopio. Cuando eso pase, resumí SOLO la noticia que
corresponde al titular que te dieron, e ignorá el resto por completo. Un
resumen que encadena "Apple lanzó X. Por otro lado, Oura sale a bolsa. A su
vez, la NASA lanza un telescopio" no es un resumen: es un índice, y no le
sirve a nadie. Si el titular no aparece en el texto, poné confianza `baja` y
resumí lo primero que el texto desarrolle de verdad.
"""

ESQUEMA_RESUMIR = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "el id que se te dio, tal cual"},
                    "titulo": {"type": "string"},
                    "resumen": {
                        "type": "string",
                        "description": "qué pasó, con cada actor identificado",
                    },
                    "por_que_importa": {
                        "type": "string",
                        "description": "una frase: qué cambia porque esto pasó",
                    },
                    "dato_concreto": {
                        "type": "string",
                        "nullable": True,
                        "description": "el número, el nombre exacto, el cambio puntual",
                    },
                    "como_aplicarlo": {
                        "type": "string",
                        "nullable": True,
                        "description": "primer paso concreto, sobre la noticia y para cualquiera",
                    },
                    "tema": {"type": "string", "enum": list(TEMAS)},
                    "confianza": {"type": "string", "enum": ["alta", "baja"]},
                },
                "required": ["id", "titulo", "resumen", "por_que_importa", "tema", "confianza"],
                "propertyOrdering": [
                    "id", "titulo", "resumen", "por_que_importa",
                    "dato_concreto", "como_aplicarlo", "tema", "confianza",
                ],
            },
        }
    },
    "required": ["items"],
}


def resumir(cliente, fuente, articulos):
    """Resume los artículos de un correo, en una sola llamada.

    NO devuelve categoría, aunque spec.md §6 paso 7 diga que sí. La categoría
    sale de `fuentes.yml`, donde ya está declarada por remitente, y la pone el
    orquestador.

    El spec se contradecía: §5 mapea las categorías por fuente, §6 se las pedía
    al modelo. Medido: con el modelo eligiendo, una nota de regulación llegada
    por The Rundown AI caía en "Noticias" y rompía el agrupamiento del
    dashboard, porque un mismo remitente repartía sus items entre categorías
    según el tema de cada nota.

    Gana §5, por el principio que el propio §6 establece: lo que tiene respuesta
    verificable es una función, no un agente.

    `articulos` es una lista de dicts con id, titulo, procedencia y texto.
    Devuelve (items, incidencia).
    """
    if not articulos:
        return [], None

    bloques = []
    for articulo in articulos:
        texto = (articulo.get("texto") or "").strip()
        recortado = texto[:LARGO_ARTICULO]
        if len(texto) > LARGO_ARTICULO:
            recortado += "\n[…texto recortado…]"
        bloques.append(
            f'--- id: {articulo["id"]}\n'
            f'procedencia: {articulo.get("procedencia", "articulo")}\n'
            f'titular del correo: {articulo.get("titulo") or "(ninguno)"}\n'
            f"texto:\n{recortado or '(vacío)'}\n"
        )

    datos, incidencia = cliente.llamar(
        INSTRUCCION_RESUMIR,
        f"Fuente: {fuente}\n\n" + "\n".join(bloques),
        ESQUEMA_RESUMIR,
        f"resumir · {fuente}",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return [], incidencia

    validos, inventados = _filtrar_por_id(datos.get("items", []), {a["id"] for a in articulos})

    aviso = None
    if inventados:
        aviso = f"el modelo devolvió {inventados} item(s) con id inexistente"

    perdidos = len(articulos) - len(validos)
    if perdidos > 0:
        extra = f"{perdidos} artículo(s) volvieron sin resumen"
        aviso = f"{aviso}; {extra}" if aviso else extra

    return validos, aviso


# ─────────────────────────────────────────────────────────────────────────────
# 2b. Resumir un ensayo (fuentes de tipo `autor`)
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_ENSAYO = """\
Resumís el ensayo de un boletín de autor.

Esto NO es una noticia y no se resume como tal. Nadie reporta un hecho: alguien
pensó algo y se tomó el trabajo de escribirlo. Preguntarle a este texto "qué
pasó" no tiene sentido: la pregunta es QUÉ SOSTIENE y con qué lo sostiene.

Devolvés:

  titulo     El título real del ensayo, sacado del texto. Si el asunto del
             correo ya es el título, usá ese.

  resumen    Tres o cuatro frases, en español:
               · cuál es la tesis
               · con qué la sostiene (datos, experiencia, un experimento)
               · a qué conclusión llega
             Si el ensayo usa una analogía o una imagen central, NOMBRALA.
             Suele ser lo que hace que la pieza se recuerde, y un resumen que
             la borra deja algo correcto y muerto.
             Identificá a quien escribe si el texto lo permite: "una lingüista
             que analiza datos de productividad" dice más que un nombre suelto.
             No copies frases del original: reformulá.

  por_que_importa
             UNA frase: por qué vale la pena leerlo entero, o qué discute que
             no se está discutiendo en otro lado. No repitas la tesis.

  dato_concreto
             Si el ensayo trae un dato duro (cuánta gente midió, qué
             porcentaje, qué encontró) ponelo. Si es puro razonamiento, null.

  como_aplicarlo
             Casi siempre null: un ensayo se piensa, no se ejecuta. Ponelo
             solo si de verdad propone algo que alguien pueda hacer.

  tema       exactamente uno de: regulacion, agentes, modelos, herramientas,
             seguridad, negocio, trabajo, otro.
             Un ensayo sobre cómo la gente usa la IA en su oficio es
             `trabajo`, aunque hable de modelos por el camino.

  confianza  alta si el texto estaba completo, baja si venía cortado.

Prohibido convertir el ensayo en consejos. Si la autora cuenta que planta
tomates aunque le llegue una caja de verduras, el resumen dice de qué se trata
esa reflexión: no la traduce a "5 lecciones sobre el esfuerzo".

Este resumen se publica en un sitio público: escribilo sobre el ensayo, nunca
sobre quien lo va a leer.
"""

ESQUEMA_ENSAYO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "resumen": {"type": "string"},
        "por_que_importa": {"type": "string"},
        "dato_concreto": {"type": "string", "nullable": True},
        "como_aplicarlo": {"type": "string", "nullable": True},
        "tema": {"type": "string", "enum": list(TEMAS)},
        "confianza": {"type": "string", "enum": ["alta", "baja"]},
    },
    "required": ["titulo", "resumen", "por_que_importa", "tema", "confianza"],
    "propertyOrdering": [
        "titulo", "resumen", "por_que_importa",
        "dato_concreto", "como_aplicarlo", "tema", "confianza",
    ],
}

# Un ensayo es más largo que el lead de una nota y no está escrito en pirámide
# invertida: la tesis puede aparecer al final. Se le manda mucho más texto.
LARGO_ENSAYO = 12000


def resumir_ensayo(cliente, fuente, asunto, texto):
    """Resume el cuerpo del correo de una fuente de tipo `autor`.

    Devuelve (item_parcial, incidencia). El item parcial trae titulo, resumen y
    confianza; el orquestador le agrega id, fuente, categoría y URL.
    """
    limpio = (texto or "").strip()
    if len(limpio) < 300:
        return None, f"el correo traía solo {len(limpio)} caracteres de texto"

    entrada = f"Fuente: {fuente}\nAsunto: {asunto}\n\nTexto:\n{limpio[:LARGO_ENSAYO]}"
    datos, incidencia = cliente.llamar(
        INSTRUCCION_ENSAYO,
        entrada,
        ESQUEMA_ENSAYO,
        f"ensayo · {fuente}",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return None, incidencia
    return datos, None


# ─────────────────────────────────────────────────────────────────────────────
# 2c. Resumir un compendio (fuentes de tipo `compendio`)
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_COMPENDIO = """\
Este correo trae VARIAS noticias, completas, adentro. No enlaza a ningún lado:
el texto que estás leyendo es todo lo que hay.

Tu trabajo es separarlo en noticias individuales y resumir cada una.

Sacá a lo sumo {maximo}, las más importantes. Si el correo desarrolla tres
notas de verdad y cierra con seis titulares sueltos de una línea, esas seis no
son noticias: no hay con qué resumirlas. Dejalas afuera.

Escribís para alguien que no viene siguiendo el tema, en español, y valen las
mismas reglas de siempre:

  · identificá a cada persona, institución o empresa la primera vez que la
    nombrás: no "el TSE dijo", sino "el Tribunal Supremo de Elecciones, el
    órgano que organiza las elecciones en Costa Rica, dijo"
  · dejá clarísimo quién le hizo qué a quién
  · nada de adjetivos que el texto no sostenga con un hecho
  · no copies frases: reformulá

Por cada noticia:

  titulo           el de la noticia, no el asunto del correo
  resumen          dos o tres frases: qué pasó, con contexto suficiente
  por_que_importa  UNA frase: qué cambia porque esto pasó. Si la respuesta
                   honesta es "poco", decilo
  dato_concreto    la cifra, la fecha, el nombre exacto. null si no está
  como_aplicarlo   casi siempre null en noticias generales. Solo si de verdad
                   hay algo que alguien deba hacer (un trámite, una fecha
                   límite, un plazo que vence)
  tema             exactamente uno de: regulacion, agentes, modelos,
                   herramientas, seguridad, negocio, trabajo, otro.
                   En noticias generales lo más común es `otro`; usá
                   `regulacion` para leyes y fallos, `negocio` para economía
                   y empresas, `trabajo` para empleo y condiciones laborales

Todo esto se publica en un sitio público: se escribe sobre la noticia, nunca
sobre quien la va a leer.
"""

ESQUEMA_COMPENDIO = {
    "type": "object",
    "properties": {
        "noticias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "resumen": {"type": "string"},
                    "por_que_importa": {"type": "string"},
                    "dato_concreto": {"type": "string", "nullable": True},
                    "como_aplicarlo": {"type": "string", "nullable": True},
                    "tema": {"type": "string", "enum": list(TEMAS)},
                },
                "required": ["titulo", "resumen", "por_que_importa", "tema"],
                "propertyOrdering": [
                    "titulo", "resumen", "por_que_importa",
                    "dato_concreto", "como_aplicarlo", "tema",
                ],
            },
        }
    },
    "required": ["noticias"],
}


def resumir_compendio(cliente, fuente, asunto, texto, maximo=NOTICIAS_POR_CORREO):
    """Saca varias noticias del cuerpo de un correo que las trae completas.

    El tercer tipo de fuente, y el que faltaba. `agregador` enlaza a terceros,
    `autor` trae un ensayo, `compendio` trae varias noticias adentro y no
    enlaza a ningún lado: medido sobre No Pasa Nada: 8.113 caracteres de
    contenido y ocho links, todos de tracking sin texto de ancla útil.

    Tratarlo como agregador lo borraba del feed entero: el paso de elegir
    links no encontraba ninguna noticia, porque efectivamente no hay ninguna
    que enlazar.

    Devuelve (noticias, incidencia).
    """
    limpio = (texto or "").strip()
    if len(limpio) < 500:
        return [], f"el correo traía solo {len(limpio)} caracteres de texto"

    datos, incidencia = cliente.llamar(
        INSTRUCCION_COMPENDIO.format(maximo=maximo),
        f"Fuente: {fuente}\nAsunto: {asunto}\n\nTexto:\n{limpio[:LARGO_ENSAYO]}",
        ESQUEMA_COMPENDIO,
        f"compendio · {fuente}",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return [], incidencia

    noticias = [n for n in datos.get("noticias", []) if (n.get("titulo") or "").strip()]
    aviso = None
    if len(noticias) > maximo:
        aviso = f"el modelo devolvió {len(noticias)} noticias pese al tope de {maximo}"
        noticias = noticias[:maximo]

    return noticias, aviso


# ─────────────────────────────────────────────────────────────────────────────
# 3. Deduplicar entre fuentes
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_DEDUPLICAR = """\
Varias newsletters cubren las mismas noticias el mismo día. Agrupá las que
cuentan EL MISMO HECHO.

Es el mismo hecho:
  "OpenAI lanzó GPT-5.2" en The Rundown y "OpenAI ships GPT-5.2" en TLDR

NO es el mismo hecho:
  "OpenAI lanzó GPT-5.2" y "Anthropic responde con Claude X"
      → mismo tema, hechos distintos. Van separados.
  el anuncio de un lanzamiento y un análisis de sus consecuencias
      → el segundo aporta algo que el primero no tiene. Van separados.
  dos noticias sobre la misma empresa en la misma semana
      → la empresa no es el hecho.

Ante la duda, NO agrupes. Perder una duplicación es barato: la lectora ve dos
resúmenes parecidos y sigue. Fusionar dos noticias distintas borra una del feed
y ella nunca se entera de que existió.

Por cada grupo elegí un item principal, con este orden de preferencia:
  1. el que tenga confianza `alta`
  2. si empatan, el resumen que dé más detalle concreto

Escribí primero por qué son el mismo hecho, y después listá los ids. Si no
encontrás ninguna duplicación, devolvé una lista vacía: es un resultado
perfectamente normal, no lo fuerces.
"""

ESQUEMA_DEDUPLICAR = {
    "type": "object",
    "properties": {
        "grupos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "por_que": {
                        "type": "string",
                        "description": "cuál es el hecho compartido, en una frase",
                    },
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "id_principal": {"type": "string"},
                },
                "required": ["por_que", "ids", "id_principal"],
                # El razonamiento va primero a propósito: el modelo genera en
                # este orden, así que escribir el porqué antes de listar los ids
                # lo obliga a justificar antes de decidir, no al revés.
                "propertyOrdering": ["por_que", "ids", "id_principal"],
            },
        }
    },
    "required": ["grupos"],
}


def deduplicar(cliente, items):
    """Agrupa items que cuentan la misma noticia. Devuelve (grupos, incidencia)."""
    if len(items) < 2:
        return [], None

    datos, incidencia = cliente.llamar(
        INSTRUCCION_DEDUPLICAR,
        tabla_de_items(items),
        ESQUEMA_DEDUPLICAR,
        f"deduplicar · {len(items)} items",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return [], incidencia

    conocidos = {item["id"] for item in items}
    grupos, descartados = [], 0

    for grupo in datos.get("grupos", []):
        ids = [i for i in grupo.get("ids", []) if i in conocidos]
        if len(ids) < 2:
            # Un grupo de uno no es un grupo. Pasa cuando el modelo alucina un
            # id: se cae solo al validar, y preferimos perderlo a fusionar mal.
            descartados += 1
            continue

        principal = grupo.get("id_principal")
        if principal not in ids:
            principal = ids[0]

        grupos.append({"ids": ids, "id_principal": principal, "por_que": grupo.get("por_que", "")})

    aviso = f"se descartaron {descartados} grupo(s) con ids inválidos" if descartados else None
    return grupos, aviso


# ─────────────────────────────────────────────────────────────────────────────
# 4. Hilos: piezas que se conectan entre sí
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_HILOS = """\
Buscá conexiones entre noticias distintas de esta misma tanda.

Esto NO es deduplicación. Los duplicados ya se sacaron. Acá buscás piezas que
cuentan hechos DIFERENTES pero que juntas dicen algo que ninguna dice sola.

Tipos de conexión que valen:

  continuacion  una avanza sobre lo que la otra empezó
                ("sale el modelo" + "los primeros que lo probaron dicen que…")
  tension       apuntan a direcciones opuestas
                ("la IA reemplaza analistas" + "esta empresa volvió a contratar")
  patron        tres o más señales del mismo movimiento de fondo
                (tres empresas distintas haciendo lo mismo la misma semana)
  causa_efecto  una explica por qué pasó la otra

Escribí primero qué dice el conjunto que no dice ninguna pieza sola, y después
listá los ids.

Reglas:
  · Una conexión floja es peor que ninguna. "Ambas son sobre IA" NO es una
    conexión: es la categoría del feed.
  · Cada hilo lleva entre 2 y 4 piezas.
  · Un item puede estar en más de un hilo.
  · Si no hay conexiones reales en esta tanda, devolvé lista vacía. Es normal
    y es la respuesta correcta más seguido de lo que parece.
"""

ESQUEMA_HILOS = {
    "type": "object",
    "properties": {
        "hilos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "que_dice_el_conjunto": {
                        "type": "string",
                        "description": "lo que se ve al juntarlas y no antes, en una o dos frases",
                    },
                    "tipo": {
                        "type": "string",
                        "enum": ["continuacion", "tension", "patron", "causa_efecto"],
                    },
                    "titulo": {"type": "string", "description": "nombre corto del hilo"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["que_dice_el_conjunto", "tipo", "titulo", "ids"],
                "propertyOrdering": ["que_dice_el_conjunto", "tipo", "titulo", "ids"],
            },
        }
    },
    "required": ["hilos"],
}


def tejer_hilos(cliente, items):
    """Encuentra conexiones entre noticias distintas. Devuelve (hilos, incidencia).

    Los hilos son públicos: son metadato editorial sobre noticias públicas y no
    revelan nada de la lectora. Distintos de las marcas de "pieza conectada" que
    hace ella a mano, que viven en localStorage y son privadas (spec.md §7).
    """
    if len(items) < 3:
        return [], None

    datos, incidencia = cliente.llamar(
        INSTRUCCION_HILOS,
        tabla_de_items(items),
        ESQUEMA_HILOS,
        f"hilos · {len(items)} items",
        TEMPERATURA_HILOS,
    )
    if incidencia:
        return [], incidencia

    conocidos = {item["id"] for item in items}
    hilos, descartados = [], 0

    for hilo in datos.get("hilos", []):
        ids = [i for i in hilo.get("ids", []) if i in conocidos]
        if len(ids) < 2:
            descartados += 1
            continue
        hilos.append(
            {
                "titulo": hilo.get("titulo", "").strip() or "Sin título",
                "tipo": hilo.get("tipo", "patron"),
                "que_dice_el_conjunto": hilo.get("que_dice_el_conjunto", ""),
                "ids": ids,
            }
        )

    aviso = f"se descartaron {descartados} hilo(s) con ids inválidos" if descartados else None
    return hilos, aviso


# ─────────────────────────────────────────────────────────────────────────────
# 6. Cursos y eventos
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCCION_CURSOS = """\
Extraé de este correo los cursos, talleres, webinars o eventos que anuncia.

Solo lo que tenga fecha o inscripción abierta. Un correo que solo habla de un
tema sin anunciar nada concreto NO tiene cursos: devolvé lista vacía. No
fuerces. Es normal que un correo promocional no anuncie ningún evento real.

Por cada uno:

  titulo        el nombre del curso o evento, no el asunto del correo
  url           el link de inscripción o de información
  fecha_evento  en formato YYYY-MM-DD, o null si el correo no la dice.
                NO la deduzcas ni la estimes. "Next week" es null.
  gratis        true si dice que es gratis, false si menciona un precio,
                null si no lo aclara

Esta sección del dashboard no lleva resumen ni análisis: es una lista de "esto
arranca pronto". No opines sobre si vale la pena.
"""

ESQUEMA_CURSOS = {
    "type": "object",
    "properties": {
        "cursos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "url": {"type": "string"},
                    "fecha_evento": {"type": "string", "nullable": True},
                    "gratis": {"type": "boolean", "nullable": True},
                },
                "required": ["titulo", "url"],
                "propertyOrdering": ["titulo", "url", "fecha_evento", "gratis"],
            },
        }
    },
    "required": ["cursos"],
}


def extraer_cursos(cliente, fuente, asunto, texto, candidatos):
    """Saca los cursos y eventos anunciados en un correo. Devuelve (cursos, incidencia)."""
    lineas = [f"Fuente: {fuente}", f"Asunto: {asunto}", "", "Texto del correo:"]
    lineas.append((texto or "").strip()[:LARGO_ARTICULO] or "(vacío)")

    if candidatos:
        lineas += ["", "Links del correo:"]
        lineas += [f'  {c["url"]}:  "{c.get("contexto", "")}"' for c in candidatos[:30]]

    datos, incidencia = cliente.llamar(
        INSTRUCCION_CURSOS,
        "\n".join(lineas),
        ESQUEMA_CURSOS,
        f"cursos · {fuente}",
        TEMPERATURA_CRITERIO,
    )
    if incidencia:
        return [], incidencia

    cursos = []
    for curso in datos.get("cursos", []):
        url = (curso.get("url") or "").strip()
        titulo = (curso.get("titulo") or "").strip()
        if not url.startswith("http") or not titulo:
            continue
        cursos.append(
            {
                "titulo": titulo,
                "url": url,
                "fuente": fuente,
                "fecha_evento": curso.get("fecha_evento"),
                "gratis": curso.get("gratis"),
            }
        )

    return cursos, None


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliares
# ─────────────────────────────────────────────────────────────────────────────


def tabla_de_items(items, con_confianza=True):
    """Formatea items para el modelo. Texto delimitado, no JSON.

    A propósito: el JSON escapa los saltos de línea y convierte un resumen en
    una tira ilegible. Cuando algo sale mal, esto se puede leer de un vistazo.
    """
    bloques = []
    for item in items:
        partes = [
            f'--- id: {item["id"]}',
            f'fuente: {item.get("fuente", "?")}',
            f'titulo: {item.get("titulo", "")}',
            f'categoria: {item.get("categoria", "?")}',
        ]
        if con_confianza:
            partes.append(f'confianza: {item.get("confianza", "?")}')
        partes.append(f'resumen: {item.get("resumen", "")}')
        bloques.append("\n".join(partes))
    return "\n\n".join(bloques)


def _filtrar_por_id(filas, conocidos):
    """Descarta las filas cuyo id no estaba en lo que mandamos. Cuenta cuántas."""
    validas, inventadas = [], 0
    vistos = set()
    for fila in filas:
        item_id = fila.get("id")
        if item_id not in conocidos or item_id in vistos:
            inventadas += 1
            continue
        vistos.add(item_id)
        validas.append(fila)
    return validas, inventadas


def volcar(datos):
    """JSON legible, para depurar en la terminal."""
    return json.dumps(datos, ensure_ascii=False, indent=2)
