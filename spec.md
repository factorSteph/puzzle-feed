# Spec — Puzzle Feed

> Versión 3 — actualizada el 2026-09-01 tras construir y correr el bloque 3b contra el
> buzón real. Cada versión la corrigieron los datos, no una discusión: ver §11 (lo que
> corrigió el escaneo del buzón) y §15 (lo que corrigió la primera corrida del pipeline).

## 1. Problem statement

Steph recibe correo de múltiples newsletters de AI/tech/emprendimiento/soporte a mujeres,
más spam de marketing/wellness/prediction markets. El volumen abruma (~356 correos sin
leer al momento del escaneo), hay contenido repetido entre fuentes, y para cuando tiene
tiempo de leer, la información ya perdió vigencia. No existe un ciclo donde la información
entrante se convierta en algo propio (reflexión, contenido) antes de volverse obsoleta.

## 2. Jobs to be done

- Cuando tengo tiempo libre, quiero abrir un solo lugar y ver lo que realmente me aplica,
  sin tener que triar 6 correos distintos.
- Cuando veo un item, quiero entender de inmediato qué cambia y qué haría falta para
  usarlo, sin tener que abrir el link.
- Cuando algo sí me sirve, quiero decidir yo cuándo pasarlo a Notion y desarrollarlo.
- Quiero sentir que reviso el feed en vez de "trabajar el inbox".
- Quiero que el dashboard sea público y sirva de portafolio, sin que nada mío quede
  expuesto en el camino.

## 3. Decisiones cerradas

Estas salieron de la entrevista de diseño. No se cambian sin volver a conversarlas.

