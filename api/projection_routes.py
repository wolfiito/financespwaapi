# En: api/projection_routes.py

from flask import Blueprint, jsonify, request
from app import db
# Importamos TODOS los modelos y Enums necesarios
from models import RecurringRule, Transaction, Account, AccountType, FrequencyType, RecurringRuleType
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from sqlalchemy import or_, and_, func
from api.security import token_required
from services.recurring import next_execution_date

projection_bp = Blueprint('projection_bp', __name__, url_prefix='/api/projection')

@projection_bp.route('', methods=['GET'])
@token_required
def get_projection(current_user):
    """
    Motor de Proyección de FLUJO DE EFECTIVO (Refactorizado)

    Esta lógica se enfoca en UNA sola línea de tiempo: el "dinero real".
    Suma todos los ingresos fijos y resta TODOS los gastos fijos
    (incluyendo deudas y pagos de TC) para proyectar el flujo de efectivo.
    """

    try:
        months_ahead = int(request.args.get('months_ahead', 3))
    except ValueError:
        return jsonify({"error": "Parámetro 'months_ahead' debe ser un número."}), 400

    start_date = date.today()
    end_date = start_date + relativedelta(months=months_ahead)

    # --- 1. Cargar Estado Inicial ---

    accounts = Account.query.filter_by(user_id=current_user.id).all()
    if not accounts:
        return jsonify({"error": "El usuario no tiene cuentas configuradas"}), 400

    balances_query = db.session.query(
        Transaction.account_id,
        db.func.sum(Transaction.amount).label('total_balance')
    ).filter(
        Transaction.user_id == current_user.id
    ).group_by(Transaction.account_id).all()

    balances_dict = {acct_id: balance for acct_id, balance in balances_query}
    account_balances = {acc.id: balances_dict.get(acc.id, Decimal('0.00')) for acc in accounts}
    account_types = {acc.id: acc.type for acc in accounts}

    real_money_account_ids = [
        acc_id for acc_id, type in account_types.items()
        if type in [AccountType.CASH, AccountType.DEBIT_CARD]
    ]

    main_cash_account_id = next((acc_id for acc_id in real_money_account_ids), None)

    if not main_cash_account_id:
         return jsonify({"error": "No se encontró una cuenta de efectivo/débito para procesar pagos"}), 400

    initial_real_money_balance = sum(
        bal for acc_id, bal in account_balances.items()
        if acc_id in real_money_account_ids
    )

    # --- 2. Cargar "Actores" de la Simulación (¡MODIFICADO!) ---

    # ¡CAMBIO! Cargamos TODAS las reglas, incluyendo las de deudas (ya no filtramos por debt_id == None).
    rules_db = RecurringRule.query.filter(
        RecurringRule.user_id == current_user.id,
        RecurringRule.is_active.is_(True),
        RecurringRule.next_execution_date <= end_date,
    ).all()

    # ¡CAMBIO! Hacemos copias Y asignamos las reglas sin cuenta a la "Cuenta Maestra".
    rules_to_simulate = []
    for rule in rules_db:
        # Si la regla no tiene cuenta (es NULL), la asignamos a la Cuenta Maestra
        account_id_to_use = rule.account_id if rule.account_id else main_cash_account_id
        
        rules_to_simulate.append({
            "id": rule.id,
            "description": rule.description,
            "amount": rule.amount,
            "frequency": rule.frequency,
            "next_execution_date": max(rule.next_execution_date, rule.start_date or rule.next_execution_date),
            "end_date": rule.end_date,
            "account_id": account_id_to_use # Usamos la cuenta (real o la maestra)
        })

    credit_cards = [acc for acc in accounts if acc.type == AccountType.CREDIT_CARD]

    # --- 3. Motor de Simulación (FASE A: Gastos y Ingresos) ---
    # (Esta fase se queda igual)
    simulation_log = []
    current_date = start_date

    while current_date <= end_date:
        for rule in rules_to_simulate:
            if rule['next_execution_date'] == current_date and (
                not rule['end_date'] or current_date <= rule['end_date']
            ):
                simulation_log.append({
                    "date": current_date,
                    "description": rule['description'],
                    "amount": rule['amount'],
                    "account_id": rule['account_id']
                })
                rule['next_execution_date'] = next_execution_date(current_date, rule['frequency'])
        current_date += timedelta(days=1)

    # --- 4. Motor de Simulación (FASE B: Pagos de TC) (¡MODIFICADO!) ---

    payment_rules_log = []
    
    for card in credit_cards:
        if not card.payment_date or not card.closing_date: continue

        next_payment_date = get_next_payment_date(card.payment_date, start_date)

        while next_payment_date <= end_date:
            if card.payment_date > card.closing_date:
                last_closing_date = next_payment_date.replace(day=card.closing_date)
                start_of_statement = (last_closing_date.replace(day=1) - timedelta(days=1)).replace(day=card.closing_date) + timedelta(days=1)
            else:
                last_closing_date = (next_payment_date.replace(day=1) - timedelta(days=1)).replace(day=card.closing_date)
                start_of_statement = (last_closing_date.replace(day=1) - timedelta(days=1)).replace(day=card.closing_date) + timedelta(days=1)

            past_balance = db.session.query(
                db.func.sum(Transaction.amount)
            ).filter(
                Transaction.account_id == card.id,
                Transaction.user_id == current_user.id,
                func.date(Transaction.date).between(start_of_statement, last_closing_date)
            ).scalar() or Decimal('0.00')

            future_balance = Decimal('0.00')
            for log_entry in simulation_log:
                log_date = log_entry['date']
                # ¡CAMBIO! Sumamos gastos futuros (TC) de la FASE A
                if log_entry['account_id'] == card.id and \
                   start_of_statement <= log_date <= last_closing_date:
                    future_balance += log_entry['amount']

            total_due = past_balance + future_balance

            if total_due < 0:
                # ¡CAMBIO! Creamos UN SÓLO evento: el débito de la cuenta de Efectivo.
                # Ya no creamos el evento de "pago" (crédito) en la TC,
                # para no anular el balance en la Fase 5.
                payment_rules_log.append({
                    "date": next_payment_date,
                    "description": f"Pago Proyectado: {card.name}",
                    "amount": total_due, # Ya es negativo
                    "account_id": main_cash_account_id # Afecta a la cuenta maestra
                })

            next_payment_date = get_next_payment_date(card.payment_date, next_payment_date + timedelta(days=1))

    # --- 5. Combinar, Procesar y Devolver (¡MODIFICADO!) ---

    final_log = simulation_log + payment_rules_log
    final_log.sort(key=lambda x: x['date'])

    projected_real_money_balance = initial_real_money_balance
    processed_log_for_client = []

    for entry in final_log:
        amount = entry['amount']

        # Un gasto cargado a tarjeta no descuenta efectivo todavía; lo hará el
        # evento de pago de la tarjeta. De este modo no se cuenta dos veces.
        if entry['account_id'] in real_money_account_ids:
            projected_real_money_balance += amount

        processed_log_for_client.append({
            "date": entry['date'].isoformat(),
            "description": entry['description'],
            "amount": str(amount),
            "account_id": entry['account_id'],
            # Damos el balance de "dinero real" en cada paso
            "new_real_money_balance": str(projected_real_money_balance)
        })

    return jsonify({
        "start_balance": str(initial_real_money_balance),
        "projected_balance_end": str(projected_real_money_balance),
        "projection_start_date": start_date.isoformat(),
        "projection_end_date": end_date.isoformat(),
        "simulation_log_count": len(processed_log_for_client),
        "simulation_log": processed_log_for_client
    })

# --- Funciones de Ayuda (Se quedan igual) ---

def rule_to_dict(rule):
    """
    Serializa los Enums de la regla a strings (.value)
    """
    return {
        "id": rule.id,
        "description": rule.description,
        "amount": str(rule.amount),
        "frequency": rule.frequency.value,
        "type": rule.type.value,
        "next_execution_date": rule.next_execution_date
    }

def get_next_payment_date(payment_day: int, today: date = None) -> date:
    """
    Calcula la próxima fecha de pago de forma robusta.
    """
    if today is None:
        today = date.today()
    try:
        payment_date_this_month = date(today.year, today.month, payment_day)
    except ValueError:
        payment_date_this_month = today.replace(day=1) - relativedelta(days=1)
    if today <= payment_date_this_month:
        return payment_date_this_month
    else:
        first_day_of_next_month = (today.replace(day=1) + relativedelta(months=1))
        try:
            return date(first_day_of_next_month.year, first_day_of_next_month.month, payment_day)
        except ValueError:
            return first_day_of_next_month.replace(day=1) + relativedelta(months=1, days=-1)
