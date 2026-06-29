Instrucciones para desplegar en Render y habilitar precomputed HTML

Resumen
- El proyecto genera HTML precalculado y lo sube a `precomputed/` en el repo, branch `main`, si `GITHUB_TOKEN` esta configurado.
- La app sirve primero HTML pregenerado fresco desde disco local y, si Render perdio ese disco, lo recupera desde GitHub usando `precomputed/_manifest.json`.
- El servicio web arranca un worker interno que regenera HTML periodicamente y cuando detecta datos actualizados.
- Tambien puedes mantener un Scheduled Job para forzar regeneraciones independientes del trafico web.

Variables de entorno necesarias
- `GITHUB_TOKEN`: token con permisos `repo` para escribir en el repo.
- `RIOT_API_KEY`: API key de Riot.
- `SECRET_KEY`: Flask secret.
- `ADMIN_TOKEN`: token para llamadas administrativas. Opcional; si no esta, se usa `SECRET_KEY`.
- `PRECOMPUTE_INTERVAL_SECONDS`: intervalo del worker interno de HTML. Opcional, default `600`.
- `PRECOMPUTE_MAX_PLAYERS`: maximo de perfiles pregenerados por ciclo. Opcional, default `50`.
- `PRECOMPUTE_INITIAL_DELAY_SECONDS`: espera inicial antes del primer ciclo. Opcional, default `30`.
- `PRECOMPUTE_HISTORIAL_GLOBAL`: activa el precompute de historial global. Opcional, default `1`. El historial global esta limitado a 2 paginas.

Servicio web
- `Procfile` debe usar `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`.
- Usa un solo worker si mantienes los workers internos dentro del servicio web, para evitar ciclos duplicados escribiendo a GitHub.

Scheduled Job recomendado
- Command: `python generate_precomputed.py --players 100`
- Frequency: `*/10 * * * *` o segun tu preferencia.
- Environment: las mismas variables que el servicio web, especialmente `GITHUB_TOKEN` y `RIOT_API_KEY`.
- El job espera a que el HTML quede guardado localmente y en GitHub antes de terminar.

Ejecutar manualmente tras deploy
```bash
python generate_precomputed.py --players 100
```

Endpoints de administracion
- Invalidar una clave: `POST /admin/invalidate` con header `X-ADMIN-TOKEN: <ADMIN_TOKEN>` y body JSON `{"key":"index"}`.
- Listar HTML local y claves guardadas en GitHub: `GET /admin/list` con header `X-ADMIN-TOKEN: <ADMIN_TOKEN>`.

Notas
- Las rutas siguen teniendo fallback: si no hay HTML fresco, renderizan la pagina y encolan escritura para futuras visitas.
- Si escalas a multiples instancias, GitHub actua como almacenamiento centralizado.
- Evita automatizar commits desde el mismo pipeline de deploy sin control, porque podria provocar redeploy loops.
