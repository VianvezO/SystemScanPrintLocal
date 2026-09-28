import sqlite3
from contextlib import contextmanager
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DB_FILE = 'printers.db'

@contextmanager
def _get_conn():
    """Context manager que abre, provee y cierra una conexión SQLite."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Tabla de redes/sedes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS redes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_sede TEXT NOT NULL,
            red_cidr TEXT NOT NULL UNIQUE,
            descripcion TEXT,
            fecha_creado TEXT
        )
    ''')

    # Tabla de impresoras con FK a redes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS printers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            ip TEXT UNIQUE,
            model TEXT,
            type TEXT,
            url_path TEXT,
            protocol TEXT,
            personal TEXT,
            area TEXT,
            fecha_escaneo TEXT,
            red_id INTEGER REFERENCES redes(id) ON DELETE SET NULL
        )
    ''')

    # Migracion: agregar red_id si la tabla ya existia sin esa columna
    try:
        cursor.execute('ALTER TABLE printers ADD COLUMN red_id INTEGER REFERENCES redes(id) ON DELETE SET NULL')
    except Exception:
        pass

    conn.commit()
    conn.close()

# ─── REDES ────────────────────────────────────────────────────────────────────

def get_redes():
    with _get_conn() as conn:
        rows = conn.execute('''
            SELECT r.*, COUNT(p.id) as total_impresoras
            FROM redes r
            LEFT JOIN printers p ON p.red_id = r.id
            GROUP BY r.id
            ORDER BY r.nombre_sede
        ''').fetchall()
    return [dict(row) for row in rows]

def get_red(red_id):
    with _get_conn() as conn:
        row = conn.execute('SELECT * FROM redes WHERE id = ?', (red_id,)).fetchone()
    return dict(row) if row else None

def insert_red(nombre_sede, red_cidr, descripcion=''):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_conn() as conn:
            cursor = conn.execute(
                'INSERT INTO redes (nombre_sede, red_cidr, descripcion, fecha_creado) VALUES (?, ?, ?, ?)',
                (nombre_sede, red_cidr, descripcion, now)
            )
            return cursor.lastrowid, None
    except sqlite3.IntegrityError:
        return None, f"La red '{red_cidr}' ya existe."

def update_red(red_id, nombre_sede, red_cidr, descripcion=''):
    try:
        with _get_conn() as conn:
            conn.execute(
                'UPDATE redes SET nombre_sede=?, red_cidr=?, descripcion=? WHERE id=?',
                (nombre_sede, red_cidr, descripcion, red_id)
            )
        return True, None
    except sqlite3.IntegrityError:
        return False, f"La red '{red_cidr}' ya existe en otra sede."

def delete_red(red_id):
    with _get_conn() as conn:
        count = conn.execute('SELECT COUNT(*) FROM printers WHERE red_id = ?', (red_id,)).fetchone()[0]
        if count > 0:
            return False, f"No se puede eliminar la sede porque tiene {count} impresora(s) vinculada(s)."
        conn.execute('DELETE FROM redes WHERE id = ?', (red_id,))
        return True, None

# ─── IMPRESORAS ───────────────────────────────────────────────────────────────

def get_printers(red_id=None):
    with _get_conn() as conn:
        if red_id is not None:
            rows = conn.execute('''
                SELECT p.*, r.nombre_sede, r.red_cidr
                FROM printers p
                LEFT JOIN redes r ON p.red_id = r.id
                WHERE p.red_id = ?
            ''', (red_id,)).fetchall()
        else:
            rows = conn.execute('''
                SELECT p.*, r.nombre_sede, r.red_cidr
                FROM printers p
                LEFT JOIN redes r ON p.red_id = r.id
            ''').fetchall()
    return [dict(r) for r in rows]

def insert_printer(printer):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    personal = printer.get('personal', '')
    area = printer.get('area', '')
    name = printer.get('name', '')
    red_id = printer.get('red_id') or None

    if name and not personal and not area and ' - ' in name:
        parts = name.split(' - ')
        personal = parts[0]
        area = parts[1] if len(parts) > 1 else ''

    try:
        with _get_conn() as conn:
            conn.execute('''
                INSERT INTO printers (name, ip, model, type, url_path, protocol, personal, area, fecha_escaneo, red_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name,
                printer.get('ip', ''),
                printer.get('model', ''),
                printer.get('type', ''),
                printer.get('url_path', ''),
                printer.get('protocol', 'http'),
                personal, area, now, red_id
            ))
    except sqlite3.IntegrityError:
        with _get_conn() as conn:
            conn.execute('''
                UPDATE printers
                SET fecha_escaneo=?, model=?, type=?, url_path=?, protocol=?,
                    name=COALESCE(NULLIF(?, ''), name),
                    personal=COALESCE(NULLIF(?, ''), personal),
                    area=COALESCE(NULLIF(?, ''), area),
                    red_id=COALESCE(?, red_id)
                WHERE ip=?
            ''', (
                now,
                printer.get('model', ''),
                printer.get('type', ''),
                printer.get('url_path', ''),
                printer.get('protocol', 'http'),
                name, personal, area, red_id,
                printer.get('ip', '')
            ))

def seed_db_from_config(config):
    for p in config:
        insert_printer(p)

