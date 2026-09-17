# -*- coding: utf-8 -*-
"""
Agrowillay — App móvil (Kivy + KivyMD)
Diagnóstico de plagas con IA Gemini, Clima y Directorio de Agroveterinarias.
Panel ADMIN protegido por PIN (673847).
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

# Código de acceso ADMIN
ADMIN_PIN_CODE = "673847"

# Modelos recomendados de Google Gemini
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash"]
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# IMPORTANTE: Reemplaza esta clave por tu clave oficial de Google AI Studio (inicia con 'AIzaSy...')
DEFAULT_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DIAGNOSIS_PROMPT = """Eres un ingeniero agrónomo experto en fitosanidad y control de plagas agrícolas en valles interandinos y sierra.
Analiza detalladamente la foto de la planta y responde ÚNICAMENTE con un objeto JSON válido, sin bloques de código, sin markdown:

{
  "planta_identificada": "Nombre de la planta observada",
  "plaga_o_problema": "Nombre de la plaga o enfermedad identificada",
  "severidad": "alta",
  "confianza": "Certeza del diagnóstico",
  "sintomas_observados": ["síntoma 1", "síntoma 2"],
  "pasos": ["paso 1 de manejo", "paso 2 de control"],
  "productos_recomendados": ["ingrediente activo o producto comercial"],
  "remedios_caseros": ["remedio orgánico 1", "remedio orgánico 2"],
  "prevencion": "Recomendación preventiva",
  "urgencia": "Urgencia del tratamiento"
}"""

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
    r, g, b = (int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    return [r, g, b, alpha]

# ---------------------------------------------------------------------------
# Gestores de Datos
# ---------------------------------------------------------------------------

class AgroveterinariaManager:
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
            "whatsapp": whatsapp.strip().replace("+", "").replace(" ", "").replace("-", ""),
            "notas": notas.strip(),
        }
        lista.insert(0, nuevo)
        cls.save_all(lista)
        return nuevo

    @classmethod
    def delete(cls, item_id: str):
        lista = [x for x in cls.load() if str(x.get("id")) != str(item_id)]
        cls.save_all(lista)

class ConfigManager:
    @staticmethod
    def load_api_key() -> str:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                saved = data.get("gemini_api_key", "").strip()
                if saved:
                    return saved
            except Exception:
                pass
        return DEFAULT_GEMINI_API_KEY

    @staticmethod
    def save_api_key(key: str):
        CONFIG_FILE.write_text(json.dumps({"gemini_api_key": key.strip()}), encoding="utf-8")

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

            entradas.insert(0, {
                "fecha": time.strftime("%d/%m/%Y %H:%M"),
                "foto": foto_guardada,
                "diagnosis": diagnosis,
            })
            HISTORY_FILE.write_text(json.dumps(entradas[:cls.MAX_ENTRADAS]), encoding="utf-8")
        except Exception:
            _write_crash_log(traceback.format_exc())

# ---------------------------------------------------------------------------
# Cliente Gemini IA Actualizado
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
        return json.loads(cleaned[start:end + 1])

    @classmethod
    def analyze_image(cls, image_path: str, api_key: str) -> dict:
        import requests

        if not api_key:
            raise cls.GeminiError("Falta la clave API de Gemini. Configúrala en el panel ADMIN.")

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
                "temperature": 0.3,
            },
        }

        last_error = ""
        for model in GEMINI_MODELS:
            url = GEMINI_ENDPOINT.format(model=model, key=api_key)
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    txt = data["candidates"][0]["content"]["parts"][0]["text"]
                    return cls._extract_json(txt)
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_error = str(e)

        raise cls.GeminiError(f"Error al conectar con Gemini: {last_error}")

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
            dias.append({
                "fecha": fechas[i],
                "temp_min": (daily.get("temperature_2m_min") or [None])[i],
                "temp_max": (daily.get("temperature_2m_max") or [None])[i],
                "prob_lluvia": (daily.get("precipitation_probability_max") or [0])[i],
                "viento_max": (daily.get("wind_speed_10m_max") or [0])[i],
                "humedad": (daily.get("relative_humidity_2m_mean") or [0])[i],
            })
        return {"current": current, "dias": dias}

def evaluar_riesgo_climatico(dias):
    riesgos = []
    for dia in dias:
        temp_min = dia.get("temp_min")
        prob_lluvia = dia.get("prob_lluvia") or 0
        viento = dia.get("viento_max") or 0
        humedad = dia.get("humedad") or 0

        if temp_min is not None and temp_min <= 2:
            riesgos.append({
                "fecha": dia["fecha"],
                "tipo": "Alerta de Helada Agrícola",
                "nivel": "alta" if temp_min <= 0 else "media",
                "detalle": f"Temperatura mínima esperada de {temp_min}°C.",
            })
        if prob_lluvia >= 70 and viento >= 30:
            riesgos.append({
                "fecha": dia["fecha"],
                "tipo": "Lluvias y Vientos Fuertes",
                "nivel": "media",
                "detalle": f"{prob_lluvia}% lluvia con ráfagas de {viento} km/h.",
            })
        if humedad >= 80 and temp_min is not None and temp_min >= 10:
            riesgos.append({
                "fecha": dia["fecha"],
                "tipo": "Alto Riesgo de Hongos / Plagas",
                "nivel": "media",
                "detalle": f"Humedad de {humedad}%. Monitorear roya y rancha.",
            })
    return riesgos

# ---------------------------------------------------------------------------
# Logging de Fallos
# ---------------------------------------------------------------------------

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