Instrucciones para desplegar en Render y habilitar precomputed HTML

Resumen
- El proyecto genera HTML precalculados y los sube a `precomputed/` en el repo (branch `main`) si la variable `GITHUB_TOKEN` está configurada.
- Para que los archivos estén disponibles en producción tras deploy en Render, se recomienda crear un Scheduled Job que ejecute `generate_precomputed.py` periódicamente y/o inmediatamente después del deploy.

Variables de entorno necesarias (Render service -> Environment)
- `GITHUB_TOKEN` : Token con permisos `repo` para escribir en el repo
- `RIOT_API_KEY`  : API key de Riot
- `SECRET_KEY`    : Flask secret
- `ADMIN_TOKEN`   : Token para llamadas administrativas (opcional; si no está, se usa `SECRET_KEY`)

Configurar el servicio web
- `Procfile` ya actualizado para usar `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`

Scheduled Job recomendado (Render)
- Crear un "Scheduled Job" en Render con estos ajustes:
  - Command: `python generate_precomputed.py --players 100`
  - Frequency: `*/10 * * * *` (cada 10 minutos) o según tu preferencia
  - Environment: usa las mismas variables que el servicio web (importante `GITHUB_TOKEN`)

Ejecutar manualmente tras deploy
- En el panel de jobs de Render o usando la consola, ejecutar:
  ```bash
  python generate_precomputed.py --players 100
  ```

Uso del endpoint de administración
- Para invalidar una clave precomputed desde la app:
  - POST `/admin/invalidate` con header `X-ADMIN-TOKEN: <ADMIN_TOKEN>` y body JSON `{"key":"index"}`
- Para listar archivos locales precomputed:
  - GET `/admin/list` con header `X-ADMIN-TOKEN: <ADMIN_TOKEN>`

Notas
- Evita subir archivos en cada request en producción: el script y el scheduler hacen el trabajo pesado.
- Si escalas a múltiples instancias, el repositorio actúa como almacenamiento centralizado.
- No automatices commits desde el mismo repo en el mismo pipeline sin control (podría provocar re-deploy loops). Usar un Scheduled Job separado evita este problema.

Contacto
- Si quieres, puedo añadir el Scheduled Job `render.yaml` o ajustar la estrategia para usar S3/Spaces en lugar de GitHub.
