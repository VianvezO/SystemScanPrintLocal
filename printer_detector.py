"""
printer_detector.py — Identifica impresoras a partir de su interfaz web embebida.

Estrategia:
  1. Intenta HTTP (80) y HTTPS (443) en varias rutas.
  2. Extrae el tag <title> de la respuesta HTML.
  3. Compara contra los patrones de marcas en config.PRINTER_BRANDS.
  4. Devuelve un dict con toda la información encontrada.
"""

import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from urllib3.exceptions import InsecureRequestWarning

import requests
import urllib3
from bs4 import BeautifulSoup

import config

# Silencia las advertencias de certificados SSL autofirmados
urllib3.disable_warnings(InsecureRequestWarning)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; PrinterScanner/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*",
})


# ─── Modelo de datos ─────────────────────────────────────────────────────────

@dataclass
class PrinterInfo:
    ip: str
    hostname: str = ""
    brand: str = "Desconocida"
    model: str = "Desconocido"
    title: str = ""
    url: str = ""
    open_ports: List[int] = field(default_factory=list)
    is_printer: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Utilidades ──────────────────────────────────────────────────────────────

def _resolve_hostname(ip: str) -> str:
    """Intenta resolver el hostname de la IP por DNS inverso."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _fetch_page(url: str) -> Optional[str]:
    """
    Descarga la página en *url* y devuelve el HTML como texto.
    Soporta HTTP y HTTPS con certificados autofirmados.
    """
    try:
        resp = SESSION.get(
            url,
            timeout=config.TIMEOUT_HTTP,
            verify=False,
            allow_redirects=True,
        )
        if resp.status_code < 400:
            # Intenta detectar el encoding correcto
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
    except Exception:
        pass
    return None


def _extract_title(html: str) -> str:
    """Extrae el contenido del tag <title> de un HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("title")
        if tag and tag.string:
            return tag.string.strip()
        # Fallback con regex por si el parser falla
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return ""


def _extract_product_tag(html: str) -> str:
    """
    Extrae el modelo desde estructuras EWS tipo HP:

        <div id="header">
          <div class="info">
            <strong class="product">HP LaserJet MFP M634</strong>
            ...
          </div>
        </div>

    También captura variantes como:
        <strong class="product-name">...
        <span  class="product">...
        <h1    class="product">...
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Busca dentro de div#header primero (HP EWS)
        header = soup.find("div", id="header")
        if header:
            tag = header.find(
                ["strong", "span", "h1", "p"],
                class_=re.compile(r"product", re.I),
            )
            if tag and tag.get_text(strip=True):
                return tag.get_text(strip=True)

        # Fallback global: cualquier elemento con class que contenga 'product'
        tag = soup.find(
            ["strong", "span", "h1", "p"],
            class_=re.compile(r"product", re.I),
        )
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)

    except Exception:
        pass
    return ""


def _extract_device_name(html: str) -> str:
    """
    Extrae el nombre del dispositivo desde:
        <p class="device-name" id="HomeDeviceName">HP LaserJet MFP M634</p>
    Presente en la EWS de HP.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("p", class_=re.compile(r"device-name", re.I))
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
        tag = soup.find(id=re.compile(r"DeviceName", re.I))
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
    except Exception:
        pass
    return ""