| # | Decisión | Razón |
|---|---|---|
| D1 | El agente **nunca** escribe en Notion | El paso a Notion es criterio de Steph |
| D2 | Lectura de Gmail por **IMAP + app password** | Ya existe la credencial; `imaplib` es stdlib; un solo secret lee y escribe. Habilita el etiquetado (D8), que OAuth read-only bloquearía |
| D3 | Modelo: **Gemini, capa gratuita** vía REST | Costo cero real. El volumen (≈24 items cada 2 días) entra de sobra en los límites del free tier. Se acepta a conciencia que en capa gratuita Google puede usar el contenido para mejorar sus modelos: son newsletters públicas y resúmenes de ellas |
| D4 | **Sin banderas, el pipeline no escribe nada** | Ni archivos, ni Gmail. Procesa, reporta en la terminal y se va. Cada salida se pide explícitamente, y las que tocan algo de afuera tienen `--simular`. Un pipeline que escribe por defecto es un pipeline que un día escribe donde no debía |
| D5 | Filtro por **lista de remitentes admitidos**, exactos | Solo entra al feed lo que esté declarado. Inverso a job-alert-agent, a propósito: ver §9 |
| D6 | El dashboard es **público y sirve de portafolio** | Es la mitad del propósito del proyecto: no solo procesar el correo, también mostrar cómo se procesó. Por eso el repositorio es público y por eso §7 existe |
| D7 | El resumen se genera del **artículo original**, no del blurb del newsletter | El blurb ya viene editorializado; resumir un resumen sesgado duplica el sesgo |
| D8 | Estado en Gmail vía etiqueta **`PuzzleFeed/Procesado`** | Idempotencia sin base de datos, sin tocar la taxonomía existente |
| D9 | Frecuencia: **cada 2 días** | — |
| D10 | Gamificación tipo rompecabezas, **sin puntos ni rachas** | Las piezas se encienden al marcarlas leídas y salen del tablero. Se avanza leyendo, no scrolleando |
| D11 | En archivos públicos solo aparecen **suscripciones públicas** | Un remitente revela algo: a qué banco pertenecés, dónde te postulaste, qué médico usás. Ver §7.1 |
| D12 | Los correos que **entran al feed o a cursos** se marcan **leídos**; nada más se toca | Si el contenido ya está resumido en el dashboard, el correo no tiene por qué seguir en negrita. Los descartados y desconocidos quedan intactos: el agente no los leyó ni los resumió, así que no puede decir que vos ya los viste. Reversible: la etiqueta identifica exactamente cuáles tocó |
| D13 | El feed se agrupa **por semana** en el dashboard | Se revisa cada 2 días; si pasan varios sin abrirlo, una lista plana se vuelve inmanejable |
| D14 | **Hilos**: piezas distintas que juntas dicen algo. Públicos | Es la mecánica de rompecabezas aplicada al contenido. Son metadato editorial sobre noticias públicas: no revelan nada de quien lee. Solo dentro de una corrida — entre corridas sigue siendo supersesión y sigue fuera del MVP |
| D15 | Las fuentes son **`agregador`, `autor` o `compendio`** | Medido: Nerd Processor trae 5.500 caracteres de ensayo y nueve links, todos de tracking hacia posts anteriores; No Pasa Nada trae 8.100 caracteres con **varias** noticias y ocho links de tracking sin texto de ancla. Ninguno apunta al texto que estás leyendo. Para las dos, el correo **es** el artículo original y D7 se cumple mejor resumiéndolo que siguiendo links. Tratadas como agregador desaparecían del feed enteras, en silencio para la lectora. `autor` da un item por correo, `compendio` da varios |
| D16 | La **categoría sale de `fuentes.yml`**, no del modelo | §5 la mapea por fuente y §6 se la pedía al LLM: el spec se contradecía. Medido: con el modelo eligiendo, una nota de regulación llegada por The Rundown AI caía en "Noticias" y rompía el agrupamiento. Gana §5, por el principio que el propio §6 fija — lo verificable es una función |
| D17 | Un correo que **falla no se marca** como procesado | No detiene la corrida: se registra y se sigue. Pero marcarlo leído diría "ya lo viste" de algo que nadie resumió, que es justo lo que D12 prohíbe. Sin etiqueta, la corrida siguiente lo reintenta gratis; la ventana de `newer_than` lo saca del rango sola a los pocos días |
| D18 | El modelo **jerarquiza los links, no los clasifica** | Medido: preguntar "¿es noticia?" daba 50 items de 6 correos, porque en un newsletter de tecnología casi todo lo es. Preguntar "¿cuáles son las N más importantes?" da 19. El tope es un parámetro (`--por-correo`), no una constante: el día a día y vaciar backlog piden números distintos |
| D19 | Cada pieza lleva **`tema`** de una lista cerrada de ocho | La categoría viene de la fuente (D16) y cinco de las seis fuentes son de tecnología: medido, 64 de 66 piezas caían en "AI & Tech" y el agrupamiento no agrupaba nada. El tema parte la categoría por dentro. La lista es cerrada porque si el modelo los inventa, cada corrida arma secciones distintas y el tablero deja de ser reconocible. El orden en el dashboard es fijo —primero lo que cambia las reglas o el trabajo, al final lo que solo informa— para que no haya que reorientarse en cada corrida |
| D20 | Cada pieza lleva **por qué importa, el dato concreto y por dónde se empieza** | La vara: si después de leer el resumen hay que abrir el link para entender de qué se trata, el resumen falló. Sin esto el feed decía "existe un cambio de configuración que lo arregla" sin decir cuál, o "Tencent presentó" sin decir quién es Tencent. `dato_concreto` y `como_aplicarlo` pueden ser `null` — un null honesto vale más que un relleno |
| D21 | Las piezas que **no se pudieron leer del original** van aparte | Se resumen del blurb, que ya viene editorializado, y muchas veces no traen el dato que hace útil la noticia. Van a una tira propia ("Rapiditas") en vez de competir de igual a igual con lo que sí se leyó completo. No se borran: eso sería descartar en silencio |
| D22 | **Extiende D12**: los correos procesados se etiquetan por categoría y se **archivan** | Steph lo pidió el 2026-09-01. Cada correo recibe `PuzzleFeed/<Categoría>` más `PuzzleFeed/Procesado`, se marca leído y sale de la bandeja. En Gmail no hay carpetas —la bandeja es una etiqueta más— así que archivar es quitar `\Inbox`, y es reversible. Lo que D12 protegía sigue protegido: lo descartado, lo desconocido y lo que falló no se toca. Escribir en el buzón es opt-in (`--marcar`) y tiene `--simular`, que imprime el plan exacto sin ejecutarlo |
| D23 | El feed público se arma con **lista de campos permitidos** | Al revés de lo intuitivo: sería más corto enumerar lo que se excluye. Con lista de excluidos, un campo nuevo se publicaría solo por olvidar agregarlo; con lista de admitidos no sale hasta que alguien lo decida. Ya evitó una fuga real: el `uid` interno que se le agregó a los cursos para poder etiquetarlos nunca llegó al archivo |

