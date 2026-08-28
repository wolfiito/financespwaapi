# En: api/transaction_routes.py

from flask import Blueprint, jsonify, request
from app import db
# ¡CAMBIO! Importamos TODOS los modelos y Enums que necesitamos
from models import Transaction, Account, Debt, AccountType, TransactionType
from datetime import date, datetime
from decimal import Decimal
from api.security import token_required
# ¡CAMBIO! Importamos 'func' para la lógica de fechas
from sqlalchemy import or_, func

transaction_bp = Blueprint('transaction_bp', __name__, url_prefix='/api/transactions')

# --- 1. FUNCIÓN HELPER (Corregida) ---
def get_or_create_main_account(user_id):
    """
    Busca la cuenta principal (de efectivo/débito) del usuario. 
    Si no existe, la prepara para ser creada.
    
    ¡CAMBIO! Esta función ya no hace 'commit'.
    ¡CAMBIO! Usa Enums (AccountType.CASH) en lugar de 'main_account'.
    """
    
    # Buscamos una cuenta que sea de tipo CASH o DEBIT_CARD con un nombre específico.
    # Usaremos CASH como el default para "dinero real".
    main_account = Account.query.filter_by(
        user_id=user_id,
        type=AccountType.CASH 
        # Podríamos ser más específicos y buscar por name="Cuenta Maestra"
        # pero por ahora, asumimos que la primera cuenta CASH es la principal.
    ).first()

    if not main_account:
        main_account = Account(
            name="Efectivo", # Un nombre más genérico
            type=AccountType.CASH, # ¡Usamos el Enum!
            user_id=user_id
        )
        db.session.add(main_account)
        # ¡IMPORTANTE! No hacemos commit. El endpoint que llama se encarga.
        # Esto mantiene la transacción atómica.
        
        # Necesitamos que tenga un ID para la transacción, 
        # así que "flusheamos" la sesión.
        db.session.flush() 

    return main_account

