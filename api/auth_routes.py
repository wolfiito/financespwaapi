# En: api/auth_routes.py

from flask import Blueprint, jsonify, request, current_app
from app import db, bcrypt, app # Importamos 'app' para el SECRET_KEY
from models import User
import jwt # La librería PyJWT que instalamos
from datetime import datetime, timedelta, timezone

# 1. Creación del Blueprint
auth_bp = Blueprint('auth_bp', __name__, url_prefix='/api/auth')

@auth_bp.route('/register', methods=['POST'])
def register_user():
    """
    Endpoint para registrar un nuevo usuario.
    Espera JSON: {"username": "...", "password": "..."}
    """
    data = request.json

    try:
        # 1. Verificar si el usuario ya existe
        existing_user = User.query.filter_by(username=data['username']).first()
        if existing_user:
            return jsonify({"error": "El nombre de usuario ya existe"}), 409 # 409 Conflict

        # 2. Crear el nuevo usuario
        new_user = User(username=data['username'])
        new_user.set_password(data['password']) # Usamos el método del modelo

        # 3. Guardar en la DB
        db.session.add(new_user)
        db.session.commit()

        return jsonify({
            "message": f"Usuario '{new_user.username}' creado exitosamente"
        }), 201 # 201 Created

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Fallo en register_user')
        return jsonify({'error': 'Error interno del servidor'}), 400

@auth_bp.route('/login', methods=['POST'])
def login_user():
    """
    Endpoint para iniciar sesión.
    Espera JSON: {"username": "...", "password": "..."}
    Devuelve un token JWT si es exitoso.
    """
    data = request.json

    try:
        # 1. Buscar al usuario
        user = User.query.filter_by(username=data['username']).first()

        # 2. Verificar usuario y contraseña
        # user.check_password() usa bcrypt para comparar
        if not user or not user.check_password(data['password']):
            return jsonify({"error": "Credenciales inválidas"}), 401 # 401 Unauthorized

        # --- Lección (Pair Programming) ---
        # 3. Crear el Token JWT (JSON Web Token)
        # El 'payload' son los datos que guardamos DENTRO del token.
        # 'exp' es la fecha de expiración.
        payload = {
            'user_id': user.id,
            'username': user.username,
            'exp': datetime.now(timezone.utc) + timedelta(days=1)
        }

        # 'jwt.encode' crea el token.
        # Se firma con la SECRET_KEY de tu app.py.
        token = jwt.encode(
            payload,
            app.config['SECRET_KEY'],
            algorithm="HS256" # Algoritmo estándar
        )

        return jsonify({
            "message": "Inicio de sesión exitoso",
            "token": token
        }), 200 # 200 OK

    except Exception:
        current_app.logger.exception('Fallo en login_user')
        return jsonify({'error': 'Error interno del servidor'}), 500