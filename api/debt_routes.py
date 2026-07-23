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
            return jsonify({"error": "Frecuencia no válida o faltante"}), 400

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
            start_date=next_payment,
            end_date=None,
            is_active=True,
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


@debt_bp.route('/<int:debt_id>', methods=['PATCH'])
@token_required
def update_debt(current_user, debt_id):
    debt = Debt.query.filter_by(id=debt_id, user_id=current_user.id).first()
    if not debt:
        return jsonify({'error': 'Deuda no encontrada'}), 404
    data = request.get_json(silent=True) or {}
    try:
        for field in ('debt_name',):
            if field in data:
                if not isinstance(data[field], str) or not data[field].strip():
                    return jsonify({'error': f'{field} no puede estar vacío'}), 400
                setattr(debt, field, data[field].strip())
        for field in ('original_amount', 'monthly_payment_amount'):
            if field in data:
                value = Decimal(str(data[field]))
                if value <= 0:
                    return jsonify({'error': f'{field} debe ser mayor que cero'}), 400
                setattr(debt, field, value)
        if 'term_months' in data:
            if not isinstance(data['term_months'], int) or data['term_months'] <= 0:
                return jsonify({'error': 'term_months debe ser un entero positivo'}), 400
            debt.term_months = data['term_months']
        db.session.commit()
        return jsonify(debt_to_dict(debt)), 200
    except (ValueError, ArithmeticError):
        db.session.rollback()
        return jsonify({'error': 'Los montos deben ser numéricos'}), 400
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudo actualizar la deuda'}), 500


@debt_bp.route('/<int:debt_id>', methods=['DELETE'])
@token_required
def delete_debt(current_user, debt_id):
    debt = Debt.query.filter_by(id=debt_id, user_id=current_user.id).first()
    if not debt:
        return jsonify({'error': 'Deuda no encontrada'}), 404
    if debt.payments.count():
        return jsonify({'error': 'La deuda tiene pagos registrados; no se puede eliminar.'}), 409
    if debt.associated_rule:
        db.session.delete(debt.associated_rule)
    db.session.delete(debt)
    db.session.commit()
    return jsonify({'message': 'Deuda eliminada exitosamente'}), 200


def debt_to_dict(debt):
    return {
        'debt_id': debt.id,
        'debt_name': debt.debt_name,
        'original_amount': str(debt.original_amount),
        'monthly_payment_amount': str(debt.monthly_payment_amount),
        'term_months': debt.term_months,
        'total_paid': str(debt.total_paid),
        'remaining_amount': str(debt.remaining_amount),
    }
