# Importamos las bibliotecas necesarias
import pygame
import math
import os
import serial
import json
from serial.tools.list_ports import comports
from pygame.locals import *
import requests
import csv
from datetime import datetime, timedelta, timezone
import time
import sys # Necesario para sys._MEIPASS



# Función para obtener la ruta correcta a los recursos (para PyInstaller)
# ESTA ES LA UBICACIÓN CORRECTA, AL PRINCIPIO
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # sys._MEIPASS no está definido, así que estamos en desarrollo
        base_path = os.path.dirname(os.path.abspath(__file__)) # Usar el directorio del script
    return os.path.join(base_path, relative_path)

import hashlib # Para generar la clave de licencia
import uuid    # Para el identificador de máquina
try:
    import pyperclip # Para la funcionalidad de pegar
    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False
    print("Advertencia: Biblioteca 'pyperclip' no encontrada. La funcionalidad de pegar no estará disponible.")

try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    print("Advertencia: Biblioteca 'tkinter' no encontrada. La selección de archivo de licencia no estará disponible.")

# --- Constantes para la Licencia ---
LICENSE_FILE = resource_path("license.json")
TRIAL_INFO_FILE = resource_path("trial_info.json") # Nuevo archivo para info del periodo de gracia
SECRET_KEY = "my_super_secret_clinometer_key" # ¡¡ESTA CLAVE DEBE SER IDÉNTICA A LA USADA EN EL GENERADOR DE LICENCIAS!!
ACTIVATED_SUCCESSFULLY = False # Variable global para controlar el estado de activación en la sesión actual
PROGRAM_MODE = "LOADING" # Posibles valores: "LOADING", "LICENSED", "GRACE_PERIOD", "TRIAL_EXPIRED", "ACTIVATION_UI_VISIBLE"
CURSOR_BLINK_INTERVAL = 500 # milisegundos, para el cursor en el campo de activación
grace_period_start_time_obj: datetime | None = None # Para almacenar el objeto datetime UTC del inicio del trial


# --- Funciones de Gestión de Trial ---
def load_trial_info() -> dict | None:
    """Carga la información del periodo de gracia desde trial_info.json."""
    if not os.path.exists(TRIAL_INFO_FILE):
        return None
    try:
        with open(TRIAL_INFO_FILE, 'r') as f:
            data = json.load(f)
            return data
    except (IOError, json.JSONDecodeError):
        return None

