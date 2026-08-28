# En: api/account_routes.py

from flask import Blueprint, jsonify, request, current_app
from app import db
from models import Account, Transaction, AccountType, TransactionType, RecurringRule
from api.security import token_required
from datetime import datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from sqlalchemy import and_

account_bp = Blueprint('account_bp', __name__, url_prefix='/api/accounts')

@account_bp.route('/new', methods=['POST'])
@token_required
def create_account(current_user):
    """
    La "Cuenta Maestra" se crea automáticamente.
    """
    data = request.json

    try:
        type_str = data['type']
        try:
            account_type_enum = AccountType(type_str)
        except ValueError:
            # 3. Si el string no es válido (ej. "tarjeta"), fallamos
            return jsonify({"error": f"Tipo de cuenta no válido: {type_str}"}), 400

        new_account = Account(
            name=data['name'],
            type=account_type_enum,
            user_id=current_user.id,
            closing_date=data.get('closing_date'),
            payment_date=data.get('payment_date')
        )

        db.session.add(new_account)
        db.session.commit()

        return jsonify({
            "message": "Cuenta de Crédito creada exitosamente",
            "account_id": new_account.id
        }), 201

    except KeyError as e:
        db.session.rollback()
        return jsonify({"error": f"Dato faltante: {str(e)}"}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Fallo en create_account')
        return jsonify({'error': 'Error interno del servidor'}), 500

@account_bp.route('/summary', methods=['GET'])
@token_required
def get_account_summary(current_user):
    """
    Calcula el SALDO ACTUAL de cada cuenta del usuario.
    """
    try:
        # 1. Obtener balances
        balances_query = db.session.query(
            Transaction.account_id,
            db.func.sum(Transaction.amount).label('total_balance')
        ).filter(
            Transaction.user_id == current_user.id
        ).group_by(
            Transaction.account_id
        ).all()

        # 2. Convertimos a un diccionario para acceso rápido
        balances_dict = {
            acct_id: balance for acct_id, balance in balances_query
        }

        accounts = Account.query.filter_by(user_id=current_user.id).all()
        summary_list = []

        for account in accounts:
            # 4. Buscamos el balance en el diccionario
            current_balance = balances_dict.get(account.id, Decimal('0.00'))

            # --- ¡AQUÍ ESTABA EL ERROR! ---
            # Esta línea ahora está indentada 8 espacios para
            # coincidir con la línea 'current_balance' de arriba.
            summary_list.append({
                "account_id": account.id,
                "account_name": account.name,
                "account_type": account.type.value,
                "current_balance": str(current_balance)
            })

        return jsonify(summary_list), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Fallo en get_account_summary')
        return jsonify({'error': 'Error interno del servidor'}), 500

@account_bp.route('/<int:account_id>/transactions', methods=['GET'])
@token_required
def get_account_transactions(current_user, account_id):
    """
    Devuelve todas las transacciones de una cuenta específica,
    asegurándose de que le pertenezca al usuario.
    """
    try:
        # 1. Verificar que la cuenta le pertenece al usuario
        account = Account.query.filter_by(
            id=account_id,
            user_id=current_user.id
        ).first()

        if not account:
            # Si no se encuentra, o no es del usuario, da error
            return jsonify({"error": "Cuenta no encontrada o no autorizada"}), 404

        # 2. Buscar las transacciones de esa cuenta
        transactions = Transaction.query.filter_by(
            account_id=account_id,
            user_id=current_user.id
        ).order_by(
            Transaction.date.desc()
        ).all()

        # 3. Formatear la respuesta
        result = [
            {
                "id": t.id,
                "description": t.description,
                "amount": str(t.amount),
                "date": t.date.isoformat(),
                "category": t.category,

                "type": t.type.value, # 'expense', 'income', etc.
                "installments": t.installments, # 1, 6, 12, etc.
                "debt_id": t.debt_id # null o el ID de la deuda
            }
            for t in transactions
        ]

        # 4. Devolver la lista Y el nombre de la cuenta
        return jsonify({
            "account_name": account.name,
            "transactions": result
        }), 200

    except Exception:
        current_app.logger.exception('Fallo en get_account_transactions')
        return jsonify({'error': 'Error interno del servidor'}), 500


@account_bp.route('/<int:account_id>', methods=['PATCH'])
@token_required
def update_account(current_user, account_id):
    """Edita una cuenta propia sin alterar sus movimientos históricos."""
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not account:
        return jsonify({'error': 'Cuenta no encontrada'}), 404
    data = request.get_json(silent=True) or {}
    try:
        if 'name' in data:
            if not isinstance(data['name'], str) or not data['name'].strip():
                return jsonify({'error': 'name no puede estar vacío'}), 400
            account.name = data['name'].strip()
        if 'type' in data:
            account.type = AccountType(data['type'])
        for field in ('closing_date', 'payment_date'):
            if field in data:
                value = data[field]
                if value is not None and (not isinstance(value, int) or not 1 <= value <= 31):
                    return jsonify({'error': f'{field} debe ser un día entre 1 y 31'}), 400
                setattr(account, field, value)
        db.session.commit()
        return jsonify(account_to_dict(account)), 200
    except ValueError:
        db.session.rollback()
        return jsonify({'error': 'Tipo de cuenta no válido'}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Fallo en update_account')
        return jsonify({'error': 'No se pudo actualizar la cuenta'}), 500


@account_bp.route('/<int:account_id>', methods=['DELETE'])
@token_required
def delete_account(current_user, account_id):
    """Elimina una cuenta vacía; conserva la integridad del historial."""
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not account:
        return jsonify({'error': 'Cuenta no encontrada'}), 404

    try:
        # El historial no desaparece con la cuenta: los movimientos y las reglas
        # se conservan y solo quedan sin cuenta asignada. Dejan de sumar en el
        # saldo por cuenta, pero siguen en la bitácora y en los totales.
        detached_transactions = Transaction.query.filter(
            Transaction.account_id == account.id,
            Transaction.user_id == current_user.id,
        ).update({'account_id': None}, synchronize_session=False)
        detached_rules = RecurringRule.query.filter(
            RecurringRule.account_id == account.id,
            RecurringRule.user_id == current_user.id,
        ).update({'account_id': None}, synchronize_session=False)

        # Las colecciones cargadas quedaron obsoletas tras el UPDATE masivo.
        db.session.expire(account)
        db.session.delete(account)
        db.session.commit()
        return jsonify({
            'message': 'Cuenta eliminada exitosamente',
            'detached_transactions': detached_transactions,
            'detached_rules': detached_rules,
        }), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Fallo en delete_account')
        return jsonify({'error': 'No se pudo eliminar la cuenta.'}), 500


def account_to_dict(account):
    return {
        'id': account.id,
        'name': account.name,
        'type': account.type.value,
        'closing_date': account.closing_date,
        'payment_date': account.payment_date,
    }