## 4. Alcance del MVP

**Incluye:**

- Ingesta de las fuentes admitidas (ver `config/fuentes.yml`), sean agregadores
  o boletines de autor (D15).
- Descarte de todo remitente fuera de la lista de admitidos.
- Extracción de links reales desde el HTML del correo (siguiendo redirecciones de tracking).
- Descarga y extracción del artículo original; resumen generado sobre ese texto.
- Deduplicación semántica entre fuentes (misma noticia en Rundown y TLDR = un item).
- Hilos entre piezas distintas de la misma corrida (D14).
- Por item: resumen, categoría, tema, por qué importa, el dato concreto y por dónde se
  empieza.
- Sección aparte **"Free Courses Coming Up Next"** para anuncios de cursos y eventos.
- Dashboard web estático, público, desplegable en GitHub Pages.
- Mecánica de rompecabezas: las piezas se encienden al marcarlas leídas y salen del
  tablero (estado en `localStorage`).

**Fuera de alcance (fases futuras):**

- Escritura automática a Notion.
- Conexión con el contenido propio ya publicado de Steph. *(Nota: el spec v1 lo difería
  "hasta que haya suficiente contenido acumulado". Steph publicó su primer post en
  Substack y LinkedIn desde julio de 2026, así que esta condición empezó a cumplirse —
  pero sigue fuera del MVP.)*
- Badges, rachas o puntos.
- **Supersesión**: marcar una pieza como superada cuando una posterior la reemplaza
  (salió Grok 4.6 y tres días después 4.7). No es deduplicación — dedup es "dos fuentes
  contaron lo mismo hoy"; supersesión es "lo de la semana pasada ya no aplica". Requiere
  comparar contra el histórico del feed, no solo dentro de la corrida. Muy valiosa para
  un feed cada 2 días, pero se difiere para no arriesgar el MVP.
- ~~Archivar o mover correos en Gmail.~~ **Entró al alcance el 2026-09-01: ver D22.**
- Bandeja de candidatos (remitentes recurrentes fuera de la lista de admitidos, sugeridos para
  promoción). Evaluado y diferido: ver §9.
- Sincronización del estado "conectado" entre dispositivos.

## 5. Categorías del feed

| Categoría | Fuentes |
|---|---|
| **AI & Tech** | The Rundown AI, The Rundown Tech, The Code, TLDR, Nerd Processor |
| **Noticias** | No Pasa Nada |
| **Mujeres y emprendimiento** | The Female Quotient (semanal) |
| **Free Courses Coming Up Next** | Rundown Learn, Allie K Miller, Justin Welsh, FQ eventos |

La sección de cursos no es feed: no lleva resumen profundo. Es una lista de "esto
arranca pronto" con fecha, link y si es gratis o pago.

## 6. Pipeline

```
1. Ingest      IMAP: correos sin etiqueta PuzzleFeed/Procesado, de remitentes admitidos
2. Filtrar     Descartar todo lo demás. Cada descarte imprime su motivo — nunca en silencio

   ── fuentes `agregador` (D15) ────────────────────────────────────────────
3. Extraer     Sacar links del HTML, seguir redirecciones de tracking hasta la URL real
4. Elegir      [LLM] De los ~30 links, ¿cuáles son las N MÁS IMPORTANTES? (D18)
5. Descargar   HTTP GET + extracción de texto limpio (trafilatura). Fallback si falla
6. Resumir     [LLM] Resumen sobre el texto del artículo (o el blurb, marcado)

   ── fuentes `autor` (D15) ────────────────────────────────────────────────
3'. Resumir    [LLM] El cuerpo del correo ES el artículo. Sin links, sin descargas

   ── todo junto ───────────────────────────────────────────────────────────
7. Deduplicar  [LLM] Agrupar items que cubren la misma noticia entre fuentes
8. Hilar       [LLM] Conectar piezas distintas que juntas dicen algo (D14)
9. Publicar    → feed.json  →  repo → GitHub Pages
10. Marcar     Etiquetar por categoría, marcar leído y archivar los correos que SÍ
               se procesaron (D17, D22)
```

