"""Pruebas básicas del SMART-BITE AI Inventory Agent.

Se ejecutan sin red y sin API key:  python test_agent.py
Cada prueba usa una base SQLite temporal que se borra al terminar.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import agent
import database as db


class BaseTemporal(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.init_db(self.db_path, con_demo=False)

    def tearDown(self):
        os.remove(self.db_path)

    def crear(self, name, quantity, min_stock, unit_cost, **kw):
        return db.crear_producto(name, quantity, min_stock, unit_cost, db_path=self.db_path, **kw)


class TestCrud(BaseTemporal):
    def test_crud_registrar_y_eliminar_persiste(self):
        pid = self.crear("Carne de res", 2, 5, 20000, category="Carnes", unit="kg")
        # Un "reinicio" es abrir una conexión nueva: el dato debe seguir ahí.
        producto = db.obtener_producto(pid, self.db_path)
        self.assertEqual(producto["name"], "Carne de res")
        self.assertEqual(producto["quantity"], 2)
        self.assertEqual(len(db.listar_productos(self.db_path)), 1)

        self.assertTrue(db.eliminar_producto(pid, self.db_path))
        self.assertIsNone(db.obtener_producto(pid, self.db_path))
        self.assertFalse(db.eliminar_producto(pid, self.db_path))

    def test_actualizar_y_buscar(self):
        pid = self.crear("Leche entera", 10, 6, 4500, category="Lácteos", unit="lt")
        db.actualizar_cantidad(pid, 4, self.db_path)
        db.actualizar_producto(pid, "Leche deslactosada", 8, 5000, "2026-12-01", db_path=self.db_path)
        p = db.obtener_producto(pid, self.db_path)
        self.assertEqual((p["name"], p["quantity"], p["min_stock"], p["expiry_date"]),
                         ("Leche deslactosada", 4, 8, "2026-12-01"))
        self.assertEqual(p["category"], "Lácteos")  # no se pasó → se conserva
        self.assertEqual(len(db.buscar_productos("lácteos", self.db_path)), 1)
        self.assertEqual(len(db.buscar_productos("deslact", self.db_path)), 1)

    def test_nombre_duplicado_o_vacio_se_rechaza(self):
        self.crear("Arroz", 25, 10, 3200)
        with self.assertRaises(ValueError):
            self.crear("arroz", 1, 1, 1)
        with self.assertRaises(ValueError):
            self.crear("   ", 1, 1, 1)

    def test_fecha_invalida_se_rechaza(self):
        with self.assertRaises(ValueError):
            self.crear("Queso", 3, 4, 28000, expiry_date="31/12/2026")


class TestLimites(BaseTemporal):
    def test_limite_cantidad_negativa_se_rechaza(self):
        # Barrera 1: validación de servicio.
        for campos in [(-1, 5, 100), (1, -5, 100), (1, 5, -100)]:
            with self.assertRaises(ValueError):
                self.crear("Producto", *campos)
        pid = self.crear("Tomate", 3, 4, 3800, unit="kg")
        with self.assertRaises(ValueError):
            db.actualizar_cantidad(pid, -2, self.db_path)
        with self.assertRaises(ValueError):
            db.ajustar_stock(pid, -5, self.db_path)  # salida mayor que la existencia
        self.assertEqual(db.obtener_producto(pid, self.db_path)["quantity"], 3)
        self.assertEqual(db.ajustar_stock(pid, -3, self.db_path), 0)

        # Barrera 2: CHECK de la propia base de datos.
        with self.assertRaises(sqlite3.IntegrityError), db._db(self.db_path) as conn:
            conn.execute("UPDATE products SET quantity = -1 WHERE id = ?", (pid,))


class TestConsultas(BaseTemporal):
    def setUp(self):
        super().setUp()
        self.hoy = date(2026, 10, 9)
        d = lambda n: (self.hoy + timedelta(days=n)).isoformat()
        self.crear("Tomate", 0, 4, 3800, unit="kg", expiry_date=d(2))
        self.crear("Carne de res", 2, 5, 20000, unit="kg", expiry_date=d(5))
        self.crear("Queso mozzarella", 3, 4, 28000, unit="kg", expiry_date=d(10))
        self.crear("Yogur", 6, 2, 2000, unit="unidad", expiry_date=d(-1))
        self.crear("Arroz", 25, 10, 3200, unit="kg")

    def test_detectar_riesgos_encuentra_agotados(self):
        self.assertEqual([p["name"] for p in db.productos_agotados(self.db_path)], ["Tomate"])

    def test_detectar_riesgos_detecta_stock_bajo(self):
        bajos = db.productos_stock_bajo(self.db_path)
        self.assertEqual([(p["name"], p["missing"]) for p in bajos],
                         [("Carne de res", 3), ("Queso mozzarella", 1)])

    def test_vencimientos_marca_dias_restantes(self):
        venc = db.vencimientos_proximos(7, self.db_path, hoy=self.hoy)
        self.assertEqual([(p["name"], p["days_left"], p["estado"]) for p in venc], [
            ("Yogur", -1, "vencido"),
            ("Tomate", 2, "por vencer (48 h)"),
            ("Carne de res", 5, "próximo a vencer"),
        ])

    def test_resumen_inventario_calcula_valor_correcto(self):
        r = db.resumen_inventario(7, self.db_path, hoy=self.hoy)
        esperado = 0 * 3800 + 2 * 20000 + 3 * 28000 + 6 * 2000 + 25 * 3200
        self.assertEqual(r["total_products"], 5)
        self.assertEqual(r["total_value"], esperado)
        self.assertEqual(r["low_stock_count"], 3)      # tomate, carne, queso
        self.assertEqual(r["out_of_stock_count"], 1)
        self.assertEqual(r["expiring_count"], 3)       # yogur, tomate, carne
        self.assertEqual(r["value_at_risk"], 6 * 2000 + 2 * 20000)

    def test_calcular_reposicion_ordena_por_impacto(self):
        r = db.calcular_reposicion(self.db_path)
        # carne 3×20000=60000 > queso 1×28000 > tomate 4×3800=15200
        self.assertEqual([(p["prioridad"], p["name"], p["estimated_cost"]) for p in r["pendientes"]], [
            (1, "Carne de res", 60000),
            (2, "Queso mozzarella", 28000),
            (3, "Tomate", 15200),
        ])
        self.assertEqual(r["costo_total_estimado"], 103200)
        self.assertEqual(r["moneda"], "COP")

    def test_resumen_vacio_no_falla(self):
        for p in db.listar_productos(self.db_path):
            db.eliminar_producto(p["id"], self.db_path)
        r = db.resumen_inventario(db_path=self.db_path)
        self.assertEqual((r["total_products"], r["total_value"], r["low_stock_count"]), (0, 0, 0))
        self.assertEqual(db.calcular_reposicion(self.db_path)["pendientes"], [])


class TestDemo(BaseTemporal):
    def test_semillas_y_restablecer(self):
        db.restablecer_demo(self.db_path)
        productos = db.listar_productos(self.db_path)
        self.assertEqual(len(productos), len(db.DATOS_DEMO))
        # Muestra todos los estados: agotado, stock bajo y vencimiento inminente.
        self.assertEqual([p["name"] for p in db.productos_agotados(self.db_path)], ["Tomate"])
        self.assertEqual({p["name"] for p in db.productos_stock_bajo(self.db_path)},
                         {"Carne de res", "Queso mozzarella"})
        # Datos contaminados → restablecer deja exactamente la demo otra vez.
        self.crear("Basura de prueba", 1, 1, 1)
        db.restablecer_demo(self.db_path)
        self.assertEqual(len(db.listar_productos(self.db_path)), len(db.DATOS_DEMO))
        self.assertEqual(min(p["id"] for p in db.listar_productos(self.db_path)), 1)

    def test_init_db_no_duplica_semillas(self):
        db.init_db(self.db_path)
        db.init_db(self.db_path)
        self.assertEqual(len(db.listar_productos(self.db_path)), len(db.DATOS_DEMO))


class TestEstado(unittest.TestCase):
    HOY = date(2026, 10, 9)

    def estado(self, quantity, min_stock, dias=None):
        expiry = (self.HOY + timedelta(days=dias)).isoformat() if dias is not None else None
        p = {"quantity": quantity, "min_stock": min_stock, "expiry_date": expiry}
        return db.texto_estado(p, hoy=self.HOY)

    def test_estado_por_producto(self):
        self.assertEqual(self.estado(10, 5), "🟢 OK")
        self.assertEqual(self.estado(10, 5, dias=30), "🟢 OK")
        self.assertEqual(self.estado(5, 5), "🟡 stock bajo")       # en el mínimo también alerta
        self.assertEqual(self.estado(0, 4, dias=2), "🔴 agotado · 🟠 vence en 2 d")
        self.assertEqual(self.estado(6, 2, dias=-1), "🟠 vencido hace 1 d")
        self.assertEqual(self.estado(6, 2, dias=0), "🟠 vence hoy")


class TestInforme(BaseTemporal):
    def test_informe_refleja_estado_actual(self):
        hoy = date(2026, 10, 9)
        self.crear("Tomate", 0, 4, 3800, unit="kg")
        pid = self.crear("Carne de res", 2, 5, 20000, unit="kg")
        self.crear("Leche entera", 10, 6, 4500, unit="lt", expiry_date=(hoy + timedelta(days=2)).isoformat())
        informe = agent.generar_informe(db_path=self.db_path, hoy=hoy)

        self.assertIn("$ 85.000", informe)                       # valor: 2×20000 + 10×4500
        self.assertIn("**Costo total estimado:** $ 75.200", informe)
        self.assertIn("faltan 3 kg", informe)
        self.assertIn("Usar primero Leche entera", informe)
        self.assertIn("No está conectado", informe)
        # El agotado va antes que el stock bajo aunque su costo sea menor.
        self.assertLess(informe.index("Comprar Tomate"), informe.index("Comprar Carne de res"))

        db.ajustar_stock(pid, 3, self.db_path)                   # carne llega al mínimo exacto
        self.assertIn("Comprar Carne de res", agent.generar_informe(db_path=self.db_path, hoy=hoy))
        db.ajustar_stock(pid, 1, self.db_path)                   # ahora por encima
        self.assertNotIn("Comprar Carne de res", agent.generar_informe(db_path=self.db_path, hoy=hoy))

    def test_informe_inventario_vacio(self):
        informe = agent.generar_informe(db_path=self.db_path)
        self.assertIn("No hay productos bajo el mínimo.", informe)
        self.assertIn("Sin acciones pendientes", informe)

    def test_formato_cop(self):
        self.assertEqual(agent.cop(1234567), "$ 1.234.567")
        self.assertEqual(agent.cop(0), "$ 0")


def respuesta_falsa(texto):
    """Imita la forma de una respuesta de chat.completions."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=texto))])


