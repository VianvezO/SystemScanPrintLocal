"""
config.py — Configuración central del escáner de impresoras.
Las variables marcadas con [ENV] pueden sobreescribirse en un archivo .env
ubicado en la raíz del proyecto (ver .env.example).
"""
import os
from dotenv import load_dotenv

load_dotenv()  # Carga variables del archivo .env si existe

# ─── Escaneo de red ───────────────────────────────────────────────────────────

# [ENV] Rango IP a escanear. Se puede definir en .env como NETWORK_RANGE=x.x.x.x/24
# None = auto-detectar la red local.
NETWORK_RANGE = os.getenv("NETWORK_RANGE", "192.168.146.0/24")

# Tiempo máximo (segundos) de espera por host al hacer ping / conexión TCP
TIMEOUT_PING = 1.0
TIMEOUT_HTTP = 5.0

# Número de hilos paralelos para el ping sweep
PING_THREADS = 100

# Número de hilos paralelos para las peticiones HTTP
HTTP_THREADS = 50

# ─── Puertos ─────────────────────────────────────────────────────────────────

# Puertos TCP que se verifican en cada IP activa
PRINTER_PORTS = [80, 443, 631, 9100]

# Puerto principal para obtener la página web de identificación
WEB_PORTS = [80, 443]

# ─── Detección de impresoras ──────────────────────────────────────────────────

# Palabras clave en el <title> que identifican marcas conocidas.
# Formato: { "Marca": ["keyword1", "keyword2", ...] }
PRINTER_BRANDS = {
    "Lexmark":  ["lexmark"],
    "HP":       ["hp laserjet", "hp color laserjet", "hp officejet",
                 "hp deskjet", "hp pagewide", "laserjet", "officejet"],
    "Brother":  ["brother mfc", "brother hl", "brother dcp", "brother"],
    "Epson":    ["epson"],
    "Canon":    ["canon pixma", "canon imagerunner", "canon lbp", "canon"],
    "Kyocera":  ["kyocera"],
    "Ricoh":    ["ricoh", "aficio"],
    "Xerox":    ["xerox", "phaser", "workcentre"],
    "Konica":   ["konica minolta", "bizhub"],
    "Samsung":  ["samsung"],
    "Pantum":   ["pantum"],
    "OKI":      ["oki data", "oki"],
}

# Rutas adicionales a probar si la raíz (/) no devuelve información útil
EXTRA_PATHS = [
    "/",
    "/index.html",
    "/index.htm",
    "/cgi-bin/dynamic/config/gen.html",   # Lexmark
    "/info_configuration.html",            # Lexmark antiguo
    "/hp/device/info",                     # HP
    "/web/guest/en/websys/webArch/mainFrame.cgi",  # Ricoh (EN)
    "/web/guest/es/websys/webArch/header.cgi",     # Ricoh (ES) — header con modelo
    "/web/guest/en/websys/webArch/header.cgi",     # Ricoh (EN) — header con modelo
    "/wcd/index.html",                     # Kyocera
    "/airprint.html",                      # generico AirPrint
]

# ─── Reportes ─────────────────────────────────────────────────────────────────

# Directorio de salida para reportes (relativo al script)
OUTPUT_DIR = "reportes"

# Nombre base del archivo de reporte (sin extensión)
REPORT_BASENAME = "impresoras"

# ─── Impresoras conocidas (semilla inicial de la BD) ──────────────────────────

# Lista de impresoras que se cargan automáticamente en la base de datos al iniciar.
# Campos:
#   name      → "Usuario - Área"
#   ip        → Dirección IP de la impresora
#   model     → Modelo exacto
#   type      → Parser a usar: "modern" | "classic" | "hp" | "ricoh" | "kyocera"
#   url_path  → Ruta HTTP para consultar el estado de suministros
#   protocol  → (opcional) "http" o "https". Por defecto: "https"
PRINTERS_CONFIG = [
    {"name": "Jose Ventura - Familia",      "ip": "192.168.146.98",  "model": "Lexmark MS632dwe",      "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Milagro Farro - Familia",     "ip": "192.168.146.44",  "model": "Lexmark MS632dwe",      "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Nancy Celis - Familia",       "ip": "192.168.146.47",  "model": "Lexmark MS632dwe",      "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Julio Suyon - Civil",         "ip": "192.168.146.45",  "model": "Lexmark MS632dwe",      "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Ricardina Gallardo - Psicologa", "ip": "192.168.146.141", "model": "Lexmark MS415dn",   "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Tania Abad - JIP",            "ip": "192.168.146.32",  "model": "Lexmark MS415dn",      "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Ricardo Quispe - Familia",    "ip": "192.168.146.52",  "model": "Lexmark MS415dn",      "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Elvia Manayalle - JIP",       "ip": "192.168.146.140", "model": "Lexmark MS415dn",      "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Jaime Marin - JUP",           "ip": "192.168.146.67",  "model": "Lexmark MS415dn",      "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Sara Veronica - JIP",         "ip": "192.168.146.68",  "model": "Lexmark MS415dn",      "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Jorge Rodriguez - JUP",       "ip": "192.168.146.104", "model": "Lexmark MX711",        "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Jaime Tenorio - Civil",       "ip": "192.168.146.85",  "model": "Lexmark MX711",        "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Marcela Diaz - 1JPL",         "ip": "192.168.146.208", "model": "Lexmark MS622de",      "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Karina Gonzales - JIP",       "ip": "192.168.146.25",  "model": "Lexmark MS810",        "type": "classic", "url_path": "/cgi-bin/dynamic/printer/PrinterStatus.html"},
    {"name": "Alex Mego - Civil",           "ip": "192.168.146.73",  "model": "Lexmark MX722adhe",    "type": "modern",  "url_path": "/webglue/content?c=Status"},
    {"name": "Maria Esteves - 2JPL",        "ip": "192.168.146.131", "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Jose Chamaya - 1JPL",         "ip": "192.168.146.142", "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Freddy Rubio - 2JPL",         "ip": "192.168.146.196", "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Cinthia Flores - 1JPL",       "ip": "192.168.146.65",  "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Patricia Huaman - 1JPL",      "ip": "192.168.146.240", "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Segundo Criollo - MP",        "ip": "192.168.146.37",  "model": "HP LaserJet MFP M634", "type": "hp",      "url_path": "/"},
    {"name": "Rosa Paz - JIP",              "ip": "192.168.146.90",  "model": "Ricoh IM 550",         "type": "ricoh",   "url_path": "/web/guest/es/websys/webArch/getStatus.cgi",   "protocol": "http"},
    {"name": "Cesar Salazar - JIP",         "ip": "192.168.146.206", "model": "Ricoh IM 550",         "type": "ricoh",   "url_path": "/web/guest/es/websys/webArch/getStatus.cgi",   "protocol": "http"},
    {"name": "Lourdes Villanueva - JIP",    "ip": "192.168.146.195", "model": "Kyocera ECOSYS M3655idn", "type": "kyocera", "url_path": "/", "protocol": "http"},
]
