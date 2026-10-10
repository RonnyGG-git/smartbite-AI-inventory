# SMART-BITE AI Inventory Agent

Prototipo académico de un **agente de IA para el inventario de un restaurante**: el encargado pregunta
en lenguaje natural ("¿qué debería comprar primero y por qué?") y el agente responde con prioridades y
cantidades basadas en los datos reales del inventario.

> ⚠️ **Prototipo, no producción.** Usa una base SQLite local con datos de demostración. **No está
> conectado** al backend ni a la base de datos real de SMART-BITE, no tiene autenticación ni despliegue.
> La IA es un asistente de decisión: recomienda, pero no modifica el inventario.

**Regla del diseño:** el código calcula (stock, mínimos, vencimientos, costos, con SQL); la IA solo
interpreta y prioriza esos resultados. Si la IA cita una cifra que no está en los datos, la respuesta se
descarta. Sin API key o sin internet, la app sigue funcionando con un análisis por reglas.

## Requisitos

- Windows 10/11 con PowerShell
- **Python 3.10 o superior** (probado con 3.13), instalado con *Add python.exe to PATH*
- Opcional: una API key de Google Gemini (<https://aistudio.google.com/apikey>) para las respuestas con IA

## Instalación (≈10 minutos)

Abrir PowerShell en la carpeta del proyecto (la que contiene `app.py`):

```powershell
# 1. Crear el entorno virtual
py -m venv .venv

# 2. Instalar las dependencias (streamlit, openai, python-dotenv)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Crear la configuración a partir de la plantilla
Copy-Item .env.example .env

# 4. (Opcional) Pegar la API key en .env, en la línea OPENAI_API_KEY=
notepad .env
```

Se usa la ruta directa `.\.venv\Scripts\python.exe` para no depender de activar el entorno (PowerShell
suele bloquear `Activate.ps1`).

## Uso

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Se abre el navegador en <http://localhost:8501>. En la primera ejecución se crea `inventario.db` con
8 productos de demostración. Para detener la app: `Ctrl + C` en la terminal.

### El panel

| Zona | Qué hace |
|---|---|
| Cabecera | estado de la IA: 🟢 conectada · 🟡 modo degradado (falló la última consulta) · ⚪ sin clave |
| Métricas | productos, alertas de stock, por vencer (7 días) y valor del inventario en COP |
| 💬 Preguntar al agente | chat con preguntas sugeridas; cada respuesta trae "🔎 Datos usados" con las cifras exactas que recibió la IA |
| 📦 Inventario | tabla con estados 🔴 agotado · 🟡 stock bajo · 🟠 por vencer/vencido · 🟢 OK; filtros en la barra lateral; clic en una fila para seleccionarla |
| Pestañas a la derecha | **Stock** (entrada/salida), **Nuevo** (registrar), **Editar** (modificar o eliminar) |
| Pie | 📥 descargar informe Markdown · ♻️ restablecer los datos de demostración |

### Guion de demostración (5–7 minutos)

1. **Estado general:** métricas y tabla (Tomate 🔴, Carne y Queso 🟡, Leche 🟠). Las alertas las calcula SQL, no la IA.
2. **Registrar:** "Papas fritas · Abarrotes · 3 caja · mín 5 · $ 12.000" → aparece 🟡 y las métricas cambian.
3. **Preguntar:** "¿Qué debo comprar primero y por qué?" → abrir "Datos usados" y cruzar las cifras.
4. **Operación diaria:** pestaña Stock → Leche entera → salida de 8 lt → pasa a 🟡 al instante.
5. **Informe:** descargarlo y mostrar resumen, riesgos y reposición priorizada (ejemplo en `informe_ejemplo.md`).

Antes de empezar: **♻️ Restablecer demo**. Entre preguntas a la IA, dejar unos segundos (la cuota gratuita
de Gemini admite pocas por minuto; si se agota, el chat sigue respondiendo por reglas).

## Configuración de la IA (`.env`)

| Variable | Valor para Gemini | Notas |
|---|---|---|
| `OPENAI_API_KEY` | tu clave de Google AI Studio | vacía = modo por reglas |
| `OPENAI_MODEL` | `gemini-3.5-flash` | `gemini-2.5-flash` ya no está disponible para cuentas nuevas |
| `OPENAI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | vacía = OpenAI |
| `OPENAI_REASONING_EFFORT` | `none` | respuestas en ~3 s en vez de ~20 s; vacía con modelos que no lo admitan |

Gemini se usa a través de su endpoint compatible con OpenAI, por eso la librería es `openai`. Para usar
OpenAI basta con `OPENAI_MODEL=gpt-4o-mini` y dejar vacías las dos últimas variables. El archivo se
vuelve a leer en cada pregunta: los cambios se aplican sin reiniciar la app.

**`.env` contiene la clave: nunca se sube a Git ni se comparte en el ZIP** (solo va `.env.example`).

## Pruebas

```powershell
.\.venv\Scripts\python.exe test_agent.py
```

35 pruebas sin red ni API key (datos, agente con un cliente simulado e interfaz con `streamlit.testing`).
El registro de las pruebas manuales M1–M13 y los hallazgos corregidos está en `PRUEBAS.md`.

## Problemas comunes

| Síntoma | Solución |
|---|---|
| `'python' no se reconoce` | usar `py` (launcher de Windows) o reinstalar Python marcando *Add to PATH* |
| `ModuleNotFoundError: streamlit` | ejecutar con `.\.venv\Scripts\python.exe`, no con el Python del sistema; repetir el paso 2 |
| PowerShell bloquea `Activate.ps1` | no hace falta activar: usar la ruta directa `.\.venv\Scripts\python.exe` |
| Puerto 8501 ocupado | `.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502` |
| 🟡 "la API key fue rechazada" | revisar `OPENAI_API_KEY` en `.env` (sin espacios ni comillas) |
| 🟡 "límite de peticiones o cuota" | esperar ~1 minuto; la cuota gratuita es por minuto |
| 🟡 "revisa OPENAI_MODEL y OPENAI_BASE_URL" | el modelo no existe para tu clave; usar `gemini-3.5-flash` |
| Datos de prueba desordenados | botón ♻️ Restablecer demo (o borrar `inventario.db`: se regenera) |

## Estructura

```text
smartbite_ai_inventory/
├── app.py              interfaz Streamlit (sin SQL ni prompts)
├── database.py         SQLite: esquema, CRUD con validación, consultas de riesgo, datos de demo
├── agent.py            herramientas, prompt, llamada a la IA, validación de cifras, modo por reglas, informe
├── test_agent.py       pruebas automáticas
├── requirements.txt    dependencias con versiones probadas
├── .env.example        plantilla de configuración (copiar como .env)
├── PRUEBAS.md          registro de pruebas manuales y automáticas
├── informe_ejemplo.md  informe generado por la app con los datos de demostración
└── inventario.db       se crea al ejecutar (no se entrega)
```

## Estado

| Fase | Estado |
|---|---|
| 0 — Entorno y esqueleto | ✅ |
| 1 — Núcleo de datos (SQLite) | ✅ |
| 2 — Interfaz de gestión | ✅ |
| 3 — Agente e integración IA (Gemini) | ✅ |
| 4 — Pruebas y pulido | ✅ ver `PRUEBAS.md` |
| 5 — Entrega | ✅ |

Fuera de alcance (roadmap): conexión con la base real de SMART-BITE, proveedores y órdenes de compra,
varios restaurantes y usuarios con roles, historial de movimientos y predicción de demanda.
