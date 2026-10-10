"""Interfaz Streamlit del SMART-BITE AI Inventory Agent.

Arranque:  python -m streamlit run app.py
Panel de gestión: métricas, chat con el agente, tabla con estados, registro,
edición, entrada/salida de stock, informe y restablecer demo.
El chat funciona sin API key (análisis por reglas).

Regla de estructura: aquí no hay SQL (database.py) ni prompts (agent.py).
"""

from datetime import date, timedelta

import streamlit as st

import agent
import database as db

UNIDADES = list(db.UNIDADES)


# 1. configuración y estado de sesión ---------------------------------------

st.set_page_config(page_title="SMART-BITE · Inventario IA", page_icon="🍽️", layout="wide")
db.init_db()
st.session_state.setdefault("flash", None)
st.session_state.setdefault("ultima_sel", None)
st.session_state.setdefault("chat", [])          # historial: {role, content, modo, aviso, fuente, modelo}
st.session_state.setdefault("ia_fallo", False)   # la última consulta con clave cayó a reglas
st.session_state.setdefault("pregunta_sugerida", None)

SUGERENCIAS = ["¿Qué debo comprar primero y por qué?",
               "¿Qué vence esta semana?",
               "¿Cuánto vale el inventario y dónde está el riesgo?"]


def flash(mensaje, tipo="success"):
    """Mensaje que sobrevive al st.rerun() posterior a una escritura."""
    st.session_state.flash = (tipo, mensaje)


def mostrar_flash():
    if st.session_state.flash:
        tipo, mensaje = st.session_state.flash
        getattr(st, tipo)(mensaje)
        st.session_state.flash = None


# Lectura única de datos por render, reutilizada en todas las zonas.
hoy = date.today()
productos = db.listar_productos()
resumen = db.resumen_inventario(hoy=hoy)
por_id = {p["id"]: p for p in productos}
categorias = sorted({p["category"] for p in productos} | {"General"})


# 2. cabecera ---------------------------------------------------------------

def render_cabecera():
    izq, der = st.columns([4, 1])
    izq.title("🧠 SMART-BITE · Agente de Inventario")
    izq.caption("Prototipo — no conectado a producción de SMART-BITE. Datos locales SQLite.")
    config = agent.configuracion()
    der.markdown("&nbsp;")
    if not agent.hay_api_key(config):
        der.markdown("⚪ **IA: sin configurar**  \n<small>respuestas por reglas · falta la clave en .env</small>",
                     unsafe_allow_html=True)
    elif st.session_state.ia_fallo:
        der.markdown("🟡 **IA: modo degradado**  \n<small>la última consulta usó el análisis por reglas</small>",
                     unsafe_allow_html=True)
    else:
        der.markdown(f"🟢 **IA conectada**  \n<small>{agent.describir_proveedor(config)}</small>",
                     unsafe_allow_html=True)


# 3. métricas ---------------------------------------------------------------

def render_metricas():
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Productos", resumen["total_products"])
    c2.metric("Alertas de stock", resumen["low_stock_count"],
              help=f"Productos en o bajo el mínimo (incluye {resumen['out_of_stock_count']} agotado(s)).")
    c3.metric(f"Por vencer ({db.DIAS_VENCIMIENTO} d)", resumen["expiring_count"],
              help=f"Valor en riesgo: {agent.cop(resumen['value_at_risk'])} (incluye vencidos).")
    c4.metric("Valor inventario", agent.cop(resumen["total_value"]),
              help="Σ cantidad × costo unitario, en COP.")


# 4. chat con el agente -----------------------------------------------------

def md(texto):
    """Escapa '$' para que Streamlit no lo interprete como fórmula LaTeX ($ 60.000 … $ 15.200)."""
    return texto.replace("$", r"\$")


def render_datos_usados(fuente):
    """Transparencia: las tablas exactas que recibió el agente."""
    with st.expander("🔎 Datos usados (calculados con SQL, no por la IA)"):
        reposicion = fuente["reposicion"]
        if reposicion["pendientes"]:
            st.markdown(md(f"**Reposición** · costo total estimado "
                           f"{agent.cop(reposicion['costo_total_estimado'])}"))
            st.dataframe(
                [{"#": p["prioridad"], "Producto": p["name"],
                  "Existencia": agent.cantidad(p["quantity"], p["unit"]),
                  "Mínimo": agent.cantidad(p["min_stock"], p["unit"]),
                  "Faltan (missing)": agent.cantidad(p["missing"], p["unit"]),
                  "Costo estimado": agent.cop(p["estimated_cost"])}
                 for p in reposicion["pendientes"]],
                hide_index=True, width="stretch")
        riesgos = fuente["riesgos"]
        if riesgos["vencimientos"]:
            st.markdown("**Vencimientos**")
            st.dataframe(
                [{"Producto": p["name"], "Vence": p["expiry_date"], "Días": p["days_left"],
                  "Estado": p["estado"], "Existencia": agent.cantidad(p["quantity"], p["unit"]),
                  "Valor en riesgo": agent.cop(p["valor_en_riesgo"])}
                 for p in riesgos["vencimientos"]],
                hide_index=True, width="stretch")
        r = fuente["resumen"]
        st.caption(md(f"Resumen: {r['total_products']} productos · valor {agent.cop(r['total_value'])} · "
                      f"valor en riesgo {agent.cop(r['value_at_risk'])} · datos generados "
                      f"{riesgos['generado_en']}"))


