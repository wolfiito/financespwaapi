from datetime import date, datetime, time

from dateutil.relativedelta import relativedelta

from app import db
from models import (
    Account,
    AccountType,
    RecurringExecution,
    RecurringRule,
    RecurringRuleType,
    Transaction,
    TransactionType,
)


def next_execution_date(current_date, frequency):
    """Devuelve la siguiente fecha o ``None`` para una regla de una sola vez."""
    value = frequency.value if hasattr(frequency, 'value') else frequency
    if value == 'daily':
        return current_date + relativedelta(days=1)
    if value == 'weekly':
        return current_date + relativedelta(weeks=1)
    if value == 'bi_weekly':
        return current_date + relativedelta(weeks=2)
    if value == 'monthly':
        return current_date + relativedelta(months=1)
    if value == 'yearly':
        return current_date + relativedelta(years=1)
    if value == 'once':
        return None
    raise ValueError(f'Frecuencia no soportada: {value}')


def _main_cash_account(user_id):
    account = Account.query.filter(
        Account.user_id == user_id,
        Account.type.in_([AccountType.CASH, AccountType.DEBIT_CARD]),
    ).order_by(Account.id).first()
    if account:
        return account

    account = Account(name='Efectivo', type=AccountType.CASH, user_id=user_id)
    db.session.add(account)
    db.session.flush()
    return account


def process_due_rules(until=None, user_id=None):
    """Materializa todas las ocurrencias pendientes hasta ``until``.

    Esta función está pensada para ser ejecutada diariamente desde una tarea
    programada. Puede recibir ``user_id`` para el endpoint manual de un usuario.
    Devuelve cuántas transacciones nuevas creó.
    """
    until = until or date.today()
    query = RecurringRule.query.filter(
        RecurringRule.is_active.is_(True),
        RecurringRule.next_execution_date <= until,
    )
    if user_id is not None:
        query = query.filter(RecurringRule.user_id == user_id)

    created = 0
    for rule in query.order_by(RecurringRule.next_execution_date, RecurringRule.id).all():
        scheduled = rule.next_execution_date
        while scheduled and scheduled <= until:
            if rule.end_date and scheduled > rule.end_date:
                rule.is_active = False
                break

            already_done = RecurringExecution.query.filter_by(
                rule_id=rule.id,
                scheduled_date=scheduled,
            ).first()
            if not already_done:
                account = rule.account or _main_cash_account(rule.user_id)
                transaction_type = (
                    TransactionType.EXPENSE
                    if rule.type == RecurringRuleType.EXPENSE
                    else TransactionType.INCOME
                )
                transaction = Transaction(
                    description=rule.description,
                    amount=rule.amount,
                    type=transaction_type,
                    category=rule.category,
                    user_id=rule.user_id,
                    account_id=account.id,
                    date=datetime.combine(scheduled, time.min),
                    debt_id=rule.debt_id,
                    installments=1,
                )
                db.session.add(transaction)
                db.session.flush()
                db.session.add(RecurringExecution(
                    rule_id=rule.id,
                    transaction_id=transaction.id,
                    scheduled_date=scheduled,
                ))
                created += 1

            scheduled = next_execution_date(scheduled, rule.frequency)
            if scheduled is None:
                rule.is_active = False
                break
            rule.next_execution_date = scheduled
            if rule.end_date and scheduled > rule.end_date:
                rule.is_active = False
                break

    db.session.commit()
    return created