# --- 2. ENDPOINT 'CREATE' (Refactorizado) ---
@transaction_bp.route('/new', methods=['POST'])
@token_required
def create_transaction(current_user):
    """
    Registra una transacción (gasto, ingreso, MSI, o pago de deuda).
    ¡Este es el endpoint más importante!
    """
    data = request.get_json(silent=True) or {}
    try:
        amount = Decimal(data['amount'])
        
        # ¡CAMBIO! Obtenemos el Enum desde el string que envía el frontend
        # ej: "expense" -> TransactionType.EXPENSE
        try:
            trans_type_str = data['type']
            trans_type = TransactionType(trans_type_str)
        except ValueError:
            return jsonify({"error": f"Tipo de transacción no válido: {trans_type_str}"}), 400

        # Lógica de montos (Gasto/Pago de Deuda es negativo)
        if (trans_type == TransactionType.EXPENSE or trans_type == TransactionType.DEBT_PAYMENT) and amount > 0:
            amount = amount * -1
        # (Ingreso/Saldo Inicial es positivo)
        elif (trans_type == TransactionType.INCOME or trans_type == TransactionType.INITIAL_BALANCE) and amount < 0:
            amount = amount * -1 # Aseguramos positivo

        account_id = data.get('account_id')
        if not account_id:
            # Si no hay cuenta, es dinero real. Usamos la "Cuenta Maestra" (CASH)
            main_account = get_or_create_main_account(current_user.id)
            account_id = main_account.id
        else:
            account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
            if not account:
                return jsonify({'error': 'La cuenta no existe o no pertenece al usuario'}), 400

        # ¡CAMBIO! Leemos los campos del refactor
        installments = data.get('installments', 1) # Default 1 (pago normal)
        if not isinstance(installments, int) or installments < 1:
            return jsonify({'error': 'installments debe ser un entero mayor o igual a 1'}), 400
        debt_id = data.get('debt_id', None) # Default null
        if debt_id is not None and not Debt.query.filter_by(id=debt_id, user_id=current_user.id).first():
            return jsonify({'error': 'La deuda no existe o no pertenece al usuario'}), 400

        new_trans = Transaction(
            description=data['description'],
            amount=amount,
            type=trans_type, # ¡Usamos el Enum!
            category=data.get('category'),
            user_id=current_user.id,
            account_id=account_id,
            installments=installments, # ¡Nuevo campo!
            debt_id=debt_id              # ¡Nuevo campo!
        )

        # ¡CAMBIO! Manejo de fecha
        # Si el usuario envía una fecha, la usamos.
        # Si no, dejamos que la BD use 'server_default=func.now()'
        transaction_date_str = data.get('date')
        if transaction_date_str:
            new_trans.date = datetime.fromisoformat(transaction_date_str)

        db.session.add(new_trans)
        db.session.commit() # ¡Commit atómico aquí!

        return jsonify({
            "message": "Transacción creada exitosamente",
            "transaction_id": new_trans.id
        }), 201

    except KeyError as e:
        db.session.rollback()
        return jsonify({"error": f"Dato faltante: {str(e)}"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- 3. ENDPOINT 'GET ALL' (Refactorizado y Optimizado) ---
@transaction_bp.route('', methods=['GET'])
@token_required
def get_transactions(current_user):
    """
    Devuelve las transacciones más recientes.
    ¡CAMBIO! Optimizado con LEFT JOIN para evitar problemas con cuentas borradas.
    ¡CAMBIO! Devuelve todos los campos nuevos y maneja valores nulos.
    ¡CAMBIO! El límite es configurable vía query param.
    """
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        if page < 1 or not 1 <= per_page <= 100:
            return jsonify({'error': 'page debe ser >= 1 y per_page debe estar entre 1 y 100'}), 400

        # ¡OPTIMIZACIÓN! Usamos un OUTERJOIN con Account para no fallar si una cuenta se borra.
        query = db.session.query(
            Transaction, 
            Account.name.label('account_name')
        ).outerjoin( # <--- LEFT JOIN
            Account, Transaction.account_id == Account.id
        ).filter(Transaction.user_id == current_user.id)

        if request.args.get('account_id'):
            query = query.filter(Transaction.account_id == request.args.get('account_id', type=int))
        if request.args.get('category'):
            query = query.filter(Transaction.category == request.args['category'])
        if request.args.get('type'):
            try:
                query = query.filter(Transaction.type == TransactionType(request.args['type']))
            except ValueError:
                return jsonify({'error': 'type no es válido'}), 400
        try:
            if request.args.get('date_from'):
                query = query.filter(func.date(Transaction.date) >= date.fromisoformat(request.args['date_from']))
            if request.args.get('date_to'):
                query = query.filter(func.date(Transaction.date) <= date.fromisoformat(request.args['date_to']))
        except ValueError:
            return jsonify({'error': 'date_from y date_to deben tener formato YYYY-MM-DD'}), 400

        total = query.count()
        transactions = query.order_by(Transaction.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

        result = []
        for t, account_name in transactions: # Desempaquetamos la tupla
            result.append({
                "id": t.id,
                "description": t.description,
                "amount": str(t.amount),
                "date": t.date.isoformat(),
                "category": t.category,
                "account_id": t.account_id,
                
                # ¡CAMBIO! Si el nombre de la cuenta es Nulo (por el outerjoin), ponemos un placeholder.
                "account_name": account_name if account_name else "Cuenta Eliminada", 
                
                # ¡CAMBIO! Devolvemos los campos del refactor, con protección contra nulos.
                # Si 'type' es nulo en la BD, esto evita que el backend crashee.
                "type": t.type.value if t.type else None, 
                "installments": t.installments,
                "debt_id": t.debt_id
            })

        return jsonify({
            'items': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_items': total,
                'total_pages': (total + per_page - 1) // per_page,
            }
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- 4. ENDPOINT 'SET INITIAL' (Refactorizado y No Destructivo) ---
@transaction_bp.route('/set_initial', methods=['POST'])
@token_required
def set_initial_balance(current_user):
    """
    Establece un saldo inicial en la cuenta principal de Efectivo.
    ¡CAMBIO! Esta operación YA NO BORRA NADA. Es aditiva.
    """
    data = request.json
    try:
        initial_amount = Decimal(data['amount'])
        if initial_amount < 0:
             return jsonify({"error": "El saldo inicial no puede ser negativo"}), 400

        # 1. Obtener (o crear) la Cuenta Maestra (Efectivo)
        main_account = get_or_create_main_account(current_user.id)

        # 2. Crear la transacción de saldo inicial
        initial_trans = Transaction(
            description="Saldo Inicial",
            amount=initial_amount,
            type=TransactionType.INITIAL_BALANCE, # ¡Usamos Enum!
            category="balance",
            user_id=current_user.id,
            account_id=main_account.id
        )
        
        # Permitir al usuario fijar la fecha del saldo inicial
        transaction_date_str = data.get('date')
        if transaction_date_str:
            initial_trans.date = datetime.fromisoformat(transaction_date_str)

        db.session.add(initial_trans)
        db.session.commit()

        return jsonify({
            "message": "Saldo inicial establecido exitosamente",
            "transaction_id": initial_trans.id,
            "account_id": main_account.id
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

# --- 5. ENDPOINT 'GET BALANCE' (Corregido) ---
@transaction_bp.route('/balance', methods=['GET'])
@token_required
def get_current_balance(current_user):
    """
    Devuelve el saldo actual "real" del usuario (solo de cuentas CASH/DEBIT).
    ¡CAMBIO! Ya no busca 'main_account', busca tipos de cuenta reales.
    """
    try:
        # Sumamos todas las transacciones de cuentas que son "dinero real"
        # (Efectivo o Débito), no de crédito.
        current_balance = db.session.query(
            db.func.sum(Transaction.amount)
        ).join(
            Account, Transaction.account_id == Account.id
        ).filter(
            Transaction.user_id == current_user.id,
            # ¡LÓGICA CORREGIDA!
            Account.type.in_([AccountType.CASH, AccountType.DEBIT_CARD])
        ).scalar() or Decimal('0.00')

        return jsonify({
            "current_balance": str(current_balance),
            "user_id": current_user.id
        }), 200

    except Exception as e:
        return jsonify({"error": f"Error interno: {str(e)}"}), 500


@transaction_bp.route('/<int:transaction_id>', methods=['PATCH'])
@token_required
def update_transaction(current_user, transaction_id):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=current_user.id).first()
    if not transaction:
        return jsonify({'error': 'Transacción no encontrada'}), 404
    data = request.get_json(silent=True) or {}
    try:
        if 'description' in data:
            if not isinstance(data['description'], str) or not data['description'].strip():
                return jsonify({'error': 'description no puede estar vacío'}), 400
            transaction.description = data['description'].strip()
        if 'type' in data:
            transaction.type = TransactionType(data['type'])
        if 'amount' in data:
            amount = Decimal(str(data['amount']))
            if transaction.type in (TransactionType.EXPENSE, TransactionType.DEBT_PAYMENT):
                amount = -abs(amount)
            else:
                amount = abs(amount)
            transaction.amount = amount
        if 'category' in data:
            transaction.category = data['category']
        if 'installments' in data:
            if not isinstance(data['installments'], int) or data['installments'] < 1:
                return jsonify({'error': 'installments debe ser un entero mayor o igual a 1'}), 400
            transaction.installments = data['installments']
        if 'account_id' in data:
            account = Account.query.filter_by(id=data['account_id'], user_id=current_user.id).first()
            if not account:
                return jsonify({'error': 'La cuenta no existe o no pertenece al usuario'}), 400
            transaction.account_id = account.id
        if 'debt_id' in data:
            debt_id = data['debt_id']
            if debt_id is not None and not Debt.query.filter_by(id=debt_id, user_id=current_user.id).first():
                return jsonify({'error': 'La deuda no existe o no pertenece al usuario'}), 400
            transaction.debt_id = debt_id
        if 'date' in data:
            transaction.date = datetime.fromisoformat(data['date'])
        if 'type' in data and 'amount' not in data:
            if transaction.type in (TransactionType.EXPENSE, TransactionType.DEBT_PAYMENT):
                transaction.amount = -abs(transaction.amount)
            else:
                transaction.amount = abs(transaction.amount)
        db.session.commit()
        return jsonify({'message': 'Transacción actualizada exitosamente', 'transaction_id': transaction.id}), 200
    except (ValueError, ArithmeticError):
        db.session.rollback()
        return jsonify({'error': 'Datos de transacción no válidos'}), 400
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudo actualizar la transacción'}), 500


@transaction_bp.route('/<int:transaction_id>', methods=['DELETE'])
@token_required
def delete_transaction(current_user, transaction_id):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=current_user.id).first()
    if not transaction:
        return jsonify({'error': 'Transacción no encontrada'}), 404

    try:
        # Si el movimiento lo generó una regla, se borra también su registro de
        # ejecución. La regla sigue viva y su calendario no retrocede, así que
        # el proceso diario no vuelve a crear este movimiento.
        execution = transaction.recurring_execution
        if execution:
            db.session.delete(execution)
            db.session.flush()

        db.session.delete(transaction)
        db.session.commit()
        return jsonify({'message': 'Transacción eliminada exitosamente'}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'No se pudo eliminar la transacción.'}), 500
