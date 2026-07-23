from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from app import db
from api.security import token_required
from models import Account, RecurringRule, RecurringRuleType, FrequencyType
from services.recurring import process_due_rules


rule_bp = Blueprint('rule_bp', __name__, url_prefix='/api/rules')


def _parse_rule_values(data, current_user, rule=None):
    """Valida los campos mutables de una regla y los devuelve como dict."""
    values = {}
    if rule is None or 'type' in data:
        try:
            values['type'] = RecurringRuleType(data['type'])
        except (KeyError, ValueError):
            raise ValueError('Tipo de regla no válido o faltante.')
    if rule is None or 'frequency' in data:
        try:
            values['frequency'] = FrequencyType(data['frequency'])
        except (KeyError, ValueError):
            raise ValueError('Frecuencia no válida o faltante.')

    if rule is None or 'amount' in data:
        try:
            amount = Decimal(str(data['amount']))
        except (KeyError, ValueError, ArithmeticError):
            raise ValueError('Monto no válido o faltante.')
        rule_type = values.get('type', rule.type if rule else None)
        values['amount'] = -abs(amount) if rule_type == RecurringRuleType.EXPENSE else abs(amount)

    start_raw = data.get('start_date', data.get('first_execution_date'))
    if rule is None or start_raw is not None:
        try:
            values['start_date'] = date.fromisoformat(start_raw)
        except (TypeError, ValueError):
            raise ValueError('start_date debe tener formato YYYY-MM-DD.')

    if 'end_date' in data:
        try:
            values['end_date'] = date.fromisoformat(data['end_date']) if data['end_date'] else None
        except ValueError:
            raise ValueError('end_date debe tener formato YYYY-MM-DD o ser null.')
    elif rule is None:
        values['end_date'] = None

    start_date = values.get('start_date', rule.start_date)
    end_date = values.get('end_date', rule.end_date if rule else None)
    if end_date and end_date < start_date:
        raise ValueError('end_date no puede ser anterior a start_date.')

    if rule is None or 'account_id' in data:
        account_id = data.get('account_id')
        if account_id is not None:
            account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
            if not account:
                raise ValueError('La cuenta no existe o no pertenece al usuario.')
        values['account_id'] = account_id

    for field in ('description', 'category', 'is_active'):
        if rule is None and field == 'description' and field not in data:
            raise ValueError('description es obligatorio.')
        if field in data:
            values[field] = data[field]
    return values


@rule_bp.route('/new', methods=['POST'])
@token_required
def create_recurring_rule(current_user):
    """Crea una regla con inicio, fin opcional y una cuenta opcional."""
    data = request.get_json(silent=True) or {}
    try:
        values = _parse_rule_values(data, current_user)
        new_rule = RecurringRule(
            description=values['description'],
            amount=values['amount'],
            type=values['type'],
            frequency=values['frequency'],
            start_date=values['start_date'],
            end_date=values['end_date'],
            next_execution_date=values['start_date'],
            account_id=values.get('account_id'),
            category=values.get('category'),
            user_id=current_user.id,
            is_active=values.get('is_active', True),
        )
        db.session.add(new_rule)
        db.session.commit()
        return jsonify({'message': 'Regla recurrente creada exitosamente', 'rule_id': new_rule.id}), 201
    except ValueError as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudo crear la regla.'}), 500


@rule_bp.route('/process', methods=['POST'])
@token_required
def process_rules_for_current_user(current_user):
    """Ejecuta manualmente las reglas pendientes del usuario autenticado."""
    data = request.get_json(silent=True) or {}
    try:
        until = date.fromisoformat(data['until']) if data.get('until') else date.today()
    except ValueError:
        return jsonify({'error': 'until debe tener formato YYYY-MM-DD.'}), 400
    try:
        created = process_due_rules(until=until, user_id=current_user.id)
        return jsonify({'created_transactions': created, 'processed_until': until.isoformat()}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudieron procesar las reglas.'}), 500


@rule_bp.route('/<int:rule_id>', methods=['PATCH'])
@token_required
def update_recurring_rule(current_user, rule_id):
    rule = RecurringRule.query.filter_by(id=rule_id, user_id=current_user.id).first()
    if not rule:
        return jsonify({'error': 'Regla no encontrada'}), 404
    data = request.get_json(silent=True) or {}
    try:
        values = _parse_rule_values(data, current_user, rule)
        start_was_changed = 'start_date' in values and values['start_date'] != rule.start_date
        for field, value in values.items():
            setattr(rule, field, value)
        # No reescribimos ejecuciones pasadas. Si se cambia el inicio y aún no
        # se ha procesado nada, el calendario empieza desde la nueva fecha.
        if start_was_changed and rule.executions.count() == 0:
            rule.next_execution_date = rule.start_date
        db.session.commit()
        return jsonify(rule_to_dict(rule)), 200
    except ValueError as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudo actualizar la regla.'}), 500


@rule_bp.route('/<int:rule_id>', methods=['DELETE'])
@token_required
def delete_recurring_rule(current_user, rule_id):
    rule = RecurringRule.query.filter_by(id=rule_id, user_id=current_user.id).first()
    if not rule:
        return jsonify({'error': 'Regla no encontrada'}), 404
    if rule.executions.count():
        return jsonify({'error': 'La regla ya tiene ejecuciones; desactívala con PATCH en lugar de eliminarla.'}), 409
    db.session.delete(rule)
    db.session.commit()
    return jsonify({'message': 'Regla eliminada exitosamente'}), 200


@rule_bp.route('/', methods=['GET'])
@token_required
def get_all_rules(current_user):
    rules = RecurringRule.query.filter_by(user_id=current_user.id).order_by(
        RecurringRule.next_execution_date.asc()
    ).all()
    return jsonify([rule_to_dict(rule) for rule in rules]), 200


def rule_to_dict(rule):
    return {
        'id': rule.id,
        'description': rule.description,
        'amount': str(rule.amount),
        'frequency': rule.frequency.value,
        'type': rule.type.value,
        'start_date': rule.start_date.isoformat() if rule.start_date else None,
        'end_date': rule.end_date.isoformat() if rule.end_date else None,
        'next_execution_date': rule.next_execution_date.isoformat(),
        'account_id': rule.account_id,
        'category': rule.category,
        'debt_id': rule.debt_id,
        'is_active': rule.is_active,
    }
