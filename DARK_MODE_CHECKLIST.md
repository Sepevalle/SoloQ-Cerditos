# 🌙 Checklist de Verificación - Modo Oscuro

## Verificación Rápida

Haz clic en el botón **"Modo Oscuro"** en cada página y verifica:

### ✅ Página Principal (Index)
- [ ] Navbar tiene fondo gris oscuro
- [ ] Texto de navegación es blanco
- [ ] Tabla es legible completamente
- [ ] Encabezados de tabla tienen fondo gris
- [ ] Filas alternadas tienen ligera diferencia de color
- [ ] Links son azul claro (#66b3ff)
- [ ] Botón "Modo Oscuro" cambia a "Modo Claro"
- [ ] Último actualizado muestra bien
- [ ] Filtro de cola (select) tiene fondo oscuro
- [ ] Imágenes de jugadores se ven bien

### ✅ Página de Estadísticas
- [ ] **TABS:** Los tabs son visibles y diferenciados
  - [ ] Tab no activo: texto gris
  - [ ] Tab activo: fondo gris oscuro + texto blanco
  - [ ] Hover en tabs: color más claro
- [ ] Formularios tienen fondo oscuro
- [ ] Cards de estadísticas legibles
- [ ] Win Rate Global muestra color correcto
- [ ] Campeones Más Jugados list-group adaptado
- [ ] Badges tienen fondo azul oscuro
- [ ] Texto "text-muted" es gris claro

### ✅ Página de Perfil (Jugador)
- [ ] Rank cards tienen fondo oscuro
- [ ] Encabezado de card (Elo) tiene fondo gris
- [ ] Detalles del ranking visibles
- [ ] Championes principales mostrados correctamente
- [ ] Match history table legible
  - [ ] Encabezados con fondo gris
  - [ ] Filas con hover funciona
  - [ ] Damage bars visibles (especialmente true damage)
  - [ ] KDA text claro
- [ ] Filtros de match history funcionan
- [ ] Todas las imágenes se ven bien

### ✅ Página 404
- [ ] Navbar funciona correctamente
- [ ] Botón toggle de tema está presente
- [ ] Alert danger tiene fondo rojo oscuro
- [ ] Texto de error es rojo claro
- [ ] Botón "Volver" es visible
- [ ] El toggle del tema persiste

---

## Verificación Detallada de Contraste

### Textos Que Deben Ser Legibles:
✅ Blanco puro (#fff) en fondo #121212 - **EXCELENTE**
✅ Gris claro (#e0e0e0) en fondo #121212 - **MUY BUENO**
✅ Azul claro (#66b3ff) en fondo #121212 - **BUENO**
✅ Gris medio (#adb5bd) en fondo #495057 - **BUENO**

### Elementos que Podrían Necesitar Atención:
⚠️ Si algo se ve oscuro: Aumentar brillo
⚠️ Si parpadea: Revisar z-index
⚠️ Si no responde al click: Verificar CSS !important

---

## Cómo Guardar la Preferencia

El tema se guarda automáticamente en `localStorage`:
- Primera vez: Aparece "Modo Oscuro"
- Después de clickear: Se guarda la preferencia
- Al recargar: Se mantiene el tema seleccionado

---

## Si Encuentras Problemas:

### Problema: Elemento no se ve en oscuro
**Solución:**
1. Verifica que el elemento esté dentro de un contenedor con `.dark-mode`
2. Busca estilos `!important` que podrían sobrescribir
3. Abre DevTools (F12) y inspecciona el elemento

### Problema: Links no se ven
**Solución:** Deben estar en azul `#66b3ff`

### Problema: Tabs no funcionan
**Solución:** Verifica que los selectors `.dark-mode .nav-tabs` estén aplicados

### Problema: Formularios oscuros
**Solución:** Deben tener `background-color: #495057 !important`

---

## Archivos a Verificar

1. ✅ `static/style.css` - Estilos centralizados
2. ✅ `templates/index.html` - Estilos inline de index
3. ✅ `templates/estadisticas.html` - Tabs y estilos
4. ✅ `templates/jugador.html` - Cards y profiling
5. ✅ `templates/404.html` - Página de error

---

## Paleta Final de Colores

```
Fondo Principal:      #121212 (Negro profundo)
Fondo Secundario:     #343a40 (Gris oscuro)
Fondo Terciario:      #495057 (Gris medio)
Texto Primario:       #ffffff (Blanco)
Texto Secundario:     #e0e0e0 (Gris claro)
Texto Terciario:      #adb5bd (Gris medio claro)
Bordes:               #6c757d (Gris)
Links:                #66b3ff (Azul claro)
Links Hover:          #99ccff (Azul más claro)
```

---

**¡La adaptación a modo oscuro está completa!** 🌙
