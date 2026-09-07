[app]

# ---------------------------------------------------------------------------
# Identidad de la app
# ---------------------------------------------------------------------------
title = Agrowillay
package.name = agrowillay
package.domain = org.agrowillay

# Carpeta fuente (donde esta main.py)
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,json,xml

version = 1.0.0

# ---------------------------------------------------------------------------
# Requerimientos de Python
# ---------------------------------------------------------------------------
# NOTA: no incluimos "google-generativeai" porque main.py llama a la API de
# Gemini directamente por REST (con "requests"), lo cual es mucho mas liviano
# y evita dependencias problematicas de compilar para Android (grpc, protobuf).
requirements = python3,kivy==2.3.0,kivymd==1.2.0,requests,pillow,plyer,certifi,urllib3,charset-normalizer,idna,pyjnius

# ---------------------------------------------------------------------------
# Recursos visuales: icono y splash screen
# ---------------------------------------------------------------------------
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png

# Color de fondo del splash mientras carga (formato hex)
android.presplash_color = #0F6B4E

orientation = portrait
fullscreen = 0

# ---------------------------------------------------------------------------
# Permisos de Android
# ---------------------------------------------------------------------------
android.permissions = INTERNET,CAMERA,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# ---------------------------------------------------------------------------
# Configuracion de compilacion Android — VERSIONES ESTABLES
# ---------------------------------------------------------------------------
# IMPORTANTE: android.api=36 + android.ndk=29 + p4a.branch=develop (usados en
# un intento anterior) son combinaciones EXPERIMENTALES que causan fallos
# impredecibles (incluida la descarga del NDK, que a veces ni siquiera esta
# disponible). Volvemos a la combinacion probada y estable que usan la
# mayoria de builds de Kivy/Buildozer en produccion.
android.api = 34
android.minapi = 24
android.ndk = 25b
p4a.branch = v2024.01.21
android.accept_sdk_license = True

# Recipe local para "freetype": el servidor oficial de GNU Savannah lleva
# horas caido (502/504). Esto le dice a Buildozer que use nuestra copia en
# p4a-recipes/freetype/, que descarga el mismo archivo desde SourceForge.
p4a.local_recipes = ./p4a-recipes

# NO fijamos p4a.branch: dejamos que Buildozer use la rama "master" de
# python-for-android, que es la version estable por defecto.

# Arquitecturas: arm64-v8a cubre los celulares modernos (64-bit).
# armeabi-v7a es NECESARIO para gama media/baja y muchos Android 12 que
# todavia traen CPU de 32-bit; sin ella, Android rechaza la instalacion
# con "la aplicacion no es compatible con este dispositivo".
android.archs = arm64-v8a,armeabi-v7a

android.allow_backup = True

# ---------------------------------------------------------------------------
# FileProvider para la camara (arregla el crash "FileUriExposedException"
# al tocar "Tomar foto"). Ver src/android/extra_manifest_application.xml y
# src/android/file_paths.xml — main.py arma la URI content:// usando la
# misma autoridad "<paquete>.fileprovider" declarada aca.
# ---------------------------------------------------------------------------
p4a.hook = src/android/hook.py
android.add_resources = src/android/file_paths.xml:xml/file_paths.xml

# Necesario para "Chrome Custom Tabs": al abrir Google Maps (ubicacion/ayuda
# cercana), esto muestra una flecha "<-" en la parte de arriba de la pagina
# para volver directo a la app, en vez de dejar al usuario sin forma de
# regresar. Tambien trae la libreria androidx.core que usa el FileProvider
# de la camara.
android.enable_androidx = True
android.gradle_dependencies = androidx.browser:browser:1.5.0,androidx.core:core:1.10.1

[buildozer]
log_level = 2
warn_on_root = 0
