# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil (Kivy + KivyMD)
Diagnóstico de plagas con IA, Clima y Directorio de Agroveterinarias
Con Panel ADMIN protegido por PIN (673847).
"""

import base64
import json
import os
import shutil
import threading
import time
import traceback
import wave
import webbrowser
from pathlib import Path

from kivy.animation import Animation
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock, mainthread
from kivy.factory import Factory
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import ListProperty, ObjectProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.screenmanager import Screen
from kivy.utils import platform

from kivymd.app import MDApp
from kivymd.toast import toast
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDRaisedButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField

# ---------------------------------------------------------------------------
# Permisos y Rutas Android
# ---------------------------------------------------------------------------

if platform == "android":
    from android.permissions import Permission, request_permissions

    _permisos = [
        Permission.INTERNET,
        Permission.CAMERA,
        Permission.ACCESS_FINE_LOCATION,
        Permission.ACCESS_COARSE_LOCATION,
    ]
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
    APP_DATA_DIR = Path(os.path.expanduser("~/.agrowillay_app"))

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "config.json"
BITACORA_FILE = APP_DATA_DIR / "bitacora.json"
HISTORY_FILE = APP_DATA_DIR / "historial.json"
AGROVETS_FILE = APP_DATA_DIR / "agroveterinarias.json"
HISTORY_PHOTOS_DIR = APP_DATA_DIR / "historial_fotos"
CAMERA_PHOTO_PATH = str(APP_DATA_DIR / "captura_temp.jpg")
CAMERA_REQUEST_CODE = 1888

# Código de acceso ADMIN solicitado
ADMIN_PIN_CODE = "673847"

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_FALLBACK = "gemini-1.5-flash"
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6JUSmY_mCwVu6L7n2oa05nwvxsf8NbHKdFWd_Tkbo-n0Q"
APP_VERSION = "1.1.0"
GITHUB_REPO = "Alexander12Po/APK-PLAGA-DETECTOR-"

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
  "productos_recomendados": ["producto comercial (ej. fungicida a base de cobre)", "insecticida específico"],
  "remedios_caseros": ["remedio orgánico o casero 1", "remedio 2"],
  "prevencion": "una recomendación breve para evitar que vuelva a ocurrir",
  "urgencia": "urgencia en una frase"
}
Escribe todos los textos en español."""

COLORS = {
    "green_700": "#0A5038",
    "green_600": "#10805B",
    "green_500": "#19B382",
    "green_50": "#E9F8F2",
    "red_600": "#E03838",
    "amber_600": "#D98218",
}


def hex_to_rgba(hex_color, alpha=1):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return [r, g, b, alpha]


# ---------------------------------------------------------------------------
# Gestor de Agroveterinarias (Persistencia JSON + Precarga)
# ---------------------------------------------------------------------------


