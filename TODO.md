# TODO - Correção multiusuário (selfbot)

- [x] Auditar isolamento por usuário no runtime (`bot_logic.py`)
- [x] Auditar start/stop/restart por usuário (`app.py`)
- [x] Auditar cache/conexões IMAP para conflito entre usuários (`imap_optimizer.py`)
- [x] Corrigir pontos de compartilhamento global indevido
- [x] Otimizar latência IMAP no `imap_optimizer.py` (reduzir espera fixa e melhorar busca por conexão persistente)
- [x] Adicionar logs de tempo de busca de pagamento no `bot_logic.py`
- [ ] Deploy na Square Cloud (`squarecloud commit -r`)
