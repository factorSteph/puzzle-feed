#!/usr/bin/env python3
"""Revisión de privacidad del repositorio. Correr antes de cada push.

    python3 verificar_privacidad.py

Existe porque la vara de "ya lo revisé" no aguanta: lo que se escapa no es lo
que uno mira, es lo que uno deja de mirar cuando la sesión se hizo larga. Estas
comprobaciones son siempre las mismas, tardan segundos y no se cansan.

## Cómo lee sus propias reglas

Ninguna comprobación repite una lista que ya exista en otro lado. Los campos
permitidos del feed se importan de `pipeline/publicar.py`, y los identificadores
propios salen de `.env`, que no se commitea. Una regla copiada a dos archivos se
desincroniza el día que alguien cambia uno solo, y entonces el verificador
aprueba con la lista vieja.

Por eso mismo este archivo se puede publicar: no dice qué se busca, dice dónde
está escrito.

## Qué revisa

  1. Que los archivos con datos personales sigan retenidos por .gitignore.
  2. Que ninguno de ellos haya entrado nunca al historial, ni en un commit viejo.
  3. Que ningún archivo rastreado nombre una extensión local concreta.
  4. Que las direcciones de correo en archivos rastreados sean todas de la
     configuración pública de fuentes.
  5. Que no haya rutas de la máquina de quien desarrolla.
  6. Que `feed.json` lleve solo campos permitidos, identificadores derivados,
     enlaces sin credenciales de suscriptor, y nada de la lista de propios.

Cada comprobación imprime lo que encontró, no solo si pasó. Un verificador que
solo dice OK enseña a confiar en él sin leerlo, y ese es el hábito que hizo
falta cambiar.
"""

import json
import pathlib
import re
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "pipeline"))

import config  # noqa: E402  (después de arreglar la ruta)
import publicar  # noqa: E402

# Patrones de .gitignore cuyo contenido es personal. Se comprueban uno por uno
# con `git check-ignore`, que da prueba positiva: dice qué línea los retiene.
# Que un archivo no aparezca en `git status` es evidencia débil.
RETENIDOS = (
    ".env",
    "config/descartados.local.yml",
    "config/perfil.local.yml",
    "salida.privado.json",
)

# Los patrones cuyo contenido nunca debe haber entrado al historial.
NUNCA_EN_EL_HISTORIAL = re.compile(
    r"(^|/)\.env$|\.local\.(yml|py)$|\.privado\.|(^|/)CLAUDE\.md$|(^|/)credentials\.json$"
)

CORREO = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# El nombre de la carpeta se arma en dos pedazos en vez de escribirse entero:
# si estuviera literal, esta misma línea sería una coincidencia y el verificador
# se acusaría a sí mismo.
#
# La barra invertida se construye por su código y se escapa para el motor de
# expresiones. Son dos capas distintas, y saltarse la segunda es un error que no
# avisa hasta que el patrón se compila: una barra suelta ahí adentro no es una
# barra, es el principio de un escape.
_CARPETA = "Us" + "ers"
_BARRA = re.escape(chr(92))
RUTA_PERSONAL = re.compile(
    "/mnt/c/" + _CARPETA + "/"
    + "|[A-Za-z]:" + _BARRA + _CARPETA + _BARRA
    + "|/home/[a-z]",
    re.IGNORECASE,
)
FORMA_DE_ID = re.compile(r"[0-9a-f]{8}")
FORMA_DE_UID = re.compile(r"^\d{3,7}-\d{1,3}$")


class Reporte:
    """Lleva la cuenta y decide el código de salida."""

    def __init__(self):
        self.fallos = 0

    def bien(self, titulo, detalle=""):
        print(f"  ok    {titulo}")
        if detalle:
            print(f"        {detalle}")

    def mal(self, titulo, detalle=""):
        self.fallos += 1
        print(f"  FALLA {titulo}")
        if detalle:
            print(f"        {detalle}")

    def aviso(self, titulo, detalle=""):
        print(f"  ·     {titulo}")
        if detalle:
            print(f"        {detalle}")