**Resumir va antes que deduplicar**, al revés del spec v2. Como el resumen viaja agrupado
por correo, resumir un item que después resulta duplicado no cuesta ni una llamada extra:
va en el mismo lote. Y deduplicar comparando resúmenes reales es mucho más preciso que
comparar textos de ancla, que en TLDR son cosas como "OpenAI's new model".

**Hilar es una llamada aparte y no viaja con deduplicar**, aunque ambos comparen todos los
items entre sí. El costo del error es distinto: un dedup mal hecho **borra** una noticia
del feed, un hilo mal puesto es cosmético. En el mismo prompt competirían por la atención
del modelo justo en la tarea donde equivocarse es caro.

**Dónde se justifica un LLM y dónde no.** Los pasos de criterio son LLM. Los pasos 3 y 5
tienen respuesta verificable: son funciones. No se despliegan agentes para bajar y limpiar
HTML — ni para asignar una categoría que ya está escrita en `fuentes.yml` (D16).

## 7. Privacidad: qué es público y qué no

Esta sección es la más importante del spec. El dashboard es **portafolio público**, y eso
significa que todo lo que llegue a `feed.json` lo puede leer cualquiera.

**La regla:** GitHub Pages sirve archivos estáticos. No hay servidor que decida quién ve
qué; cualquiera puede abrir `/docs/feed.json` directo o mirar la pestaña Network. **Si el
dato llega al navegador, es público.** Esconderlo en el frontend —por CSS, o en un campo
que el frontend ignora— es maquillaje, no privacidad. De ahí sale la consecuencia
operativa: lo que no pueda ser público no se escribe en el archivo.

| Dato | Público | Local |
|---|---|---|
| Fuente, título, link original | ✅ | |
| Resumen y procedencia del resumen | ✅ | |
| Por qué importa, el dato, por dónde se empieza | ✅ | |
| Categoría, tema, grupo de duplicados, hilos | ✅ | |
| Sección de cursos | ✅ | |
| **Credenciales** | | ✅ `.env` |
| **Remitentes descartados** | | ✅ archivo local, ver §7.1 |
| **Marcas de pieza leída** | | ✅ `localStorage` del navegador |

**Cómo se sostiene, y no por disciplina.** `feed.json` se arma **enumerando los campos
permitidos**, uno por uno; todo lo que no esté en esa lista se descarta. Es al revés de
lo intuitivo —sería más corto enumerar lo que se excluye— y es a propósito: enumerando
exclusiones, un campo nuevo del pipeline se publicaría solo por olvidar agregarlo. Ya
evitó una fuga real (D23).

### 7.1 La regla de los remitentes

**En un archivo público solo pueden aparecer suscripciones públicas** — newsletters a
las que cualquiera se puede suscribir. Nada más.

La razón es que **un remitente es un dato personal aunque no sea tuyo**. La lista de
descartes original incluía bancos, la CCSS, Hacienda y siete ATS de empresas donde Steph
se postuló. Publicarla habría revelado a qué banco pertenece, qué servicios usa y dónde
buscó trabajo — sin que ninguno de esos datos fuera necesario para nada.

Por eso la configuración está partida en dos:

| Archivo | Contenido | Git |
|---|---|---|
| `config/fuentes.yml` | Lista de admitidos: newsletters públicas | ✅ se commitea |
| `config/descartados.local.yml` | Bancos, trámites, postulaciones | ❌ gitignored |
| `config/descartados.local.yml.example` | Plantilla con datos de ejemplo | ✅ se commitea |

No cuesta nada funcionalmente: con lista de admitidos, los descartes no filtran nada — solo
sirven para agrupar el reporte por motivo. El archivo local es opcional.