def render_mensaje(msg):
    with st.chat_message(msg["role"], avatar="🧑‍🍳" if msg["role"] == "user" else "🧠"):
        if msg.get("aviso"):
            st.warning(msg["aviso"])
        st.markdown(md(msg["content"]))
        if msg["role"] == "assistant":
            st.caption(f"Modo: IA ({msg['modelo']})" if msg["modo"] == "ia"
                       else "Modo: análisis por reglas (sin IA)")
            render_datos_usados(msg["fuente"])


def preguntar(pregunta):
    historial = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat]
    st.session_state.chat.append({"role": "user", "content": pregunta})
    with st.spinner("El agente está analizando el inventario…"):
        r = agent.consultar(pregunta, historial=historial)
    st.session_state.chat.append({"role": "assistant", "content": r["respuesta"], "modo": r["modo"],
                                  "aviso": r["aviso"], "fuente": r["fuente"], "modelo": r["modelo"]})
    st.session_state.ia_fallo = r["modo"] == "reglas" and agent.hay_api_key()


def ultima_respuesta_ia():
    """Última pregunta + respuesta en modo IA, para anexarla al informe."""
    chat = st.session_state.chat
    for i in range(len(chat) - 1, 0, -1):
        if chat[i]["role"] == "assistant" and chat[i]["modo"] == "ia":
            return f"**Pregunta:** {chat[i - 1]['content']}\n\n{chat[i]['content']}"
    return None


def render_chat():
    st.subheader("💬 Preguntar al agente")
    columnas = st.columns(len(SUGERENCIAS) + 1)
    for col, sugerencia in zip(columnas, SUGERENCIAS):
        if col.button(sugerencia, width="stretch"):
            st.session_state.pregunta_sugerida = sugerencia
    if columnas[-1].button("🧹 Limpiar chat", width="stretch", disabled=not st.session_state.chat):
        st.session_state.chat = []
        st.rerun()

    if st.session_state.chat:
        with st.container(height=460):
            for msg in st.session_state.chat:
                render_mensaje(msg)
    with st.container():  # dentro de un contenedor el input queda junto al chat, no fijo al pie
        pregunta = st.chat_input("Pregúntale al agente: ¿qué comprar primero?…")
    pregunta = pregunta or st.session_state.pregunta_sugerida
    st.session_state.pregunta_sugerida = None
    if pregunta:
        preguntar(pregunta)
        st.rerun()
    st.caption("El agente recomienda; las decisiones de compra y los cambios de inventario son tuyos.")


# 5. filtros (sidebar) y tabla ----------------------------------------------

def render_filtros():
    st.sidebar.header("Filtros")
    texto = st.sidebar.text_input("Buscar por nombre", placeholder="ej: leche")
    cats = st.sidebar.multiselect("Categoría", categorias, placeholder="Todas")
    vista = st.sidebar.radio("Estado", ["Todos", "Con riesgo", "Vencimientos"])
    st.sidebar.caption("🔴 agotado · 🟡 stock bajo · 🟠 por vencer/vencido · 🟢 OK")
    return texto, cats, vista


def filtrar(texto, cats, vista):
    filas = []
    for p in productos:
        riesgos = db.estado_producto(p, hoy=hoy)
        if texto and texto.strip().lower() not in p["name"].lower():
            continue
        if cats and p["category"] not in cats:
            continue
        if vista == "Con riesgo" and not riesgos:
            continue
        if vista == "Vencimientos" and not any(e == "🟠" for e, _ in riesgos):
            continue
        filas.append({**p, "estado": db.texto_estado(p, hoy=hoy),
                      "existencia": agent.cantidad(p["quantity"], p["unit"]),
                      "minimo": agent.cantidad(p["min_stock"], p["unit"]),
                      "costo": agent.cop(p["unit_cost"]),
                      "vence": p["expiry_date"] or "no perecedero"})
    return filas


