# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil (Kivy + KivyMD)
================================================
Puerto a Android de la web original "Agrowillay": diagnóstico de
plagas en plantas en 3 pasos:

    1. Foto de la planta (cámara o galería)
    2. Diagnóstico con la API de Gemini (Google AI)
    3. Ayuda cercana: enlaces a Google Maps con viveros/agrónomos cerca
       del usuario (usando el GPS del teléfono, sin API de mapas paga)

Diseño: mismo tema verde y misma estructura de 3 pasos que la web
(index.html), adaptado a componentes nativos de KivyMD.

IMPORTANTE — manejo de la API Key:
La clave de Gemini NUNCA se escribe en este archivo. El usuario la
ingresa una sola vez en la app (pantalla de Ajustes) y se guarda de
forma local en un archivo de configuración en el almacenamiento
privado de la app (no en el APK, no en el repositorio, no visible
para otras apps).
"""

import base64
import json
import os
import shutil
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from kivy.animation import Animation
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import (
    ListProperty,
    ObjectProperty,
    StringProperty,
)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.screenmanager import Screen

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.card import MDCard
from kivymd.uix.button import MDRaisedButton
from kivymd.uix.label import MDLabel
from kivymd.uix.spinner import MDSpinner
from kivymd.toast import toast

from kivy.utils import platform

# ---------------------------------------------------------------------------
# Permisos y rutas específicas de Android
# ---------------------------------------------------------------------------

if platform == "android":
    from android.permissions import Permission, request_permissions

    _permisos = [
        Permission.INTERNET,
        Permission.CAMERA,
        Permission.ACCESS_FINE_LOCATION,
        Permission.ACCESS_COARSE_LOCATION,
    ]
    # En Android 12 y anteriores existen estos permisos; en 13+ ya no
    # existen (dan error si se piden) y se reemplazan por READ_MEDIA_IMAGES.
    try:
        _permisos.append(Permission.WRITE_EXTERNAL_STORAGE)
        _permisos.append(Permission.READ_EXTERNAL_STORAGE)
    except AttributeError:
        pass
    try:
        _permisos.append(Permission.READ_MEDIA_IMAGES)
    except AttributeError:
        pass

    request_permissions(_permisos)
    from android.storage import app_storage_path

    APP_DATA_DIR = Path(app_storage_path())
else:
    # Para probar en escritorio (Windows/Linux/Mac) mientras desarrollas.
    APP_DATA_DIR = Path(os.path.expanduser("~/.agrotech_curahuasi"))

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "config.json"
BITACORA_FILE = APP_DATA_DIR / "bitacora.json"
HISTORY_FILE = APP_DATA_DIR / "historial.json"
HISTORY_PHOTOS_DIR = APP_DATA_DIR / "historial_fotos"

# Nombre de la foto tomada con la camara (debe coincidir con el <files-path>
# declarado en src/android/file_paths.xml para que el FileProvider funcione).
CAMERA_PHOTO_PATH = str(APP_DATA_DIR / "captura_temp.jpg")

# Codigo de peticion usado para identificar el resultado del Intent de camara
# en onActivityResult (cualquier numero fijo sirve, solo debe ser unico).
CAMERA_REQUEST_CODE = 1888

# Modelo de Gemini usado para el diagnóstico (visión + texto)
GEMINI_MODEL = "gemini-3.7-flash"
# Si el modelo principal esta saturado (error 503) tras varios reintentos,
# se prueba con este modelo de respaldo, mucho mas antiguo y estable.
GEMINI_MODEL_FALLBACK = "gemini-3.6-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# Clave de Gemini incluida por defecto para que la app funcione al abrirla,
# sin que el usuario tenga que configurar nada manualmente.
#
# IMPORTANTE: esto SOLO es seguro porque el repositorio de GitHub es
# PRIVADO. Si en algun momento lo pones publico de nuevo, esta clave
# quedaria expuesta otra vez y habria que revocarla y generar una nueva
# (en https://aistudio.google.com/apikey) antes de hacerlo publico.
DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6JUSmY_mCwVu6L7n2oa05nwvxsf8NbHKdFWd_Tkbo-n0Q"

# Version actual de esta build. El workflow de GitHub Actions publica un
# release con un tag "v<esta_version>" cada vez que compila el APK; la
# app compara esta constante contra el tag_name del ultimo release para
# avisar si hay una version mas nueva.
APP_VERSION = "1.0.3"

# Repositorio publico donde se publican los releases con el APK.
GITHUB_REPO = "Alexander12Po/APK-PLAGA-DETECTOR-"

# Mismo prompt que usaba el backend original, para mantener la misma
# calidad y estructura de diagnóstico.
DIAGNOSIS_PROMPT = """Eres un ingeniero agrónomo experto en fitosanidad y control de plagas.
Observa la foto de la planta y responde ÚNICAMENTE con un objeto JSON válido,
sin texto adicional, sin explicaciones, sin markdown. Usa exactamente esta forma:

{
  "planta_identificada": "nombre común de la planta si es identificable, o 'planta no identificada'",
  "plaga_o_problema": "nombre de la plaga, enfermedad o problema detectado",
  "severidad": "alta" | "media" | "baja",
  "confianza": "breve frase sobre qué tan clara es la evidencia visual en la foto",
  "sintomas_observados": ["síntoma 1", "síntoma 2"],
  "pasos": ["paso 1 de tratamiento", "paso 2", "paso 3", "paso 4 opcional"],
  "productos_recomendados": ["producto comercial 1 (ej. fungicida a base de cobre)", "producto comercial 2"],
  "remedios_caseros": ["remedio casero 1 (ej. jabon potasico diluido)", "remedio casero 2"],
  "prevencion": "una recomendación breve para evitar que vuelva a ocurrir",
  "urgencia": "si requiere atención inmediata o puede esperar, en una frase"
}

Para "productos_recomendados": sugiere 1 a 3 productos AGRICOLAS reales y de
venta comun (fungicidas, insecticidas, abonos), con su ingrediente activo o
tipo, sin inventar una marca especifica.
Para "remedios_caseros": sugiere 1 a 3 alternativas caseras/organicas reales
y de bajo costo (ej. jabon potasico, extracto de ajo o aji, ceniza, aceite
de neem casero) que el agricultor pueda preparar con lo que tiene a mano.
Si el problema es leve o no requiere ningun producto, deja esas dos listas
vacias en vez de inventar algo innecesario.

