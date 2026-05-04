#!/usr/bin/env python3
\"\"\"Teste completo fluxo pagamento - sala\"\"\"

import json
import os
from unittest.mock import Mock, patch
from imap_optimizer import imap_manager
from bot_logic import _buscar_pagamento_otimizado, log_msg, _criar_sala_api
from models import BotConfig

def test_pagamento_completo():
    user_id = 999  # Mock user
    config = {
        'email_user': 'test@gmail.com',
        'email_pass': 'app_pass',
        'imap_server': 'imap.gmail.com',
    }
    
    print("🧪 Teste 1: Busca pagamento mock")
    # Mock pagamento encontrado
    with patch('imap_optimizer.imap_manager.get_cache') as mock_cache:
        cache_mock = Mock()
        cache_mock.search_payment_optimized.return_value = {'uid': 123, 'valor': 50.0, 'banco': 'Nubank'}
        mock_cache.return_value = cache_mock
        
        resultado = _buscar_pagamento_otimizado(config, "João Silva", user_id)
        print(f"Resultado: {resultado}")
        assert resultado is not None
    
    print("✅ Teste 1 OK")
    
    print("🧪 Teste 2: API sala mock")
    # Mock API sala
    with patch('aiohttp.ClientSession') as mock_session:
        # Simula resposta sala criada
        mock_resp = Mock()
        mock_resp.json.return_value = {'success': True, 'status': 3, 'sala': {'id': '123456', 'senha': '22'}}
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        
        # Test _criar_sala_api (precisa asyncio mas mock simples)
        print("API mock configurado - execute manual bot para teste completo")
    
    print("🚀 Teste local: python app.py & cliente_app.py")
    print("1. Config → salve modo_sala_id")
    print("2. Test /api_modos → veja lista modos")
    print("3. Inicie bot → pg teste → verifique modo usado nos logs")

if __name__ == '__main__':
    test_pagamento_completo()
    print("\\n🎉 Testes unitários OK! Execute fluxos manuais.")
