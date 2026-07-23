# Finanzas PWA API

API REST en Flask para registrar usuarios, cuentas, movimientos, deudas, reglas recurrentes y proyecciones financieras. La base URL depende del despliegue; localmente suele ser `http://127.0.0.1:5000`.

## Autenticación

Salvo donde se indique lo contrario, todas las rutas requieren un JWT en la cabecera:

```http
x-access-token: <jwt>
```

El token se obtiene en `POST /api/auth/login`, usa HS256 y expira 24 horas después de emitirse.

Los importes se reciben como texto o número y se devuelven como cadenas para conservar precisión decimal. Los tipos válidos son:

- Cuenta: `cash`, `debit_card`, `credit_card`.
- Movimiento: `expense`, `income`, `initial_balance`, `debt_payment`.
- Regla: `expense`, `income`.
- Frecuencia: `daily`, `weekly`, `bi_weekly`, `monthly`, `yearly`, `once`.

## Estado del servicio

### `GET /`

No requiere autenticación. Comprueba que el backend responde.

Respuesta: `{"message": "Finance backend is up and running!"}`.

## Usuarios

### `POST /api/auth/register`

No requiere autenticación. Crea un usuario.

```json
{"username":"ana","password":"una-contraseña"}
```

Devuelve `201` al crear el usuario, `409` si el nombre ya existe y `400` ante datos inválidos.

### `POST /api/auth/login`

No requiere autenticación. Verifica las credenciales y devuelve el token.

```json
{"username":"ana","password":"una-contraseña"}
```

Respuesta `200`:

```json
{"message":"Inicio de sesión exitoso","token":"<jwt>"}
```

## Cuentas

### `POST /api/accounts/new`

Crea una cuenta del usuario autenticado.

```json
{
  "name":"Tarjeta principal",
  "type":"credit_card",
  "closing_date":5,
  "payment_date":24
}
```

`name` y `type` son obligatorios. Las fechas de corte y pago se almacenan tal cual y son opcionales, aunque se necesitan para calcular pagos proyectados de una tarjeta de crédito.

### `GET /api/accounts/summary`

Devuelve todas las cuentas del usuario con el acumulado de los movimientos asociados.

```json
[{"account_id":1,"account_name":"Efectivo","account_type":"cash","current_balance":"2500.00"}]
```

### `GET /api/accounts/<account_id>/transactions`

Devuelve el nombre de una cuenta propia y todos sus movimientos, ordenados del más reciente al más antiguo. Responde `404` si la cuenta no existe o no pertenece al usuario.

## Movimientos

### `POST /api/transactions/new`

Registra un movimiento. Si no se envía `account_id`, crea o reutiliza una cuenta de efectivo para el usuario.

```json
{
  "description":"Supermercado",
  "amount":"850.00",
  "type":"expense",
  "category":"Comida",
  "account_id":1,
  "installments":1,
  "debt_id":null,
  "date":"2026-07-23T10:30:00"
}
```

Obligatorios: `description`, `amount`, `type`. Para `expense` y `debt_payment` el backend guarda el monto como negativo; para `income` e `initial_balance`, como positivo. `installments` tiene valor predeterminado `1`; `category`, `account_id`, `debt_id` y `date` son opcionales.

### `GET /api/transactions?page=1&per_page=20`

Devuelve movimientos del usuario ordenados por fecha descendente. `page` vale `1` por defecto y `per_page` vale `20` (máximo `100`). Puede filtrarse por `account_id`, `category`, `type`, `date_from` y `date_to`.

Cada elemento incluye id, descripción, importe, fecha, categoría, cuenta, tipo, número de mensualidades y deuda asociada.

### `POST /api/transactions/set_initial`

Agrega un movimiento de saldo inicial a la cuenta de efectivo principal. No borra saldos previos.

```json
{"amount":"5000.00","date":"2026-07-01T00:00:00"}
```

`amount` es obligatorio y no puede ser negativo; `date` es opcional.

### `GET /api/transactions/balance`

Devuelve el saldo real actual, sumando movimientos de las cuentas `cash` y `debit_card` del usuario.

```json
{"current_balance":"2500.00","user_id":1}
```

## Deudas

### `POST /api/debts/new`

Crea una deuda y, en la misma operación, una regla recurrente de pago asociada.