def git(*argumentos):
    """Corre git y devuelve su salida. Cadena vacía si el comando falla."""
    try:
        salida = subprocess.run(
            ("git", *argumentos), cwd=RAIZ, capture_output=True, text=True, check=False
        )
    except OSError as error:
        return f"__error__ {error}"
    return salida.stdout


def archivos_rastreados():
    return [linea for linea in git("ls-files").splitlines() if linea.strip()]


def identificadores_propios():
    """Los textos que nunca deben salir, leídos por el mismo código que usa el
    pipeline: en una máquina de desarrollo salen de `.env`, y en una corrida
    automática del entorno. Duplicar la lectura acá sería tener dos lugares
    donde se puede desincronizar."""
    return config.cargar_identificadores()


# ─────────────────────────────────────────────────────────────────────────────


def revisar_retenidos(reporte):
    print("\n1. Los archivos con datos personales siguen retenidos")
    for ruta in RETENIDOS:
        if not (RAIZ / ruta).exists():
            reporte.aviso(f"{ruta}", "no existe en esta máquina, nada que retener")
            continue
        prueba = git("check-ignore", "-v", ruta).strip()
        if prueba:
            reporte.bien(ruta, prueba.split("\t")[0])
        else:
            reporte.mal(ruta, "NO está siendo ignorado por .gitignore")


def revisar_historial(reporte):
    print("\n2. Nada personal entró nunca al historial")
    salida = git("log", "--all", "--pretty=format:", "--name-only", "--diff-filter=A")
    if salida.startswith("__error__"):
        reporte.mal("no pude leer el historial", salida)
        return
    vistos = sorted({linea.strip() for linea in salida.splitlines() if linea.strip()})
    if not vistos:
        reporte.aviso("el repositorio todavía no tiene commits")
        return

    colados = [ruta for ruta in vistos if NUNCA_EN_EL_HISTORIAL.search(ruta)]
    if colados:
        reporte.mal(
            "archivos personales en el historial",
            ", ".join(colados) + "  ← borrar el archivo NO los saca de acá",
        )
    else:
        reporte.bien(f"{len(vistos)} archivo(s) en el historial, ninguno personal")


def revisar_menciones(reporte):
    print("\n3. Ningún archivo rastreado nombra una extensión local concreta")
    # El patrón `pipeline/*.local.py` en .gitignore es una regla, no una
    # mención: describe una forma, no un archivo que exista. Lo que no puede
    # aparecer es el nombre de uno concreto.
    concretos = [
        ruta.name
        for ruta in (RAIZ / "pipeline").glob("*.local.py")
    ]
    if not concretos:
        reporte.aviso("no hay extensiones locales en esta máquina")
        return

    encontrados = []
    for ruta in archivos_rastreados():
        archivo = RAIZ / ruta
        if not archivo.is_file():
            continue
        try:
            texto = archivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for nombre in concretos:
            if nombre in texto or nombre.replace(".local.py", "") + ".local" in texto:
                encontrados.append(f"{ruta} nombra una extensión local")

    if encontrados:
        reporte.mal("hay menciones", "; ".join(sorted(set(encontrados))))
    else:
        reporte.bien(f"{len(concretos)} extensión(es) local(es), ninguna nombrada")


def revisar_correos(reporte):
    print("\n4. Las direcciones en archivos rastreados son todas de fuentes públicas")
    permitidas = set()
    for nombre in ("config/fuentes.yml",):
        archivo = RAIZ / nombre
        if archivo.exists():
            permitidas |= set(CORREO.findall(archivo.read_text(encoding="utf-8")))

    ajenas = {}
    for ruta in archivos_rastreados():
        archivo = RAIZ / ruta
        if not archivo.is_file():
            continue
        # Los .example llevan direcciones inventadas a propósito.
        if ruta.endswith(".example") or ruta.endswith(".example.yml"):
            continue
        try:
            texto = archivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for direccion in CORREO.findall(texto):
            if direccion not in permitidas and not direccion.endswith((".example", "@gmail.com")):
                ajenas.setdefault(direccion, []).append(ruta)
            elif direccion.endswith("@gmail.com") and direccion not in permitidas:
                ajenas.setdefault(direccion, []).append(ruta)

    if ajenas:
        reporte.mal(
            f"{len(ajenas)} dirección(es) que no están en fuentes.yml",
            "; ".join(f"{d} en {', '.join(r)}" for d, r in sorted(ajenas.items())),
        )
    else:
        reporte.bien(f"{len(permitidas)} dirección(es), todas de la configuración pública")


