import enum
from app import db, bcrypt
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.sql import func # ¡Importante! Para el server_default

# --- 1. DEFINICIÓN DE ENUMS (LA VERSIÓN CORRECTA) ---
class RecurringRuleType(enum.Enum):
    EXPENSE = 'expense'
    INCOME = 'income'

class FrequencyType(enum.Enum):
    DAILY = 'daily'
    WEEKLY = 'weekly'
    BI_WEEKLY = 'bi_weekly'
    MONTHLY = 'monthly'
    YEARLY = 'yearly'
    ONCE = 'once'

class AccountType(enum.Enum):
    CREDIT_CARD = 'credit_card'
    DEBIT_CARD = 'debit_card'
    CASH = 'cash'
    # Compatibilidad con cuentas creadas por la versión anterior de la API.
    # SQLAlchemy guarda el nombre SAVINGS en SQLite, por eso debe permanecer.
    SAVINGS = 'savings'

class TransactionType(enum.Enum):
    EXPENSE = 'expense'
    INCOME = 'income'
    INITIAL_BALANCE = 'initial_balance'
    DEBT_PAYMENT = 'debt_payment'

# --- MODELOS PRINCIPALES ---

class User(db.Model):
    # ... (Se queda igual que antes) ...
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    transactions = db.relationship('Transaction', backref='owner', lazy=True)
    recurring_rules = db.relationship('RecurringRule', backref='owner', lazy=True)
    debts = db.relationship('Debt', backref='owner', lazy=True)
    accounts = db.relationship('Account', backref='owner', lazy=True)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

# --- 2. MODELO ACCOUNT (Refactorizado) ---

class Account(db.Model):
    __tablename__ = 'account'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    # ¡CAMBIO! Usamos el Enum
    type = db.Column(db.Enum(AccountType), nullable=False)

    # Campos Específicos para Tarjetas de Crédito
    closing_date = db.Column(db.Integer, nullable=True) # Día del mes (ej: 5)
    payment_date = db.Column(db.Integer, nullable=True) # Día del mes (ej: 24)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    transactions = db.relationship('Transaction', backref='account', lazy=True)

# --- 3. MODELO TRANSACTION (Refactorizado) ---

class Transaction(db.Model):
    __tablename__ = 'transaction'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(precision=10, scale=2), nullable=False)

    # ¡CAMBIO! Zona horaria y default en el servidor
    date = db.Column(db.DateTime(timezone=True),
                     server_default=func.now(),
                     nullable=False)

    # ¡CAMBIO! Usamos el Enum
    type = db.Column(db.Enum(TransactionType), nullable=False)

    category = db.Column(db.String(50), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)

    # --- NUEVO CAMPO (Ajuste #2) ---
    # Para Meses Sin Intereses (MSI).
    # 1 = Pago normal (default)
    # 3, 6, 12, etc. = Compra a MSI
    installments = db.Column(db.Integer, nullable=False, default=1)

    # --- NUEVO CAMPO (Ajuste #1) ---
    # Vincula esta transacción al pago de una deuda
    debt_id = db.Column(db.Integer, db.ForeignKey('debt.id'), nullable=True)
    recurring_execution = db.relationship('RecurringExecution', backref='transaction', uselist=False)

# --- 4. MODELO DEBT (Refactorizado) ---

class Debt(db.Model):
    __tablename__ = 'debt'
    id = db.Column(db.Integer, primary_key=True)
    debt_name = db.Column(db.String(100), nullable=False)
    original_amount = db.Column(db.Numeric(precision=10, scale=2), nullable=False)
    monthly_payment_amount = db.Column(db.Numeric(precision=10, scale=2), nullable=False)
    term_months = db.Column(db.Integer, nullable=False)

    # --- CAMPO DEPRECADO ---
    # 'payments_made' ya no es la "fuente de verdad".
    # Lo comento para eliminarlo. Si tienes datos, usa una migración.
    # payments_made = db.Column(db.Integer, nullable=False, default=0)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    associated_rule = db.relationship('RecurringRule', backref='associated_debt', uselist=False)

    # --- NUEVA RELACIÓN (Ajuste #1) ---
    # Una deuda tiene muchas transacciones (pagos) asociadas.
    # 'lazy="dynamic"' nos permite hacer consultas sobre esta relación.
    payments = db.relationship('Transaction', backref='paid_debt', lazy='dynamic')

    # --- LÓGICA MEJORADA (Ajuste #1) ---
    @property
    def total_paid(self):
        # Suma el monto de todas las transacciones vinculadas a esta deuda.
        # Asume que los pagos (ej: TransactionType.DEBT_PAYMENT)
        # se registran con 'amount' positivo.
        paid = self.payments.with_entities(
            db.func.sum(Transaction.amount)
        ).scalar() or Decimal('0.00')
        return paid

    @property
    def remaining_amount(self):
        remaining = self.original_amount - self.total_paid
        return remaining if remaining > Decimal('0.00') else Decimal('0.00')

# ... El modelo RecurringRule se queda igual por ahora ...
class RecurringRule(db.Model):
    # ... (sin cambios) ...
    __tablename__ = 'recurring_rule'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(precision=10, scale=2), nullable=False)
    type = db.Column(db.Enum(RecurringRuleType), nullable=False)
    frequency = db.Column(db.Enum(FrequencyType), nullable=False)
    next_execution_date = db.Column(db.Date, nullable=False)
    # Fecha de inicio del calendario y fecha final opcional.  La próxima
    # ejecución se conserva para poder procesar una regla de forma incremental.
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    category = db.Column(db.String(50), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    debt_id = db.Column(db.Integer, db.ForeignKey('debt.id'), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)
    account = db.relationship('Account', backref='recurring_rules')


class RecurringExecution(db.Model):
    """Una ocurrencia ya materializada de una regla recurrente.

    La restricción única hace idempotente al proceso diario: aunque se ejecute
    dos veces, una misma regla no puede crear dos movimientos para la misma
    fecha programada.
    """
    __tablename__ = 'recurring_execution'
    __table_args__ = (
        db.UniqueConstraint('rule_id', 'scheduled_date', name='uq_rule_scheduled_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('recurring_rule.id'), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=False)
    scheduled_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    rule = db.relationship('RecurringRule', backref=db.backref('executions', lazy='dynamic'))
