from flask import Flask, render_template, jsonify, send_file, request
from scraper import get_all_printers_status, seed_initial
import io
import openpyxl
import db
import logging
import threading
import uuid

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize DB on startup
with app.app_context():
    seed_initial()

import scanner
import printer_detector
import config

# ─── Tareas de escaneo en background ─────────────────────────────────────────
# Diccionario {task_id: {"status": "running"|"done"|"error", "result": [...], "error": str}}
_scan_tasks: dict = {}
_scan_lock = threading.Lock()


def _run_scan(task_id: str, network: str, red_id):
    """Ejecuta el escaneo de red en un hilo separado y guarda el resultado."""
    try:
        logger.info("Escaneo iniciado — red=%s red_id=%s task_id=%s", network, red_id, task_id)
        alive_hosts = scanner.ping_sweep(network)
        discovered = []
        if alive_hosts:
            results = printer_detector.scan_devices(alive_hosts)
            for r in results:
                if r.is_printer:
                    brand = r.brand.lower() if r.brand else "unknown"
                    model_upper = (r.model or "").upper()
                    
                    p_type = brand
                    p_url = "/"
                    p_protocol = "http" if 80 in r.open_ports else "https"
                    
                    if brand == "lexmark":
                        # Distinguir classic vs modern según modelo
                        if any(m in model_upper for m in ["MS415", "MX711", "MS810", "MS610","MS811"]):
                            p_type = "classic"
                            p_url = "/cgi-bin/dynamic/printer/PrinterStatus.html"
                        else:
                            p_type = "modern"
                            p_url = "/webglue/content?c=Status"
                    elif brand == "hp":
                        p_type = "hp"
                        p_url = "/"
                    elif brand == "ricoh":
                        p_type = "ricoh"
                        p_url = "/web/guest/es/websys/webArch/getStatus.cgi"
                    elif brand == "kyocera":
                        p_type = "kyocera"
                        p_url = "/"

                    p_dict = {
                        "ip": r.ip,
                        "model": r.model,
                        "type": p_type,
                        "url_path": p_url,
                        "protocol": p_protocol,
                        "name": r.title,
                        "red_id": red_id
                    }
                    db.insert_printer(p_dict)
                    discovered.append(p_dict)

        logger.info("Escaneo completado — %d impresoras encontradas (task_id=%s)", len(discovered), task_id)
        with _scan_lock:
            _scan_tasks[task_id] = {"status": "done", "result": discovered}
    except Exception as exc:
        logger.error("Error en escaneo task_id=%s: %s", task_id, exc)
        with _scan_lock:
            _scan_tasks[task_id] = {"status": "error", "error": str(exc)}


# ─── PAGINAS ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', default_network=config.NETWORK_RANGE)

@app.route('/sedes')
def sedes():
    return render_template('sedes.html')

# ─── API: IMPRESORAS ─────────────────────────────────────────────────────────

@app.route('/api/printers')
def api_printers():
    red_id = request.args.get('red_id', type=int)
    force = request.args.get('refresh', '').lower() in ('1', 'true')
    data = get_all_printers_status(force_refresh=force)
    if red_id:
        db_printers = {p['ip']: p for p in db.get_printers(red_id=red_id)}
        data = [p for p in data if p.get('ip') in db_printers]
    return jsonify(data)

@app.route('/api/db_printers')
def api_db_printers():
    red_id = request.args.get('red_id', type=int)
    printers = db.get_printers(red_id=red_id)
    return jsonify(printers)

@app.route('/api/update_printer', methods=['POST'])
def update_printer():
    data = request.json
    if not data or 'ip' not in data:
        return jsonify({"error": "IP is required"}), 400
    db.insert_printer(data)
    return jsonify({"success": True})

