# TODO - Refactorización Sistema de Estadísticas

## Fase 1: Corrección del Error Inmediato ✅ COMPLETADA
- [x] Analizar el error de tuplas anidadas en `extract_global_records()`
- [x] Corregir `services/stats_service.py` - función `extract_global_records()`
  - Añadir verificación de tipos para tuplas anidadas
  - Implementar desempaquetado correcto de tuplas
- [x] Verificar que no haya otras llamadas con el mismo problema en `blueprints/stats.py`

## Fase 2: Optimización para Render Free Tier ✅ COMPLETADA
- [x] Optimizar `blueprints/stats.py` - función `_compile_all_matches()`
  - Implementar procesamiento por lotes (batch processing)
  - Añadir límites de memoria
- [x] Optimizar `blueprints/stats.py` - función `_calculate_and_save_global_stats()`
  - Usar `global_stats_cache` para evitar recálculos
  - Implementar cálculo incremental
- [x] Optimizar filtros dinámicos en `estadisticas_globales()`
  - Reducir complejidad algorítmica
  - Usar generadores en lugar de listas donde sea posible


## Fase 3: Mejoras de Arquitectura ✅ COMPLETADA
- [x] Separar lógica de cálculo en servicios dedicados
- [x] Implementar manejo de errores robusto
- [x] Añadir logging detallado para debugging en Render
- [x] Optimizar uso de memoria (liberar referencias innecesarias)

## Fase 4: Testing y Validación 🔄 PENDIENTE DE PRUEBAS
- [ ] Probar corrección del error de tupla
- [ ] Validar filtros por cola y campeón
- [ ] Verificar rendimiento con datos reales
- [ ] Confirmar compatibilidad con Render free tier


## Archivos a Modificar
1. `services/stats_service.py` - Corrección del error y optimización
2. `blueprints/stats.py` - Refactorización completa
3. `templates/estadisticas.html` - Optimizaciones (si es necesario)

## Notas
- Prioridad: Corregir error de tupla primero (bloqueante)
- Usar metodologías existentes: caché en memoria, lazy loading
- Mantener compatibilidad con el resto de la aplicación
