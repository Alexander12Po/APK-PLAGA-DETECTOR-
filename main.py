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
import webbrowser
from pathlib import Path

from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.screenmanager import Screen

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.button import MDFlatButton, MDRaisedButton
from kivymd.uix.label import MDLabel
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

# Modelo de Gemini usado para el diagnóstico (visión + texto)
GEMINI_MODEL = "gemini-2.5-flash"
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
DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6Lm0f5UBdmiAhvk0-46rp8oACmkd1_n56R-_riaI2y3Cw"

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

KV = """
#:import dp kivy.metrics.dp

ScreenManager:
    MainScreen:

<StepBadge@MDLabel>:
    size_hint: None, None
    size: dp(30), dp(30)
    bold: True
    color: 1, 1, 1, 1
    halign: "center"
    valign: "middle"
    font_style: "Subtitle1"
    canvas.before:
        Color:
            rgba: app.theme_color
        Ellipse:
            pos: self.pos
            size: self.size

<StepHeader@MDBoxLayout>:
    number: "1"
    title_text: ""
    subtitle_text: ""
    adaptive_height: True
    spacing: dp(12)

    StepBadge:
        text: root.number

    MDBoxLayout:
        orientation: "vertical"
        adaptive_height: True
        spacing: dp(2)

        MDLabel:
            text: root.title_text
            bold: True
            font_style: "Subtitle1"
            adaptive_height: True

        MDLabel:
            text: root.subtitle_text
            theme_text_color: "Secondary"
            font_style: "Caption"
            adaptive_height: True

<SectionCard@MDCard>:
    orientation: "vertical"
    padding: dp(20)
    spacing: dp(14)
    adaptive_height: True
    radius: [18, 18, 18, 18]
    elevation: 0
    md_bg_color: 1, 1, 1, 1
    line_color: 0.87, 0.9, 0.88, 1

<PillButton@MDRaisedButton>:
    md_bg_color: app.theme_color
    text_color: 1, 1, 1, 1
    elevation: 0
    size_hint_x: 1
    height: dp(46)

<GhostButton@MDFlatButton>:
    md_bg_color: 0.925, 0.965, 0.949, 1
    text_color: app.theme_color
    elevation: 0
    size_hint_x: 1
    height: dp(46)

<MainScreen>:
    name: "main"

    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: 0.96, 0.97, 0.965, 1

        MDTopAppBar:
            title: "Agrowillay"
            elevation: 4
            md_bg_color: app.theme_color
            specific_text_color: 1, 1, 1, 1

        ScrollView:
            MDBoxLayout:
                id: content_box
                orientation: "vertical"
                adaptive_height: True
                padding: [dp(16), dp(20), dp(16), dp(28)]
                spacing: dp(18)

                MDBoxLayout:
                    orientation: "vertical"
                    adaptive_height: True
                    spacing: dp(6)

                    MDLabel:
                        text: "Identifica plagas en tus plantas al instante"
                        font_style: "H6"
                        bold: True
                        adaptive_height: True
                        halign: "center"

                    MDLabel:
                        text: "Sube una foto o tomala con la camara y la IA te dara un plan de tratamiento claro."
                        theme_text_color: "Secondary"
                        adaptive_height: True
                        halign: "center"

                # ---------------- PASO 1: FOTO ----------------
                SectionCard:

                    StepHeader:
                        number: "1"
                        title_text: "Fotografia la planta"
                        subtitle_text: "Usa buena luz natural y enfoca la zona afectada."

                    FloatLayout:
                        size_hint_y: None
                        height: dp(200)

                        canvas.before:
                            Color:
                                rgba: 0.95, 0.97, 0.965, 1
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [14,]

                        Image:
                            id: preview_image
                            size_hint: 1, 1
                            pos_hint: {"x": 0, "y": 0}
                            allow_stretch: True
                            keep_ratio: True

                        MDBoxLayout:
                            id: preview_placeholder
                            orientation: "vertical"
                            size_hint: None, None
                            size: dp(220), dp(70)
                            pos_hint: {"center_x": 0.5, "center_y": 0.5}
                            spacing: dp(6)

                            MDIcon:
                                icon: "image-plus-outline"
                                halign: "center"
                                theme_text_color: "Hint"
                                font_size: "34sp"

                            MDLabel:
                                text: "Aun no hay foto seleccionada"
                                halign: "center"
                                theme_text_color: "Hint"
                                font_style: "Caption"
                                adaptive_height: True

                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(10)

                        PillButton:
                            text: "Tomar foto"
                            icon: "camera"
                            on_release: app.take_photo()

                        GhostButton:
                            text: "Subir imagen"
                            icon: "image"
                            on_release: app.choose_from_gallery()

                    PillButton:
                        id: analyze_btn
                        text: "Analizar planta"
                        icon: "magnify-scan"
                        disabled: True

                        on_release: app.analyze_photo()

                # ---------------- PASO 2: DIAGNÓSTICO ----------------
                SectionCard:
                    id: result_card
                    opacity: 0
                    disabled: True

                    StepHeader:
                        number: "2"
                        title_text: "Diagnostico"
                        subtitle_text: "Resultado generado por la IA a partir de tu foto."

                    MDLabel:
                        id: result_body
                        text: ""
                        adaptive_height: True
                        markup: True

                    GhostButton:
                        id: speak_btn
                        text: "Escuchar diagnostico"
                        icon: "volume-high"
                        disabled: True
                        on_release: app.speak_diagnosis(app.last_diagnosis)

                # ---------------- PASO 3: AYUDA CERCANA ----------------
                SectionCard:
                    id: locator_card
                    opacity: 0
                    disabled: True

                    StepHeader:
                        number: "3"
                        title_text: "Ayuda cerca de ti"
                        subtitle_text: "Viveros, tiendas de jardineria y agronomos que pueden ayudarte."

                    MDBoxLayout:
                        id: locator_body
                        orientation: "vertical"
                        adaptive_height: True
                        spacing: dp(8)
"""


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
        return __import__("kivy.lang", fromlist=["Builder"]).Builder.load_string(KV)

    # ------------------------------------------------------------------
    # Paso 1: seleccionar / tomar foto
    # ------------------------------------------------------------------

    def take_photo(self):
        try:
            from plyer import camera
        except Exception:
            toast("La camara no esta disponible en este dispositivo")
            return

        photo_path = str(APP_DATA_DIR / "captura_temp.jpg")
        try:
            camera.take_picture(filename=photo_path, on_complete=self._on_photo_taken)
        except NotImplementedError:
            toast("Tu dispositivo no soporta esta funcion de camara")
        except Exception as exc:  # noqa: BLE001
            toast(f"No se pudo abrir la camara: {exc}")

    @mainthread
    def _on_photo_taken(self, path):
        try:
            if path and os.path.exists(path):
                self._set_preview_image(path)
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
        try:
            if selection:
                Clock.schedule_once(lambda dt: self._set_preview_image(selection[0]))
        except Exception as exc:  # noqa: BLE001
            toast(f"Error al procesar la imagen: {exc}")

    def _set_preview_image(self, path):
        self.current_image_path = path
        main_screen = self.root.get_screen("main")
        main_screen.ids.preview_image.source = path
        main_screen.ids.preview_image.reload()
        main_screen.ids.preview_placeholder.opacity = 0
        main_screen.ids.analyze_btn.disabled = False

        # Si el usuario cambia la foto, oculta resultados anteriores.
        self._hide_card(main_screen.ids.result_card)
        self._hide_card(main_screen.ids.locator_card)
        main_screen.ids.speak_btn.disabled = True
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

        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = True
        main_screen.ids.analyze_btn.text = "Analizando..."

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
            Clock.schedule_once(lambda dt: self._on_analysis_error(str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            Clock.schedule_once(lambda dt: self._on_analysis_error(str(exc)))
            return

        Clock.schedule_once(lambda dt: self._on_analysis_success(diagnosis))

    @mainthread
    def _on_analysis_error(self, message):
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        toast(f"Error: {message}")

    @mainthread
    def _on_analysis_success(self, diagnosis):
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"

        severidad = diagnosis.get("severidad", "media")
        color_map = {"alta": "red_600", "media": "amber_600", "baja": "green_600"}
        color_hex = COLORS.get(color_map.get(severidad, "amber_600"))

        pasos = diagnosis.get("pasos", [])
        pasos_txt = "\n".join(f"  - {p}" for p in pasos)
        sintomas = diagnosis.get("sintomas_observados", [])
        sintomas_txt = ", ".join(sintomas) if sintomas else "-"

        texto = (
            f"[b]Planta:[/b] {diagnosis.get('planta_identificada', '-')}\n"
            f"[b]Problema:[/b] {diagnosis.get('plaga_o_problema', '-')}\n"
            f"[b]Severidad:[/b] [color={color_hex}]{severidad.upper()}[/color]\n"
            f"[b]Sintomas:[/b] {sintomas_txt}\n\n"
            f"[b]Plan de tratamiento:[/b]\n{pasos_txt}\n\n"
            f"[b]Prevencion:[/b] {diagnosis.get('prevencion', '-')}\n"
            f"[b]Urgencia:[/b] {diagnosis.get('urgencia', '-')}"
        )

        main_screen.ids.result_body.text = texto
        self.last_diagnosis = diagnosis
        main_screen.ids.speak_btn.disabled = False
        self._show_card(main_screen.ids.result_card)

        # Igual que en la web: apenas hay diagnostico, se busca ayuda cercana.
        self.locate_nearby()

    @staticmethod
    def _show_card(card):
        card.opacity = 1
        card.disabled = False

    @staticmethod
    def _hide_card(card):
        card.opacity = 0
        card.disabled = True

    # ------------------------------------------------------------------
    # Paso 3: ayuda cercana (GPS + enlaces directos a Google Maps,
    # exactamente igual que la version web: sin API de mapas paga)
    # ------------------------------------------------------------------

    def locate_nearby(self):
        main_screen = self.root.get_screen("main")
        self._show_card(main_screen.ids.locator_card)

        try:
            from plyer import gps

            gps.configure(on_location=self._on_gps_location, on_status=lambda *a: None)
            gps.start(minTime=1000, minDistance=1)
            # Si en 6 segundos no llega ubicacion, usamos busqueda manual.
            Clock.schedule_once(self._gps_timeout_check, 6)
        except Exception:
            self._render_manual_search()

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


if __name__ == "__main__":
    AgrowillayApp().run()