def _extract_ricoh_model(html: str) -> tuple[str, str]:
    """
    Extrae marca y modelo desde la EWS de Ricoh / Aficio:

        <div id="frameHead">
          <div id="logoArea">
            <div id="logo">
              <h1><img alt="RICOH" ...></h1>          <- marca
              <h2 id="modelName">IM 550</h2>          <- modelo
            </div>
          </div>
        </div>

    Returns:
        (brand, model) o ("", "") si no se encuentra.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        frame = soup.find("div", id="frameHead")
        search_root = frame if frame else soup

        # Modelo: <h2 id="modelName">
        model_tag = search_root.find("h2", id=re.compile(r"modelName", re.I))
        if not model_tag:
            # Fallback: cualquier h2 dentro del logo
            logo = search_root.find("div", id="logo")
            model_tag = logo.find("h2") if logo else None

        if not model_tag or not model_tag.get_text(strip=True):
            return "", ""

        model = model_tag.get_text(strip=True)

        # Marca: img[alt] dentro del h1 del logo
        brand = "Ricoh"  # default si no se puede leer el alt
        logo = search_root.find("div", id="logo")
        if logo:
            h1 = logo.find("h1")
            if h1:
                img = h1.find("img")
                if img and img.get("alt"):
                    brand = img["alt"].strip()

        return brand, model

    except Exception:
        pass
    return "", ""


def _extract_kyocera_model(html: str) -> tuple[str, str]:
    """
    Extrae marca y modelo desde la EWS de Kyocera:

        <div class="header_info" id="basicinfo">
        ...
        <td nowrap="" id="info" ...>Modelo : ECOSYS M3655idn</td>
        ...
        </div>

    Returns:
        (brand, model) o ("", "") si no se encuentra.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        basic_info = soup.find("div", id="basicinfo")
        if basic_info:
            tds = basic_info.find_all("td", id="info")
            for td in tds:
                text = td.get_text(strip=True)
                if text.lower().startswith("modelo"):
                    if ":" in text:
                        model = text.split(":", 1)[1].strip()
                        return "Kyocera", model
    except Exception:
        pass
        
    # Fallback: si usa Javascript (Knockout) el DOM inicial estará vacío, 
    # pero podemos detectar comentarios o scripts de Kyocera.
    if re.search(r"kyocera", html, re.IGNORECASE):
        # Buscar modelo en el HTML original (preservar capitalización)
        match = re.search(r"(ECOSYS[\w\s-]+|TASKalfa[\w\s-]+)", html)
        if match:
            return "Kyocera", match.group(1).strip()
        return "Kyocera", "Modelo oculto por JS"
        
    return "", ""

