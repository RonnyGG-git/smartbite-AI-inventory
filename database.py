"""Capa de datos del SMART-BITE AI Inventory Agent (SQLite local).

Todo lo que aquí se calcula es determinista: la IA solo recibe estos
resultados, nunca los produce (ver 04-Modelo-de-datos en CEREBRO).
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).with_name("inventario.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    category      TEXT    NOT NULL DEFAULT 'General',
    quantity      REAL    NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    unit          TEXT    NOT NULL DEFAULT 'unidad',
    min_stock     REAL    NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
    unit_cost     REAL    NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    expiry_date   DATE,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_expiry   ON products(expiry_date);
"""

UNIDADES = ("kg", "lt", "unidad", "caja")
DIAS_VENCIMIENTO = 7

# (name, category, quantity, unit, min_stock, unit_cost, días hasta vencer o None)
DATOS_DEMO = [
    ("Carne de res", "Carnes", 2, "kg", 5, 20000, 5),
    ("Pechuga de pollo", "Carnes", 12, "kg", 8, 14000, 6),
    ("Leche entera", "Lácteos", 10, "lt", 6, 4500, 3),
    ("Queso mozzarella", "Lácteos", 3, "kg", 4, 28000, 10),
    ("Tomate", "Verduras", 0, "kg", 4, 3800, 2),
    ("Arroz", "Abarrotes", 25, "kg", 10, 3200, None),
    ("Aceite vegetal", "Abarrotes", 15, "lt", 5, 9000, None),
    ("Gaseosa 1.5 lt", "Bebidas", 24, "unidad", 12, 3500, 30),
]


# ---------------------------------------------------------------- conexión