class AgroveterinariaManager:
    """Gestiona el registro local de agroveterinarias."""

    DEFAULT_AGROVETS = [
        {
            "id": "1",
            "nombre": "Agroveterinaria El Campo",
            "ciudad": "Curahuasi / Centro",
            "telefono": "984123456",
            "whatsapp": "51984123456",
            "notas": "Fungicidas, insecticidas, fertilizantes foliares y semillas.",
        },
        {
            "id": "2",
            "nombre": "Agroinsumos Apurímac",
            "ciudad": "Abancay - Av. Arenas",
            "telefono": "983654321",
            "whatsapp": "51983654321",
            "notas": "Control fitosanitario, bombas de mochila y abono orgánico.",
        },
        {
            "id": "3",
            "nombre": "Veterinaria y Agronomía San Isidro",
            "ciudad": "Curahuasi",
            "telefono": "972112233",
            "whatsapp": "51972112233",
            "notas": "Asesoría técnica agrícola y salud animal.",
        },
    ]

    @classmethod
    def load(cls) -> list:
        if AGROVETS_FILE.exists():
            try:
                data = json.loads(AGROVETS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception:
                pass
        cls.save_all(cls.DEFAULT_AGROVETS)
        return cls.DEFAULT_AGROVETS

    @classmethod
    def save_all(cls, lista: list):
        try:
            AGROVETS_FILE.write_text(
                json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            _write_crash_log(traceback.format_exc())

    @classmethod
    def add(cls, nombre, ciudad, telefono, whatsapp, notas=""):
        lista = cls.load()
        nuevo = {
            "id": str(int(time.time() * 1000)),
            "nombre": nombre.strip(),
            "ciudad": ciudad.strip(),
            "telefono": telefono.strip(),
            "whatsapp": whatsapp.strip().replace("+", "").replace(" ", ""),
            "notas": notas.strip(),
        }
        lista.insert(0, nuevo)
        cls.save_all(lista)
        return nuevo

    @classmethod
    def delete(cls, item_id: str):
        lista = [x for x in cls.load() if str(x.get("id")) != str(item_id)]
        cls.save_all(lista)


# ---------------------------------------------------------------------------
# Otros Gestores
# ---------------------------------------------------------------------------


class ConfigManager:
    @staticmethod
    def load_api_key() -> str:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                saved = data.get("gemini_api_key", "")
                if saved:
                    return saved
            except Exception:
                pass
        return DEFAULT_GEMINI_API_KEY


class BitacoraManager:
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
            HISTORY_FILE.write_text(
                json.dumps(entradas[: cls.MAX_ENTRADAS]), encoding="utf-8"
            )
        except Exception:
            _write_crash_log(traceback.format_exc())


# ---------------------------------------------------------------------------
# Clima con Open-Meteo
# ---------------------------------------------------------------------------


class WeatherClient:
    ENDPOINT = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        "&daily=temperature_2m_min,temperature_2m_max,precipitation_probability_max,wind_speed_10m_max,relative_humidity_2m_mean"
        "&timezone=auto&forecast_days=3"
    )

    @classmethod
    def get_forecast(cls, lat, lon):
        import requests

        url = cls.ENDPOINT.format(lat=lat, lon=lon)
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        fechas = daily.get("time", [])

        dias = []
        for i in range(len(fechas)):
            dias.append(
                {
                    "fecha": fechas[i],
                    "temp_min": (daily.get("temperature_2m_min") or [None])[i],
                    "temp_max": (daily.get("temperature_2m_max") or [None])[i],
                    "prob_lluvia": (
                        daily.get("precipitation_probability_max") or [0]
                    )[i],
                    "viento_max": (daily.get("wind_speed_10m_max") or [0])[i],
                    "humedad": (daily.get("relative_humidity_2m_mean") or [0])[i],
                }
            )
        return {"current": current, "dias": dias}


def evaluar_riesgo_climatico(dias):
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
                    "tipo": "Alerta de Helada",
                    "nivel": "alta" if temp_min <= 0 else "media",
                    "detalle": f"Temperatura mínima esperada de {temp_min}°C.",
                }
            )
        if prob_lluvia >= 70 and viento >= 30:
            riesgos.append(
                {
                    "fecha": dia["fecha"],
                    "tipo": "Lluvias y Vientos Fuertes",
                    "nivel": "media",
                    "detalle": f"{prob_lluvia}% lluvia con ráfagas de {viento} km/h.",
                }
            )
        if humedad >= 80 and temp_min is not None and temp_min >= 10:
            riesgos.append(
                {
                    "fecha": dia["fecha"],
                    "tipo": "Alto Riesgo de Hongos / Plagas",
                    "nivel": "media",
                    "detalle": f"Humedad de {humedad}%. Monitorea roya, rancha o mildiu.",
                }
            )
    return riesgos


# ---------------------------------------------------------------------------
# Cliente Gemini IA
# ---------------------------------------------------------------------------