```json
{
  "debt_name":"Préstamo",
  "original_amount":"12000.00",
  "monthly_payment_amount":"1000.00",
  "term_months":12,
  "frequency":"monthly",
  "first_payment_date":"2026-08-05"
}
```

La regla generada es un gasto por el pago mensual en negativo. La deuda calcula `total_paid` a partir de los movimientos asociados a su `debt_id` y `remaining_amount` como el importe original menos lo pagado.

### `GET /api/debts/`

Lista las deudas del usuario, con sus importes original, mensual, total abonado y restante.

## Reglas recurrentes

### `POST /api/rules/new`

Crea una regla recurrente.

```json
{
  "description":"Renta",
  "amount":"9500.00",
  "type":"expense",
  "frequency":"monthly",
  "first_execution_date":"2026-08-01"
}
```

Obligatorios: `description`, `amount`, `type`, `frequency` y `start_date` (también se acepta temporalmente `first_execution_date`). Los gastos se guardan como montos negativos. `end_date`, `account_id` y `category` son opcionales.

### `GET /api/rules/`

Lista todas las reglas del usuario, por próxima fecha de ejecución.

### `DELETE /api/rules/<rule_id>`

Elimina una regla propia. Devuelve `404` si no existe o no pertenece al usuario.

### `PATCH /api/rules/<rule_id>`

Actualiza los campos de una regla propia. Para detenerla sin perder su historial, envía `{"is_active": false}`.

## Resúmenes y proyección

### `GET /api/summary/categories`

Devuelve gastos agrupados por categoría. Acepta opcionalmente `date_from` y `date_to` (`YYYY-MM-DD`), omite movimientos sin categoría y entrega el total en valor absoluto.

### `GET /api/summary/monthly_payments`

Devuelve gastos recurrentes con fecha dentro del mes actual y pagos proyectados de tarjetas de crédito. Para tarjetas se usan `closing_date` y `payment_date` de la cuenta y los movimientos que caen en el periodo de corte.

### `GET /api/projection?months_ahead=3`

Proyecta el flujo de efectivo desde hoy durante el número de meses indicado (tres por defecto). Requiere que el usuario tenga una cuenta `cash` o `debit_card`.

La respuesta incluye saldo inicial, saldo proyectado al final, rango de fechas y una bitácora de eventos con el saldo real después de cada uno.

## Modelo de datos

- `User`: usuario y hash bcrypt de contraseña.
- `Account`: cuenta, tipo, fechas de corte/pago y propietario.
- `Transaction`: movimiento, importe, fecha, categoría, cuenta, mensualidades y posible deuda.
- `Debt`: deuda original, pago mensual, plazo y pagos asociados.
- `RecurringRule`: ingreso/gasto fijo, frecuencia, próxima ejecución, cuenta y deuda opcionales.

## Límites actuales


## Actualización: reglas, CRUD y consultas

Las reglas ahora aceptan `start_date`, `end_date` opcional, `account_id` opcional y `category`. Para compatibilidad temporal también se acepta `first_execution_date` como alias de `start_date`.

```json
{
  "description":"Renta",
  "amount":"9500.00",
  "type":"expense",
  "frequency":"bi_weekly",
  "start_date":"2026-08-01",
  "end_date":"2026-12-31",
  "account_id":1,
  "category":"Hogar"
}
```

- `POST /api/rules/process` procesa manualmente las reglas pendientes del usuario; acepta `{"until":"YYYY-MM-DD"}` de forma opcional.
- `PATCH /api/rules/<id>` permite editar una regla, incluyendo `is_active`; una regla con ejecuciones ya registradas se desactiva en vez de eliminarse.
- `PATCH` y `DELETE /api/accounts/<id>`, `/api/debts/<id>` y `/api/transactions/<id>` editan o eliminan recursos propios. Cuentas con movimientos/reglas y deudas con pagos devuelven `409` para no romper el historial.
- `GET /api/transactions` usa paginación: `page` (desde 1) y `per_page` (1 a 100). Acepta `account_id`, `category`, `type`, `date_from` y `date_to`. La respuesta es `{"items": [...], "pagination": {...}}`.
- `GET /api/summary/categories` acepta `date_from` y `date_to` con formato `YYYY-MM-DD`.

Consulta [OPERATIONS.md](OPERATIONS.md) para ejecutar la migración y configurar la tarea diaria que genera movimientos reales.
