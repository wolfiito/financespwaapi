# En: api/debt_routes.py

from flask import Blueprint, jsonify, request
from app import db
# ¡CAMBIO! Importamos los modelos y TODOS los Enums que necesitamos
from models import Debt, RecurringRule, RecurringRuleType, FrequencyType
from datetime import date, datetime
from api.security import token_required
from decimal import Decimal

debt_bp = Blueprint('debt_bp', __name__, url_prefix='/api/debts')

# --- 1. ENDPOINT 'CREATE' (Refactorizado) ---
@debt_bp.route('/new', methods=['POST'])
@token_required
def create_debt(current_user):
    """
    Registra una nueva deuda y su regla de pago recurrente.
    ¡CAMBIO! Ya no usa 'payments_made'.
    ¡CAMBIO! Usa Enums para la regla.
    """
    data = request.json

    try:
        # 1. Convertir datos de ENUMS primero (falla rápido)
        try:
            payment_frequency_str = data['frequency']
            payment_frequency = FrequencyType(payment_frequency_str)
        except (KeyError, ValueError):
            return jsonify({"error": f"Frecuencia no válida o faltante: {payment_frequency_str}"}), 400

        # 2. Crear el 'Debt'
        new_debt = Debt(
            debt_name=data['debt_name'],
            original_amount=data['original_amount'],
            monthly_payment_amount=data['monthly_payment_amount'],
            term_months=data['term_months'],
            # ¡CAMBIO! 'payments_made' se eliminó.
            # La lógica ahora es automática.
            user_id=current_user.id
        )
        db.session.add(new_debt)

        # 3. Datos para la regla
        first_payment_date_str = data['first_payment_date']
        next_payment = date.fromisoformat(first_payment_date_str)

        # 4. Crear la 'RecurringRule'
        new_rule = RecurringRule(
            description=f"Pago de: {new_debt.debt_name}",
            # ¡CAMBIO! El monto de la regla debe ser negativo
            amount=abs(Decimal(new_debt.monthly_payment_amount)) * -1,

            # --- ¡CAMBIOS DE ENUM! ---
            type=RecurringRuleType.EXPENSE, # Usamos el Enum
            frequency=payment_frequency,     # Usamos el Enum
            # --- FIN CAMBIOS ---

            next_execution_date=next_payment,
            user_id=current_user.id,
            associated_debt=new_debt # Vinculamos la regla a la deuda
        )

        db.session.add(new_rule)

        # 5. Commit atómico
        # Si algo falla (la deuda o la regla), todo se revierte.
        db.session.commit()

        return jsonify({
            "message": "Deuda y regla de pago creadas exitosamente",
            "debt_id": new_debt.id,
            "rule_id": new_rule.id
        }), 201

    except KeyError as e:
        db.session.rollback()
        return jsonify({"error": f"Dato faltante: {str(e)}"}), 400

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- 2. ENDPOINT 'GET ALL' (¡Nuevo!) ---
@debt_bp.route('/', methods=['GET'])
@token_required
def get_debts(current_user):
    """
    Devuelve todas las deudas del usuario,
    calculando el total pagado y el restante.
    """
    try:
        debts = Debt.query.filter_by(user_id=current_user.id).all()

        result_list = []
        for debt in debts:
            result_list.append({
                "debt_id": debt.id,
                "debt_name": debt.debt_name,
                "original_amount": str(debt.original_amount),
                "monthly_payment_amount": str(debt.monthly_payment_amount),

                # ¡MAGIA! Estas son nuestras propiedades calculadas
                "total_paid": str(debt.total_paid),
                "remaining_amount": str(debt.remaining_amount)
            })

        return jsonify(result_list), 200

    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500