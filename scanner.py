"""
scanner.py — Escaneo de red: ping sweep + verificación de puertos TCP.
"""

import socket
import struct
import ipaddress
import subprocess
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

import config


def _ping(ip: str) -> bool:
    """Envía un ping al host y devuelve True si responde."""
    param = "-n" if platform.system().lower() == "windows" else "-c"
    timeout_param = "-w" if platform.system().lower() == "windows" else "-W"
    timeout_val = "500" if platform.system().lower() == "windows" else "1"
    cmd = ["ping", param, "1", timeout_param, timeout_val, ip]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=config.TIMEOUT_PING + 0.5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _tcp_connect(ip: str, port: int) -> bool:
    """Intenta abrir una conexión TCP al puerto dado; devuelve True si tiene éxito."""
    try:
        with socket.create_connection((ip, port), timeout=config.TIMEOUT_PING):
            return True
    except Exception:
        return False


def _host_is_alive(ip: str) -> bool:
    """
    Un host se considera vivo si responde al ping O tiene algún puerto relevante abierto.
    El doble método evita falsos negativos cuando el firewall bloquea ICMP.
    """
    if _ping(ip):
        return True
    # Fallback: intenta conectar a los puertos definidos en config
    for port in config.PRINTER_PORTS:
        if _tcp_connect(ip, port):
            return True
    return False


def ping_sweep(network_range: str, callback=None) -> List[str]:
    """
    Escanea todos los hosts en *network_range* en paralelo.
    Soporta múltiples rangos separados por coma (ej. "192.168.1.0/24, 192.168.2.0/24").

    Args:
        network_range: Cadena CIDR o lista de cadenas separadas por coma.
        callback: Función opcional que se llama con (ip, alive) por cada host procesado.

    Returns:
        Lista de IPs activas.
    """
    hosts = []
    for net_str in network_range.split(','):
        net_str = net_str.strip()
        if net_str:
            try:
                network = ipaddress.ip_network(net_str, strict=False)
                hosts.extend([str(h) for h in network.hosts()])
            except ValueError:
                pass

    alive: List[str] = []

    with ThreadPoolExecutor(max_workers=config.PING_THREADS) as executor:
        futures = {executor.submit(_host_is_alive, ip): ip for ip in hosts}
        for future in as_completed(futures):
            ip = futures[future]
            is_alive = future.result()
            if is_alive:
                alive.append(ip)
            if callback:
                callback(ip, is_alive)

    return sorted(alive, key=lambda x: tuple(int(p) for p in x.split(".")))


def scan_ports(ip: str, ports: List[int] = None) -> Dict[int, bool]:
    """
    Verifica qué puertos de *ports* están abiertos en *ip*.

    Returns:
        Dict {puerto: True/False}
    """
    if ports is None:
        ports = config.PRINTER_PORTS

    results = {}
    for port in ports:
        results[port] = _tcp_connect(ip, port)
    return results


def get_open_web_ports(ip: str) -> List[int]:
    """Devuelve los puertos web (80, 443) abiertos en *ip*."""
    return [p for p in config.WEB_PORTS if _tcp_connect(ip, p)]


def get_local_network() -> str:
    """
    Auto-detecta el rango de red local /24 a partir de la IP del host.
    Se usa cuando NETWORK_RANGE es None en config.py.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.rsplit(".", 1)
        return f"{parts[0]}.0/24"
    except Exception:
        return "192.168.1.0/24"
