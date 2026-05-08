# TODO3: Seleção Modo Sala por Thread
Status: In progress

## Plan:
**Information:** API /modos retorna lista modos com salaid/nome. Config atual usa primeiro modo.

**Changes:**
1. [x] bot_logic.py: Clean duplicate URLs
2. templates/cliente.html: Dropdown modos (fetch /api_modos)
3. cliente_app.py: Add /api_modos lista + salvar modo_sala_id
4. bot_logic.py: Use config.modo_sala_id em /criar?salaid=...
5. DB migration in app init.

**Files:**
- models.py, cliente_app.py, templates/cliente.html, bot_logic.py

**Followup:** python app.py cliente_app.py → config modo por user.
