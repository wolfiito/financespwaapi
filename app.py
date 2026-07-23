# En: app.py

import os
import click
from datetime import date
from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from decimal import Decimal
from datetime import datetime

# --- Configuración de la App ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)

CORS(app)
# --- FIN DE LA CORRECCIÓN ---

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'finanzas.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'esta-es-mi-clave-secreta-para-jwt-y-debe-ser-larga'

# --- Inicialización de Extensiones ---
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# --- Importación de Modelos ---
# (Importamos DESPUÉS de crear 'db' y 'bcrypt')
from models import User, Transaction, RecurringRule, Debt, Account

# --- REGISTRO DE BLUEPRINTS ---
from api.debt_routes import debt_bp
app.register_blueprint(debt_bp)

from api.rule_routes import rule_bp
app.register_blueprint(rule_bp)

from api.projection_routes import projection_bp
app.register_blueprint(projection_bp)

from api.transaction_routes import transaction_bp
app.register_blueprint(transaction_bp)

from api.auth_routes import auth_bp
app.register_blueprint(auth_bp)

from api.account_routes import account_bp
app.register_blueprint(account_bp)

from api.summary_routes import summary_bp
app.register_blueprint(summary_bp)


@app.cli.command('process-rules')
@click.option('--until', 'until_value', default=None, help='Fecha límite YYYY-MM-DD; por defecto, hoy.')
def process_rules_command(until_value):
    """Materializa las reglas recurrentes pendientes sin duplicarlas."""
    from services.recurring import process_due_rules

    try:
        until = date.fromisoformat(until_value) if until_value else date.today()
    except ValueError as error:
        raise click.BadParameter('Debe tener formato YYYY-MM-DD.') from error
    created = process_due_rules(until=until)
    click.echo(f'{created} movimiento(s) recurrente(s) creado(s) hasta {until.isoformat()}.')

# --- Rutas de Prueba ---
@app.route('/')
def index():
    """ Una ruta de prueba para verificar que el servidor funciona. """
    return jsonify({"message": "Finance backend is up and running!"})

# Esta línea es para ejecutar la app localmente
if __name__ == '__main__':
    app.run(debug=True)
