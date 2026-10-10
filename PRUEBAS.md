# Registro de pruebas — SMART-BITE AI Inventory Agent

Fecha: 2026-10-09 · Entorno: Windows 11, Python 3.13.0, Streamlit 1.65.0, Gemini `gemini-3.5-flash`
Ejecutadas en el navegador (Playwright sobre `streamlit run app.py`) y con `test_agent.py`.

## 1. Pruebas automáticas

```powershell
.\.venv\Scripts\python.exe test_agent.py
```

**Resultado: 35 pruebas, todas en verde.** No usan red ni API key: cada prueba trabaja sobre una base
SQLite temporal y el cliente de la IA se reemplaza por uno falso.

| Grupo | Qué cubre |
|---|---|
| CRUD y límites | registrar/leer/editar/borrar, persistencia, nombres duplicados, fechas inválidas, negativos (validación + `CHECK` de SQLite) |
| Consultas | agotados, stock bajo con `missing`, vencimientos con `days_left`, valor total, reposición por impacto, inventario vacío |
| Demo | datos semilla, restablecer demo, `init_db` sin duplicar |
| Informe | refleja el estado actual, orden agotado > stock bajo, formato COP |
| Agente | modo degradado sin clave, respuesta válida, cifras inventadas (reintento corregido / fallido), error de conexión sin exponer la clave, clave inválida de Gemini (400), `reasoning_effort` opcional, validación de cifras |
| Interfaz (`AppTest`) | métricas, registrar, error claro con cantidad negativa, nombre duplicado, salida de stock, edición, filtro, chat sin clave, preguntas sugeridas |

Sin `streamlit`/`openai` instalados, las pruebas que los necesitan se omiten (*skipped*) en lugar de fallar.

## 2. Matriz de pruebas manuales

| # | Escenario | Resultado observado | ✔/✘ |
|---|---|---|---|
| M1 | Primer arranque sin `inventario.db` | se crea la base con 8 productos; métricas 8 · 3 · 4 · $ 636.000 | ✔ |
| M2 | Registrar "Papas fritas · 3 cajas · mín 5 · $ 12.000" | aparece en la tabla; 9 productos, 4 alertas, valor $ 672.000 (+ 3 × 12.000) | ✔ |
| M3 | Cantidad `-1` | error visible "La cantidad no puede ser menor que 0 (recibido: -1)"; no se escribe nada y el formulario conserva lo escrito | ✔ |
| M4 | Salida de 8 lt de Leche entera | saldo 2 lt → 🟡 stock bajo al instante; alertas 4 → 5 | ✔ |
| M5 | Salida de los 3 kg de Queso mozzarella | saldo 0 kg → 🔴 agotado | ✔ |
| M6 | Registrar "Crema de leche" que vence en +3 días | 🟠 vence en 3 d; "Por vencer" 4 → 5 | ✔ |
| M7 | "¿Qué debería comprar primero y por qué?" | Tomate (agotado) primero, luego Carne, Queso y Papas; todas las cifras coinciden con SQLite | ✔ |
| M8 | Abrir "Datos usados" | tablas de reposición y vencimientos con las mismas cifras que cita la respuesta ($ 127.200 total) | ✔ |
| M9 | `.env` sin clave + reiniciar | indicador ⚪ "IA: sin configurar"; el chat responde por reglas con aviso; sin errores | ✔ |
| M10 | Clave inválida con el panel abierto | indicador 🟡 "modo degradado"; aviso "la API key fue rechazada…"; respuesta por reglas | ✔ |
| M11 | Descargar informe | Markdown con resumen, alertas, reposición, recomendaciones por reglas y la última respuesta de la IA; refleja los cambios (Leche 2 lt) | ✔ |
| M12 | Reiniciar la app | los productos registrados siguen ahí (10 productos) | ✔ |
| M13 | ♻️ Restablecer demo + confirmar | vuelve a 8 · 3 · 4 · $ 636.000 | ✔ |

**Guion de demostración (5 pasos)** recorrido completo en el navegador: ✔.

## 3. Hallazgos corregidos durante las pruebas

| Hallazgo | Corrección |
|---|---|
| M3: el campo se marcaba en rojo sin explicar el error | la validación pasa a la capa de servicio y muestra un mensaje claro |
| M10: una clave inválida decía "revisa OPENAI_MODEL…" | Gemini responde 400 (no 401) a una clave inválida; ahora se reconoce y se indica revisar `OPENAI_API_KEY` |
| La IA tardaba 9–40 s por respuesta | `OPENAI_REASONING_EFFORT=none` → ~3–4 s sin perder calidad (cifras validadas, prioridades correctas) |
| Streamlit mostraba "$ … $" como fórmula LaTeX | se escapa `$` antes de mostrar Markdown |

## 4. Riesgos conocidos para el día de la demo

| Riesgo | Observado | Reacción |
|---|---|---|
| Cuota gratuita de Gemini (429) | tras 4–5 preguntas seguidas en menos de un minuto | esperar ~1 minuto entre preguntas; si salta, el panel sigue en 🟡 con el análisis por reglas |
| Sin internet | — | todo funciona salvo la respuesta con IA (plan B: reglas) |
| Puerto 8501 ocupado | — | `python -m streamlit run app.py --server.port 8502` |
| Datos de prueba "sucios" | — | botón ♻️ Restablecer demo |
