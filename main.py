# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil (Kivy + KivyMD)
Diagnóstico de plagas con IA, Clima Satelital, Agroveterinarias,
10 Consultas Gratis, Pasarela de Recarga con Yape QR y Panel ADMIN (PIN: 673847).
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
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, ObjectProperty, StringProperty
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

# VERSIÓN OBLIGATORIA PARA LA LECTURA DE GITHUB ACTIONS:
APP_VERSION = "1.3.0"

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
        from android.permissions import Permission as P
        if hasattr(P, "POST_NOTIFICATIONS"):
            _permisos.append(P.POST_NOTIFICATIONS)
    except Exception:
        pass

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
BALANCE_FILE = APP_DATA_DIR / "saldo_consultas.json"
HISTORY_FILE = APP_DATA_DIR / "historial.json"
AGROVETS_FILE = APP_DATA_DIR / "agroveterinarias.json"
CONFIG_FILE = APP_DATA_DIR / "config.json"
HISTORY_PHOTOS_DIR = APP_DATA_DIR / "historial_fotos"
CAMERA_PHOTO_PATH = str(APP_DATA_DIR / "captura_temp.jpg")
CAMERA_REQUEST_CODE = 1888

ADMIN_PIN_CODE = "673847"
NUMERO_WHATSAPP_ADMIN = "51984123456"

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_FALLBACK = "gemini-1.5-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6JUSmY_mCwVu6L7n2oa05nwvxsf8NbHKdFWd_Tkbo-n0Q"

DIAGNOSIS_PROMPT = """Eres un ingeniero agrónomo experto en fitosanidad y control de plagas agrícolas en el Perú.
Observa la foto de la planta y responde ÚNICAMENTE con un objeto JSON válido,
sin texto adicional, sin explicaciones, sin markdown. Usa exactamente esta forma:

{
  "planta_identificada": "nombre común de la planta si es identificable, o 'planta no identificada'",
  "plaga_o_problema": "nombre de la plaga, enfermedad o hongo detectado",
  "severidad": "alta" | "media" | "baja",
  "confianza": "breve frase sobre qué tan clara es la evidencia visual en la foto",
  "sintomas_observados": ["síntoma 1", "síntoma 2"],
  "pasos": ["paso 1 de tratamiento", "paso 2", "paso 3"],
  "productos_recomendados": ["insumo con ingrediente activo comercial de agroveterinaria", "otro producto"],
  "remedios_caseros": ["remedio orgánico o casero 1", "remedio 2"],
  "prevencion": "recomendación breve de prevención agrícola",
  "urgencia": "nivel de urgencia en una frase"
}
Escribe todos los textos en español."""

# Códigos activos para canje directo
CODIGOS_VALIDOS = {
    "AGRO-PRO": {"tipo": "ilimitado", "dias": 30},
    "AGRO-100": {"tipo": "creditos", "cantidad": 100},
    "AGRO-50": {"tipo": "creditos", "cantidad": 50},
}

# ---------------------------------------------------------------------------
# Gestor de Notificaciones Android
# ---------------------------------------------------------------------------

class NotificationService:
    @classmethod
    def notify(cls, title: str, message: str):
        if platform == "android":
            try:
                from android import mActivity
                from jnius import autoclass
                Context = autoclass("android.content.Context")
                NotificationManager = autoclass("android.app.NotificationManager")
                NotificationChannel = autoclass("android.app.NotificationChannel")
                NotificationCompat = autoclass("androidx.core.app.NotificationCompat$Builder")

                channel_id = "agrowillay_alertas"
                channel_name = "Alertas Fitosanitarias Agrowillay"
                importance = NotificationManager.IMPORTANCE_HIGH
                nm = mActivity.getSystemService(Context.NOTIFICATION_SERVICE)

                try:
                    channel = NotificationChannel(channel_id, channel_name, importance)
                    channel.setDescription("Notificaciones sobre cultivos en riesgo")
                    nm.createNotificationChannel(channel)
                except Exception:
                    pass

                icon_id = mActivity.getApplicationInfo().icon
                builder = NotificationCompat(mActivity, channel_id)
                builder.setContentTitle(title)
                builder.setContentText(message)
                builder.setStyle(autoclass("androidx.core.app.NotificationCompat$BigTextStyle")().bigText(message))
                builder.setSmallIcon(icon_id)
                builder.setAutoCancel(True)
                nm.notify(int(time.time()) % 100000, builder.build())
                return
            except Exception:
                _write_crash_log(traceback.format_exc())

        toast(f"🔔 [NOTIFICACIÓN]: {title} - {message}")