def render_tabla(filas):
    st.subheader("📦 Inventario")
    if not productos:
        st.info("El inventario está vacío. Registra el primer producto o restablece la demo.")
        return
    if not filas:
        st.info("Ningún producto coincide con los filtros.")
        return
    evento = st.dataframe(
        filas,
        column_order=["estado", "name", "existencia", "minimo", "costo", "vence", "category"],
        column_config={
            "estado": st.column_config.TextColumn("Estado", width="medium"),
            "name": "Producto",
            "existencia": "Cantidad",
            "minimo": "Mínimo",
            "category": "Categoría",
            "costo": "Costo unit.",
            "vence": "Vence",
        },
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="tabla",
    )
    seleccion = evento.selection.rows
    elegido = filas[seleccion[0]]["id"] if seleccion else None
    if elegido is not None and elegido != st.session_state.ultima_sel:
        # Nueva fila elegida → los dos selectores del panel derecho la siguen.
        st.session_state.ajuste_sel = st.session_state.editar_sel = elegido
    st.session_state.ultima_sel = elegido
    st.caption("Selecciona una fila para editarla o ajustar su stock en el panel derecho.")


# 6. formularios ------------------------------------------------------------

def campo_vencimiento(prefijo, actual=None):
    """Fecha opcional: 'No perecedero' guarda NULL."""
    no_perecedero = st.checkbox("No perecedero", value=actual is None and prefijo != "nuevo",
                                key=f"{prefijo}_np")
    fecha = st.date_input("Fecha de vencimiento",
                          value=date.fromisoformat(actual) if actual else hoy + timedelta(days=7),
                          format="YYYY-MM-DD", key=f"{prefijo}_fecha")
    return None if no_perecedero else fecha


def validar_fecha_pasada(fecha, confirmada):
    if fecha is not None and fecha < hoy and not confirmada:
        st.error("La fecha de vencimiento ya pasó: el producto está vencido. "
                 "Marca «Registrar aunque esté vencido» para continuar.")
        return False
    return True


def render_registrar():
    with st.form("form_registrar"):
        nombre = st.text_input("Nombre *", placeholder="ej: Papas fritas", key="nuevo_nombre")
        c1, c2 = st.columns(2)
        categoria = c1.selectbox("Categoría", categorias, index=categorias.index("General"))
        nueva_cat = c2.text_input("…o nueva categoría", placeholder="ej: Congelados", key="nuevo_cat")
        c1, c2 = st.columns(2)
        cant = c1.number_input("Cantidad *", step=1.0, format="%g", key="nuevo_cant")
        unidad = c2.selectbox("Unidad", UNIDADES, key="nuevo_unidad")
        c1, c2 = st.columns(2)
        minimo = c1.number_input("Stock mínimo", step=1.0, format="%g", key="nuevo_min")
        costo = c2.number_input("Costo unitario (COP)", step=500.0, format="%g",
                                key="nuevo_costo")
        fecha = campo_vencimiento("nuevo")
        vencido_ok = st.checkbox("Registrar aunque esté vencido", key="nuevo_vencido")
        if st.form_submit_button("➕ Registrar", type="primary"):
            if not validar_fecha_pasada(fecha, vencido_ok):
                return
            try:
                db.crear_producto(nombre, cant, minimo, costo,
                                  category=nueva_cat.strip() or categoria,
                                  unit=unidad, expiry_date=fecha)
            except ValueError as e:
                st.error(str(e))
                return
            for clave in [k for k in st.session_state if str(k).startswith("nuevo_")]:
                del st.session_state[clave]  # formulario limpio solo tras un registro exitoso
            flash(f"Producto «{nombre.strip()}» registrado.")
            st.rerun()


def selector_producto(clave):
    """Selector de producto que arranca en la fila seleccionada de la tabla."""
    ids = [p["id"] for p in productos]
    if st.session_state.get(clave) not in ids:  # primer render o producto eliminado
        st.session_state[clave] = ids[0]
    pid = st.selectbox("Producto", ids, key=clave,
                       format_func=lambda i: f"{por_id[i]['name']} — {db.texto_estado(por_id[i], hoy=hoy)}")
    return por_id[pid]


