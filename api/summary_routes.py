# En: api/summary_routes.py

from flask import Blueprint, jsonify, request
from app import db
# ¡CAMBIO! Importamos TODOS los modelos y Enums necesarios
from models import Transaction, Account, RecurringRule, AccountType, TransactionType, RecurringRuleType, FrequencyType
from api.security import token_required
from sqlalchemy import func, or_, and_
from decimal import Decimal
# ¡CAMBIO! Importamos datetime para la corrección
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta

summary_bp = Blueprint('summary_bp', __name__, url_prefix='/api/summary')

@summary_bp.route('/categories', methods=['GET'])
@token_required
def get_category_summary(current_user):
    """
    Devuelve un resumen de los gastos del usuario,
    agrupados por categoría (para el gráfico de dona).
    ¡CAMBIO! Usa Enums.
    """
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        try:
            parsed_from = date.fromisoformat(date_from) if date_from else None
            parsed_to = date.fromisoformat(date_to) if date_to else None
        except ValueError:
            return jsonify({'error': 'date_from y date_to deben tener formato YYYY-MM-DD'}), 400

        summary_query = db.session.query(
            Transaction.category,
            func.sum(Transaction.amount).label('total_amount')
        ).filter(
            Transaction.user_id == current_user.id,
            # ¡CORREGIDO! Usamos el Enum
            Transaction.type == TransactionType.EXPENSE
        )
        if parsed_from:
            summary_query = summary_query.filter(func.date(Transaction.date) >= parsed_from)
        if parsed_to:
            summary_query = summary_query.filter(func.date(Transaction.date) <= parsed_to)
        summary_query = summary_query.group_by(
            Transaction.category
        ).all()

        summary_data = [
            {
                "category": category,
                "total": abs(total)
            }
            for category, total in summary_query if category and total
        ]
        return jsonify(summary_data), 200

    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

@summary_bp.route('/monthly_payments', methods=['GET'])
@token_required
def get_monthly_payments(current_user):
    """
    Devuelve TODOS los pagos (Reglas Fijas + Pagos de TC)
    proyectados para el mes calendario ACTUAL.
    ¡CAMBIO! Usa Enums y optimización N+1.
    """
    try:
        today = date.today()
        start_of_month = today.replace(day=1)
        end_of_month = (start_of_month + relativedelta(months=1)) - timedelta(days=1)

        rules_list = [] # Aquí guardaremos todos los eventos

        # --- 1. Buscar Reglas Fijas (Corregido) ---
        fixed_rules_query = RecurringRule.query.filter(
            RecurringRule.user_id == current_user.id,
            # ¡CORREGIDO! Usamos el Enum
            RecurringRule.type == RecurringRuleType.EXPENSE,
            RecurringRule.next_execution_date.between(start_of_month, end_of_month)
        ).all()

        for rule in fixed_rules_query:
            # ¡CORREGIDO! Usamos el helper bueno
            rules_list.append(rule_to_dict(rule))

        # --- 2. Buscar Pagos de TC (Corregido y Optimizado) ---

        # ¡CORREGIDO! Usamos Enum
        credit_cards = Account.query.filter_by(
            user_id=current_user.id,
            type=AccountType.CREDIT_CARD
        ).all()

        # ¡OPTIMIZACIÓN N+1!
        card_ids = [card.id for card in credit_cards]
        all_tc_transactions = []
        if card_ids:
            all_tc_transactions = db.session.query(Transaction).filter(
                Transaction.user_id == current_user.id,
                Transaction.account_id.in_(card_ids),
                # ¡BUG #3 CORREGIDO! Usamos func.date() para comparar
                func.date(Transaction.date) >= (start_of_month - relativedelta(months=2))
            ).all()
        # --- FIN OPTIMIZACIÓN ---

        for card in credit_cards:
            if not card.payment_date or not card.closing_date:
                continue

            closing_day = card.closing_date
            payment_day = card.payment_date

            # Determinar el mes de pago
            payment_month = today
            if today.day > payment_day:
                payment_month = today + relativedelta(months=1)
            else:
                payment_month = today

            # Solo nos importa si el pago es ESTE MES
            if payment_month.month != today.month:
                continue

            # Calcular la fecha de pago de este mes
            try:
                next_payment_date = payment_month.replace(day=payment_day)
            except ValueError:
                last_day_of_month = (payment_month.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
                next_payment_date = last_day_of_month

            # Calcular el período de corte para ESE pago
            if payment_day > closing_day:
                last_closing_date = next_payment_date.replace(day=closing_day)
                start_of_statement = (last_closing_date.replace(day=1) - timedelta(days=1)).replace(day=closing_day) + timedelta(days=1)
            else:
                last_closing_date = (next_payment_date.replace(day=1) - timedelta(days=1)).replace(day=closing_day)
                start_of_statement = (last_closing_date.replace(day=1) - timedelta(days=1)).replace(day=closing_day) + timedelta(days=1)

            # ¡OPTIMIZACIÓN! Filtramos la lista de Python
            statement_transactions = [
                t for t in all_tc_transactions
                if t.account_id == card.id and \
                   # ¡BUG #2 CORREGIDO! Usamos el helper to_date()
                   start_of_statement <= to_date(t.date) <= last_closing_date
            ]
            statement_balance = sum(t.amount for t in statement_transactions) or Decimal('0.00')
            # --- FIN OPTIMIZACIÓN ---

            if statement_balance < 0:
                payment_rule = {
                    "id": f"tc_payment_{card.id}",
                    "description": f"Pago Proyectado (Corte {last_closing_date.day}/{last_closing_date.month}): {card.name}",
                    "amount": str(statement_balance),
                    "frequency": FrequencyType.ONCE.value, # ¡Enum!
                    "next_execution_date": next_payment_date
                }
                rules_list.append(payment_rule)

        # Ordenar la lista final por fecha
        rules_list.sort(key=lambda x: x['next_execution_date'])

        # Formatear la respuesta final
        final_list = [
            {
                # ¡CORREGIDO! Convertimos fecha a string
                "date": r['next_execution_date'].isoformat(),
                "description": r['description'],
                "amount": str(r['amount'])
            }
            for r in rules_list
        ]

        return jsonify(final_list), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- ¡HELPER CORREGIDO (BUG #1)! ---
def rule_to_dict(rule):
    """
    Helper REFACTORIZADO para serializar correctamente
    Reglas Recurrentes a JSON.
    """
    return {
        "id": rule.id,
        "description": rule.description,
        "amount": str(rule.amount),
        "frequency": rule.frequency.value, # 'monthly', 'once', etc.
        "type": rule.type.value,         # 'expense', 'income'
        "next_execution_date": rule.next_execution_date.isoformat()
    }

# --- ¡NUEVO HELPER (BUG #2)! ---
def to_date(dt):
    """
    Convierte un objeto datetime a date de forma segura.
    Si ya es un date, lo devuelve sin cambios.
    """
    if isinstance(dt, datetime):
        return dt.date()
    return dt # Si ya es 'date', devuélvelo tal cual