**Correos de personas nunca se listan, ni siquiera en el archivo local.**

**Pendiente para el Bloque 6:** los logs de GitHub Actions de un repo público **son
públicos**. El reporte de diagnóstico imprime remitentes desconocidos, que incluyen
correspondencia personal. En CI hay que suprimir esa sección o el pipeline filtraría en
silencio hacia afuera lo que tanto cuidamos adentro.

### 7.2 Consecuencias aceptadas

Consecuencias aceptadas:
- El estado "conectado" no sincroniza entre celular y compu, y se pierde al limpiar el
  navegador. Es el costo de no tener backend.
- El dashboard revela qué newsletters lee Steph. Para un portafolio, es aceptable.
- Los resúmenes se publican con crédito y link a la fuente, y se mantienen cortos.

## 8. Modelo de datos

**`feed.json` — público, se commitea al repo:**

```json
{
  "generado": "2026-08-17T10:00:00Z",
  "items": [{
    "id": "string",
    "fuente": "The Rundown AI",
    "titulo": "string",
    "url_original": "https://...",
    "resumen": "string corto",
    "por_que_importa": "string, una frase",
    "dato_concreto": "string | null",
    "como_aplicarlo": "string | null",
    "tema": "regulacion | agentes | modelos | herramientas | seguridad | negocio | trabajo | otro",
    "nivel": "principal | secundario",
    "procedencia_resumen": "articulo_completo | solo_newsletter | cuerpo_del_correo",
    "motivo_fallback": "paywall | bloqueado | sin_link | null",
    "categoria": "AI & Tech | Noticias | Mujeres y emprendimiento",
    "fecha_original": "date",
    "fecha_procesado": "date",
    "duplicado_de": ["ids"],
    "confianza": "alta | baja"
  }],
  "hilos": [{
    "titulo": "string",
    "tipo": "continuacion | tension | patron | causa_efecto",
    "que_dice_el_conjunto": "string",
    "ids": ["ids"]
  }],
  "cursos": [{
    "id": "string",
    "titulo": "string",
    "fuente": "string",
    "url": "https://...",
    "fecha_evento": "date | null",
    "gratis": true
  }]
}
```

**Estado local — `localStorage` del navegador:**

```json
{ "item_id": true }   // las piezas que ya se marcaron leídas
```

## 9. Decisiones de diseño con su razón

**El filtro es una lista de admitidos, al revés del job-alert-agent.** Allá el costo del error era
asimétrico hacia dejar pasar: un buen empleo descartado se perdía para siempre. Acá se
invierte — una noticia perdida reaparece mañana en otra newsletter, pero el ruido mata el
objetivo entero (revisar el feed en menos de 10 minutos). Además el universo de fuentes es
chico y conocido. Costo aceptado: una newsletter nueva no entra hasta agregarla al config.

**La lista de excluidos se documenta aunque no se ejecute.** Con lista de admitidos, `descartados_conocidos`
en el config no hace nada funcionalmente. Se mantiene igual, porque cuando Steph se
pregunte "¿por qué no está X aquí?", la respuesta debe estar escrita. Es el principio
"ningún filtro descarta sin decir por qué", aplicado a la configuración.

**El filtro es por remitente exacto, nunca por dominio.** `therundown.ai` manda desde
cuatro direcciones y solo dos son noticias; las otras venden cursos. Filtrar por dominio
metería la mitad de la basura junto con la señal.

**Ningún fallo es silencioso.** Todo descarte, fetch fallido, paywall o respuesta
malformada del LLM se nombra en el log y, si afecta al item, en el dashboard.

## 10. Riesgos conocidos

| Riesgo | Mitigación |
|---|---|
| Paywalls (Bloomberg, WSJ, The Information) | Fallback al blurb + badge visible de procedencia |
| Bloqueo de bots (Cloudflare 403) | User-agent honesto, rate limit, fallback + badge |
| Links de tracking que no resuelven | Timeout, se registra y se descarta el link |
| Corrida duplicada por cron | Etiqueta `PuzzleFeed/Procesado` como memoria |
| App password da acceso total al buzón | Aceptado a conciencia; vive solo en `.env` y GitHub Secrets |
| El LLM devuelve JSON malformado | Se atrapa y se nombra, nunca se absorbe en silencio |
| Publicar resúmenes de contenido ajeno | Resúmenes cortos, siempre con crédito y link a la fuente |

