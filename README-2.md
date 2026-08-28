# Agrowillay — App Android (Kivy + KivyMD)

Version movil nativa de la web Agrowillay. Diagnostico de plagas en
plantas en 3 pasos: foto -> diagnostico con IA (Gemini) -> ayuda cercana
(viveros/agronomos via Google Maps). Mismo diseno y colores que la web
original.

## ¿Qué se arregló en esta versión?

Las compilaciones anteriores fallaban en el paso 5 porque el
`buildozer.spec` tenía una configuración **experimental**:
`android.api=36`, `android.ndk=29` y `p4a.branch=develop`. Esa
combinación es inestable (rama en desarrollo activo, NDK sin descarga
garantizada) y por eso la compilación se cortaba a mitad de camino con
errores distintos cada vez.

Esta versión:
- Vuelve a `android.api=34`, `android.ndk=25b` y la rama **estable**
  (master) de python-for-android — la combinación más probada para
  Kivy/KivyMD.
- Instala y usa **Python 3.11 en un entorno virtual aislado** durante la
  compilación en Colab, sin depender de qué versión de Python traiga
  Colab por defecto (esto era lo que rompía la compilación de raíz).
- El notebook guarda el log completo en `build_log.txt` y trae una celda
  que filtra automáticamente las líneas con error — ya no hace falta
  buscar manualmente en el log.

## Estructura

```
APKDE-PLAGA/
├── main.py                  # App completa (UI + lógica + llamada a Gemini)
├── buildozer.spec            # Configuración de empaquetado a APK (estable)
├── colab_build_apk.ipynb     # Notebook para compilar en Google Colab
├── icon.png                  # Icono de la app
├── presplash.png             # Pantalla de carga
└── README.md
```

## La clave de Gemini

`main.py` trae una constante `DEFAULT_GEMINI_API_KEY` con un texto de
relleno (`PON_AQUI_TU_CLAVE_NUEVA_DE_GEMINI`). Antes de compilar:

1. Genera una clave nueva en https://aistudio.google.com/apikey
2. Reemplaza el texto de relleno en `main.py` (línea con
   `DEFAULT_GEMINI_API_KEY`) por tu clave real.
3. Sube el archivo actualizado a tu repositorio de GitHub **privado**
   antes de compilar.

Como el repositorio es privado, es seguro dejar la clave escrita
directamente ahí (no queda expuesta públicamente).

## Compilar el APK (Google Colab)

1. Sube estos archivos a la raíz de `Alexander12Po/APKDE-PLAGA`
   (reemplazando los que ya existen).
2. Abre `colab_build_apk.ipynb` en Google Colab.
3. Ejecuta las celdas **en orden, de arriba hacia abajo, sin saltarte
   ninguna**.
4. Si la sesión se desconecta a mitad de camino, vuelve a ejecutar
   todas las celdas desde la primera.
5. Al final, el APK se descarga automáticamente a tu teléfono.

## Si algo falla de todos modos

Ejecuta la celda que dice:
```python
!grep -i -B2 -A5 "error" build_log.txt | tail -80
```
Va a mostrar automáticamente la causa real del fallo. Comparte esa
salida completa para poder diagnosticar el problema puntual.
