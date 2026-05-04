# TODO: Fix Payment Verification
Status: ✅ Plan Approved - In Progress

## Breakdown from Approved Plan

### Step 1: Create TODO.md [✅ COMPLETE]
- Track all implementation steps

### Step 2: Enhance bot_logic.py [✅ COMPLETE]
- Add retry logic for IMAP search (3x, 5s delay)
- Full debug logging on misses (name parts, top 3 matches)
- Increase IMAP cache window reference to 3 days

### Step 3: Improve imap_optimizer.py [✅ COMPLETE]
- Fuzzy name matching (Levenshtein ≤2)
- More regex for valor/banco (PIX keys, "crédito recebido")
- Poll every 30s + on-demand refresh
- Delay "usado" mark until bot confirmation

### Step 4: Add Debug Endpoints [⏳ ACTIVE]
- app.py: `/debug_imap?nome=João` (shows cache matches)
- cliente_app.py: Client test button

### Step 5: Create test_pagamento.py [PENDING]
- Mock IMAP + sample emails → full verification flow test

### Step 6: Testing & Deploy [PENDING]
- Run `python test_pagamento.py`
- Local test: Check logs for "✅ Encontrado"
- Deploy: git commit → update_squarecloud.py

## Progress Commands:
/status - Show current step
/next - Complete current → next step
/revert - Go back one step
/logs - Show recent logs
/test - Run specific test

