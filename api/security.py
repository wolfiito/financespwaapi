# En: api/security.py

from functools import wraps
from flask import request, jsonify
import jwt
from app import app # Para el SECRET_KEY
from models import User

def token_required(f):
    """
    Decorador que verifica el token JWT.
    El token se espera en la cabecera 'x-access-token'.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # 1. Buscar el token en los headers de la petición
        if 'x-access-token' in request.headers:
            token = request.headers['x-access-token']
            
        if not token:
            # Si no hay token, 401 Unauthorized
            return jsonify({'error': 'Token no encontrado'}), 401

        # 2. Decodificar el token
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            
            # 3. Encontrar al usuario basado en el ID del token
            current_user = User.query.get(data['user_id'])
            
            if not current_user:
                 return jsonify({'error': 'Usuario del token no válido'}), 401
                 
        except jwt.ExpiredSignatureError:
            # El token es válido pero ya expiró
            return jsonify({'error': 'Token ha expirado'}), 401
        except jwt.InvalidTokenError:
            # El token no es válido (firma incorrecta, etc.)
            return jsonify({'error': 'Token inválido'}), 401

        # 4. Pasar el usuario a la ruta
        # Este es el 'truco': la ruta (ej. create_debt) 
        # ahora recibirá 'current_user' como primer argumento.
        return f(current_user, *args, **kwargs)
    
    return decorated