def _extract_via_pjl(ip: str, retries: int = 2) -> str:
    """
    Se conecta al puerto 9100 (JetDirect) y envía un comando PJL para pedir el modelo.
    Esto salta cualquier protección de Javascript en la web.
    Reintenta hasta *retries* veces para manejar respuestas lentas.
    """
    for attempt in range(retries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(4)   # 4 s — las Kyocera TASKalfa pueden tardar
            s.connect((ip, 9100))
            s.sendall(b'\x1b%-12345X@PJL INFO ID\r\n\x1b%-12345X')
            # Leer respuesta completa (puede venir en varios paquetes)
            chunks = []
            try:
                while True:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except socket.timeout:
                pass
            s.close()
            resp = b"".join(chunks).decode('utf-8', errors='ignore')
            # resp suele ser: @PJL INFO ID\r\n"ECOSYS M3655idn"\r\n
            match = re.search(r'"([^"]+)"', resp)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
    return ""

def _detect_brand_from_title(title: str) -> tuple[str, str]:
    """
    Compara el título contra los patrones de config.PRINTER_BRANDS.

    Returns:
        (marca, modelo) — modelo es el título completo si no se puede desglosar mejor.
    """
    title_lower = title.lower()
    for brand, keywords in config.PRINTER_BRANDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                # Extraer modelo: quitar la parte de la marca del título
                model = title.strip()
                return brand, model
    return "", title.strip()


def _is_printer_title(title: str) -> bool:
    """Devuelve True si el título sugiere que el dispositivo es una impresora."""
    title_lower = title.lower()
    for keywords in config.PRINTER_BRANDS.values():
        for kw in keywords:
            if kw.lower() in title_lower:
                return True
    # Palabras genéricas que suelen aparecer en impresoras
    generic = ["printer", "print server", "impresora", "laserjet", "inkjet",
               "mfp", "multifunction", "copier", "scanner"]
    return any(g in title_lower for g in generic)


# ─── Detección principal ─────────────────────────────────────────────────────

def identify_device(ip: str, open_ports: List[int]) -> PrinterInfo:
    """
    Intenta identificar si la IP es una impresora y obtiene su informacion.
    Estrategia de extraccion en orden de prioridad:
      1. <title>                           - Lexmark, generico
      2. <strong class=product> (header)   - HP EWS
      3. <p class=device-name>             - HP EWS (fallback)
      4. <h2 id=modelName> (frameHead)     - Ricoh / Aficio EWS
      5. <td id=info> (basicinfo)          - Kyocera EWS
    """
    info = PrinterInfo(ip=ip, open_ports=open_ports)
    info.hostname = _resolve_hostname(ip)

    # Construir URLs a probar según los puertos abiertos
    urls_to_try = []
    for port in [80, 443]:
        if port not in open_ports:
            continue
        scheme = "https" if port == 443 else "http"
        for path in config.EXTRA_PATHS:
            urls_to_try.append(f"{scheme}://{ip}{path}")

    # Intentar cada URL hasta obtener un nombre de producto válido
    for url in urls_to_try:
        html = _fetch_page(url)
        if not html:
            continue

        # --- Estrategia 1: <title> -----------------------------------------
        candidate = _extract_title(html)
        if candidate and _is_printer_title(candidate):
            brand, model = _detect_brand_from_title(candidate)
            info.title   = candidate
            info.url     = url
            info.brand   = brand or "Generica"
            info.model   = model
            info.is_printer = True
            break

        # --- Estrategia 2: <strong class="product"> en div#header (HP) -----
        candidate = _extract_product_tag(html)
        if candidate and _is_printer_title(candidate):
            brand, model = _detect_brand_from_title(candidate)
            info.title   = candidate
            info.url     = url
            info.brand   = brand or "Generica"
            info.model   = model
            info.is_printer = True
            break

        # --- Estrategia 3: <p class="device-name"> (HP fallback) -----------
        candidate = _extract_device_name(html)
        if candidate and _is_printer_title(candidate):
            brand, model = _detect_brand_from_title(candidate)
            info.title   = candidate
            info.url     = url
            info.brand   = brand or "Generica"
            info.model   = model
            info.is_printer = True
            break

        # --- Estrategia 4: <h2 id="modelName"> en frameHead (Ricoh) --------
        ricoh_brand, ricoh_model = _extract_ricoh_model(html)
        if ricoh_model:
            full_name = f"{ricoh_brand} {ricoh_model}".strip()
            info.title      = full_name
            info.url        = url
            info.brand      = ricoh_brand or "Ricoh"
            info.model      = full_name
            info.is_printer = True
            break

        # --- Estrategia 5: <td id="info"> en div#basicinfo (Kyocera) -------
        kyo_brand, kyo_model = _extract_kyocera_model(html)
        if kyo_model:
            # Si el modelo fue ocultado por JS, intentamos sacarlo por PJL (puerto 9100)
            if kyo_model == "Modelo oculto por JS" and 9100 in open_ports:
                pjl_model = _extract_via_pjl(ip)
                if pjl_model:
                    kyo_model = pjl_model

            full_name = f"{kyo_brand} {kyo_model}".strip()
            info.title      = full_name
            info.url        = url
            info.brand      = kyo_brand
            info.model      = kyo_model
            info.is_printer = True
            break

        # Guardar el titulo aunque no sea impresora (para depuracion)
        if not info.title and _extract_title(html):
            info.title = _extract_title(html)
            info.url   = url

    # --- Estrategia 6: PJL directo (puerto 9100) ----------------------------
    # Si la web no identificó el dispositivo, intentamos PJL como último recurso.
    # Esto cubre casos donde la web está completamente renderizada con JS.
    if not info.is_printer and 9100 in open_ports:
        pjl_model = _extract_via_pjl(ip)
        if pjl_model:
            # Detectar marca desde el nombre PJL
            brand, model = _detect_brand_from_title(pjl_model)
            if brand and _is_printer_title(pjl_model):
                info.title      = pjl_model
                info.brand      = brand
                info.model      = model
                info.is_printer = True
                if not info.url:
                    info.url = f"http://{ip}/"

    return info


def scan_devices(alive_ips: List[str], progress_callback=None) -> List[PrinterInfo]:
    """
    Escanea en paralelo todas las IPs activas para identificar impresoras.

    Args:
        alive_ips: Lista de IPs activas.
        progress_callback: Función opcional (ip, info) que se llama al procesar cada IP.

    Returns:
        Lista de PrinterInfo de TODAS las IPs (is_printer=True para las impresoras).
    """
    from scanner import scan_ports

    results: List[PrinterInfo] = []

    def _process(ip: str) -> PrinterInfo:
        ports = scan_ports(ip)
        open_ports = [p for p, open_ in ports.items() if open_]
        web_ports = [p for p in open_ports if p in config.WEB_PORTS]

        if web_ports:
            info = identify_device(ip, open_ports)
        else:
            # Sin puerto web, registramos el dispositivo sin identificar
            info = PrinterInfo(ip=ip, open_ports=open_ports)
            info.hostname = _resolve_hostname(ip)

        return info

    with ThreadPoolExecutor(max_workers=config.HTTP_THREADS) as executor:
        futures = {executor.submit(_process, ip): ip for ip in alive_ips}
        for future in as_completed(futures):
            ip = futures[future]
            try:
                info = future.result()
            except Exception as e:
                info = PrinterInfo(ip=ip, extra={"error": str(e)})

            results.append(info)
            if progress_callback:
                progress_callback(ip, info)

    return sorted(results, key=lambda x: tuple(int(p) for p in x.ip.split(".")))
