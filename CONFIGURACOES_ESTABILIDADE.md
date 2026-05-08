# 🔧 Configurações de Estabilidade - Discord Selfbot

## ⚙️ **Melhorias Implementadas para Conexão:**

### **1. Health Check Avançado:**
- ✅ Verificação a cada 20 segundos (mais frequente)
- ✅ Teste real de conectividade com o servidor
- ✅ Detecção de latência alta (>30s)
- ✅ Reconexão automática inteligente
- ✅ Até 5 tentativas de reconexão
- ✅ Reinício completo como último recurso

### **2. Sistema de Reconexão Melhorado:**
- ✅ Timeout progressivo (30s, 45s, 60s, 75s, 90s)
- ✅ Detecção de rate limiting
- ✅ Fechamento adequado antes de reconectar
- ✅ Verificação pós-reconexão
- ✅ Logs detalhados para diagnóstico

### **3. Configurações do Cliente Discord:**
```python
discord.Client(
    chunk_guilds_at_startup=False,    # Não carrega todos os membros
    heartbeat_timeout=120.0,          # 2 minutos de timeout
    max_messages=500,                 # Limite de mensagens em cache
    guild_ready_timeout=30.0          # Timeout para guilds
)
```

### **4. Eventos de Conexão Melhorados:**
- ✅ `on_disconnect()` - Detecta desconexões
- ✅ `on_resumed()` - Confirma reconexões
- ✅ Logs informativos com emojis
- ✅ Verificação de acesso ao servidor

## 🚨 **Principais Causas de "Conexão Perdida":**

### **1. Token Expirado/Inválido:**
- **Sintoma:** Erro de login constante
- **Solução:** Obter novo token seguindo `COMO_OBTER_TOKEN.md`

### **2. Rate Limiting do Discord:**
- **Sintoma:** Erro HTTP 429
- **Solução:** Sistema aguarda automaticamente

### **3. Problemas de Rede:**
- **Sintoma:** Timeouts frequentes
- **Solução:** Health check reconecta automaticamente

### **4. Servidor/Categoria Inacessível:**
- **Sintoma:** Servidor não encontrado
- **Solução:** Verificar IDs e permissões

## 📊 **Logs de Diagnóstico:**

### **Conexão Normal:**
```
✅ Sessao: Usuario#1234 (ID: 123456789)
🌐 Servidor: Nome do Servidor
📂 Categoria: Nome da Categoria
📊 Status: conectado=True, latência=0.123s, falhas=0
```

### **Problema Detectado:**
```
🔴 Conexão perdida com Discord
Estado: closed=True, latency=inf
Problema de conexão detectado (falha #1)
Tentativa de reconexão 1/5
```

### **Reconexão Bem-sucedida:**
```
✅ Reconexão bem-sucedida
🔄 Conexão restaurada com Discord
Latência atual: 0.156s
✅ Acesso restaurado: Servidor / Categoria
```

## 🔄 **Processo de Reconexão:**

1. **Detecção:** Health check identifica problema
2. **Diagnóstico:** Verifica causa (latência, servidor, etc.)
3. **Fechamento:** Para conexão atual adequadamente
4. **Aguardo:** Espera tempo progressivo
5. **Reconexão:** Tenta conectar novamente
6. **Verificação:** Confirma se funcionou
7. **Repetição:** Até 5 tentativas
8. **Reinício:** Como último recurso

## ⚡ **Otimizações de Performance:**

- ✅ Cache IMAP otimizado (reduz timeouts)
- ✅ Rate limiting inteligente
- ✅ Timeouts adequados para cada operação
- ✅ Limpeza automática de memória
- ✅ Pool de conexões IMAP

## 🛠️ **Troubleshooting:**

### **Se continuar perdendo conexão:**

1. **Verificar Token:**
   ```bash
   python token_validator.py
   ```

2. **Testar Conectividade:**
   ```bash
   python connection_diagnostics.py
   ```

3. **Verificar Logs:**
   - Procurar por padrões nos logs
   - Identificar horários específicos
   - Verificar mensagens de erro

4. **Reiniciar Aplicação:**
   - Parar bot no painel
   - Aguardar 30 segundos
   - Iniciar novamente

---
**Versão:** 3.0 - Sistema de reconexão automática avançado