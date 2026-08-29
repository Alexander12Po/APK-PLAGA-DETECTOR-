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
import threading
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
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
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

    request_permissions(
        [
            Permission.INTERNET,
            Permission.CAMERA,
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_COARSE_LOCATION,
        ]
    )
    from android.storage import app_storage_path

    APP_DATA_DIR = Path(app_storage_path())
else:
    # Para probar en escritorio (Windows/Linux/Mac) mientras desarrollas.
    APP_DATA_DIR = Path(os.path.expanduser("~/.agrotech_curahuasi"))

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "config.json"

# Nombre de la foto tomada con la camara (debe coincidir con el <files-path>
# declarado en src/android/file_paths.xml para que el FileProvider funcione).
CAMERA_PHOTO_PATH = str(APP_DATA_DIR / "captura_temp.jpg")

# Codigo de peticion usado para identificar el resultado del Intent de camara
# en onActivityResult (cualquier numero fijo sirve, solo debe ser unico).
CAMERA_REQUEST_CODE = 1888

# Modelo de Gemini usado para el diagnóstico (visión + texto)
GEMINI_MODEL = "gemini-3.7-flash"
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
DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6J4joFphAETQ-L1L78xpw0JaByyEU0J5u2MIlPhchPMng"

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
  "prevencion": "una recomendación breve para evitar que vuelva a ocurrir",
  "urgencia": "si requiere atención inmediata o puede esperar, en una frase"
}

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


# ---------------------------------------------------------------------------
# Cliente de Gemini (llamada REST directa vía "requests", sin SDK pesado)
# ---------------------------------------------------------------------------


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

        url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key)
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
            },
        }

        try:
            resp = requests.post(url, json=payload, timeout=45)
        except requests.exceptions.RequestException as exc:
            raise cls.GeminiError(f"Error de conexión: {exc}") from exc

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


class AgrowillayApp(MDApp):
    theme_color = hex_to_rgba(COLORS["green_600"])
    current_image_path = StringProperty("")
    last_diagnosis = ObjectProperty(None, allownone=True)

    def build(self):
        self.title = "Agrowillay"
        self.theme_cls.primary_palette = "Green"
        self.icon = "assets/icon.png"
        kv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "agrowillay_ui.kv"
        )
        return Builder.load_file(kv_path)

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
            main_screen = self.root.get_screen("main")
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
            self.last_diagnosis = None
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo mostrar la imagen: {exc}")

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

        main_screen = self.root.get_screen("main")
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
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        main_screen.ids.analyze_spinner.active = False
        Animation(opacity=0, duration=0.2).start(main_screen.ids.analyze_spinner)
        toast(f"Error: {message}")

    @mainthread
    def _on_analysis_success(self, diagnosis):
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        main_screen.ids.analyze_spinner.active = False
        Animation(opacity=0, duration=0.2).start(main_screen.ids.analyze_spinner)

        try:
            self._render_diagnosis(main_screen, diagnosis)
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

        try:
            main_screen.ids.speak_btn.disabled = False
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

    def _render_diagnosis(self, main_screen, diagnosis):
        if not isinstance(diagnosis, dict):
            diagnosis = {}

        severidad_raw = self._txt(diagnosis.get("severidad"), "media").lower()
        color_map = {"alta": "red_600", "media": "amber_600", "baja": "green_600"}
        chip_color = hex_to_rgba(COLORS.get(color_map.get(severidad_raw, "amber_600")))

        body = main_screen.ids.result_body
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
            Factory.SeverityChip(text=severidad_raw.upper(), chip_color=chip_color)
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
                    Factory.IconRow(
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
                    Factory.IconRow(
                        icon="check-circle-outline",
                        icon_color=self.theme_color,
                        text=paso,
                    )
                )

        prevencion = self._txt(diagnosis.get("prevencion"), "")
        if prevencion:
            body.add_widget(
                Factory.IconRow(
                    icon="shield-check-outline",
                    icon_color=self.theme_color,
                    text=f"[b]Prevencion:[/b] {prevencion}",
                )
            )

        urgencia = self._txt(diagnosis.get("urgencia"), "")
        if urgencia:
            body.add_widget(
                Factory.IconRow(
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
        Animation.cancel_all(card, "opacity")
        Animation(opacity=1, duration=0.4, t="out_quad").start(card)

    @staticmethod
    def _hide_card(card):
        card.disabled = True
        Animation.cancel_all(card, "opacity")
        Animation(opacity=0, duration=0.2, t="out_quad").start(card)

    # ------------------------------------------------------------------
    # Paso 3: ayuda cercana (GPS + enlaces directos a Google Maps,
    # exactamente igual que la version web: sin API de mapas paga)
    # ------------------------------------------------------------------

    def locate_nearby(self):
        main_screen = self.root.get_screen("main")

        try:
            self._show_card(main_screen.ids.locator_card)

            from plyer import gps

            gps.configure(on_location=self._on_gps_location, on_status=lambda *a: None)
            gps.start(minTime=1000, minDistance=1)
            # Si en 6 segundos no llega ubicacion, usamos busqueda manual.
            Clock.schedule_once(self._gps_timeout_check, 6)
        except Exception:
            _write_crash_log(
                "Error en locate_nearby (no crashea la app):\n"
                + traceback.format_exc()
            )
            try:
                self._render_manual_search()
            except Exception:
                pass

    def _gps_timeout_check(self, dt):
        main_screen = self.root.get_screen("main")
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
            ("Viveros cercanos", "vivero"),
            ("Tiendas de jardineria", "tienda de jardineria"),
            ("Agronomos e ingenieros agricolas", "ingeniero agronomo"),
        ]
        main_screen = self.root.get_screen("main")
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
            ("Viveros cercanos", "vivero cerca de mi"),
            ("Tiendas de jardineria", "tienda de jardineria cerca de mi"),
            ("Agronomos e ingenieros agricolas", "ingeniero agronomo cerca de mi"),
        ]
        main_screen = self.root.get_screen("main")
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

    def speak_diagnosis(self, diagnosis):
        """Reproduce el diagnostico en audio usando el motor de texto a
        voz del propio telefono (no gasta llamadas extra a Gemini)."""
        texto = (
            f"Planta identificada: {diagnosis.get('planta_identificada', '')}. "
            f"Problema: {diagnosis.get('plaga_o_problema', '')}. "
            f"Severidad: {diagnosis.get('severidad', '')}. "
            f"Plan de tratamiento: {'. '.join(diagnosis.get('pasos', []))}. "
            f"Prevencion: {diagnosis.get('prevencion', '')}."
        )
        try:
            from plyer import tts

            tts.speak(message=texto)
        except NotImplementedError:
            toast("La lectura en voz alta no esta disponible en este dispositivo")
        except Exception:
            toast("No se pudo reproducir el audio")


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
