"""Carga y validación de la configuración.

Hay tres archivos, separados por lo que se puede publicar y lo que no:

    config/fuentes.yml             →  QUÉ leemos. PÚBLICO, se commitea.
                                      Solo suscripciones públicas (newsletters).

    config/descartados.local.yml   →  Qué ignoramos. LOCAL, en .gitignore.
                                      Bancos, trámites, postulaciones. Opcional.

    .env                           →  CON QUÉ entramos. LOCAL, en .gitignore.

La separación no es cosmética: el repo es público y el dashboard es portafolio.
Los descartes son un mapa de la vida privada de quien usa esto: a qué banco
pertenece, con qué instituciones tramita, qué servicios usa. No tienen por qué
salir de su máquina.

Funcionalmente los descartes no hacen falta: el pipeline usa lista de admitidos, así que
lo que no está en fuentes.yml ya queda fuera. La lista local solo sirve para que el
reporte agrupe cada descarte con su motivo.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO_FUENTES = RAIZ / "config" / "fuentes.yml"
ARCHIVO_DESCARTADOS = RAIZ / "config" / "descartados.local.yml"
ARCHIVO_PERFIL = RAIZ / "config" / "perfil.local.yml"

load_dotenv(RAIZ / ".env")


class ErrorDeConfiguracion(Exception):
    """Falta algo o está mal escrito. Siempre dice qué y cómo arreglarlo."""


def cargar_credenciales():
    """Devuelve (usuario, app_password) desde .env, o explica qué falta."""
    usuario = os.getenv("GMAIL_USUARIO")
    password = os.getenv("GMAIL_APP_PASSWORD")

    faltantes = [
        nombre
        for nombre, valor in (("GMAIL_USUARIO", usuario), ("GMAIL_APP_PASSWORD", password))
        if not valor
    ]
    if faltantes:
        raise ErrorDeConfiguracion(
            f"Faltan estas variables en .env: {', '.join(faltantes)}\n"
            f"  1. Copiá .env.example a .env\n"
            f"  2. Llenalo con tus datos\n"
            f"  3. Volvé a correr"
        )

    # Google muestra el app password como "abcd efgh ijkl mnop". Si lo pegás tal cual,
    # IMAP lo rechaza sin decir por qué. Limpiamos los espacios acá para que no pase.
    return usuario, password.replace(" ", "")


def en_ci():
    """Cierto cuando el pipeline corre en un servidor de integración continua.

    Sirve para que las partes del reporte que solo tienen sentido con alguien
    mirando la pantalla no se armen en una corrida desatendida.
    """
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))


def cargar_sal():
    """Devuelve la sal con la que se derivan los identificadores del feed.

    Tiene que ser la misma en todas las máquinas que publiquen el feed: el
    tablero guarda el progreso del rompecabezas con esos identificadores, y con
    otra sal cada pieza vuelve a empezar apagada.
    """
    sal = os.getenv("PUZZLE_SALT")
    if not sal:
        raise ErrorDeConfiguracion(
            "Falta PUZZLE_SALT en .env.\n"
            "  1. Generá una:  python3 -c \"import secrets; print(secrets.token_hex(16))\"\n"
            "  2. Pegala en .env como PUZZLE_SALT=…\n"
            "  3. Guardala: cambiarla reinicia el progreso del tablero"
        )
    return sal


# Un identificador más corto que esto coincide con prosa por accidente, y un
# falso positivo acá aborta la corrida entera.
LARGO_MINIMO_DE_IDENTIFICADOR = 4


def cargar_identificadores():
    """Devuelve los textos propios que nunca deben aparecer en el feed público.

    La dirección de correo sale de la configuración que ya existe; el resto
    —nombre, apellido, cualquier cosa con la que un remitente personalice— se
    lista en PUZZLE_IDENTIFICADORES, separado por comas. Vive en .env por la
    misma razón que el resto: enumerar en el repositorio lo que se busca es
    publicarlo.

    Devolver una lista vacía sería un filtro que no filtra, así que el llamador
    reporta cuántos quedaron activos.
    """
    crudos = list((os.getenv("PUZZLE_IDENTIFICADORES") or "").split(","))

    usuario = os.getenv("GMAIL_USUARIO") or ""
    if usuario:
        crudos.append(usuario)
        crudos.append(usuario.split("@")[0])

    return sorted(
        {
            texto.strip()
            for texto in crudos
            if len(texto.strip()) >= LARGO_MINIMO_DE_IDENTIFICADOR
        }
    )


def cargar_fuentes():
    """Lee la configuración y devuelve tres diccionarios indexados por remitente.

    Devuelve (fuentes, cursos, descartados). `descartados` mapea cada remitente
    conocido al motivo por el que está fuera, y viene vacío si no existe el
    archivo local, que es opcional a propósito.
    """
    if not ARCHIVO_FUENTES.exists():
        raise ErrorDeConfiguracion(f"No encuentro el archivo de fuentes: {ARCHIVO_FUENTES}")

    with ARCHIVO_FUENTES.open(encoding="utf-8") as archivo:
        datos = yaml.safe_load(archivo) or {}

    fuentes = _indexar(datos.get("fuentes"), "fuentes")
    cursos = _indexar(datos.get("cursos_eventos"), "cursos_eventos")

    repetidos = fuentes.keys() & cursos.keys()
    if repetidos:
        raise ErrorDeConfiguracion(
            f"Estos remitentes están en `fuentes` y en `cursos_eventos` a la vez, "
            f"y no puedo saber a cuál pertenecen: {', '.join(sorted(repetidos))}"
        )

    return fuentes, cursos, _cargar_descartados()


def cargar_perfil():
    """Lee `config/perfil.local.yml`, si alguien lo necesita.

    El pipeline público no lo usa: existe para que una extensión local pueda leer
    contexto propio sin inventarse su propio formato de configuración.

    Ojo con lo que se le agrega: está gitignored, pero nada impide que quien lo
    use lo mande a un servicio externo. Gitignored no quiere decir privado
    frente a terceros.
    """
    if not ARCHIVO_PERFIL.exists():
        raise ErrorDeConfiguracion(
            f"No encuentro {ARCHIVO_PERFIL}.\n"
            f"Ese archivo es opcional para el pipeline, pero algo lo pidió."
        )

    with ARCHIVO_PERFIL.open(encoding="utf-8") as archivo:
        perfil = yaml.safe_load(archivo) or {}

    if not perfil:
        raise ErrorDeConfiguracion(f"El perfil {ARCHIVO_PERFIL} está vacío.")

    return perfil


def _cargar_descartados():
    """Lee la lista local de descartes. Su ausencia no es un error."""
    if not ARCHIVO_DESCARTADOS.exists():
        return {}

    with ARCHIVO_DESCARTADOS.open(encoding="utf-8") as archivo:
        datos = yaml.safe_load(archivo) or {}

    descartados = {}
    for motivo, lista in (datos.get("descartados_conocidos") or {}).items():
        for remitente in lista or []:
            descartados[remitente.strip().lower()] = motivo
    return descartados


def _indexar(entradas, seccion):
    indice = {}
    for entrada in entradas or []:
        remitente = (entrada.get("remitente") or "").strip().lower()
        if not remitente:
            raise ErrorDeConfiguracion(
                f"Hay una entrada sin `remitente` en la sección `{seccion}` de fuentes.yml"
            )
        indice[remitente] = entrada
    return indice
