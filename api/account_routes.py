# En: api/account_routes.py

from flask import Blueprint, jsonify, request
from app import db
from models import Account, Transaction, AccountType, TransactionType
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
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

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

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

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

    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500