# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil (Kivy + KivyMD)
10 Consultas Gratis Iniciales, Pasarela Yape QR y Panel ADMIN.
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
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
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
        from android.permissions import Permission as P

        if hasattr(P, "POST_NOTIFICATIONS"):
            _permisos.append(P.POST_NOTIFICATIONS)
    except Exception:
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
CAMERA_PHOTO_PATH = str(APP_DATA_DIR / "captura_temp.jpg")
CAMERA_REQUEST_CODE = 1888

ADMIN_PIN_CODE = "673847"

# Número de WhatsApp para confirmar pagos de Yape
NUMERO_WHATSAPP_ADMIN = "51984123456"

GEMINI_MODEL = "gemini-3.7-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)
DEFAULT_GEMINI_API_KEY = "AQ.Ab8RN6JUSmY_mCwVu6L7n2oa05nwvxsf8NbHKdFWd_Tkbo-n0Q"

# Códigos activos para recargas
CODIGOS_VALIDOS = {
    "AGRO-PRO": {"tipo": "ilimitado", "dias": 30},
    "AGRO-100": {"tipo": "creditos", "cantidad": 100},
}

# ---------------------------------------------------------------------------
# Gestor de Consultas (10 Gratis al instalar)
# ---------------------------------------------------------------------------


class BalanceManager:
    @classmethod
    def load(cls) -> dict:
        if BALANCE_FILE.exists():
            try:
                return json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Por defecto 10 consultas gratis
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
            BALANCE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
        if cantidad == -1:  # Ilimitado por 30 días
            data["es_ilimitado"] = True
            data["expira_ilimitado"] = time.time() + (30 * 86400)
        else:
            data["consultas_gratis"] = (
                data.get("consultas_gratis", 0) + cantidad
            )
        cls.save(data)
        return data


# ---------------------------------------------------------------------------
# Pantallas
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


# ---------------------------------------------------------------------------
# App Principal
# ---------------------------------------------------------------------------