def render_ajuste():
    if not productos:
        st.info("No hay productos para ajustar.")
        return
    p = selector_producto("ajuste_sel")
    st.caption(f"Existencia actual: **{agent.cantidad(p['quantity'], p['unit'])}** · "
               f"mínimo {agent.cantidad(p['min_stock'], p['unit'])}")
    with st.form(f"form_ajuste_{p['id']}", clear_on_submit=True):
        tipo = st.radio("Movimiento", ["➖ Salida (consumo / merma)", "➕ Entrada (compra recibida)"],
                        horizontal=True)
        cant = st.number_input(f"Cantidad ({p['unit']})", step=1.0, format="%g")
        if st.form_submit_button("Aplicar", type="primary"):
            if cant <= 0:
                st.error("La cantidad del movimiento debe ser mayor que 0.")
                return
            delta = -cant if tipo.startswith("➖") else cant
            try:
                nueva = db.ajustar_stock(p["id"], delta)
            except ValueError as e:
                st.error(str(e))
                return
            actualizado = {**p, "quantity": nueva}
            flash(f"{p['name']}: nuevo saldo {agent.cantidad(nueva, p['unit'])} "
                  f"→ {db.texto_estado(actualizado, hoy=hoy)}")
            st.rerun()


@st.dialog("Eliminar producto")
def dialogo_eliminar(p):
    st.write(f"¿Eliminar **{p['name']}** del inventario? Esta acción no se puede deshacer.")
    c1, c2 = st.columns(2)
    if c1.button("🗑️ Sí, eliminar", type="primary"):
        db.eliminar_producto(p["id"])
        flash(f"Producto «{p['name']}» eliminado.")
        st.rerun()
    if c2.button("Cancelar"):
        st.rerun()


def render_editar():
    if not productos:
        st.info("No hay productos para editar.")
        return
    p = selector_producto("editar_sel")
    sufijo = f"edit_{p['id']}"
    with st.form(f"form_{sufijo}"):
        nombre = st.text_input("Nombre *", value=p["name"])
        c1, c2 = st.columns(2)
        cats = sorted(set(categorias) | {p["category"]})
        categoria = c1.selectbox("Categoría", cats, index=cats.index(p["category"]))
        unidades = UNIDADES if p["unit"] in UNIDADES else UNIDADES + [p["unit"]]
        unidad = c2.selectbox("Unidad", unidades, index=unidades.index(p["unit"]))
        c1, c2 = st.columns(2)
        minimo = c1.number_input("Stock mínimo", value=float(p["min_stock"]),
                                 step=1.0, format="%g")
        costo = c2.number_input("Costo unitario (COP)", value=float(p["unit_cost"]),
                                step=500.0, format="%g")
        fecha = campo_vencimiento(sufijo, p["expiry_date"])
        vencido_ok = st.checkbox("Guardar aunque esté vencido", key=f"{sufijo}_vencido")
        st.caption("La cantidad se cambia en la pestaña «Stock».")
        if st.form_submit_button("💾 Guardar cambios", type="primary"):
            if not validar_fecha_pasada(fecha, vencido_ok):
                return
            try:
                db.actualizar_producto(p["id"], nombre, minimo, costo, fecha,
                                       category=categoria, unit=unidad)
            except ValueError as e:
                st.error(str(e))
                return
            flash(f"Producto «{nombre.strip()}» actualizado.")
            st.rerun()
    if st.button("🗑️ Eliminar producto", key=f"del_{p['id']}"):
        dialogo_eliminar(p)


def render_formularios():
    tab_ajuste, tab_nuevo, tab_editar = st.tabs(["▶️ Stock", "➕ Nuevo", "✏️ Editar"])
    with tab_ajuste:
        render_ajuste()
    with tab_nuevo:
        render_registrar()
    with tab_editar:
        render_editar()


# 7. pie: informe y restablecer ---------------------------------------------

@st.dialog("Restablecer demo")
def dialogo_restablecer():
    st.write("Se borrarán **todos** los productos y se cargarán los 8 productos de demostración.")
    c1, c2 = st.columns(2)
    if c1.button("♻️ Sí, restablecer", type="primary"):
        db.restablecer_demo()
        flash("Demo restablecida: inventario igual al primer arranque.")
        st.rerun()
    if c2.button("Cancelar"):
        st.rerun()


def render_pie():
    st.divider()
    c1, c2, _ = st.columns([1, 1, 2])
    c1.download_button("📥 Descargar informe Markdown",
                       data=agent.generar_informe(hoy=hoy, recomendacion_ia=ultima_respuesta_ia()),
                       file_name=f"informe_inventario_{hoy.isoformat()}.md",
                       mime="text/markdown", type="primary")
    if c2.button("♻️ Restablecer demo"):
        dialogo_restablecer()
    st.caption("Prototipo académico · datos locales SQLite · no conectado al backend SMART-BITE")


# render ---------------------------------------------------------------------

render_cabecera()
mostrar_flash()
render_metricas()
st.divider()
render_chat()
st.divider()
filtros = render_filtros()
col_tabla, col_form = st.columns([5, 2], gap="medium")
with col_tabla:
    render_tabla(filtrar(*filtros))
with col_form:
    render_formularios()
render_pie()
