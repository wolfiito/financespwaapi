"""Migración mínima y segura para la base SQLite existente.

Ejecutar una vez antes de desplegar la nueva versión:
    python migrate_db.py
"""
from sqlalchemy import inspect, text

from app import app, db


def add_column_if_missing(connection, table, column, ddl):
    columns = {item['name'] for item in inspect(connection).get_columns(table)}
    if column not in columns:
        connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {ddl}'))


with app.app_context():
    # Crea recurring_execution sin modificar tablas existentes.
    db.create_all()
    with db.engine.begin() as connection:
        add_column_if_missing(connection, 'recurring_rule', 'start_date', 'start_date DATE')
        add_column_if_missing(connection, 'recurring_rule', 'end_date', 'end_date DATE')
        add_column_if_missing(connection, 'recurring_rule', 'is_active', 'is_active BOOLEAN NOT NULL DEFAULT 1')
        add_column_if_missing(connection, 'recurring_rule', 'category', 'category VARCHAR(50)')
        # Las reglas existentes comienzan en su próxima fecha conocida.
        connection.execute(text(
            'UPDATE recurring_rule SET start_date = next_execution_date '
            'WHERE start_date IS NULL'
        ))

print('Migración completada.')
