import re
import base64
import json
from datetime import datetime

def validate_discord_token(token: str) -> dict:
    """
    Valida um token do Discord e retorna informações sobre ele
    """
    if not token or not isinstance(token, str):
        return {"valid": False, "error": "Token vazio ou inválido"}
    
    token = token.strip()
    
    # Verifica formato básico
    if len(token) < 50:
        return {"valid": False, "error": "Token muito curto"}
    
    # Verifica se contém apenas caracteres válidos
    if not re.match(r'^[A-Za-z0-9._-]+$', token):
        return {"valid": False, "error": "Token contém caracteres inválidos"}
    
    try:
        # Tenta decodificar a primeira parte (user ID)
        parts = token.split('.')
        if len(parts) < 2:
            return {"valid": False, "error": "Formato de token inválido"}
        
        # Decodifica o user ID
        user_id_encoded = parts[0]
        # Adiciona padding se necessário
        padding = 4 - (len(user_id_encoded) % 4)
        if padding != 4:
            user_id_encoded += '=' * padding
        
        user_id_bytes = base64.b64decode(user_id_encoded)
        user_id = int.from_bytes(user_id_bytes, 'big')
        
        # Verifica se o user ID é válido (Discord IDs são snowflakes)
        if user_id < 1000000000000000000:  # Primeiro snowflake válido
            return {"valid": False, "error": "User ID inválido no token"}
        
        return {
            "valid": True, 
            "user_id": user_id,
            "format": "válido"
        }
        
    except Exception as e:
        return {"valid": False, "error": f"Erro ao validar token: {str(e)}"}

def check_token_expiry(token: str) -> dict:
    """
    Verifica se o token pode estar expirado baseado em padrões
    """
    validation = validate_discord_token(token)
    if not validation["valid"]:
        return validation
    
    # Tokens de bot geralmente não expiram
    # Tokens de usuário podem expirar
    if token.startswith('Bot '):
        return {"expired": False, "type": "bot_token"}
    else:
        return {"expired": "unknown", "type": "user_token", "note": "Tokens de usuário podem expirar"}