class TestAgente(BaseTemporal):
    """El agente sin red: el cliente de la API se reemplaza por uno falso."""

    def setUp(self):
        super().setUp()
        db.restablecer_demo(self.db_path)
        self.sin_env = os.path.join(tempfile.gettempdir(), "no_existe_smartbite.env")
        fd, self.env_falso = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as f:
            f.write("OPENAI_API_KEY=clave-de-prueba\nOPENAI_MODEL=modelo-prueba\n")
        self.entorno = mock.patch.dict(os.environ, {}, clear=False)
        self.entorno.start()
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        self.entorno.stop()
        os.remove(self.env_falso)
        super().tearDown()

    def consultar(self, pregunta="¿Qué debo comprar primero?", env=None):
        return agent.consultar(pregunta, db_path=self.db_path, env_path=env or self.env_falso)

    def cliente_que_responde(self, *textos):
        cliente = mock.MagicMock()
        cliente.chat.completions.create.side_effect = [respuesta_falsa(t) for t in textos]
        return mock.patch.object(agent, "_crear_cliente", return_value=cliente), cliente

    def test_modo_degradado_sin_api_key(self):  # RF-15 / M9
        with mock.patch.object(agent, "_crear_cliente") as crear:
            r = self.consultar(env=self.sin_env)
        crear.assert_not_called()                      # sin clave no se intenta la red
        self.assertEqual(r["modo"], "reglas")
        self.assertIn("no hay API key", r["aviso"])
        self.assertIn("**Comprar Tomate (agotado):**", r["respuesta"])
        self.assertIn("$ 103.200", r["respuesta"])     # costo total de reposición de la demo
        self.assertEqual(r["fuente"]["reposicion"]["costo_total_estimado"], 103200)

    def test_plantilla_sin_rellenar_cuenta_como_sin_clave(self):
        with open(self.env_falso, "w") as f:
            f.write("OPENAI_API_KEY=TU_API_KEY_AQUI\n")
        self.assertFalse(agent.hay_api_key(agent.configuracion(self.env_falso)))

    def test_respuesta_con_datos_reales_se_acepta(self):
        texto = ("1. **Tomate** agotado: faltan 4 kg ($ 15.200).\n"
                 "2. **Carne de res**: quedan 2 kg, mínimo 5 kg → faltan 3 kg ($ 60.000).\n"
                 "Subtotal tomate + carne: $ 75.200. Total reposición: $ 103.200.")
        parche, cliente = self.cliente_que_responde(texto)
        with parche:
            r = self.consultar()
        self.assertEqual((r["modo"], r["respuesta"], r["aviso"]), ("ia", texto, None))
        mensajes = cliente.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("CONTEXTO", mensajes[-1]["content"])       # datos y pregunta juntos
        self.assertIn("Carne de res", mensajes[-1]["content"])

    def test_reasoning_effort_solo_se_envia_si_esta_configurado(self):
        parche, cliente = self.cliente_que_responde("Faltan 3 kg de carne.", "Faltan 3 kg de carne.")
        with parche:
            self.consultar()
            self.assertNotIn("reasoning_effort", cliente.chat.completions.create.call_args.kwargs)
            with open(self.env_falso, "a") as f:
                f.write("OPENAI_REASONING_EFFORT=none\n")
            self.consultar()
            self.assertEqual(cliente.chat.completions.create.call_args.kwargs["reasoning_effort"], "none")

    def test_cifras_inventadas_reintenta_y_luego_degrada(self):
        parche, cliente = self.cliente_que_responde("Faltan 73 kg de carne.", "Compra 250 kg de tomate.")
        with parche:
            r = self.consultar()
        self.assertEqual(cliente.chat.completions.create.call_count, 2)
        self.assertEqual(r["modo"], "reglas")
        self.assertIn("no se pudo verificar", r["aviso"])

    def test_cifras_inventadas_corregidas_en_el_reintento(self):
        parche, cliente = self.cliente_que_responde("Faltan 73 kg de carne.", "Faltan 3 kg de carne ($ 60.000).")
        with parche:
            r = self.consultar()
        self.assertEqual(r["modo"], "ia")
        reintento = cliente.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(reintento[-2], {"role": "assistant", "content": "Faltan 73 kg de carne."})
        self.assertIn("73", reintento[-1]["content"])

    def test_error_de_conexion_degrada_sin_exponer_detalles(self):  # M10
        try:
            import openai
        except ImportError:
            self.skipTest("openai no está instalado")
        # Se crea sin __init__ para no depender de la librería HTTP interna del cliente.
        error = openai.APIConnectionError.__new__(openai.APIConnectionError)
        Exception.__init__(error, "Connection error.")
        cliente = mock.MagicMock()
        cliente.chat.completions.create.side_effect = error
        with mock.patch.object(agent, "_crear_cliente", return_value=cliente):
            r = self.consultar()
        self.assertEqual(r["modo"], "reglas")
        self.assertIn("revisa tu conexión", r["aviso"])
        self.assertNotIn("clave-de-prueba", r["aviso"] + r["respuesta"])

    def error_de_cuota(self, mensaje):
        try:
            import openai
        except ImportError:
            self.skipTest("openai no está instalado")
        error = openai.RateLimitError.__new__(openai.RateLimitError)
        Exception.__init__(error, mensaje)
        return error

    CUOTA_DIARIA = ("Error code: 429 - Quota exceeded for metric: free_tier_requests, limit: 20 "
                    "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier. "
                    "Please retry in 3h27m30.5s.")

    def test_cuota_agotada_usa_el_modelo_de_respaldo(self):
        with open(self.env_falso, "a") as f:
            f.write("OPENAI_MODEL_RESPALDO=modelo-respaldo\n")
        cliente = mock.MagicMock()
        cliente.chat.completions.create.side_effect = [
            self.error_de_cuota(self.CUOTA_DIARIA), respuesta_falsa("Faltan 3 kg de carne ($ 60.000).")]
        with mock.patch.object(agent, "_crear_cliente", return_value=cliente):
            r = self.consultar()
        self.assertEqual(r["modo"], "ia")
        self.assertEqual(r["modelo"], "modelo-respaldo")
        modelos = [c.kwargs["model"] for c in cliente.chat.completions.create.call_args_list]
        self.assertEqual(modelos, ["modelo-prueba", "modelo-respaldo"])

    def test_cuota_diaria_sin_respaldo_lo_explica(self):
        cliente = mock.MagicMock()
        cliente.chat.completions.create.side_effect = self.error_de_cuota(self.CUOTA_DIARIA)
        with mock.patch.object(agent, "_crear_cliente", return_value=cliente):
            r = self.consultar()
        self.assertEqual(r["modo"], "reglas")
        self.assertIn("cuota diaria", r["aviso"])
        self.assertIn("~3 h 27 min", r["aviso"])
        self.assertIn("por minuto", agent._motivo_error(self.error_de_cuota("429 Too many requests")))

    def test_clave_invalida_de_gemini_se_reconoce(self):
        # Gemini devuelve 400 "Please pass a valid API key" en vez de 401.
        error = Exception("Error code: 400 - [{'error': {'message': 'Please pass a valid API key'}}]")
        try:
            import openai  # noqa: F401
        except ImportError:
            self.skipTest("openai no está instalado")
        self.assertIn("API key fue rechazada", agent._motivo_error(error))

    def test_validacion_de_cifras(self):
        contexto = agent.construir_contexto(self.db_path)
        self.assertEqual(agent.cifras_no_respaldadas("faltan 3 kg, $ 60.000, total $ 103.200", contexto), [])
        self.assertEqual(agent.cifras_no_respaldadas("leche: $ 45.000 en riesgo", contexto), [])
        self.assertEqual(agent.cifras_no_respaldadas("vence el 2026-10-12", contexto), [])
        self.assertEqual(agent.cifras_no_respaldadas("faltan 73 kg y $ 999.999", contexto),
                         ["73", "999.999"])


