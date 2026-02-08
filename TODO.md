# Plan de Reorganización del Proyecto SoloQ-Cerditos

## FASE 1: Configuración y Constantes ✅ COMPLETADA
- [x] Crear `config/settings.py` - Configuración centralizada (API keys, URLs, etc.)
- [x] Crear `config/constants.py` - Constantes de la aplicación
- [x] Crear `config/__init__.py` - Inicialización del módulo config

## FASE 2: Utilidades ✅ COMPLETADA
- [x] Crear `utils/filters.py` - Filtros Jinja2 personalizados
- [x] Crear `utils/helpers.py` - Funciones auxiliares
- [x] Crear `utils/__init__.py` - Inicialización del módulo utils

## FASE 3: Modelos de Datos ✅ COMPLETADA
- [x] Crear `models/__init__.py` - Clases de datos (Player, Match, etc.)

## FASE 4: Servicios ✅ COMPLETADA
- [x] Crear `services/cache_service.py` - Gestión de caché
- [x] Crear `services/github_service.py` - Operaciones con GitHub (SHA-aware)
- [x] Crear `services/player_service.py` - Lógica de jugadores
- [x] Crear `services/match_service.py` - Lógica de partidas
- [x] Crear `services/stats_service.py` - Cálculo de estadísticas
- [x] Crear `services/ai_service.py` - Integración con Gemini AI
- [x] Crear `services/data_updater.py` - Actualización de datos
- [x] Crear `services/__init__.py` - Inicialización del módulo services

## FASE 5: Blueprints ✅ COMPLETADA
- [x] Crear `blueprints/__init__.py` - Registro de blueprints con url_prefix
- [x] Crear `blueprints/main.py` - Rutas principales (index)
- [x] Crear `blueprints/player.py` - Rutas de perfil de jugador
- [x] Crear `blueprints/stats.py` - Rutas de estadísticas
- [x] Crear `blueprints/api.py` - API endpoints

## FASE 6: Actualizar Templates ✅ COMPLETADA
- [x] Actualizar `templates/index.html` - url_for con blueprint names
- [x] Actualizar `templates/jugador.html` - url_for con blueprint names
- [x] Actualizar `templates/estadisticas.html` - url_for con blueprint names

## FASE 7: Reescribir app.py ✅ COMPLETADA
- [x] Reescribir `app.py` - Aplicación principal limpia


## FASE 8: Pruebas (PENDIENTE)
- [ ] Verificar que no hay errores de importación
- [ ] Verificar que las rutas funcionan correctamente
- [ ] Verificar que los templates renderizan bien

---

## Errores Resueltos

### Error 422 en GitHub API
**Problema:** Al intentar escribir archivos en GitHub, se recibía error 422 (Unprocessable Entity).

**Causa:** No se estaba proporcionando el SHA del archivo existente al actualizar.

**Solución:** En `services/github_service.py`, todas las funciones de guardado ahora:
1. Primero leen el archivo existente para obtener el SHA
2. Luego escriben con el SHA incluido en el payload

### Error 500 en Flask (BuildError)
**Problema:** Flask no encontraba los endpoints al generar URLs con `url_for()`.

**Causa:** Los blueprints no estaban registrados correctamente con `url_prefix` y las rutas tenían paths incorrectos.

**Solución:** 
1. En `blueprints/__init__.py`: Registrar blueprints con `url_prefix` apropiado:
   - `main_bp` → `/`
   - `player_bp` → `/jugador`
   - `stats_bp` → `/`
   - `api_bp` → `/api`

2. En `blueprints/player.py`: Cambiar ruta de `/jugador/<path:game_name>` a `/<path:game_name>` porque el prefix ya incluye `/jugador`

3. En templates: Actualizar todas las llamadas `url_for()` para usar el formato `blueprint.endpoint`:
   - `url_for('index')` → `url_for('main.index')`
   - `url_for('estadisticas_globales')` → `url_for('stats.estadisticas_globales')`
   - `url_for('perfil_jugador', ...)` → `url_for('player.perfil_jugador', ...)`

---

## Estructura Final del Proyecto

```
SoloQ-Cerditos/
├── app.py                      # Aplicación principal (reescrita)
├── config/
│   ├── __init__.py
│   ├── settings.py             # Configuración centralizada
│   └── constants.py            # Constantes
├── models/
│   └── __init__.py             # Clases de datos
├── services/
│   ├── __init__.py
│   ├── cache_service.py        # Gestión de caché
│   ├── github_service.py       # Operaciones GitHub (SHA-aware)
│   ├── player_service.py     # Lógica de jugadores
│   ├── match_service.py        # Lógica de partidas
│   ├── stats_service.py        # Estadísticas
│   ├── ai_service.py           # Integración Gemini
│   └── data_updater.py         # Actualización de datos
├── blueprints/
│   ├── __init__.py             # Registro de blueprints
│   ├── main.py                 # Rutas principales
│   ├── player.py               # Perfil de jugador
│   ├── stats.py                # Estadísticas
│   └── api.py                  # API endpoints
├── utils/
│   ├── __init__.py
│   ├── filters.py              # Filtros Jinja2
│   └── helpers.py              # Funciones auxiliares
├── templates/
│   ├── index.html
│   ├── jugador.html
│   ├── estadisticas.html
│   └── 404.html
├── static/
│   └── style.css
└── ... (archivos de datos, imágenes, etc.)
