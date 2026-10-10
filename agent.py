"""Agente de IA del SMART-BITE AI Inventory Agent.

Regla de oro: el código calcula, la IA interpreta. Las herramientas leen
SQLite; el modelo solo recibe sus resultados y nunca escribe en la base.
Sin API key o ante un error de la API, se responde con el análisis por
reglas (modo degradado). Ver 05-Agente-y-herramientas en CEREBRO.
"""

import json
import logging
import os
import re
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

from dotenv import dotenv_values

import database as db

log = logging.getLogger("smartbite.agent")

ENV_PATH = Path(__file__).with_name(".env")
MODELO_POR_DEFECTO = "gpt-4o-mini"
TIMEOUT_API = 45          # segundos
MAX_PRODUCTOS_CONTEXTO = 100
MAX_TURNOS_HISTORIAL = 4  # mensajes previos que se reenvían para dar continuidad

SYSTEM_PROMPT = """Eres el asistente de inventario de un restaurante (SMART-BITE).
Trabajas SOLO con los datos que se te entregan en el CONTEXTO (JSON).

REGLAS OBLIGATORIAS:
1. Nunca inventes cantidades, precios ni fechas. Si un dato no está
   en el contexto, dilo explícitamente.
2. Cita siempre el producto y la cifra exacta al recomendar
   (ej: "quedan 2 kg, mínimo 5 kg → faltan 3 kg").
3. No hagas cálculos nuevos (sumas, porcentajes, proyecciones): usa las
   cifras ya calculadas del contexto (missing, estimated_cost,
   costo_total_estimado, value_at_risk, days_left).
4. Prioriza SIEMPRE en este orden: agotados (quantity 0) > bajo mínimo >
   vencimientos próximos > optimización. El campo "prioridad" de
   reposicion solo ordena por impacto económico DENTRO de cada grupo:
   un producto agotado va antes que cualquiera con existencia.
5. Las decisiones de compra y de cambio de inventario las toma el
   encargado: tú recomiendas, no ejecutas.
6. Responde en español, en máximo ~180 palabras, con viñetas cortas.
   Montos en COP con punto de miles (ej: $ 60.000); cantidades sin
   decimales innecesarios (2 kg, no 2.0 kg).
7. Si la pregunta no trata del inventario del restaurante, NO la
   respondas: indica amablemente que solo ayudas con el inventario."""

ADVERTENCIA = ("Prototipo académico con datos locales de demostración. "
               "No está conectado al backend ni a la base de datos real de SMART-BITE.")


def cop(valor):
    """Formatea pesos colombianos: 20000 → '$ 20.000'."""
    return "$ " + f"{valor:,.0f}".replace(",", ".")


def cantidad(valor, unidad):
    return f"{valor:g} {unidad}"


def recomendaciones_por_reglas(n_days=db.DIAS_VENCIMIENTO, db_path=None, hoy=None):
    """Recomendaciones deterministas, en orden de prioridad:
    agotados > bajo mínimo > vencidos/por vencer. Cada cifra sale de SQLite."""
    reposicion = db.calcular_reposicion(db_path)["pendientes"]
    recomendaciones = []

    for p in [p for p in reposicion if p["quantity"] == 0]:
        recomendaciones.append(
            f"**Comprar {p['name']} (agotado):** reponer al menos "
            f"{cantidad(p['missing'], p['unit'])} para llegar al mínimo "
            f"(≈ {cop(p['estimated_cost'])})."
        )
    for p in [p for p in reposicion if p["quantity"] > 0]:
        recomendaciones.append(
            f"**Comprar {p['name']} (stock bajo):** quedan {cantidad(p['quantity'], p['unit'])}, "
            f"mínimo {cantidad(p['min_stock'], p['unit'])} → faltan "
            f"{cantidad(p['missing'], p['unit'])} (≈ {cop(p['estimated_cost'])})."
        )
    for p in db.vencimientos_proximos(n_days, db_path, hoy):
        valor = cop(p["quantity"] * p["unit_cost"])
        if p["quantity"] == 0:
            continue  # nada que usar ni descartar; ya figura como agotado
        if p["days_left"] < 0:
            recomendaciones.append(
                f"**Retirar {p['name']}:** venció hace {-p['days_left']} día(s); "
                f"{cantidad(p['quantity'], p['unit'])} en riesgo ({valor})."
            )
        elif p["days_left"] <= 2:
            recomendaciones.append(
                f"**Usar primero {p['name']}:** vence en {p['days_left']} día(s); "
                f"{cantidad(p['quantity'], p['unit'])} ({valor})."
            )
        else:
            recomendaciones.append(
                f"**Planificar uso de {p['name']}:** vence en {p['days_left']} días; "
                f"{cantidad(p['quantity'], p['unit'])} ({valor})."
            )
    return recomendaciones


