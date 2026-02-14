# Implementación del Sistema de JSON Pre-generado

## Objetivo
Generar un archivo JSON con todas las estadísticas necesarias para index.html, permitiendo carga instantánea de la página.

## Tareas

### ✅ Fase 1: Crear Generador de JSON
- [x] Crear `services/index_json_generator.py`
- [x] Función para generar JSON con todos los datos de jugadores
- [x] Incluir: datos básicos, top campeones, rachas, LP 24h, peak elo, estado de partida
- [x] Guardar en `stats_index.json`

**Estado: COMPLETADO** ✓


### ✅ Fase 2: Modificar Blueprint Principal
- [x] Modificar `blueprints/main.py`
- [x] Función `index()` lee directamente desde `stats_index.json`
- [x] Fallback: si no existe JSON, generarlo sincrónicamente una vez
- [x] Eliminar cálculos sincrónicos pesados

**Estado: COMPLETADO** ✓


### ✅ Fase 3: Optimizar Template
- [x] Modificar `templates/index.html`
- [x] Eliminar pantalla de carga bloqueante larga
- [x] Mostrar datos inmediatamente
- [x] Indicador de última actualización del JSON

### ✅ Fase 4: Integrar con Data Updater
- [x] Modificar `services/data_updater.py`
- [x] Llamar a `generate_index_json()` después de actualizar datos
- [x] Asegurar que el JSON se regenera periódicamente

### ✅ Fase 5: Precarga al Iniciar Servidor
- [x] Modificar `app.py` o `server.py`
- [x] Generar JSON al iniciar si no existe o está antiguo
- [x] Iniciar thread de actualización periódica del JSON

## Resultado Esperado
- Tiempo de carga de index.html: < 200ms
- Datos siempre disponibles aunque no sean los más recientes
- Actualización en background sin afectar al usuario