@app.route('/api/scan', methods=['POST'])
def api_scan():
    """
    Inicia un escaneo de red en background y devuelve un task_id inmediatamente.
    Consulta el resultado con GET /api/scan/<task_id>.
    """
    data = request.json or {}
    network = data.get('network', config.NETWORK_RANGE)
    red_id = data.get('red_id') or None

    task_id = str(uuid.uuid4())
    with _scan_lock:
        _scan_tasks[task_id] = {"status": "running"}

    thread = threading.Thread(target=_run_scan, args=(task_id, network, red_id), daemon=True)
    thread.start()

    logger.info("Escaneo encolado — task_id=%s red=%s", task_id, network)
    return jsonify({"success": True, "task_id": task_id, "status": "running"}), 202

@app.route('/api/scan/<task_id>', methods=['GET'])
def api_scan_status(task_id: str):
    """Consulta el estado de un escaneo en background."""
    with _scan_lock:
        task = _scan_tasks.get(task_id)
    if task is None:
        return jsonify({"error": "task_id no encontrado"}), 404
    return jsonify(task)

# ─── API: REDES/SEDES ─────────────────────────────────────────────────────────

@app.route('/api/redes', methods=['GET'])
def api_get_redes():
    return jsonify(db.get_redes())

@app.route('/api/redes', methods=['POST'])
def api_create_red():
    data = request.json
    if not data or not data.get('nombre_sede') or not data.get('red_cidr'):
        return jsonify({"error": "nombre_sede y red_cidr son requeridos"}), 400
    new_id, error = db.insert_red(
        data['nombre_sede'],
        data['red_cidr'],
        data.get('descripcion', '')
    )
    if error:
        return jsonify({"error": error}), 409
    return jsonify({"success": True, "id": new_id}), 201

@app.route('/api/redes/<int:red_id>', methods=['PUT'])
def api_update_red(red_id):
    data = request.json
    if not data or not data.get('nombre_sede') or not data.get('red_cidr'):
        return jsonify({"error": "nombre_sede y red_cidr son requeridos"}), 400
    ok, error = db.update_red(
        red_id,
        data['nombre_sede'],
        data['red_cidr'],
        data.get('descripcion', '')
    )
    if not ok:
        return jsonify({"error": error}), 409
    return jsonify({"success": True})

@app.route('/api/redes/<int:red_id>', methods=['DELETE'])
def api_delete_red(red_id):
    success, error = db.delete_red(red_id)
    if not success:
        return jsonify({"error": error}), 409
    return jsonify({"success": True})

# ─── EXPORTAR ─────────────────────────────────────────────────────────────────

@app.route('/api/export')
def api_export():
    red_id = request.args.get('red_id', type=int)
    data = get_all_printers_status()

    # Enriquecer con datos de DB (personal, area, sede)
    db_map = {p['ip']: p for p in db.get_printers(red_id=red_id)}
    if red_id:
        data = [p for p in data if p.get('ip') in db_map]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estado Suministros"

    headers = ["Usuario / Impresora", "Personal", "Área", "Sede", "Red", "Modelo",
               "Dirección IP", "Tóner (%)", "Unidad de Imagen (%)", "Kit Mantenimiento (%)", "Estado"]
    ws.append(headers)

    for printer in data:
        db_info = db_map.get(printer.get('ip'), {})
        ws.append([
            printer.get("name", "N/A"),
            db_info.get("personal", "N/A"),
            db_info.get("area", "N/A"),
            db_info.get("nombre_sede", "N/A"),
            db_info.get("red_cidr", "N/A"),
            printer.get("model", "N/A"),
            printer.get("ip", "N/A"),
            printer.get("toner", "N/A"),
            printer.get("imaging_unit", "N/A"),
            printer.get("maintenance_kit", "N/A"),
            printer.get("status", "N/A")
        ])

    for col in ws.columns:
        max_length = max((len(str(cell.value or '')) for cell in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = max_length + 2

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    return send_file(
        out,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Reporte_Suministros.xlsx'
    )

# ─── Manejo global de errores ─────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Ruta no encontrada"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Método no permitido"}), 405

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Error interno del servidor: %s", e)
    return jsonify({"error": "Error interno del servidor. Revisa los logs para más detalles."}), 500

# ─── Entrada principal ────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(config.os.getenv("FLASK_PORT", 5000))
    debug = config.os.getenv("FLASK_ENV", "production") == "development"
    app.run(debug=debug, host='0.0.0.0', port=port)
