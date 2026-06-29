# TODO5: Regras de Decisão do Selfbot - Filtro de Salas
Status: ✅ Complete

## Plan Steps:
1. [x] Add `_detectar_tipo_sala()` function in `bot_logic.py` implementing:
   - PRIORITY: 2x2/3x3/4x4 → always SALA_GN (Gelo Normal).
   - RULE 1: 1x1 + variations of "Infinito" → SALA_INF (Gelo Infinito).
   - RULE 2: (1x1..4x4) + "Mobile"/"Gel Normal" + NO "Inf" / "Infinito" → SALA_GN.
   - Fallback → SALA_PADRAO.
2. [x] In `on_message` after 2 payments confirmed: fetch thread history, call `_detectar_tipo_sala()`, log decision, pass salaid to `_enviar_sala()`.
3. [ ] Test via Discord threads with different challenge texts.

## Files edited:
- `bot_logic.py`