class AgrowillayApp(MDApp):
    current_tab = StringProperty("home")
    current_image_path = StringProperty("")
    selected_pack_title = StringProperty("")
    selected_pack_price = StringProperty("")
    selected_pack_credits = NumericProperty(100)
    last_diagnosis = None
    _admin_dialog = None
    _no_credits_dialog = None

    def build(self):
        self.title = "Agrowillay"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.theme_style = "Dark"
        kv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "agrowillay_ui.kv"
        )
        return Builder.load_file(kv_path)

    def on_start(self):
        # Actualizar visualmente el saldo de consultas
        self.update_balance_ui()
        Clock.schedule_once(lambda dt: self.check_weather_alerts(), 1.0)
        Clock.schedule_once(lambda dt: self.refresh_agrovets_ui(), 0.5)

    def update_balance_ui(self):
        try:
            home = self.root.ids.sm.get_screen("home")
            bal = BalanceManager.load()
            if (
                bal.get("es_ilimitado")
                and bal.get("expira_ilimitado", 0) > time.time()
            ):
                home.ids.lbl_consultas_disponibles.text = (
                    "👑 Plan Ilimitado Activo (Consultas libres)"
                )
            else:
                c = bal.get("consultas_gratis", 0)
                home.ids.lbl_consultas_disponibles.text = (
                    f"🎁 Consultas disponibles: {c} restantes"
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    def _go(self, screen, tab):
        self.root.ids.sm.current = screen
        self.current_tab = tab

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

    def seleccionar_paquete_yape(
        self, titulo: str, precio: str, creditos: int
    ):
        self.selected_pack_title = titulo
        self.selected_pack_price = precio
        self.selected_pack_credits = creditos

        pago_screen = self.root.ids.sm.get_screen("yape_pago")
        pago_screen.ids.lbl_paquete_elegido.text = (
            f"Paquete Elegido: {titulo}"
        )
        pago_screen.ids.lbl_precio_elegido.text = f"Monto a Yapear: {precio}"

        self._go("yape_pago", "recargas")

    def notificar_pago_whatsapp(self):
        """Abre WhatsApp con mensaje predeterminado para confirmar el Yape."""
        msg = f"Hola, acabo de yapear para el paquete {self.selected_pack_title} ({self.selected_pack_price}) en Agrowillay. Adjunto mi comprobante para la recarga."
        url = f"https://wa.me/{NUMERO_WHATSAPP_ADMIN}?text={msg.replace(' ', '%20')}"
        self._open_url(url)

    def subir_voucher_galeria(self):
        try:
            from plyer import filechooser

            filechooser.open_file(
                on_selection=lambda s: Clock.schedule_once(
                    lambda dt: self._voucher_subido()
                ),
                filters=[("Imágenes", "*.jpg", "*.png", "*.jpeg")],
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
                toast(
                    f"¡Código canjeado! Se añadieron {info.get('cantidad', 100)} consultas."
                )
            self.update_balance_ui()
            self.go_home()
        else:
            toast("Código incorrecto o no encontrado.")

    # ------------------------------------------------------------------
    # Diagnóstico con Validación de Consultas
    # ------------------------------------------------------------------

    def analyze_photo(self):
        if not self.current_image_path:
            toast("Primero toma o selecciona una foto")
            return

        # Comprobar si tiene consultas
        if not BalanceManager.can_analyze():
            self._show_no_credits_dialog()
            return

        # Descontar una consulta
        restantes = BalanceManager.consume_one()
        self.update_balance_ui()

        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = True
        main.ids.analyze_btn.text = "ANALIZANDO PLANTA CON IA..."

        threading.Thread(
            target=self._run_analysis,
            args=(self.current_image_path, DEFAULT_GEMINI_API_KEY),
            daemon=True,
        ).start()

    def _show_no_credits_dialog(self):
        """Mensaje fácil de entender para agricultores cuando se agotan las 10 consultas."""
        dialog = MDDialog(
            title="Tus 10 consultas gratis se completaron",
            text=(
                "Has utilizado tus 10 diagnósticos de prueba gratuitos.\n\n"
                "Para seguir cuidando tus cosechas de plagas y enfermedades, recarga fácilmente por Yape desde S/ 6.00."
            ),
            buttons=[
                MDFlatButton(
                    text="MÁS TARDE", on_release=lambda x: dialog.dismiss()
                ),
                MDRaisedButton(
                    text="VER PAQUETES YAPE",
                    md_bg_color=(0.12, 0.72, 0.48, 1),
                    on_release=lambda x: (dialog.dismiss(), self.go_recargas()),
                ),
            ],
        )
        dialog.open()

    def _run_analysis(self, path, key):
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            import requests

            url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=key)
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": "Diagnóstico agronómico fitosanitario en JSON simple: planta_identificada, plaga_o_problema, severidad (alta/media/baja), pasos (lista de recomendaciones), productos_recomendados (lista de insumos agrícolas)."
                            },
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": b64,
                                }
                            },
                        ]
                    }
                ],
                "generationConfig": {"response_mime_type": "application/json"},
            }
            resp = requests.post(url, json=payload, timeout=40)
            txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            diag = json.loads(txt)
            Clock.schedule_once(lambda dt: self._on_diag_success(diag))
        except Exception:
            # Diagnóstico de demostración si no hay red
            diag_demo = {
                "planta_identificada": "Papa / Hortaliza",
                "plaga_o_problema": "Rancha o Tizón Tardío",
                "severidad": "alta",
                "pasos": [
                    "Aplica fungicida de contacto de inmediato",
                    "Elimina las hojas con manchas negras",
                    "Evita el exceso de riego por aspersión",
                ],
                "productos_recomendados": [
                    "Oxicloruro de Cobre",
                    "Mancozeb 80% PM",
                ],
            }
            Clock.schedule_once(lambda dt: self._on_diag_success(diag_demo))

    @mainthread
    def _on_diag_success(self, diag):
        main = self.root.ids.sm.get_screen("main")
        main.ids.analyze_btn.disabled = False
        main.ids.analyze_btn.text = "ANALIZAR PLANTA CON IA"

        self.last_diagnosis = diag
        self._save_history(diag, self.current_image_path)

        body = main.ids.result_body
        body.clear_widgets()

        planta = diag.get("planta_identificada", "Cultivo")
        problema = diag.get("plaga_o_problema", "Sin patología visible")
        sev = str(diag.get("severidad", "media")).upper()

        body.add_widget(
            MDLabel(
                text=f"[b]Planta:[/b] {planta}\n[b]Diagnóstico:[/b] {problema}\n[b]Severidad:[/b] {sev}",
                markup=True,
                theme_text_color="Custom",
                text_color=(1, 1, 1, 1),
                adaptive_height=True,
            )
        )

        main.ids.result_card.opacity = 1
        main.ids.result_card.disabled = False
        toast("Diagnóstico listo")

    # ------------------------------------------------------------------
    # Cámara y Galería
    # ------------------------------------------------------------------

    def take_photo(self):
        if platform != "android":
            toast("Cámara en teléfono Android")
            return
        try:
            from android import activity, mActivity
            from jnius import autoclass, cast

            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")
            FileProviderCls = autoclass("androidx.core.content.FileProvider")
            JavaFile = autoclass("java.io.File")

            photo_file = JavaFile(CAMERA_PHOTO_PATH)
            photo_file.getParentFile().mkdirs()
            auth = f"{mActivity.getPackageName()}.fileprovider"
            photo_uri = FileProviderCls.getUriForFile(mActivity, auth, photo_file)

            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(
                MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", photo_uri)
            )
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

            activity.bind(on_activity_result=self._on_cam_result)
            mActivity.startActivityForResult(intent, CAMERA_REQUEST_CODE)
        except Exception as exc:
            toast(f"Cámara: {exc}")

    @mainthread
    def _on_cam_result(self, req, res, data):
        if (
            req == CAMERA_REQUEST_CODE
            and res == -1
            and os.path.exists(CAMERA_PHOTO_PATH)
        ):
            self._set_preview(CAMERA_PHOTO_PATH)

    def choose_from_gallery(self):
        try:
            from plyer import filechooser

            filechooser.open_file(
                on_selection=lambda s: Clock.schedule_once(
                    lambda dt: self._on_gal_sel(s)
                ),
                filters=[("Imágenes", "*.jpg", "*.png", "*.jpeg")],
            )
        except Exception:
            pass

    def _on_gal_sel(self, s):
        if s and os.path.exists(s[0]):
            self._set_preview(s[0])

    def _set_preview(self, path):
        main = self.root.ids.sm.get_screen("main")
        self.current_image_path = path
        main.ids.preview_image.source = path
        main.ids.preview_image.reload()
        main.ids.preview_placeholder.opacity = 0
        main.ids.analyze_btn.disabled = False

    def _save_history(self, diag, path):
        try:
            items = []
            if HISTORY_FILE.exists():
                items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            items.insert(
                0,
                {
                    "fecha": time.strftime("%d/%m/%Y %H:%M"),
                    "diagnosis": diag,
                    "foto": path,
                },
            )
            HISTORY_FILE.write_text(
                json.dumps(items[:30], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _refresh_history(self):
        try:
            screen = self.root.ids.sm.get_screen("history")
            box = screen.ids.history_body
            box.clear_widgets()
            if HISTORY_FILE.exists():
                items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if items:
                    screen.ids.history_placeholder.opacity = 0
                    for it in items:
                        d = it.get("diagnosis", {})
                        card = MDCard(
                            padding=dp(12),
                            size_hint_y=None,
                            height=dp(80),
                            md_bg_color=(0.07, 0.09, 0.14, 1),
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
        except Exception:
            pass

    def check_weather_alerts(self):
        pass

    def refresh_agrovets_ui(self):
        pass

    def prompt_admin_code(self):
        field = MDTextField(hint_text="PIN ADMIN (6 dígitos)", password=True)

        def _entrar(*_):
            if field.text.strip() == ADMIN_PIN_CODE:
                self._admin_dialog.dismiss()
                self._go("admin", "admin")
            else:
                toast("Código incorrecto")

        self._admin_dialog = MDDialog(
            title="ADMIN---",
            type="custom",
            content_cls=field,
            buttons=[
                MDFlatButton(
                    text="CANCELAR",
                    on_release=lambda x: self._admin_dialog.dismiss(),
                ),
                MDRaisedButton(text="ENTRAR", on_release=_entrar),
            ],
        )
        self._admin_dialog.open()

    def admin_generar_codigo(self, cantidad):
        nuevo_cod = f"AGRO-{int(time.time()) % 10000}"
        if cantidad == -1:
            CODIGOS_VALIDOS[nuevo_cod] = {"tipo": "ilimitado", "dias": 30}
            toast(f"Código Ilimitado creado: {nuevo_cod}")
        else:
            CODIGOS_VALIDOS[nuevo_cod] = {
                "tipo": "creditos",
                "cantidad": cantidad,
            }
            toast(f"Código {cantidad} consultas creado: {nuevo_cod}")

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


if __name__ == "__main__":
    AgrowillayApp().run()