def revisar_rutas(reporte):
    print("\n5. No hay rutas de la máquina de quien desarrolla")
    encontradas = []
    for ruta in archivos_rastreados():
        archivo = RAIZ / ruta
        if not archivo.is_file():
            continue
        try:
            texto = archivo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for numero, linea in enumerate(texto.splitlines(), 1):
            if RUTA_PERSONAL.search(linea):
                encontradas.append(f"{ruta}:{numero}")

    if encontradas:
        reporte.mal(f"{len(encontradas)} línea(s) con rutas locales", ", ".join(encontradas))
    else:
        reporte.bien("ninguna")


def revisar_feed(reporte):
    print("\n6. El archivo público")
    ruta = RAIZ / "docs" / "feed.json"
    if not ruta.exists():
        reporte.aviso("todavía no hay docs/feed.json")
        return

    feed = json.loads(ruta.read_text(encoding="utf-8"))
    items = feed.get("items") or []

    permitidos = set(publicar.CAMPOS_PUBLICOS)
    sobrantes = sorted({c for i in items for c in i} - permitidos)
    if sobrantes:
        reporte.mal("campos fuera de la lista permitida", ", ".join(sobrantes))
    else:
        reporte.bien(f"{len(items)} pieza(s), solo campos permitidos")

    ids = [i.get("id") for i in items]
    derivados = [i for i in ids if i and FORMA_DE_ID.fullmatch(str(i))]
    crudos = [i for i in ids if i and FORMA_DE_UID.match(str(i))]
    if crudos:
        reporte.mal(f"{len(crudos)} identificador(es) sin derivar", ", ".join(map(str, crudos)))
    elif len(derivados) != len(ids):
        reporte.mal("hay identificadores con una forma inesperada")
    elif len(set(ids)) != len(ids):
        reporte.mal("hay identificadores repetidos")
    else:
        reporte.bien(f"{len(ids)} identificador(es) derivados y únicos")

    texto_entero = json.dumps(feed, ensure_ascii=False)

    enlaces = re.findall(r'"(https?://[^"]+)"', texto_entero)
    colgando = []
    for enlace in enlaces:
        cuerpo = enlace.split("?", 1)
        if len(cuerpo) == 2:
            for parametro in cuerpo[1].split("&"):
                _, _, valor = parametro.partition("=")
                if len(valor) >= publicar.LARGO_DE_TOKEN:
                    colgando.append(enlace)
    if colgando:
        reporte.mal(f"{len(colgando)} enlace(s) con un identificador colgando", colgando[0])
    else:
        reporte.bien(f"{len(enlaces)} enlace(s), ninguno con identificador de suscriptor")

    propios = identificadores_propios()
    if not propios:
        reporte.mal("no pude leer los identificadores propios de .env", "la revisión 6 quedó coja")
        return
    apariciones = [p for p in propios if p.lower() in texto_entero.lower()]
    if apariciones:
        reporte.mal(f"{len(apariciones)} identificador(es) propios aparecen en el feed")
    else:
        reporte.bien(f"ninguno de los {len(propios)} identificadores propios aparece")


def main():
    print("REVISIÓN DE PRIVACIDAD")
    print("─" * 78)
    reporte = Reporte()
    for revisar in (
        revisar_retenidos,
        revisar_historial,
        revisar_menciones,
        revisar_correos,
        revisar_rutas,
        revisar_feed,
    ):
        revisar(reporte)

    print("\n" + "─" * 78)
    if reporte.fallos:
        print(f"{reporte.fallos} comprobación(es) fallaron. No subas nada todavía.\n")
        return 1
    print("Todo en orden.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
