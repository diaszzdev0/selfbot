# TODO - Fix Limite de Salas (0/0 bug) ✅ COMPLETO

## Problem
User with 50 rooms gets "Limite: 0/0" error because:
1. Default limite_salas is 0 instead of reasonable default (10)
2. !normal and !infinito commands don't check limits
3. New users created via web get limite_salas=0

## Fixes Applied (ALL COMPLETE)
- [x] 1. Fix _get_salas_info() default limit (0 → 10) - bot_logic.py
- [x] 2. Fix _incrementar_sala() default limit (handled in _get_salas_info) - bot_logic.py
- [x] 3. Add limit check to !normal and !infinito commands - bot_logic.py  
- [x] 4. Fix admin endpoint default (0 → 10) - app.py
- [x] 5. Fix cliente_app.py BotStatus creation (limite_salas=0 → 10)

## Files Modified
- bot_logic.py: Added _DEFAULT_LIMITE_SALAS=10, _get_salas_info() uses default
- cliente_app.py: Added _DEFAULT_LIMITE_SALAS=10, BotStatus creation uses default
- app.py: Uses _DEFAULT_LIMITE_SALAS in admin endpoint

## Notes
- Fix also auto-fixes existing users with limite_salas=0 when accessing the panel
- New users will get 10 rooms by default instead of 0
