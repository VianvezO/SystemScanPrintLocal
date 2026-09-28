import requests
import ssl
from bs4 import BeautifulSoup
import re
import urllib3
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import asyncio
import logging
import time
import db
from config import PRINTERS_CONFIG

# ─── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─── Caché de estado de impresoras ────────────────────────────────────────────
CACHE_TTL_SECONDS = 300   # 5 minutos
_cache_data: list = []
_cache_timestamp: float = 0.0

def seed_initial():
    """Inicializa la BD y carga la config inicial SOLO si la tabla de impresoras está vacía."""
    db.init_db()
    existing = db.get_printers()
    if not existing:
        logger.info("BD vacía — cargando %d impresoras desde PRINTERS_CONFIG.", len(PRINTERS_CONFIG))
        db.seed_db_from_config(PRINTERS_CONFIG)
    else:
        logger.info("BD ya contiene %d impresoras — se omite el seed inicial.", len(existing))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        # Permitir conexiones a servidores heredados (Legacy)
        try:
            context.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except AttributeError:
            pass
            
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1
        except AttributeError:
            pass

        try:
            # SECLEVEL=0 permite algoritmos y protocolos antiguos que SECLEVEL=1 rechaza
            context.set_ciphers('DEFAULT@SECLEVEL=0')
        except Exception:
            try:
                context.set_ciphers('DEFAULT@SECLEVEL=1')
            except Exception:
                pass
                
        kwargs['ssl_context'] = context
        return super(LegacyTLSAdapter, self).init_poolmanager(*args, **kwargs)



def fetch_modern_printer(soup):
    results = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}
    
    # Toner
    toner_node = soup.find('li', id='TonerSupplies')
    if toner_node:
        data_text = toner_node.find('span', class_='dataText')
        if data_text:
            results["toner"] = data_text.text.strip()
    else:
        # Fallback para MS622de que podría no tener el ID en el LI, buscamos por texto
        headers = soup.find_all('div', class_='contentHeader')
        for header in headers:
            if 'Black Cartridge' in header.text or 'Cartucho negro' in header.text:
                container = header.find_parent('div', class_='contentRow')
                if container:
                    data_text = container.find('span', class_='dataText')
                    if data_text:
                        results["toner"] = data_text.text.strip()
                        break
                        
    # Imaging Unit
    drum_node = soup.find('li', id='PCDrumStatus')
    if drum_node:
        data_text = drum_node.find('span', class_='dataText')
        if data_text:
            results["imaging_unit"] = data_text.text.strip()
            
    # Maintenance Kit
    fuser_node = soup.find('li', id='FuserSuppliesStatus')
    if fuser_node:
        data_text = fuser_node.find('span', class_='dataText')
        if data_text:
            results["maintenance_kit"] = data_text.text.strip()
            
    return results

def fetch_classic_printer(soup):
    results = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}
    
    text_content = soup.get_text()
    
    # Toner - buscar Cartucho negro ~XX%
    toner_match = re.search(r'Cartucho negro\s*~?(\d+)', text_content, re.IGNORECASE)
    if toner_match:
        results["toner"] = toner_match.group(1)
        
    # Imaging Unit - buscar Unidad imagen Duración restante: XX%
    imaging_match = re.search(r'Unidad imagen[^:]*:\s*(\d+)', text_content, re.IGNORECASE)
    if imaging_match:
        results["imaging_unit"] = imaging_match.group(1)
        
    # Maintenance Kit - buscar Kit mantenimient Duración restante: XX%
    maint_match = re.search(r'Kit mantenimient[^:]*:\s*(\d+)', text_content, re.IGNORECASE)
    if maint_match:
        results["maintenance_kit"] = maint_match.group(1)
        
    return results

def fetch_hp_printer(soup):
    results = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}
    
    # Toner
    toner_gauge = soup.find('span', id='SupplyGauge0')
    if toner_gauge:
        results["toner"] = toner_gauge.text.replace('%', '').strip()
        
    # Maintenance Kit
    maint_gauge = soup.find('span', id='SupplyGauge1')
    if maint_gauge:
        results["maintenance_kit"] = maint_gauge.text.replace('%', '').strip()
        
    return results

def fetch_ricoh_printer(soup):
    results = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}
    
    # Check for empty cartridge
    if soup.find(lambda tag: tag.name == 'dd' and 'Cartucho vacío' in tag.get_text()):
        results["toner"] = "0"
    else:
        # Toner
        toner_area = soup.find('div', class_='tonerArea')
        if toner_area:
            img = toner_area.find('img')
            if img and img.has_attr('width'):
                try:
                    width = int(img['width'])
                    # El ancho maximo de esta imagen para el 100% de toner es 160px
                    percentage = int((width / 160.0) * 100)
                    results["toner"] = str(percentage)
                except ValueError:
                    pass
                
    return results

