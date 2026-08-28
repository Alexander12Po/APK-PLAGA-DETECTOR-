# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil profesional (Kivy + KivyMD)
Diagnóstico de plagas en plantas con IA (Gemini)
Diseño profesional con Material Design 3
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
from kivy.properties import (
    BooleanProperty,
    ColorProperty,
    ObjectProperty,
    StringProperty,
    NumericProperty,
)
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.image import Image
from kivy.uix.behaviors import ButtonBehavior
from kivy.animation import Animation
from kivy.lang import Builder
from kivy.utils import platform

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.button import MDFlatButton, MDRaisedButton, MDFillRoundFlatButton
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField
from kivymd.toast import toast
from kivymd.uix.snackbar import Snackbar

# ---------------------------------------------------------------------------
# Permisos Android
# ---------------------------------------------------------------------------
if platform == "android":
    from android.permissions import Permission, request_permissions
    from android import mActivity
    from jnius import autoclass, cast

    request_permissions(
        [
            Permission.INTERNET,
            Permission.CAMERA,
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_COARSE_LOCATION,
            Permission.READ_EXTERNAL_STORAGE,
            Permission.WRITE_EXTERNAL_STORAGE,
        ]
    )
    from android.storage import app_storage_path

    APP_DATA_DIR = Path(app_storage_path())
else:
    APP_DATA_DIR = Path(os.path.expanduser("~/.agrowillay"))
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = APP_DATA_DIR / "config.json"

# ---------------------------------------------------------------------------
# Configuración Gemini
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# ⚠️ REEMPLAZA con tu nueva API key después de revocar la expuesta
DEFAULT_GEMINI_API_KEY = ""

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

# ---------------------------------------------------------------------------
# Paleta de colores profesional
# ---------------------------------------------------------------------------
COLORS = {
    "primary": "#0F6B4E",
    "primary_light": "#12805E",
    "primary_dark": "#0A4A36",
    "secondary": "#17976F",
    "accent": "#E8F5E9",
    "surface": "#FAFBFA",
    "surface_variant": "#F1F5F3",
    "on_surface": "#1A1A1A",
    "on_surface_variant": "#5B6B62",
    "outline": "#E0E7E4",
    "error": "#D8402E",
    "warning": "#C4801A",
    "success": "#2E7D32",
    "white": "#FFFFFF",
}


def hex_to_rgba(hex_color, alpha=1):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return [r, g, b, alpha]


