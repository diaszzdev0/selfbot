# 🚀 Atualização Rápida na SquareCloud

## ✅ Código Atualizado no GitHub
- ✅Correção buscar_pagamento_imap para usar user_id como chave primária
- ✅ Logs de debug mais claros para IMAP
- ✅Conexões IMAP isoladas por usuário

## 📋 Passos para Atualizar na SquareCloud:

### 1. Acesse o Painel
- Vá para: https://squarecloud.app
- Faça login na sua conta

### 2. Encontre sua Aplicação
- Procure por "Selfbot Manager" ou o nome da sua aplicação
- Clique na aplicação

### 3. Redeploy
- Clique no botão **"Redeploy"** ou **"Deploy"**
- Aguarde o processo de build (pode levar 2-5 minutos)

### 4. Verifique os Logs
- Vá para a aba **"Logs"**
- Procure por mensagens como:
  ```
  📬 Usando conexão IMAP do user_id=X
  ```

## 🔍 Correção de Bug
O problema era que quando múltiplos usuários usam o mesmo email,um deles não encontrava pagamentos. Agora cada conexão IMAP usa o user_id como chave para evitar conflitos.

## 🔄 Próximas Atualizações:
Para futuras atualizações, basta:
1. Fazer push no GitHub
2. Clicar "Redeploy" na SquareCloud
3. Aguardar o build

---
**Última atualização**: Correção buscar_pagamento_imap com user_id