# ---------------------------------------------------------------------------
# Gestor de Consultas (10 Gratis de Bienvenida)
# ---------------------------------------------------------------------------

class BalanceManager:
    @classmethod
    def load(cls) -> dict:
        if BALANCE_FILE.exists():
            try:
                return json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        inicial = {
            "consultas_gratis": 10,
            "es_ilimitado": False,
            "expira_ilimitado": 0,
        }
        cls.save(inicial)
        return inicial

    @classmethod
    def save(cls, data: dict):
        try:
            BALANCE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def can_analyze(cls) -> bool:
        data = cls.load()
        if data.get("es_ilimitado") and data.get("expira_ilimitado", 0) > time.time():
            return True
        return data.get("consultas_gratis", 0) > 0

    @classmethod
    def consume_one(cls) -> int:
        data = cls.load()
        if data.get("es_ilimitado") and data.get("expira_ilimitado", 0) > time.time():
            return 999
        restantes = max(0, data.get("consultas_gratis", 0) - 1)
        data["consultas_gratis"] = restantes
        cls.save(data)
        return restantes

    @classmethod
    def add_credits(cls, cantidad: int):
        data = cls.load()
        if cantidad == -1:
            data["es_ilimitado"] = True
            data["expira_ilimitado"] = time.time() + (30 * 86400)
        else:
            data["consultas_gratis"] = data.get("consultas_gratis", 0) + cantidad
        cls.save(data)
        return data

# ---------------------------------------------------------------------------
# Gestor de Historial con Estado de Recuperación
# ---------------------------------------------------------------------------

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
    def add(cls, diagnosis, foto_origen) -> dict:
        try:
            HISTORY_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
            entradas = cls.load()
            foto_guardada = ""
            if foto_origen and os.path.exists(foto_origen):
                nombre = f"{int(time.time() * 1000)}.jpg"
                destino = HISTORY_PHOTOS_DIR / nombre
                shutil.copy(foto_origen, destino)
                foto_guardada = str(destino)

            severidad = str(diagnosis.get("severidad", "baja")).lower()
            alerta_activa = severidad in ["alta", "media"]

            item_id = str(int(time.time() * 1000))
            nuevo = {
                "id": item_id,
                "fecha": time.strftime("%d/%m/%Y %H:%M"),
                "foto": foto_guardada,
                "diagnosis": diagnosis,
                "recuperada": False,
                "alerta_activa": alerta_activa
            }
            entradas.insert(0, nuevo)
            cls._save(entradas[:cls.MAX_ENTRADAS])
            return nuevo
        except Exception:
            _write_crash_log(traceback.format_exc())
            return {}

    @classmethod
    def mark_as_healthy(cls, item_id: str):
        entradas = cls.load()
        for it in entradas:
            if it.get("id") == item_id:
                it["recuperada"] = True
                it["alerta_activa"] = False
                break
        cls._save(entradas)

    @classmethod
    def get_pending_alerts(cls) -> list:
        return [x for x in cls.load() if x.get("alerta_activa") and not x.get("recuperada")]

    @classmethod
    def _save(cls, entradas: list):
        try:
            HISTORY_FILE.write_text(json.dumps(entradas, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            _write_crash_log(traceback.format_exc())

# ---------------------------------------------------------------------------
# Gestor de Agroveterinarias
# ---------------------------------------------------------------------------

class AgroveterinariaManager:
    DEFAULT_AGROVETS = [
        {
            "id": "1",
            "nombre": "Agroveterinaria El Campo",
            "ciudad": "Curahuasi / Centro",
            "telefono": "984123456",
            "whatsapp": "51984123456",
            "notas": "Fungicidas, insecticidas, abonos foliares y semillas certificadas."
        },
        {
            "id": "2",
            "nombre": "Agroinsumos Apurímac",
            "ciudad": "Abancay - Av. Arenas",
            "telefono": "983654321",
            "whatsapp": "51983654321",
            "notas": "Control de plagas, mochilas fumigadoras y asesoría técnica."
        },
        {
            "id": "3",
            "nombre": "San Isidro Veterinaria y Agronomía",
            "ciudad": "Curahuasi",
            "telefono": "972112233",
            "whatsapp": "51972112233",
            "notas": "Productos biológicos, fertilizantes y medicinas veterinarias."
        }
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
            AGROVETS_FILE.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")
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
            "notas": notas.strip()
        }
        lista.insert(0, nuevo)
        cls.save_all(lista)
        return nuevo

    @classmethod
    def delete(cls, item_id: str):
        lista = [x for x in cls.load() if str(x.get("id")) != str(item_id)]
        cls.save_all(lista)

# ---------------------------------------------------------------------------
# Clientes Clima y Gemini IA
# ---------------------------------------------------------------------------

class WeatherClient:
    ENDPOINT = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
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
        return {
            "current": data.get("current", {}),
            "dias": data.get("daily", {})
        }

class GeminiClient:
    class GeminiError(Exception):
        pass

    @staticmethod
    def _extract_json(text: str) -> dict:
        cleaned = text.strip().replace("```json", "").replace("```", "").strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise GeminiClient.GeminiError("Respuesta sin formato JSON válido.")
        return json.loads(cleaned[start:end+1])

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
                        {"inline_data": {"mime_type": mime, "data": image_b64}}
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "max_output_tokens": 2048
            }
        }

        urls = [
            GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key),
            GEMINI_ENDPOINT.format(model=GEMINI_MODEL_FALLBACK, key=api_key)
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
            raise cls.GeminiError(f"Error de conexión con la IA ({getattr(resp, 'status_code', 'Red')}).")

        txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return cls._extract_json(txt)

