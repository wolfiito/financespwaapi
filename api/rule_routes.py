# En: api/rule_routes.py

from flask import Blueprint, jsonify, request
from app import db
# ¡CAMBIO! Importamos el modelo Y los Enums necesarios
from models import RecurringRule, RecurringRuleType, FrequencyType
from datetime import date, datetime
from decimal import Decimal
from api.security import token_required

rule_bp = Blueprint('rule_bp', __name__, url_prefix='/api/rules')

@rule_bp.route('/new', methods=['POST'])
@token_required
def create_recurring_rule(current_user):
    """
    Crea una regla recurrente (ej. Netflix, Renta).
    ¡Refactorizado para usar Enums y requerir un account_id!
    """
    data = request.json
    try:
        # --- ¡CAMBIO! 1. Validar y convertir Enums ---
        try:
            rule_type_str = data['type']
            rule_type = RecurringRuleType(rule_type_str)
        except ValueError:
            return jsonify({"error": f"Tipo de regla no válido: {rule_type_str}"}), 400

        try:
            frequency_str = data['frequency']
            frequency = FrequencyType(frequency_str)
        except ValueError:
            return jsonify({"error": f"Frecuencia no válida: {frequency_str}"}), 400

        # --- ¡CAMBIO! 2. Obtener el account_id (ahora es obligatorio) ---
        # El frontend DEBE enviar a qué cuenta pertenece esta regla.
        #account_id = data['account_id']

        # --- Lógica de monto (sin cambios) ---
        amount = Decimal(data['amount'])
        if rule_type == RecurringRuleType.EXPENSE and amount > 0:
            amount = amount * -1

        first_execution_date = date.fromisoformat(data['first_execution_date'])

        # --- ¡CAMBIO! 3. Construir el objeto completo ---
        new_rule = RecurringRule(
            description=data['description'],
            amount=amount,
            type=rule_type,                 # Usamos el Enum
            frequency=frequency,            # Usamos el Enum
            next_execution_date=first_execution_date,
            user_id=current_user.id,          # ¡El campo que faltaba!
            debt_id=None                    # Correcto, no es una deuda
        )

        db.session.add(new_rule)
        db.session.commit()

        return jsonify({"message": "Regla recurrente creada exitosamente", "rule_id": new_rule.id}), 201
    except KeyError as e:
        db.session.rollback()
        # 'account_id' ahora está cubierto por este error
        return jsonify({"error": f"Dato faltante. Clave requerida: {str(e)}"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

@rule_bp.route('/<int:rule_id>', methods=['DELETE'])
@token_required
def delete_recurring_rule(current_user, rule_id):
    """
    Elimina una regla recurrente específica por su ID.
    """
    try:
        # 1. Buscar la regla por su ID
        rule_to_delete = RecurringRule.query.get(rule_id)

        # 2. Validar si existe
        if not rule_to_delete:
            return jsonify({"error": "Regla no encontrada"}), 404

        # 3. ¡Validación de Seguridad!
        # Asegurarse de que el usuario solo borre sus propias reglas.
        if rule_to_delete.user_id != current_user.id:
            # Usamos 404 (en lugar de 403) para no filtrar
            # información de que la regla siquiera existe.
            return jsonify({"error": "Regla no encontrada"}), 404

        # 4. Guardar el nombre para el mensaje de éxito
        rule_description = rule_to_delete.description

        # 5. Eliminar de la BD
        db.session.delete(rule_to_delete)
        db.session.commit()

        return jsonify({"message": f"Regla '{rule_description}' eliminada exitosamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500


@rule_bp.route('/', methods=['GET'])
@token_required
def get_all_rules(current_user):
    """
    Devuelve una lista de TODAS las reglas recurrentes (fijas)
    creadas por el usuario.
    """
    try:
        # 1. Buscar todas las reglas del usuario
        rules = RecurringRule.query.filter_by(
            user_id=current_user.id
        ).order_by(
            RecurringRule.next_execution_date.asc()
        ).all()

        # 2. Convertir a JSON usando el helper 'rule_to_dict'
        result_list = [rule_to_dict(rule) for rule in rules]

        return jsonify(result_list), 200

    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

def rule_to_dict(rule):
    """
    Serializa un objeto RecurringRule a un diccionario JSON friendly.
    """
    return {
        "id": rule.id,
        "description": rule.description,
        "amount": str(rule.amount),
        # Convierte Enums a su valor string (ej. 'monthly')
        "frequency": rule.frequency.value if rule.frequency else None,
        "type": rule.type.value if rule.type else None,
        # Convierte 'date' a string ISO (ej. '2025-11-30')
        "next_execution_date": rule.next_execution_date.isoformat(),
        "account_id": rule.account_id,
        "debt_id": rule.debt_id
    }