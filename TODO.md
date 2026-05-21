# TODO - arrumou (executando)

## Objetivo
- Estabilizar: `modo_sala_id`, IMAP multiusuário e Deploy Square Cloud.

## Checklist (do plano aprovado)
- [ ] 1) Revisar `imap_optimizer.py` (buscar usa `user_id` / sem estado global indevido)
- [ ] 2) Revisar `bot_logic.py` (prioridade modo_sala_id → detecção → default; config completa)
- [ ] 3) Revisar `app.py` e `cliente_app.py` (migrações/persistência de `modo_sala_id` e endpoint `/api_modos`)
- [ ] 4) Revisar scripts de deploy: `deploy_squarecloud_all.py`, `check_deploy.py`, `deploy_now.py`, `deploy_auto.py`
- [ ] 5) Rodar testes: `python test_pagamento.py`
- [ ] 6) Rodar local: `python app.py & python cliente_app.py` e validar `/cliente/api_modos`
- [ ] 7) Deploy: `python deploy_squarecloud_all.py` e salvar resultado

## Notas
- Ao final de cada passo, atualizarei esta seção com progresso.

