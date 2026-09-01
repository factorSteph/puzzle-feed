"""Cliente de Gemini por REST.

Este módulo es TRANSPORTE. No sabe qué es un newsletter ni qué es una noticia.
Recibe una instrucción, unos datos y un esquema, y devuelve JSON validado o el
motivo exacto por el que no pudo. Los prompts viven en `analisis.py`; acá no
hay ni uno.

## Por qué tiene tanta maquinaria de reintento

Está medido contra la API real, no supuesto (spec.md §14):

    gemini-flash-lite-latest   200 OK, JSON válido contra el esquema, 18.8 s
    gemini-flash-latest        503 UNAVAILABLE: "high demand"

La capa gratuita devuelve 503 por congestión de Google, no por culpa nuestra ni
de la key. Sin reintento y sin modelos de respaldo, una corrida se cae por algo
que se resuelve esperando veinte segundos.

De ahí las tres defensas:

  1. Reintento con espera creciente dentro del mismo modelo.
  2. Cadena de respaldo: si un modelo no está disponible, se prueba el siguiente.
  3. Presupuesto de tiempo por llamada, para que ninguna se coma la corrida.

## Cómo falla

Nunca con una excepción suelta, nunca devolviendo un dict vacío que parezca
éxito. Siempre `(None, "motivo legible")`, y ese motivo termina visible en el
dashboard (regla 4 del proyecto: ningún fallo en silencio).

## Sobre los esquemas

Gemini acepta un subconjunto de OpenAPI: `type`, `properties`, `required`,
`items`, `enum`, `description`, `nullable` y `propertyOrdering`. No acepta
`$ref` ni `additionalProperties`.

`propertyOrdering` importa más de lo que parece: el modelo genera los campos en
ese orden, así que poner el campo de razonamiento ANTES del veredicto le da
lugar a pensar antes de decidir. Al revés, decide primero y justifica después.
"""

import json
import os
import sys
import time

import requests

# Cadena de respaldo. El primero es el que está medido; los otros dos son
# alias fijos para cuando el "latest" apunte a algo congestionado.
MODELOS = (
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash-lite",
)

RAIZ_API = "https://generativelanguage.googleapis.com/v1beta/models"

TIEMPO_LIMITE = 120             # una llamada normal tarda ~19 s; esto es el techo
INTENTOS_POR_MODELO = 2
ESPERAS = (3, 10)               # segundos entre intentos del mismo modelo
PRESUPUESTO_POR_LLAMADA = 180   # techo total: ninguna llamada se come la corrida
MAXIMO_DE_SALIDA = 8192

# Códigos que significan "el problema es del otro lado, volvé a intentar".
TRANSITORIOS = frozenset({429, 500, 502, 503, 504})


class ErrorDeConfiguracion(Exception):
    """Falta la key o está mal. Se distingue de un fallo del modelo a propósito."""