## 11. Qué corrigieron los datos respecto al spec v1

- **The Female Quotient sí llega**, semanalmente, desde `hello@newsletter.thefq.com`. El
  primer escaneo la dio por ausente porque filtró por categorías de Gmail y se topó con el
  límite de 50 resultados por búsqueda. El error fue del método de búsqueda, no del buzón.
- **Nerd Processor nunca llegó porque la suscripción está sin confirmar.** El correo de
  confirmación de `kieran@nerdprocessor.com` (2026-08-11) quedó en la papelera.
- **TLDR (`dan@tldrnewsletter.com`) no estaba en el spec** y llega a diario. Se agrega.
- **"The Rundown" no es una fuente sino cuatro remitentes**, dos de noticias y dos de venta.
- El spec v1 descartaba el digest por correo. **Se reincorpora**, con otro propósito: no es
  un digest. Ver §12 para lo que quedó abierto ahí.

## 12. Preguntas abiertas

- Ninguna bloqueante para empezar a construir.
- ~~Pendiente de Steph: confirmar la suscripción a Nerd Processor.~~ Confirmada; ya llega
  (ver §15).
- ~~A decidir cuando el pipeline funcione: si el dashboard se publica en GitHub Pages o se
  queda local.~~ **Decidido: las dos cosas.** Un solo `feed.json` alimenta el sitio
  estático de GitHub Pages (el portafolio) y una copia publicada como artifact en
  claude.ai. La única diferencia entre las dos es que en el artifact cada pieza puede
  preguntarle algo a un modelo; en GitHub Pages no, porque haría falta una API key en el
  navegador y ahí queda expuesta a cualquiera. Por eso **todo el contexto que la lectora
  necesita se pre-genera en el pipeline** —qué es cada empresa, por qué importa, el dato
  concreto, por dónde se empieza— y el chat queda como extra, no como plan.
- **Abierta, para el bloque 4: mover los correos procesados a una carpeta.** Steph lo
  pidió el 2026-09-01, y contradice lo que hay escrito: §4 tiene "archivar o mover
  correos" fuera de alcance y D12 dice "se marcan leídos; nada más se toca". En Gmail
  "mover a carpeta" es aplicar la etiqueta y quitar `\Inbox` — es reversible y la
  etiqueta deja rastro de qué tocó el agente, pero sería la primera escritura de verdad
  sobre el buzón. Antes de hacerlo hay que cerrar: a qué carpeta va cada cosa, si se
  mueven también los correos de cursos, y si conviene un `--simular` que imprima lo que
  haría sin hacerlo.

## 13. Criterio de éxito del MVP

Steph abre el dashboard día de por medio, en menos de 10 minutos identifica 1-2 piezas que
le aportan, y sale con al menos una idea que ella misma desarrolla — sin haber tenido que
leer los correos originales.

**Criterio secundario:** el dashboard es mostrable como portafolio sin exponer nada suyo.

## 14. Notas de implementación del LLM (medidas, no supuestas)

Probado contra la API real el 2026-08-25 con la key de Steph:

| Modelo | Resultado |
|---|---|
| `gemini-flash-lite-latest` | ✅ 200, JSON estructurado válido, **18.8 s** para 57 tokens de entrada |
| `gemini-flash-latest` | ⚠️ 503 `UNAVAILABLE` — "high demand" |

Tres consecuencias de diseño:

1. **La capa gratuita devuelve 503 por congestión.** No es un error nuestro ni de la
   key. Hace falta reintento con espera creciente y una cadena de modelos de respaldo
   (`flash-lite-latest` → `gemini-2.5-flash-lite` → `gemini-3.5-flash-lite`).