try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # streamlit no instalado → se omiten las pruebas de la interfaz
    AppTest = None


@unittest.skipIf(AppTest is None, "streamlit no está instalado")
class TestInterfaz(BaseTemporal):
    """Ejecuta app.py sin navegador contra una base temporal."""

    def setUp(self):
        super().setUp()
        self._db_original = db.DB_PATH
        db.DB_PATH = self.db_path
        db.restablecer_demo(self.db_path)
        # Sin clave: la interfaz nunca llama a la API real durante las pruebas.
        self._env_original = agent.ENV_PATH
        agent.ENV_PATH = os.path.join(tempfile.gettempdir(), "no_existe_smartbite.env")
        self.entorno = mock.patch.dict(os.environ, {}, clear=False)
        self.entorno.start()
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        self.entorno.stop()
        agent.ENV_PATH = self._env_original
        db.DB_PATH = self._db_original
        super().tearDown()

    def abrir(self):
        at = AppTest.from_file("app.py", default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        return at

    def metrica(self, at, etiqueta):
        return next(m.value for m in at.metric if m.label == etiqueta)

    def test_panel_muestra_metricas_de_la_demo(self):
        at = self.abrir()
        self.assertEqual(self.metrica(at, "Productos"), "8")
        self.assertEqual(self.metrica(at, "Alertas de stock"), "3")   # tomate, carne, queso
        self.assertEqual(self.metrica(at, "Valor inventario"), agent.cop(db.resumen_inventario()["total_value"]))

    def test_registrar_producto_actualiza_metricas(self):  # demo paso 2 / M2
        at = self.abrir()
        at.text_input(key="nuevo_nombre").input("Papas fritas")
        at.number_input(key="nuevo_cant").set_value(3)
        at.selectbox(key="nuevo_unidad").set_value("caja")
        at.number_input(key="nuevo_min").set_value(5)
        at.number_input(key="nuevo_costo").set_value(12000)
        at.checkbox(key="nuevo_np").check()
        at.button[[b.label for b in at.button].index("➕ Registrar")].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(self.metrica(at, "Productos"), "9")
        self.assertEqual(self.metrica(at, "Alertas de stock"), "4")   # papas bajo el mínimo
        self.assertIn("registrado", at.success[0].value)

    def test_nombre_duplicado_muestra_error_y_no_escribe(self):  # M3
        at = self.abrir()
        at.text_input(key="nuevo_nombre").input("tomate")
        at.button[[b.label for b in at.button].index("➕ Registrar")].click().run()
        self.assertTrue(any("Ya existe" in e.value for e in at.error))
        self.assertEqual(len(db.listar_productos()), 8)

    def test_cantidad_negativa_muestra_error_claro(self):  # M3
        at = self.abrir()
        at.text_input(key="nuevo_nombre").input("Prueba negativa")
        at.number_input(key="nuevo_cant").set_value(-1)
        at.button[[b.label for b in at.button].index("➕ Registrar")].click().run()
        self.assertTrue(any("La cantidad no puede ser menor que 0" in e.value for e in at.error),
                        [e.value for e in at.error])
        self.assertEqual(len(db.listar_productos()), 8)
        self.assertEqual(at.text_input(key="nuevo_nombre").value, "Prueba negativa")  # no se borra

    def test_salida_de_stock_dispara_alerta(self):  # demo paso 4 / M4
        leche = db.buscar_productos("Leche entera")[0]
        at = self.abrir()
        at.selectbox(key="ajuste_sel").set_value(leche["id"]).run()
        at.number_input[0].set_value(8)
        at.button[[b.label for b in at.button].index("Aplicar")].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(db.obtener_producto(leche["id"])["quantity"], 2)
        self.assertEqual(self.metrica(at, "Alertas de stock"), "4")
        self.assertIn("🟡 stock bajo", at.success[0].value)

    def test_editar_minimo_cambia_alertas(self):
        arroz = db.buscar_productos("Arroz")[0]
        at = self.abrir()
        at.selectbox(key="editar_sel").set_value(arroz["id"]).run()
        minimo = next(n for n in at.number_input if n.label == "Stock mínimo" and n.value == 10)
        minimo.set_value(30)
        at.button[[b.label for b in at.button].index("💾 Guardar cambios")].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(db.obtener_producto(arroz["id"])["min_stock"], 30)
        self.assertEqual(self.metrica(at, "Alertas de stock"), "4")   # 25 kg < 30 kg

    def test_chat_sin_clave_responde_por_reglas(self):  # M9 desde la interfaz
        at = self.abrir()
        self.assertTrue(any("IA: sin configurar" in m.value for m in at.markdown))
        at.chat_input[0].set_value("¿Qué debo comprar primero?").run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("no hay API key" in w.value for w in at.warning))
        self.assertTrue(any("Comprar Tomate" in m.value for m in at.markdown))
        self.assertEqual(len(at.chat_message), 2)                 # pregunta + respuesta

    def test_pregunta_sugerida(self):
        at = self.abrir()
        at.button[[b.label for b in at.button].index("¿Qué vence esta semana?")].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.chat_message[0].markdown[0].value, "¿Qué vence esta semana?")

    def test_filtro_con_riesgo_oculta_productos_ok(self):
        at = self.abrir()
        at.sidebar.radio[0].set_value("Con riesgo").run()
        nombres = set(at.dataframe[0].value["name"])
        self.assertEqual(nombres, {"Carne de res", "Pechuga de pollo", "Leche entera",
                                   "Queso mozzarella", "Tomate"})




if __name__ == "__main__":
    unittest.main(verbosity=2)