async def _snmp_get_async(ip, oid, community='public'):
    """Single SNMP GET using pysnmp asyncio API."""
    try:
        from pysnmp.hlapi.asyncio import (
            SnmpEngine, CommunityData, UdpTransportTarget,
            ContextData, ObjectType, ObjectIdentity, getCmd
        )
        snmpEngine = SnmpEngine()
        transport = UdpTransportTarget((ip, 161), timeout=3, retries=1)
        errorIndication, errorStatus, errorIndex, varBinds = await getCmd(
            snmpEngine,
            CommunityData(community, mpModel=0),
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )
        snmpEngine.closeDispatcher()
        if errorIndication or errorStatus:
            return None
        for vb in varBinds:
            return str(vb[1])
    except Exception:
        return None
    return None


def fetch_kyocera_printer_snmp(ip):
    """
    Lee el nivel del toner de una impresora Kyocera via SNMP.
    Usa los OIDs estándar del Printer-MIB (RFC 3805):
      - 1.3.6.1.2.1.43.11.1.1.6.1.N  -> Descripción del suministro
      - 1.3.6.1.2.1.43.11.1.1.9.1.N  -> Nivel actual
      - 1.3.6.1.2.1.43.11.1.1.8.1.N  -> Capacidad máxima
    El tóner en la Kyocera ECOSYS M3655idn es siempre el suministro índice 1.
    Porcentaje = (level / maxCapacity) * 100
    """
    results = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}

    async def _fetch():
        # Tóner - índice 1 (TK-3182)
        level = await _snmp_get_async(ip, f'1.3.6.1.2.1.43.11.1.1.9.1.1')
        maxcap = await _snmp_get_async(ip, f'1.3.6.1.2.1.43.11.1.1.8.1.1')
        return level, maxcap

    try:
        import threading
        result_holder = [None]
        exc_holder = [None]

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result_holder[0] = loop.run_until_complete(_fetch())
            except Exception as ex:
                exc_holder[0] = ex
            finally:
                loop.close()

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        t.join(timeout=10)

        if exc_holder[0]:
            raise exc_holder[0]

        if result_holder[0] is not None:
            level_str, maxcap_str = result_holder[0]
            if level_str is not None and maxcap_str is not None:
                level = int(level_str)
                maxcap = int(maxcap_str)
                # maxcap == -2 significa capacidad desconocida/ilimitada
                if maxcap > 0 and level >= 0:
                    pct = round((level / maxcap) * 100)
                    results["toner"] = str(pct)
                elif level >= 0:
                    results["toner"] = level_str
    except Exception as e:
        logger.error("SNMP error en Kyocera %s: %s", ip, e)

    return results

def get_printer_status(printer):
    protocol = printer.get("protocol", "https")
    url = f"{protocol}://{printer['ip']}{printer['url_path']}"
    result_data = {
        **printer,
        "toner": "N/A",
        "imaging_unit": "N/A",
        "maintenance_kit": "N/A",
        "status": "Offline"
    }
    
    try:
        session = requests.Session()
        session.mount('https://', LegacyTLSAdapter())
        response = session.get(url, verify=False, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        result_data["status"] = "Online"
        
        if printer["type"] == "modern":
            scraped = fetch_modern_printer(soup)
        elif printer["type"] == "classic":
            scraped = fetch_classic_printer(soup)
        elif printer["type"] == "hp":
            scraped = fetch_hp_printer(soup)
        elif printer["type"] == "ricoh":
            scraped = fetch_ricoh_printer(soup)
        elif printer["type"] == "kyocera":
            scraped = fetch_kyocera_printer_snmp(printer["ip"])
        else:
            scraped = {"toner": "N/A", "imaging_unit": "N/A", "maintenance_kit": "N/A"}
            
        result_data.update(scraped)
            
    except Exception as e:
        logger.warning("Error al consultar %s: %s", printer['ip'], e)
        
    return result_data

def get_all_printers_status(force_refresh: bool = False) -> list:
    """
    Devuelve el estado de todas las impresoras.
    Usa caché en memoria (TTL: CACHE_TTL_SECONDS) para no bloquear Flask
    en cada petición. Pasa force_refresh=True para saltarse la caché.
    """
    global _cache_data, _cache_timestamp

    now = time.monotonic()
    if not force_refresh and _cache_data and (now - _cache_timestamp) < CACHE_TTL_SECONDS:
        logger.debug("Devolviendo estado de impresoras desde caché (%.0fs restantes).",
                     CACHE_TTL_SECONDS - (now - _cache_timestamp))
        return _cache_data

    logger.info("Consultando estado de impresoras (force_refresh=%s)...", force_refresh)
    results = []
    printers = db.get_printers()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_printer = {executor.submit(get_printer_status, p): p for p in printers}
        for future in concurrent.futures.as_completed(future_to_printer):
            printer = future_to_printer[future]
            try:
                data = future.result()
                results.append(data)
            except Exception as exc:
                logger.error("%s generó una excepción: %s", printer["ip"], exc)
                results.append({**printer, "status": "Offline", "toner": "N/A",
                                 "imaging_unit": "N/A", "maintenance_kit": "N/A"})

    results = sorted(results, key=lambda k: (k['model'], k['ip']))

    # Actualizar caché
    _cache_data = results
    _cache_timestamp = time.monotonic()
    logger.info("Estado actualizado para %d impresoras. Caché válida por %ds.",
                len(results), CACHE_TTL_SECONDS)
    return results


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_all_printers_status(), indent=2))
