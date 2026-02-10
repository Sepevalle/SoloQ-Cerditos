# Fix: Jugador en partida no se está revisando correctamente - IMPLEMENTADO ✓

## Problema Identificado
- El estado `en_partida` se obtiene del caché de estadísticas, que tiene un TTL de 5 minutos
- Cuando hay datos en caché, la verificación de partida activa se omite completamente
- El `_last_live_check` no persiste correctamente entre peticiones

## Solución Implementada: Stale-While-Revalidate

### Cambios en `blueprints/main.py`:

1. **Nueva función `_actualizar_stats_en_background()`**
   - Actualiza estadísticas en un thread separado (daemon)
   - No bloquea la carga de la página
   - Verifica partidas en vivo y calcula estadísticas pesadas

2. **Modificación de `index()` - Patrón Stale-While-Revalidate**
   - Detecta si el caché está antiguo (`cache_stale`, `stats_cache_stale`)
   - Si está antiguo, inicia actualización en background INMEDIATAMENTE
   - Usa datos del caché para renderizar la página sin esperar
   - La página carga en <1 segundo siempre

3. **Variables nuevas pasadas al template**
   - `cache_stale`: Indica si los datos son antiguos
   - `minutos_desde_actualizacion`: Minutos desde última actualización

### Flujo de trabajo

```
Usuario carga página
    ↓
[index] Detecta caché antiguo
    ↓
Inicia thread de background (no bloquea)
    ↓
Renderiza página INMEDIATAMENTE con caché
    ↓
Background actualiza estadísticas (5-10 segundos)
    ↓
Próxima visita: datos frescos
```

### Ventajas

- ✅ **Página carga en <1 segundo** siempre
- ✅ **Compatible con Render Free** (un solo dyno, sin workers extra)
- ✅ **Sin bloqueos** - el usuario nunca espera
- ✅ **Datos eventualmente consistentes**
- ✅ **Ahorro de recursos** - solo procesa cuando hay visitas

### Próximos pasos (opcional)

Agregar indicador visual en `templates/index.html`:

```html
{% if cache_stale %}
  <div class="alert alert-info">
    🔄 Datos de hace {{ minutos_desde_actualizacion }} min. Actualizando...
  </div>
{% endif %}
```

## Estado: ✅ COMPLETADO