2. **La latencia es impredecible, no alta.** Los 18,8 s de arriba fueron un mal
   momento, no la norma: medido sobre corridas completas del pipeline, la misma
   llamada tarda entre **0,9 s y 86 s**. Una corrida real de 9 correos hace 22
   llamadas en **155 segundos**. Igual se agrupa —una llamada por correo, no por
   item— porque con esa varianza, 96 llamadas serían una lotería.
3. **`responseSchema` funciona bien** y devuelve JSON válido contra el esquema. No hay
   que parsear texto libre ni pedirle al modelo que "responda solo JSON".

La variable de entorno es `GOOGLE_API_KEY` (convención de Google), no `GEMINI_API_KEY`.

4. **Con key inválida, Google contesta 400, no 401 ni 403.** Si solo se mira el código
   HTTP, ese caso se confunde con "esquema mal formado" y el mensaje de error pierde
   las instrucciones para arreglarlo. Hay que mirar también el texto.

5. **A temperatura 0.9 el modelo empieza a escribir sin tildes** ("nomina",
   "informacion"). A 0.7 mantiene la ortografía sin volverse predecible.

## 15. Qué corrigieron los datos en el bloque 3b

Medido el 2026-09-01 contra el buzón real, corriendo el pipeline completo.

- **Nerd Processor ya llega**, semanalmente. §11 la daba por no confirmada. Y llega
  como **boletín de autor**: el ensayo completo viene en el correo, sin permalink al
  post del día. Ver D15.
- **El volumen es 3× lo que el spec asumía.** §4 daba por hecho "≈24 items cada 2
  días". Con el filtro original, 9 correos de 3 días producían **41 items**, y eso ya
  con un tope de 8 por correo: sin tope habrían sido ~70. La causa no es el backlog —
  son cinco newsletters diarias que traen entre 10 y 18 noticias legítimas cada una.
  Con D18 la misma ventana da 26 items.
- **TLDR y No Pasa Nada no son diarias.** Medido sobre 14 días: 8 y 6 ediciones
  respectivamente. `fuentes.yml` decía "diaria" para las dos.
- **La descarga de artículos falla ~35% de las veces**, y todos los motivos son
  legítimos: bloqueo del sitio (Bloomberg, Wired), texto demasiado corto, un servidor
  que devuelve `text/markdown`. El fallback al blurb con badge visible (§10) no es un
  caso raro: es un tercio del feed.
- **El dedup rinde menos de lo esperado.** De 45 items, solo 4 grupos duplicados. Las
  fuentes se solapan menos de lo que parecía, así que no se puede contar con el dedup
  para bajar el volumen — hay que filtrar antes.

### 15.1 La primera escritura sobre el buzón

La primera corrida con `--marcar` falló, y las dos cosas que enseñó valen más que el
error en sí.

**El error.** Para sacar un correo de la bandeja hay que mandarle `(\Inbox)` al servidor,
que en un literal de Python se escribe con dos barras. Estaba escrito con cuatro, que le
manda `(\\Inbox)`; Gmail contestó `BAD Could not parse command` y tenía razón.

**Lo que en realidad estaba mal.** El módulo atrapaba `RuntimeError`, pero `imaplib` a
veces devuelve `("BAD", …)` y otras veces levanta su propia excepción, según dónde se
rompa el comando. Esa segunda se escapó, y el fallo de UN correo abortó los otros seis
—exactamente lo que el diseño decía que no debía pasar—. El comentario del código ahora
lo dice, porque es el tipo de cosa que se vuelve a escribir mal dentro de seis meses.

**Lo que sí funcionó, y por qué importa.** El correo que falló alcanzó a recibir etiqueta
y marca de leído antes de romperse, así que quedó a medias. Encontrarlo fue una búsqueda:

    label:PuzzleFeed/Procesado in:inbox

La reversibilidad que D8 y D22 prometían se usó de verdad, a la primera, para reparar el
estado. Y los otros seis correos, al no haber recibido nunca la etiqueta, siguieron
contando como no procesados: la corrida siguiente los tomó sin que hubiera nada que
reparar. Eso es D17 haciendo su trabajo sin que nadie tuviera que intervenir.

Estado final verificado contra Gmail: 8 correos etiquetados, 0 en la bandeja, 0 sin leer,
13 intactos.