def _tabla(encabezados, filas):
    lineas = ["| " + " | ".join(encabezados) + " |",
              "|" + "|".join("---" for _ in encabezados) + "|"]
    lineas += ["| " + " | ".join(str(c) for c in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def generar_informe(n_days=db.DIAS_VENCIMIENTO, db_path=None, hoy=None, recomendacion_ia=None):
    """Informe de gestión en Markdown con el estado actual del inventario (RF-10)."""
    hoy = hoy or date.today()
    resumen = db.resumen_inventario(n_days, db_path, hoy)
    agotados = db.productos_agotados(db_path)
    bajos = db.productos_stock_bajo(db_path)
    vencimientos = db.vencimientos_proximos(n_days, db_path, hoy)
    reposicion = db.calcular_reposicion(db_path)

    partes = [
        "# Informe de inventario — SMART-BITE",
        f"Generado: {datetime.now():%Y-%m-%d %H:%M} · Horizonte de vencimientos: {n_days} días",
        f"> {ADVERTENCIA}",
        "## 1. Resumen",
        _tabla(["Indicador", "Valor"], [
            ["Productos registrados", resumen["total_products"]],
            ["Valor del inventario", cop(resumen["total_value"])],
            ["Alertas de stock (incluye agotados)", resumen["low_stock_count"]],
            ["Agotados", resumen["out_of_stock_count"]],
            [f"Por vencer o vencidos ({n_days} d)", resumen["expiring_count"]],
            ["Valor en riesgo por vencimiento", cop(resumen["value_at_risk"])],
        ]),
        "## 2. Alertas",
        "### Agotados",
        _tabla(["Producto", "Categoría", "Mínimo"],
               [[p["name"], p["category"], cantidad(p["min_stock"], p["unit"])] for p in agotados])
        if agotados else "Ninguno.",
        "### Stock bajo el mínimo",
        _tabla(["Producto", "Existencia", "Mínimo", "Faltan"],
               [[p["name"], cantidad(p["quantity"], p["unit"]), cantidad(p["min_stock"], p["unit"]),
                 cantidad(p["missing"], p["unit"])] for p in bajos])
        if bajos else "Ninguno.",
        "### Vencimientos",
        _tabla(["Producto", "Vence", "Días", "Estado", "Valor"],
               [[p["name"], p["expiry_date"], p["days_left"], p["estado"],
                 cop(p["quantity"] * p["unit_cost"])] for p in vencimientos])
        if vencimientos else "Ninguno.",
        "## 3. Reposición sugerida (prioridad por faltante × costo)",
        _tabla(["#", "Producto", "Faltan", "Costo estimado"],
               [[p["prioridad"], p["name"], cantidad(p["missing"], p["unit"]),
                 cop(p["estimated_cost"])] for p in reposicion["pendientes"]])
        + f"\n\n**Costo total estimado:** {cop(reposicion['costo_total_estimado'])}"
        if reposicion["pendientes"] else "No hay productos bajo el mínimo.",
        "## 4. Recomendaciones (análisis por reglas)",
        "\n".join(f"{i}. {r}" for i, r in enumerate(recomendaciones_por_reglas(n_days, db_path, hoy), 1))
        or "Sin acciones pendientes: el inventario está dentro de los mínimos.",
    ]
    if recomendacion_ia:
        partes += ["## 5. Recomendaciones del agente IA", recomendacion_ia]
    partes.append("---\n*Las cifras provienen de SQLite; las decisiones de compra son del encargado.*")
    return "\n\n".join(partes) + "\n"


# ------------------------------------------------------------ herramientas
# Funciones deterministas con contrato fijo: lo único que el agente "ve".

def listar_productos(db_path=None):
    return db.listar_productos(db_path)


def detectar_riesgos(n_days=db.DIAS_VENCIMIENTO, db_path=None, hoy=None):
    campos_bajo = ("id", "name", "quantity", "min_stock", "missing", "unit")
    return {
        "agotados": [{k: p[k] for k in ("id", "name", "category", "min_stock", "unit")}
                     for p in db.productos_agotados(db_path)],
        "stock_bajo": [{k: p[k] for k in campos_bajo} for p in db.productos_stock_bajo(db_path)],
        "vencimientos": [{**{k: p[k] for k in ("id", "name", "expiry_date", "days_left", "estado",
                                               "quantity", "unit", "unit_cost")},
                          "valor_en_riesgo": p["quantity"] * p["unit_cost"]}
                         for p in db.vencimientos_proximos(n_days, db_path, hoy)],
        "generado_en": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
    }


def vencimientos_proximos(n_days=db.DIAS_VENCIMIENTO, db_path=None, hoy=None):
    return db.vencimientos_proximos(n_days, db_path, hoy)


def calcular_reposicion(db_path=None):
    return db.calcular_reposicion(db_path)


def resumen_inventario(n_days=db.DIAS_VENCIMIENTO, db_path=None, hoy=None):
    return db.resumen_inventario(n_days, db_path, hoy)


def construir_contexto(db_path=None, hoy=None):
    """Contexto acotado que recibe el modelo (RNF-05): métricas, riesgos y
    reposición; el listado completo solo si el inventario es pequeño."""
    hoy = hoy or date.today()
    reposicion = calcular_reposicion(db_path)
    contexto = {
        "fecha_hoy": hoy.isoformat(),
        "moneda": "COP",
        "horizonte_vencimiento_dias": db.DIAS_VENCIMIENTO,
        "resumen": resumen_inventario(db_path=db_path, hoy=hoy),
        "riesgos": detectar_riesgos(db_path=db_path, hoy=hoy),
        "reposicion": {
            "pendientes": [{k: p[k] for k in ("prioridad", "name", "quantity", "min_stock",
                                              "missing", "unit", "unit_cost", "estimated_cost")}
                           for p in reposicion["pendientes"]],
            "costo_total_estimado": reposicion["costo_total_estimado"],
        },
    }
    productos = listar_productos(db_path)
    if len(productos) <= MAX_PRODUCTOS_CONTEXTO:
        contexto["inventario"] = [
            {k: p[k] for k in ("name", "category", "quantity", "unit", "min_stock",
                               "unit_cost", "expiry_date")}
            for p in productos
        ]
    return contexto


# ------------------------------------------------------------ configuración

def configuracion(env_path=None):
    """Lee la configuración en cada consulta: si se edita .env con la app
    abierta, el cambio se aplica sin reiniciar. El archivo .env manda; las
    variables de entorno del sistema son el respaldo."""
    archivo = dotenv_values(env_path or ENV_PATH)

    def valor(nombre):
        return (archivo.get(nombre) or os.environ.get(nombre) or "").strip()

    clave = valor("OPENAI_API_KEY")
    if clave.startswith("TU_API_KEY"):  # plantilla sin rellenar
        clave = ""
    return {
        "api_key": clave,
        "model": valor("OPENAI_MODEL") or MODELO_POR_DEFECTO,
        "base_url": valor("OPENAI_BASE_URL") or None,
        # Opcional: "none" quita el razonamiento previo en Gemini (~20 s → ~3 s).
        "reasoning_effort": valor("OPENAI_REASONING_EFFORT") or None,
        # Opcional: modelo que se usa si el principal agota su cuota (cada modelo tiene la suya).
        "model_respaldo": valor("OPENAI_MODEL_RESPALDO") or None,
    }


def hay_api_key(config=None):
    return bool((config or configuracion())["api_key"])


def describir_proveedor(config=None):
    config = config or configuracion()
    if config["base_url"] and "generativelanguage.googleapis.com" in config["base_url"]:
        return f"Gemini · {config['model']}"
    return config["model"]


# --------------------------------------------------------------- llamada IA

class RespuestaNoVerificable(Exception):
    """La respuesta cita cifras que no existen en el contexto."""


def _crear_cliente(config):
    from openai import OpenAI  # import diferido: el modo reglas no necesita la librería
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"],
                  timeout=TIMEOUT_API, max_retries=0)  # el respaldo cubre cuota agotada y 503


def construir_mensajes(contexto, pregunta, historial=None):
    """El contexto viaja en el mismo mensaje que la pregunta: cada pregunta
    se responde con los datos de ese momento y el modelo no lo pierde."""
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turno in (historial or [])[-MAX_TURNOS_HISTORIAL:]:
        mensajes.append({"role": turno["role"], "content": turno["content"]})
    mensajes.append({"role": "user", "content": (
        "CONTEXTO (datos actuales del inventario, JSON):\n"
        + json.dumps(contexto, ensure_ascii=False, default=str)
        + f"\n\nPREGUNTA: {pregunta}")})
    return mensajes


def llamar_api(config, mensajes):
    extra = {"reasoning_effort": config["reasoning_effort"]} if config.get("reasoning_effort") else {}
    respuesta = _crear_cliente(config).chat.completions.create(
        model=config["model"], messages=mensajes, temperature=0.2, **extra)
    texto = (respuesta.choices[0].message.content or "").strip()
    if not texto:
        raise RespuestaNoVerificable("respuesta vacía")
    return texto


# ----------------------------------------------------- validación de cifras

_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")


def _a_numero(token):
    """'60.000' → 60000 · '1.234.567' → 1234567 · '2,5' → 2.5 · '2.5' → 2.5"""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        return float(re.sub(r"[.,]", "", token))
    try:
        return float(token.replace(",", "."))
    except ValueError:  # p. ej. "2026.10.09"
        return float(re.split(r"[.,]", token)[0])


def _numeros_del_contexto(valor, acumulado):
    if isinstance(valor, dict):
        for v in valor.values():
            _numeros_del_contexto(v, acumulado)
    elif isinstance(valor, list):
        for v in valor:
            _numeros_del_contexto(v, acumulado)
    elif isinstance(valor, bool):
        pass
    elif isinstance(valor, (int, float)):
        acumulado.add(round(abs(float(valor)), 2))  # "venció hace 1 día" ↔ days_left = -1
    elif isinstance(valor, str):
        for token in _NUMERO.findall(valor):  # fechas, nombres ("Gaseosa 1.5 lt")
            acumulado.add(round(_a_numero(token), 2))
            for parte in re.split(r"[.,]", token):
                acumulado.add(float(parte))
    return acumulado


_CAMPOS_MONTO = ("estimated_cost", "valor_en_riesgo")


def _montos(valor, acumulado):
    if isinstance(valor, dict):
        for k, v in valor.items():
            if k in _CAMPOS_MONTO and isinstance(v, (int, float)):
                acumulado.append(float(v))
            else:
                _montos(v, acumulado)
    elif isinstance(valor, list):
        for v in valor:
            _montos(v, acumulado)
    return acumulado


def cifras_no_respaldadas(texto, contexto):
    """Números > 10 de la respuesta que no aparecen en el contexto.
    Se toleran los pequeños (viñetas, prioridades, 'en 2 días') y las sumas
    de 2 o 3 montos del contexto (subtotales de compra o de riesgo)."""
    permitidos = _numeros_del_contexto(contexto, set())
    montos = _montos(contexto, [])
    permitidos |= {round(sum(c), 2) for n in (2, 3) for c in combinations(montos, n)}
    return [token for token in _NUMERO.findall(texto)
            if round(_a_numero(token), 2) > 10 and round(_a_numero(token), 2) not in permitidos]


def validar_que_cita_datos(texto, contexto):
    ajenos = cifras_no_respaldadas(texto, contexto)
    if ajenos:
        raise RespuestaNoVerificable(f"cifras sin respaldo en los datos: {', '.join(ajenos[:5])}")


# ------------------------------------------------------------ modo degradado

def _motivo_error(error):
    """Mensaje para el usuario según §5.5 (nunca la clave ni el stack trace)."""
    if isinstance(error, RespuestaNoVerificable):
        return f"la respuesta de la IA no se pudo verificar contra los datos ({error})."
    try:
        import openai
    except ImportError:
        return "falta la librería `openai` (ejecuta `pip install -r requirements.txt`)."
    clave_invalida = "api key" in str(error).lower()  # Gemini responde 400 (no 401) a una clave inválida
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)) or clave_invalida:
        return "la API key fue rechazada por el proveedor; revisa OPENAI_API_KEY en `.env`."
    if isinstance(error, openai.RateLimitError):
        if "perday" in str(error).lower():  # p. ej. GenerateRequestsPerDayPerProjectPerModel
            espera = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?", str(error))
            cuando = (f" (se renueva en ~{espera.group(1) or 0} h {espera.group(2) or 0} min)"
                      if espera and (espera.group(1) or espera.group(2)) else "")
            return f"se agotó la cuota diaria del plan gratuito{cuando}."
        return "límite de peticiones por minuto alcanzado; intenta en un minuto."
    if isinstance(error, (openai.NotFoundError, openai.BadRequestError)):
        return "revisa OPENAI_MODEL y OPENAI_BASE_URL en `.env`."
    if isinstance(error, (openai.APITimeoutError, openai.APIConnectionError)):
        return "no hubo conexión con el proveedor de IA; revisa tu conexión."
    if isinstance(error, openai.InternalServerError):
        return "el proveedor de IA está saturado o caído; intenta de nuevo en un momento."
    return "error inesperado al consultar la IA."