class GeminiClient:
    class GeminiError(Exception):
        pass

    @staticmethod
    def _extract_json(text: str) -> dict:
        cleaned = text.strip().replace("```json", "").replace("```", "").strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise GeminiClient.GeminiError("Respuesta de IA sin JSON válido.")
        return json.loads(cleaned[start : end + 1])

    @classmethod
    def analyze_image(cls, image_path: str, api_key: str) -> dict:
        import requests

        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        mime = "image/jpeg"
        if image_path.lower().endswith(".png"):
            mime = "image/png"
        elif image_path.lower().endswith(".webp"):
            mime = "image/webp"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": DIAGNOSIS_PROMPT},
                        {"inline_data": {"mime_type": mime, "data": image_b64}},
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "max_output_tokens": 2048,
            },
        }

        urls = [
            GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key),
            GEMINI_ENDPOINT.format(model=GEMINI_MODEL_FALLBACK, key=api_key),
        ]

        resp = None
        for url in urls:
            try:
                resp = requests.post(url, json=payload, timeout=50)
                if resp.status_code == 200:
                    break
            except Exception:
                continue

        if not resp or resp.status_code != 200:
            raise cls.GeminiError(
                f"Error al conectar con la IA ({getattr(resp, 'status_code', 'Red')}). Revisa internet."
            )

        data = resp.json()
        try:
            txt = data["candidates"][0]["content"]["parts"][0]["text"]
            return cls._extract_json(txt)
        except Exception as exc:
            raise cls.GeminiError("Formato de respuesta no procesable.") from exc


# ---------------------------------------------------------------------------
# Clases de Pantallas y Widgets
# ---------------------------------------------------------------------------


class MainScreen(Screen):
    pass


class HomeScreen(Screen):
    pass


class HistoryScreen(Screen):
    pass


class AgroveterinariasScreen(Screen):
    pass


class AdminScreen(Screen):
    pass


class TipsScreen(Screen):
    pass


class MoreScreen(Screen):
    pass


class RoundIconButton(ButtonBehavior, FloatLayout):
    icon = StringProperty("volume-high")
    bg_color = ListProperty([0.10, 0.68, 0.45, 1])


# ---------------------------------------------------------------------------
# Aplicación Principal Agrowillay
# ---------------------------------------------------------------------------


