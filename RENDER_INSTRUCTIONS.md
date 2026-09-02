# Despliegue sostenible en Render Free

## Límite real

Un servicio web Free se detiene tras 15 minutos sin tráfico y cada workspace
recibe 750 horas de instancia por mes. Un único servicio activo durante el mes
más largo consume como máximo 744 horas, por lo que cabe en el límite. No se
deben ejecutar otros servicios Free activos en el mismo workspace.

Render puede reiniciar servicios Free y su disco es efímero. Los datos
persistentes de esta aplicación se guardan en GitHub, no en el disco de Render.

## Configuración del servicio web

- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
- Health check path: `/healthz`
- Variables obligatorias: `RIOT_API_KEY`, `GITHUB_TOKEN` y `SECRET_KEY`.
- Mantén `PRECOMPUTE_ENABLED=0` (valor por defecto). Evita commits masivos de
  HTML que no añaden frescura a los datos.

Las cadencias por defecto son: rango e índice cada 15 minutos, LP cada 30,
partidas en vivo cada 5, estadísticas globales cada 30, récords cada 2 horas y
Data Dragon cada 24 horas. Se pueden ajustar con las variables homónimas
`*_INTERVAL` definidas en `config/settings.py`.

## Mantenerlo despierto

La aplicación ya no se hace llamadas HTTP a sí misma: eso consume red saliente
y puede activar el límite de tráfico iniciado por el servicio. Configura un
monitor externo para solicitar `https://<tu-servicio>.onrender.com/healthz`
cada 10 minutos. Esa ruta es constante y no toca Riot, GitHub ni plantillas.

Revisa cada mes en Render Dashboard > Billing > Monthly Included Usage que el
workspace tenga una única instancia Free activa y menos de 750 horas. Si no
puedes garantizar ese presupuesto compartido, elimina el monitor: la web
despertará bajo demanda tras aproximadamente un minuto.

## No usar Scheduled Jobs de Render Free

Render no ofrece Cron Jobs gratuitos. No crees un Scheduled Job para
`generate_precomputed.py` en este plan: requeriría un plan de pago y duplicaría
el trabajo de la aplicación.