def responder_por_reglas(contexto, pregunta, db_path=None, hoy=None):
    """Análisis determinista en Markdown; se reordena según el tema de la pregunta."""
    r = contexto["resumen"]
    recomendaciones = recomendaciones_por_reglas(db_path=db_path, hoy=hoy)
    if re.search(r"venc|caduc|desperdic|merma", pregunta.lower()):
        recomendaciones.sort(key=lambda t: not re.match(r"\*\*(Retirar|Usar|Planificar)", t))
    partes = [f"Inventario: **{r['total_products']} productos** por **{cop(r['total_value'])}**; "
              f"{r['low_stock_count']} alerta(s) de stock, {r['expiring_count']} por vencer "
              f"({cop(r['value_at_risk'])} en riesgo)."]
    if recomendaciones:
        partes.append("\n".join(f"{i}. {t}" for i, t in enumerate(recomendaciones, 1)))
        costo = contexto["reposicion"]["costo_total_estimado"]
        if costo:
            partes.append(f"Costo total estimado de reposición: **{cop(costo)}**.")
    else:
        partes.append("Sin acciones pendientes: todo está sobre el mínimo y sin vencimientos próximos.")
    partes.append("_La decisión de compra es del encargado._")
    return "\n\n".join(partes)


# ------------------------------------------------------------------ consulta