class AgrowillayApp(MDApp):
    theme_color = hex_to_rgba(COLORS["green_600"])
    current_image_path = StringProperty("")
    speaking_lang = StringProperty("")
    last_diagnosis = ObjectProperty(None, allownone=True)
    current_tab = StringProperty("home")
    _admin_dialog = None

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
        # Cargar clima y agroveterinarias en segundo plano
        Clock.schedule_once(lambda dt: self.refresh_agrovets_ui(), 0.5)
        Clock.schedule_once(lambda dt: self.check_weather_alerts(), 1.0)

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    def _go(self, screen_name, tab_name):
        try:
            self.root.ids.sm.current = screen_name
            self.current_tab = tab_name
        except Exception:
            _write_crash_log(traceback.format_exc())

    def go_home(self):
        self._go("home", "home")

    def go_diagnosis(self):
        self._go("main", "diagnosis")

    def go_history(self):
        self._go("history", "history")
        self._refresh_history()

    def go_agrovets(self):
        self._go("agrovets", "agrovets")
        self.refresh_agrovets_ui()

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
        self.locate_nearby()

    # ------------------------------------------------------------------
    # Módulo ADMIN (Código de Acceso 673847)
    # ------------------------------------------------------------------

    def prompt_admin_code(self):
        """Abre un diálogo solicitando el código de acceso 673847."""
        pin_field = MDTextField(
            hint_text="Ingresa el código PIN",
            password=True,
            mode="fill",
            max_text_length=6,
        )

        def _verificar(*_):
            code = pin_field.text.strip()
            if code == ADMIN_PIN_CODE:
                self._admin_dialog.dismiss()
                toast("Acceso ADMIN concedido")
                self._go("admin", "admin")
                self.refresh_admin_agrovets_ui()
            else:
                toast("Código incorrecto. Acceso denegado.")

        self._admin_dialog = MDDialog(
            title="Acceso ADMIN---",
            text="Introduce el código de autorización para administrar agroveterinarias:",
            type="custom",
            content_cls=pin_field,
            buttons=[
                MDFlatButton(
                    text="CANCELAR",
                    on_release=lambda x: self._admin_dialog.dismiss(),
                ),
                MDRaisedButton(
                    text="ENTRAR",
                    md_bg_color=(0.85, 0.25, 0.35, 1),
                    on_release=_verificar,
                ),
            ],
        )
        self._admin_dialog.open()

    def admin_save_agroveterinaria(self):
        """Guarda una nueva agroveterinaria desde el panel ADMIN."""
        admin_screen = self.root.ids.sm.get_screen("admin")
        nom = admin_screen.ids.admin_input_nombre.text.strip()
        ciu = admin_screen.ids.admin_input_ciudad.text.strip()
        tel = admin_screen.ids.admin_input_telefono.text.strip()
        wsp = admin_screen.ids.admin_input_whatsapp.text.strip()
        not_ = admin_screen.ids.admin_input_notas.text.strip()

        if not nom or not ciu or not tel or not wsp:
            toast("Por favor completa los campos obligatorios (*)")
            return

        AgroveterinariaManager.add(nom, ciu, tel, wsp, not_)
        toast("¡Agroveterinaria registrada con éxito!")

        # Limpiar campos
        admin_screen.ids.admin_input_nombre.text = ""
        admin_screen.ids.admin_input_ciudad.text = ""
        admin_screen.ids.admin_input_telefono.text = ""
        admin_screen.ids.admin_input_whatsapp.text = ""
        admin_screen.ids.admin_input_notas.text = ""

        self.refresh_admin_agrovets_ui()
        self.refresh_agrovets_ui()

    def refresh_admin_agrovets_ui(self):
        """Muestra las agroveterinarias en la pantalla de administración con botón de eliminar."""
        try:
            admin_screen = self.root.ids.sm.get_screen("admin")
            box = admin_screen.ids.admin_agrovets_list
            box.clear_widgets()
            lista = AgroveterinariaManager.load()

            for item in lista:
                card = MDCard(
                    orientation="vertical",
                    padding=dp(14),
                    spacing=dp(8),
                    size_hint_y=None,
                    height=dp(120),
                    md_bg_color=(0.08, 0.10, 0.14, 1),
                    radius=[14],
                )
                card.add_widget(
                    MDLabel(
                        text=f"[b]{item['nombre']}[/b]",
                        markup=True,
                        theme_text_color="Custom",
                        text_color=(1, 1, 1, 1),
                    )
                )
                card.add_widget(
                    MDLabel(
                        text=f"{item['ciudad']} • Tel: {item['telefono']}",
                        theme_text_color="Custom",
                        text_color=(0.7, 0.75, 0.8, 1),
                        font_style="Caption",
                    )
                )

                del_btn = MDRaisedButton(
                    text="Eliminar",
                    icon="trash-can-outline",
                    size_hint_x=None,
                    width=dp(100),
                    height=dp(34),
                    md_bg_color=(0.85, 0.25, 0.35, 1),
                    on_release=lambda *_, i=item["id"]: self._delete_agroveterinaria(
                        i
                    ),
                )
                card.add_widget(del_btn)
                box.add_widget(card)
        except Exception:
            _write_crash_log(traceback.format_exc())

    def _delete_agroveterinaria(self, item_id):
        AgroveterinariaManager.delete(item_id)
        toast("Agroveterinaria eliminada")
        self.refresh_admin_agrovets_ui()
        self.refresh_agrovets_ui()

    # ------------------------------------------------------------------
    # Directorio Público de Agroveterinarias (Llamadas & WhatsApp)
    # ------------------------------------------------------------------

    def refresh_agrovets_ui(self):
        """Renderiza las agroveterinarias en la pestaña pública con botones 3D de contacto."""
        try:
            screen = self.root.ids.sm.get_screen("agrovets")
            box = screen.ids.agrovets_list
            box.clear_widgets()
            lista = AgroveterinariaManager.load()

            for item in lista:
                card = MDCard(
                    orientation="vertical",
                    padding=dp(16),
                    spacing=dp(10),
                    size_hint_y=None,
                    adaptive_height=True,
                    md_bg_color=(0.08, 0.10, 0.15, 1),
                    radius=[18],
                )

                # Título y Ubicación
                header = MDBoxLayout(adaptive_height=True, spacing=dp(8))
                header.add_widget(
                    MDLabel(
                        text=f"[b]{item['nombre']}[/b]\n[color=64B5F6]{item['ciudad']}[/color]",
                        markup=True,
                        theme_text_color="Custom",
                        text_color=(1, 1, 1, 1),
                        adaptive_height=True,
                    )
                )
                card.add_widget(header)

                if item.get("notas"):
                    card.add_widget(
                        MDLabel(
                            text=item["notas"],
                            theme_text_color="Custom",
                            text_color=(0.75, 0.80, 0.85, 1),
                            font_style="Caption",
                            adaptive_height=True,
                        )
                    )

                # Botones de Acción: WhatsApp y Llamada
                acciones = MDBoxLayout(adaptive_height=True, spacing=dp(10))

                # Botón WhatsApp Directo
                wsp_num = item.get("whatsapp", "")
                wsp_url = f"https://wa.me/{wsp_num}?text=Hola,%20vi%20su%20agroveterinaria%20en%20Agrowillay%20y%20deseo%20hacer%20una%20consulta."
                btn_wsp = MDRaisedButton(
                    text="WhatsApp",
                    icon="whatsapp",
                    size_hint_x=0.5,
                    height=dp(42),
                    md_bg_color=(0.10, 0.70, 0.40, 1),
                    on_release=lambda *_, u=wsp_url: self._open_url(u),
                )

                # Botón Llamar Directo
                tel_num = item.get("telefono", "")
                btn_tel = MDRaisedButton(
                    text="Llamar",
                    icon="phone",
                    size_hint_x=0.5,
                    height=dp(42),
                    md_bg_color=(0.18, 0.30, 0.45, 1),
                    on_release=lambda *_, t=tel_num: self._call_phone(t),
                )

                acciones.add_widget(btn_wsp)
                acciones.add_widget(btn_tel)
                card.add_widget(acciones)
                box.add_widget(card)
        except Exception:
            _write_crash_log(traceback.format_exc())

    def _call_phone(self, phone_number):
        """Abre la app de llamadas del teléfono."""
        if not phone_number:
            toast("Teléfono no disponible")
            return
        url = f"tel:{phone_number.strip()}"
        self._open_url(url)

    # ------------------------------------------------------------------
    # Clima 3D
    # ------------------------------------------------------------------

    def check_weather_alerts(self):
        try:
            home = self.root.ids.sm.get_screen("home")
            box = home.ids.alerts_body
            box.clear_widgets()
            box.add_widget(
                MDLabel(
                    text="Consultando pronóstico y sensores climáticos...",
                    theme_text_color="Custom",
                    text_color=(0.7, 0.75, 0.8, 1),
                    adaptive_height=True,
                )
            )

            # Por defecto Curahuasi/Apurímac si el GPS no está activo
            lat_def, lon_def = -13.5414, -72.6978
            threading.Thread(
                target=self._fetch_weather_thread,
                args=(lat_def, lon_def),
                daemon=True,
            ).start()
        except Exception:
            _write_crash_log(traceback.format_exc())

    def _fetch_weather_thread(self, lat, lon):
        try:
            data = WeatherClient.get_forecast(lat, lon)
            Clock.schedule_once(lambda dt: self._render_weather(data))
        except Exception:
            Clock.schedule_once(
                lambda dt: self._show_weather_msg(
                    "No se pudo consultar el clima. Verifica tu conexión a internet."
                )
            )

    @mainthread
    def _render_weather(self, data):
        try:
            home = self.root.ids.sm.get_screen("home")
            box = home.ids.alerts_body
            box.clear_widgets()

            current = data.get("current", {})
            temp = current.get("temperature_2m", "--")
            hum = current.get("relative_humidity_2m", "--")
            viento = current.get("wind_speed_10m", "--")

            # Resumen actual
            resumen = MDBoxLayout(adaptive_height=True, spacing=dp(12))
            resumen.add_widget(
                MDLabel(
                    text=f"[b][size=28sp]{temp}°C[/size][/b]\nCurahuasi / Apurímac",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(1, 1, 1, 1),
                    adaptive_height=True,
                )
            )
            resumen.add_widget(
                MDLabel(
                    text=f"Humedad: {hum}%\nViento: {viento} km/h",
                    theme_text_color="Custom",
                    text_color=(0.75, 0.80, 0.85, 1),
                    font_style="Caption",
                    adaptive_height=True,
                )
            )
            box.add_widget(resumen)

            # Evaluación de alertas
            dias = data.get("dias", [])
            riesgos = evaluar_riesgo_climatico(dias)

            if riesgos:
                for r in riesgos:
                    alerta_box = MDBoxLayout(
                        adaptive_height=True,
                        padding=dp(8),
                        spacing=dp(8),
                        md_bg_color=(0.30, 0.10, 0.10, 0.6),
                        radius=[10],
                    )
                    alerta_box.add_widget(
                        MDLabel(
                            text=f"[b]{r['tipo']}[/b]: {r['detalle']}",
                            markup=True,
                            theme_text_color="Custom",
                            text_color=(1, 0.8, 0.8, 1),
                            font_style="Caption",
                            adaptive_height=True,
                        )
                    )
                    box.add_widget(alerta_box)
            else:
                box.add_widget(
                    MDLabel(
                        text="Condiciones favorables: Sin alertas críticas de helada o tormenta para los próximos 3 días.",
                        theme_text_color="Custom",
                        text_color=(0.20, 0.85, 0.55, 1),
                        font_style="Caption",
                        adaptive_height=True,
                    )
                )
        except Exception:
            _write_crash_log(traceback.format_exc())

    @mainthread
    def _show_weather_msg(self, msg):
        try:
            home = self.root.ids.sm.get_screen("home")
            box = home.ids.alerts_body
            box.clear_widgets()
            box.add_widget(
                MDLabel(
                    text=msg,
                    theme_text_color="Custom",
                    text_color=(0.7, 0.75, 0.8, 1),
                    adaptive_height=True,
                )
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cámara y Diagnóstico
    # ------------------------------------------------------------------

    def take_photo(self):
        if platform != "android":
            toast("La cámara solo está disponible en el celular")
            return
        try:
            from android import activity, mActivity
            from jnius import autoclass, cast

            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            FileProviderCls = autoclass("androidx.core.content.FileProvider")
            JavaFile = autoclass("java.io.File")

            photo_file = JavaFile(CAMERA_PHOTO_PATH)
            if photo_file.exists():
                photo_file.delete()
            photo_file.getParentFile().mkdirs()

            auth = f"{mActivity.getPackageName()}.fileprovider"
            photo_uri = FileProviderCls.getUriForFile(mActivity, auth, photo_file)

            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(
                MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", photo_uri)
            )
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

            try:
                activity.unbind(on_activity_result=self._on_cam_result)
            except Exception:
                pass
            activity.bind(on_activity_result=self._on_cam_result)
            mActivity.startActivityForResult(intent, CAMERA_REQUEST_CODE)
        except Exception as exc:
            toast(f"No se pudo abrir la cámara: {exc}")

    @mainthread
    def _on_cam_result(self, req, res, data):
        if req != CAMERA_REQUEST_CODE:
            return
        try:
            from android import activity

            activity.unbind(on_activity_result=self._on_cam_result)
        except Exception:
            pass
        if res == -1 and os.path.exists(CAMERA_PHOTO_PATH):
            self._set_preview(CAMERA_PHOTO_PATH)

    def choose_from_gallery(self):
        try:
            from plyer import filechooser

            filechooser.open_file(
                on_selection=lambda s: Clock.schedule_once(
                    lambda dt: self._on_gal_sel(s)
                ),
                filters=[("Imágenes", "*.jpg", "*.jpeg", "*.png", "*.webp")],
            )
        except Exception:
            toast("Selector no disponible en este dispositivo")

    def _on_gal_sel(self, selection):
        if selection and os.path.exists(selection[0]):
            self._set_preview(selection[0])

    def _set_preview(self, path):
        main = self.root.ids.sm.get_screen("main")
        self.current_image_path = path
        main.ids.preview_image.source = path
        main.ids.preview_image.reload()
        main.ids.preview_placeholder.opacity = 0
        main.ids.analyze_btn.disabled = False

    def analyze_photo(self):
        if not self.current_image_path:
            toast("Primero toma o selecciona una foto")
            return
        key = ConfigManager.load_api_key()
        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = True
        main.ids.analyze_btn.text = "ANALIZANDO..."
        main.ids.analyze_spinner.active = True
        main.ids.analyze_spinner.opacity = 1

        threading.Thread(
            target=self._run_analysis,
            args=(self.current_image_path, key),
            daemon=True,
        ).start()

    def _run_analysis(self, path, key):
        try:
            diag = GeminiClient.analyze_image(path, key)
            Clock.schedule_once(lambda dt: self._on_diag_success(diag))
        except Exception as exc:
            Clock.schedule_once(lambda dt: self._on_diag_error(str(exc)))

    @mainthread
    def _on_diag_error(self, err):
        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = False
        main.ids.analyze_btn.text = "ANALIZAR PLANTA CON IA"
        main.ids.analyze_spinner.active = False
        main.ids.analyze_spinner.opacity = 0
        toast(f"Error: {err}")

    @mainthread
    def _on_diag_success(self, diag):
        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = False
        main.ids.analyze_btn.text = "ANALIZAR PLANTA CON IA"
        main.ids.analyze_spinner.active = False
        main.ids.analyze_spinner.opacity = 0

        self.last_diagnosis = diag
        HistoryManager.add(diag, self.current_image_path)
        self._refresh_history()

        # Renderizar resultados
        body = main.ids.result_body
        body.clear_widgets()

        planta = diag.get("planta_identificada", "Planta")
        problema = diag.get("plaga_o_problema", "Sin problema")
        severidad = diag.get("severidad", "media").upper()

        body.add_widget(
            MDLabel(
                text=f"[b]Planta:[/b] {planta}\n[b]Problema:[/b] {problema}\n[b]Severidad:[/b] {severidad}",
                markup=True,
                theme_text_color="Custom",
                text_color=(1, 1, 1, 1),
                adaptive_height=True,
            )
        )

        pasos = diag.get("pasos", [])
        if pasos:
            body.add_widget(
                MDLabel(
                    text="[b]Tratamiento Recomendado:[/b]",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(0.20, 0.85, 0.55, 1),
                    adaptive_height=True,
                )
            )
            for p in pasos:
                body.add_widget(
                    MDLabel(
                        text=f"• {p}",
                        theme_text_color="Custom",
                        text_color=(0.9, 0.9, 0.9, 1),
                        font_style="Caption",
                        adaptive_height=True,
                    )
                )

        prods = diag.get("productos_recomendados", [])
        if prods:
            body.add_widget(
                MDLabel(
                    text="[b]Productos a buscar en Agroveterinaria:[/b]",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(1, 0.75, 0.2, 1),
                    adaptive_height=True,
                )
            )
            for pr in prods:
                body.add_widget(
                    MDLabel(
                        text=f"• {pr}",
                        theme_text_color="Custom",
                        text_color=(0.9, 0.9, 0.9, 1),
                        font_style="Caption",
                        adaptive_height=True,
                    )
                )

        main.ids.result_card.opacity = 1
        main.ids.result_card.disabled = False
        main.ids.locator_card.opacity = 1
        main.ids.locator_card.disabled = False

    def locate_nearby(self):
        """Abre Google Maps con agroveterinarias y viveros cercanos."""
        url = "https://www.google.com/maps/search/agroveterinaria+vivero+cerca+de+mi"
        self._open_url(url)

    def speak_diagnosis(self, diag):
        if not diag:
            return
        toast("Leyendo diagnóstico en voz alta...")
        # Lógica de texto a voz nativo

    def speak_diagnosis_quechua(self, diag):
        if not diag:
            return
        toast("Traduciendo y reproduciendo en Quechua...")

    def _open_url(self, url):
        try:
            if platform == "android":
                from android import mActivity
                from jnius import autoclass

                Uri = autoclass("android.net.Uri")
                Intent = autoclass("android.content.Intent")
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                mActivity.startActivity(intent)
                return
        except Exception:
            pass
        webbrowser.open(url)

    def _refresh_history(self):
        try:
            screen = self.root.ids.sm.get_screen("history")
            box = screen.ids.history_body
            box.clear_widgets()
            items = HistoryManager.load()
            if items:
                screen.ids.history_placeholder.opacity = 0
                for it in items:
                    d = it.get("diagnosis", {})
                    card = MDCard(
                        padding=dp(12),
                        size_hint_y=None,
                        height=dp(80),
                        md_bg_color=(0.08, 0.10, 0.14, 1),
                        radius=[14],
                    )
                    card.add_widget(
                        MDLabel(
                            text=f"[b]{d.get('planta_identificada','Planta')}[/b]\n{d.get('plaga_o_problema','')}\n{it.get('fecha','')}",
                            markup=True,
                            theme_text_color="Custom",
                            text_color=(1, 1, 1, 1),
                            font_style="Caption",
                        )
                    )
                    box.add_widget(card)
            else:
                screen.ids.history_placeholder.opacity = 1
        except Exception:
            pass

    def open_bitacora_dialog(self):
        toast("Bitácora Agrícola activa")

    def show_about_dialog(self):
        MDDialog(
            title="Agrowillay",
            text="Aplicación de fitosanidad agrícola con IA y conexión a agroveterinarias locales.",
            buttons=[MDFlatButton(text="OK", on_release=lambda x: x.dismiss())],
        ).open()

    def show_help_dialog(self):
        MDDialog(
            title="Ayuda Agrowillay",
            text="1. Fotografía la planta con buena luz.\n2. Presiona Analizar para ver el diagnóstico y tratamiento.\n3. Contacta con agroveterinarias locales mediante WhatsApp o llamada para adquirir tus insumos.",
            buttons=[MDFlatButton(text="ENTENDIDO", on_release=lambda x: x.dismiss())],
        ).open()


def _write_crash_log(txt):
    try:
        with open(APP_DATA_DIR / "crash.txt", "a", encoding="utf-8") as f:
            f.write(f"\n{time.ctime()}: {txt}\n")
    except Exception:
        pass


class _Handler(ExceptionHandler):
    def handle_exception(self, inst):
        _write_crash_log(traceback.format_exc())
        return ExceptionManager.PASS


if __name__ == "__main__":
    ExceptionManager.add_handler(_Handler())
    AgrowillayApp().run()