def conectar(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db(db_path=None):
    """Conexión con commit/rollback automático que siempre se cierra."""
    conn = conectar(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path=None, con_demo=True):
    """Crea el esquema y, si la tabla está vacía, inserta los datos de demo."""
    with _db(db_path) as conn:
        conn.executescript(SCHEMA)
        vacia = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
        if con_demo and vacia:
            _insertar_demo(conn)


def restablecer_demo(db_path=None):
    """Borra todas las filas y reinserta los datos de demostración."""
    with _db(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'products'")
        _insertar_demo(conn)


def _insertar_demo(conn):
    hoy = date.today()
    for name, cat, qty, unit, minimo, costo, dias in DATOS_DEMO:
        expiry = (hoy + timedelta(days=dias)).isoformat() if dias is not None else None
        conn.execute(
            "INSERT INTO products (name, category, quantity, unit, min_stock, unit_cost, expiry_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, cat, qty, unit, minimo, costo, expiry),
        )


# ------------------------------------------------------------- validación

ETIQUETAS = {"quantity": "La cantidad", "min_stock": "El stock mínimo", "unit_cost": "El costo unitario"}


def _no_negativo(valor, campo):
    etiqueta = ETIQUETAS.get(campo, campo)
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{etiqueta} debe ser un número.") from None
    if numero < 0:
        raise ValueError(f"{etiqueta} no puede ser menor que 0 (recibido: {numero:g}).")
    return numero


def _fecha_valida(valor):
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor.isoformat()
    try:
        return datetime.strptime(str(valor), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("La fecha de vencimiento debe tener formato AAAA-MM-DD.") from None


def _nombre_valido(conn, name, excluir_id=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("El nombre del producto es obligatorio.")
    fila = conn.execute(
        "SELECT id FROM products WHERE lower(name) = lower(?) AND id IS NOT ?",
        (name, excluir_id),
    ).fetchone()
    if fila:
        raise ValueError(f"Ya existe un producto llamado '{name}'.")
    return name


# ------------------------------------------------------------------- CRUD

def crear_producto(name, quantity, min_stock, unit_cost, category="General",
                   unit="unidad", expiry_date=None, db_path=None):
    """Registra un producto y devuelve su id."""
    with _db(db_path) as conn:
        datos = (
            _nombre_valido(conn, name),
            (category or "General").strip() or "General",
            _no_negativo(quantity, "quantity"),
            unit or "unidad",
            _no_negativo(min_stock, "min_stock"),
            _no_negativo(unit_cost, "unit_cost"),
            _fecha_valida(expiry_date),
        )
        cur = conn.execute(
            "INSERT INTO products (name, category, quantity, unit, min_stock, unit_cost, expiry_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            datos,
        )
        nuevo_id = cur.lastrowid
    return nuevo_id


def listar_productos(db_path=None):
    with _db(db_path) as conn:
        filas = conn.execute("SELECT * FROM products ORDER BY category, name").fetchall()
    return [dict(f) for f in filas]


def obtener_producto(product_id, db_path=None):
    with _db(db_path) as conn:
        fila = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return dict(fila) if fila else None


def buscar_productos(texto, db_path=None):
    """Busca por nombre o categoría (sin distinguir mayúsculas)."""
    patron = f"%{(texto or '').strip()}%"
    with _db(db_path) as conn:
        filas = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR category LIKE ? ORDER BY name",
            (patron, patron),
        ).fetchall()
    return [dict(f) for f in filas]


def actualizar_cantidad(product_id, quantity, db_path=None):
    """Fija la existencia de un producto (entrada/salida de stock, HU-06)."""
    quantity = _no_negativo(quantity, "quantity")
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE products SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
            (quantity, product_id),
        )
    if cur.rowcount == 0:
        raise ValueError(f"No existe el producto con id {product_id}.")


def ajustar_stock(product_id, delta, db_path=None):
    """Suma (entrada) o resta (salida) al stock. Una salida mayor que la
    existencia se rechaza en vez de dejar la cantidad en negativo."""
    producto = obtener_producto(product_id, db_path)
    if producto is None:
        raise ValueError(f"No existe el producto con id {product_id}.")
    nueva = producto["quantity"] + float(delta)
    if nueva < 0:
        raise ValueError(
            f"Salida rechazada: solo hay {producto['quantity']:g} {producto['unit']} "
            f"de '{producto['name']}'."
        )
    actualizar_cantidad(product_id, nueva, db_path)
    return nueva


def actualizar_producto(product_id, name, min_stock, unit_cost, expiry_date=None,
                        category=None, unit=None, db_path=None):
    """Edita los datos descriptivos de un producto (no su cantidad)."""
    with _db(db_path) as conn:
        actual = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if actual is None:
            raise ValueError(f"No existe el producto con id {product_id}.")
        conn.execute(
            "UPDATE products SET name = ?, category = ?, unit = ?, min_stock = ?, "
            "unit_cost = ?, expiry_date = ?, updated_at = datetime('now') WHERE id = ?",
            (
                _nombre_valido(conn, name, excluir_id=product_id),
                category or actual["category"],
                unit or actual["unit"],
                _no_negativo(min_stock, "min_stock"),
                _no_negativo(unit_cost, "unit_cost"),
                _fecha_valida(expiry_date),
                product_id,
            ),
        )


def eliminar_producto(product_id, db_path=None):
    with _db(db_path) as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    return cur.rowcount > 0


# ------------------------------------------------- consultas deterministas

def _hoy_iso(hoy):
    return (hoy or date.today()).isoformat()


def productos_agotados(db_path=None):
    with _db(db_path) as conn:
        filas = conn.execute(
            "SELECT * FROM products WHERE quantity = 0 ORDER BY name"
        ).fetchall()
    return [dict(f) for f in filas]


def productos_stock_bajo(db_path=None):
    """Productos con existencia > 0 pero en o bajo el mínimo, con `missing`."""
    with _db(db_path) as conn:
        filas = conn.execute(
            "SELECT *, (min_stock - quantity) AS missing FROM products "
            "WHERE quantity > 0 AND quantity <= min_stock ORDER BY missing DESC, name"
        ).fetchall()
    return [dict(f) for f in filas]


def etiqueta_vencimiento(days_left):
    if days_left < 0:
        return "vencido"
    if days_left <= 2:
        return "por vencer (48 h)"
    return "próximo a vencer"


def dias_para_vencer(producto, hoy=None):
    """Días calendario hasta el vencimiento, o None si no es perecedero."""
    if not producto.get("expiry_date"):
        return None
    return (date.fromisoformat(producto["expiry_date"]) - (hoy or date.today())).days


def estado_producto(producto, n_days=DIAS_VENCIMIENTO, hoy=None):
    """Indicadores de riesgo de un producto, de más a menos severo.

    Un producto puede acumular riesgos (p. ej. agotado y por vencer).
    Devuelve una lista de (emoji, etiqueta); vacía = sin riesgo.
    """
    riesgos = []
    if producto["quantity"] == 0:
        riesgos.append(("🔴", "agotado"))
    elif producto["quantity"] <= producto["min_stock"]:
        riesgos.append(("🟡", "stock bajo"))
    dias = dias_para_vencer(producto, hoy)
    if dias is not None and dias <= n_days:
        if dias < 0:
            riesgos.append(("🟠", f"vencido hace {-dias} d"))
        elif dias == 0:
            riesgos.append(("🟠", "vence hoy"))
        else:
            riesgos.append(("🟠", f"vence en {dias} d"))
    return riesgos


def texto_estado(producto, n_days=DIAS_VENCIMIENTO, hoy=None):
    riesgos = estado_producto(producto, n_days, hoy)
    if not riesgos:
        return "🟢 OK"
    return " · ".join(f"{emoji} {etiqueta}" for emoji, etiqueta in riesgos)


def vencimientos_proximos(n_days=DIAS_VENCIMIENTO, db_path=None, hoy=None):
    """Productos que vencen en `n_days` días o menos (incluye vencidos).
    `days_left` se cuenta en días calendario desde `hoy` (por defecto, hoy local)."""
    with _db(db_path) as conn:
        filas = conn.execute(
            "SELECT *, CAST(julianday(expiry_date) - julianday(?) AS INTEGER) AS days_left "
            "FROM products WHERE expiry_date IS NOT NULL "
            "AND julianday(expiry_date) - julianday(?) <= ? ORDER BY expiry_date, name",
            (_hoy_iso(hoy), _hoy_iso(hoy), n_days),
        ).fetchall()
    resultado = [dict(f) for f in filas]
    for p in resultado:
        p["estado"] = etiqueta_vencimiento(p["days_left"])
    return resultado


def resumen_inventario(n_days=DIAS_VENCIMIENTO, db_path=None, hoy=None):
    """Las métricas del panel en una sola lectura (RF-09, RF-16)."""
    with _db(db_path) as conn:
        fila = conn.execute(
            "SELECT COUNT(*) AS total_products, "
            "COALESCE(SUM(quantity * unit_cost), 0) AS total_value, "
            "COALESCE(SUM(CASE WHEN quantity <= min_stock THEN 1 ELSE 0 END), 0) AS low_stock_count, "
            "COALESCE(SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END), 0) AS out_of_stock_count, "
            "COALESCE(SUM(CASE WHEN expiry_date IS NOT NULL "
            "  AND julianday(expiry_date) - julianday(?) <= ? THEN 1 ELSE 0 END), 0) AS expiring_count, "
            "COALESCE(SUM(CASE WHEN expiry_date IS NOT NULL "
            "  AND julianday(expiry_date) - julianday(?) <= ? THEN quantity * unit_cost ELSE 0 END), 0) "
            "  AS value_at_risk "
            "FROM products",
            (_hoy_iso(hoy), n_days, _hoy_iso(hoy), n_days),
        ).fetchone()
    return dict(fila)


def calcular_reposicion(db_path=None):
    """Productos en o bajo el mínimo, priorizados por impacto (missing × unit_cost)."""
    with _db(db_path) as conn:
        filas = conn.execute(
            "SELECT id, name, quantity, min_stock, unit, unit_cost, "
            "(min_stock - quantity) AS missing, "
            "(min_stock - quantity) * unit_cost AS estimated_cost "
            "FROM products WHERE quantity <= min_stock "
            "ORDER BY (min_stock - quantity) * unit_cost DESC, name"
        ).fetchall()
    pendientes = [dict(f) for f in filas]
    for prioridad, p in enumerate(pendientes, start=1):
        p["prioridad"] = prioridad
    return {
        "pendientes": pendientes,
        "costo_total_estimado": sum(p["estimated_cost"] for p in pendientes),
        "moneda": "COP",
    }