def consultar(pregunta, historial=None, db_path=None, hoy=None, env_path=None):
    """Punto de entrada del chat. Devuelve un dict con:
    respuesta, modo ("ia" | "reglas"), aviso (str | None), fuente (contexto), modelo."""
    contexto = construir_contexto(db_path, hoy)
    config = configuracion(env_path)

    def degradar(motivo):
        log.warning("Modo degradado: %s", motivo)
        return {"respuesta": responder_por_reglas(contexto, pregunta, db_path, hoy),
                "modo": "reglas",
                "aviso": f"Análisis con IA no disponible: {motivo} Se muestra el análisis por reglas.",
                "fuente": contexto, "modelo": None}

    if not hay_api_key(config):
        return degradar("no hay API key configurada (edita `OPENAI_API_KEY` en `.env`).")

    modelos = [config["model"]] + ([config["model_respaldo"]] if config.get("model_respaldo") else [])
    for n, modelo in enumerate(modelos):
        config_modelo = {**config, "model": modelo}
        try:
            return _consultar_modelo(config_modelo, contexto, pregunta, historial)
        except RespuestaNoVerificable as e:
            return degradar(_motivo_error(e))
        except Exception as e:  # cualquier fallo de red/proveedor → respaldo o modo degradado
            log.warning("Fallo de la API con %s: %s", modelo, type(e).__name__)
            if _amerita_respaldo(e) and n + 1 < len(modelos):
                continue  # cuota agotada o modelo saturado: se prueba el modelo de respaldo
            return degradar(_motivo_error(e))


