# TODO - Roadmap de Optimizacion y Produccion

Contexto: la app corre en Render gratuito con 500 MB de RAM. La prioridad no es solo acelerar paginas, sino reducir duplicacion de datos en memoria, minimizar lecturas remotas y mover calculos pesados fuera del camino critico del usuario.

## Prioridad 1 - Critico antes de produccion

### 1. Cachear datos base pequenos y muy reutilizados
- [ ] Crear cache ligero para `accounts` en `services/player_service.py`
- [ ] Crear cache ligero para `puuids` en `services/player_service.py`
- [ ] Crear cache ligero para `peak_elo` y `lp_history`
- [ ] Hacer que las lecturas base usen primero cache/memoria y solo despues GitHub
- [ ] Reducir las lecturas repetidas a GitHub en requests normales

Motivo:
Esto da mucho rendimiento con muy poco coste de RAM.

### 2. Auditar y eliminar duplicacion de partidas en memoria
- [ ] Revisar donde se guardan listas completas de partidas
- [ ] Identificar duplicaciones entre:
- [ ] `player_match_history_cache`
- [ ] snapshots de historial global
- [ ] snapshots de logros
- [ ] `global_stats_cache`
- [ ] perfil de jugador
- [ ] Dejar una sola copia completa de cada historial cuando sea posible
- [ ] Sustituir copias completas por resumentes ligeros cuando la vista no necesite el objeto entero

Motivo:
Con 500 MB RAM, la duplicacion de historiales es el mayor riesgo real.

### 3. Revisar `global_stats_cache`
- [ ] Ver si `global_stats_cache` necesita guardar `all_matches` completo
- [ ] Si no es imprescindible, guardar solo agregados o un resumen reducido
- [ ] Separar estadisticas calculadas de datos crudos si ahora van juntos

Motivo:
Es una de las caches con mas papeletas de comer RAM.

## Prioridad 2 - Alto impacto en rendimiento

### 4. Extender el patron snapshot ligero a todas las vistas pesadas
- [ ] Mantener `logros` como snapshot ligero con refresco on demand
- [ ] Mantener `historial_global` como snapshot ligero con refresco on demand
- [ ] Evaluar que otras vistas agregadas deben pasar a snapshot
- [ ] Evitar recalculos completos dentro de requests HTTP normales

Motivo:
La app responde mejor si sirve datos precomputados y recalcula detras.

### 5. Adelgazar la pagina de jugador
- [ ] Revisar `_build_player_profile()` en `blueprints/player.py`
- [ ] Separar datos de cabecera, rachas, top champs y elo history del historial completo
- [ ] Evitar guardar en cache perfiles con historiales completos si no hace falta
- [ ] Valorar paginar o limitar el historial embebido en perfil
- [ ] Dejar la cache del perfil en modo parcial o TTL corto

Motivo:
La pagina de jugador tiene bastante riesgo de CPU y RAM.

### 6. Reducir payload de datos hacia plantillas
- [ ] Revisar cada `render_template()` importante
- [ ] Pasar solo campos necesarios a las vistas
- [ ] Evitar mandar listas enormes si solo se muestra top N
- [ ] Evitar objetos completos de partida cuando solo se usan 8-10 campos

Motivo:
Menos RAM, menos serializacion mental del codigo y menos render pesado.

## Prioridad 3 - Operacion y estabilidad

### 7. Unificar estrategia de cache e invalidacion
- [ ] Definir 3 tipos de cache claros:
- [ ] cache de lookups pequenos
- [ ] cache TTL generica
- [ ] snapshot cache con `data + timestamp + stale + calculating`
- [ ] Revisar invalidaciones al guardar partidas nuevas
- [ ] Revisar invalidaciones al cambiar configuraciones
- [ ] Documentar que snapshots dependen de que fuentes

Motivo:
Ahora ya hay buenos patrones, pero conviene hacerlos coherentes globalmente.

### 8. Anadir endpoints manuales para precalentar snapshots
- [ ] Crear `POST /api/update-historial-global`
- [ ] Mantener `POST /api/update-achievements`
- [ ] Revisar `POST /api/update-global-stats`
- [ ] Dejar un flujo operativo para recalcular tras deploy o mantenimiento

Motivo:
En Render gratis interesa precalentar manualmente en vez de hacer esperar al primer usuario.

### 9. Mostrar estado de snapshots en las paginas criticas
- [ ] Mostrar ultima actualizacion en `logros`
- [ ] Mostrar ultima actualizacion en `historial_global`
- [ ] Mostrar si el snapshot esta stale
- [ ] Anadir boton de recarga manual donde tenga sentido

Motivo:
Mejora la operacion y evita dudas sobre si los datos estan frescos.

## Prioridad 4 - Observabilidad y ajuste fino

### 10. Instrumentar tiempos y aciertos de cache
- [ ] Medir tiempo total por ruta importante
- [ ] Medir tiempo de construccion de snapshots
- [ ] Registrar hits/misses de cache
- [ ] Registrar cuando una vista fuerza recálculo
- [ ] Registrar tamano aproximado de datasets grandes

Motivo:
Sin metricas es facil optimizar lo que parece lento y no lo que realmente lo es.

### 11. Medir consumo de memoria por bloques
- [ ] Estimar tamano de historiales por jugador
- [ ] Estimar tamano de snapshots globales
- [ ] Detectar si hay caches que crecen demasiado
- [ ] Revisar especialmente `match_lookup_cache` y caches de perfil

Motivo:
La limitacion principal del hosting es RAM, no solo CPU.

## Prioridad 5 - Limpieza y mantenimiento

### 12. Limpiar caches, residuos y codigo ya obsoleto
- [ ] Revisar si `page_data_cache` sigue siendo necesario en todos los casos
- [ ] Eliminar claves antiguas de cache que ya no se usan
- [ ] Limpiar `__pycache__` antes de subir a produccion
- [ ] Revisar TODOs antiguos y quitar trabajo ya superado

Motivo:
Mantener el sistema pequeno y entendible tambien mejora estabilidad.

## Ajustes funcionales pendientes

### 13. Rebalancear sistema de logros y ligas
- [ ] Ajustar cortes de ligas altas en `services/achievements_service.py`
- [ ] Reequilibrar el peso de logros negativos frente a positivos
- [ ] Revisar logros publicos que puntuan demasiado para su frecuencia
- [ ] Preparar una version `balance v2` del catalogo

Motivo:
El sistema ya esta mejor, pero aun puede quedar mas competitivo y justo.

### 14. Mejorar UX de la pagina de logros
- [ ] Reforzar la narrativa de portada: top 3, logro raro, error mas repetido, ultimo secreto
- [ ] Diferenciar mejor prestigio, alertas y secretos
- [ ] Mostrar estado de cache y recarga manual en la propia pagina
- [ ] Reducir densidad inicial mostrando top N y "ver todos"

Motivo:
La pagina ya funciona mejor, pero aun puede ganar bastante en claridad.

## Despliegue

### 15. Checklist antes de subir a produccion
- [ ] Validar sintaxis Python de archivos tocados
- [ ] Validar JSONs de configuracion
- [ ] Probar carga en frio de index, logros, historial global y perfil jugador
- [ ] Probar recarga manual de snapshots
- [ ] Verificar que las invalidaciones funcionan al guardar nuevas partidas
- [ ] Revisar logs de memoria y tiempos tras desplegar

Motivo:
Con recursos tan justos, conviene desplegar con checklist y no a ciegas.