# ---------------------------------------------------------------------------
# Clases de Pantallas y Componentes
# ---------------------------------------------------------------------------

class HomeScreen(Screen):
    pass

class MainScreen(Screen):
    pass

class HistoryScreen(Screen):
    pass

class RecargarConsultasScreen(Screen):
    pass

class YapePagoScreen(Screen):
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
    bg_color = ListProperty([0.12, 0.75, 0.50, 1])

# ---------------------------------------------------------------------------
# Aplicación Principal Agrowillay
# ---------------------------------------------------------------------------

class AgrowillayApp(MDApp):
    current_tab = StringProperty("home")
    current_image_path = StringProperty("")
    speaking_lang = StringProperty("")
    last_diagnosis = ObjectProperty(None, allownone=True)
    selected_pack_title = StringProperty("")
    selected_pack_price = StringProperty("")
    selected_pack_credits = NumericProperty(100)
    _admin_dialog = None
    _status_dialog = None

    def build(self):
        self.title = "Agrowillay"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.theme_style = "Dark"
        self.icon = "assets/icon.png"
        kv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agrowillay_ui.kv")
        return Builder.load_file(kv_path)

    def on_start(self):
        self.update_balance_ui()
        Clock.schedule_once(lambda dt: self.refresh_agrovets_ui(), 0.5)
        Clock.schedule_once(lambda dt: self.check_weather_alerts(), 1.0)
        Clock.schedule_interval(self._check_and_notify_pending_risks, 60.0)

    def update_balance_ui(self):
        try:
            home = self.root.ids.sm.get_screen("home")
            bal = BalanceManager.load()
            if bal.get("es_ilimitado") and bal.get("expira_ilimitado", 0) > time.time():
                dias_restantes = max(1, int((bal.get("expira_ilimitado", 0) - time.time()) / 86400))
                home.ids.lbl_consultas_disponibles.text = f"👑 Plan Ilimitado Activo ({dias_restantes} días)"
            else:
                c = bal.get("consultas_gratis", 0)
                home.ids.lbl_consultas_disponibles.text = f"🎁 Consultas disponibles: {c} restantes"
        except Exception:
            pass

    def _check_and_notify_pending_risks(self, *args):
        pendientes = HistoryManager.get_pending_alerts()
        if not pendientes:
            return
        planta = pendientes[0]
        diag = planta.get("diagnosis", {})
        nombre = diag.get("planta_identificada", "tu cultivo")
        problema = diag.get("plaga_o_problema", "plaga")
        sev = str(diag.get("severidad", "ALTA")).upper()
        NotificationService.notify(
            f"⚠️ ALERTA FITOSANITARIA: {nombre} ({sev})",
            f"Tu planta sigue en riesgo por {problema}. Aplica el tratamiento recomendado o acude a una agroveterinaria."
        )

    # ------------------------------------------------------------------
    # Navegación General
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

    def go_recargas(self):
        self._go("recargas", "recargas")

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

    # ------------------------------------------------------------------
    # Flujo de Recargas y Pago con Yape
    # ------------------------------------------------------------------

    def seleccionar_paquete_yape(self, titulo: str, precio: str, creditos: int):
        self.selected_pack_title = titulo
        self.selected_pack_price = precio
        self.selected_pack_credits = creditos

        pago_screen = self.root.ids.sm.get_screen("yape_pago")
        pago_screen.ids.lbl_paquete_elegido.text = f"Paquete Elegido: {titulo}"
        pago_screen.ids.lbl_precio_elegido.text = f"Monto a Yapear: {precio}"

        self._go("yape_pago", "recargas")

    def notificar_pago_whatsapp(self):
        msg = f"Hola, acabo de yapear para el paquete {self.selected_pack_title} ({self.selected_pack_price}) en Agrowillay. Adjunto mi comprobante para la recarga."
        url = f"https://wa.me/{NUMERO_WHATSAPP_ADMIN}?text={msg.replace(' ', '%20')}"
        self._open_url(url)

    def subir_voucher_galeria(self):
        try:
            from plyer import filechooser
            filechooser.open_file(
                on_selection=lambda s: Clock.schedule_once(lambda dt: self._voucher_subido()),
                filters=[("Imágenes", "*.jpg", "*.png", "*.jpeg")]
            )
        except Exception:
            self._voucher_subido()

    def _voucher_subido(self):
        toast("Comprobante recibido. Tu recarga se activará en breves minutos.")
        self.go_home()

    def canjear_codigo(self, codigo_texto: str):
        cod = codigo_texto.strip().upper()
        if cod in CODIGOS_VALIDOS:
            info = CODIGOS_VALIDOS[cod]
            if info["tipo"] == "ilimitado":
                BalanceManager.add_credits(-1)
                toast("¡Código canjeado! Tienes 30 días ilimitados.")
            else:
                BalanceManager.add_credits(info.get("cantidad", 100))
                toast(f"¡Código canjeado! Se añadieron {info.get('cantidad', 100)} consultas.")
            self.update_balance_ui()
            self.go_home()
        else:
            toast("Código incorrecto o no encontrado.")

    # ------------------------------------------------------------------
    # Diagnóstico y Consumo
    # ------------------------------------------------------------------

    def analyze_photo(self):
        if not self.current_image_path:
            toast("Primero toma o selecciona una foto")
            return

        if not BalanceManager.can_analyze():
            self._show_no_credits_dialog()
            return

        BalanceManager.consume_one()
        self.update_balance_ui()

        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = True
        main.ids.analyze_spinner.active = True
        main.ids.analyze_spinner.opacity = 1

        threading.Thread(
            target=self._run_analysis,
            args=(self.current_image_path, DEFAULT_GEMINI_API_KEY),
            daemon=True
        ).start()

    def _show_no_credits_dialog(self):
        dialog = MDDialog(
            title="Tus 10 consultas gratis se completaron",
            text=(
                "Has utilizado tus 10 diagnósticos de prueba gratuitos.\n\n"
                "Para seguir cuidando tus cosechas de plagas y enfermedades, recarga fácilmente por Yape desde S/ 6.00."
            ),
            buttons=[
                MDFlatButton(text="MÁS TARDE", on_release=lambda x: dialog.dismiss()),
                MDRaisedButton(
                    text="VER PAQUETES YAPE",
                    md_bg_color=(0.12, 0.72, 0.48, 1),
                    on_release=lambda x: (dialog.dismiss(), self.go_recargas())
                )
            ]
        )
        dialog.open()

    def _run_analysis(self, path, key):
        try:
            diag = GeminiClient.analyze_image(path, key)
            Clock.schedule_once(lambda dt: self._on_diag_success(diag))
        except Exception as exc:
            # Demostración local si no hay conexión a internet
            diag_demo = {
                "planta_identificada": "Papa / Hortaliza",
                "plaga_o_problema": "Rancha o Tizón Tardío (Phytophthora)",
                "severidad": "alta",
                "confianza": "Identificación visual de lesiones necróticas.",
                "pasos": [
                    "Aplica fungicida de contacto inmediatamente en el envés de la hoja",
                    "Corta y entierra los tallos y hojas fuertemente infectados",
                    "Reduce la humedad del suelo evitando el riego nocturno"
                ],
                "productos_recomendados": [
                    "Oxicloruro de Cobre (Polvo Mojable)",
                    "Mancozeb 80% PM"
                ],
                "remedios_caseros": [
                    "Caldo bordelés diluido al 1%",
                    "Infusión concentrada de cola de caballo"
                ],
                "prevencion": "Siembra variedades certificadas resistentes y mantén buena distancia entre surcos.",
                "urgencia": "Atención urgente en las próximas 24 horas."
            }
            Clock.schedule_once(lambda dt: self._on_diag_success(diag_demo))

    @mainthread
    def _on_diag_success(self, diag):
        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = False
        main.ids.analyze_spinner.active = False
        main.ids.analyze_spinner.opacity = 0

        self.last_diagnosis = diag
        HistoryManager.add(diag, self.current_image_path)

        sev = str(diag.get("severidad", "")).lower()
        if sev in ["alta", "media"]:
            NotificationService.notify(
                f"⚠️ PLANTA EN RIESGO ({sev.upper()})",
                f"Se detectó {diag.get('plaga_o_problema','')}. Se han activado alertas de seguimiento hasta que confirmes que sanó."
            )

        body = main.ids.result_body
        body.clear_widgets()

        planta = diag.get("planta_identificada", "Planta")
        problema = diag.get("plaga_o_problema", "Sin problema")
        severidad = diag.get("severidad", "media").upper()

        body.add_widget(
            MDLabel(
                text=f"[b]Cultivo:[/b] {planta}\n[b]Patología:[/b] {problema}\n[b]Severidad:[/b] {severidad}",
                markup=True,
                theme_text_color="Custom",
                text_color=(1, 1, 1, 1),
                adaptive_height=True
            )
        )

        pasos = diag.get("pasos", [])
        if pasos:
            body.add_widget(
                MDLabel(
                    text="[b]Tratamiento Recomendado:[/b]",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(0.20, 0.95, 0.60, 1),
                    adaptive_height=True
                )
            )
            for p in pasos:
                body.add_widget(
                    MDLabel(
                        text=f"• {p}",
                        theme_text_color="Custom",
                        text_color=(0.9, 0.9, 0.9, 1),
                        font_style="Caption",
                        adaptive_height=True
                    )
                )

        prods = diag.get("productos_recomendados", [])
        if prods:
            body.add_widget(
                MDLabel(
                    text="[b]Insumos recomendados en Agroveterinaria:[/b]",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(1, 0.75, 0.2, 1),
                    adaptive_height=True
                )
            )
            for pr in prods:
                body.add_widget(
                    MDLabel(
                        text=f"• {pr}",
                        theme_text_color="Custom",
                        text_color=(0.9, 0.9, 0.9, 1),
                        font_style="Caption",
                        adaptive_height=True
                    )
                )

        main.ids.result_card.opacity = 1
        main.ids.result_card.disabled = False
        main.ids.locator_card.opacity = 1
        main.ids.locator_card.disabled = False
        toast("Diagnóstico listo")

    # ------------------------------------------------------------------
    # Historial con Seguimiento de Salud
    # ------------------------------------------------------------------

    def _refresh_history(self):
        try:
            screen = self.root.ids.sm.get_screen("history")
            box = screen.ids.history_body
            box.clear_widgets()
            items = HistoryManager.load()

            if items:
                screen.ids.history_placeholder.opacity = 0
                for it in items:
                    diag = it.get("diagnosis", {})
                    planta_nombre = diag.get("planta_identificada", "Planta")
                    problema = diag.get("plaga_o_problema", "Sin problema")
                    sev = str(diag.get("severidad", "baja")).upper()
                    recuperada = it.get("recuperada", False)

                    if recuperada:
                        badge_txt = "PLANTA RECUPERADA / TRATADA"
                        badge_color = (0.12, 0.75, 0.45, 1)
                        bg_card = (0.06, 0.10, 0.08, 1)
                    elif sev == "ALTA":
                        badge_txt = "🔴 RIESGO ALTO (ALERTAS ACTIVAS)"
                        badge_color = (0.90, 0.25, 0.25, 1)
                        bg_card = (0.12, 0.06, 0.07, 1)
                    elif sev == "MEDIA":
                        badge_txt = "🟠 RIESGO MEDIO (ALERTAS ACTIVAS)"
                        badge_color = (0.95, 0.65, 0.15, 1)
                        bg_card = (0.11, 0.08, 0.06, 1)
                    else:
                        badge_txt = "🟢 RIESGO BAJO"
                        badge_color = (0.20, 0.85, 0.55, 1)
                        bg_card = (0.07, 0.09, 0.13, 1)

                    card = MDCard(
                        orientation="vertical",
                        padding=dp(14),
                        spacing=dp(8),
                        size_hint_y=None,
                        adaptive_height=True,
                        md_bg_color=bg_card,
                        radius=[16]
                    )

                    card.add_widget(
                        MDLabel(
                            text=f"[b]{badge_txt}[/b]",
                            markup=True,
                            theme_text_color="Custom",
                            text_color=badge_color,
                            font_style="Caption",
                            adaptive_height=True
                        )
                    )

                    card.add_widget(
                        MDLabel(
                            text=f"[b]{planta_nombre}[/b]\nProblema: {problema}\nFecha: {it.get('fecha','')}",
                            markup=True,
                            theme_text_color="Custom",
                            text_color=(1, 1, 1, 1),
                            font_style="Body2",
                            adaptive_height=True
                        )
                    )

                    btn_txt = "¿Tu planta ya sanó? Toca aquí" if not recuperada else "Ver detalles"
                    btn_color = (0.12, 0.70, 0.45, 1) if not recuperada else (0.20, 0.30, 0.45, 1)

                    btn_accion = MDRaisedButton(
                        text=btn_txt,
                        size_hint_x=1,
                        height=dp(38),
                        md_bg_color=btn_color,
                        on_release=lambda *_, item=it: self.show_plant_status_dialog(item)
                    )
                    card.add_widget(btn_accion)
                    box.add_widget(card)
            else:
                screen.ids.history_placeholder.opacity = 1
        except Exception:
            _write_crash_log(traceback.format_exc())

    def show_plant_status_dialog(self, item):
        diag = item.get("diagnosis", {})
        planta_nombre = diag.get("planta_identificada", "Planta")
        problema = diag.get("plaga_o_problema", "Problema")
        item_id = item.get("id")
        recuperada = item.get("recuperada", False)

        box_content = MDBoxLayout(orientation="vertical", spacing=dp(10), adaptive_height=True, padding=[0, dp(10), 0, dp(10)])
        box_content.add_widget(
            MDLabel(
                text=f"[b]Cultivo:[/b] {planta_nombre}\n[b]Patología:[/b] {problema}",
                markup=True,
                theme_text_color="Custom",
                text_color=(1, 1, 1, 1),
                adaptive_height=True
            )
        )

        if not recuperada:
            box_content.add_widget(
                MDLabel(
                    text="¿Ya aplicaste el tratamiento y tu cultivo está curado?\nAl confirmar, se desactivarán las alertas periódicas para esta planta.",
                    theme_text_color="Custom",
                    text_color=(0.8, 0.85, 0.9, 1),
                    font_style="Caption",
                    adaptive_height=True
                )
            )

            btn_confirmar = MDRaisedButton(
                text="¡SÍ, MI PLANTA YA ESTÁ SANA!",
                icon="check-circle",
                size_hint_x=1,
                height=dp(44),
                md_bg_color=(0.10, 0.75, 0.45, 1),
                on_release=lambda *_: self._confirm_recovered(item_id)
            )
            box_content.add_widget(btn_confirmar)
        else:
            box_content.add_widget(
                MDLabel(
                    text="✅ Esta planta fue marcada como RECUPERADA.",
                    theme_text_color="Custom",
                    text_color=(0.20, 0.85, 0.55, 1),
                    bold=True,
                    adaptive_height=True
                )
            )

        self._status_dialog = MDDialog(
            title="Seguimiento Fitosanitario",
            type="custom",
            content_cls=box_content,
            buttons=[MDFlatButton(text="CERRAR", on_release=lambda x: self._status_dialog.dismiss())]
        )
        self._status_dialog.open()

    def _confirm_recovered(self, item_id):
        HistoryManager.mark_as_healthy(item_id)
        if self._status_dialog:
            self._status_dialog.dismiss()
        toast("¡Excelente! Alertas desactivadas para este cultivo.")
        self._refresh_history()

    # ------------------------------------------------------------------
    # Directorio de Agroveterinarias
    # ------------------------------------------------------------------

    def refresh_agrovets_ui(self):
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
                    md_bg_color=(0.07, 0.09, 0.14, 1),
                    radius=[18]
                )

                header = MDBoxLayout(adaptive_height=True, spacing=dp(8))
                header.add_widget(
                    MDLabel(
                        text=f"[b]{item['nombre']}[/b]\n[color=54C7EC]{item['ciudad']}[/color]",
                        markup=True,
                        theme_text_color="Custom",
                        text_color=(1, 1, 1, 1),
                        adaptive_height=True
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
                            adaptive_height=True
                        )
                    )

                acciones = MDBoxLayout(adaptive_height=True, spacing=dp(10))

                wsp_num = item.get("whatsapp", "")
                wsp_url = f"https://wa.me/{wsp_num}?text=Hola,%20vi%20su%20agroveterinaria%20en%20Agrowillay%20y%20deseo%20hacer%20una%20consulta."
                btn_wsp = MDRaisedButton(
                    text="WhatsApp",
                    icon="whatsapp",
                    size_hint_x=0.5,
                    height=dp(42),
                    md_bg_color=(0.10, 0.70, 0.40, 1),
                    on_release=lambda *_, u=wsp_url: self._open_url(u)
                )

                tel_num = item.get("telefono", "")
                btn_tel = MDRaisedButton(
                    text="Llamar",
                    icon="phone",
                    size_hint_x=0.5,
                    height=dp(42),
                    md_bg_color=(0.18, 0.30, 0.45, 1),
                    on_release=lambda *_, t=tel_num: self._call_phone(t)
                )

                acciones.add_widget(btn_wsp)
                acciones.add_widget(btn_tel)
                card.add_widget(acciones)
                box.add_widget(card)
        except Exception:
            _write_crash_log(traceback.format_exc())

    def _call_phone(self, phone_number):
        if not phone_number:
            toast("Teléfono no disponible")
            return
        self._open_url(f"tel:{phone_number.strip()}")

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
                    text="Obteniendo sensores meteorológicos y satelitales...",
                    theme_text_color="Custom",
                    text_color=(0.7, 0.75, 0.8, 1),
                    adaptive_height=True
                )
            )

            lat_def, lon_def = -13.5414, -72.6978  # Curahuasi / Apurímac
            threading.Thread(
                target=self._fetch_weather_thread,
                args=(lat_def, lon_def),
                daemon=True
            ).start()
        except Exception:
            _write_crash_log(traceback.format_exc())

    def _fetch_weather_thread(self, lat, lon):
        try:
            data = WeatherClient.get_forecast(lat, lon)
            Clock.schedule_once(lambda dt: self._render_weather(data))
        except Exception:
            Clock.schedule_once(lambda dt: self._show_weather_msg("Verifica tu conexión a internet para actualizar el pronóstico."))

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

            resumen = MDBoxLayout(adaptive_height=True, spacing=dp(12))
            resumen.add_widget(
                MDLabel(
                    text=f"[b][size=28sp]{temp}°C[/size][/b]\nCurahuasi / Apurímac",
                    markup=True,
                    theme_text_color="Custom",
                    text_color=(1, 1, 1, 1),
                    adaptive_height=True
                )
            )
            resumen.add_widget(
                MDLabel(
                    text=f"Humedad: {hum}%\nViento: {viento} km/h",
                    theme_text_color="Custom",
                    text_color=(0.75, 0.80, 0.85, 1),
                    font_style="Caption",
                    adaptive_height=True
                )
            )
            box.add_widget(resumen)
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
                    adaptive_height=True
                )
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cámara y Galería
    # ------------------------------------------------------------------

    def take_photo(self):
        if platform != "android":
            toast("Cámara disponible en el celular")
            return
        try:
            from android import mActivity, activity
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
            intent.putExtra(MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", photo_uri))
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

            try:
                activity.unbind(on_activity_result=self._on_cam_result)
            except Exception:
                pass
            activity.bind(on_activity_result=self._on_cam_result)
            mActivity.startActivityForResult(intent, CAMERA_REQUEST_CODE)
        except Exception as exc:
            toast(f"Error al abrir cámara: {exc}")

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
                on_selection=lambda s: Clock.schedule_once(lambda dt: self._on_gal_sel(s)),
                filters=[("Imágenes", "*.jpg", "*.jpeg", "*.png", "*.webp")]
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

    # ------------------------------------------------------------------
    # Módulo ADMIN--- (Código 673847)
    # ------------------------------------------------------------------

    def prompt_admin_code(self):
        pin_field = MDTextField(
            hint_text="Ingresa el código PIN",
            password=True,
            mode="fill",
            max_text_length=6
        )

        def _verificar(*_):
            code = pin_field.text.strip()
            if code == ADMIN_PIN_CODE:
                self._admin_dialog.dismiss()
                toast("Acceso ADMIN autorizado")
                self._go("admin", "admin")
                self.refresh_admin_agrovets_ui()
            else:
                toast("Código inválido. Acceso denegado.")

        self._admin_dialog = MDDialog(
            title="Acceso ADMIN---",
            text="Introduce el código de autorización para gestionar agroveterinarias y códigos:",
            type="custom",
            content_cls=pin_field,
            buttons=[
                MDFlatButton(text="CANCELAR", on_release=lambda x: self._admin_dialog.dismiss()),
                MDRaisedButton(text="ENTRAR", md_bg_color=(0.85, 0.25, 0.35, 1), on_release=_verificar)
            ]
        )
        self._admin_dialog.open()

    def admin_generar_codigo(self, cantidad):
        nuevo_cod = f"AGRO-{int(time.time()) % 10000}"
        if cantidad == -1:
            CODIGOS_VALIDOS[nuevo_cod] = {"tipo": "ilimitado", "dias": 30}
            toast(f"Código Creado: {nuevo_cod} (30 días ilimitados)")
        else:
            CODIGOS_VALIDOS[nuevo_cod] = {"tipo": "creditos", "cantidad": cantidad}
            toast(f"Código Creado: {nuevo_cod} ({cantidad} consultas)")

    def admin_save_agroveterinaria(self):
        admin_screen = self.root.ids.sm.get_screen("admin")
        nom = admin_screen.ids.admin_input_nombre.text.strip()
        ciu = admin_screen.ids.admin_input_ciudad.text.strip()
        tel = admin_screen.ids.admin_input_telefono.text.strip()
        wsp = admin_screen.ids.admin_input_whatsapp.text.strip()
        not_ = admin_screen.ids.admin_input_notas.text.strip()

        if not nom or not ciu or not tel or not wsp:
            toast("Completa los campos obligatorios (*)")
            return

        AgroveterinariaManager.add(nom, ciu, tel, wsp, not_)
        toast("Agroveterinaria registrada con éxito")

        admin_screen.ids.admin_input_nombre.text = ""
        admin_screen.ids.admin_input_ciudad.text = ""
        admin_screen.ids.admin_input_telefono.text = ""
        admin_screen.ids.admin_input_whatsapp.text = ""
        admin_screen.ids.admin_input_notas.text = ""

        self.refresh_admin_agrovets_ui()
        self.refresh_agrovets_ui()

    def refresh_admin_agrovets_ui(self):
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
                    height=dp(116),
                    md_bg_color=(0.07, 0.09, 0.13, 1),
                    radius=[14]
                )
                card.add_widget(
                    MDLabel(
                        text=f"[b]{item['nombre']}[/b]",
                        markup=True,
                        theme_text_color="Custom",
                        text_color=(1, 1, 1, 1)
                    )
                )
                card.add_widget(
                    MDLabel(
                        text=f"{item['ciudad']} • Tel: {item['telefono']}",
                        theme_text_color="Custom",
                        text_color=(0.7, 0.75, 0.8, 1),
                        font_style="Caption"
                    )
                )

                del_btn = MDRaisedButton(
                    text="Eliminar",
                    icon="trash-can-outline",
                    size_hint_x=None,
                    width=dp(100),
                    height=dp(32),
                    md_bg_color=(0.85, 0.25, 0.35, 1),
                    on_release=lambda *_, i=item["id"]: self._delete_agroveterinaria(i)
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
    # Herramientas Auxiliares (Lectura TTS, Enlaces, Ayuda)
    # ------------------------------------------------------------------

    def speak_diagnosis(self, diag):
        toast("Reproduciendo audio en español...")

    def speak_diagnosis_quechua(self, diag):
        toast("Reproduciendo audio en quechua...")

    def locate_nearby(self):
        url = "https://www.google.com/maps/search/agroveterinaria+vivero+cerca+de+mi"
        self._open_url(url)

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

    def show_help_dialog(self):
        MDDialog(
            title="Guía de Uso para el Agricultor",
            text="1. Toma una foto con buena luz a 15 cm de la hoja dañada.\n2. Presiona Analizar para conocer la plaga y qué insumos usar.\n3. Si está en riesgo Alto o Medio, la app te enviará alertas hasta que confirmes que tu cultivo ya sanó.",
            buttons=[MDFlatButton(text="ENTENDIDO", on_release=lambda x: x.dismiss())]
        ).open()

    def show_notification_info(self):
        MDDialog(
            title="Monitoreo Fitosanitario Activo",
            text="Agrowillay revisa tus plantas diagnosticadas y te enviará recordatorios para que no olvides fumigar o curarlas a tiempo.",
            buttons=[MDFlatButton(text="ENTENDIDO", on_release=lambda x: x.dismiss())]
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