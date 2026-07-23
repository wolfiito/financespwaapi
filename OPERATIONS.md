# Operación de reglas recurrentes

Las reglas se convierten en movimientos reales mediante un proceso idempotente. Cada ejecución se registra en `recurring_execution`; por eso una misma regla y fecha no se pueden duplicar aunque el proceso se ejecute de nuevo.

## Instalar la actualización

En PythonAnywhere, antes de recargar la aplicación:

```bash
cd ~/financespwaapi
git pull origin main
python migrate_db.py
```

Haz una copia de `finanzas.db` antes de ejecutar la migración. El script agrega a las reglas `start_date`, `end_date`, `is_active` y `category`, y crea la tabla de ejecuciones. No borra movimientos existentes.

## Procesamiento diario

En **Tasks** de PythonAnywhere agrega una tarea diaria (cambia la ruta del entorno virtual por la de tu cuenta):

```bash
cd ~/financespwaapi && /home/TU_USUARIO/.virtualenvs/TU_ENTORNO/bin/flask --app app process-rules
```

La tarea debe ejecutarse una vez al día, después de medianoche. Para poner al día reglas atrasadas, puedes ejecutarla manualmente; procesará todas las fechas pendientes hasta hoy sin repetir movimientos.

También existe el endpoint autenticado `POST /api/rules/process`. Acepta opcionalmente:

```json
{"until":"2026-07-23"}
```

Úsalo solo como ejecución manual para el usuario que inició sesión; la tarea diaria es el mecanismo automático para todos los usuarios.

## Semántica de frecuencia

- `once`: un único movimiento en `start_date` y la regla se desactiva.
- `weekly`: cada siete días desde `start_date`.
- `bi_weekly`: cada catorce días desde `start_date`.
- `monthly`: cada mes desde la fecha anterior; si el día no existe (por ejemplo, 31 de febrero), se ajusta al último día válido de ese mes.
- `daily` y `yearly`: diario o anual respectivamente.

`end_date` es opcional e inclusiva: una regla no genera movimientos posteriores a esa fecha. Si se omite, sigue activa hasta que se desactive con `PATCH /api/rules/<id>` enviando `{"is_active": false}`.