# ---------------------------------------------------------------------------
# Config Manager
# ---------------------------------------------------------------------------
class ConfigManager:
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
        return DEFAULT_GEMINI_API_KEY

    @staticmethod
    def save_api_key(key: str) -> None:
        CONFIG_FILE.write_text(
            json.dumps({"gemini_api_key": key.strip()}), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Gemini Client
# ---------------------------------------------------------------------------
class GeminiClient:
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
        import requests

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
# KV Design - Material Design 3 Profesional
# ---------------------------------------------------------------------------
KV = """
#:import dp kivy.metrics.dp
#:import Animation kivy.animation.Animation
#:import hex_to_rgba main.hex_to_rgba
#:import COLORS main.COLORS

<StepBadge@MDBoxLayout>:
    number: "1"
    size_hint: None, None
    size: dp(36), dp(36)
    radius: [dp(18),]
    md_bg_color: hex_to_rgba(COLORS['primary'])
    padding: [dp(8),]
    MDLabel:
        text: root.number
        font_style: "BodyMedium"
        bold: True
        color: 1, 1, 1, 1
        halign: "center"
        valign: "middle"

<StepHeader@MDBoxLayout>:
    number: "1"
    title_text: ""
    subtitle_text: ""
    adaptive_height: True
    spacing: dp(12)
    padding: [dp(4),]
    StepBadge:
        number: root.number
    MDBoxLayout:
        orientation: "vertical"
        adaptive_height: True
        spacing: dp(2)
        MDLabel:
            text: root.title_text
            font_style: "TitleMedium"
            bold: True
            adaptive_height: True
            theme_text_color: "Primary"
        MDLabel:
            text: root.subtitle_text
            theme_text_color: "Secondary"
            font_style: "BodySmall"
            adaptive_height: True

<PhotoPlaceholder@MDBoxLayout>:
    orientation: "vertical"
    size_hint: None, None
    size: dp(200), dp(120)
    spacing: dp(12)
    MDIcon:
        icon: "image-plus-outline"
        halign: "center"
        font_size: "48sp"
        theme_text_color: "Hint"
    MDLabel:
        text: "Aún no hay foto seleccionada"
        halign: "center"
        theme_text_color: "Hint"
        font_style: "BodyMedium"
        adaptive_height: True

<MainScreen>:
    name: "main"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: hex_to_rgba(COLORS['surface'])
        
        MDTopAppBar:
            title: "Agrowillay"
            elevation: 2
            md_bg_color: hex_to_rgba(COLORS['primary'])
            specific_text_color: 1, 1, 1, 1
            right_action_items: [["cog-outline", lambda x: app.open_settings()]]
        
        ScrollView:
            do_scroll_x: False
            MDBoxLayout:
                id: content_box
                orientation: "vertical"
                adaptive_height: True
                padding: [dp(16), dp(20), dp(16), dp(32)]
                spacing: dp(20)
                
                # Header
                MDBoxLayout:
                    orientation: "vertical"
                    adaptive_height: True
                    spacing: dp(8)
                    padding: [dp(4), dp(8), dp(4), dp(16)]
                    MDLabel:
                        text: "Identifica plagas en tus plantas al instante"
                        font_style: "HeadlineSmall"
                        bold: True
                        adaptive_height: True
                        halign: "center"
                        theme_text_color: "Primary"
                    MDLabel:
                        text: "Sube una foto o tómala con la cámara y la IA te dará un plan de tratamiento claro."
                        theme_text_color: "Secondary"
                        font_style: "BodyMedium"
                        adaptive_height: True
                        halign: "center"
                
                # PASO 1: FOTO
                MDCard:
                    orientation: "vertical"
                    padding: dp(20)
                    spacing: dp(16)
                    adaptive_height: True
                    radius: [dp(16),]
                    elevation: 1
                    md_bg_color: hex_to_rgba(COLORS['white'])
                    
                    StepHeader:
                        number: "1"
                        title_text: "Fotografía la planta"
                        subtitle_text: "Usa buena luz natural y enfoca la zona afectada."
                    
                    # Preview area
                    MDBoxLayout:
                        size_hint_y: None
                        height: dp(220)
                        radius: [dp(12),]
                        md_bg_color: hex_to_rgba(COLORS['surface_variant'])
                        canvas.before:
                            Color:
                                rgba: hex_to_rgba(COLORS['outline'])
                            Line:
                                rounded_rectangle: (self.x, self.y, self.width, self.height, dp(12))
                                width: dp(1)
                                dash_offset: 4
                                dash_length: 4
                        FloatLayout:
                            Image:
                                id: preview_image
                                size_hint: 1, 1
                                pos_hint: {"x": 0, "y": 0}
                                allow_stretch: True
                                keep_ratio: True
                            PhotoPlaceholder:
                                id: preview_placeholder
                                pos_hint: {"center_x": 0.5, "center_y": 0.5}
                    
                    # Action buttons
                    MDBoxLayout:
                        adaptive_height: True
                        spacing: dp(10)
                        MDRaisedButton:
                            text: "Tomar foto"
                            icon: "camera"
                            md_bg_color: hex_to_rgba(COLORS['primary'])
                            size_hint_x: 0.5
                            height: dp(48)
                            on_release: app.take_photo()
                        MDFillRoundFlatButton:
                            text: "Subir imagen"
                            icon: "image"
                            md_bg_color: hex_to_rgba(COLORS['accent'])
                            text_color: hex_to_rgba(COLORS['primary'])
                            size_hint_x: 0.5
                            height: dp(48)
                            on_release: app.choose_from_gallery()
                    
                    MDRaisedButton:
                        id: analyze_btn
                        text: "Analizar planta"
                        icon: "magnify-scan"
                        md_bg_color: hex_to_rgba(COLORS['primary'])
                        size_hint_x: 1
                        height: dp(52)
                        disabled: True
                        on_release: app.analyze_photo()
                
                # PASO 2: DIAGNÓSTICO
                MDCard:
                    id: result_card
                    orientation: "vertical"
                    padding: dp(20)
                    spacing: dp(16)
                    adaptive_height: True
                    radius: [dp(16),]
                    elevation: 1
                    md_bg_color: hex_to_rgba(COLORS['white'])
                    opacity: 0
                    disabled: True
                    
                    StepHeader:
                        number: "2"
                        title_text: "Diagnóstico"
                        subtitle_text: "Resultado generado por la IA a partir de tu foto."
                    
                    MDLabel:
                        id: result_body
                        text: ""
                        adaptive_height: True
                        markup: True
                        font_style: "BodyMedium"
                    
                    MDFillRoundFlatButton:
                        id: speak_btn
                        text: "Escuchar diagnóstico"
                        icon: "volume-high"
                        md_bg_color: hex_to_rgba(COLORS['accent'])
                        text_color: hex_to_rgba(COLORS['primary'])
                        size_hint_x: 1
                        height: dp(48)
                        disabled: True
                        on_release: app.speak_diagnosis(app.last_diagnosis)
                
                # PASO 3: AYUDA CERCANA
                MDCard:
                    id: locator_card
                    orientation: "vertical"
                    padding: dp(20)
                    spacing: dp(16)
                    adaptive_height: True
                    radius: [dp(16),]
                    elevation: 1
                    md_bg_color: hex_to_rgba(COLORS['white'])
                    opacity: 0
                    disabled: True
                    
                    StepHeader:
                        number: "3"
                        title_text: "Ayuda cerca de ti"
                        subtitle_text: "Viveros, tiendas de jardinería y agrónomos que pueden ayudarte."
                    
                    MDBoxLayout:
                        id: locator_body
                        orientation: "vertical"
                        adaptive_height: True
                        spacing: dp(10)
"""


class MainScreen(Screen):
    pass


class AgrowillayApp(MDApp):
    current_image_path = StringProperty("")
    last_diagnosis = ObjectProperty(None, allownone=True)

    def build(self):
        self.title = "Agrowillay"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.primary_hue = "700"
        self.theme_cls.theme_style = "Light"
        Window.clearcolor = hex_to_rgba(COLORS["surface"])
        return Builder.load_string(KV)

    # ------------------------------------------------------------------
    # Paso 1: Cámara usando Intent nativo de Android (más confiable)
    # ------------------------------------------------------------------
    def take_photo(self):
        if platform == "android":
            try:
                self._take_photo_android()
            except Exception as e:
                toast(f"Error al abrir cámara: {str(e)[:50]}")
        else:
            try:
                from plyer import camera
                photo_path = str(APP_DATA_DIR / "captura_temp.jpg")
                camera.take_picture(
                    filename=photo_path, on_complete=self._on_photo_taken
                )
            except Exception as e:
                toast(f"Cámara no disponible: {str(e)[:50]}")

    def _take_photo_android(self):
        """Usa intent nativo de Android para cámara - más confiable que plyer"""
        try:
            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            FileProvider = autoclass("androidx.core.content.FileProvider")
            File = autoclass("java.io.File")

            photo_path = str(APP_DATA_DIR / "captura_temp.jpg")
            photo_file = File(photo_path)

            # Crear URI con FileProvider
            package_name = mActivity.getPackageName()
            authority = package_name + ".fileprovider"
            photo_uri = FileProvider.getUriForFile(
                mActivity, authority, photo_file
            )

            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(MediaStore.EXTRA_OUTPUT, photo_uri)
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)

            # Usar startActivityForResult
            from android import mActivity as activity

            # Guardar path para usar después
            self._pending_photo_path = photo_path

            # Lanzar intent
            activity.startActivityForResult(intent, 1)

            # Programar verificación (en Android real, usar onActivityResult)
            Clock.schedule_once(
                lambda dt: self._check_photo_taken(photo_path), 2
            )

        except Exception as e:
            # Fallback: intentar con plyer
            try:
                from plyer import camera
                photo_path = str(APP_DATA_DIR / "captura_temp.jpg")
                camera.take_picture(
                    filename=photo_path, on_complete=self._on_photo_taken
                )
            except Exception:
                raise e

    def _check_photo_taken(self, photo_path):
        """Verifica si la foto fue tomada"""
        if os.path.exists(photo_path):
            self._set_preview_image(photo_path)
        else:
            toast("No se pudo obtener la foto")

    @mainthread
    def _on_photo_taken(self, path):
        try:
            if path and os.path.exists(path):
                self._set_preview_image(path)
            else:
                toast("No se pudo obtener la foto")
        except Exception as exc:
            toast(f"Error al procesar la foto: {str(exc)[:50]}")

    def choose_from_gallery(self):
        try:
            from plyer import filechooser
            filechooser.open_file(
                on_selection=self._on_file_chosen,
                filters=[("Imágenes", "*.jpg", "*.jpeg", "*.png", "*.webp")],
            )
        except Exception as exc:
            toast(f"No se pudo abrir la galería: {str(exc)[:50]}")

    def _on_file_chosen(self, selection):
        try:
            if selection:
                Clock.schedule_once(
                    lambda dt: self._set_preview_image(selection[0])
                )
        except Exception as exc:
            toast(f"Error al procesar la imagen: {str(exc)[:50]}")

    def _set_preview_image(self, path):
        self.current_image_path = path
        main_screen = self.root.get_screen("main")
        main_screen.ids.preview_image.source = path
        main_screen.ids.preview_image.reload()
        main_screen.ids.preview_placeholder.opacity = 0
        main_screen.ids.analyze_btn.disabled = False
        self._hide_card(main_screen.ids.result_card)
        self._hide_card(main_screen.ids.locator_card)
        main_screen.ids.speak_btn.disabled = True
        self.last_diagnosis = None

    # ------------------------------------------------------------------
    # Paso 2: Análisis con Gemini
    # ------------------------------------------------------------------
    def analyze_photo(self):
        if not self.current_image_path:
            toast("Primero selecciona o toma una foto")
            return
        api_key = ConfigManager.load_api_key()
        if not api_key:
            self._show_api_key_dialog()
            return

        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = True
        main_screen.ids.analyze_btn.text = "Analizando..."

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
        except Exception as exc:
            Clock.schedule_once(lambda dt: self._on_analysis_error(str(exc)))
            return
        Clock.schedule_once(lambda dt: self._on_analysis_success(diagnosis))

    @mainthread
    def _on_analysis_error(self, message):
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"
        Snackbar(text=f"Error: {message[:80]}").open()

    @mainthread
    def _on_analysis_success(self, diagnosis):
        main_screen = self.root.get_screen("main")
        main_screen.ids.analyze_btn.disabled = False
        main_screen.ids.analyze_btn.text = "Analizar planta"

        severidad = diagnosis.get("severidad", "media")
        color_map = {
            "alta": "error",
            "media": "warning",
            "baja": "success",
        }
        color_hex = COLORS.get(color_map.get(severidad, "warning"))

        pasos = diagnosis.get("pasos", [])
        pasos_txt = "\n".join(f"  • {p}" for p in pasos)
        sintomas = diagnosis.get("sintomas_observados", [])
        sintomas_txt = ", ".join(sintomas) if sintomas else "-"

        texto = (
            f"[b][color={color_hex}]Planta:[/color][/b] {diagnosis.get('planta_identificada', '-')}\\n"
            f"[b][color={color_hex}]Problema:[/color][/b] {diagnosis.get('plaga_o_problema', '-')}\\n"
            f"[b][color={color_hex}]Severidad:[/color][/b] [color={color_hex}]{severidad.upper()}[/color]\\n"
            f"[b][color={color_hex}]Síntomas:[/color][/b] {sintomas_txt}\\n\\n"
            f"[b][color={COLORS['primary']}]Plan de tratamiento:[/color][/b]\\n{pasos_txt}\\n\\n"
            f"[b][color={COLORS['primary']}]Prevención:[/color][/b] {diagnosis.get('prevencion', '-')}\\n"
            f"[b][color={COLORS['primary']}]Urgencia:[/color][/b] {diagnosis.get('urgencia', '-')}"
        )

        main_screen.ids.result_body.text = texto
        self.last_diagnosis = diagnosis
        main_screen.ids.speak_btn.disabled = False
        self._show_card(main_screen.ids.result_card)
        self.locate_nearby()

    # ------------------------------------------------------------------
    # Paso 3: Ayuda cercana
    # ------------------------------------------------------------------
    def locate_nearby(self):
        main_screen = self.root.get_screen("main")
        self._show_card(main_screen.ids.locator_card)
        try:
            from plyer import gps
            gps.configure(
                on_location=self._on_gps_location,
                on_status=lambda *a: None,
            )
            gps.start(minTime=1000, minDistance=1)
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
            ("Tiendas de jardinería", "tienda de jardinería"),
            ("Agrónomos e ingenieros agrícolas", "ingeniero agrónomo"),
        ]
        main_screen = self.root.get_screen("main")
        box = main_screen.ids.locator_body
        box.clear_widgets()
        for label, query in categorias:
            url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}/@{lat},{lon},14z"
            box.add_widget(self._make_place_button(label, url))

    def _render_manual_search(self):
        categorias = [
            ("Viveros cercanos", "vivero cerca de mí"),
            ("Tiendas de jardinería", "tienda de jardinería cerca de mí"),
            ("Agrónomos e ingenieros agrícolas", "ingeniero agrónomo cerca de mí"),
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
            md_bg_color=hex_to_rgba(COLORS["accent"]),
            text_color=hex_to_rgba(COLORS["primary"]),
            size_hint_x=1,
            height=dp(48),
        )
        btn.bind(on_release=lambda *_: self._open_url(url))
        return btn

    @staticmethod
    def _open_url(url):
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
                pass
        webbrowser.open(url)

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------
    def speak_diagnosis(self, diagnosis):
        texto = (
            f"Planta identificada: {diagnosis.get('planta_identificada', '')}. "
            f"Problema: {diagnosis.get('plaga_o_problema', '')}. "
            f"Severidad: {diagnosis.get('severidad', '')}. "
            f"Plan de tratamiento: {'. '.join(diagnosis.get('pasos', []))}. "
            f"Prevención: {diagnosis.get('prevencion', '')}."
        )
        try:
            from plyer import tts
            tts.speak(message=texto)
        except NotImplementedError:
            toast("La lectura en voz alta no está disponible")
        except Exception:
            toast("No se pudo reproducir el audio")

    # ------------------------------------------------------------------
    # Utilidades UI
    # ------------------------------------------------------------------
    def _show_card(self, card):
        card.opacity = 0
        card.disabled = False
        anim = Animation(opacity=1, duration=0.4)
        anim.start(card)

    def _hide_card(self, card):
        card.opacity = 0
        card.disabled = True

    def _show_api_key_dialog(self):
        content = MDBoxLayout(
            orientation="vertical",
            spacing=dp(12),
            adaptive_height=True,
        )
        self._api_key_field = MDTextField(
            hint_text="Ingresa tu API Key de Gemini",
            mode="rectangle",
            size_hint_x=1,
        )
        content.add_widget(MDLabel(text="Configuración de API Key"))
        content.add_widget(self._api_key_field)

        dialog = MDDialog(
            title="API Key requerida",
            type="custom",
            content_cls=content,
            buttons=[
                MDFlatButton(
                    text="CANCELAR",
                    on_release=lambda x: dialog.dismiss(),
                ),
                MDRaisedButton(
                    text="GUARDAR",
                    on_release=lambda x: self._save_api_key_from_dialog(),
                ),
            ],
        )
        dialog.open()

    def _save_api_key_from_dialog(self):
        key = self._api_key_field.text.strip()
        if key:
            ConfigManager.save_api_key(key)
            toast("API Key guardada correctamente")
        try:
            self.root.get_screen("main").ids.analyze_btn.disabled = False
        except:
            pass

    def open_settings(self):
        self._show_api_key_dialog()


if __name__ == "__main__":
    AgrowillayApp().run()