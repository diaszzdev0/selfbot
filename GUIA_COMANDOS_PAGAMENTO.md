# 💰 Guia de Comandos de Pagamento - Selfbot

## ✅ **Formatos Aceitos para Verificar Pagamentos:**

### **Formato Básico:**
- `pg João Silva`
- `pago Maria Santos`
- `paguei Pedro Costa`

### **Com Pontuação:**
- `pg: João Silva`
- `pago- Maria Santos`
- `pg. Pedro Costa`

### **Formato Invertido:**
- `João Silva pg`
- `Maria Santos pago`
- `Pedro Costa paguei`

### **Comandos Abreviados:**
- `p João Silva`
- `pag Maria Santos`

### **Comandos Naturais:**
- `verificar João Silva`
- `check Maria Santos`
- `buscar Pedro Costa`
- `consultar Ana Oliveira`

### **Frases Completas:**
- `verificar pagamento de João Silva`
- `check pagamento do Pedro`
- `buscar pagamento da Maria`

## 🚫 **Formatos NÃO Aceitos:**
- `pg` (sem nome)
- `p` (muito curto)
- Mensagens sem palavras-chave de pagamento

## 📝 **Dicas Importantes:**

1. **Nome Mínimo:** Use pelo menos 2 caracteres
2. **Acentos:** Funcionam normalmente (João, María, etc.)
3. **Maiúsculas/Minúsculas:** Tanto faz
4. **Espaços Extras:** São removidos automaticamente
5. **Pontuação:** Aceita `:`, `-`, `.`, etc.

## 🎯 **Exemplos Práticos:**

### ✅ **Funcionam:**
```
pg João Silva
PAGO MARIA SANTOS
verificar Pedro
check Ana Costa
João pg
pag Carlos
pg: Maria
```

### ❌ **Não Funcionam:**
```
pg
olá pessoal
como estão?
p
```

## 🔧 **Melhorias Implementadas:**

- ✅ Detecção de múltiplos formatos
- ✅ Suporte a acentos e caracteres especiais
- ✅ Filtragem inteligente de palavras comuns
- ✅ Logs de debug para troubleshooting
- ✅ Validação de tamanho mínimo
- ✅ Limpeza automática de pontuação

---
**Versão:** 2.0 - Sistema de detecção melhorado