def _amerita_respaldo(error):
    """Fallos propios del modelo (cuota agotada, 429; saturado, 5xx): otro modelo puede responder.
    Una clave inválida o la falta de red fallarían igual con el respaldo."""
    try:
        import openai
    except ImportError:
        return False
    return isinstance(error, (openai.RateLimitError, openai.InternalServerError))


def _consultar_modelo(config, contexto, pregunta, historial):
    """Una pregunta a un modelo, con un reintento si cita cifras inventadas.
    Lanza RespuestaNoVerificable si ambos intentos fallan la validación."""
    mensajes, error = construir_mensajes(contexto, pregunta, historial), None
    for intento in (1, 2):
        texto = None
        try:
            texto = llamar_api(config, mensajes)
            validar_que_cita_datos(texto, contexto)
            return {"respuesta": texto, "modo": "ia", "aviso": None,
                    "fuente": contexto, "modelo": describir_proveedor(config)}
        except RespuestaNoVerificable as e:
            log.warning("Respuesta descartada (intento %d): %s", intento, e)
            if texto:  # se le muestra su respuesta y se pide corregirla, como un turno normal
                mensajes = mensajes + [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": (
                        f"Tu respuesta usa cifras que no están en el CONTEXTO ({e}). Reescríbela "
                        "usando solo cifras que aparezcan en el CONTEXTO de mi mensaje anterior.")}]
            error = e
    raise error