class Cliente:
    """Mantiene la sesión HTTP y lleva la cuenta de lo que costó la corrida.

    Es una clase y no funciones sueltas por dos razones concretas: una corrida
    hace ~30 llamadas, y reusar la conexión TCP ahorra el handshake de cada una;
    y al terminar queremos poder decir cuánto tardó todo y cuántos reintentos
    hicieron falta, que es justo lo que hay que medir para ajustar el diseño.
    """

    def __init__(self, api_key=None, verboso=True):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ErrorDeConfiguracion(
                "Falta GOOGLE_API_KEY en .env\n"
                "  1. Generá una key gratis en https://aistudio.google.com/apikey\n"
                "  2. Pegala en .env como GOOGLE_API_KEY=...\n"
                "  3. Volvé a correr"
            )
        self.verboso = verboso
        self.sesion = requests.Session()
        self.llamadas = 0
        self.reintentos = 0
        self.fallos = 0
        self.segundos = 0.0

    def llamar(self, instruccion, entrada, esquema, etiqueta, temperatura=0.2):
        """Le pide al modelo una respuesta con la forma de `esquema`.

        `etiqueta` es para el log: "resumir · The Rundown AI 27-ago". Aparece en
        cada línea de diagnóstico, así que cuando algo falle vas a saber qué era
        sin tener que reconstruirlo.

        Devuelve (datos, incidencia):
            (dict, None)   salió bien
            (None, str)    no salió, y el str dice por qué en español
        """
        cuerpo = {
            "systemInstruction": {"parts": [{"text": instruccion}]},
            "contents": [{"role": "user", "parts": [{"text": entrada}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": esquema,
                "temperature": temperatura,
                "maxOutputTokens": MAXIMO_DE_SALIDA,
            },
        }

        arranque = time.monotonic()
        self.llamadas += 1
        ultimo_motivo = "sin intentos"

        for modelo in MODELOS:
            for intento in range(INTENTOS_POR_MODELO):
                if time.monotonic() - arranque > PRESUPUESTO_POR_LLAMADA:
                    return self._rendirse(
                        etiqueta, f"se agotó el presupuesto de {PRESUPUESTO_POR_LLAMADA}s", arranque
                    )

                if intento:
                    self.reintentos += 1
                    espera = ESPERAS[min(intento - 1, len(ESPERAS) - 1)]
                    self._avisar(f"{etiqueta}: reintento en {espera}s ({ultimo_motivo})")
                    time.sleep(espera)

                datos, motivo, que_hacer = self._intentar(modelo, cuerpo)

                if datos is not None:
                    self.segundos += time.monotonic() - arranque
                    return datos, None

                ultimo_motivo = motivo

                if que_hacer == "abandonar":
                    # Key inválida o esquema mal formado: cambiar de modelo o
                    # esperar no arregla nada. Insistir solo gasta tiempo.
                    return self._rendirse(etiqueta, motivo, arranque)

                if que_hacer == "otro_modelo":
                    self._avisar(f"{etiqueta}: {modelo} no sirve ({motivo}), voy al siguiente")
                    break
            else:
                self._avisar(f"{etiqueta}: {modelo} agotado ({ultimo_motivo}), pruebo respaldo")

        return self._rendirse(etiqueta, ultimo_motivo, arranque)

    def _intentar(self, modelo, cuerpo):
        """Un intento contra un modelo.

        Devuelve (datos, motivo, que_hacer), donde que_hacer es:
            "reintentar"   el problema es transitorio, vale esperar
            "otro_modelo"  este modelo no existe o no acepta lo que pedimos
            "abandonar"    el problema es nuestro, insistir no lo arregla
        """
        url = f"{RAIZ_API}/{modelo}:generateContent"
        try:
            respuesta = self.sesion.post(
                url,
                # La key va en el encabezado y no en la URL: así no queda
                # escrita en logs de proxies ni en el historial de nadie.
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=cuerpo,
                timeout=TIEMPO_LIMITE,
            )
        except requests.Timeout:
            return None, f"timeout de {TIEMPO_LIMITE}s", "reintentar"
        except requests.RequestException as error:
            return None, f"error de red: {type(error).__name__}", "reintentar"

        codigo = respuesta.status_code
        detalle = _detalle_del_error(respuesta) if codigo != 200 else ""

        # Medido: con una key inválida Google contesta 400, no 401 ni 403. Si
        # solo miráramos el código, este caso caería en la rama del esquema roto
        # y el mensaje perdería las instrucciones para arreglarlo. Por eso se
        # mira también el texto.
        if codigo in (401, 403) or "api key not valid" in detalle.lower():
            return None, (
                "Google rechazó la API key. Revisá que:\n"
                "     1. GOOGLE_API_KEY en .env sea la key de AI Studio\n"
                "        (se genera en https://aistudio.google.com/apikey)\n"
                "     2. no esté revocada ni le sobren espacios o comillas\n"
                f"     Respuesta de Google: {detalle}"
            ), "abandonar"

        if codigo == 404:
            return None, f"el modelo {modelo} no existe en esta API", "otro_modelo"

        if codigo == 400:
            # Lo que queda acá es casi siempre un esquema que Gemini no acepta.
            # Es un bug nuestro y hay que verlo, no enterrarlo bajo reintentos.
            return None, f"petición rechazada: {detalle}", "abandonar"

        if codigo in TRANSITORIOS:
            return None, f"HTTP {codigo} ({detalle})", "reintentar"

        if codigo != 200:
            return None, f"HTTP {codigo}", "reintentar"

        return _leer_respuesta(respuesta)

    def _rendirse(self, etiqueta, motivo, arranque):
        """Cierra la contabilidad de una llamada perdida y devuelve su motivo."""
        self.segundos += time.monotonic() - arranque
        self.fallos += 1
        self._avisar(f"{etiqueta}: SIN RESULTADO: {motivo}")
        return None, f"modelo_no_disponible: {motivo}"

    def _avisar(self, texto):
        """Diagnóstico a stderr, para no ensuciar la salida del reporte."""
        if self.verboso:
            print(f"  [gemini] {texto}", file=sys.stderr, flush=True)

    def resumen(self):
        """Qué costó la corrida. Los tiempos de spec.md §14 salieron de medir así."""
        return (
            f"{self.llamadas} llamada(s) al modelo · {self.segundos:.0f}s en total · "
            f"{self.reintentos} reintento(s) · {self.fallos} sin resultado"
        )


def _leer_respuesta(respuesta):
    """Saca el JSON del envoltorio de Gemini, o dice por qué no se pudo."""
    try:
        sobre = respuesta.json()
    except ValueError:
        return None, "la respuesta no era JSON", "reintentar"

    bloqueo = (sobre.get("promptFeedback") or {}).get("blockReason")
    if bloqueo:
        # Los filtros de seguridad de Google. Reintentar da lo mismo: hay que
        # verlo, porque significa que algo del contenido los activó.
        return None, f"bloqueado por los filtros de Google ({bloqueo})", "abandonar"

    candidatos = sobre.get("candidates") or []
    if not candidatos:
        return None, "la respuesta vino sin candidatos", "reintentar"

    candidato = candidatos[0]
    razon = candidato.get("finishReason")

    if razon == "MAX_TOKENS":
        # El JSON viene cortado a la mitad: parsearlo daría basura. Este motivo
        # es accionable a propósito: dice qué hacer, no solo qué pasó.
        return None, (
            f"la respuesta se cortó en el límite de {MAXIMO_DE_SALIDA} tokens; "
            "hay que partir el lote en pedazos más chicos"
        ), "abandonar"

    if razon == "SAFETY":
        return None, "el modelo cortó la respuesta por sus filtros de seguridad", "abandonar"

    partes = (candidato.get("content") or {}).get("parts") or []
    texto = "".join(parte.get("text", "") for parte in partes)
    if not texto.strip():
        return None, f"el modelo devolvió texto vacío (finishReason: {razon})", "reintentar"

    try:
        return json.loads(texto), None, None
    except json.JSONDecodeError as error:
        # Con responseSchema esto no debería pasar nunca. Si pasa, queremos
        # verlo con nombre y apellido en vez de que se absorba en silencio.
        return None, f"el modelo devolvió JSON malformado: {error}", "reintentar"


def _detalle_del_error(respuesta):
    """El mensaje de error de Google, si viene; si no, el cuerpo recortado."""
    try:
        return (respuesta.json().get("error") or {}).get("message", "sin detalle")
    except ValueError:
        return respuesta.text[:200] or "sin detalle"