def save_trial_info(timestamp_utc_str: str):
    """Guarda el timestamp de inicio del periodo de gracia."""
    data = {"grace_period_start_timestamp_utc": timestamp_utc_str}
    try:
        with open(TRIAL_INFO_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except IOError:
        print(f"Error al guardar la información de trial en {TRIAL_INFO_FILE}")

def delete_trial_info():
    """Elimina el archivo trial_info.json si existe."""
    try:
        if os.path.exists(TRIAL_INFO_FILE):
            os.remove(TRIAL_INFO_FILE)
    except OSError as e:
        print(f"Error al eliminar {TRIAL_INFO_FILE}: {e}")

# --- Funciones de Licencia (adaptadas de license_manager.py) ---
def get_machine_specific_identifier() -> tuple[str, str]:
    """
    Genera un identificador de máquina completo (para la lógica de licencia) 
    y un ID corto para mostrar al usuario.
    """
    try:
        mac_num = uuid.getnode()
        if mac_num != uuid.getnode() or mac_num == 0: # Comprobación más robusta
            try:
                import socket
                hostname_hash = hashlib.sha1(socket.gethostname().encode()).hexdigest()
                internal_id_str = hostname_hash
                print("Advertencia: No se pudo obtener la MAC address de forma fiable. Usando ID derivado del hostname.")
            except:
                internal_id_str = str(uuid.uuid4())
                print("Advertencia: No se pudo obtener MAC ni ID de hostname. Usando UUID aleatorio como identificador interno.")
        else:
            internal_id_str = ':'.join(('%012X' % mac_num)[i:i+2] for i in range(0, 12, 2))
    except Exception as e:
        print(f"Error obteniendo MAC/UUID: {e}. Usando UUID aleatorio como identificador interno.")
        internal_id_str = str(uuid.uuid4())

    # display_id = hashlib.sha1(internal_id_str.encode('utf-8')).hexdigest()[:6].upper() # Modificación solicitada
    display_id = internal_id_str # Mostrar el ID completo
    
    return internal_id_str, display_id

def generate_license_key(user_identifier: str) -> str:
    m = hashlib.sha256()
    m.update(user_identifier.encode('utf-8'))
    m.update(SECRET_KEY.encode('utf-8'))
    return m.hexdigest()[:32]

def store_license_data(license_key: str, internal_machine_id: str):
    data = {
        "license_key": license_key,
        "machine_identifier": internal_machine_id 
    }
    try:
        with open(LICENSE_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except IOError:
        print(f"Error al guardar la licencia en {LICENSE_FILE}")

def load_license_data() -> dict | None:
    if not os.path.exists(LICENSE_FILE):
        return None
    try:
        with open(LICENSE_FILE, 'r') as f:
            data = json.load(f)
            return data
    except (IOError, json.JSONDecodeError):
        return None

def verify_license_key(provided_license_key: str, internal_machine_id: str) -> bool:
    expected_license_key = generate_license_key(internal_machine_id) # ya es minúscula
    return provided_license_key.lower() == expected_license_key

def check_license_status() -> bool:
    """
    Comprueba el estado de la licencia almacenada.
    Devuelve True si la licencia es válida, False en caso contrario.
    """
    global ACTIVATED_SUCCESSFULLY
    if ACTIVATED_SUCCESSFULLY:
        return True

    license_data = load_license_data()
    if not license_data:
        return False

    stored_key = license_data.get("license_key")
    stored_identifier = license_data.get("machine_identifier")

    if not stored_key or not stored_identifier:
        return False

    current_internal_id, _ = get_machine_specific_identifier()
    if stored_identifier != current_internal_id:
        print("Advertencia: La licencia almacenada no corresponde a este identificador de máquina.")
        return False

    if verify_license_key(stored_key, stored_identifier):
        ACTIVATED_SUCCESSFULLY = True
        return True
    else:
        print("Advertencia: La clave de licencia almacenada es inválida para el identificador de máquina guardado.")
        return False

def save_id_to_file(display_id: str, filename="machine_id.txt"):
    """Guarda el Display ID en un archivo de texto en el directorio del script."""
    filepath = resource_path(filename) # Guardar junto al ejecutable/script
    try:
        with open(filepath, 'w') as f:
            f.write(f"Su ID de Máquina para la activación es: {display_id}\n")
            f.write("Por favor, proporcione este ID al administrador para obtener su clave de licencia.")
        print(f"ID de Máquina guardado en: {filepath}")
        return True
    except IOError as e:
        print(f"Error al guardar el ID de máquina en archivo: {e}")
        return False

# Definimos colores base
NEGRO = (0, 0, 0)
BLANCO = (255, 255, 255)
VERDE = (0, 255, 0)
ROJO = (255, 0, 0)
AZUL = (0, 0, 255)

# Constantes globales
ALTURA_BARRA_HERRAMIENTAS = 30
IDIOMA = "es"  # Por defecto en español ("es" o "en")

# Diccionarios de textos para multiidioma
TEXTOS = {
    "es": {
        "titulo_ventana": "Lalito",
        "menu_config": "CONFIG. PUERTO",
        "menu_alarma": "CONFIG. ALARMAS",
        "menu_idioma": "IDIOMA",
        "menu_acerca": "ACERCA DE",
        "lat_lon": "LAT / LON",
        "actitud": "CABECEO",
        "rumbo": "RUMBO         ",
        "velocidad": "VELOCIDAD",
        "pitch": "PITCH",
        "roll": "ROLL ",
        "no_datos": "NO HAY DATOS NMEA",
        "desconectado": "Puerto NMEA desconectado",
        "titulo_config": "Configuración Puerto",
        "etiqueta_puerto": "Puerto:",
        "etiqueta_baudios": "Baudios:",
        "boton_guardar": "Guardar",
        "titulo_alarma": "Configuración de Alarmas",
        "pitch_rango": "Pitch (5 a 30):",
        "roll_rango": "Roll (5 a 30):",
        "boton_salir": "Salir",
        "titulo_acerca": "Acerca de Lalito",
        "boton_cerrar": "Cerrar",
        "menu_servicio_datos": "ELEGIR SERVICIO",
        "titulo_servicio_datos": "Configurar Servicio de Datos",
        "etiqueta_servicio": "Servicio:",
        "opcion_thingspeak": "ThingSpeak",
        "opcion_google_cloud": "Google Cloud",
        "etiqueta_apikey_thingspeak": "API Key ThingSpeak:",
        "etiqueta_apikey_google_cloud": "API Key Google Cloud:",
        "titulo_password_servicio": "Ingrese Contraseña",
        "etiqueta_password": "Contraseña:",
        "boton_entrar": "Entrar",
        "password_incorrecta": "Contraseña incorrecta!",
        "menu_activar": "ACTIVAR PRODUCTO",
        "trial_expired_message": "TIEMPO DE PRUEBA EXPIRADO",
        "data_disabled_trial": "Deshabilitado en prueba"
    },
    "en": {
        "titulo_ventana": "Lalito",
        "menu_config": "SERIAL CONFIG",
        "menu_alarma": "ALARM SETTINGS",
        "menu_idioma": "LANGUAGE",
        "menu_acerca": "ABOUT",
        "lat_lon": "LAT / LON",
        "actitud": "ATTITUDE",
        "rumbo": "HEADING",
        "velocidad": "SPEED     ",
        "pitch": "PITCH",
        "roll": "ROLL ",
        "no_datos": "NO NMEA DATA",
        "desconectado": "NMEA port disconnected",
        "titulo_config": "Serial Settings",
        "etiqueta_puerto": "Port:",
        "etiqueta_baudios": "Baud rate:",
        "boton_guardar": "Save",
        "titulo_alarma": "Alarm Settings",
        "pitch_rango": "Pitch (5 to 30):",
        "roll_rango": "Roll (5 to 30):",
        "boton_salir": "Exit",
        "titulo_acerca": "About Lalito",
        "boton_cerrar": "Close",
        "menu_servicio_datos": "CHOOSE SERVICE",
        "titulo_servicio_datos": "Data Service Settings",
        "etiqueta_servicio": "Service:",
        "opcion_thingspeak": "ThingSpeak",
        "opcion_google_cloud": "Google Cloud",
        "etiqueta_apikey_thingspeak": "ThingSpeak API Key:",
        "etiqueta_apikey_google_cloud": "Google Cloud API Key:",
        "titulo_password_servicio": "Enter Password",
        "etiqueta_password": "Password:",
        "boton_entrar": "Enter",
        "password_incorrecta": "Incorrect Password!",
        "menu_activar": "ACTIVATE PRODUCT",
        "trial_expired_message": "TRIAL PERIOD EXPIRED",
        "data_disabled_trial": "Disabled in trial"
    }
}

# Configuración para logging y ThingSpeak
API_KEY_THINGSPEAK = "5TRR6EXF6N5CZF54"
THINGSPEAK_URL = "https://api.thingspeak.com/update"


# Configuración para logging y ThingSpeak
API_KEY_THINGSPEAK = "5TRR6EXF6N5CZF54"
THINGSPEAK_URL = "https://api.thingspeak.com/update"

# Comenta o elimina las siguientes dos líneas:
# CSV_FILENAME = "nmea_log.csv"
# ALARM_LOG_FILENAME = "alarm_log.csv" 

# Añade estas nuevas definiciones:
CSV_FILENAME = resource_path("nmea_log.csv")
ALARM_LOG_FILENAME = resource_path("alarm_log.csv")

INTERVALO_ENVIO_DATOS_S = 15 
# ... (el resto de constantes sigue igual)





INTERVALO_ENVIO_DATOS_S = 15
INTERVALO_REPETICION_ALARMA_ROLL_S = 5
INTERVALO_REPETICION_ALARMA_PITCH_S = 5

ARCHIVO_CONFIG_SERIAL = "config_serial.json"
ARCHIVO_CONFIG_ALARMA = "config_alarma.json"

# Variables globales
valores_alarma = {
    "max_pitch_pos": "15", 
    "min_pitch_neg": "-15",
    "max_roll_pos": "15",
    "min_roll_neg": "-15"
}

valores_ui_input_alarma = {"pitch": "15", "roll": "15"}
lista_puertos_detectados = []
ultimo_intento_reconeccion_tiempo = 0 # Inicializada globalmente
INTERVALO_RECONECCION_MS = 5000
ultima_vez_datos_recibidos = 0 # Se inicializará después de pygame.init() o globalmente
UMBRAL_SIN_DATOS_MS = 3000

# Variables para datos de ThingSpeak
ts_pitch_float = 0.0
ts_roll_float = 0.0
ts_lat_decimal = 0.0
ts_lon_decimal = 0.0
ts_speed_float = 0.0
ts_heading_float = 0.0
ts_timestamp_str = "N/A"
ultima_vez_envio_datos = 0

# Variables de estado de alarmas
alarma_roll_babor_activa = False
alarma_roll_estribor_activa = False
ultima_reproduccion_alarma_babor_tiempo = 0.0
ultima_reproduccion_alarma_estribor_tiempo = 0.0
alarma_pitch_sentado_activa = False
alarma_pitch_encabuzado_activa = False
ultima_reproduccion_alarma_sentado_tiempo = 0.0
ultima_reproduccion_alarma_encabuzado_tiempo = 0.0

# Variables para control de sonidos
sonido_alarma_actualmente_reproduciendo = None
tiempo_ultimo_sonido_iniciado = 0.0
INDICE_PROXIMA_ALARMA_A_SONAR = 0
PAUSA_ENTRE_SONIDOS_ALTERNADOS_S = 1.0

# Variables para selección de servicio de datos y API Keys
SERVICIO_DATOS_ACTUAL = "thingspeak"  # Por defecto "thingspeak" o "google_cloud"
API_KEY_GOOGLE_CLOUD = ""  # Para almacenar la API Key de Google Cloud
# Variables para los campos de texto en la ventana de configuración del servicio
input_api_key_thingspeak_str = API_KEY_THINGSPEAK # Se inicializará desde config o default
input_api_key_google_cloud_str = "" # Se inicializará desde config o default

# Variables para la ventana de contraseña del servicio de datos
CLAVE_ACCESO_SERVICIO = "29121975"
mostrar_ventana_password_servicio = False
input_password_str = ""
intento_password_fallido = False

# Inicialización de Pygame
pygame.init()
pygame.mixer.init()
# script_dir = os.path.dirname(os.path.abspath(__file__)) # Comentado, resource_path se usa para assets

# Cargar sonidos de alarma según idioma
try:
    # Sonidos en español
    sonidos_es = {
        'babor': pygame.mixer.Sound(resource_path("alarma_babor.mp3")),
        'estribor': pygame.mixer.Sound(resource_path("alarma_estribor.mp3")),
        'sentado': pygame.mixer.Sound(resource_path("alarma_sentado.mp3")),
        'encabuzado': pygame.mixer.Sound(resource_path("alarma_encabuzado.mp3"))
    }
    
    # Sonidos en inglés (equivalentes)
    sonidos_en = {
        'babor': pygame.mixer.Sound(resource_path("port_alarm.mp3")),
        'estribor': pygame.mixer.Sound(resource_path("starboard_alarm.mp3")),
        'sentado': pygame.mixer.Sound(resource_path("stern_alarm.mp3")),
        'encabuzado': pygame.mixer.Sound(resource_path("head_alarm.mp3"))
    }
    
    # Ajustar volumen de los sonidos
    for sonido in sonidos_es.values():
        if sonido:
            sonido.set_volume(0.7)
    for sonido in sonidos_en.values():
        if sonido:
            sonido.set_volume(0.7)
            
except pygame.error as e:
    print(f"Error al cargar archivos de sonido: {e}")
    sonidos_es = {'babor': None, 'estribor': None, 'sentado': None, 'encabuzado': None}
    sonidos_en = {'babor': None, 'estribor': None, 'sentado': None, 'encabuzado': None}

# Función para reproducir alarmas según idioma
def reproducir_alarma(tipo_alarma):
    """Reproduce la alarma correspondiente según el idioma actual"""
    global sonido_alarma_actualmente_reproduciendo, tiempo_ultimo_sonido_iniciado
    
    # Detener alarma actual si está sonando
    if sonido_alarma_actualmente_reproduciendo:
        sonido_alarma_actualmente_reproduciendo.stop()
    
    # Seleccionar diccionario de sonidos según idioma
    sonidos = sonidos_en if IDIOMA == "en" else sonidos_es
    
    # Mapeo de tipos de alarma
    mapeo_alarmas = {
        'roll_babor': 'babor',
        'roll_estribor': 'estribor',
        'pitch_sentado': 'sentado',
        'pitch_encabuzado': 'encabuzado'
    }
    
    # Obtener y reproducir el sonido adecuado
    clave_sonido = mapeo_alarmas.get(tipo_alarma)
    if clave_sonido:
        sonido = sonidos.get(clave_sonido)
        if sonido:
            sonido.play()
            sonido_alarma_actualmente_reproduciendo = sonido
            tiempo_ultimo_sonido_iniciado = time.time()
            return True
    return False

# Funciones de configuración
def cargar_configuracion_serial():
    global IDIOMA, SERVICIO_DATOS_ACTUAL, API_KEY_THINGSPEAK, API_KEY_GOOGLE_CLOUD
    global input_api_key_thingspeak_str, input_api_key_google_cloud_str
    try:
        with open(ARCHIVO_CONFIG_SERIAL, 'r') as f:
            config = json.load(f)
        IDIOMA = config.get('idioma', 'es')
        
        # Cargar configuración del servicio de datos
        SERVICIO_DATOS_ACTUAL = config.get('servicio_datos', 'thingspeak')
        API_KEY_THINGSPEAK = config.get('api_key_thingspeak', API_KEY_THINGSPEAK) # Usa el global actual como fallback si no está en el archivo
        API_KEY_GOOGLE_CLOUD = config.get('api_key_google_cloud', '')
        
        # Inicializar los strings de input para la UI
        input_api_key_thingspeak_str = API_KEY_THINGSPEAK
        input_api_key_google_cloud_str = API_KEY_GOOGLE_CLOUD
        
        return config.get('puerto', 'COM9'), int(config.get('baudios', 9600))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        IDIOMA = 'es'
        SERVICIO_DATOS_ACTUAL = 'thingspeak'
        # API_KEY_THINGSPEAK ya tiene un valor global por defecto
        input_api_key_thingspeak_str = API_KEY_THINGSPEAK
        API_KEY_GOOGLE_CLOUD = ''
        input_api_key_google_cloud_str = ''
        return 'COM9', 9600

def guardar_configuracion_serial(puerto, baudios):
    # Los valores de SERVICIO_DATOS_ACTUAL, input_api_key_thingspeak_str, 
    # y input_api_key_google_cloud_str se actualizan directamente 
    # desde la UI antes de llamar a esta función.
    config = {
        'puerto': puerto, 
        'baudios': int(baudios),
        'idioma': IDIOMA,
        'servicio_datos': SERVICIO_DATOS_ACTUAL,
        'api_key_thingspeak': input_api_key_thingspeak_str, # Guardar el valor del input
        'api_key_google_cloud': input_api_key_google_cloud_str # Guardar el valor del input
    }
    try:
        with open(ARCHIVO_CONFIG_SERIAL, 'w') as f: 
            json.dump(config, f, indent=4)
        return True
    except IOError as e:
        print(f"ERROR: No se pudo guardar la configuración: {e}")
        return False

def cargar_configuracion_alarma():
    global valores_alarma, valores_ui_input_alarma
    try:
        with open(ARCHIVO_CONFIG_ALARMA, 'r') as f: 
            config = json.load(f)
        valores_alarma["max_pitch_pos"] = str(config.get('max_pitch_pos', "15"))
        valores_alarma["min_pitch_neg"] = str(config.get('min_pitch_neg', "-15"))
        valores_alarma["max_roll_pos"] = str(config.get('max_roll_pos', "15"))
        valores_alarma["min_roll_neg"] = str(config.get('min_roll_neg', "-15"))
        
        valores_ui_input_alarma["pitch"] = str(abs(int(float(valores_alarma["max_pitch_pos"]))))
        valores_ui_input_alarma["roll"] = str(abs(int(float(valores_alarma["max_roll_pos"]))))
    except: 
        valores_alarma = {"max_pitch_pos": "15", "min_pitch_neg": "-15", "max_roll_pos": "15", "min_roll_neg": "-15"}
        valores_ui_input_alarma = {"pitch": "15", "roll": "15"}

def guardar_configuracion_alarma(): 
    global valores_alarma, valores_ui_input_alarma
    try:
        pitch_val_ui = int(valores_ui_input_alarma["pitch"])
        roll_val_ui = int(valores_ui_input_alarma["roll"])
        if not (5 <= pitch_val_ui <= 30): pitch_val_ui = 15 
        if not (5 <= roll_val_ui <= 30): roll_val_ui = 15 
        
        valores_ui_input_alarma["pitch"] = str(pitch_val_ui)
        valores_ui_input_alarma["roll"] = str(roll_val_ui)
        valores_alarma["max_pitch_pos"] = str(pitch_val_ui)
        valores_alarma["min_pitch_neg"] = str(-pitch_val_ui)
        valores_alarma["max_roll_pos"] = str(roll_val_ui)
        valores_alarma["min_roll_neg"] = str(-roll_val_ui)
        
        with open(ARCHIVO_CONFIG_ALARMA, 'w') as f: 
            json.dump(valores_alarma, f, indent=4) 
        return True
    except (IOError, ValueError) as e:
        print(f"Error al guardar configuración de alarma: {e}")
        return False

# Funciones de parseo NMEA
def parse_pfec_gpatt(sentence):
    global att_heading_str, att_pitch_str, att_roll_str, ultima_vez_datos_recibidos, ts_pitch_float, ts_roll_float
    try:
        parts = sentence.split(',')
        if len(parts) >= 5 and parts[1] == "GPatt":
            att_heading_str = parts[2] if parts[2] else "N/A"
            raw_pitch = parts[3]
            att_pitch_str = raw_pitch if raw_pitch else "N/A"
            try: 
                ts_pitch_float = float(raw_pitch)
            except: 
                ts_pitch_float = 0.0 
            raw_roll_part = parts[4].split('*')[0]
            att_roll_str = raw_roll_part if raw_roll_part else "N/A"
            try: 
                ts_roll_float = float(raw_roll_part)
            except: 
                ts_roll_float = 0.0
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
    except: 
        pass

def convertir_coord(coord_str, direccion, is_longitude=False):
    try:
        idx_punto = coord_str.find('.')
        if idx_punto == -1 or not coord_str or not direccion: 
            return 0.0 
        min_start_index = idx_punto - 2
        if min_start_index < 0: 
            return 0.0
        grados_str = coord_str[:min_start_index]
        minutos_str = coord_str[min_start_index:] 
        if not grados_str: 
            return 0.0
        grados = int(grados_str)
        minutos = float(minutos_str)
        decimal = grados + minutos / 60.0
        if direccion in ['S', 'W']: 
            decimal *= -1
        return round(decimal, 6)
    except: 
        return 0.0 
    


def parse_gll(sentence):
    global ultima_vez_datos_recibidos
    try:
        # Verificar que es una sentencia GLL válida
        if not sentence.startswith('$GPGLL'):
            return
            
        parts = sentence.split(',')
        
        # Validación más robusta (6 campos mínimos + checksum)
        if len(parts) < 7:
            return
            
        # Verificar que los datos son válidos (campo 6 == 'A')
        if parts[6] != 'A' or not parts[1] or not parts[3]:
            return
            
        lat_raw_val = parts[1]  # ¡OJO! El índice era incorrecto en tu código
        lat_dir = parts[2]      # parts[2] es la dirección, no parts[3]
        lon_raw_val = parts[3]  # parts[3] es longitud, no parts[4]
        lon_dir = parts[4]      # parts[4] es dirección, no parts[5]
        
        global latitude_str, longitude_str, ts_lat_decimal, ts_lon_decimal
        latitude_str_temp, longitude_str_temp = "N/A", "N/A"
        ts_lat_decimal_temp, ts_lon_decimal_temp = 0.0, 0.0
        
        # Procesamiento de latitud (igual que antes)
        if lat_raw_val and lat_dir and len(lat_raw_val) >= 2:
            lat_deg_ui = lat_raw_val[:2]
            lat_min_full_ui = lat_raw_val[2:]
            try: 
                lat_min_formatted_ui = f"{float(lat_min_full_ui):.3f}"
            except: 
                lat_min_formatted_ui = lat_min_full_ui 
            latitude_str_temp = f"{lat_deg_ui}° {lat_min_formatted_ui}' {lat_dir}"
            ts_lat_decimal_temp = convertir_coord(lat_raw_val, lat_dir, is_longitude=False)
        
        # Procesamiento de longitud (igual que antes)
        if lon_raw_val and lon_dir and len(lon_raw_val) >= 3:
            lon_parts_ui = lon_raw_val.split('.')[0]
            deg_chars = 0
            if len(lon_parts_ui) >= 5: 
                deg_chars = 3 
            elif len(lon_parts_ui) >= 4: 
                deg_chars = 2 
            elif len(lon_parts_ui) >= 3: 
                deg_chars = 1 
            if deg_chars > 0:
                lon_deg_ui = lon_raw_val[:deg_chars]
                lon_min_full_ui = lon_raw_val[deg_chars:]
                try: 
                    lon_min_formatted_ui = f"{float(lon_min_full_ui):.3f}"
                except: 
                    lon_min_formatted_ui = lon_min_full_ui
                longitude_str_temp = f"{lon_deg_ui}° {lon_min_formatted_ui}' {lon_dir}"
                ts_lon_decimal_temp = convertir_coord(lon_raw_val, lon_dir, is_longitude=True)
        
        latitude_str, longitude_str = latitude_str_temp, longitude_str_temp
        ts_lat_decimal, ts_lon_decimal = ts_lat_decimal_temp, ts_lon_decimal_temp
        ultima_vez_datos_recibidos = pygame.time.get_ticks()
        
    except: 
        pass

    

def parse_gga(sentence):
    global ultima_vez_datos_recibidos
    try:
        parts = sentence.split(',')
        if len(parts) > 5 and parts[2] and parts[3] and parts[4] and parts[5]:
            lat_raw_val = parts[2]
            lat_dir = parts[3]
            lon_raw_val = parts[4]
            lon_dir = parts[5]
            
            global latitude_str, longitude_str, ts_lat_decimal, ts_lon_decimal
            latitude_str_temp, longitude_str_temp = "N/A", "N/A"
            ts_lat_decimal_temp, ts_lon_decimal_temp = 0.0, 0.0
            
            if lat_raw_val and lat_dir and len(lat_raw_val) >=2:
                lat_deg_ui = lat_raw_val[:2]
                lat_min_full_ui = lat_raw_val[2:]
                try: 
                    lat_min_formatted_ui = f"{float(lat_min_full_ui):.3f}"
                except: 
                    lat_min_formatted_ui = lat_min_full_ui 
                latitude_str_temp = f"{lat_deg_ui}° {lat_min_formatted_ui}' {lat_dir}"
                ts_lat_decimal_temp = convertir_coord(lat_raw_val, lat_dir, is_longitude=False)
            
            if lon_raw_val and lon_dir and len(lon_raw_val) >=3:
                lon_parts_ui = lon_raw_val.split('.')[0]
                deg_chars = 0
                if len(lon_parts_ui) >= 5: 
                    deg_chars = 3 
                elif len(lon_parts_ui) >= 4: 
                    deg_chars = 2 
                elif len(lon_parts_ui) >= 3: 
                    deg_chars = 1 
                if deg_chars > 0:
                    lon_deg_ui = lon_raw_val[:deg_chars]
                    lon_min_full_ui = lon_raw_val[deg_chars:]
                    try: 
                        lon_min_formatted_ui = f"{float(lon_min_full_ui):.3f}"
                    except: 
                        lon_min_formatted_ui = lon_min_full_ui
                    longitude_str_temp = f"{lon_deg_ui}° {lon_min_formatted_ui}' {lon_dir}"
                    ts_lon_decimal_temp = convertir_coord(lon_raw_val, lon_dir, is_longitude=True)
            
            latitude_str, longitude_str = latitude_str_temp, longitude_str_temp
            ts_lat_decimal, ts_lon_decimal = ts_lat_decimal_temp, ts_lon_decimal_temp
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
    except: 
        pass

def parse_rmc(sentence):
    global ultima_vez_datos_recibidos
    try:
        parts = sentence.split(',')
        if len(parts) > 6 and parts[3] and parts[4] and parts[5] and parts[6]: 
            lat_raw_val = parts[3]
            lat_dir = parts[4]
            lon_raw_val = parts[5]
            lon_dir = parts[6]
            
            global latitude_str, longitude_str, ts_lat_decimal, ts_lon_decimal
            latitude_str_temp, longitude_str_temp = "N/A", "N/A"
            ts_lat_decimal_temp, ts_lon_decimal_temp = 0.0, 0.0
            
            if lat_raw_val and lat_dir and len(lat_raw_val) >=2:
                lat_deg_ui = lat_raw_val[:2]
                lat_min_full_ui = lat_raw_val[2:]
                try: 
                    lat_min_formatted_ui = f"{float(lat_min_full_ui):.3f}"
                except: 
                    lat_min_formatted_ui = lat_min_full_ui 
                latitude_str_temp = f"{lat_deg_ui}° {lat_min_formatted_ui}' {lat_dir}"
                ts_lat_decimal_temp = convertir_coord(lat_raw_val, lat_dir, is_longitude=False)
            
            if lon_raw_val and lon_dir and len(lon_raw_val) >=3:
                lon_parts_ui = lon_raw_val.split('.')[0]
                deg_chars = 0
                if len(lon_parts_ui) >= 5: 
                    deg_chars = 3 
                elif len(lon_parts_ui) >= 4: 
                    deg_chars = 2 
                elif len(lon_parts_ui) >= 3: 
                    deg_chars = 1 
                if deg_chars > 0:
                    lon_deg_ui = lon_raw_val[:deg_chars]
                    lon_min_full_ui = lon_raw_val[deg_chars:]
                    try: 
                        lon_min_formatted_ui = f"{float(lon_min_full_ui):.3f}"
                    except: 
                        lon_min_formatted_ui = lon_min_full_ui
                    longitude_str_temp = f"{lon_deg_ui}° {lon_min_formatted_ui}' {lon_dir}"
                    ts_lon_decimal_temp = convertir_coord(lon_raw_val, lon_dir, is_longitude=True)
            
            latitude_str, longitude_str = latitude_str_temp, longitude_str_temp
            ts_lat_decimal, ts_lon_decimal = ts_lat_decimal_temp, ts_lon_decimal_temp
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
    except: 
        pass

def parse_vtg(sentence):
    global speed_str, ultima_vez_datos_recibidos, ts_speed_float
    try: 
        parts = sentence.split(',')
        speed_val_str_ui = "N/A"
        temp_speed_float = 0.0
        if len(parts) > 5 and parts[5]: 
            speed_val_str_ui = parts[5] 
            try: 
                temp_speed_float = float(speed_val_str_ui)
            except: 
                temp_speed_float = 0.0
        elif len(parts) > 7 and parts[7]: 
            speed_kmh_str = parts[7]
            try: 
                speed_kmh = float(speed_kmh_str)
                temp_speed_float = round(speed_kmh / 1.852, 1)
                speed_val_str_ui = str(temp_speed_float)
            except: 
                pass
        if speed_val_str_ui != "N/A": 
            speed_str = f"{speed_val_str_ui} Kt"
            ts_speed_float = temp_speed_float
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
        else: 
            speed_str = "N/A Kt"
            ts_speed_float = 0.0
    except: 
        pass

def parse_hdt(sentence):
    global heading_str, ultima_vez_datos_recibidos, ts_heading_float
    try: 
        parts = sentence.split(',') 
        if len(parts) > 1 and parts[1]: 
            heading_val_str = parts[1]
            try: 
                heading_val_float = float(heading_val_str)
                heading_str = f"{heading_val_float:.0f}°"
                ts_heading_float = heading_val_float      
            except: 
                heading_str = f"{heading_val_str}°"
                ts_heading_float = 0.0 
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
    except: 
        pass

def parse_hdg(sentence):
    global heading_str, ultima_vez_datos_recibidos, ts_heading_float
    try: 
        parts = sentence.split(',') 
        if len(parts) > 1 and parts[1]: 
            heading_val_str = parts[1] 
            try: 
                heading_val_float = float(heading_val_str)
                heading_str = f"{heading_val_float:.0f}°"
                ts_heading_float = heading_val_float      
            except: 
                heading_str = f"{heading_val_str}°"
                ts_heading_float = 0.0
            ultima_vez_datos_recibidos = pygame.time.get_ticks()
    except: 
        pass

def parse_gpzda(sentence):
    global ts_timestamp_str 
    try:
        parts = sentence.split(',')
        if len(parts) >= 5: 
            time_utc_str = parts[1]
            day_str = parts[2]
            month_str = parts[3]
            year_str = parts[4]
            if '.' in time_utc_str: 
                time_utc_str = time_utc_str.split('.')[0]
            if len(time_utc_str) == 6 and day_str and month_str and year_str and len(year_str) == 4:
                h = time_utc_str[0:2]
                m = time_utc_str[2:4]
                s = time_utc_str[4:6]
                ts_timestamp_str = f"{year_str}-{month_str.zfill(2)}-{day_str.zfill(2)} {h}:{m}:{s}"
    except: 
        pass

def reset_ui_data():
    global latitude_str, longitude_str, speed_str, heading_str
    global att_pitch_str, att_roll_str, att_heading_str
    global ts_pitch_float, ts_roll_float, ts_lat_decimal, ts_lon_decimal
    global ts_speed_float, ts_heading_float, ts_timestamp_str
    global alarma_roll_babor_activa, alarma_roll_estribor_activa
    global alarma_pitch_sentado_activa, alarma_pitch_encabuzado_activa
    global ultima_reproduccion_alarma_babor_tiempo, ultima_reproduccion_alarma_estribor_tiempo
    global ultima_reproduccion_alarma_sentado_tiempo, ultima_reproduccion_alarma_encabuzado_tiempo
    global sonido_alarma_actualmente_reproduciendo, INDICE_PROXIMA_ALARMA_A_SONAR

    latitude_str = "N/A"
    longitude_str = "N/A"
    speed_str = "N/A Kt" 
    heading_str = "N/A°" 
    att_pitch_str = "N/A"
    att_roll_str = "N/A"
    att_heading_str = "N/A"
    
    ts_pitch_float = 0.0
    ts_roll_float = 0.0
    ts_lat_decimal = 0.0
    ts_lon_decimal = 0.0
    ts_speed_float = 0.0
    ts_heading_float = 0.0
    ts_timestamp_str = "N/A"

    # Resetear estados de alarma
    alarma_roll_babor_activa = False
    alarma_roll_estribor_activa = False
    alarma_pitch_sentado_activa = False
    alarma_pitch_encabuzado_activa = False

    # Resetear temporizadores de repetición de alarma
    ultima_reproduccion_alarma_babor_tiempo = 0.0
    ultima_reproduccion_alarma_estribor_tiempo = 0.0
    ultima_reproduccion_alarma_sentado_tiempo = 0.0
    ultima_reproduccion_alarma_encabuzado_tiempo = 0.0

    # Detener sonido de alarma actual y resetear gestor de sonido
    if sonido_alarma_actualmente_reproduciendo is not None:
        sonido_alarma_actualmente_reproduciendo.stop()
        sonido_alarma_actualmente_reproduciendo = None
    INDICE_PROXIMA_ALARMA_A_SONAR = 0

def init_csv():
    print(f"DEBUG: init_csv - Intentando inicializar/verificar: {CSV_FILENAME}")
    try:
        file_exists = os.path.exists(CSV_FILENAME)
        is_empty = False
        if file_exists:
            is_empty = os.path.getsize(CSV_FILENAME) == 0

        with open(CSV_FILENAME, 'a', newline='') as f: 
            if not file_exists or is_empty:
                writer = csv.writer(f)
                writer.writerow(["FechaHora", "Pitch", "Roll", "Latitud", "Longitud", "Velocidad", "Rumbo"])
                print(f"DEBUG: init_csv - Cabecera escrita en: {CSV_FILENAME}")
            else:
                print(f"DEBUG: init_csv - Archivo ya existe y no está vacío: {CSV_FILENAME}")
        print(f"DEBUG: init_csv - Finalizado para: {CSV_FILENAME}")
    except Exception as e:
        print(f"[ERROR] En init_csv para {CSV_FILENAME}: {e}")



def init_alarm_csv():
    print(f"DEBUG: init_alarm_csv - Intentando inicializar/verificar: {ALARM_LOG_FILENAME}")
    try:
        file_exists_before_open = os.path.exists(ALARM_LOG_FILENAME)
        is_empty = False
        if file_exists_before_open:
            is_empty = os.path.getsize(ALARM_LOG_FILENAME) == 0
        
        with open(ALARM_LOG_FILENAME, 'a', newline='') as f:
            if not file_exists_before_open or is_empty:
                writer = csv.writer(f)
                writer.writerow(["TimestampUTC", "TipoAlarma", "EstadoAlarma", "ValorActual", "UmbralConfigurado"])
                print(f"DEBUG: init_alarm_csv - Cabecera escrita en: {ALARM_LOG_FILENAME}")
            else:
                print(f"DEBUG: init_alarm_csv - Archivo ya existe y no está vacío: {ALARM_LOG_FILENAME}")
        print(f"DEBUG: init_alarm_csv - Finalizado para: {ALARM_LOG_FILENAME}")

    except FileExistsError: 
        print(f"DEBUG: init_alarm_csv - Archivo ya existe (manejado por FileExistsError): {ALARM_LOG_FILENAME}")
        pass 
    except Exception as e:
        print(f"[ERROR] En init_alarm_csv para {ALARM_LOG_FILENAME}: {e}")


def guardar_alarma_csv(timestamp, tipo_alarma, estado_alarma, valor_actual, umbral_configurado):
    print(f"DEBUG: guardar_alarma_csv - Intentando escribir en: {ALARM_LOG_FILENAME}")
    print(f"DEBUG: Datos a guardar: TS={timestamp}, Tipo={tipo_alarma}, Estado={estado_alarma}, Val={valor_actual}, Umbral={umbral_configurado}")
    try:
        with open(ALARM_LOG_FILENAME, 'a', newline='') as f: 
            writer = csv.writer(f)
            writer.writerow([timestamp, tipo_alarma, estado_alarma, valor_actual, umbral_configurado])
        print(f"DEBUG: guardar_alarma_csv - Escritura exitosa en: {ALARM_LOG_FILENAME}")
    except Exception as e:
        print(f"[ERROR] No se pudo escribir en {ALARM_LOG_FILENAME}: {e}")






def guardar_csv():
    try:
        with open(CSV_FILENAME, 'a', newline='') as f: 
            writer = csv.writer(f)
            writer.writerow([
                ts_timestamp_str, 
                ts_pitch_float, 
                ts_roll_float, 
                ts_lat_decimal, 
                ts_lon_decimal, 
                ts_speed_float, 
                ts_heading_float
            ])
    except Exception as e:
        print(f"[ERROR] No se pudo escribir en {CSV_FILENAME} (guardar_csv): {e}")



def enviar_thingspeak():
    global PROGRAM_MODE # Necesitamos acceder al estado global
    if PROGRAM_MODE == "TRIAL_EXPIRED":
        print("INFO: Modo trial expirado. Envío a ThingSpeak deshabilitado.")
        return

    payload = {
        'api_key': API_KEY_THINGSPEAK, 
        'field1': ts_pitch_float, 
        'field2': ts_roll_float, 
        'field3': ts_lat_decimal, 
        'field4': ts_lon_decimal, 
        'field5': ts_speed_float, 
        'field6': ts_heading_float, 
        'field7': ts_timestamp_str
    }
    
    estado_alarma = "SIN ALARMA"
    if alarma_roll_babor_activa:
        estado_alarma = "ALARMA BABOR"
    elif alarma_roll_estribor_activa:
        estado_alarma = "ALARMA ESTRIBOR"
    if alarma_pitch_sentado_activa:
        estado_alarma += " Y SENTADO" if "ALARMA" in estado_alarma else "ALARMA SENTADO"
    elif alarma_pitch_encabuzado_activa:
        estado_alarma += " Y ENCABUZADO" if "ALARMA" in estado_alarma else "ALARMA ENCABUZADO"
    
    payload['field8'] = estado_alarma
    
    try:
        r = requests.get(THINGSPEAK_URL, params=payload) 
        if r.status_code == 200: 
            print(f"[OK] Datos enviados a ThingSpeak: {ts_timestamp_str}")
        else: 
            print(f"[ERROR] Respuesta ThingSpeak: {r.status_code} - {r.text}")
    except Exception as e: 
        print(f"[ERROR] Conexión ThingSpeak: {e}")

def draw_activation_window(screen, display_id_str, input_key_str, error_message=None, input_active: bool = False, show_cursor: bool = False):
    """Dibuja la ventana de activación de licencia."""
    font_titulo = pygame.font.Font(None, 36)
    font_texto = pygame.font.Font(None, 28)
    font_error = pygame.font.Font(None, 24)
    font_boton_med = pygame.font.Font(None, 26) # Fuente para botones un poco más anchos
    font_boton_pequeno = pygame.font.Font(None, 22) # Fuente para botones más pequeños si el texto es largo

    ventana_width = 500
    ventana_height = 380 # Aumentada para nueva disposición y mensaje de error
    ventana_x = (screen.get_width() - ventana_width) // 2
    ventana_y = (screen.get_height() - ventana_height) // 2
    rect_ventana = pygame.Rect(ventana_x, ventana_y, ventana_width, ventana_height)

    # Colores
    color_fondo_ventana = (220, 220, 220)
    color_borde_ventana = (100, 100, 100)
    color_texto = (0, 0, 0)
    color_input_fondo = (255, 255, 255)
    color_input_borde = (50, 50, 50)
    color_boton_fondo = (180, 180, 180)
    color_boton_texto = (0, 0, 0)
    color_error_texto = (200, 0, 0)

    # Dibujar ventana
    pygame.draw.rect(screen, color_fondo_ventana, rect_ventana)
    pygame.draw.rect(screen, color_borde_ventana, rect_ventana, 2)

    # Título
    titulo_surf = font_titulo.render("Activación Requerida", True, color_texto)
    screen.blit(titulo_surf, (rect_ventana.centerx - titulo_surf.get_width() // 2, rect_ventana.top + 20))

    # Mostrar ID de Máquina (Display ID)
    id_label_surf = font_texto.render("Su ID de Máquina:", True, color_texto)
    screen.blit(id_label_surf, (rect_ventana.left + 30, rect_ventana.top + 70))
    id_value_surf = font_texto.render(display_id_str, True, color_texto)
    screen.blit(id_value_surf, (rect_ventana.left + 30 + id_label_surf.get_width() + 10, rect_ventana.top + 70))

    # Posición del ID de máquina (para referencia)
    id_text_y_pos = rect_ventana.top + 70
    id_value_rect = id_value_surf.get_rect(top=id_text_y_pos) # Solo para obtener altura y bottom

    # Botón "Copiar ID" - Centrado debajo del ID de máquina
    button_copiar_id_w = 120 # Ancho ajustado
    button_copiar_id_h = 30
    y_copiar_id = id_text_y_pos + id_value_rect.height + 10 # 10px debajo del texto del ID
    rect_boton_copiar_id = pygame.Rect(0, 0, button_copiar_id_w, button_copiar_id_h)
    rect_boton_copiar_id.centerx = rect_ventana.centerx
    rect_boton_copiar_id.top = y_copiar_id
    
    pygame.draw.rect(screen, color_boton_fondo, rect_boton_copiar_id)
    pygame.draw.rect(screen, color_input_borde, rect_boton_copiar_id, 1)
    copiar_id_text_surf = font_boton_pequeno.render("Copiar ID", True, color_boton_texto)
    screen.blit(copiar_id_text_surf, copiar_id_text_surf.get_rect(center=rect_boton_copiar_id.center))

    # Etiqueta para Clave de Licencia (debajo de Copiar ID)
    y_label_clave = rect_boton_copiar_id.bottom + 15
    key_label_surf = font_texto.render("Clave de Licencia:", True, color_texto)
    screen.blit(key_label_surf, (rect_ventana.left + 30, y_label_clave))

    # Campo de entrada para Clave de Licencia
    y_input_clave = y_label_clave + key_label_surf.get_height() + 5
    rect_input_key = pygame.Rect(rect_ventana.left + 30, y_input_clave, ventana_width - 60, 35)
    pygame.draw.rect(screen, color_input_fondo, rect_input_key)
    pygame.draw.rect(screen, color_input_borde, rect_input_key, 1)
    input_key_surf = font_texto.render(input_key_str, True, color_texto)
    screen.blit(input_key_surf, (rect_input_key.left + 5, rect_input_key.centery - input_key_surf.get_height() // 2))

    # Mensaje de error/feedback (debajo del input de clave)
    y_despues_del_mensaje = rect_input_key.bottom + 8 # Posición Y base después del input key + padding para mensaje
    if error_message:
        error_surf = font_error.render(error_message, True, color_error_texto)
        screen.blit(error_surf, (rect_ventana.centerx - error_surf.get_width() // 2, y_despues_del_mensaje))
        y_inicio_area_botones = y_despues_del_mensaje + error_surf.get_height() + 15 # 15px padding después del mensaje
    else:
        y_inicio_area_botones = y_despues_del_mensaje + 15 # 15px padding incluso si no hay mensaje (o ajustar si se quiere más pegado)

    # --- Nueva Disposición de Botones ---
    button_height = 40
    espacio_vertical_entre_filas = 15
    espacio_entre_botones_horizontal = 20 # Espacio entre botones en la misma fila
    
    # Calcular la altura total del bloque de los 4 botones (2 filas)
    altura_total_bloque_botones = (2 * button_height) + espacio_vertical_entre_filas

    # Determinar el espacio vertical disponible para centrar los botones
    padding_inferior_ventana = 20 # Espacio deseado en la parte inferior de la ventana
    espacio_vertical_disponible_para_bloque = rect_ventana.bottom - y_inicio_area_botones - padding_inferior_ventana
    
    # Calcular la coordenada Y para la primera fila de botones para centrar el bloque
    offset_y_bloque_botones = 0
    if espacio_vertical_disponible_para_bloque > altura_total_bloque_botones:
        offset_y_bloque_botones = (espacio_vertical_disponible_para_bloque - altura_total_bloque_botones) / 2
    
    y_botones_fila1 = y_inicio_area_botones + offset_y_bloque_botones
    
    # El desplazamiento vertical adicional de 10px ha sido eliminado.
    # y_botones_fila1 += 10 # Eliminado

    # Fila 1: "Usar Archivo Lic." e "Guardar ID"
    # Anchos definidos
    w_usar_lic = 190 # "Usar Archivo Lic."
    w_guardar_id = 160 # "Guardar ID"
    
    # Cálculo para centrar los dos botones con espacio entre ellos
    ancho_total_fila1 = w_usar_lic + espacio_entre_botones_horizontal + w_guardar_id
    x_inicio_fila1 = rect_ventana.left + (ventana_width - ancho_total_fila1) // 2 # Corregido: relativo a rect_ventana.left
    
    rect_boton_usar_archivo = pygame.Rect(x_inicio_fila1, y_botones_fila1, w_usar_lic, button_height)
    rect_boton_guardar_id = pygame.Rect(x_inicio_fila1 + w_usar_lic + espacio_entre_botones_horizontal, y_botones_fila1, w_guardar_id, button_height)

    # Fila 2: "Activar" y "Salir"
    y_botones_fila2 = y_botones_fila1 + button_height + espacio_vertical_entre_filas 
    w_activar = 120
    w_salir = 120

    ancho_total_fila2 = w_activar + espacio_entre_botones_horizontal + w_salir
    x_inicio_fila2 = rect_ventana.left + (ventana_width - ancho_total_fila2) // 2 # Corregido: relativo a rect_ventana.left

    rect_boton_activar = pygame.Rect(x_inicio_fila2, y_botones_fila2, w_activar, button_height)
    rect_boton_salir_app = pygame.Rect(x_inicio_fila2 + w_activar + espacio_entre_botones_horizontal, y_botones_fila2, w_salir, button_height)

    # Dibujar botones y sus textos
    # "Usar Archivo Lic."
    pygame.draw.rect(screen, color_boton_fondo, rect_boton_usar_archivo)
    pygame.draw.rect(screen, color_input_borde, rect_boton_usar_archivo, 1)
    usar_archivo_text_surf = font_boton_med.render("Usar Archivo Lic.", True, color_boton_texto)
    screen.blit(usar_archivo_text_surf, usar_archivo_text_surf.get_rect(center=rect_boton_usar_archivo.center))

    # "Guardar ID"
    pygame.draw.rect(screen, color_boton_fondo, rect_boton_guardar_id)
    pygame.draw.rect(screen, color_input_borde, rect_boton_guardar_id, 1)
    guardar_id_text_surf = font_boton_med.render("Guardar ID", True, color_boton_texto)
    screen.blit(guardar_id_text_surf, guardar_id_text_surf.get_rect(center=rect_boton_guardar_id.center))

    # "Activar"
    pygame.draw.rect(screen, color_boton_fondo, rect_boton_activar)
    pygame.draw.rect(screen, color_input_borde, rect_boton_activar, 1)
    activar_text_surf = font_texto.render("Activar", True, color_boton_texto)
    screen.blit(activar_text_surf, activar_text_surf.get_rect(center=rect_boton_activar.center))

    # "Salir"
    pygame.draw.rect(screen, color_boton_fondo, rect_boton_salir_app)
    pygame.draw.rect(screen, color_input_borde, rect_boton_salir_app, 1)
    salir_text_surf = font_texto.render("Salir", True, color_boton_texto)
    screen.blit(salir_text_surf, salir_text_surf.get_rect(center=rect_boton_salir_app.center))

    # Dibujar cursor si el input está activo y el cursor es visible
    if input_active and show_cursor:
        text_width = input_key_surf.get_width()
        cursor_x = rect_input_key.left + 5 + text_width
        # Asegurarse de que el cursor no se dibuje más allá del borde derecho del campo de entrada
        if cursor_x > rect_input_key.right - 3: # 3px de padding derecho para el cursor
            cursor_x = rect_input_key.right - 3
        
        pygame.draw.line(screen, color_texto, 
                         (cursor_x, rect_input_key.top + 5), 
                         (cursor_x, rect_input_key.bottom - 5), 1)
    
    pygame.display.flip()
    return rect_input_key, rect_boton_activar, rect_boton_salir_app, rect_boton_guardar_id, rect_boton_usar_archivo, rect_boton_copiar_id

# --- Función para manejar la ventana de activación ---
def run_activation_sequence(screen, current_internal_id, current_display_id):
    """
    Maneja la lógica y el bucle de eventos para la ventana de activación.
    Devuelve True si la activación fue exitosa, False en caso contrario.
    Actualiza las variables globales PROGRAM_MODE y ACTIVATED_SUCCESSFULLY.
    """
    global PROGRAM_MODE, ACTIVATED_SUCCESSFULLY, user_license_key_input # Necesitamos user_license_key_input si se mantiene entre llamadas
                                                                    # o se reinicia cada vez. Por ahora, reiniciemos.
    
    user_license_key_input = "" # Reiniciar para cada vez que se muestra la ventana
    activation_error_message = None
    id_saved_message = None 
    id_saved_message_timer = 0 
    activation_window_active = True
    input_key_active = False 

    cursor_visible = True
    cursor_blink_timer = 0
    # CURSOR_BLINK_INTERVAL ya es una constante global, o debería serlo, o pasada como arg.
    # Por ahora, asumimos que es accesible globalmente o la definimos aquí si es local a esta función.
    # Si es global, no hay problema. Si no, necesita ser definida o pasada.
# CURSOR_BLINK_INTERVAL = 500 # Ya está definida globalmente en main antes de llamar a esta función.
# No es necesario redefinirla aquí ni pasarla como argumento si es una constante global del script.
# Sin embargo, para buena práctica, las constantes usadas por una función deberían estar disponibles en su scope
# o pasadas como argumento. CURSOR_BLINK_INTERVAL se define en main().
# Para que esta función sea más autocontenida o si CURSOR_BLINK_INTERVAL no fuera global:
# Descomentar la siguiente línea o añadir CURSOR_BLINK_INTERVAL como parámetro.
# CURSOR_BLINK_INTERVAL = 500 # OJO: Si se define aquí, puede haber inconsistencia si se cambia en main.

    while activation_window_active:
        current_time_millis = pygame.time.get_ticks()
        if id_saved_message and current_time_millis > id_saved_message_timer:
            id_saved_message = None 
        
        if current_time_millis - cursor_blink_timer > CURSOR_BLINK_INTERVAL:
            cursor_visible = not cursor_visible
            cursor_blink_timer = current_time_millis
        
        current_display_message = activation_error_message if activation_error_message else id_saved_message

        # Llamada a draw_activation_window
        # Asegurarse que los parámetros coinciden con la definición de draw_activation_window
        drawn_rects = draw_activation_window(
            screen, 
            current_display_id, # Renombrado para claridad, antes era display_id
            user_license_key_input, 
            current_display_message, 
            input_key_active, 
            cursor_visible
        )
        rect_input_key_field, rect_btn_activar, rect_btn_salir_app, \
        rect_boton_guardar_id, rect_boton_usar_archivo, rect_boton_copiar_id = drawn_rects
        
        for evento_activacion in pygame.event.get():
            if evento_activacion.type == pygame.QUIT:
                pygame.quit()
                sys.exit() 
            if evento_activacion.type == pygame.MOUSEBUTTONDOWN:
                if evento_activacion.button == 1:
                    if rect_btn_activar.collidepoint(evento_activacion.pos):
                        if verify_license_key(user_license_key_input, current_internal_id): # current_internal_id
                            store_license_data(user_license_key_input, current_internal_id) # current_internal_id
                            ACTIVATED_SUCCESSFULLY = True
                            PROGRAM_MODE = "LICENSED"
                            delete_trial_info()
                            activation_window_active = False 
                            print("Licencia activada exitosamente.")
                        else:
                            activation_error_message = "Clave de licencia inválida. Intente de nuevo."
                            user_license_key_input = "" 
                    elif rect_btn_salir_app.collidepoint(evento_activacion.pos):
                        activation_window_active = False 
                        # La lógica de si se inicia trial o no al salir, se manejará fuera,
                        # basado en el valor de retorno de esta función y el estado previo.
                    
                    elif rect_boton_usar_archivo.collidepoint(evento_activacion.pos):
                        input_key_active = False; activation_error_message = None; id_saved_message = None
                        if TKINTER_AVAILABLE:
                            root = tk.Tk(); root.withdraw()
                            filepath = filedialog.askopenfilename(title="Seleccione el archivo de licencia (.json)", filetypes=(("JSON files", "*.json"), ("All files", "*.*")))
                            root.destroy()
                            if filepath:
                                try:
                                    with open(filepath, 'r') as f_import: data_importada = json.load(f_import)
                                    clave_importada = data_importada.get("license_key")
                                    id_maquina_importado = data_importada.get("machine_identifier")
                                    if clave_importada and id_maquina_importado:
                                        if id_maquina_importado == current_internal_id: # current_internal_id
                                            if verify_license_key(clave_importada, id_maquina_importado):
                                                store_license_data(clave_importada, id_maquina_importado)
                                                ACTIVATED_SUCCESSFULLY = True; PROGRAM_MODE = "LICENSED"; delete_trial_info()
                                                activation_window_active = False
                                            else: activation_error_message = "Clave en archivo de licencia es inválida."
                                        else: activation_error_message = "Licencia no corresponde a esta máquina."
                                    else: activation_error_message = "Archivo de licencia con formato incorrecto."
                                except Exception as e_import: activation_error_message = f"Error al procesar archivo: {e_import}"
                        else: activation_error_message = "Tkinter no disponible."
                        id_saved_message_timer = current_time_millis + 3000
                    
                    elif rect_boton_copiar_id.collidepoint(evento_activacion.pos):
                        input_key_active = False; activation_error_message = None
                        if PYPERCLIP_AVAILABLE:
                            try: pyperclip.copy(current_display_id); id_saved_message = "ID de Máquina copiado." # current_display_id
                            except Exception: id_saved_message = "Error al copiar ID."
                        else: id_saved_message = "Copiar ID no disponible."
                        id_saved_message_timer = current_time_millis + 3000
                    elif rect_boton_guardar_id.collidepoint(evento_activacion.pos):
                        input_key_active = False; activation_error_message = None
                        if save_id_to_file(current_display_id): id_saved_message = f"ID guardado en machine_id.txt" # current_display_id
                        else: id_saved_message = "Error al guardar ID."
                        id_saved_message_timer = current_time_millis + 3000
                    elif rect_input_key_field.collidepoint(evento_activacion.pos):
                        input_key_active = True; activation_error_message = None; id_saved_message = None
                    else:
                        input_key_active = False
            
            if evento_activacion.type == pygame.KEYDOWN and input_key_active:
                if evento_activacion.key == pygame.K_RETURN:
                    if verify_license_key(user_license_key_input, current_internal_id): # current_internal_id
                        store_license_data(user_license_key_input, current_internal_id) # current_internal_id
                        ACTIVATED_SUCCESSFULLY = True; PROGRAM_MODE = "LICENSED"; delete_trial_info()
                        activation_window_active = False
                    else:
                        activation_error_message = "Clave de licencia inválida."; user_license_key_input = ""
                elif evento_activacion.key == pygame.K_BACKSPACE: user_license_key_input = user_license_key_input[:-1]
                elif evento_activacion.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL or pygame.key.get_mods() & pygame.KMOD_META):
                    if PYPERCLIP_AVAILABLE:
                        try:
                                pasted_text = pyperclip.paste() # Punto y coma eliminado de aquí también para consistencia
                                remaining_len = 32 - len(user_license_key_input)
                                # No convertir a .upper() al pegar
                                user_license_key_input += pasted_text[:remaining_len] # Punto y coma eliminado
                                user_license_key_input = user_license_key_input[:32] # Asegurar longitud
                        except Exception: pass 
                elif evento_activacion.unicode.isalnum() and len(user_license_key_input) < 32: # Permitir solo alfanuméricos
                    user_license_key_input += evento_activacion.unicode # No convertir a .upper()
    
    return ACTIVATED_SUCCESSFULLY # Devuelve el estado de activación

# --- Función para formatear el tiempo restante del periodo de gracia ---
def format_remaining_grace_time(start_time_utc: datetime | None) -> str | None:
    """
    Calcula y formatea el tiempo restante del periodo de gracia.
    start_time_utc debe ser un objeto datetime aware (UTC).
    Devuelve un string formateado "HHh MMm SSs" o None si el tiempo expiró o no es válido.
    """
    if start_time_utc is None:
        return None

    try:
        # Asegurarse que start_time_utc es aware, si no lo es, asumirlo UTC (aunque debería serlo)
        if start_time_utc.tzinfo is None or start_time_utc.tzinfo.utcoffset(start_time_utc) is None:
            # Esto es un fallback, idealmente start_time_utc siempre es aware.
            start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)

        end_time_utc = start_time_utc + timedelta(days=1)
        now_utc = datetime.now(timezone.utc)
        remaining_delta = end_time_utc - now_utc

        if remaining_delta.total_seconds() <= 0:
            return None # O podría ser "Expirado"

        total_seconds = int(remaining_delta.total_seconds())
        
        days = total_seconds // (24 * 3600)
        total_seconds %= (24 * 3600)
        
        hours = total_seconds // 3600
        total_seconds %= 3600
        
        minutes = total_seconds // 60
        seconds = total_seconds % 60

        if days > 0:
            return f"{days}d {hours:02}h {minutes:02}m" # No mostrar segundos si hay días
        else:
            return f"{hours:02}h {minutes:02}m {seconds:02}s"

    except Exception as e:
        print(f"Error al formatear tiempo restante de gracia: {e}")
        return None


def main():
    global IDIOMA, sonido_alarma_actualmente_reproduciendo, tiempo_ultimo_sonido_iniciado
    global INDICE_PROXIMA_ALARMA_A_SONAR, ultima_reproduccion_alarma_babor_tiempo
    global ultima_reproduccion_alarma_estribor_tiempo, ultima_reproduccion_alarma_sentado_tiempo
    global ultima_reproduccion_alarma_encabuzado_tiempo, ultima_vez_envio_datos
    global ultimo_intento_reconeccion_tiempo # Declaración global añadida
    global latitude_str, longitude_str, speed_str, heading_str, att_heading_str, att_pitch_str, att_roll_str
    global ts_pitch_float, ts_roll_float, ts_lat_decimal, ts_lon_decimal, ts_speed_float, ts_heading_float, ts_timestamp_str
    global alarma_roll_babor_activa, alarma_roll_estribor_activa, alarma_pitch_sentado_activa, alarma_pitch_encabuzado_activa
    global ser, serial_port_available, ultima_vez_datos_recibidos, nmea_data_stale
    # Globales para la configuración del servicio de datos
    global SERVICIO_DATOS_ACTUAL, API_KEY_THINGSPEAK, API_KEY_GOOGLE_CLOUD
    global input_api_key_thingspeak_str, input_api_key_google_cloud_str
    # Globales para el estado de las ventanas modales y sus inputs
    global mostrar_ventana_config_serial, mostrar_ventana_alarma, mostrar_ventana_idioma, mostrar_ventana_acerca_de
    global mostrar_ventana_servicio_datos, mostrar_ventana_password_servicio
    global input_puerto_str, input_baudios_idx, puerto_dropdown_activo, baudios_dropdown_activo # Para config puerto
    global input_alarma_activo # Para config alarma
    global input_password_str, intento_password_fallido, input_password_activo # Para password servicio
    global input_servicio_activo # Para config servicio datos
    global ACTIVATED_SUCCESSFULLY
    
    # Inicializar Pygame Temprano para Ventana de Activación (si es necesario)
    pygame.init() # Asegurar que Pygame esté inicializado
    dimensiones = [1060, 430] # Definir dimensiones antes para la ventana de activación
    screen = pygame.display.set_mode(dimensiones) # Crear la pantalla
    pygame.display.set_caption("Clinómetro") # Título genérico inicial

    # --- Gestión de Licencia y Periodo de Gracia ---
    global PROGRAM_MODE, ACTIVATED_SUCCESSFULLY, grace_period_start_time_obj # Añadir grace_period_start_time_obj a globales de main
    
    if check_license_status(): # check_license_status actualiza ACTIVATED_SUCCESSFULLY
        PROGRAM_MODE = "LICENSED"
        delete_trial_info() # Si está licenciado, no necesitamos el archivo de trial
        print("INFO: Licencia válida encontrada. Programa en modo LICENSED.")
    else:
        ACTIVATED_SUCCESSFULLY = False # Asegurar que está False si check_license_status falló
        trial_data = load_trial_info()
        if trial_data is None:
            # No hay licencia y no hay información de trial: Primera vez o trial_info.json borrado
            # Mostrar ventana de activación
            PROGRAM_MODE = "ACTIVATION_UI_VISIBLE"
            print("INFO: No hay licencia válida ni información de trial. Mostrando ventana de activación.")
            # (El bucle de la ventana de activación se manejará más adelante)
        else:
            # Hay información de trial, verificar si el periodo de gracia ha expirado
            try:
                start_timestamp_str = trial_data.get("grace_period_start_timestamp_utc")
                if not start_timestamp_str:
                    raise ValueError("Timestamp de inicio de trial no encontrado en trial_info.json")
                
                # Manejar el formato antiguo incorrecto si existe (ej. "...+00:00Z")
                if start_timestamp_str.endswith("+00:00Z"):
                    print(f"DEBUG: Detectado formato de timestamp antiguo '{start_timestamp_str}'. Corrigiendo eliminando 'Z' final.")
                    start_timestamp_str = start_timestamp_str[:-1] 
                
                print(f"DEBUG: Leyendo start_timestamp_str (potencialmente corregido) de trial_info.json: {start_timestamp_str}") # DEBUG
                start_datetime_utc = datetime.fromisoformat(start_timestamp_str) # Parseo directo
                
                # Asegurar que es aware y UTC (fromisoformat con offset ya lo hace aware)
                if start_datetime_utc.tzinfo is None or start_datetime_utc.tzinfo.utcoffset(start_datetime_utc) != timedelta(0):
                    print(f"ADVERTENCIA: El timestamp leído de trial_info.json no es UTC o no es aware: {start_datetime_utc}. Forzando a UTC.")
                    start_datetime_utc = start_datetime_utc.astimezone(timezone.utc) if start_datetime_utc.tzinfo else start_datetime_utc.replace(tzinfo=timezone.utc)


                current_datetime_utc = datetime.now(timezone.utc) # Siempre aware UTC
                
                print(f"DEBUG: start_datetime_utc (procesado): {start_datetime_utc}") # DEBUG
                print(f"DEBUG: current_datetime_utc: {current_datetime_utc}") # DEBUG
                
                # El bloque if hasattr...else... que causaba IndentationError ha sido eliminado.
                # La línea current_datetime_utc = datetime.now(timezone.utc) ya está antes y es la correcta.

                grace_period_duration = timedelta(days=1) # 24 horas

                if current_datetime_utc < start_datetime_utc + grace_period_duration:
                    PROGRAM_MODE = "GRACE_PERIOD"
                    grace_period_start_time_obj = start_datetime_utc # Poblar la variable global
                    print(f"INFO: Programa en periodo de gracia. Restante: {start_datetime_utc + grace_period_duration - current_datetime_utc}")
                else:
                    PROGRAM_MODE = "TRIAL_EXPIRED"
                    print("INFO: Periodo de gracia expirado. Programa en modo TRIAL_EXPIRED.")
            except Exception as e:
                print(f"ERROR: Error al procesar trial_info.json: {e}. No se pudo determinar el estado del periodo de gracia.")
                print("INFO: Se mostrará la ventana de activación. El archivo trial_info.json (si existe y causó el error) NO será eliminado para inspección.")
                # delete_trial_info() # NO borrar el archivo para poder depurar su contenido si es necesario.
                PROGRAM_MODE = "ACTIVATION_UI_VISIBLE" # Forzar activación.


    # --- Bucle de Activación de Licencia (Solo si PROGRAM_MODE es "ACTIVATION_UI_VISIBLE" al inicio) ---
    proceed_to_main_loop = False

    if PROGRAM_MODE == "LICENSED":
        ACTIVATED_SUCCESSFULLY = True 
        proceed_to_main_loop = True
    elif PROGRAM_MODE == "GRACE_PERIOD":
        ACTIVATED_SUCCESSFULLY = True 
        proceed_to_main_loop = True
    elif PROGRAM_MODE == "TRIAL_EXPIRED":
        ACTIVATED_SUCCESSFULLY = False 
        proceed_to_main_loop = True
    elif PROGRAM_MODE == "ACTIVATION_UI_VISIBLE":
        # Obtener IDs solo si vamos a mostrar la ventana
        internal_id, display_id = get_machine_specific_identifier()
        # CURSOR_BLINK_INTERVAL ya es global, no se necesita inicializar aquí.
        # cursor_visible y cursor_blink_timer son locales al bucle de run_activation_sequence.
        
        # Llamar a la función refactorizada
        activation_success = run_activation_sequence(screen, internal_id, display_id)

        if activation_success: # ACTIVATED_SUCCESSFULLY y PROGRAM_MODE ya se actualizan dentro de run_activation_sequence
            proceed_to_main_loop = True
        else: # Activación fallida o cerrada por el usuario
            # Si era la primera vez (no hay trial_info), run_activation_sequence NO inicia el trial al presionar "Salir".
            # Esa lógica debe estar aquí, después de que la ventana se cierra.
            if load_trial_info() is None:
                current_utc_time_for_trial = datetime.now(timezone.utc)
                save_trial_info(current_utc_time_for_trial.isoformat()) # Guardar en formato ISO estándar (ya incluye offset UTC)
                grace_period_start_time_obj = current_utc_time_for_trial # Poblar la variable global
                PROGRAM_MODE = "GRACE_PERIOD"
                ACTIVATED_SUCCESSFULLY = True # Permitir uso completo en gracia
                print("INFO: Ventana de activación cerrada/omitida. Iniciando periodo de gracia de 24h.")
                proceed_to_main_loop = True
            else:
                # Si ya había un trial_info (ej. porque el periodo de gracia ya había empezado y se accedió al menú,
                # o el trial expiró y se accedió al menú), y el usuario cierra sin activar,
                # el estado no cambia y proceed_to_main_loop dependerá del estado previo.
                # Si PROGRAM_MODE era TRIAL_EXPIRED, seguirá siéndolo.
                if PROGRAM_MODE == "TRIAL_EXPIRED": # Si estaba expirado y no activó
                    proceed_to_main_loop = True # Permite continuar en modo trial
                    ACTIVATED_SUCCESSFULLY = False
                elif PROGRAM_MODE == "GRACE_PERIOD": # Si estaba en gracia y no activó
                     proceed_to_main_loop = True # Permite continuar en modo gracia
                     ACTIVATED_SUCCESSFULLY = True
                else: # Otro caso inesperado
                     proceed_to_main_loop = False


    if not proceed_to_main_loop:
        print("El programa no puede continuar. Verifique el estado de la licencia.")
        pygame.quit()
        sys.exit()
    
    # Si llegamos aquí, ACTIVATED_SUCCESSFULLY es True para LICENSED y GRACE_PERIOD
    # o False para TRIAL_EXPIRED (pero proceed_to_main_loop es True para TRIAL_EXPIRED)
    
    print(f"INFO: Estado final antes del bucle principal: PROGRAM_MODE = {PROGRAM_MODE}, ACTIVATED_SUCCESSFULLY = {ACTIVATED_SUCCESSFULLY}")

    # Cargar configuraciones (después de la lógica de licencia/trial y antes de usar IDIOMA)
    puerto, baudios = cargar_configuracion_serial() # Carga IDIOMA también
    cargar_configuracion_alarma()
    
    # Inicializar variables de datos
    reset_ui_data()
    init_csv()
    init_alarm_csv() # Inicializar CSV de alarmas
    
    # Configurar título de ventana con idioma correcto
    pygame.display.set_caption(TEXTOS[IDIOMA]["titulo_ventana"])
    
    # Configuración de áreas de visualización
    ALTURA_BARRA_HERRAMIENTAS = 30
    nuevo_ancho_area_izquierda = 750
    area_izquierda_rect = pygame.Rect(10, ALTURA_BARRA_HERRAMIENTAS + 10, nuevo_ancho_area_izquierda, dimensiones[1] - (ALTURA_BARRA_HERRAMIENTAS + 20))
    
    # Configuración de círculos para pitch y roll
    radio_circulo_img = 78 * 2
    margen_superior_circulos = 20
    centro_y_circulos = area_izquierda_rect.top + radio_circulo_img + margen_superior_circulos
    centro_x_circulo1 = 10 + radio_circulo_img + 43 # 45 - 2 = 43
    centro_x_circulo2 = centro_x_circulo1 + (2 * radio_circulo_img) + 50
    
    # Configuración de marcas de grados
    LONGITUD_MARCA_GRADO = 16
    GROSOR_MARCA_GRADO = 3
    COLOR_MARCA_GRADO = BLANCO
    COLOR_ETIQUETA_GRADO = BLANCO
    RADIO_INICIO_MARCAS = radio_circulo_img
    RADIO_FIN_MARCAS = radio_circulo_img + LONGITUD_MARCA_GRADO
    OFFSET_TEXTO_ETIQUETA = 20
    RADIO_POSICION_TEXTO_ETIQUETA = RADIO_FIN_MARCAS + OFFSET_TEXTO_ETIQUETA
    
    ANGULOS_MARCAS_ETIQUETAS_DEF = {
        "0_der": (0, "0"), 
        "0_izq": (180, "0"), 
        "sup_mas_30": (-120, "+30"), 
        "sup_menos_30": (-60, "-30")
    }
    
    # Configuración de flechas
    LONGITUD_FLECHA_DIR = 20
    ANCHO_FLECHA_DIR = 12
    OFFSET_FLECHA_TEXTO = 10
    OFFSET_LETRA_ROLL_Y = 10
    COLOR_LETRA_ROLL = BLANCO
    
    # Cargar imágenes de fondo
    try:
        imagen_fondo_original = pygame.image.load(resource_path("mar.jpg"))
        imagen_fondo_escalada = pygame.transform.scale(imagen_fondo_original, dimensiones)
        imagen_fondo_escalada = imagen_fondo_escalada.convert()
    except:
        imagen_fondo_escalada = None
    
    # Cargar imágenes de pitch y roll
    pitch_image_base_grande = None
    try:
        pitch_image_surface_original_temp = pygame.image.load(resource_path("pitch.png"))
        lado_pitch_deseado_grande = int((2 * radio_circulo_img) + 2)
        pitch_image_base_grande = pygame.transform.smoothscale(pitch_image_surface_original_temp, (lado_pitch_deseado_grande, lado_pitch_deseado_grande)).convert_alpha()
    except:
        pitch_image_base_grande = None
    
    roll_image_base_grande = None
    try:
        roll_image_surface_original_temp = pygame.image.load(resource_path("roll.png"))
        lado_img_deseado_grande = int((2 * radio_circulo_img) + 2)
        roll_image_base_grande = pygame.transform.smoothscale(roll_image_surface_original_temp, (lado_img_deseado_grande, lado_img_deseado_grande)).convert_alpha()
    except:
        roll_image_base_grande = None
    
    # Configuración de fuentes
    font = pygame.font.Font(None, 24)
    font_bar_herramientas = pygame.font.Font(None, 22)
    font_datos_grandes = pygame.font.Font(None, 50)
    font_circulos_textos = pygame.font.Font(None, 72)
    
    # Configuración de reloj
    reloj = pygame.time.Clock()
    
    # Configuración de la barra de herramientas
    rect_barra_herramientas = pygame.Rect(0, 0, dimensiones[0], ALTURA_BARRA_HERRAMIENTAS)
    rects_opciones_menu_barra = []
    padding_menu_x = 15
    espacio_entre_menus = 10
    
    # Configuración de ventanas
    mostrar_ventana_config_serial = False
    mostrar_ventana_acerca_de = False
    mostrar_ventana_alarma = False
    mostrar_ventana_idioma = False
    
    # Configuración de colores para la interfaz
    COLOR_VENTANA_FONDO = (144, 238, 144)
    COLOR_TEXTO_NORMAL = NEGRO
    COLOR_BORDE_VENTANA = (170, 170, 170)
    COLOR_BORDE_VENTANA_CLARO = (220, 220, 220)
    COLOR_BORDE_VENTANA_OSCURO = (100, 100, 100)
    COLOR_BARRA_HERRAMIENTAS_FONDO = (220, 220, 220)
    COLOR_BARRA_HERRAMIENTAS_BORDE = (180, 180, 180)
    COLOR_ITEM_MENU_TEXTO = NEGRO
    COLOR_BOTON_FONDO = (225, 225, 225)
    COLOR_BOTON_BORDE = (150, 150, 150)
    COLOR_BOTON_FONDO_3D = (210, 210, 210)
    COLOR_BOTON_BORDE_CLARO_3D = (230, 230, 230)
    COLOR_BOTON_BORDE_OSCURO_3D = (130, 130, 130)
    COLOR_INPUT_FONDO = BLANCO
    COLOR_INPUT_BORDE = (120, 120, 120)
    COLOR_INPUT_BORDE_CLARO_3D = (200, 200, 200)
    COLOR_INPUT_BORDE_OSCURO_3D = (80, 80, 80)
    COLOR_DROPDOWN_FONDO = (250, 250, 250)
    COLOR_DROPDOWN_BORDE = (100, 100, 100)
    COLOR_SELECCION_DROPDOWN = (200, 220, 255)
    COLOR_CAJA_DATOS_FONDO = NEGRO  # Cambiado a NEGRO
    COLOR_CAJA_DATOS_BORDE = (120, 120, 120) # Se mantiene el borde gris, o se puede cambiar si se desea
    COLOR_CAJA_DATOS_TEXTO = ROJO   # Cambiado a ROJO
    
    # Configuración del puerto serial
    ser = None
    serial_port_available = True
    try:
        ser = serial.Serial(puerto, baudios, timeout=1)
        print(f"Puerto serial {puerto} abierto con {baudios} baudios.")
    except serial.SerialException as e:
        print(f"Error opening serial port {puerto} with {baudios} baud: {e}")
        serial_port_available = False
    except Exception as e:
        print(f"An unexpected error occurred opening serial port: {e}")
        serial_port_available = False
    
    # Variables para la ventana de configuración
    ventana_config_width = 300
    ventana_config_height = 400
    ventana_config_x = (dimensiones[0] - ventana_config_width) // 2
    ventana_config_y = (dimensiones[1] - ventana_config_height) // 2
    rect_ventana_config = pygame.Rect(ventana_config_x, ventana_config_y, ventana_config_width, ventana_config_height)
    
    input_puerto_str = str(puerto)
    lista_baudios_seleccionables = sorted([4800, 9600, 19200, 38400, 57600, 115200])
    try:
        input_baudios_idx = lista_baudios_seleccionables.index(baudios)
    except ValueError:
        input_baudios_idx = 0
    
    input_elements_top_offset_config = 50
    input_elements_height_config = 30
    label_width_config = 70
    padding_interno_config = 10
    input_width_config = ventana_config_width - label_width_config - padding_interno_config * 3
    
    rect_input_puerto_config = pygame.Rect(
        rect_ventana_config.left + padding_interno_config + label_width_config + padding_interno_config,
        rect_ventana_config.top + input_elements_top_offset_config,
        input_width_config, input_elements_height_config
    )
    
    y_pos_baudios = rect_input_puerto_config.bottom + 15 + 50
    rect_input_baudios_display_config = pygame.Rect(
        rect_ventana_config.left + padding_interno_config + label_width_config + padding_interno_config,
        y_pos_baudios,
        input_width_config, input_elements_height_config
    )
    
    button_config_width = ventana_config_width - 40
    button_config_height = 40
    rect_boton_guardar_config = pygame.Rect(
        rect_ventana_config.centerx - button_config_width // 2,
        rect_ventana_config.bottom - button_config_height - 20,
        button_config_width, button_config_height
    )
    
    rect_boton_cerrar_config = pygame.Rect(
        rect_ventana_config.right - 35,
        rect_ventana_config.top + 5,
        30, 30
    )
    
    # Variables para la ventana de alarma
    rect_ventana_alarma = pygame.Rect(250, 100, 380, 230)
    input_alarma_activo = None
    
    # Variables para la ventana de idioma
    rect_ventana_idioma = pygame.Rect(0, 0, 250, 170)
    rect_boton_es = None
    rect_boton_en = None

    # Variables para la ventana de servicio de datos
    mostrar_ventana_servicio_datos = False
    ventana_servicio_width = 400
    ventana_servicio_height = 280 # Ajustable según contenido
    ventana_servicio_x = (dimensiones[0] - ventana_servicio_width) // 2
    ventana_servicio_y = (dimensiones[1] - ventana_servicio_height) // 2
    rect_ventana_servicio_datos = pygame.Rect(ventana_servicio_x, ventana_servicio_y, ventana_servicio_width, ventana_servicio_height)
    input_servicio_activo = None # Para saber qué API key se está editando: "thingspeak" o "google"
    rect_radio_thingspeak = None
    rect_radio_google_cloud = None
    rect_input_apikey_thingspeak = None
    rect_input_apikey_google_cloud = None
    rect_boton_guardar_servicio = None
    rect_boton_cerrar_servicio = None
    RADIO_BUTTON_SIZE = 10 # Radio del círculo del radio button

    # Variables para la ventana de contraseña del servicio de datos (dimensiones y rects)
    ventana_password_width = 300
    ventana_password_height = 200
    ventana_password_x = (dimensiones[0] - ventana_password_width) // 2
    ventana_password_y = (dimensiones[1] - ventana_password_height) // 2
    rect_ventana_password_servicio = pygame.Rect(ventana_password_x, ventana_password_y, ventana_password_width, ventana_password_height)
    rect_input_password = None
    rect_boton_entrar_password = None
    rect_boton_cerrar_password_servicio = None # Para el botón 'X' de esta ventana
    input_password_activo = False # Para saber si el campo de contraseña está activo
    
    # Bucle principal
    hecho = False
    nmea_data_stale = False
    
    while not hecho:
        mouse_pos = pygame.mouse.get_pos()
        
        # Verificar si el mouse está sobre la barra de herramientas
        toolbar_visible = rect_barra_herramientas.collidepoint(mouse_pos)
        
        # Definir opciones del menú dinámicamente
        opciones_menu_barra = []
        opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_config"])
        opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_alarma"])
        opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_idioma"])
        opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_servicio_datos"])
        
        if PROGRAM_MODE != "LICENSED":
            opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_activar"])
            
        opciones_menu_barra.append(TEXTOS[IDIOMA]["menu_acerca"])
        
        # Manejo de eventos
        for evento in pygame.event.get():
            if evento.type == pygame.QUIT:
                hecho = True
            
            if evento.type == pygame.MOUSEBUTTONDOWN:
                if evento.button == 1:  # Clic izquierdo
                    # Manejo de clic en la barra de herramientas
                    if toolbar_visible and not mostrar_ventana_config_serial and not mostrar_ventana_acerca_de and not mostrar_ventana_alarma and not mostrar_ventana_idioma:
                        for i, rect_opcion in enumerate(rects_opciones_menu_barra):
                            if rect_opcion.collidepoint(evento.pos):
                                opcion_clicada_texto = opciones_menu_barra[i] # Obtener el texto de la opción

                                if opcion_clicada_texto == TEXTOS[IDIOMA]["menu_config"]:
                                    mostrar_ventana_config_serial = True
                                    input_puerto_str = str(puerto)
                                    try: input_baudios_idx = lista_baudios_seleccionables.index(int(baudios))
                                    except ValueError: input_baudios_idx = 0
                                    lista_puertos_detectados.clear()
                                    try:
                                        ports = comports()
                                        if ports: lista_puertos_detectados.extend(p.device for p in ports)
                                        else: lista_puertos_detectados.append("N/A")
                                    except Exception: lista_puertos_detectados.append("Error")
                                    
                                elif opcion_clicada_texto == TEXTOS[IDIOMA]["menu_alarma"]:
                                    mostrar_ventana_alarma = True
                                    input_alarma_activo = None
                                    try: valores_ui_input_alarma["pitch"] = str(abs(int(float(valores_alarma["max_pitch_pos"]))))
                                    except: valores_ui_input_alarma["pitch"] = "15"
                                    try: valores_ui_input_alarma["roll"] = str(abs(int(float(valores_alarma["max_roll_pos"]))))
                                    except: valores_ui_input_alarma["roll"] = "15"
                                
                                elif opcion_clicada_texto == TEXTOS[IDIOMA]["menu_idioma"]:
                                    mostrar_ventana_idioma = True
                                    rect_ventana_idioma.center = screen.get_rect().center
                                
                                elif opcion_clicada_texto == TEXTOS[IDIOMA]["menu_servicio_datos"]:
                                    mostrar_ventana_password_servicio = True
                                    input_password_str = ""; intento_password_fallido = False; input_servicio_activo = None
                                
                                elif opcion_clicada_texto == TEXTOS[IDIOMA]["menu_activar"]:
                                    # Esta opción solo está en opciones_menu_barra si PROGRAM_MODE != "LICENSED"
                                    # así que no necesitamos un check explícito de PROGRAM_MODE aquí, aunque no hace daño.
                                    if PROGRAM_MODE == "LICENSED": # Doble check, por si acaso la lógica de lista falla
                                         print("INFO: El producto ya está activado (clic en menú).")
                                    else:
                                        print(f"INFO: Accediendo a activación desde menú. PROGRAM_MODE actual: {PROGRAM_MODE}")
                                        temp_internal_id, temp_display_id = get_machine_specific_identifier()
                                        activation_was_successful = run_activation_sequence(screen, temp_internal_id, temp_display_id)
                                        
                                        if activation_was_successful:
                                            print("INFO: Activación exitosa desde el menú.")
                                            # PROGRAM_MODE y ACTIVATED_SUCCESSFULLY se actualizan en run_activation_sequence
                                        else:
                                            print("INFO: Ventana de activación cerrada desde el menú sin activación exitosa.")
                                            # El estado (GRACE_PERIOD o TRIAL_EXPIRED) no cambia.
                                
                                elif opcion_clicada_texto == TEXTOS[IDIOMA]["menu_acerca"]:
                                    mostrar_ventana_acerca_de = True
                                break # Salir del bucle for de opciones de menú una vez que se maneja un clic
                    
                    # Manejo de clic en ventana de servicio de datos
                    elif mostrar_ventana_servicio_datos:
                        if globals().get('rect_boton_cerrar_servicio') and globals().get('rect_boton_cerrar_servicio').collidepoint(evento.pos):
                            mostrar_ventana_servicio_datos = False
                            input_servicio_activo = None
                        elif globals().get('rect_radio_thingspeak') and globals().get('rect_radio_thingspeak').collidepoint(evento.pos):
                            SERVICIO_DATOS_ACTUAL = "thingspeak"
                        elif globals().get('rect_radio_google_cloud') and globals().get('rect_radio_google_cloud').collidepoint(evento.pos):
                            SERVICIO_DATOS_ACTUAL = "google_cloud"
                        elif globals().get('rect_input_apikey_thingspeak') and globals().get('rect_input_apikey_thingspeak').collidepoint(evento.pos):
                            input_servicio_activo = "thingspeak"
                        elif globals().get('rect_input_apikey_google_cloud') and globals().get('rect_input_apikey_google_cloud').collidepoint(evento.pos):
                            input_servicio_activo = "google_cloud"
                        elif globals().get('rect_boton_guardar_servicio') and globals().get('rect_boton_guardar_servicio').collidepoint(evento.pos):
                            # Guardar los valores de los inputs en las variables globales principales
                            API_KEY_THINGSPEAK = input_api_key_thingspeak_str
                            API_KEY_GOOGLE_CLOUD = input_api_key_google_cloud_str
                            guardar_configuracion_serial(puerto, baudios) # Guarda todas las configs incluido el servicio y keys
                            mostrar_ventana_servicio_datos = False
                            input_servicio_activo = None
                        else:
                            input_servicio_activo = None # Clic fuera de elementos interactivos

                    # Manejo de clic en ventana de idioma
                    elif mostrar_ventana_idioma:
                        if rect_boton_es and rect_boton_es.collidepoint(evento.pos):
                            IDIOMA = "es"
                            guardar_configuracion_serial(puerto, baudios) # Guardar idioma
                            mostrar_ventana_idioma = False
                        elif rect_boton_en and rect_boton_en.collidepoint(evento.pos):
                            IDIOMA = "en"
                            guardar_configuracion_serial(puerto, baudios) # Guardar idioma
                            mostrar_ventana_idioma = False
                        elif not rect_ventana_idioma.collidepoint(evento.pos): # Clic fuera de la ventana de idioma
                            mostrar_ventana_idioma = False

                    # Manejo de clic en la ventana de Contraseña para Servicio de Datos
                    elif mostrar_ventana_password_servicio:
                        if globals().get('rect_boton_cerrar_password_servicio') and globals().get('rect_boton_cerrar_password_servicio').collidepoint(evento.pos):
                            mostrar_ventana_password_servicio = False
                            input_password_str = ""
                            intento_password_fallido = False
                            input_password_activo = False
                        elif globals().get('rect_input_password') and globals().get('rect_input_password').collidepoint(evento.pos):
                            input_password_activo = True
                        elif globals().get('rect_boton_entrar_password') and globals().get('rect_boton_entrar_password').collidepoint(evento.pos):
                            if input_password_str == CLAVE_ACCESO_SERVICIO:
                                mostrar_ventana_password_servicio = False
                                mostrar_ventana_servicio_datos = True # Abrir ventana de servicio
                                input_password_str = ""
                                intento_password_fallido = False
                                input_password_activo = False
                                # Cargar claves actuales a los inputs de la ventana de servicio
                                input_api_key_thingspeak_str = API_KEY_THINGSPEAK
                                input_api_key_google_cloud_str = API_KEY_GOOGLE_CLOUD
                            else:
                                input_password_str = ""
                                intento_password_fallido = True
                                input_password_activo = False # Desactivar input para que se pueda ver el mensaje
                        else:
                            input_password_activo = False # Clic fuera de elementos interactivos
                    
                    # Manejo de clic en ventana de configuración serial
                    elif mostrar_ventana_config_serial:
                        # Esta variable debe definirse antes de usarse en el evento de clic
                        puerto_dropdown_activo = globals().get('puerto_dropdown_activo', False)
                        baudios_dropdown_activo = globals().get('baudios_dropdown_activo', False)
                        lista_rects_items_puerto = globals().get('lista_rects_items_puerto', [])
                        lista_rects_items_baudios = globals().get('lista_rects_items_baudios', [])


                        if puerto_dropdown_activo:
                            clic_en_item_puerto = False
                            for i, item_rect in enumerate(lista_rects_items_puerto):
                                if item_rect.collidepoint(evento.pos):
                                    input_puerto_str = lista_puertos_detectados[i]
                                    puerto_dropdown_activo = False
                                    globals()['puerto_dropdown_activo'] = False # Actualizar global
                                    clic_en_item_puerto = True
                                    break
                            if not clic_en_item_puerto and not rect_input_puerto_config.collidepoint(evento.pos):
                                puerto_dropdown_activo = False
                                globals()['puerto_dropdown_activo'] = False
                        
                        elif rect_input_puerto_config.collidepoint(evento.pos):
                            puerto_dropdown_activo = not puerto_dropdown_activo
                            globals()['puerto_dropdown_activo'] = puerto_dropdown_activo
                            if puerto_dropdown_activo:
                                baudios_dropdown_activo = False
                                globals()['baudios_dropdown_activo'] = False
                        
                        elif baudios_dropdown_activo:
                            clic_en_item_baudios = False
                            for i, item_rect in enumerate(lista_rects_items_baudios):
                                if item_rect.collidepoint(evento.pos):
                                    input_baudios_idx = i
                                    baudios_dropdown_activo = False
                                    globals()['baudios_dropdown_activo'] = False
                                    clic_en_item_baudios = True
                                    break
                            if not clic_en_item_baudios and not rect_input_baudios_display_config.collidepoint(evento.pos):
                                baudios_dropdown_activo = False
                                globals()['baudios_dropdown_activo'] = False
                        
                        elif rect_input_baudios_display_config.collidepoint(evento.pos):
                            baudios_dropdown_activo = not baudios_dropdown_activo
                            globals()['baudios_dropdown_activo'] = baudios_dropdown_activo
                            if baudios_dropdown_activo:
                                puerto_dropdown_activo = False
                                globals()['puerto_dropdown_activo'] = False
                        
                        elif rect_boton_cerrar_config.collidepoint(evento.pos):
                            mostrar_ventana_config_serial = False
                            puerto_dropdown_activo = False; globals()['puerto_dropdown_activo'] = False
                            baudios_dropdown_activo = False; globals()['baudios_dropdown_activo'] = False
                        
                        elif rect_boton_guardar_config.collidepoint(evento.pos):
                            nuevos_baudios = lista_baudios_seleccionables[input_baudios_idx]
                            guardado_exitoso = guardar_configuracion_serial(input_puerto_str, nuevos_baudios)
                            if guardado_exitoso:
                                puerto = input_puerto_str
                                baudios = nuevos_baudios
                                if ser and ser.is_open:
                                    ser.close()
                                try:
                                    ser = serial.Serial(puerto, baudios, timeout=2) # Timeout aumentado
                                    serial_port_available = True
                                except serial.SerialException as e:
                                    print(f"Error reabriendo puerto serial {puerto} con {baudios} baud: {e}")
                                    serial_port_available = False
                                except Exception as e: # Captura de error más genérica
                                    print(f"Error inesperado reabriendo puerto: {e}")
                                    serial_port_available = False
                                
                                mostrar_ventana_config_serial = False
                                puerto_dropdown_activo = False; globals()['puerto_dropdown_activo'] = False
                                baudios_dropdown_activo = False; globals()['baudios_dropdown_activo'] = False
                        
                        else: # Clic fuera de los elementos interactivos de la config window
                            puerto_dropdown_activo = False; globals()['puerto_dropdown_activo'] = False
                            baudios_dropdown_activo = False; globals()['baudios_dropdown_activo'] = False
                    
                    # Manejo de clic en ventana de alarma
                    elif mostrar_ventana_alarma:
                        # Asegurarse que los rects de botones/inputs de alarma están definidos si la ventana es visible
                        # (Se definen más adelante en la sección de dibujado, idealmente deberían definirse antes del bucle de eventos si se usan aquí)
                        # Por ahora, asumimos que si la ventana es visible, estos rects existen.
                        # Para robustez, añadir chequeos de if rect_boton_salir_alarma: etc.
                        if 'rect_boton_salir_alarma' in locals() and rect_boton_salir_alarma.collidepoint(evento.pos):
                            mostrar_ventana_alarma = False
                            input_alarma_activo = None
                        
                        elif 'rect_boton_guardar_alarma' in locals() and rect_boton_guardar_alarma.collidepoint(evento.pos):
                            try:
                                pitch_ui_val_str = valores_ui_input_alarma["pitch"]
                                roll_ui_val_str = valores_ui_input_alarma["roll"]
                                # Usar 15 como default si el string está vacío
                                pitch_val_to_save = int(pitch_ui_val_str) if pitch_ui_val_str else 15
                                roll_val_to_save = int(roll_ui_val_str) if roll_ui_val_str else 15
                                
                                # Actualizar el diccionario de UI con el valor que se va a guardar (validado o default)
                                valores_ui_input_alarma["pitch"] = str(pitch_val_to_save)
                                valores_ui_input_alarma["roll"] = str(roll_val_to_save)
                                
                                if guardar_configuracion_alarma(): # guardar_config_alarma ahora usa valores_ui_input_alarma
                                    mostrar_ventana_alarma = False
                                    input_alarma_activo = None
                            except ValueError: # Si la conversión a int falla (e.g. string no numérico)
                                print("DEBUG: Error de valor en inputs de alarma. No numérico.")
                        
                        elif 'rect_input_pitch_alarma' in locals() and rect_input_pitch_alarma.collidepoint(evento.pos):
                            input_alarma_activo = "pitch"
                        
                        elif 'rect_input_roll_alarma' in locals() and rect_input_roll_alarma.collidepoint(evento.pos):
                            input_alarma_activo = "roll"
                        
                        else: # Clic fuera de los elementos interactivos de la ventana de alarma
                            input_alarma_activo = None
                    
                    # Manejo de clic en ventana Acerca de
                    elif mostrar_ventana_acerca_de:
                        if 'rect_boton_cerrar_acerca_de' in locals() and rect_boton_cerrar_acerca_de.collidepoint(evento.pos):
                            mostrar_ventana_acerca_de = False
            
            # Manejo de entrada de teclado
            if evento.type == pygame.KEYDOWN:
                if mostrar_ventana_config_serial:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_config_serial = False
                        puerto_dropdown_activo = False # Resetear estado dropdown
                        globals()['puerto_dropdown_activo'] = False
                
                elif mostrar_ventana_alarma and input_alarma_activo:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_alarma = False
                        input_alarma_activo = None
                    elif evento.key == pygame.K_BACKSPACE:
                        valores_ui_input_alarma[input_alarma_activo] = valores_ui_input_alarma[input_alarma_activo][:-1]
                    elif evento.unicode.isdigit():
                        # Permitir solo 2 dígitos para pitch/roll
                        if len(valores_ui_input_alarma[input_alarma_activo]) < 2:
                            valores_ui_input_alarma[input_alarma_activo] += evento.unicode
                
                elif mostrar_ventana_password_servicio and input_password_activo:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_password_servicio = False
                        input_password_str = ""
                        intento_password_fallido = False
                        input_password_activo = False
                    elif evento.key == pygame.K_RETURN or evento.key == pygame.K_KP_ENTER:
                        # Simular clic en botón "Entrar"
                        if input_password_str == CLAVE_ACCESO_SERVICIO:
                            mostrar_ventana_password_servicio = False
                            mostrar_ventana_servicio_datos = True
                            input_password_str = ""
                            intento_password_fallido = False
                            input_password_activo = False
                            input_api_key_thingspeak_str = API_KEY_THINGSPEAK
                            input_api_key_google_cloud_str = API_KEY_GOOGLE_CLOUD
                        else:
                            input_password_str = ""
                            intento_password_fallido = True
                            # input_password_activo podría mantenerse True o False aquí
                    elif evento.key == pygame.K_BACKSPACE:
                        input_password_str = input_password_str[:-1]
                    elif evento.unicode.isprintable():
                        if len(input_password_str) < 50: # Limitar longitud de contraseña
                             input_password_str += evento.unicode

                elif mostrar_ventana_servicio_datos and input_servicio_activo:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_servicio_datos = False
                        input_servicio_activo = None
                    elif evento.key == pygame.K_BACKSPACE:
                        if input_servicio_activo == "thingspeak":
                            input_api_key_thingspeak_str = input_api_key_thingspeak_str[:-1]
                        elif input_servicio_activo == "google_cloud":
                            input_api_key_google_cloud_str = input_api_key_google_cloud_str[:-1]
                    elif evento.unicode.isprintable(): # Aceptar cualquier caracter imprimible para API keys
                        if input_servicio_activo == "thingspeak":
                            if len(input_api_key_thingspeak_str) < 50: # Limitar longitud
                                input_api_key_thingspeak_str += evento.unicode
                        elif input_servicio_activo == "google_cloud":
                            if len(input_api_key_google_cloud_str) < 200: # Limitar longitud (Google keys pueden ser largas)
                                input_api_key_google_cloud_str += evento.unicode

                elif mostrar_ventana_idioma:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_idioma = False
                
                elif mostrar_ventana_acerca_de:
                    if evento.key == pygame.K_ESCAPE:
                        mostrar_ventana_acerca_de = False
        
        # Reconexión automática si el puerto no está disponible
        if not serial_port_available and not mostrar_ventana_config_serial and not mostrar_ventana_alarma and not mostrar_ventana_acerca_de and not mostrar_ventana_idioma:
            ahora = pygame.time.get_ticks()
            if ahora - ultimo_intento_reconeccion_tiempo > INTERVALO_RECONECCION_MS:
                ultimo_intento_reconeccion_tiempo = ahora
                try:
                    if ser: # Si el objeto ser existe
                        try:
                            if ser.is_open:
                                ser.close()
                                print(f"INFO: Puerto {ser.portstr if ser.portstr else puerto} cerrado antes de reconectar.")
                        except Exception as e_close:
                            print(f"ERROR: Al intentar cerrar el puerto {ser.portstr if ser.portstr else puerto} antes de reconectar: {e_close}")
                        ser = None # Asegurarse de que ser es None antes de intentar reabrir
                    
                    ser = serial.Serial(puerto, baudios, timeout=1)
                    serial_port_available = True
                    nmea_data_stale = False # Los datos ya no son viejos
                    ultima_vez_datos_recibidos = pygame.time.get_ticks() # Resetear temporizador de "NO HAY DATOS"
                    print(f"INFO: Reconexión exitosa al puerto {puerto} a {baudios} baudios.")
                except serial.SerialException as e_reconect:
                    # print(f"Fallo reconexión: {e_reconect}") # Mantenido comentado para no ser muy verboso
                    ser = None # Asegurarse que ser es None si falla
                    serial_port_available = False
                except Exception as e_reconect_general: # Capturar otros posibles errores
                    print(f"Error inesperado durante reconexión: {e_reconect_general}")
                    ser = None
                    serial_port_available = False
        
        # Lectura de datos del puerto serial
        if serial_port_available and ser and ser.is_open:
            try:
                if ser.in_waiting > 0:
                    line = ser.readline().decode('ascii', errors='replace').strip()
                    if line.startswith('$GPGLL') or line.startswith('$GNGLL'):
                        parse_gll(line) # Assuming you have or will create this function
                        pass # Placeholder if parse_gll is not defined
                    elif line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                        parse_gga(line)
                    elif line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                        parse_rmc(line)
                    elif line.startswith('$GPVTG') or line.startswith('$GNVTG'):
                        parse_vtg(line)
                    elif line.startswith('$GPHDT') or line.startswith('$GNHDT'):
                        parse_hdt(line)
                    elif line.startswith('$GPHDG') or line.startswith('$GNHDG'):
                        parse_hdg(line)
                    elif line.startswith('$PFEC,GPatt'):
                        parse_pfec_gpatt(line)
                    elif line.startswith('$GPZDA') or line.startswith('$GNZDA'):
                        parse_gpzda(line)
            except serial.SerialException as se:
                print(f"SerialException durante lectura: {se}. Marcando puerto como desconectado.")
                if ser:
                    ser.close()
                ser = None
                serial_port_available = False
                ultimo_intento_reconeccion_tiempo = pygame.time.get_ticks() # Para intentar reconectar pronto
                reset_ui_data() # Limpiar datos en pantalla
                nmea_data_stale = True 
            except Exception as e: # Otros errores de lectura/decode
                # print(f"Error general durante lectura/procesamiento NMEA: {e}")
                pass # Continuar, puede ser un error puntual de datos
        
        # Envío periódico de datos a ThingSpeak y CSV
        if time.time() - ultima_vez_envio_datos >= INTERVALO_ENVIO_DATOS_S:
            if serial_port_available and not nmea_data_stale: # Solo enviar si hay datos frescos
                estado_alarma_para_print = "SIN ALARMA"
                if alarma_roll_babor_activa:
                    estado_alarma_para_print = "ALARMA BABOR"
                elif alarma_roll_estribor_activa: # Usar elif para que no se sobreescriban si ambas son true (aunque la lógica de activación debería prevenirlo)
                    estado_alarma_para_print = "ALARMA ESTRIBOR"
                
                # Combinar alarmas de pitch si están activas
                if alarma_pitch_sentado_activa:
                    estado_alarma_para_print += " Y SENTADO" if "ALARMA" in estado_alarma_para_print else "ALARMA SENTADO"
                elif alarma_pitch_encabuzado_activa:
                    estado_alarma_para_print += " Y ENCABUZADO" if "ALARMA" in estado_alarma_para_print else "ALARMA ENCABUZADO"
                
                print(f"--- Guardando y Enviando Datos ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
                print(f"Valores: P:{ts_pitch_float}, R:{ts_roll_float}, Lat:{ts_lat_decimal}, Lon:{ts_lon_decimal}, Spd:{ts_speed_float}, Hdg:{ts_heading_float}, TS:{ts_timestamp_str}, Alarma: {estado_alarma_para_print}")
                
                guardar_csv()
                if SERVICIO_DATOS_ACTUAL == "thingspeak":
                    enviar_thingspeak()
                elif SERVICIO_DATOS_ACTUAL == "google_cloud":
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Google Cloud envío no implementado. API Key: {API_KEY_GOOGLE_CLOUD}")
                # else: # Otros servicios futuros
                    # pass
                print("---------------------------------------------------\n")
            
            ultima_vez_envio_datos = time.time() # Actualizar siempre para mantener el intervalo
        
        # Detección de condiciones de alarma
        # Usar los valores de ts_pitch_float y ts_roll_float que ya son floats
        if valores_alarma: # Asegurarse que valores_alarma está cargado
            try:
                # Roll
                umbral_min_roll_float = float(valores_alarma["min_roll_neg"])
                umbral_max_roll_float = float(valores_alarma["max_roll_pos"])
                
                # Alarma Roll Babor
                condicion_babor = ts_roll_float < umbral_min_roll_float if att_roll_str != "N/A" else False
                if condicion_babor and not alarma_roll_babor_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ROLL_BABOR", "ACTIVANDO", ts_roll_float, umbral_min_roll_float)
                elif not condicion_babor and alarma_roll_babor_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ROLL_BABOR", "DESACTIVANDO", ts_roll_float, umbral_min_roll_float)
                alarma_roll_babor_activa = condicion_babor

                # Alarma Roll Estribor
                condicion_estribor = ts_roll_float > umbral_max_roll_float if att_roll_str != "N/A" else False
                if condicion_estribor and not alarma_roll_estribor_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ROLL_ESTRIBOR", "ACTIVANDO", ts_roll_float, umbral_max_roll_float)
                elif not condicion_estribor and alarma_roll_estribor_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ROLL_ESTRIBOR", "DESACTIVANDO", ts_roll_float, umbral_max_roll_float)
                alarma_roll_estribor_activa = condicion_estribor

                # Pitch
                umbral_min_pitch_float = float(valores_alarma["min_pitch_neg"])
                umbral_max_pitch_float = float(valores_alarma["max_pitch_pos"])

                # Alarma Pitch Encabuzado
                condicion_encabuzado = ts_pitch_float < umbral_min_pitch_float if att_pitch_str != "N/A" else False
                if condicion_encabuzado and not alarma_pitch_encabuzado_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "PITCH_ENCABUZADO", "ACTIVANDO", ts_pitch_float, umbral_min_pitch_float)
                elif not condicion_encabuzado and alarma_pitch_encabuzado_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "PITCH_ENCABUZADO", "DESACTIVANDO", ts_pitch_float, umbral_min_pitch_float)
                alarma_pitch_encabuzado_activa = condicion_encabuzado
                
                # Alarma Pitch Sentado
                condicion_sentado = ts_pitch_float > umbral_max_pitch_float if att_pitch_str != "N/A" else False
                if condicion_sentado and not alarma_pitch_sentado_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "PITCH_SENTADO", "ACTIVANDO", ts_pitch_float, umbral_max_pitch_float)
                elif not condicion_sentado and alarma_pitch_sentado_activa:
                    guardar_alarma_csv(ts_timestamp_str if ts_timestamp_str != "N/A" else datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "PITCH_SENTADO", "DESACTIVANDO", ts_pitch_float, umbral_max_pitch_float)
                alarma_pitch_sentado_activa = condicion_sentado
                
            except (ValueError, KeyError) as e: # Si hay error en conversión o claves
                # print(f"Error al procesar umbrales de alarma: {e}")
                alarma_roll_babor_activa = False
                alarma_roll_estribor_activa = False
                alarma_pitch_sentado_activa = False
                alarma_pitch_encabuzado_activa = False
        
        # Manejo de reproducción de alarmas
        alarmas_activas_para_sonar = []
        if alarma_roll_babor_activa:
            alarmas_activas_para_sonar.append(('roll_babor', ultima_reproduccion_alarma_babor_tiempo))
        if alarma_roll_estribor_activa:
            alarmas_activas_para_sonar.append(('roll_estribor', ultima_reproduccion_alarma_estribor_tiempo))
        if alarma_pitch_sentado_activa:
            alarmas_activas_para_sonar.append(('pitch_sentado', ultima_reproduccion_alarma_sentado_tiempo))
        if alarma_pitch_encabuzado_activa:
            alarmas_activas_para_sonar.append(('pitch_encabuzado', ultima_reproduccion_alarma_encabuzado_tiempo))
        
        # Si no hay alarmas activas, detener cualquier sonido que esté reproduciéndose
        if not alarmas_activas_para_sonar:
            if sonido_alarma_actualmente_reproduciendo is not None:
                sonido_alarma_actualmente_reproduciendo.stop()
                sonido_alarma_actualmente_reproduciendo = None
            INDICE_PROXIMA_ALARMA_A_SONAR = 0 # Resetear índice
        else:
            # Manejar reproducción de alarmas activas
            ahora = time.time()
            
            # Verificar si el sonido actual ha terminado o ha pasado el tiempo de pausa para alternar
            puede_reproducir_nueva_alarma = True
            if sonido_alarma_actualmente_reproduciendo is not None:
                try:
                    duracion_sonido_actual = sonido_alarma_actualmente_reproduciendo.get_length()
                    # Si el sonido está activo y no ha pasado su duración + pausa, no reproducir nueva
                    if pygame.mixer.get_busy() and (ahora < tiempo_ultimo_sonido_iniciado + duracion_sonido_actual + PAUSA_ENTRE_SONIDOS_ALTERNADOS_S):
                        puede_reproducir_nueva_alarma = False
                except pygame.error: # Si el sonido ya no es válido
                    sonido_alarma_actualmente_reproduciendo = None # Resetear

            if puede_reproducir_nueva_alarma:
                # Filtrar las alarmas que realmente necesitan sonar (cuyo intervalo ha pasado)
                alarmas_que_deben_sonar_ahora = []
                for tipo_alarma, ultima_vez_reproduccion in alarmas_activas_para_sonar:
                    intervalo_repeticion = INTERVALO_REPETICION_ALARMA_PITCH_S if 'pitch' in tipo_alarma else INTERVALO_REPETICION_ALARMA_ROLL_S
                    if ahora - ultima_vez_reproduccion >= intervalo_repeticion:
                        alarmas_que_deben_sonar_ahora.append(tipo_alarma)
                
                if alarmas_que_deben_sonar_ahora:
                    if INDICE_PROXIMA_ALARMA_A_SONAR >= len(alarmas_que_deben_sonar_ahora):
                        INDICE_PROXIMA_ALARMA_A_SONAR = 0
                    
                    tipo_alarma_a_reproducir = alarmas_que_deben_sonar_ahora[INDICE_PROXIMA_ALARMA_A_SONAR]
                    
                    if reproducir_alarma(tipo_alarma_a_reproducir):
                        # Actualizar el tiempo de última reproducción para ESTA alarma específica
                        if tipo_alarma_a_reproducir == 'roll_babor':
                            ultima_reproduccion_alarma_babor_tiempo = ahora
                        elif tipo_alarma_a_reproducir == 'roll_estribor':
                            ultima_reproduccion_alarma_estribor_tiempo = ahora
                        elif tipo_alarma_a_reproducir == 'pitch_sentado':
                            ultima_reproduccion_alarma_sentado_tiempo = ahora
                        elif tipo_alarma_a_reproducir == 'pitch_encabuzado':
                            ultima_reproduccion_alarma_encabuzado_tiempo = ahora
                        
                        INDICE_PROXIMA_ALARMA_A_SONAR = (INDICE_PROXIMA_ALARMA_A_SONAR + 1) % len(alarmas_que_deben_sonar_ahora)
        
        # Renderizado
        if imagen_fondo_escalada:
            screen.blit(imagen_fondo_escalada, (0, 0))
        else:
            screen.fill(AZUL) # Color de fondo por defecto si no hay imagen
        
        # Actualizar título de la ventana principal con el idioma actual
        pygame.display.set_caption(TEXTOS[IDIOMA]["titulo_ventana"])
        
        # Dibujar barra de herramientas si está visible
        if toolbar_visible: # Solo dibujar si el mouse está encima
            pygame.draw.rect(screen, COLOR_BARRA_HERRAMIENTAS_FONDO, rect_barra_herramientas)
            pygame.draw.rect(screen, COLOR_BARRA_HERRAMIENTAS_BORDE, rect_barra_herramientas, 1) # Borde
            
            rects_opciones_menu_barra.clear() # Limpiar rects anteriores
            current_x_menu_draw = padding_menu_x # Posición X inicial para el primer item del menú
            
            for opcion_texto in opciones_menu_barra: # Usar la lista actualizada
                texto_surf = font_bar_herramientas.render(opcion_texto, True, COLOR_ITEM_MENU_TEXTO)
                texto_rect = texto_surf.get_rect(left=current_x_menu_draw, centery=rect_barra_herramientas.centery)
                
                # Crear un Rect más grande para la detección de clics, centrado en el texto
                clickable_rect = texto_rect.inflate(padding_menu_x * 2, ALTURA_BARRA_HERRAMIENTAS // 3) # Padding horizontal y un poco vertical
                clickable_rect.centery = rect_barra_herramientas.centery # Asegurar centrado vertical
                
                rects_opciones_menu_barra.append(clickable_rect)
                screen.blit(texto_surf, texto_rect)
                current_x_menu_draw += texto_rect.width + espacio_entre_menus + padding_menu_x # Mover X para el siguiente item
        
        # Dibujar círculos para pitch y roll
        pygame.draw.circle(screen, BLANCO, (centro_x_circulo1, centro_y_circulos), radio_circulo_img, 2) # Círculo Pitch
        pygame.draw.circle(screen, BLANCO, (centro_x_circulo2, centro_y_circulos), radio_circulo_img, 2) # Círculo Roll
        
        # Dibujar indicador de pitch
        if PROGRAM_MODE != "TRIAL_EXPIRED":
            if pitch_image_base_grande and att_pitch_str != "N/A":
                try:
                    valor_pitch_float = float(att_pitch_str)
                    angulo_rotacion_pygame = -valor_pitch_float
                    imagen_pitch_rotada_grande = pygame.transform.rotate(pitch_image_base_grande, angulo_rotacion_pygame) # Asegurar esta línea y las siguientes están indentadas un nivel más que el try
                    diametro_claraboya = 2 * radio_circulo_img
                    claraboya_surface = pygame.Surface((diametro_claraboya, diametro_claraboya), pygame.SRCALPHA)
                    claraboya_surface.fill((0,0,0,0))
                    offset_x = (diametro_claraboya - imagen_pitch_rotada_grande.get_width()) // 2
                    offset_y = (diametro_claraboya - imagen_pitch_rotada_grande.get_height()) // 2
                    claraboya_surface.blit(imagen_pitch_rotada_grande, (offset_x, offset_y))
                    mask = pygame.Surface((diametro_claraboya, diametro_claraboya), pygame.SRCALPHA)
                    mask.fill((0,0,0,0))
                    pygame.draw.circle(mask, (255,255,255,255), (radio_circulo_img, radio_circulo_img), radio_circulo_img)
                    claraboya_surface.blit(mask, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
                    rect_claraboya_final = claraboya_surface.get_rect(center=(centro_x_circulo1, centro_y_circulos))
                    screen.blit(claraboya_surface, rect_claraboya_final)
                except ValueError:
                    pass # Si float(att_pitch_str) falla, no se dibuja la imagen.
            
            pygame.draw.circle(screen, BLANCO, (centro_x_circulo1, centro_y_circulos), radio_circulo_img, 2)
            
            for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
                angle_rad = math.radians(angle_deg)
                x_inicio_marca = centro_x_circulo1 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
                y_inicio_marca = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad)
                x_fin_marca = centro_x_circulo1 + RADIO_FIN_MARCAS * math.cos(angle_rad)
                y_fin_marca = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad)
                pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca, y_inicio_marca), (x_fin_marca, y_fin_marca), GROSOR_MARCA_GRADO)
                etiqueta_surf = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
                x_texto_etiqueta = centro_x_circulo1 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
                y_texto_etiqueta = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad)
                etiqueta_rect = etiqueta_surf.get_rect(center=(int(x_texto_etiqueta), int(y_texto_etiqueta)))
                screen.blit(etiqueta_surf, etiqueta_rect)

            if att_pitch_str != "N/A":
                try:
                    valor_pitch_float = float(att_pitch_str)
                    pitch_valor_surf = font_circulos_textos.render(f"{valor_pitch_float:+.1f}°", True, BLANCO)
                    y_pos_texto_pitch = centro_y_circulos + radio_circulo_img * 0.0282
                    pitch_valor_rect = pitch_valor_surf.get_rect(center=(centro_x_circulo1, y_pos_texto_pitch))
                    screen.blit(pitch_valor_surf, pitch_valor_rect)
                    
                    pos_flecha_pitch_x = pitch_valor_rect.left - OFFSET_FLECHA_TEXTO - (LONGITUD_FLECHA_DIR // 2)
                    pos_flecha_pitch_y_centro = pitch_valor_rect.centery
                    if valor_pitch_float > 0.1:
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2)
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x - ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2)
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x + ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2)
                    elif valor_pitch_float < -0.1:
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2)
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x - ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2)
                        pygame.draw.line(screen, BLANCO, (pos_flecha_pitch_x + ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2), (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2)
                except ValueError:
                    pass
        else: # PROGRAM_MODE == "TRIAL_EXPIRED"
            disabled_text_surf = font.render(TEXTOS[IDIOMA]["data_disabled_trial"], True, ROJO)
            disabled_text_rect = disabled_text_surf.get_rect(center=(centro_x_circulo1, centro_y_circulos))
            screen.blit(disabled_text_surf, disabled_text_rect)
            # Dibujar marcas y etiquetas para pitch (siempre visibles)
            for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
                angle_rad = math.radians(angle_deg)
                x_inicio_marca = centro_x_circulo1 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
                y_inicio_marca = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad)
                x_fin_marca = centro_x_circulo1 + RADIO_FIN_MARCAS * math.cos(angle_rad)
                y_fin_marca = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad)
                pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca, y_inicio_marca), (x_fin_marca, y_fin_marca), GROSOR_MARCA_GRADO)
                etiqueta_surf = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
                x_texto_etiqueta = centro_x_circulo1 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
                y_texto_etiqueta = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad)
                etiqueta_rect = etiqueta_surf.get_rect(center=(int(x_texto_etiqueta), int(y_texto_etiqueta)))
                screen.blit(etiqueta_surf, etiqueta_rect)

        # Dibujar indicador de roll
        if PROGRAM_MODE != "TRIAL_EXPIRED":
            if roll_image_base_grande and att_roll_str != "N/A":
                try:
                    valor_roll_float = float(att_roll_str)
                    angulo_rotacion_pygame_roll = -valor_roll_float
                    # Correctamente indentado dentro del try:
                    imagen_roll_rotada_grande = pygame.transform.rotate(roll_image_base_grande, angulo_rotacion_pygame_roll)
                    diametro_claraboya_roll = 2 * radio_circulo_img
                    claraboya_surface_roll = pygame.Surface((diametro_claraboya_roll, diametro_claraboya_roll), pygame.SRCALPHA)
                    claraboya_surface_roll.fill((0,0,0,0))
                    offset_x_roll = (diametro_claraboya_roll - imagen_roll_rotada_grande.get_width()) // 2
                    offset_y_roll = (diametro_claraboya_roll - imagen_roll_rotada_grande.get_height()) // 2
                    claraboya_surface_roll.blit(imagen_roll_rotada_grande, (offset_x_roll, offset_y_roll))
                    mask_roll = pygame.Surface((diametro_claraboya_roll, diametro_claraboya_roll), pygame.SRCALPHA)
                    mask_roll.fill((0,0,0,0))
                    pygame.draw.circle(mask_roll, (255,255,255,255), (radio_circulo_img, radio_circulo_img), radio_circulo_img)
                    claraboya_surface_roll.blit(mask_roll, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
                    rect_claraboya_final_roll = claraboya_surface_roll.get_rect(center=(centro_x_circulo2, centro_y_circulos))
                    screen.blit(claraboya_surface_roll, rect_claraboya_final_roll)
                except ValueError:
                    pass # Si float(att_roll_str) falla, no se dibuja la imagen.
            
            pygame.draw.circle(screen, BLANCO, (centro_x_circulo2, centro_y_circulos), radio_circulo_img, 2)
            
            for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
                angle_rad = math.radians(angle_deg)
                x_inicio_marca_roll = centro_x_circulo2 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
                y_inicio_marca_roll = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad)
                x_fin_marca_roll = centro_x_circulo2 + RADIO_FIN_MARCAS * math.cos(angle_rad)
                y_fin_marca_roll = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad)
                pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca_roll, y_inicio_marca_roll), (x_fin_marca_roll, y_fin_marca_roll), GROSOR_MARCA_GRADO)
                etiqueta_surf_roll = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
                x_texto_etiqueta_roll = centro_x_circulo2 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
                y_texto_etiqueta_roll = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad)
                etiqueta_rect_roll = etiqueta_surf_roll.get_rect(center=(int(x_texto_etiqueta_roll), int(y_texto_etiqueta_roll)))
                screen.blit(etiqueta_surf_roll, etiqueta_rect_roll)

            if att_roll_str != "N/A":
                try:
                    valor_roll_float = float(att_roll_str)
                    roll_valor_surf = font_circulos_textos.render(f"{valor_roll_float:+.1f}°", True, BLANCO)
                    y_pos_texto_roll = centro_y_circulos + radio_circulo_img * 0.0282
                    roll_valor_rect = roll_valor_surf.get_rect(center=(centro_x_circulo2, y_pos_texto_roll))
                    screen.blit(roll_valor_surf, roll_valor_rect)
                    
                    letra_roll_str = ""
                    if valor_roll_float > 0.1: letra_roll_str = "S"
                    elif valor_roll_float < -0.1: letra_roll_str = "P"
                    if letra_roll_str:
                        letra_roll_surf = font_circulos_textos.render(letra_roll_str, True, COLOR_LETRA_ROLL)
                        letra_roll_rect = letra_roll_surf.get_rect(midtop=(roll_valor_rect.centerx, roll_valor_rect.bottom + OFFSET_LETRA_ROLL_Y))
                        screen.blit(letra_roll_surf, letra_roll_rect)

                    pos_flecha_roll_y_centro = roll_valor_rect.centery
                    if valor_roll_float > 0.1:
                        pos_flecha_roll_x = roll_valor_rect.right + OFFSET_FLECHA_TEXTO + (LONGITUD_FLECHA_DIR // 2)
                        pygame.draw.line(screen, VERDE, (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                        pygame.draw.line(screen, VERDE, (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro - ANCHO_FLECHA_DIR // 2), (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                        pygame.draw.line(screen, VERDE, (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro + ANCHO_FLECHA_DIR // 2), (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                    elif valor_roll_float < -0.1:
                        pos_flecha_roll_x = roll_valor_rect.left - OFFSET_FLECHA_TEXTO - (LONGITUD_FLECHA_DIR // 2)
                        pygame.draw.line(screen, ROJO, (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                        pygame.draw.line(screen, ROJO, (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro - ANCHO_FLECHA_DIR // 2), (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                        pygame.draw.line(screen, ROJO, (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro + ANCHO_FLECHA_DIR // 2), (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2)
                except ValueError:
                    pass
        else: # PROGRAM_MODE == "TRIAL_EXPIRED"
            disabled_text_surf = font.render(TEXTOS[IDIOMA]["data_disabled_trial"], True, ROJO)
            disabled_text_rect = disabled_text_surf.get_rect(center=(centro_x_circulo2, centro_y_circulos))
            screen.blit(disabled_text_surf, disabled_text_rect)
            
            for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
                angle_rad = math.radians(angle_deg)
                x_inicio_marca_roll = centro_x_circulo2 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
                y_inicio_marca_roll = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad)
                x_fin_marca_roll = centro_x_circulo2 + RADIO_FIN_MARCAS * math.cos(angle_rad)
                y_fin_marca_roll = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad)
                pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca_roll, y_inicio_marca_roll), (x_fin_marca_roll, y_fin_marca_roll), GROSOR_MARCA_GRADO)
                etiqueta_surf_roll = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
                x_texto_etiqueta_roll = centro_x_circulo2 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
                y_texto_etiqueta_roll = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad)
                etiqueta_rect_roll = etiqueta_surf_roll.get_rect(center=(int(x_texto_etiqueta_roll), int(y_texto_etiqueta_roll)))
                screen.blit(etiqueta_surf_roll, etiqueta_rect_roll)

        # Dibujar cajas de datos
        espacio_entre_cajas_vertical = 10
        ancho_cajas_datos = 280 # Ancho fijo para las 3 cajas
                imagen_pitch_rotada_grande = pygame.transform.rotate(pitch_image_base_grande, angulo_rotacion_pygame)
                
                # Crear "claraboya" (máscara circular)
                diametro_claraboya = 2 * radio_circulo_img
                claraboya_surface = pygame.Surface((diametro_claraboya, diametro_claraboya), pygame.SRCALPHA) # Superficie transparente
                claraboya_surface.fill((0,0,0,0)) # Llenar con transparencia
                
                # Centrar la imagen rotada en la superficie de la claraboya
                offset_x = (diametro_claraboya - imagen_pitch_rotada_grande.get_width()) // 2
                offset_y = (diametro_claraboya - imagen_pitch_rotada_grande.get_height()) // 2
                claraboya_surface.blit(imagen_pitch_rotada_grande, (offset_x, offset_y))
                
                # Crear máscara circular
                mask = pygame.Surface((diametro_claraboya, diametro_claraboya), pygame.SRCALPHA)
                mask.fill((0,0,0,0)) # Transparente por defecto
                pygame.draw.circle(mask, (255,255,255,255), (radio_circulo_img, radio_circulo_img), radio_circulo_img) # Círculo opaco
                
                # Aplicar máscara a la claraboya (mostrar solo la parte circular de la imagen rotada)
                claraboya_surface.blit(mask, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
                
                # Dibujar la claraboya final en la pantalla
                rect_claraboya_final = claraboya_surface.get_rect(center=(centro_x_circulo1, centro_y_circulos))
                screen.blit(claraboya_surface, rect_claraboya_final)
            except ValueError:
                pass # Si att_pitch_str no es un float válido
        
        pygame.draw.circle(screen, BLANCO, (centro_x_circulo1, centro_y_circulos), radio_circulo_img, 2) # Redibujar borde por si la imagen lo tapa
        
        # Dibujar marcas y etiquetas para pitch
        for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
            angle_rad = math.radians(angle_deg) # Convertir a radianes
            # Calcular puntos de inicio y fin de la marca
            x_inicio_marca = centro_x_circulo1 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
            y_inicio_marca = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad) # Sumar para Y (hacia abajo)
            x_fin_marca = centro_x_circulo1 + RADIO_FIN_MARCAS * math.cos(angle_rad)
            y_fin_marca = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad) # Sumar para Y
            
            pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca, y_inicio_marca), (x_fin_marca, y_fin_marca), GROSOR_MARCA_GRADO)
            
            # Posicionar texto de etiqueta
            etiqueta_surf = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
            x_texto_etiqueta = centro_x_circulo1 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
            y_texto_etiqueta = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad) # Sumar para Y
            
            etiqueta_rect = etiqueta_surf.get_rect(center=(int(x_texto_etiqueta), int(y_texto_etiqueta)))
            screen.blit(etiqueta_surf, etiqueta_rect)
        
        # Mostrar valor de pitch
        if att_pitch_str != "N/A":
            try:
                valor_pitch_float = float(att_pitch_str)
                pitch_valor_surf = font_circulos_textos.render(f"{valor_pitch_float:+.1f}°", True, BLANCO)
                # Ajustar la posición Y del texto del valor de pitch para que esté más centrado
                y_pos_texto_pitch = centro_y_circulos + radio_circulo_img * 0.0282 # Ajuste fino basado en pruebas
                pitch_valor_rect = pitch_valor_surf.get_rect(center=(centro_x_circulo1, y_pos_texto_pitch))
                screen.blit(pitch_valor_surf, pitch_valor_rect)
                
                # Dibujar flecha de dirección para Pitch
                pos_flecha_pitch_x = pitch_valor_rect.left - OFFSET_FLECHA_TEXTO - (LONGITUD_FLECHA_DIR // 2)
                pos_flecha_pitch_y_centro = pitch_valor_rect.centery
                
                if valor_pitch_float > 0.1: # Flecha hacia arriba (positivo)
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2) # Palo vertical
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x - ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2) # Punta izquierda
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x + ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2), 2) # Punta derecha
                elif valor_pitch_float < -0.1: # Flecha hacia abajo (negativo)
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro - LONGITUD_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2) # Palo vertical
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x - ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2) # Punta izquierda
                    pygame.draw.line(screen, BLANCO, 
                                   (pos_flecha_pitch_x + ANCHO_FLECHA_DIR // 2, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_pitch_x, pos_flecha_pitch_y_centro + LONGITUD_FLECHA_DIR // 2), 2) # Punta derecha
            except ValueError:
                pass
        
        # Dibujar indicador de roll
        if roll_image_base_grande and att_roll_str != "N/A":
            try:
                valor_roll_float = float(att_roll_str)
                angulo_rotacion_pygame_roll = -valor_roll_float # Negativo porque Pygame rota antihorario
                imagen_roll_rotada_grande = pygame.transform.rotate(roll_image_base_grande, angulo_rotacion_pygame_roll)
                
                diametro_claraboya_roll = 2 * radio_circulo_img
                claraboya_surface_roll = pygame.Surface((diametro_claraboya_roll, diametro_claraboya_roll), pygame.SRCALPHA)
                claraboya_surface_roll.fill((0,0,0,0))
                
                offset_x_roll = (diametro_claraboya_roll - imagen_roll_rotada_grande.get_width()) // 2
                offset_y_roll = (diametro_claraboya_roll - imagen_roll_rotada_grande.get_height()) // 2
                claraboya_surface_roll.blit(imagen_roll_rotada_grande, (offset_x_roll, offset_y_roll))
                
                mask_roll = pygame.Surface((diametro_claraboya_roll, diametro_claraboya_roll), pygame.SRCALPHA)
                mask_roll.fill((0,0,0,0))
                pygame.draw.circle(mask_roll, (255,255,255,255), (radio_circulo_img, radio_circulo_img), radio_circulo_img)
                claraboya_surface_roll.blit(mask_roll, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
                
                rect_claraboya_final_roll = claraboya_surface_roll.get_rect(center=(centro_x_circulo2, centro_y_circulos))
                screen.blit(claraboya_surface_roll, rect_claraboya_final_roll)
            except ValueError:
                pass
        
        pygame.draw.circle(screen, BLANCO, (centro_x_circulo2, centro_y_circulos), radio_circulo_img, 2) # Redibujar borde
        
        # Dibujar marcas y etiquetas para roll (similar a pitch)
        for key, (angle_deg, etiqueta_str) in ANGULOS_MARCAS_ETIQUETAS_DEF.items():
            angle_rad = math.radians(angle_deg)
            x_inicio_marca_roll = centro_x_circulo2 + RADIO_INICIO_MARCAS * math.cos(angle_rad)
            y_inicio_marca_roll = centro_y_circulos + RADIO_INICIO_MARCAS * math.sin(angle_rad)
            x_fin_marca_roll = centro_x_circulo2 + RADIO_FIN_MARCAS * math.cos(angle_rad)
            y_fin_marca_roll = centro_y_circulos + RADIO_FIN_MARCAS * math.sin(angle_rad)
            pygame.draw.line(screen, COLOR_MARCA_GRADO, (x_inicio_marca_roll, y_inicio_marca_roll), (x_fin_marca_roll, y_fin_marca_roll), GROSOR_MARCA_GRADO)
            
            etiqueta_surf_roll = font.render(etiqueta_str, True, COLOR_ETIQUETA_GRADO)
            x_texto_etiqueta_roll = centro_x_circulo2 + RADIO_POSICION_TEXTO_ETIQUETA * math.cos(angle_rad)
            y_texto_etiqueta_roll = centro_y_circulos + RADIO_POSICION_TEXTO_ETIQUETA * math.sin(angle_rad)
            etiqueta_rect_roll = etiqueta_surf_roll.get_rect(center=(int(x_texto_etiqueta_roll), int(y_texto_etiqueta_roll)))
            screen.blit(etiqueta_surf_roll, etiqueta_rect_roll)
        
        # Mostrar valor de roll
        if att_roll_str != "N/A":
            try:
                valor_roll_float = float(att_roll_str)
                roll_valor_surf = font_circulos_textos.render(f"{valor_roll_float:+.1f}°", True, BLANCO)
                y_pos_texto_roll = centro_y_circulos + radio_circulo_img * 0.0282 # Mismo ajuste Y que pitch
                roll_valor_rect = roll_valor_surf.get_rect(center=(centro_x_circulo2, y_pos_texto_roll))
                screen.blit(roll_valor_surf, roll_valor_rect)
                
                # Mostrar dirección de Roll (P/S)
                letra_roll_str = ""
                if valor_roll_float > 0.1: letra_roll_str = "S" # Estribor
                elif valor_roll_float < -0.1: letra_roll_str = "P" # Babor
                
                if letra_roll_str:
                    letra_roll_surf = font_circulos_textos.render(letra_roll_str, True, COLOR_LETRA_ROLL)
                    letra_roll_rect = letra_roll_surf.get_rect(midtop=(roll_valor_rect.centerx, roll_valor_rect.bottom + OFFSET_LETRA_ROLL_Y))
                    screen.blit(letra_roll_surf, letra_roll_rect)

                # Dibujar flecha de dirección para Roll
                pos_flecha_roll_y_centro = roll_valor_rect.centery
                if valor_roll_float > 0.1: # Flecha hacia la derecha (Estribor - Verde)
                    pos_flecha_roll_x = roll_valor_rect.right + OFFSET_FLECHA_TEXTO + (LONGITUD_FLECHA_DIR // 2)
                    pygame.draw.line(screen, VERDE, 
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro),
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Palo horizontal
                    pygame.draw.line(screen, VERDE, 
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro - ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Punta superior
                    pygame.draw.line(screen, VERDE, 
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2 - ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro + ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Punta inferior
                elif valor_roll_float < -0.1: # Flecha hacia la izquierda (Babor - Rojo)
                    pos_flecha_roll_x = roll_valor_rect.left - OFFSET_FLECHA_TEXTO - (LONGITUD_FLECHA_DIR // 2)
                    pygame.draw.line(screen, ROJO, 
                                   (pos_flecha_roll_x + LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro),
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Palo horizontal
                    pygame.draw.line(screen, ROJO, 
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro - ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Punta superior
                    pygame.draw.line(screen, ROJO, 
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2 + ANCHO_FLECHA_DIR // 2, pos_flecha_roll_y_centro + ANCHO_FLECHA_DIR // 2),
                                   (pos_flecha_roll_x - LONGITUD_FLECHA_DIR // 2, pos_flecha_roll_y_centro), 2) # Punta inferior
            except ValueError:
                pass
        
        # Dibujar cajas de datos
        espacio_entre_cajas_vertical = 10
        ancho_cajas_datos = 280 # Ancho fijo para las 3 cajas
        x_inicio_cajas_datos = area_izquierda_rect.right + 10 # A la derecha del área de círculos
        
        # Caja de Lat/Lon
        altura_caja_latlon = 120 # Altura ajustada
        y_caja_latlon = ALTURA_BARRA_HERRAMIENTAS + espacio_entre_cajas_vertical # Debajo de la barra de herramientas
        dim_caja_gll = [x_inicio_cajas_datos, y_caja_latlon, ancho_cajas_datos, altura_caja_latlon]
        
        # Caja de Rumbo/Velocidad
        altura_caja_rumbo_vel = 120 # Altura ajustada
        y_caja_rumbo_vel = y_caja_latlon + altura_caja_latlon + espacio_entre_cajas_vertical # Debajo de Lat/Lon
        dim_caja_rumbo_vel = [x_inicio_cajas_datos, y_caja_rumbo_vel, ancho_cajas_datos, altura_caja_rumbo_vel]
        
        # Caja de Cabeceo (Attitude)
        altura_caja_cabeceo = 120 # Altura ajustada
        y_caja_cabeceo = y_caja_rumbo_vel + altura_caja_rumbo_vel + espacio_entre_cajas_vertical # Debajo de Rumbo/Vel
        dim_caja_att = [x_inicio_cajas_datos, y_caja_cabeceo, ancho_cajas_datos, altura_caja_cabeceo]
        
        # Dibujar las cajas (fondo y borde)
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_FONDO, dim_caja_gll)
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_BORDE, dim_caja_gll, 1)
        
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_FONDO, dim_caja_rumbo_vel)
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_BORDE, dim_caja_rumbo_vel, 1)
        
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_FONDO, dim_caja_att)
        pygame.draw.rect(screen, COLOR_CAJA_DATOS_BORDE, dim_caja_att, 1)
        
        # Mostrar datos en las cajas (usando TEXTOS[IDIOMA])
        
        # Caja Lat/Lon
        text_surface_titulo_latlon = font.render(TEXTOS[IDIOMA]["lat_lon"], True, COLOR_CAJA_DATOS_TEXTO)
        text_rect_titulo_latlon = text_surface_titulo_latlon.get_rect(centerx=dim_caja_gll[0] + dim_caja_gll[2] // 2, top=dim_caja_gll[1] + 5)
        screen.blit(text_surface_titulo_latlon, text_rect_titulo_latlon)
        
        text_surface_lat_data = font_datos_grandes.render(latitude_str, True, COLOR_CAJA_DATOS_TEXTO)
        text_rect_lat_data = text_surface_lat_data.get_rect(centerx=dim_caja_gll[0] + dim_caja_gll[2] // 2, top=text_rect_titulo_latlon.bottom + 2) # Espacio después del título
        screen.blit(text_surface_lat_data, text_rect_lat_data)
        
        text_surface_lon_data = font_datos_grandes.render(longitude_str, True, COLOR_CAJA_DATOS_TEXTO)
        text_rect_lon_data = text_surface_lon_data.get_rect(centerx=dim_caja_gll[0] + dim_caja_gll[2] // 2, top=text_rect_lat_data.bottom + 2) # Espacio después de latitud
        screen.blit(text_surface_lon_data, text_rect_lon_data)
        
        # Caja Rumbo/Velocidad
        padding_horizontal_caja = 15 # Padding interno para el texto
        y_pos_rumbo_vel = dim_caja_rumbo_vel[1] + 10 # Y inicial para la primera línea de texto
        
        rumbo_etiqueta_surf = font.render(TEXTOS[IDIOMA]["rumbo"] + " :", True, COLOR_CAJA_DATOS_TEXTO)
        rumbo_etiqueta_rect = rumbo_etiqueta_surf.get_rect(left=dim_caja_rumbo_vel[0] + padding_horizontal_caja, top=y_pos_rumbo_vel)
        rumbo_valor_surf = font_datos_grandes.render(heading_str, True, COLOR_CAJA_DATOS_TEXTO)
        # Alinear el valor grande verticalmente con la etiqueta pequeña
        rumbo_valor_rect = rumbo_valor_surf.get_rect(
            left=rumbo_etiqueta_rect.right + 5, 
            centery=rumbo_etiqueta_rect.centery + (font_datos_grandes.get_linesize() - font.get_linesize()) // 2 + 2 # Ajuste fino
        )
        screen.blit(rumbo_etiqueta_surf, rumbo_etiqueta_rect)
        screen.blit(rumbo_valor_surf, rumbo_valor_rect)
        
        y_pos_velocidad = rumbo_etiqueta_rect.top + font_datos_grandes.get_linesize() + 5 # Debajo de la línea de rumbo
        vel_etiqueta_surf = font.render(TEXTOS[IDIOMA]["velocidad"] + " :", True, COLOR_CAJA_DATOS_TEXTO)
        vel_etiqueta_rect = vel_etiqueta_surf.get_rect(left=dim_caja_rumbo_vel[0] + padding_horizontal_caja, top=y_pos_velocidad)
        vel_valor_surf = font_datos_grandes.render(speed_str, True, COLOR_CAJA_DATOS_TEXTO)
        vel_valor_rect = vel_valor_surf.get_rect(
            left=vel_etiqueta_rect.right + 5, 
            centery=vel_etiqueta_rect.centery + (font_datos_grandes.get_linesize() - font.get_linesize()) // 2 + 2 # Ajuste fino
        )
        screen.blit(vel_etiqueta_surf, vel_etiqueta_rect)
        screen.blit(vel_valor_surf, vel_valor_rect)
        
        # Caja Cabeceo (Attitude)
        text_surface_titulo_cabeceo = font.render(TEXTOS[IDIOMA]["actitud"], True, COLOR_CAJA_DATOS_TEXTO)
        text_rect_titulo_cabeceo = text_surface_titulo_cabeceo.get_rect(
            centerx=dim_caja_att[0] + dim_caja_att[2] // 2, 
            top=dim_caja_att[1] + 10 # Espacio desde el borde superior de la caja
        )
        screen.blit(text_surface_titulo_cabeceo, text_rect_titulo_cabeceo)
        
        current_y_att = text_rect_titulo_cabeceo.bottom + 10 # Y para la primera línea de datos (Pitch)
        
        # Pitch
        pitch_etiqueta_str = TEXTOS[IDIOMA]["pitch"] + " :"
        pitch_valor_str = "N/A" # Default
        if att_pitch_str != "N/A":
            try:
                pitch_valor_str = f"{float(att_pitch_str):+.0f}°" # Formato con signo y 0 decimales
            except ValueError:
                pass # Mantener "N/A" si no es convertible
        
        pitch_etiqueta_surf = font.render(pitch_etiqueta_str, True, COLOR_CAJA_DATOS_TEXTO)
        pitch_etiqueta_rect = pitch_etiqueta_surf.get_rect(
            left=dim_caja_att[0] + padding_horizontal_caja, 
            top=current_y_att
        )
        pitch_valor_surf = font_datos_grandes.render(pitch_valor_str, True, COLOR_CAJA_DATOS_TEXTO)
        pitch_valor_rect = pitch_valor_surf.get_rect(
            left=pitch_etiqueta_rect.right + 5, 
            centery=pitch_etiqueta_rect.centery + (font_datos_grandes.get_linesize() - font.get_linesize()) // 2 + 2 # Ajuste
        )
        screen.blit(pitch_etiqueta_surf, pitch_etiqueta_rect)
        screen.blit(pitch_valor_surf, pitch_valor_rect)
        
        current_y_att += font_datos_grandes.get_linesize() + 5 # Y para la siguiente línea (Roll)
        
        # Roll
        roll_etiqueta_str = TEXTOS[IDIOMA]["roll"] + "  :" # Espacio extra para alinear con Pitch
        roll_valor_display_str = "N/A"
        roll_direccion_str = "" # Para "BABOR" o "ESTRIBOR"
        
        if att_roll_str != "N/A":
            try:
                roll_val = float(att_roll_str)
                roll_valor_display_str = f"{roll_val:+.0f}°"
                if roll_val > 0.1: # Umbral pequeño para evitar mostrar dirección con 0.0
                    roll_direccion_str = "ESTRIBOR" if IDIOMA == "es" else "STARBOARD"
                elif roll_val < -0.1:
                    roll_direccion_str = "BABOR" if IDIOMA == "es" else "PORT"
            except ValueError:
                pass
        
        roll_etiqueta_surf = font.render(roll_etiqueta_str, True, COLOR_CAJA_DATOS_TEXTO)
        roll_etiqueta_rect = roll_etiqueta_surf.get_rect(
            left=dim_caja_att[0] + padding_horizontal_caja, 
            top=current_y_att
        )
        roll_valor_surf = font_datos_grandes.render(roll_valor_display_str, True, COLOR_CAJA_DATOS_TEXTO)
        roll_valor_rect = roll_valor_surf.get_rect(
            left=roll_etiqueta_rect.right + 5, 
            centery=roll_etiqueta_rect.centery + (font_datos_grandes.get_linesize() - font.get_linesize()) // 2 + 2 # Ajuste
        )
        screen.blit(roll_etiqueta_surf, roll_etiqueta_rect)
        screen.blit(roll_valor_surf, roll_valor_rect)
        
        if roll_direccion_str: # Mostrar "BABOR" / "ESTRIBOR" si aplica
            roll_direccion_surf = font.render(roll_direccion_str, True, COLOR_CAJA_DATOS_TEXTO)
            # Alinear con la etiqueta de Roll, a la derecha del valor numérico
            roll_direccion_rect = roll_direccion_surf.get_rect(
                left=roll_valor_rect.right + 5, 
                centery=roll_etiqueta_rect.centery # Alinear verticalmente con la etiqueta "Roll:"
            )
            screen.blit(roll_direccion_surf, roll_direccion_rect)
        
        # Mostrar mensajes de estado (NO HAY DATOS / DESCONECTADO)
        # Estos mensajes deben aparecer encima de los círculos si están activos
        if serial_port_available and ser and ser.is_open:
            ahora_ms = pygame.time.get_ticks()
            if ahora_ms - ultima_vez_datos_recibidos > UMBRAL_SIN_DATOS_MS:
                if not nmea_data_stale: # Si acabamos de perder datos
                    reset_ui_data() # Limpiar todos los valores en pantalla
                    nmea_data_stale = True # Marcar que los datos son viejos
                
                mensaje_no_datos_actual = TEXTOS[IDIOMA]["no_datos"]
                texto_no_datos_surf = font.render(mensaje_no_datos_actual, True, ROJO)
                rect_texto_no_datos = texto_no_datos_surf.get_rect(center=area_izquierda_rect.center)
                screen.blit(texto_no_datos_surf, rect_texto_no_datos)
        elif not serial_port_available: # Si el puerto está desconectado
            if not nmea_data_stale: # Si acabamos de detectar la desconexión
                reset_ui_data()
                nmea_data_stale = True
            
            mensaje_desconexion_actual = TEXTOS[IDIOMA]["desconectado"]
            texto_desconexion_surf = font.render(mensaje_desconexion_actual, True, ROJO)
            rect_texto_desconexion = texto_desconexion_surf.get_rect(center=area_izquierda_rect.center)
            screen.blit(texto_desconexion_surf, rect_texto_desconexion)
        
        # Dibujar ventanas modales (Configuración, Alarma, Idioma, Acerca de)
        # Estas deben dibujarse al final para que estén por encima de todo lo demás.
        
        # Ventana de configuración de puerto
        if mostrar_ventana_config_serial:
            # Dibujar fondo y borde 3D de la ventana
            pygame.draw.rect(screen, COLOR_VENTANA_FONDO, rect_ventana_config)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_config.topleft, rect_ventana_config.topright, 2) # Borde superior claro
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_config.topleft, rect_ventana_config.bottomleft, 2) # Borde izquierdo claro
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_config.bottomleft, rect_ventana_config.bottomright, 2) # Borde inferior oscuro
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_config.topright, rect_ventana_config.bottomright, 2) # Borde derecho oscuro
            
            # Título de la ventana
            titulo_surf = font.render(TEXTOS[IDIOMA]["titulo_config"], True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_surf, (rect_ventana_config.centerx - titulo_surf.get_width() // 2, rect_ventana_config.top + 15))
            
            # Botón cerrar (X)
            pygame.draw.rect(screen, COLOR_BOTON_FONDO_3D, rect_boton_cerrar_config) # Fondo del botón
            # Bordes 3D para el botón
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_config.topleft, rect_boton_cerrar_config.topright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_config.topleft, pygame.math.Vector2(rect_boton_cerrar_config.left, rect_boton_cerrar_config.bottom -1), 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_config.left, rect_boton_cerrar_config.bottom -1), rect_boton_cerrar_config.bottomright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_config.right -1, rect_boton_cerrar_config.top), rect_boton_cerrar_config.bottomright, 1)
            cerrar_text = font.render("X", True, COLOR_TEXTO_NORMAL)
            screen.blit(cerrar_text, cerrar_text.get_rect(center=rect_boton_cerrar_config.center))
            
            # Etiqueta y campo de puerto (dropdown)
            label_puerto_surf = font.render(TEXTOS[IDIOMA]["etiqueta_puerto"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_puerto_surf, (rect_ventana_config.left + padding_interno_config, rect_input_puerto_config.centery - label_puerto_surf.get_height() // 2))
            
            # Input field para Puerto (display del valor seleccionado)
            pygame.draw.rect(screen, COLOR_INPUT_FONDO, rect_input_puerto_config, 0) # Fondo del input
            # Borde 3D para el input
            pygame.draw.line(screen, COLOR_INPUT_BORDE_OSCURO_3D, rect_input_puerto_config.topleft, rect_input_puerto_config.topright, 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_OSCURO_3D, rect_input_puerto_config.topleft, pygame.math.Vector2(rect_input_puerto_config.left, rect_input_puerto_config.bottom -1), 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_CLARO_3D, pygame.math.Vector2(rect_input_puerto_config.left, rect_input_puerto_config.bottom -1), rect_input_puerto_config.bottomright, 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_CLARO_3D, pygame.math.Vector2(rect_input_puerto_config.right -1, rect_input_puerto_config.top), rect_input_puerto_config.bottomright, 1)
            
            input_puerto_surf = font.render(input_puerto_str, True, COLOR_TEXTO_NORMAL) # Valor actual del puerto
            screen.blit(input_puerto_surf, (rect_input_puerto_config.left + 5, rect_input_puerto_config.centery - input_puerto_surf.get_height() // 2))
            
            # Flecha del dropdown de puerto
            pygame.draw.polygon(screen, COLOR_TEXTO_NORMAL, [
                (rect_input_puerto_config.right - 15, rect_input_puerto_config.centery - 3),
                (rect_input_puerto_config.right - 5, rect_input_puerto_config.centery - 3),
                (rect_input_puerto_config.right - 10, rect_input_puerto_config.centery + 3)
            ])
            
            # Etiqueta y campo de baudios (dropdown)
            label_baudios_surf = font.render(TEXTOS[IDIOMA]["etiqueta_baudios"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_baudios_surf, (rect_ventana_config.left + padding_interno_config, rect_input_baudios_display_config.centery - label_baudios_surf.get_height() // 2))
            
            pygame.draw.rect(screen, COLOR_INPUT_FONDO, rect_input_baudios_display_config, 0) # Fondo
            # Borde 3D
            pygame.draw.line(screen, COLOR_INPUT_BORDE_OSCURO_3D, rect_input_baudios_display_config.topleft, rect_input_baudios_display_config.topright, 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_OSCURO_3D, rect_input_baudios_display_config.topleft, pygame.math.Vector2(rect_input_baudios_display_config.left, rect_input_baudios_display_config.bottom -1), 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_CLARO_3D, pygame.math.Vector2(rect_input_baudios_display_config.left, rect_input_baudios_display_config.bottom -1), rect_input_baudios_display_config.bottomright, 1)
            pygame.draw.line(screen, COLOR_INPUT_BORDE_CLARO_3D, pygame.math.Vector2(rect_input_baudios_display_config.right -1, rect_input_baudios_display_config.top), rect_input_baudios_display_config.bottomright, 1)

            baudios_surf = font.render(str(lista_baudios_seleccionables[input_baudios_idx]), True, COLOR_TEXTO_NORMAL)
            screen.blit(baudios_surf, baudios_surf.get_rect(center=rect_input_baudios_display_config.center))
            
            # Flecha del dropdown de baudios
            pygame.draw.polygon(screen, COLOR_TEXTO_NORMAL, [
                (rect_input_baudios_display_config.right - 15, rect_input_baudios_display_config.centery - 3),
                (rect_input_baudios_display_config.right - 5, rect_input_baudios_display_config.centery - 3),
                (rect_input_baudios_display_config.right - 10, rect_input_baudios_display_config.centery + 3)
            ])
            
            # Botón guardar
            pygame.draw.rect(screen, COLOR_BOTON_FONDO_3D, rect_boton_guardar_config) # Fondo
            # Borde 3D
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_guardar_config.topleft, rect_boton_guardar_config.topright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_guardar_config.topleft, pygame.math.Vector2(rect_boton_guardar_config.left, rect_boton_guardar_config.bottom -1), 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_guardar_config.left, rect_boton_guardar_config.bottom -1), rect_boton_guardar_config.bottomright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_guardar_config.right -1, rect_boton_guardar_config.top), rect_boton_guardar_config.bottomright, 1)
            
            guardar_surf = font.render(TEXTOS[IDIOMA]["boton_guardar"] + " & Aplicar", True, COLOR_TEXTO_NORMAL) # Texto del botón
            screen.blit(guardar_surf, guardar_surf.get_rect(center=rect_boton_guardar_config.center))
            
            # Dibujar desplegables si están activos (deben dibujarse encima de otros elementos)
            puerto_dropdown_activo = globals().get('puerto_dropdown_activo', False) # Obtener estado actual
            baudios_dropdown_activo = globals().get('baudios_dropdown_activo', False)
            lista_rects_items_puerto = globals().get('lista_rects_items_puerto', [])
            lista_rects_items_baudios = globals().get('lista_rects_items_baudios', [])


            if puerto_dropdown_activo and lista_puertos_detectados:
                lista_rects_items_puerto.clear() # Limpiar rects de items anteriores
                item_height = input_puerto_surf.get_height() + 4 # Altura de cada item en el dropdown
                # Calcular altura máxima del dropdown para que no se salga de la ventana
                dropdown_max_items = 5 # Mostrar max 5 items a la vez, luego scroll (no implementado aquí)
                dropdown_height = min(item_height * len(lista_puertos_detectados), item_height * dropdown_max_items)
                
                rect_lista_puertos_desplegable = pygame.Rect(
                    rect_input_puerto_config.left,
                    rect_input_puerto_config.bottom, # Debajo del input de puerto
                    rect_input_puerto_config.width,
                    dropdown_height
                )
                globals()['rect_lista_puertos_desplegable'] = rect_lista_puertos_desplegable


                pygame.draw.rect(screen, COLOR_DROPDOWN_FONDO, rect_lista_puertos_desplegable)
                pygame.draw.rect(screen, COLOR_DROPDOWN_BORDE, rect_lista_puertos_desplegable, 1) # Borde del dropdown
                
                for i, port_name in enumerate(lista_puertos_detectados):
                    if i * item_height >= dropdown_height: # No dibujar más items de los que caben
                        break
                    
                    item_rect = pygame.Rect(
                        rect_lista_puertos_desplegable.left,
                        rect_lista_puertos_desplegable.top + i * item_height,
                        rect_lista_puertos_desplegable.width,
                        item_height
                    )
                    lista_rects_items_puerto.append(item_rect) # Guardar rect para detección de clic
                    
                    # Resaltar item si el mouse está encima (opcional, no implementado aquí)
                    # if item_rect.collidepoint(mouse_pos):
                    #    pygame.draw.rect(screen, COLOR_SELECCION_DROPDOWN, item_rect)
                    
                    item_surf = font.render(port_name, True, COLOR_TEXTO_NORMAL)
                    screen.blit(item_surf, (item_rect.left + 5, item_rect.centery - item_surf.get_height() // 2))
                globals()['lista_rects_items_puerto'] = lista_rects_items_puerto
            
            elif baudios_dropdown_activo: # Similar para baudios
                lista_rects_items_baudios.clear()
                item_height = baudios_surf.get_height() + 4
                dropdown_max_items = 5
                dropdown_height = min(item_height * len(lista_baudios_seleccionables), item_height * dropdown_max_items)
                
                rect_lista_baudios_desplegable = pygame.Rect(
                    rect_input_baudios_display_config.left,
                    rect_input_baudios_display_config.bottom,
                    rect_input_baudios_display_config.width,
                    dropdown_height
                )
                globals()['rect_lista_baudios_desplegable'] = rect_lista_baudios_desplegable

                pygame.draw.rect(screen, COLOR_DROPDOWN_FONDO, rect_lista_baudios_desplegable)
                pygame.draw.rect(screen, COLOR_DROPDOWN_BORDE, rect_lista_baudios_desplegable, 1)
                
                for i, baud_rate in enumerate(lista_baudios_seleccionables):
                    if i * item_height >= dropdown_height:
                        break
                    
                    item_rect = pygame.Rect(
                        rect_lista_baudios_desplegable.left,
                        rect_lista_baudios_desplegable.top + i * item_height,
                        rect_lista_baudios_desplegable.width,
                        item_height
                    )
                    lista_rects_items_baudios.append(item_rect)
                    
                    if i == input_baudios_idx: # Resaltar el baudrate seleccionado actualmente
                        pygame.draw.rect(screen, COLOR_SELECCION_DROPDOWN, item_rect)
                    # elif item_rect.collidepoint(mouse_pos): # Resaltar si mouse encima (opcional)
                    #    pygame.draw.rect(screen, pygame.Color('lightgrey'), item_rect)
                        
                    item_surf = font.render(str(baud_rate), True, COLOR_TEXTO_NORMAL)
                    screen.blit(item_surf, (item_rect.left + 5, item_rect.centery - item_surf.get_height() // 2))
                globals()['lista_rects_items_baudios'] = lista_rects_items_baudios
        
        # Ventana de configuración de alarmas
        elif mostrar_ventana_alarma:
            pygame.draw.rect(screen, (240, 240, 240), rect_ventana_alarma) # Fondo gris claro
            pygame.draw.rect(screen, COLOR_BORDE_VENTANA, rect_ventana_alarma, 2) # Borde
            
            titulo_alarma_surf = font.render(TEXTOS[IDIOMA]["titulo_alarma"], True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_alarma_surf, (rect_ventana_alarma.centerx - titulo_alarma_surf.get_width() // 2, rect_ventana_alarma.top + 15))
            
            # Definición de geometrías para inputs de alarma (importante que estén antes de usarlos en eventos)
            y_start_inputs = rect_ventana_alarma.top + 70
            input_height = 30
            label_width_alarma = 150 
            input_width_alarma = 80 
            padding_y_alarma = 25 # Espacio vertical entre inputs
            label_x_alarma = rect_ventana_alarma.left + 20
            input_x_alarma = label_x_alarma + label_width_alarma + 10
            
            # Campo de pitch
            rect_label_pitch_alarma = pygame.Rect(label_x_alarma, y_start_inputs, label_width_alarma, input_height)
            rect_input_pitch_alarma = pygame.Rect(input_x_alarma, y_start_inputs, input_width_alarma, input_height)
            globals()['rect_input_pitch_alarma'] = rect_input_pitch_alarma # Para acceso en eventos
            
            label_pitch_surf = font.render(TEXTOS[IDIOMA]["pitch_rango"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_pitch_surf, label_pitch_surf.get_rect(centery=rect_label_pitch_alarma.centery, left=rect_label_pitch_alarma.left))
            
            color_fondo_input_pitch = pygame.Color('lightskyblue1') if input_alarma_activo == "pitch" else COLOR_INPUT_FONDO
            pygame.draw.rect(screen, color_fondo_input_pitch, rect_input_pitch_alarma)
            pygame.draw.rect(screen, COLOR_INPUT_BORDE, rect_input_pitch_alarma, 1) # Borde del input
            
            input_pitch_surf = font.render(valores_ui_input_alarma["pitch"], True, COLOR_TEXTO_NORMAL)
            screen.blit(input_pitch_surf, (rect_input_pitch_alarma.left + 5, rect_input_pitch_alarma.centery - input_pitch_surf.get_height() // 2))
            
            # Campo de roll
            y_current_alarma = y_start_inputs + input_height + padding_y_alarma
            rect_label_roll_alarma = pygame.Rect(label_x_alarma, y_current_alarma, label_width_alarma, input_height)
            rect_input_roll_alarma = pygame.Rect(input_x_alarma, y_current_alarma, input_width_alarma, input_height)
            globals()['rect_input_roll_alarma'] = rect_input_roll_alarma # Para acceso en eventos

            label_roll_surf = font.render(TEXTOS[IDIOMA]["roll_rango"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_roll_surf, label_roll_surf.get_rect(centery=rect_label_roll_alarma.centery, left=rect_label_roll_alarma.left))
            
            color_fondo_input_roll = pygame.Color('lightskyblue1') if input_alarma_activo == "roll" else COLOR_INPUT_FONDO
            pygame.draw.rect(screen, color_fondo_input_roll, rect_input_roll_alarma)
            pygame.draw.rect(screen, COLOR_INPUT_BORDE, rect_input_roll_alarma, 1) # Borde
            
            input_roll_surf = font.render(valores_ui_input_alarma["roll"], True, COLOR_TEXTO_NORMAL)
            screen.blit(input_roll_surf, (rect_input_roll_alarma.left + 5, rect_input_roll_alarma.centery - input_roll_surf.get_height() // 2))
            
            # Botones Guardar y Salir para ventana de alarma
            button_width_alarma = 120
            button_height_alarma = 40
            y_botones_alarma = rect_ventana_alarma.bottom - button_height_alarma - 20 # Posición Y de los botones
            
            rect_boton_guardar_alarma = pygame.Rect(
                rect_ventana_alarma.centerx - button_width_alarma - 10, # Botón Guardar a la izquierda
                y_botones_alarma,
                button_width_alarma,
                button_height_alarma
            )
            globals()['rect_boton_guardar_alarma'] = rect_boton_guardar_alarma

            rect_boton_salir_alarma = pygame.Rect(
                rect_ventana_alarma.centerx + 10, # Botón Salir a la derecha
                y_botones_alarma,
                button_width_alarma,
                button_height_alarma
            )
            globals()['rect_boton_salir_alarma'] = rect_boton_salir_alarma
            
            # Dibujar botón Guardar
            pygame.draw.rect(screen, COLOR_BOTON_FONDO, rect_boton_guardar_alarma)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_guardar_alarma, 1) # Borde
            guardar_alarma_surf = font.render(TEXTOS[IDIOMA]["boton_guardar"], True, COLOR_TEXTO_NORMAL)
            screen.blit(guardar_alarma_surf, guardar_alarma_surf.get_rect(center=rect_boton_guardar_alarma.center))
            
            # Dibujar botón Salir
            pygame.draw.rect(screen, COLOR_BOTON_FONDO, rect_boton_salir_alarma)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_salir_alarma, 1) # Borde
            salir_alarma_surf = font.render(TEXTOS[IDIOMA]["boton_salir"], True, COLOR_TEXTO_NORMAL)
            screen.blit(salir_alarma_surf, salir_alarma_surf.get_rect(center=rect_boton_salir_alarma.center))
        
        # Ventana Acerca de
        elif mostrar_ventana_acerca_de:
            rect_ventana_acerca_de = pygame.Rect(250, 150, 400, 250) # Definir dimensiones
            pygame.draw.rect(screen, (240, 240, 240), rect_ventana_acerca_de) # Fondo
            pygame.draw.rect(screen, COLOR_BORDE_VENTANA, rect_ventana_acerca_de, 2) # Borde
            
            titulo_acerca_surf = font.render(TEXTOS[IDIOMA]["titulo_acerca"], True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_acerca_surf, (rect_ventana_acerca_de.centerx - titulo_acerca_surf.get_width() // 2, rect_ventana_acerca_de.top + 15))
            
            texto_info = [
                "NMEA Data Reader Program",
                "Versión: 1.0",
                "Realizado por: Hdelacruz",
                "Email: hugo_delacruz@hotmail.com",
                "                                        2025" # Año, un poco desalineado, se podría mejorar
            ]
            
            y_offset_info = rect_ventana_acerca_de.top + 60
            for linea in texto_info:
                info_surf = font.render(linea, True, COLOR_TEXTO_NORMAL)
                screen.blit(info_surf, (rect_ventana_acerca_de.left + 20, y_offset_info))
                y_offset_info += info_surf.get_height() + 10 # Espacio entre líneas
            
            # Botón Cerrar para ventana Acerca de
            rect_boton_cerrar_acerca_de = pygame.Rect(
                rect_ventana_acerca_de.centerx - 50, # Centrado
                rect_ventana_acerca_de.bottom - 50, # Cerca del fondo
                100, 30 # Ancho, Alto
            )
            globals()['rect_boton_cerrar_acerca_de'] = rect_boton_cerrar_acerca_de
            
            pygame.draw.rect(screen, COLOR_BOTON_FONDO, rect_boton_cerrar_acerca_de)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_cerrar_acerca_de, 1) # Borde
            cerrar_acerca_surf = font.render(TEXTOS[IDIOMA]["boton_cerrar"], True, COLOR_TEXTO_NORMAL)
            screen.blit(cerrar_acerca_surf, cerrar_acerca_surf.get_rect(center=rect_boton_cerrar_acerca_de.center))
        
        # Ventana de selección de idioma
        elif mostrar_ventana_idioma:
            pygame.draw.rect(screen, COLOR_VENTANA_FONDO, rect_ventana_idioma) # Fondo
            pygame.draw.rect(screen, COLOR_BORDE_VENTANA, rect_ventana_idioma, 2) # Borde
            
            # Título de la ventana de idioma (no está en TEXTOS, así que se hardcodea o se añade)
            # Por ahora, hardcodeado para simplicidad, idealmente estaría en TEXTOS.
            titulo_idioma_surf = font.render("IDIOMA / LANGUAGE", True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_idioma_surf, (rect_ventana_idioma.centerx - titulo_idioma_surf.get_width() // 2, rect_ventana_idioma.top + 15))
            
            button_width_lang = 150
            button_height_lang = 40
            padding_y_lang = 20 # Espacio vertical entre botones
            
            # Botón Español
            rect_boton_es = pygame.Rect(
                rect_ventana_idioma.centerx - button_width_lang // 2,
                rect_ventana_idioma.top + titulo_idioma_surf.get_height() + 30, # Debajo del título
                button_width_lang,
                button_height_lang
            )
            globals()['rect_boton_es'] = rect_boton_es


            # Botón Inglés
            rect_boton_en = pygame.Rect(
                rect_ventana_idioma.centerx - button_width_lang // 2,
                rect_boton_es.bottom + padding_y_lang, # Debajo del botón español
                button_width_lang,
                button_height_lang
            )
            globals()['rect_boton_en'] = rect_boton_en
            
            # Dibujar botón Español (resaltado si es el idioma actual)
            color_fondo_es = COLOR_BOTON_FONDO_3D if IDIOMA == "es" else COLOR_BOTON_FONDO
            pygame.draw.rect(screen, color_fondo_es, rect_boton_es)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_es, 1) # Borde
            texto_es_surf = font.render("ESPAÑOL", True, COLOR_TEXTO_NORMAL)
            screen.blit(texto_es_surf, texto_es_surf.get_rect(center=rect_boton_es.center))
            
            # Dibujar botón Inglés (resaltado si es el idioma actual)
            color_fondo_en = COLOR_BOTON_FONDO_3D if IDIOMA == "en" else COLOR_BOTON_FONDO
            pygame.draw.rect(screen, color_fondo_en, rect_boton_en)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_en, 1) # Borde
            texto_en_surf = font.render("ENGLISH", True, COLOR_TEXTO_NORMAL)
            screen.blit(texto_en_surf, texto_en_surf.get_rect(center=rect_boton_en.center))
        
        # Ventana de Contraseña para Servicio de Datos
        elif mostrar_ventana_password_servicio:
            pygame.draw.rect(screen, COLOR_VENTANA_FONDO, rect_ventana_password_servicio)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_password_servicio.topleft, rect_ventana_password_servicio.topright, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_password_servicio.topleft, rect_ventana_password_servicio.bottomleft, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_password_servicio.bottomleft, rect_ventana_password_servicio.bottomright, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_password_servicio.topright, rect_ventana_password_servicio.bottomright, 2)

            titulo_pwd_surf = font.render(TEXTOS[IDIOMA]["titulo_password_servicio"], True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_pwd_surf, (rect_ventana_password_servicio.centerx - titulo_pwd_surf.get_width() // 2, rect_ventana_password_servicio.top + 15))

            # Botón Cerrar 'X'
            rect_boton_cerrar_password_servicio = pygame.Rect(rect_ventana_password_servicio.right - 35, rect_ventana_password_servicio.top + 5, 30, 30)
            globals()['rect_boton_cerrar_password_servicio'] = rect_boton_cerrar_password_servicio
            pygame.draw.rect(screen, COLOR_BOTON_FONDO_3D, rect_boton_cerrar_password_servicio)
            # ... (bordes 3D para el botón cerrar)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_password_servicio.topleft, rect_boton_cerrar_password_servicio.topright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_password_servicio.topleft, pygame.math.Vector2(rect_boton_cerrar_password_servicio.left, rect_boton_cerrar_password_servicio.bottom -1), 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_password_servicio.left, rect_boton_cerrar_password_servicio.bottom -1), rect_boton_cerrar_password_servicio.bottomright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_password_servicio.right -1, rect_boton_cerrar_password_servicio.top), rect_boton_cerrar_password_servicio.bottomright, 1) # Typo corrected here
            cerrar_pwd_text = font.render("X", True, COLOR_TEXTO_NORMAL)
            screen.blit(cerrar_pwd_text, cerrar_pwd_text.get_rect(center=rect_boton_cerrar_password_servicio.center))

            # Etiqueta y campo de contraseña
            label_pwd_surf = font.render(TEXTOS[IDIOMA]["etiqueta_password"], True, COLOR_TEXTO_NORMAL)
            input_y_pwd = rect_ventana_password_servicio.top + 60
            screen.blit(label_pwd_surf, (rect_ventana_password_servicio.left + 20, input_y_pwd + 5))

            input_pwd_width = ventana_password_width - 40 - label_pwd_surf.get_width() - 10
            rect_input_password = pygame.Rect(rect_ventana_password_servicio.left + 20 + label_pwd_surf.get_width() + 5, input_y_pwd, input_pwd_width, 30)
            globals()['rect_input_password'] = rect_input_password
            
            color_fondo_input_pwd = pygame.Color('lightskyblue1') if input_password_activo else COLOR_INPUT_FONDO
            pygame.draw.rect(screen, color_fondo_input_pwd, rect_input_password)
            pygame.draw.rect(screen, COLOR_INPUT_BORDE, rect_input_password, 1)
            input_pwd_text_surf = font.render("*" * len(input_password_str), True, COLOR_TEXTO_NORMAL) # Mostrar asteriscos
            screen.blit(input_pwd_text_surf, (rect_input_password.left + 5, rect_input_password.centery - input_pwd_text_surf.get_height() // 2))

            # Mensaje de contraseña incorrecta
            if intento_password_fallido:
                error_surf = font.render(TEXTOS[IDIOMA]["password_incorrecta"], True, ROJO)
                screen.blit(error_surf, (rect_ventana_password_servicio.centerx - error_surf.get_width() // 2, rect_input_password.bottom + 10))

            # Botón Entrar
            btn_entrar_width = 100
            rect_boton_entrar_password = pygame.Rect(rect_ventana_password_servicio.centerx - btn_entrar_width // 2, rect_ventana_password_servicio.bottom - 50, btn_entrar_width, 30)
            globals()['rect_boton_entrar_password'] = rect_boton_entrar_password
            pygame.draw.rect(screen, COLOR_BOTON_FONDO, rect_boton_entrar_password)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_entrar_password, 1)
            entrar_surf = font.render(TEXTOS[IDIOMA]["boton_entrar"], True, COLOR_TEXTO_NORMAL)
            screen.blit(entrar_surf, entrar_surf.get_rect(center=rect_boton_entrar_password.center))

        # Ventana de configuración de servicio de datos
        elif mostrar_ventana_servicio_datos:
            pygame.draw.rect(screen, COLOR_VENTANA_FONDO, rect_ventana_servicio_datos)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_servicio_datos.topleft, rect_ventana_servicio_datos.topright, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_CLARO, rect_ventana_servicio_datos.topleft, rect_ventana_servicio_datos.bottomleft, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_servicio_datos.bottomleft, rect_ventana_servicio_datos.bottomright, 2)
            pygame.draw.line(screen, COLOR_BORDE_VENTANA_OSCURO, rect_ventana_servicio_datos.topright, rect_ventana_servicio_datos.bottomright, 2)

            titulo_surf = font.render(TEXTOS[IDIOMA]["titulo_servicio_datos"], True, COLOR_TEXTO_NORMAL)
            screen.blit(titulo_surf, (rect_ventana_servicio_datos.centerx - titulo_surf.get_width() // 2, rect_ventana_servicio_datos.top + 15))

            # Botón cerrar (X) - similar al de otras ventanas
            rect_boton_cerrar_servicio = pygame.Rect(rect_ventana_servicio_datos.right - 35, rect_ventana_servicio_datos.top + 5, 30, 30)
            globals()['rect_boton_cerrar_servicio'] = rect_boton_cerrar_servicio
            pygame.draw.rect(screen, COLOR_BOTON_FONDO_3D, rect_boton_cerrar_servicio)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_servicio.topleft, rect_boton_cerrar_servicio.topright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_CLARO_3D, rect_boton_cerrar_servicio.topleft, pygame.math.Vector2(rect_boton_cerrar_servicio.left, rect_boton_cerrar_servicio.bottom -1), 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_servicio.left, rect_boton_cerrar_servicio.bottom -1), rect_boton_cerrar_servicio.bottomright, 1)
            pygame.draw.line(screen, COLOR_BOTON_BORDE_OSCURO_3D, pygame.math.Vector2(rect_boton_cerrar_servicio.right -1, rect_boton_cerrar_servicio.top), rect_boton_cerrar_servicio.bottomright, 1)
            cerrar_text_servicio = font.render("X", True, COLOR_TEXTO_NORMAL)
            screen.blit(cerrar_text_servicio, cerrar_text_servicio.get_rect(center=rect_boton_cerrar_servicio.center))

            # Sección de selección de servicio
            padding_x_servicio = 20
            padding_y_servicio = 20
            current_y_servicio = rect_ventana_servicio_datos.top + titulo_surf.get_height() + 30
            
            label_servicio_surf = font.render(TEXTOS[IDIOMA]["etiqueta_servicio"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_servicio_surf, (rect_ventana_servicio_datos.left + padding_x_servicio, current_y_servicio))
            current_y_servicio += label_servicio_surf.get_height() + 10

            # Radio Button ThingSpeak
            radio_x = rect_ventana_servicio_datos.left + padding_x_servicio + RADIO_BUTTON_SIZE + 5
            rect_radio_thingspeak = pygame.Rect(radio_x, current_y_servicio, RADIO_BUTTON_SIZE * 2, RADIO_BUTTON_SIZE * 2)
            globals()['rect_radio_thingspeak'] = pygame.Rect(rect_ventana_servicio_datos.left + padding_x_servicio, current_y_servicio, ventana_servicio_width - 2*padding_x_servicio, RADIO_BUTTON_SIZE*2 + 4 ) # Rect más grande para clic
            
            pygame.draw.circle(screen, COLOR_TEXTO_NORMAL, (radio_x + RADIO_BUTTON_SIZE, current_y_servicio + RADIO_BUTTON_SIZE), RADIO_BUTTON_SIZE, 1)
            if SERVICIO_DATOS_ACTUAL == "thingspeak":
                pygame.draw.circle(screen, COLOR_TEXTO_NORMAL, (radio_x + RADIO_BUTTON_SIZE, current_y_servicio + RADIO_BUTTON_SIZE), RADIO_BUTTON_SIZE - 4)
            
            label_ts_surf = font.render(TEXTOS[IDIOMA]["opcion_thingspeak"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_ts_surf, (radio_x + RADIO_BUTTON_SIZE * 2 + 10, current_y_servicio + (RADIO_BUTTON_SIZE*2 - label_ts_surf.get_height())//2))
            current_y_servicio += RADIO_BUTTON_SIZE * 2 + padding_y_servicio

            # API Key ThingSpeak
            label_apikey_ts_surf = font.render(TEXTOS[IDIOMA]["etiqueta_apikey_thingspeak"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_apikey_ts_surf, (rect_ventana_servicio_datos.left + padding_x_servicio, current_y_servicio))
            
            input_width_servicio = ventana_servicio_width - (padding_x_servicio * 2) - label_apikey_ts_surf.get_width() - 10
            max_input_width = 200 # Limitar ancho para que no sea demasiado grande
            input_width_servicio = min(input_width_servicio, max_input_width)

            rect_input_apikey_thingspeak = pygame.Rect(rect_ventana_servicio_datos.left + padding_x_servicio + label_apikey_ts_surf.get_width() + 5, current_y_servicio -2, input_width_servicio, font.get_height() + 4)
            globals()['rect_input_apikey_thingspeak'] = rect_input_apikey_thingspeak
            
            color_fondo_input_ts = pygame.Color('lightskyblue1') if input_servicio_activo == "thingspeak" else COLOR_INPUT_FONDO
            pygame.draw.rect(screen, color_fondo_input_ts, rect_input_apikey_thingspeak)
            pygame.draw.rect(screen, COLOR_INPUT_BORDE, rect_input_apikey_thingspeak, 1)
            input_ts_surf = font.render(input_api_key_thingspeak_str, True, COLOR_TEXTO_NORMAL)
            screen.blit(input_ts_surf, (rect_input_apikey_thingspeak.left + 5, rect_input_apikey_thingspeak.top + 2))
            current_y_servicio += label_apikey_ts_surf.get_height() + padding_y_servicio


            # Radio Button Google Cloud
            rect_radio_google_cloud = pygame.Rect(radio_x, current_y_servicio, RADIO_BUTTON_SIZE * 2, RADIO_BUTTON_SIZE * 2)
            globals()['rect_radio_google_cloud'] = pygame.Rect(rect_ventana_servicio_datos.left + padding_x_servicio, current_y_servicio, ventana_servicio_width - 2*padding_x_servicio, RADIO_BUTTON_SIZE*2 + 4)

            pygame.draw.circle(screen, COLOR_TEXTO_NORMAL, (radio_x + RADIO_BUTTON_SIZE, current_y_servicio + RADIO_BUTTON_SIZE), RADIO_BUTTON_SIZE, 1)
            if SERVICIO_DATOS_ACTUAL == "google_cloud":
                pygame.draw.circle(screen, COLOR_TEXTO_NORMAL, (radio_x + RADIO_BUTTON_SIZE, current_y_servicio + RADIO_BUTTON_SIZE), RADIO_BUTTON_SIZE - 4)

            label_gc_surf = font.render(TEXTOS[IDIOMA]["opcion_google_cloud"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_gc_surf, (radio_x + RADIO_BUTTON_SIZE * 2 + 10, current_y_servicio + (RADIO_BUTTON_SIZE*2 - label_gc_surf.get_height())//2))
            current_y_servicio += RADIO_BUTTON_SIZE * 2 + padding_y_servicio

            # API Key Google Cloud
            label_apikey_gc_surf = font.render(TEXTOS[IDIOMA]["etiqueta_apikey_google_cloud"], True, COLOR_TEXTO_NORMAL)
            screen.blit(label_apikey_gc_surf, (rect_ventana_servicio_datos.left + padding_x_servicio, current_y_servicio))
            
            rect_input_apikey_google_cloud = pygame.Rect(rect_ventana_servicio_datos.left + padding_x_servicio + label_apikey_gc_surf.get_width() + 5, current_y_servicio -2, input_width_servicio, font.get_height() + 4)
            globals()['rect_input_apikey_google_cloud'] = rect_input_apikey_google_cloud

            color_fondo_input_gc = pygame.Color('lightskyblue1') if input_servicio_activo == "google_cloud" else COLOR_INPUT_FONDO
            pygame.draw.rect(screen, color_fondo_input_gc, rect_input_apikey_google_cloud)
            pygame.draw.rect(screen, COLOR_INPUT_BORDE, rect_input_apikey_google_cloud, 1)
            input_gc_surf = font.render(input_api_key_google_cloud_str, True, COLOR_TEXTO_NORMAL)
            screen.blit(input_gc_surf, (rect_input_apikey_google_cloud.left + 5, rect_input_apikey_google_cloud.top + 2))
            current_y_servicio += label_apikey_gc_surf.get_height() + padding_y_servicio + 10 # Espacio antes de botones

            # Botones Guardar
            button_servicio_width = 120
            button_servicio_height = 40
            rect_boton_guardar_servicio = pygame.Rect(
                rect_ventana_servicio_datos.centerx - button_servicio_width // 2,
                rect_ventana_servicio_datos.bottom - button_servicio_height - padding_y_servicio,
                button_servicio_width, button_servicio_height
            )
            globals()['rect_boton_guardar_servicio'] = rect_boton_guardar_servicio
            pygame.draw.rect(screen, COLOR_BOTON_FONDO, rect_boton_guardar_servicio)
            pygame.draw.rect(screen, COLOR_BOTON_BORDE, rect_boton_guardar_servicio, 1)
            guardar_servicio_surf = font.render(TEXTOS[IDIOMA]["boton_guardar"], True, COLOR_TEXTO_NORMAL)
            screen.blit(guardar_servicio_surf, guardar_servicio_surf.get_rect(center=rect_boton_guardar_servicio.center))

        # Mostrar tiempo restante del periodo de gracia
        if PROGRAM_MODE == "GRACE_PERIOD" and grace_period_start_time_obj is not None:
            remaining_time_str = format_remaining_grace_time(grace_period_start_time_obj)
            if remaining_time_str:
                # Usar una fuente un poco más pequeña para este mensaje
                font_trial_info = pygame.font.Font(None, 20) # Misma que font_bar_herramientas o similar
                trial_text_color = (255, 223, 0) # Un color dorado/amarillo
                
                if IDIOMA == "es":
                    grace_message = f"periodo de prueba restante: {remaining_time_str}"
                else: # IDIOMA == "en"
                    grace_message = f"remaining trial period: {remaining_time_str}"

                tiempo_surf = font_trial_info.render(grace_message, True, trial_text_color)
                tiempo_rect = tiempo_surf.get_rect(centerx=screen.get_width() // 2, bottom=screen.get_height() - 5) # 5px padding desde abajo
                
                # Pequeño fondo oscuro semi-transparente para legibilidad
                fondo_rect = tiempo_rect.inflate(10, 4) # Un poco más grande que el texto
                fondo_surf = pygame.Surface(fondo_rect.size, pygame.SRCALPHA)
                fondo_surf.fill((0, 0, 0, 120)) # Negro semi-transparente
                screen.blit(fondo_surf, fondo_rect.topleft)
                
                screen.blit(tiempo_surf, tiempo_rect)

        # Mostrar mensaje de TRIAL EXPIRADO
        if PROGRAM_MODE == "TRIAL_EXPIRED":
            font_trial_expired = pygame.font.Font(None, 74) # Fuente grande
            expired_text_color = ROJO 
            
            expired_message = TEXTOS[IDIOMA]["trial_expired_message"]
            expired_surf = font_trial_expired.render(expired_message, True, expired_text_color)
            
            # Crear una superficie para la marca de agua con transparencia alfa
            # Esto permite que el texto sea semi-transparente si se desea,
            # o simplemente para controlar su renderizado sobre otros elementos.
            alpha_surface = pygame.Surface(expired_surf.get_size(), pygame.SRCALPHA)
            alpha_surface.fill((0,0,0,0)) # Fondo transparente para la superficie del texto
            
            # Dibujar el texto en la superficie alfa con la opacidad deseada
            # Para texto sólido pero que se dibuja encima:
            # expired_surf.set_alpha(200) # Opcional: hacer el texto mismo semi-transparente
            # alpha_surface.blit(expired_surf, (0,0))
            # O dibujar directamente el texto sólido:
            
            expired_rect = expired_surf.get_rect(center=(screen.get_width() // 2, screen.get_height() // 2))
            
            # Opcional: Fondo semi-transparente para el texto para destacarlo
            bg_rect = expired_rect.inflate(20, 10)
            bg_surf = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
            bg_surf.fill((50, 50, 50, 180)) # Gris oscuro semi-transparente
            screen.blit(bg_surf, bg_rect.topleft)
            
            screen.blit(expired_surf, expired_rect)


        pygame.display.flip()
        reloj.tick(60) # Limitar a 60 FPS
    
    # Limpieza al salir del bucle principal
    if serial_port_available and ser is not None:
        if ser.is_open:
            ser.close()
    pygame.quit()

# Punto de entrada del programa
if __name__ == "__main__":
    main()