Si la imagen no muestra una planta o no se aprecia ninguna plaga o enfermedad,
usa "plaga_o_problema": "No se detectó plaga visible" y ajusta pasos y
sintomas_observados a cuidados generales de mantenimiento.
Escribe todos los textos en español."""

# Colores tomados de la paleta original (:root del CSS de la web)
COLORS = {
    "green_700": "#0F6B4E",
    "green_600": "#12805E",
    "green_500": "#17976F",
    "green_50": "#EAF7F1",
    "surface": "#F6F8F7",
    "ink": "#101915",
    "ink_soft": "#5B6B62",
    "red_600": "#D8402E",
    "amber_600": "#C4801A",
}


def hex_to_rgba(hex_color, alpha=1):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return [r, g, b, alpha]


# ---------------------------------------------------------------------------
# Configuración local (API Key) — NUNCA se guarda en el código fuente
# ---------------------------------------------------------------------------


class ConfigManager:
    """Lee y escribe la clave de Gemini en un archivo JSON privado de la
    app (no incluido en el repositorio ni en el APK)."""

    @staticmethod
    def load_api_key() -> str:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                saved_key = data.get("gemini_api_key", "")
                if saved_key:
                    return saved_key
            except Exception:
                pass
        # Si el usuario no configuro una clave propia en Ajustes, se usa
        # la clave incluida por defecto en la app.
        return DEFAULT_GEMINI_API_KEY

    @staticmethod
    def save_api_key(key: str) -> None:
        CONFIG_FILE.write_text(
            json.dumps({"gemini_api_key": key.strip()}), encoding="utf-8"
        )


class BitacoraManager:
    """Guarda localmente los datos del predio del usuario (cultivo,
    variedad, fecha de siembra, superficie) para personalizar las
    alertas y recomendaciones."""

    @staticmethod
    def load() -> dict:
        if BITACORA_FILE.exists():
            try:
                return json.loads(BITACORA_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @staticmethod
    def save(cultivo="", variedad="", fecha_siembra="", superficie=""):
        BITACORA_FILE.write_text(
            json.dumps(
                {
                    "cultivo": cultivo.strip(),
                    "variedad": variedad.strip(),
                    "fecha_siembra": fecha_siembra.strip(),
                    "superficie": superficie.strip(),
                }
            ),
            encoding="utf-8",
        )


class HistoryManager:
    """Guarda cada diagnostico (con una copia de su foto) en un archivo
    local, para que el historial sobreviva a cerrar la app. Antes solo
    se guardaba el ultimo resultado en memoria y se perdia al cerrar."""

    MAX_ENTRADAS = 30

    @staticmethod
    def load() -> list:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    @classmethod
    def add(cls, diagnosis, foto_origen) -> None:
        try:
            HISTORY_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
            entradas = cls.load()

            foto_guardada = ""
            if foto_origen and os.path.exists(foto_origen):
                nombre = f"{int(time.time() * 1000)}.jpg"
                destino = HISTORY_PHOTOS_DIR / nombre
                shutil.copy(foto_origen, destino)
                foto_guardada = str(destino)

            entradas.insert(
                0,
                {
                    "fecha": time.strftime("%d/%m/%Y %H:%M"),
                    "foto": foto_guardada,
                    "diagnosis": diagnosis,
                },
            )
            entradas = entradas[: cls.MAX_ENTRADAS]
            HISTORY_FILE.write_text(json.dumps(entradas), encoding="utf-8")
        except Exception:
            _write_crash_log(
                "Error guardando en el historial (no crashea la app):\n"
                + traceback.format_exc()
            )


class WeatherClient:
    """Pronostico gratuito de Open-Meteo (no requiere clave de API)."""

    ENDPOINT = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_min,temperature_2m_max,precipitation_sum,"
        "precipitation_probability_max,wind_speed_10m_max,"
        "relative_humidity_2m_mean"
        "&timezone=auto&forecast_days=3"
    )

    @staticmethod
    def _valor(lista, i):
        try:
            return lista[i]
        except (TypeError, IndexError):
            return None

    @classmethod
    def get_forecast(cls, lat, lon):
        import requests  # import local: solo se necesita aqui

        url = cls.ENDPOINT.format(lat=lat, lon=lon)
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        fechas = daily.get("time", [])
        dias = []
        for i in range(len(fechas)):
            dias.append(
                {
                    "fecha": fechas[i],
                    "temp_min": cls._valor(daily.get("temperature_2m_min"), i),
                    "temp_max": cls._valor(daily.get("temperature_2m_max"), i),
                    "prob_lluvia": cls._valor(
                        daily.get("precipitation_probability_max"), i
                    ),
                    "viento_max": cls._valor(daily.get("wind_speed_10m_max"), i),
                    "humedad": cls._valor(
                        daily.get("relative_humidity_2m_mean"), i
                    ),
                }
            )
        return dias


def evaluar_riesgo_climatico(dias):
    """Reglas simples de alerta temprana a partir del pronostico de 3 dias.

    No reemplaza un modelo meteorologico real: son umbrales practicos
    (helada, granizada/tormenta, condiciones para hongos o plagas) para
    dar un aviso util con lo que ofrece una API gratuita."""
    riesgos = []
    for dia in dias:
        temp_min = dia.get("temp_min")
        prob_lluvia = dia.get("prob_lluvia") or 0
        viento = dia.get("viento_max") or 0
        humedad = dia.get("humedad") or 0

        if temp_min is not None and temp_min <= 2:
            riesgos.append(
                {
                    "fecha": dia["fecha"],
                    "tipo": "Helada",
                    "nivel": "alta" if temp_min <= 0 else "media",
                    "detalle": f"Temperatura minima prevista: {temp_min} grados.",
                }
            )
        if prob_lluvia >= 70 and viento >= 30:
            riesgos.append(
                {
                    "fecha": dia["fecha"],
                    "tipo": "Granizada o tormenta fuerte",
                    "nivel": "media",
                    "detalle": (
                        f"{prob_lluvia}% de probabilidad de lluvia con viento "
                        f"de hasta {viento} km/h."
                    ),
                }
            )
        if humedad >= 80 and temp_min is not None and temp_min >= 10:
            riesgos.append(
                {
                    "fecha": dia["fecha"],
                    "tipo": "Condiciones para hongos o plagas",
                    "nivel": "media",
                    "detalle": (
                        f"Humedad alta ({humedad}%) con clima templado: "
                        "vigila roya, rancha u otros hongos."
                    ),
                }
            )
    return riesgos


# ---------------------------------------------------------------------------
# Cliente de Gemini (llamada REST directa vía "requests", sin SDK pesado)
# ---------------------------------------------------------------------------


class SpeechManager:
    """Texto a voz propio via pyjnius, en vez de plyer.tts.

    plyer.tts en Android SIEMPRE fija el idioma a Locale.US por dentro
    (confirmado en su codigo fuente) y crea una instancia nueva de
    TextToSpeech cada vez que se llama, sin guardar ninguna referencia
    -> por eso no habia forma de elegir idioma ni de detener la voz una
    vez iniciada. Aca se guarda UNA sola instancia reutilizable para
    poder hacer ambas cosas, siguiendo el mismo patron de reintentos que
    ya usa plyer internamente (la primera llamada casi nunca funciona a
    la primera por un tema de tiempos de inicializacion de Android)."""

    _tts = None

    @classmethod
    def _get_engine(cls):
        if cls._tts is None:
            from jnius import autoclass

            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            cls._tts = TextToSpeech(PythonActivity.mActivity, None)
        return cls._tts

    @classmethod
    def speak(cls, texto, locale_code="es"):
        """Bloqueante: SIEMPRE llamar desde un hilo aparte, nunca desde
        el hilo principal. Devuelve True/False segun si se pudo hablar."""
        try:
            from jnius import autoclass

            Locale = autoclass("java.util.Locale")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")

            tts = cls._get_engine()
            resultado_idioma = tts.setLanguage(Locale(locale_code))
            if resultado_idioma in (-1, -2):
                # -1 = LANG_MISSING_DATA, -2 = LANG_NOT_SUPPORTED. La
                # mayoria de celulares NO traen una voz en quechua
                # instalada; se avisa aca pero se intenta leer igual
                # (puede sonar con acento incorrecto, o el sistema puede
                # simplemente no decir nada, segun el fabricante).
                _write_crash_log(
                    f"SpeechManager: idioma '{locale_code}' sin soporte "
                    f"completo en este celular (codigo={resultado_idioma})."
                )

            # IMPORTANTE: TextToSpeech.speak() tiene dos formas posibles.
            # La nueva (CharSequence, int, Bundle, String) le genera a
            # pyjnius una ambiguedad real entre CharSequence y String que
            # nunca logra resolver (falla siempre, con o sin Bundle real).
            # Se usa la forma vieja (String, int, HashMap), la misma que
            # usa la libreria plyer internamente y que si funciona.
            intentos = 0
            resultado = tts.speak(texto, TextToSpeech.QUEUE_FLUSH, None)
            while resultado == -1 and intentos < 100:
                time.sleep(0.1)
                intentos += 1
                resultado = tts.speak(texto, TextToSpeech.QUEUE_FLUSH, None)
            return resultado != -1
        except Exception:
            _write_crash_log(
                "SpeechManager.speak() fallo (no crashea la app):\n"
                + traceback.format_exc()
            )
            return False

    @classmethod
    def is_speaking_now(cls):
        """Consulta real al motor de Android (tts.isSpeaking()) para saber
        si todavia esta sonando. speak() NO espera a que termine (es
        asincrono en Android), asi que esto es lo unico confiable para
        saber cuando de verdad termino de hablar."""
        if cls._tts is None:
            return False
        try:
            return bool(cls._tts.isSpeaking())
        except Exception:
            return False

    @classmethod
    def stop(cls):
        if cls._tts is not None:
            try:
                cls._tts.stop()
            except Exception:
                pass


class UpdateChecker:
    """Consulta el ultimo release publicado en GitHub para avisar si hay
    una version mas nueva que la instalada. El workflow de GitHub Actions
    (.github/workflows/build.yml) es el que publica cada release con su
    tag de version y el APK adjunto."""

    @staticmethod
    def _version_a_tupla(version_texto):
        """Convierte '1.10.2' en (1, 10, 2) para comparar bien los
        numeros (comparar como texto ordenaria '1.9.0' por encima de
        '1.10.0', que esta mal)."""
        limpio = (version_texto or "").strip().lstrip("vV")
        partes = []
        for parte in limpio.split("."):
            digitos = "".join(c for c in parte if c.isdigit())
            partes.append(int(digitos) if digitos else 0)
        return tuple(partes) or (0,)

    @classmethod
    def hay_version_nueva(cls, version_actual, version_remota):
        try:
            return cls._version_a_tupla(version_remota) > cls._version_a_tupla(
                version_actual
            )
        except Exception:
            return False

    @classmethod
    def buscar_ultima_version(cls):
        """Bloqueante: llamar siempre desde un hilo aparte. Devuelve un
        dict {"version": "1.2.0", "url_descarga": "https://..."} o None
        si no hay internet, no hay releases todavia, o algo fallo (nunca
        interrumpe el arranque de la app)."""
        import requests  # import local: solo se necesita aqui

        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            resp = requests.get(
                url, timeout=15, headers={"Accept": "application/vnd.github+json"}
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            tag = data.get("tag_name", "")
            apk_url = None
            for asset in data.get("assets", []):
                if asset.get("name", "").lower().endswith(".apk"):
                    apk_url = asset.get("browser_download_url")
                    break
            if not tag or not apk_url:
                return None
            return {"version": tag, "url_descarga": apk_url}
        except Exception:
            return None


class GeminiClient:
    """Envía la imagen + el prompt a la API de Gemini y devuelve un dict
    con el diagnóstico. Se ejecuta siempre en un hilo aparte para no
    congelar la interfaz."""

    class GeminiError(Exception):
        pass

    @staticmethod
    def _extract_json(text: str) -> dict:
        cleaned = text.strip().replace("```json", "").replace("```", "").strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise GeminiClient.GeminiError(
                "La respuesta de la IA no trajo un JSON válido."
            )
        return json.loads(cleaned[start : end + 1])

    @classmethod
    def translate_text(cls, texto: str, idioma_destino: str, api_key: str) -> str:
        """Traduce un texto corto con Gemini (usado para leer el
        diagnostico en quechua). Sin reintentos con modelo de respaldo:
        es una funcion secundaria, si falla simplemente no se muestra
        la traduccion."""
        import requests  # import local: solo se necesita aqui

        url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key)
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                f"Traduce el siguiente texto al {idioma_destino} "
                                "de forma clara y natural, sin explicaciones "
                                "adicionales, solo la traduccion:\n\n" + texto
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "max_output_tokens": 1024,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    @classmethod
    def analyze_image(cls, image_path: str, api_key: str) -> dict:
        import requests  # import local: solo se necesita aquí

        if not api_key:
            raise cls.GeminiError(
                "No configuraste tu clave de Gemini. Ve a Ajustes y agrégala."
            )

        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        mime_type = "image/jpeg"
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith(".webp"):
            mime_type = "image/webp"

        url_principal = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key)
        url_respaldo = GEMINI_ENDPOINT.format(
            model=GEMINI_MODEL_FALLBACK, key=api_key
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": DIAGNOSIS_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "max_output_tokens": 2048,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }

        # Reintentos con espera creciente: 503 (servidor saturado) y los
        # cortes de conexion son casi siempre temporales (wifi/datos
        # inestables o un pico de demanda pasajero en Gemini). Si el
        # modelo principal sigue sin responder despues de sus reintentos,
        # se prueba automaticamente con el modelo de respaldo antes de
        # mostrarle el error al usuario.
        intentos_por_modelo = 2
        urls = [
            (GEMINI_MODEL, url_principal),
            (GEMINI_MODEL_FALLBACK, url_respaldo),
        ]
        ultimo_error = None
        resp = None
        for nombre_modelo, url in urls:
            for intento in range(1, intentos_por_modelo + 1):
                es_ultimo_intento_global = (
                    nombre_modelo == urls[-1][0] and intento == intentos_por_modelo
                )
                try:
                    resp = requests.post(url, json=payload, timeout=60)
                except requests.exceptions.RequestException as exc:
                    ultimo_error = cls.GeminiError(f"Error de conexión: {exc}")
                    if es_ultimo_intento_global:
                        raise ultimo_error from exc
                    time.sleep(2 * intento)
                    continue

                if resp.status_code in (429, 500, 503, 504):
                    ultimo_error = cls.GeminiError(
                        f"La API de Gemini ({nombre_modelo}) respondió con "
                        f"error {resp.status_code}: {resp.text[:200]}"
                    )
                    if es_ultimo_intento_global:
                        raise ultimo_error
                    time.sleep(2 * intento)
                    continue

                break
            else:
                continue
            break

        if resp.status_code != 200:
            raise cls.GeminiError(
                f"La API de Gemini respondió con error {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise cls.GeminiError(
                "La respuesta de Gemini no tuvo el formato esperado."
            ) from exc

        return cls._extract_json(text)


# ---------------------------------------------------------------------------
# Pantalla principal (los 3 pasos, todo en una sola pantalla con scroll,
# igual que en la web original)
# ---------------------------------------------------------------------------




class MainScreen(Screen):
    pass


class RoundIconButton(ButtonBehavior, FloatLayout):
    """Boton circular con icono (bocina, traducir, nav de Inicio, etc.).

    Antes era una clase dinamica definida solo en el .kv con '@'. Se paso
    a clase real de Python porque agregar la propiedad nueva "bg_color"
    (para poder pintar el boton de Quechua de otro color) causaba un
    crash real en el celular: 'rgba: root.bg_color' se evaluaba a None
    justo al construir el canvas, porque una propiedad recien creada
    dentro de una clase '@' no tiene su valor listo a tiempo cuando se
    usa en el canvas.before de esa misma clase. Con una Property real de
    Python, el valor por defecto ya existe desde antes de armar el
    canvas, asi que nunca es None.
    """

    icon = StringProperty("volume-high")
    bg_color = ListProperty([0.0706, 0.502, 0.369, 1])  # verde green_600


class HomeScreen(Screen):
    pass


class HistoryScreen(Screen):
    pass


class TipsScreen(Screen):
    pass


class MoreScreen(Screen):
    pass


class AgrowillayApp(MDApp):
    theme_color = hex_to_rgba(COLORS["green_600"])
    current_image_path = StringProperty("")
    speaking_lang = StringProperty("")  # "" | "es" | "qu" -- cual boton
    # de audio esta realmente sonando ahora mismo. Antes speak_btn y
    # speak_qu_btn compartian is_speaking, asi que tocar cualquiera de
    # los dos prendia el icono de "detener" en LOS DOS a la vez.
    _speech_token = 0
    last_diagnosis = ObjectProperty(None, allownone=True)
    current_tab = StringProperty("home")
    _update_dialog = None
    _update_progress_bar = None
    _update_progress_label = None

    def build(self):
        self.title = "Agrowillay"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.theme_style = "Dark"
        self.icon = "assets/icon.png"
        kv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "agrowillay_ui.kv"
        )
        return Builder.load_file(kv_path)

    def on_start(self):
        # En segundo plano, sin bloquear el arranque ni molestar si no
        # hay internet: se fija si hay un release mas nuevo publicado.
        threading.Thread(target=self._check_updates_thread, daemon=True).start()

    def _check_updates_thread(self):
        info = UpdateChecker.buscar_ultima_version()
        if not info:
            return
        if UpdateChecker.hay_version_nueva(APP_VERSION, info["version"]):
            Clock.schedule_once(lambda dt: self._mostrar_dialogo_actualizacion(info))

    def _mostrar_dialogo_actualizacion(self, info):
        from kivymd.uix.dialog import MDDialog
        from kivymd.uix.button import MDFlatButton

        def _descargar(*_a):
            dialog.dismiss()
            self._iniciar_descarga_actualizacion(info["url_descarga"], info["version"])

        dialog = MDDialog(
            title="Nueva version disponible",
            text=(
                f"Hay una version nueva de Agrowillay ({info['version']}).\n"
                f"Tienes instalada: v{APP_VERSION}."
            ),
            auto_dismiss=False,
            buttons=[
                MDFlatButton(
                    text="MAS TARDE", on_release=lambda x: dialog.dismiss()
                ),
                MDFlatButton(
                    text="DESCARGAR",
                    text_color=self.theme_color,
                    on_release=_descargar,
                ),
            ],
        )
        dialog.open()

    # ------------------------------------------------------------------
    # Descarga del APK con barra de progreso + instalacion automatica
    # ------------------------------------------------------------------

    def _iniciar_descarga_actualizacion(self, url_descarga, version):
        from kivymd.uix.dialog import MDDialog
        from kivymd.uix.progressbar import MDProgressBar

        self._update_progress_bar = MDProgressBar(value=0, max=100)
        self._update_progress_label = MDLabel(
            text="Descargando 0%",
            halign="center",
            size_hint_y=None,
            height=dp(30),
        )
        contenido = MDBoxLayout(
            orientation="vertical",
            spacing=dp(12),
            size_hint_y=None,
            height=dp(70),
        )
        contenido.add_widget(self._update_progress_label)
        contenido.add_widget(self._update_progress_bar)

        self._update_dialog = MDDialog(
            title=f"Descargando actualizacion ({version})",
            type="custom",
            content_cls=contenido,
            auto_dismiss=False,
        )
        self._update_dialog.open()

        threading.Thread(
            target=self._descargar_apk_thread, args=(url_descarga,), daemon=True
        ).start()

    def _descargar_apk_thread(self, url_descarga):
        import requests

        apk_path = str(APP_DATA_DIR / "actualizacion.apk")
        try:
            resp = requests.get(url_descarga, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or None
            descargado = 0
            with open(apk_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    descargado += len(chunk)
                    if total:
                        porcentaje = int(descargado * 100 / total)
                        Clock.schedule_once(
                            lambda dt, p=porcentaje: self._actualizar_progreso(p)
                        )
            Clock.schedule_once(lambda dt: self._descarga_completa(apk_path))
        except Exception as exc:  # noqa: BLE001
            Clock.schedule_once(lambda dt: self._descarga_fallo(str(exc)))

    @mainthread
    def _actualizar_progreso(self, porcentaje):
        if self._update_progress_bar:
            self._update_progress_bar.value = porcentaje
        if self._update_progress_label:
            self._update_progress_label.text = f"Descargando {porcentaje}%"

    @mainthread
    def _descarga_completa(self, apk_path):
        if self._update_dialog:
            self._update_dialog.dismiss()
            self._update_dialog = None
        toast("Descarga completa, abriendo instalador...")
        self._instalar_apk(apk_path)

    @mainthread
    def _descarga_fallo(self, mensaje_error):
        if self._update_dialog:
            self._update_dialog.dismiss()
            self._update_dialog = None
        toast(f"No se pudo descargar la actualizacion: {mensaje_error}")

    @staticmethod
    def _instalar_apk(apk_path):
        if platform != "android":
            toast("La instalacion solo esta disponible en el celular")
            return
        try:
            from jnius import autoclass
            from android import mActivity

            Intent = autoclass("android.content.Intent")
            FileProviderCls = autoclass("androidx.core.content.FileProvider")
            JavaFile = autoclass("java.io.File")

            apk_file = JavaFile(apk_path)
            authority = f"{mActivity.getPackageName()}.fileprovider"
            apk_uri = FileProviderCls.getUriForFile(mActivity, authority, apk_file)

            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(apk_uri, "application/vnd.android.package-archive")
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            mActivity.startActivity(intent)
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo abrir el instalador: {exc}")

    # ------------------------------------------------------------------
    # Navegacion entre pantallas (barra inferior)
    # ------------------------------------------------------------------

    def _go(self, screen_name, tab_name):
        try:
            self.root.ids.sm.current = screen_name
            self.current_tab = tab_name
        except Exception:  # noqa: BLE001
            _write_crash_log(
                f"Error navegando a {screen_name} (no crashea la app):\n"
                + traceback.format_exc()
            )

    def go_home(self):
        self._go("home", "home")

    def go_diagnosis(self):
        self._go("main", "diagnosis")

    def go_history(self):
        self._go("history", "history")
        self._refresh_history()

    def go_tips(self):
        self._go("tips", "tips")

    def go_more(self):
        self._go("more", "more")

    def home_take_photo(self):
        self.go_diagnosis()
        self.take_photo()

    def home_choose_gallery(self):
        self.go_diagnosis()
        self.choose_from_gallery()

    def home_show_help(self):
        self.go_diagnosis()
        self.locate_nearby()

    def _refresh_history(self):
        """Llena la pestana 'Diagnosticos' con una tarjeta compacta por
        cada diagnostico guardado (mas reciente primero). Al tocar una
        tarjeta se abre el detalle completo en un dialogo."""
        try:
            history_screen = self.root.ids.sm.get_screen("history")
        except Exception:
            return

        try:
            placeholder = history_screen.ids.history_placeholder
            body = history_screen.ids.history_body
            entradas = HistoryManager.load()

            body.clear_widgets()

            if not entradas:
                self._show_card(placeholder)
                self._hide_card(body)
                return

            self._hide_card(placeholder)
            self._show_card(body)

            for entrada in entradas:
                diagnosis = entrada.get("diagnosis") or {}
                planta = self._txt(diagnosis.get("planta_identificada"))
                severidad = self._txt(diagnosis.get("severidad"), "").upper()
                resumen = f"{planta}" + (f" - {severidad}" if severidad else "")

                card = self._make_widget(
                    "HistoryEntryCard",
                    foto=entrada.get("foto", ""),
                    fecha=entrada.get("fecha", ""),
                    resumen=resumen,
                )
                card.bind(
                    on_release=lambda *_a, e=entrada: self.show_history_detail(e)
                )
                body.add_widget(card)
        except Exception:  # noqa: BLE001
            _write_crash_log(
                "Error refrescando historial (no crashea la app):\n"
                + traceback.format_exc()
            )

    def show_history_detail(self, entrada):
        from kivymd.uix.dialog import MDDialog
        from kivymd.uix.button import MDFlatButton
        from kivy.uix.scrollview import ScrollView

        try:
            contenedor = MDBoxLayout(
                orientation="vertical",
                spacing=dp(12),
                adaptive_height=True,
                padding=(0, dp(10)),
            )

            foto = entrada.get("foto", "")
            if foto and os.path.exists(foto):
                from kivy.uix.image import Image as KivyImage

                contenedor.add_widget(
                    KivyImage(
                        source=foto,
                        size_hint_y=None,
                        height=dp(180),
                        allow_stretch=True,
                        keep_ratio=True,
                    )
                )

            self._render_diagnosis(contenedor, entrada.get("diagnosis") or {})

            scroll = ScrollView(size_hint_y=None, height=dp(420))
            scroll.add_widget(contenedor)

            dialog = MDDialog(
                title=entrada.get("fecha", "Diagnostico"),
                type="custom",
                content_cls=scroll,
                buttons=[
                    MDFlatButton(
                        text="CERRAR", on_release=lambda x: dialog.dismiss()
                    ),
                ],
            )
            dialog.open()
        except Exception:
            _write_crash_log(
                "Error mostrando detalle del historial (no crashea la app):\n"
                + traceback.format_exc()
            )
            toast("No se pudo mostrar ese diagnostico.")

    @staticmethod
    def _simple_dialog(title, text):
        from kivymd.uix.dialog import MDDialog
        from kivymd.uix.button import MDFlatButton

        dialog = MDDialog(
            title=title,
            text=text,
            buttons=[
                MDFlatButton(text="CERRAR", on_release=lambda x: dialog.dismiss())
            ],
        )
        dialog.open()

    def show_language_options(self):
        self._simple_dialog(
            "Idioma",
            "El cambio de idioma Espanol/Quechua estara disponible "
            "proximamente para toda la app.",
        )

    def show_about_dialog(self):
        self._simple_dialog(
            "Acerca de Agrowillay",
            "Agrowillay ayuda a identificar plagas y enfermedades en "
            "plantas usando inteligencia artificial, pensada para "
            "agricultores de Curahuasi y la region de Apurimac.",
        )

    def show_help_dialog(self):
        self._simple_dialog(
            "Ayuda",
            "1) Toma o sube una foto de la planta.\n"
            "2) Toca 'Analizar planta'.\n"
            "3) Revisa el diagnostico y la ayuda cercana.\n\n"
            "Si algo falla, revisa tu conexion a internet e intenta de nuevo.",
        )

    # ------------------------------------------------------------------
    # Paso 1: seleccionar / tomar foto
    # ------------------------------------------------------------------

    def take_photo(self):
        """Abre la camara nativa usando un Intent + FileProvider directamente
        (sin pasar por plyer.camera).

        plyer.camera.take_picture() esta roto en Android 7+ (API 24+): pasa
        una URI "file://" cruda al Intent de la camara, lo cual esta
        prohibido desde Android Nougat y provoca un
        "FileUriExposedException" que tumba la funcion (aunque no toda la
        app, porque el error se atrapa aca). La solucion correcta es
        generar una URI "content://" con un FileProvider, que es lo que
        hace este metodo.
        """
        if platform != "android":
            toast("La camara solo esta disponible en el celular")
            return

        try:
            from jnius import autoclass, cast
            from android import activity, mActivity

            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            FileProviderCls = autoclass("androidx.core.content.FileProvider")
            JavaFile = autoclass("java.io.File")

            photo_file = JavaFile(CAMERA_PHOTO_PATH)
            if photo_file.exists():
                photo_file.delete()
            photo_file.getParentFile().mkdirs()

            authority = f"{mActivity.getPackageName()}.fileprovider"
            photo_uri = FileProviderCls.getUriForFile(
                mActivity, authority, photo_file
            )

            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(
                MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", photo_uri)
            )
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

            # Nos aseguramos de no acumular binds repetidos si el usuario
            # toca "Tomar foto" varias veces.
            try:
                activity.unbind(on_activity_result=self._on_camera_activity_result)
            except Exception:
                pass
            activity.bind(on_activity_result=self._on_camera_activity_result)

            mActivity.startActivityForResult(intent, CAMERA_REQUEST_CODE)
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo abrir la camara: {exc}")

    @mainthread
    def _on_camera_activity_result(self, request_code, result_code, data):
        if request_code != CAMERA_REQUEST_CODE:
            return

        try:
            from android import activity

            activity.unbind(on_activity_result=self._on_camera_activity_result)
        except Exception:
            pass

        RESULT_OK = -1  # android.app.Activity.RESULT_OK
        if result_code != RESULT_OK:
            # El usuario cancelo la foto, no es un error.
            return

        try:
            if os.path.exists(CAMERA_PHOTO_PATH) and os.path.getsize(
                CAMERA_PHOTO_PATH
            ) > 0:
                self._set_preview_image(CAMERA_PHOTO_PATH)
            else:
                toast("No se pudo obtener la foto")
        except Exception as exc:  # noqa: BLE001
            toast(f"Error al procesar la foto: {exc}")

    def choose_from_gallery(self):
        try:
            from plyer import filechooser
        except Exception:
            toast("El selector de archivos no esta disponible")
            return

        try:
            filechooser.open_file(
                on_selection=self._on_file_chosen,
                filters=[("Imagenes", "*.jpg", "*.jpeg", "*.png", "*.webp")],
            )
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo abrir la galeria: {exc}")

    def _on_file_chosen(self, selection):
        # OJO: este callback puede llegar desde el hilo de UI de Android,
        # por eso se agenda con Clock.schedule_once. Todo lo que pasa
        # DENTRO del lambda debe ir protegido con try/except: si no, una
        # excepcion ahi tumba la app entera sin pasar por ningun toast.
        Clock.schedule_once(lambda dt: self._handle_gallery_selection(selection))

    def _handle_gallery_selection(self, selection):
        try:
            if not selection:
                return

            path = selection[0]

            # En Android, plyer a veces no logra resolver el content://
            # que entrega Google Fotos / la galeria a una ruta de archivo
            # real (pasa sobre todo con "almacenamiento con alcance" en
            # Android 10+), y en ese caso devuelve una lista vacia o algo
            # que no es una ruta usable. Antes esto hacia crashear toda la
            # app; ahora simplemente avisamos y no rompemos nada.
            if not isinstance(path, str) or not path or not os.path.exists(path):
                toast(
                    "No se pudo leer esa imagen. Prueba con otra foto "
                    "o usa 'Tomar foto'."
                )
                return

            self._set_preview_image(path)
        except Exception as exc:  # noqa: BLE001
            toast(f"Error al procesar la imagen: {exc}")

    @staticmethod
    def _prepare_image_for_use(path):
        """Reduce el tamano de la foto si es muy grande.

        Las fotos de camara modernas pueden pesar 4000x3000px o mas. Eso
        puede agotar la memoria (OOM) tanto al crear la textura para la
        previsualizacion como al generar el base64 para subir a Gemini, y
        ese tipo de error puede tumbar la app a nivel NATIVO, sin pasar
        por ningun try/except de Python (por eso no dejaba log antes).
        """
        try:
            from PIL import Image as PILImage

            img = PILImage.open(path)
            img = img.convert("RGB")
            max_side = 1600
            w, h = img.size
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    PILImage.LANCZOS,
                )
            out_path = str(APP_DATA_DIR / "preview_resized.jpg")
            img.save(out_path, "JPEG", quality=85)
            return out_path
        except Exception:
            # Si Pillow no esta disponible o algo sale mal, seguimos con
            # la imagen original: mejor eso que tronar la app.
            _write_crash_log(
                "No se pudo reducir la imagen, se usa la original:\n"
                + traceback.format_exc()
            )
            return path

    def _set_preview_image(self, path):
        try:
            path = self._prepare_image_for_use(path)
            main_screen = self.root.ids.sm.get_screen("main")
            self.current_image_path = path

            preview = main_screen.ids.preview_image
            Animation.cancel_all(preview, "opacity")
            preview.opacity = 0
            preview.source = path
            preview.reload()
            Animation(opacity=1, duration=0.35, t="out_quad").start(preview)

            main_screen.ids.preview_placeholder.opacity = 0
            main_screen.ids.analyze_btn.disabled = False

            # Si el usuario cambia la foto, oculta resultados anteriores.
            self._hide_card(main_screen.ids.result_card)
            self._hide_card(main_screen.ids.locator_card)
            main_screen.ids.speak_btn.disabled = True
            main_screen.ids.speak_qu_btn.disabled = True
            self.last_diagnosis = None
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo mostrar la imagen: {exc}")

    def clear_photo(self):
        """Quita la foto seleccionada y vuelve al estado 'sin foto'."""
        main_screen = self.root.ids.sm.get_screen("main")
        self.current_image_path = ""

        preview = main_screen.ids.preview_image
        Animation.cancel_all(preview, "opacity")
        preview.opacity = 0
        preview.source = ""

        main_screen.ids.preview_placeholder.opacity = 1
        main_screen.ids.analyze_btn.disabled = True

        self._hide_card(main_screen.ids.result_card)
        self._hide_card(main_screen.ids.locator_card)
        main_screen.ids.speak_btn.disabled = True
        main_screen.ids.speak_qu_btn.disabled = True
        self.last_diagnosis = None

    # ------------------------------------------------------------------
    # Paso 2: analizar con Gemini (en un hilo aparte -> no bloquea la UI)
    # ------------------------------------------------------------------

    def analyze_photo(self):
        if not self.current_image_path:
            toast("Primero selecciona o toma una foto")
            return

        api_key = ConfigManager.load_api_key()
        if not api_key:
            toast("No hay una clave de Gemini configurada en la app")
            return

        main_screen = self.root.ids.sm.get_screen("main")
        main_screen.ids.analyze_btn.disabled = True
        main_screen.ids.analyze_btn.text = "Analizando..."
        main_screen.ids.analyze_spinner.active = True
        Animation(opacity=1, duration=0.2).start(main_screen.ids.analyze_spinner)

        # La llamada de red va en un hilo para no congelar la interfaz.
        thread = threading.Thread(
            target=self._run_analysis,
            args=(self.current_image_path, api_key),
            daemon=True,
        )
        thread.start()

    def _run_analysis(self, image_path, api_key):
        try:
            diagnosis = GeminiClient.analyze_image(image_path, api_key)
        except GeminiClient.GeminiError as exc:
            _write_crash_log(
                "Error de GeminiClient al analizar (la app sigue abierta):\n"
                + traceback.format_exc()
            )
            # OJO: 'exc' se borra automaticamente al salir de este bloque
            # 'except' (asi funciona Python 3), y el lambda de abajo se
            # ejecuta MAS TARDE via Clock, cuando 'exc' ya no existe. Por
            # eso el mensaje se calcula aqui mismo, antes de programarlo.
            msg = self._safe_msg(exc)
            Clock.schedule_once(lambda dt: self._on_analysis_error(msg))
            return
        except Exception as exc:  # noqa: BLE001
            _write_crash_log(
                "Error inesperado al analizar (la app sigue abierta):\n"
                + traceback.format_exc()
            )
            msg = self._safe_msg(exc)
            Clock.schedule_once(lambda dt: self._on_analysis_error(msg))
            return

        Clock.schedule_once(lambda dt: self._on_analysis_success(diagnosis))

    @staticmethod
    def _safe_msg(exc):
        """str(exc) puede salir vacio o literalmente 'None' con ciertos
        errores de red mal formados; en ese caso mostramos algo util."""
        text = str(exc).strip()
        if not text or text == "None":
            return (
                "No se pudo conectar con el servidor de la IA. "
                "Revisa tu conexion a internet e intenta de nuevo."
            )
        return text

    @mainthread
    def _on_analysis_error(self, message):
        main_screen = self.root.ids.sm.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        main_screen.ids.analyze_spinner.active = False
        Animation(opacity=0, duration=0.2).start(main_screen.ids.analyze_spinner)
        toast(f"Error: {message}")

    @mainthread
    def _on_analysis_success(self, diagnosis):
        main_screen = self.root.ids.sm.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        main_screen.ids.analyze_spinner.active = False
        Animation(opacity=0, duration=0.2).start(main_screen.ids.analyze_spinner)

        try:
            self._render_diagnosis(main_screen.ids.result_body, diagnosis)
        except Exception as exc:  # noqa: BLE001
            # Pase lo que pase con el formato de la respuesta de la IA, la
            # app NUNCA debe cerrarse por esto: mostramos un aviso y ya.
            toast("No se pudo mostrar el diagnostico. Intenta de nuevo.")
            _write_crash_log(
                "Error mostrando diagnostico (no crashea la app):\n"
                + traceback.format_exc()
                + f"\ndiagnosis recibido: {diagnosis!r}"
            )
            return

        self.last_diagnosis = diagnosis
        HistoryManager.add(diagnosis, self.current_image_path)
        self._refresh_history()

        try:
            speak_btn = main_screen.ids.speak_btn
            speak_btn.disabled = False
            main_screen.ids.speak_qu_btn.disabled = False
            Animation.cancel_all(speak_btn, "size")
            base_size = speak_btn.size[:]
            speak_btn.size = (base_size[0] * 0.6, base_size[1] * 0.6)
            Animation(
                size=base_size, duration=0.35, t="out_back"
            ).start(speak_btn)
            self._show_card(main_screen.ids.result_card)

            # Igual que en la web: apenas hay diagnostico, se busca ayuda cercana.
            self.locate_nearby()
        except Exception:  # noqa: BLE001
            # Si algo falla ACA (por ejemplo el GPS o un id del .kv), el
            # diagnostico ya se mostro correctamente: no debe cerrar la app.
            _write_crash_log(
                "Error despues de mostrar el diagnostico (no crashea la app):\n"
                + traceback.format_exc()
            )
            toast("No se pudo cargar la ayuda cercana, pero el diagnostico es correcto")

    @staticmethod
    def _txt(value, default="-"):
        """Convierte cualquier valor (incluido None) a texto seguro para
        mostrar, sin tronar si la IA devolvio null en vez de un string."""
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _make_widget(cls_name, **kwargs):
        """Crea un widget de una clase dinamica del .kv (definida con @,
        como SeverityChip o IconRow) SIN pasarle propiedades al
        constructor. En KivyMD, MDBoxLayout mezcla BackgroundColorBehavior,
        cuyo __init__ propio no reconoce las propiedades que agrega la
        regla del .kv (ej. 'chip_color', 'icon_color') si se las pasamos
        como kwargs -> TypeError: 'may not be existing property names'.
        Crear el widget vacio y asignar los atributos despues evita el
        problema por completo (confirmado con el traceback real)."""
        widget = getattr(Factory, cls_name)()
        for key, value in kwargs.items():
            setattr(widget, key, value)
        return widget

    def _render_diagnosis(self, body, diagnosis):
        if not isinstance(diagnosis, dict):
            diagnosis = {}

        severidad_raw = self._txt(diagnosis.get("severidad"), "media").lower()
        color_map = {"alta": "red_600", "media": "amber_600", "baja": "green_600"}
        chip_color = hex_to_rgba(COLORS.get(color_map.get(severidad_raw, "amber_600")))

        body.clear_widgets()

        body.add_widget(
            self._make_field_row(
                "Planta", self._txt(diagnosis.get("planta_identificada"))
            )
        )
        body.add_widget(
            self._make_field_row(
                "Problema", self._txt(diagnosis.get("plaga_o_problema"))
            )
        )

        severity_row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
        severity_row.add_widget(
            MDLabel(
                text="[b]Severidad:[/b]",
                markup=True,
                adaptive_height=True,
                size_hint_x=None,
                width=dp(96),
            )
        )
        severity_row.add_widget(
            self._make_widget(
                "SeverityChip", text=severidad_raw.upper(), chip_color=chip_color
            )
        )
        body.add_widget(severity_row)

        sintomas = diagnosis.get("sintomas_observados") or []
        if not isinstance(sintomas, list):
            sintomas = [sintomas]
        sintomas = [self._txt(s, "") for s in sintomas]
        sintomas = [s for s in sintomas if s]
        if sintomas:
            body.add_widget(
                MDLabel(
                    text="[b]Sintomas observados[/b]",
                    markup=True,
                    adaptive_height=True,
                )
            )
            for sintoma in sintomas:
                body.add_widget(
                    self._make_widget(
                        "IconRow",
                        icon="alert-circle-outline",
                        icon_color=hex_to_rgba(COLORS["amber_600"]),
                        text=sintoma,
                    )
                )

        pasos = diagnosis.get("pasos") or []
        if not isinstance(pasos, list):
            pasos = [pasos]
        pasos = [self._txt(p, "") for p in pasos]
        pasos = [p for p in pasos if p]
        if pasos:
            body.add_widget(
                MDLabel(
                    text="[b]Plan de tratamiento[/b]",
                    markup=True,
                    adaptive_height=True,
                )
            )
            for paso in pasos:
                body.add_widget(
                    self._make_widget(
                        "IconRow",
                        icon="check-circle-outline",
                        icon_color=self.theme_color,
                        text=paso,
                    )
                )

        productos = diagnosis.get("productos_recomendados") or []
        if not isinstance(productos, list):
            productos = [productos]
        productos = [self._txt(p, "") for p in productos]
        productos = [p for p in productos if p]
        if productos:
            body.add_widget(
                MDLabel(
                    text="[b]Productos recomendados[/b]",
                    markup=True,
                    adaptive_height=True,
                )
            )
            for producto in productos:
                body.add_widget(
                    self._make_widget(
                        "IconRow",
                        icon="bottle-tonic-outline",
                        icon_color=hex_to_rgba(COLORS["amber_600"]),
                        text=producto,
                    )
                )

        remedios = diagnosis.get("remedios_caseros") or []
        if not isinstance(remedios, list):
            remedios = [remedios]
        remedios = [self._txt(r, "") for r in remedios]
        remedios = [r for r in remedios if r]
        if remedios:
            body.add_widget(
                MDLabel(
                    text="[b]Remedios caseros[/b]",
                    markup=True,
                    adaptive_height=True,
                )
            )
            for remedio in remedios:
                body.add_widget(
                    self._make_widget(
                        "IconRow",
                        icon="leaf-circle-outline",
                        icon_color=self.theme_color,
                        text=remedio,
                    )
                )

        prevencion = self._txt(diagnosis.get("prevencion"), "")
        if prevencion:
            body.add_widget(
                self._make_widget(
                    "IconRow",
                    icon="shield-check-outline",
                    icon_color=self.theme_color,
                    text=f"[b]Prevencion:[/b] {prevencion}",
                )
            )

        urgencia = self._txt(diagnosis.get("urgencia"), "")
        if urgencia:
            body.add_widget(
                self._make_widget(
                    "IconRow",
                    icon="clock-alert-outline",
                    icon_color=hex_to_rgba(COLORS["red_600"]),
                    text=f"[b]Urgencia:[/b] {urgencia}",
                )
            )

    @staticmethod
    def _make_field_row(label, value):
        row = MDBoxLayout(adaptive_height=True, spacing=dp(8))
        row.add_widget(
            MDLabel(
                text=f"[b]{label}:[/b]",
                markup=True,
                adaptive_height=True,
                size_hint_x=None,
                width=dp(96),
            )
        )
        row.add_widget(MDLabel(text=value, adaptive_height=True))
        return row

    @staticmethod
    def _show_card(card):
        card.disabled = False
        Animation.cancel_all(card, "opacity", "y")
        target_y = card.y
        card.y = target_y - dp(16)
        Animation(
            opacity=1, y=target_y, duration=0.45, t="out_cubic"
        ).start(card)

    @staticmethod
    def _hide_card(card):
        card.disabled = True
        Animation.cancel_all(card, "opacity", "y")
        Animation(opacity=0, duration=0.2, t="out_quad").start(card)

    # ------------------------------------------------------------------
    # Alertas climaticas (Open-Meteo, gratis) + bitacora del predio
    # ------------------------------------------------------------------

    def check_weather_alerts(self):
        home_screen = self.root.ids.sm.get_screen("home")
        box = home_screen.ids.alerts_body
        box.clear_widgets()
        box.add_widget(
            MDLabel(
                text="Buscando tu ubicacion (puede tardar unos segundos)...",
                theme_text_color="Hint",
                adaptive_height=True,
            )
        )
        _write_crash_log("GPS-CLIMA: check_weather_alerts() llamado.")
        try:
            from plyer import gps

            _write_crash_log("GPS-CLIMA: import plyer.gps OK, llamando configure()...")
            gps.configure(
                on_location=self._on_weather_gps, on_status=self._on_gps_status
            )
            _write_crash_log("GPS-CLIMA: configure() OK, llamando start()...")
            gps.start(minTime=1000, minDistance=1)
            _write_crash_log("GPS-CLIMA: start() no lanzo excepcion. Esperando...")
            Clock.schedule_once(self._weather_gps_timeout, 20)
        except Exception:
            _write_crash_log(
                "GPS-CLIMA: excepcion en configure()/start():\n"
                + traceback.format_exc()
            )
            self._show_weather_error(
                "No se pudo acceder al GPS. Revisa que la ubicacion "
                "este activada en tu celular y que le diste permiso a la app."
            )

    @mainthread
    def _on_gps_status(self, stype, status):
        """Esto lo llama Android directamente (aunque el usuario ya dio el
        permiso) para avisar del ESTADO del proveedor de ubicacion. Antes
        se ignoraba por completo; ahora se guarda para saber la causa
        real si el GPS nunca responde.

        IMPORTANTE: este callback llega desde el hilo de Android, no desde
        el hilo de Kivy. @mainthread lo reencola en el hilo correcto; sin
        esto, tocar widgets aca puede fallar en silencio."""
        _write_crash_log(f"GPS-CLIMA: on_status -> tipo={stype!r} status={status!r}")
        if status == "provider-disabled":
            self._gps_disabled_count = getattr(self, "_gps_disabled_count", 0) + 1
            _write_crash_log(
                f"GPS-CLIMA: contador provider-disabled = {self._gps_disabled_count}"
            )
            # Si TODOS los proveedores avisan disabled, la ubicacion del
            # sistema (no el permiso de la app) esta apagada. No tiene
            # sentido esperar los 20s: avisamos ya y ofrecemos abrir Ajustes.
            if self._gps_disabled_count >= 4:
                _write_crash_log(
                    "GPS-CLIMA: 4 providers disabled detectados, "
                    "mostrando aviso inmediato (sin esperar timeout)."
                )
                try:
                    from plyer import gps

                    gps.stop()
                except Exception:
                    pass
                self._gps_disabled_count = 0
                try:
                    self._show_weather_error(
                        "La ubicacion de tu celular esta APAGADA (no es un "
                        "tema de permisos). Toca aqui para abrir Ajustes y "
                        "activarla.",
                        on_press=self._open_location_settings,
                    )
                    _write_crash_log("GPS-CLIMA: _show_weather_error OK.")
                except Exception:
                    _write_crash_log(
                        "GPS-CLIMA: excepcion mostrando el aviso:\n"
                        + traceback.format_exc()
                    )

    def _open_location_settings(self, *args):
        """Abre directamente la pantalla de Ajustes > Ubicacion del sistema."""
        if platform != "android":
            return
        try:
            from jnius import autoclass

            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            intent = Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
            PythonActivity.mActivity.startActivity(intent)
        except Exception:
            _write_crash_log(
                "GPS-CLIMA: no se pudo abrir Ajustes de ubicacion:\n"
                + traceback.format_exc()
            )

    def _weather_gps_timeout(self, dt):
        _write_crash_log("GPS-CLIMA: se cumplieron los 20s de espera (timeout).")
        home_screen = self.root.ids.sm.get_screen("home")
        if len(home_screen.ids.alerts_body.children) == 1:
            self._show_weather_error(
                "No se pudo obtener tu ubicacion. Activa el GPS en tu "
                "celular (mejor al aire libre) y vuelve a intentar."
            )

    @mainthread
    def _on_weather_gps(self, **kwargs):
        _write_crash_log(f"GPS-CLIMA: on_location -> kwargs={kwargs!r}")
        lat, lon = kwargs.get("lat"), kwargs.get("lon")
        try:
            from plyer import gps

            gps.stop()
        except Exception:
            pass
        if not (lat and lon):
            self._show_weather_error(
                "No se pudo obtener tu ubicacion. Activa el GPS e intenta de nuevo."
            )
            return
        threading.Thread(
            target=self._fetch_weather_thread, args=(lat, lon), daemon=True
        ).start()

    def _fetch_weather_thread(self, lat, lon):
        try:
            dias = WeatherClient.get_forecast(lat, lon)
            riesgos = evaluar_riesgo_climatico(dias)
        except Exception:
            _write_crash_log(
                "Error consultando el clima (no crashea la app):\n"
                + traceback.format_exc()
            )
            Clock.schedule_once(
                lambda dt: self._show_weather_error(
                    "No se pudo consultar el clima. Revisa tu conexion."
                )
            )
            return
        Clock.schedule_once(lambda dt: self._render_weather_alerts(riesgos))

    @mainthread
    def _render_weather_alerts(self, riesgos):
        home_screen = self.root.ids.sm.get_screen("home")
        box = home_screen.ids.alerts_body
        box.clear_widgets()

        if not riesgos:
            box.add_widget(
                self._make_widget(
                    "IconRow",
                    icon="check-circle-outline",
                    icon_color=self.theme_color,
                    text="Sin riesgos climaticos relevantes en los proximos 3 dias.",
                )
            )
            return

        for riesgo in riesgos:
            color_key = "red_600" if riesgo["nivel"] == "alta" else "amber_600"
            box.add_widget(
                self._make_widget(
                    "IconRow",
                    icon="alert-outline",
                    icon_color=hex_to_rgba(COLORS[color_key]),
                    text=(
                        f"[b]{riesgo['tipo']}[/b] ({riesgo['fecha']}): "
                        f"{riesgo['detalle']}"
                    ),
                )
            )

    @mainthread
    def _show_weather_error(self, mensaje="No se pudo consultar el clima.", on_press=None):
        home_screen = self.root.ids.sm.get_screen("home")
        box = home_screen.ids.alerts_body
        box.clear_widgets()
        box.add_widget(
            MDLabel(text=mensaje, theme_text_color="Hint", adaptive_height=True)
        )
        if on_press is not None:
            box.add_widget(
                MDRaisedButton(
                    text="Activar ubicacion",
                    on_release=on_press,
                    pos_hint={"center_x": 0.5},
                )
            )

    def open_bitacora_dialog(self):
        from kivymd.uix.textfield import MDTextField
        from kivymd.uix.dialog import MDDialog
        from kivymd.uix.button import MDFlatButton

        datos = BitacoraManager.load()

        content = MDBoxLayout(
            orientation="vertical",
            spacing=dp(10),
            adaptive_height=True,
            padding=(0, dp(10)),
        )
        campo_cultivo = MDTextField(
            text=datos.get("cultivo", ""), hint_text="Cultivo (ej. maiz, papa)"
        )
        campo_variedad = MDTextField(
            text=datos.get("variedad", ""), hint_text="Variedad"
        )
        campo_fecha = MDTextField(
            text=datos.get("fecha_siembra", ""),
            hint_text="Fecha de siembra (dd/mm/aaaa)",
        )
        campo_superficie = MDTextField(
            text=datos.get("superficie", ""),
            hint_text="Superficie (ej. 0.5 hectareas)",
        )
        for campo in (campo_cultivo, campo_variedad, campo_fecha, campo_superficie):
            content.add_widget(campo)

        def _guardar(*_a):
            BitacoraManager.save(
                cultivo=campo_cultivo.text,
                variedad=campo_variedad.text,
                fecha_siembra=campo_fecha.text,
                superficie=campo_superficie.text,
            )
            toast("Bitacora guardada")
            dialog.dismiss()

        dialog = MDDialog(
            title="Mi bitacora agricola",
            type="custom",
            content_cls=content,
            buttons=[
                MDFlatButton(text="CANCELAR", on_release=lambda x: dialog.dismiss()),
                MDFlatButton(
                    text="GUARDAR", text_color=self.theme_color, on_release=_guardar
                ),
            ],
        )
        dialog.open()

    # ------------------------------------------------------------------
    # Paso 3: ayuda cercana (GPS + enlaces directos a Google Maps,
    # exactamente igual que la version web: sin API de mapas paga)
    # ------------------------------------------------------------------

    def locate_nearby(self):
        main_screen = self.root.ids.sm.get_screen("main")

        try:
            self._show_card(main_screen.ids.locator_card)

            from plyer import gps

            self._locate_disabled_count = 0
            gps.configure(
                on_location=self._on_gps_location, on_status=self._on_locate_status
            )
            gps.start(minTime=1000, minDistance=1)
            # Si en 20 segundos no llega ubicacion (o el sistema avisa que
            # esta apagada), usamos busqueda manual.
            Clock.schedule_once(self._gps_timeout_check, 20)
        except Exception:
            _write_crash_log(
                "Error en locate_nearby (no crashea la app):\n"
                + traceback.format_exc()
            )
            try:
                self._render_manual_search()
            except Exception:
                pass

    @mainthread
    def _on_locate_status(self, stype, status):
        if status == "provider-disabled":
            self._locate_disabled_count = getattr(self, "_locate_disabled_count", 0) + 1
            if self._locate_disabled_count >= 4:
                _write_crash_log(
                    "GPS-LOCATE: 4 providers disabled, pasando a busqueda manual."
                )
                try:
                    from plyer import gps

                    gps.stop()
                except Exception:
                    pass
                main_screen = self.root.ids.sm.get_screen("main")
                if not main_screen.ids.locator_body.children:
                    self._render_manual_search()

    def _gps_timeout_check(self, dt):
        main_screen = self.root.ids.sm.get_screen("main")
        if not main_screen.ids.locator_body.children:
            self._render_manual_search()

    @mainthread
    def _on_gps_location(self, **kwargs):
        lat = kwargs.get("lat")
        lon = kwargs.get("lon")
        try:
            from plyer import gps

            gps.stop()
        except Exception:
            pass
        if lat and lon:
            self._render_nearby_results(lat, lon)
        else:
            self._render_manual_search()

    def _render_nearby_results(self, lat, lon):
        categorias = [
            ("Jardineria y viveros", "vivero jardineria"),
            ("Tiendas agroveterinarias", "tienda agroveterinaria"),
        ]
        main_screen = self.root.ids.sm.get_screen("main")
        box = main_screen.ids.locator_body
        box.clear_widgets()
        for label, query in categorias:
            url = (
                f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
                f"/@{lat},{lon},14z"
            )
            box.add_widget(self._make_place_button(label, url))

    def _render_manual_search(self):
        categorias = [
            ("Jardineria y viveros", "vivero jardineria cerca de mi"),
            ("Tiendas agroveterinarias", "tienda agroveterinaria cerca de mi"),
        ]
        main_screen = self.root.ids.sm.get_screen("main")
        box = main_screen.ids.locator_body
        box.clear_widgets()
        for label, query in categorias:
            url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            box.add_widget(self._make_place_button(label, url))

    def _make_place_button(self, label, url):
        btn = MDRaisedButton(
            text=label,
            icon="map-marker",
            md_bg_color=hex_to_rgba(COLORS["green_50"], 1),
            text_color=hex_to_rgba(COLORS["green_700"]),
            size_hint_x=1,
        )
        btn.bind(on_release=lambda *_: self._open_url(url))
        return btn

    @staticmethod
    def _open_url(url):
        """Abre un enlace externo (Google Maps, etc.).

        En Android usa "Chrome Custom Tabs": es una pestaña que se abre
        DENTRO del flujo de la app, con una flecha "<-" arriba a la
        izquierda para volver directo a Agrowillay con un solo toque.
        Sin esto, el navegador se abre como una app totalmente aparte y
        no hay ningun boton visible para regresar.
        """
        if platform == "android":
            try:
                from jnius import autoclass
                from android import mActivity

                Uri = autoclass("android.net.Uri")
                CustomTabsIntentBuilder = autoclass(
                    "androidx.browser.customtabs.CustomTabsIntent$Builder"
                )
                custom_tabs_intent = CustomTabsIntentBuilder().build()
                custom_tabs_intent.launchUrl(mActivity, Uri.parse(url))
                return
            except Exception:
                pass  # si algo falla, cae al metodo normal de abajo
        webbrowser.open(url)

    # ------------------------------------------------------------------
    # Audio: leer el diagnostico en voz alta (texto a voz nativo)
    # ------------------------------------------------------------------

    def _texto_diagnostico(self, diagnosis):
        return (
            f"Planta identificada: {diagnosis.get('planta_identificada', '')}. "
            f"Problema: {diagnosis.get('plaga_o_problema', '')}. "
            f"Severidad: {diagnosis.get('severidad', '')}. "
            f"Plan de tratamiento: {'. '.join(diagnosis.get('pasos', []))}. "
            f"Prevencion: {diagnosis.get('prevencion', '')}."
        )

    def speak_diagnosis(self, diagnosis):
        """Reproduce el diagnostico en audio (español). Si ya esta
        hablando en español, el mismo boton lo detiene. Si esta hablando
        en quechua, lo interrumpe y arranca en español (antes ambos
        botones compartian una sola bandera "is_speaking", asi que
        tocar cualquiera de los dos encendia el icono de "detener" en
        LOS DOS a la vez, aunque solo uno estuviera sonando)."""
        if self.speaking_lang == "es":
            self.stop_speaking()
            return
        if not diagnosis:
            return
        if self.speaking_lang:
            SpeechManager.stop()  # interrumpe el quechua que estaba sonando
        texto = self._texto_diagnostico(diagnosis)
        self.speaking_lang = "es"
        self._speech_token += 1
        threading.Thread(
            target=self._speak_thread,
            args=(texto, "es", self._speech_token),
            daemon=True,
        ).start()

    def speak_diagnosis_quechua(self, diagnosis):
        """Traduce el resumen del diagnostico al quechua con Gemini y lo
        lee. AVISO HONESTO: casi ningun celular trae una voz en quechua
        instalada, asi que el audio puede sonar con acento incorrecto o
        no reproducirse; el texto traducido igual sirve por si solo."""
        if self.speaking_lang == "qu":
            self.stop_speaking()
            return
        if not diagnosis:
            return
        if self.speaking_lang:
            SpeechManager.stop()  # interrumpe el español que estaba sonando
        toast("Traduciendo al quechua...")
        self._speech_token += 1
        threading.Thread(
            target=self._speak_quechua_thread,
            args=(diagnosis, self._speech_token),
            daemon=True,
        ).start()

    def _speak_quechua_thread(self, diagnosis, token):
        texto_es = self._texto_diagnostico(diagnosis)
        try:
            api_key = ConfigManager.load_api_key() or DEFAULT_GEMINI_API_KEY
            texto_qu = GeminiClient.translate_text(texto_es, "quechua", api_key)
        except Exception:
            _write_crash_log(
                "Error traduciendo a quechua (no crashea la app):\n"
                + traceback.format_exc()
            )
            Clock.schedule_once(
                lambda dt: toast("No se pudo traducir al quechua ahora")
            )
            return

        if token != self._speech_token:
            return  # el usuario ya cancelo o pidio otra lectura mientras se traducia

        Clock.schedule_once(lambda dt: setattr(self, "speaking_lang", "qu"))
        self._speak_thread(texto_qu, "qu", token)

    def _speak_thread(self, texto, locale_code, token):
        ok = SpeechManager.speak(texto, locale_code)
        if not ok:
            Clock.schedule_once(
                lambda dt: toast("No se pudo reproducir el audio")
            )
            if token == self._speech_token:
                Clock.schedule_once(lambda dt: setattr(self, "speaking_lang", ""))
            return

        # SpeechManager.speak() NO espera a que termine de hablar (en
        # Android es asincrono: la voz recien empieza cuando esto ya
        # devolvio resultado). Por eso antes el boton de "detener" se
        # apagaba solo, al toque, aunque la voz seguia sonando, y tocarlo
        # de nuevo no la paraba (solo la reiniciaba desde cero). Aca se
        # espera de verdad a que el motor termine (tts.isSpeaking()),
        # con un limite de seguridad de 60s por si el motor se queda
        # colgado en "hablando" en algun celular.
        time.sleep(0.15)
        espera = 0.0
        while (
            SpeechManager.is_speaking_now()
            and token == self._speech_token
            and espera < 60.0
        ):
            time.sleep(0.2)
            espera += 0.2

        if token == self._speech_token:
            Clock.schedule_once(lambda dt: setattr(self, "speaking_lang", ""))

    def stop_speaking(self):
        SpeechManager.stop()
        self.speaking_lang = ""

    def on_pause(self):
        # El usuario sale de la app (a otra app, al Home, etc.): la voz
        # no debe seguir sonando de fondo.
        self.stop_speaking()
        return True

    def on_stop(self):
        self.stop_speaking()


def _write_crash_log(exc_text):
    """Guarda el error completo en un .txt.

    IMPORTANTE: se escribe primero en el almacenamiento PRIVADO de la app
    (APP_DATA_DIR), porque ese siempre se puede escribir sin pedir ningun
    permiso, ni siquiera en Android 10+ con almacenamiento con alcance.
    La carpeta publica "Download" puede fallar silenciosamente en
    versiones recientes de Android si el permiso de almacenamiento no fue
    concedido, y antes eso dejaba el log sin guardarse y sin avisar nada.
    """
    import datetime

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_text = f"[{stamp}]\n{exc_text}\n"

    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(APP_DATA_DIR / "agrowillay_crash.txt", "a", encoding="utf-8") as f:
            f.write(full_text + ("-" * 60) + "\n")
    except Exception:
        pass

    # Intento extra: tambien copiarlo a Descargas si es posible, para que
    # sea mas facil de encontrar. Si falla, no importa: ya se guardo arriba.
    try:
        if platform == "android":
            from android.storage import primary_external_storage_path

            log_dir = Path(primary_external_storage_path()) / "Download"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "agrowillay_crash.txt", "a", encoding="utf-8") as f:
                f.write(full_text + ("-" * 60) + "\n")
    except Exception:
        pass


class _GlobalExceptionHandler(ExceptionHandler):
    """Atrapa CUALQUIER excepcion no manejada que ocurra dentro del loop
    principal de Kivy (por ejemplo dentro de un callback de Clock que no
    tenia su propio try/except). Sin esto, ese tipo de error tumbaba la
    app entera SIN pasar por ningun 'except' de nuestro codigo, y por eso
    el log nunca se generaba."""

    def handle_exception(self, inst):
        _write_crash_log(
            "Excepcion global no capturada (kivy ExceptionManager):\n"
            + traceback.format_exc()
        )
        try:
            toast("Ocurrio un error, pero la app sigue abierta")
        except Exception:
            pass
        return ExceptionManager.PASS


if __name__ == "__main__":
    ExceptionManager.add_handler(_GlobalExceptionHandler())
    try:
        AgrowillayApp().run()
    except Exception:
        _write_crash_log(traceback.format_exc())
        raise
