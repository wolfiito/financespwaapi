# En: api/transaction_routes.py

from flask import Blueprint, jsonify, request
from app import db
# ¡CAMBIO! Importamos TODOS los modelos y Enums que necesitamos
from models import Transaction, Account, AccountType, TransactionType
from datetime import datetime
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
    data = request.json
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

        # ¡CAMBIO! Leemos los campos del refactor
        installments = data.get('installments', 1) # Default 1 (pago normal)
        debt_id = data.get('debt_id', None) # Default null

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
        # ¡CAMBIO! Obtenemos el límite del query string, con 20 como default.
        limit = request.args.get('limit', 20, type=int)

        # ¡OPTIMIZACIÓN! Usamos un OUTERJOIN con Account para no fallar si una cuenta se borra.
        transactions = db.session.query(
            Transaction, 
            Account.name.label('account_name')
        ).outerjoin( # <--- LEFT JOIN
            Account, Transaction.account_id == Account.id
        ).filter(
            Transaction.user_id == current_user.id
        ).order_by(
            Transaction.date.desc()
        ).limit(limit).all() # Usamos el límite dinámico

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

        return jsonify(result), 200

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