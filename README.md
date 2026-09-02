# Puzzle Feed

> 🔗 **[Ver el tablero en vivo ↗](https://factorsteph.github.io/puzzle-feed/)**
>
> 🇬🇧 **[English version below ↓](#english)**
>
> 📖 **[¿Alguna palabra no te suena? Glosario sin jerga ↓](#glosario-sin-jerga)**

Agente personal que lee mis newsletters de AI, tecnología y emprendimiento desde Gmail,
separa lo que importa del ruido, y me arma un tablero de noticias cada dos días.

No resume el correo: **busca el artículo original, lo lee, y resume desde ahí**. Después
junta las noticias que cuentan lo mismo y conecta las que juntas dicen algo que ninguna
dice sola.

---

## Por qué existe

Mi correo se había vuelto un archivo muerto. Acumulaba newsletters sin leer y, para
cuando tenía tiempo, la noticia ya era vieja, o tenía tantas pendientes que me abrumaba y acto seguido cerraba el correo. Cinco boletines diarios traen entre
**10 y 18 noticias legítimas cada uno**: el problema nunca fue encontrar información, fue que no existía un ciclo donde lo que entra se convierta en algo mío antes de
volverse obsoleto o too much.

Esto reemplaza ese ciclo: abro un tablero ya procesado en vez de revisar seis correos.

## Cómo se ve

El tablero es un rompecabezas. Cada noticia es una pieza que empieza apagada; al marcarla leída se enciende con el color de su sección, y sale del tablero. Se avanza
leyendo, no scrolleando.

Cada pieza responde tres cosas que un resumen suelto no responde:

| | |
|---|---|
| **Por qué importa** | qué cambia en el mundo real o por qué esto pasó |
| **El dato** | la cifra, el nombre exacto, el cambio puntual. O nada, si el texto no lo trae |
| **Por dónde se empieza** | el primer paso concreto para usar esto hoy |

El estándar del resumen está escrito en el prompt: *si después de leerlo hay que abrir el link para entender de qué se trata, el resumen falló.*

## Cómo funciona

```
1. Ingesta      IMAP en solo-lectura: correos aún sin procesar,
                de remitentes admitidos
2. Filtrar      Descartar el resto. Cada descarte dice su motivo

   ── según el tipo de fuente ──────────────────────────────────
3. Agregador    Extraer links → [IA] elegir los N más importantes →
                descargar el artículo → [IA] resumir
3'. Autor       El correo ES el ensayo. Se resume su cuerpo
3''. Compendio  El correo trae varias noticias adentro. Se separan

   ── sobre todo lo recolectado ────────────────────────────────
4. Deduplicar   [IA] Misma noticia en dos fuentes = una pieza
5. Hilar        [IA] Piezas distintas que juntas dicen algo
6. Publicar     → feed.json → el tablero
7. Marcar       Etiquetar, marcar leído y archivar en Gmail
```

Los pasos que necesitan criterio usan un modelo. Los que tienen respuesta verificable
(bajar un HTML, limpiarlo, asignar una categoría que ya está declarada en la
configuración) son funciones. No se despliega un agente para algo que se resuelve
comparando.

## Lo que hace este proyecto distinto de un resumidor de correos

**Tres tipos de fuente, porque los datos lo exigieron.** Un boletín de Nerd Processor
trae 5.500 caracteres de ensayo y nueve links, *todos* de rastreo hacia posts
anteriores: ninguno apunta al texto que estás leyendo. Tratado como agregador, el feed
traía sus posts viejos y perdía el del día. No Pasa Nada trae ocho mil caracteres con
varias noticias adentro y desaparecía del feed entero. Ahora cada fuente declara si
enlaza, si es un ensayo, o si es un compendio.

**El modelo jerarquiza, no clasifica.** Preguntarle "¿esto es noticia?" daba 50 piezas
de 6 correos, porque en un boletín de tecnología casi todo lo es. Preguntarle "¿cuáles
son las 6 más importantes?" da 19. El criterio de importancia está escrito, incluido lo
que *no* cuenta: movimientos de ejecutivos, rondas de inversión, contratos con gobiernos.

**Nada se descarta en silencio.** Todo rechazo, descarga fallida, muro de pago o
respuesta malformada dice su motivo: en el log y, si afecta a una pieza, en el tablero.
Un tercio de los artículos no se puede bajar (bloqueos, muros de pago, un servidor que
devuelve Markdown); esas piezas van a una caja aparte, marcadas, en vez de competir de
igual a igual con las que sí se leyeron completas.

**Escribir en el buzón es opt-in.** El pipeline abre Gmail en solo-lectura y usa
`BODY.PEEK` para no marcar nada como leído sin querer. Solo con `--marcar` etiqueta y
archiva, y `--simular` imprime el plan exacto sin ejecutarlo.

## Privacidad

GitHub Pages sirve archivos estáticos: no hay servidor que decida quién ve qué, así que
cualquiera puede abrir el `.json` directo. **Si el dato llega al navegador, es público**,
y esconderlo con CSS o en un campo que el frontend ignora es maquillaje, no privacidad.
De ahí sale la regla del proyecto: lo que no pueda ser público, no se escribe en
`feed.json`.

**El archivo público se arma enumerando lo permitido**, campo por campo, y todo lo que no
esté en esa lista se descarta. Es al revés de lo intuitivo (sería más corto enumerar lo
que se excluye) y a propósito: enumerando exclusiones, un campo nuevo del pipeline se
publicaría solo por olvidar agregarlo a la lista. Ya se ganó el sueldo: un campo interno
que agregué para uso del propio pipeline nunca llegó al archivo, sin que nadie tuviera
que acordarse de filtrarlo.

La configuración también está partida: `config/fuentes.yml` es público y solo lleva
suscripciones públicas. Lo que revela algo de mí (bancos, trámites) vive en archivos locales que git ignora. Un remitente es un dato personal aunque no sea tuyo como tal.

Ver [`spec.md`](spec.md) §7 para el detalle completo.

## Estado

| Bloque | Qué incluye | Estado |
|---|---|---|
| 1. Spec | Entrevista de diseño, escaneo del buzón, decisiones cerradas | ✅ |
| 2. Ingesta y filtro | IMAP, remitentes admitidos, reporte de clasificación | ✅ |
| 3a. Links y artículos | Extracción de links, descarga y limpieza del texto | ✅ |
| 3b. Análisis | Elegir, resumir, clasificar por tema, deduplicar, hilar | ✅ |
| 4. Salidas | `feed.json`, etiquetado y archivado en Gmail | ✅ |
| 5. Tablero | HTML/JS estático leyendo `feed.json` | ✅ |
| 6. Automatización | GitHub Actions cada 2 días | ⬜ |

Medido el 2026-09-01 sobre el buzón real, con la ventana de 2 días que es la frecuencia
del feed: **25 piezas → 15 principales + 10 breves, 17 llamadas al modelo, 264 s, cero
fallos.**

## Decisiones de diseño

Las 23 decisiones cerradas, cada una con lo que la motivó, están en
[`spec.md`](spec.md) §3. Las tres que más moldearon el proyecto:

**El filtro admite en vez de excluir, al revés de mi job-alert-agent.** Allá un buen
empleo descartado se perdía para siempre, así que el filtro fallaba hacia dejar pasar.
Acá se invierte: una noticia perdida reaparece mañana en otra newsletter, pero el ruido
mata el objetivo entero de revisar el feed en menos de diez minutos.

**El filtro es por remitente exacto, nunca por dominio.** `therundown.ai` manda desde
cuatro direcciones y solo dos traen noticias; las otras venden o promocionan cursos. Filtrar por
dominio metería la mitad de la basura junto con la señal. Esto no estaba en el spec
original: apareció mirando el buzón real.

**El spec se corrige con datos, no con opiniones.** Va por su tercera versión, y las
tres veces la corrigieron los hechos: el primer escaneo del buzón contradijo supuestos
de la v1 (§11), y la primera corrida del pipeline contradijo supuestos de la v2 (§15).
El volumen real resultó ser el triple del estimado; la latencia del modelo, un tercio.

## Setup

### 1. Python

Requiere Python 3.10 o superior. En WSL/Ubuntu:

```bash
python3 --version
```

### 2. Entorno virtual y dependencias

El proyecto vive en OneDrive, así que el entorno virtual va **fuera** de la carpeta: un
`.venv` adentro serían miles de archivos sincronizándose sin ninguna razón.

```bash
python3 -m venv ~/.venvs/puzzlefeed
```

```bash
~/.venvs/puzzlefeed/bin/pip install -r requirements.txt
```

> En WSL/Ubuntu el comando es `python3`, no `python`. Además, Ubuntu 24.04 bloquea
> `pip install` contra el Python del sistema, así que el entorno virtual no es opcional.

### 3. Credenciales

```bash
cp .env.example .env
```

Llenalo con tu correo, un **app password de Google** (16 caracteres, no tu contraseña
normal) y una **API key de Google AI Studio** (capa gratuita, no pide tarjeta).

El app password se genera en: *Cuenta de Google → Seguridad → Verificación en 2 pasos →
Contraseñas de aplicaciones*. Google lo muestra una sola vez. Generá uno nuevo para este
proyecto en vez de reusar otro: si hay que revocarlo, no se cae nada más.

## Uso

```bash
~/.venvs/puzzlefeed/bin/python pipeline/main.py --dias 2
```

Procesa y reporta en la terminal. **No escribe nada**: ni archivos, ni correo, ni Gmail.

| bandera | qué hace |
|---|---|
| `--dias N` | cuántos días hacia atrás mirar |
| `--por-correo N` | cuántas noticias tomar de cada correo (6 por defecto) |
| `--sin-llm` | solo ingesta, sin gastar modelo |
| `--publicar` | escribir `docs/feed.json` |
| `--marcar` | etiquetar, marcar leído y archivar en Gmail |
| `--simular` | con `--marcar`: mostrar qué haría, sin hacerlo |

La primera vez que uses `--marcar`, usalo con `--simular`.

## Glosario, sin jerga

Todo lo de arriba usa palabras que en tecnología significan algo muy concreto y
en la vida diaria no significan nada. Acá va cada una en dos pasos: primero qué
es, en la frase más simple que encontré, y después con qué se parece.

No hace falta leer esto para entender el proyecto; está por si en algún momento
una palabra estorba o confunde.

### Las piezas del correo

**IMAP**
Un conjunto de reglas que le permite a un programa conectarse al servidor donde
vive tu correo y leer los mensajes, sin abrir Gmail en el navegador. Gmail es la
ventanilla; IMAP es la puerta de servicio.

*Funciona como la llave de la bodega: deja entrar a mirar los sobres sin sacarlos
de su lugar. Este proyecto además entra con la puerta trabada en modo mirar, así
que aunque el código tuviera un error, el servidor no lo dejaría escribir nada.*

**App password**
Una contraseña de 16 caracteres que Google genera aparte, para que un programa
entre a tu cuenta sin usar tu contraseña real ni tu segundo factor. Se puede
anular en cualquier momento sin tocar el resto de la cuenta.

*Es una llave de repuesto que abre una sola puerta. Si se pierde, se cambia esa
cerradura y todo lo demás sigue igual. Por eso conviene generar una por proyecto
en vez de reusar la misma.*

**SMTP**
Las reglas para el camino de vuelta: en vez de leer el buzón, permiten dejar algo
en él. IMAP saca, SMTP mete.

### Las piezas del procesamiento

**Pipeline**
Un programa partido en pasos, donde cada paso hace una sola cosa y le entrega su
resultado al siguiente. En vez de un bloque grande que hace todo, hay siete
pedazos ordenados.

*Es una línea de producción: una estación separa el correo que sirve, otra baja el
artículo, otra lo resume. Que estén separadas es lo que permite saber en cuál se
trabó algo cuando algo se traba.*

**LLM (el modelo)**
Un programa entrenado con muchísimo texto que, dado un texto de entrada, predice
qué texto tiene sentido devolver. No busca en internet ni recuerda conversaciones
anteriores: solo trabaja con lo que se le manda en ese momento.

*Es un asistente que lee rapidísimo y tiene criterio, pero que olvida todo entre
una consulta y la siguiente. Cada vez que se le pregunta algo hay que darle el
contexto completo, como si fuera su primer día.*

**Prompt**
El texto que se le manda al modelo, que incluye tanto la instrucción como los
datos sobre los que tiene que trabajar. Es literalmente el programa: se
"programa" un modelo escribiéndole en español.

*Es la instrucción de trabajo que se le entrega a ese asistente, y se parece
bastante a redactar un procedimiento. Si está mal escrita, el resultado sale mal
y la culpa es de quien la escribió. Buena parte de este proyecto fue afinar esas
instrucciones contra resultados reales.*

**Token**
La unidad en que el modelo mide el texto: más o menos un pedazo de palabra. Hay
un tope de cuántos puede devolver en una sola respuesta.

*Importa por lo mismo que importa el tamaño de una hoja: si se le pide demasiado
de una vez, la respuesta se corta a la mitad y se pierde el trabajo entero de esa
consulta.*

**Deduplicar**
Detectar que dos registros distintos se refieren a la misma cosa, y dejar uno
solo anotando que había más.

*Es lo que se hace cuando tres proveedores mandan la misma factura: se archiva una
y se anota que llegó por tres lados. Acá pasa cuando dos newsletters cuentan la
misma noticia el mismo día.*

**Fallback**
El camino alternativo que toma un programa cuando el principal falla, decidido de
antemano en vez de improvisado.

*Es el plan B, anotado. Si no se pudo bajar el artículo original, se usa el resumen
que venía en el correo y queda dicho de dónde salió. Lo que lo hace un plan B y no
un parche es que está declarado y se avisa cuando se usa.*

**Scraping**
Bajar una página web y sacarle el texto útil, descartando menús, publicidad y todo
lo que no es el contenido.

*Es ir a la fuente a leer el documento completo en vez de fiarse del resumen que
hizo otra persona. Es el motivo por el que este agente baja los artículos en lugar
de resumir el correo.*

### Las piezas del archivo

**JSON**
Una forma de escribir datos en un archivo de texto para que un programa los lea
sin ambigüedad: cada dato tiene un nombre y un valor, siempre con la misma
estructura.

*Es una ficha con casillas fijas: siempre las mismas, siempre en el mismo orden,
para que quien la lea sepa dónde buscar cada cosa. `feed.json` es la ficha con
todas las noticias procesadas.*

**Repositorio (repo)**
Una carpeta que además guarda el historial completo de sí misma: cada versión de
cada archivo, con la fecha, quién la cambió y por qué.

*Es el archivo maestro del proyecto. Se puede volver a cualquier día anterior y ver
exactamente cómo estaba todo.*

**Commit**
Una versión guardada del proyecto, con un mensaje que explica qué cambió.

*Es una entrada en la bitácora de ese archivo maestro. Este proyecto los escribe
largos a propósito, porque el porqué de una decisión se olvida mucho antes que el
qué.*

**`.gitignore`**
Un archivo con la lista de nombres y patrones que el sistema de versiones debe
ignorar. Lo que aparece ahí nunca se guarda ni se publica, aunque esté en la
carpeta.

*Es la lista de lo que no sale de la oficina, escrita de antemano. No se decide
documento por documento en el momento: se declara una vez qué clase de cosas nunca
se publican, y el sistema las retiene solo.*

### Las piezas de la publicación

**Sitio estático**
Una página web que es un archivo ya escrito, que se entrega igual a todo el mundo.
No hay ningún programa corriendo del otro lado que decida qué mostrarle a quién.

*Es un afiche pegado en la pared: quien pase, ve lo mismo. De ahí sale la regla más
importante de este proyecto, la de la sección de privacidad. Si el dato está en el
afiche es público, y taparlo con un papelito no lo vuelve privado.*

**GitHub Pages**
Un servicio gratuito de GitHub que toma los archivos de un repositorio y los sirve
como sitio web, con una dirección propia.

*Es la pared donde se pega ese afiche.*

**Cron**
Un programador de tareas: se le dice "esto, cada dos días a tal hora" y lo ejecuta
sin que nadie intervenga.

*Es el despertador del sistema.*

**GitHub Actions**
El servicio de GitHub que corre programas cuando pasa algo: un cambio en el
repositorio, o una hora del calendario.

*Es el empleado que atiende ese despertador. Corre el proceso, guarda el resultado
y se vuelve a dormir.*

**API key**
Una cadena de caracteres que identifica a un programa ante un servicio de afuera,
para saber quién está pidiendo y cuánto lleva usado.

*Es el gafete del agente. Sin gafete no lo dejan entrar, y por eso vive en un
archivo que nunca se publica.*

## Licencia

Proyecto personal, abierto para que se lea. Las credenciales y la configuración privada
no están acá y nunca van a estar.

<br>

---

<a id="english"></a>

# 🇬🇧 English

> 🔗 **[See the live board ↗](https://factorsteph.github.io/puzzle-feed/)**
>
> 🇨🇷 **[Volver al español ↑](#puzzle-feed)**. Spanish is the original; this is a
> translation. The spec, the code and its comments are in Spanish.

A personal agent that reads my AI, tech and entrepreneurship newsletters from Gmail,
separates signal from noise, and builds me a news board every two days.

It doesn't summarize the email: **it finds the original article, reads it, and
summarizes from there**. Then it merges the stories that say the same thing and links
the ones that together say something neither says alone.

### Why it exists

My inbox had become a dead archive. Newsletters piled up unread and by the time I had a
moment the news was stale, or there were so many waiting that I got overwhelmed and
closed the tab. Five daily newsletters carry **10 to 18 legitimate stories each**:
finding information was never the problem. The problem was that nothing turned what came
in into something of my own before it went obsolete, or before it became too much.

### What it looks like

The board is a jigsaw puzzle. Every story is a piece that starts unlit; marking it read
lights it up in its section's color and removes it from the board. You make progress by
reading, not by scrolling.

Each piece answers three things a plain summary doesn't:

| | |
|---|---|
| **Why it matters** | what changes in the real world, or why this happened |
| **The fact** | the number, the exact name, the specific change. Or nothing, if the text doesn't have it |
| **Where to start** | the first concrete step to use this today |

The standard for a summary is written into the prompt: *if you have to open the link to
understand what this is about, the summary failed.*

### How it works

```
1. Ingest       Read-only IMAP: not-yet-processed emails
                from allowed senders
2. Filter       Discard the rest. Every discard states its reason

   ── by source type ───────────────────────────────────────────
3. Aggregator   Extract links → [AI] pick the N most important →
                download the article → [AI] summarize
3'. Author      The email IS the essay. Summarize its body
3''. Digest     The email carries several stories inside. Split them

   ── across everything collected ──────────────────────────────
4. Deduplicate  [AI] Same story in two sources = one piece
5. Thread       [AI] Distinct pieces that together say something
6. Publish      → feed.json → the board
7. Mark         Label, mark read and archive in Gmail
```

Steps that need judgment use a model. Steps with a verifiable answer (fetching HTML,
cleaning it, assigning a category already declared in config) are functions. You don't
deploy an agent for something a comparison solves.

### What makes this different from an email summarizer

**Three source types, because the data demanded it.** A Nerd Processor issue carries
5,500 characters of essay and nine links, *all* tracking redirects to earlier posts:
none point at the text you're reading. Treated as an aggregator, the feed pulled its old
posts and lost the day's. No Pasa Nada carries eight thousand characters with several
stories inside and vanished from the feed entirely. Now each source declares whether it
links out, is an essay, or is a digest.

**The model ranks, it doesn't classify.** Asking "is this news?" returned 50 pieces from
6 emails, because in a tech newsletter almost everything is. Asking "which are the 6
most important?" returns 19. The importance criteria are written down, including what
does *not* count: executive reshuffles, funding rounds, government contracts.

**Nothing is discarded silently.** Every rejection, failed fetch, paywall or malformed
response states its reason: in the log and, if it affects a piece, on the board. A
third of articles can't be fetched (blocks, paywalls, a server returning Markdown);
those pieces go to a separate box, labeled, instead of competing head-to-head with the
ones that were read in full.

**Writing to the inbox is opt-in.** The pipeline opens Gmail read-only and uses
`BODY.PEEK` so nothing gets marked as read by accident. Only `--marcar` labels and
archives, and `--simular` prints the exact plan without running it.

### Privacy

GitHub Pages serves static files: there's no server deciding who sees what, so anyone
can open the `.json` directly. **If the data reaches the browser, it's public**, and
hiding it with CSS or in a field the frontend ignores is makeup, not privacy. Hence the
project's rule: whatever can't be public doesn't get written to `feed.json`.

**The public file is built by listing what's allowed**, field by field, and anything not
on that list is dropped. It's the opposite of the intuitive approach (listing exclusions
would be shorter) and deliberately so: listing exclusions, a new pipeline field would
publish itself just by someone forgetting to add it. It has already earned its keep: an
internal field I added for the pipeline's own use never reached the file, without anyone
having to remember to filter it.

Configuration is split too: `config/fuentes.yml` is public and only carries public
subscriptions. Anything that reveals something about me (banks, paperwork) lives in local files git
ignores. A sender is personal data even when it
isn't yours.

### Status

| Block | What it covers | Status |
|---|---|---|
| 1. Spec | Design interview, inbox scan, closed decisions | ✅ |
| 2. Ingest and filter | IMAP, allowed senders, classification report | ✅ |
| 3a. Links and articles | Link extraction, fetching and text cleanup | ✅ |
| 3b. Analysis | Pick, summarize, classify by topic, deduplicate, thread | ✅ |
| 4. Outputs | `feed.json`, labeling and archiving in Gmail | ✅ |
| 5. Board | Static HTML/JS reading `feed.json` | ✅ |
| 6. Automation | GitHub Actions every 2 days | ⬜ |

Measured on 2026-09-01 against the real inbox, over the 2-day window that matches the
feed's cadence: **25 pieces → 15 main + 10 briefs, 17 model calls, 264 s, zero
failures.**

### Design decisions

All 23 closed decisions, each with what prompted it, are in [`spec.md`](spec.md) §3,
in Spanish. The three that shaped the project most:

**The filter allows rather than excludes, the opposite of my job-alert-agent.** There, a
good job discarded was lost forever, so the filter erred toward letting things through.
Here it inverts: a missed story reappears tomorrow in another newsletter, but noise
kills the whole point of reviewing the feed in under ten minutes.

**The filter matches exact senders, never domains.** `therundown.ai` sends from four
addresses and only two carry news; the others sell or promote courses. Filtering by domain would
pull in half the junk along with the signal. This wasn't in the original spec: it came
from looking at the actual inbox.

**The spec is corrected by data, not by opinion.** It's on its third version, and all
three times facts did the correcting: the first inbox scan contradicted v1's assumptions
(§11), and the pipeline's first run contradicted v2's (§15). Real volume turned out to
be triple the estimate; model latency, a third of it.

### Setup and usage

See the Spanish sections above: the commands are identical. Flags are in Spanish
because the whole codebase is: the project is written in the language its author thinks
in.

| flag | what it does |
|---|---|
| `--dias N` | how many days back to look |
| `--por-correo N` | how many stories to take per email (6 by default) |
| `--sin-llm` | ingest only, no model calls |
| `--publicar` | write `docs/feed.json` |
| `--marcar` | label, mark read and archive in Gmail |
| `--simular` | with `--marcar`: show what it would do, without doing it |

The first time you use `--marcar`, use it with `--simular`.
