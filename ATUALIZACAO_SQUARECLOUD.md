# 🚀 Atualização Rápida na SquareCloud

## ✅ Código Atualizado no GitHub
- ✅ Sistema de detecção de threads melhorado
- ✅ Eventos em tempo real (on_thread_create, on_thread_join)
- ✅ Monitoramento de threads arquivadas
- ✅ Verificação inicial de threads existentes
- ✅ Sistema de reconexão automática
- ✅ Validação de token melhorada
- ✅ Diagnósticos de conectividade

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
  ✅ Sistema de cache otimizado ativado!
  🧵 Verificando threads existentes na inicialização...
  ✅ Verificação inicial concluída: X threads encontradas
  ```

### 5. Configure Variáveis (SE NECESSÁRIO)
Se ainda não configurou, adicione estas variáveis em **"Environment Variables"**:
```
DISCORD_TOKEN=SEU_TOKEN_DE_USUARIO_REAL
SERVER_ID=1293459542797062165
CATEGORIA_ID=1293555181547683923
EMAIL_USER=seu_email@gmail.com
EMAIL_PASS=sua_senha_de_app_gmail
IMAP_SERVER=imap.gmail.com
MENSAGEM_ENTRADA=👋 Olá! Use pg Nome Sobrenome para verificar pagamento
```

## 🔍 Verificação de Funcionamento:

### Logs de Sucesso:
```
✅ Sessao: SeuUsuario#1234 (ID: 123456789)
🌐 Servidor: Nome do Servidor
📂 Categoria: Nome da Categoria
🧵 X thread(s) carregada(s)
🚀 Sistema de cache otimizado ativado!
```

### Logs de Thread Detection:
```
🧵 Nova thread detectada: 'Nome da Thread'
✅ Mensagem enviada para thread: Nome
🎆 Thread criada em tempo real: 'Nome'
```

## ⚠️ Troubleshooting:

### Se aparecer "Token inválido":
1. Obtenha novo token seguindo `COMO_OBTER_TOKEN.md`
2. Atualize a variável `DISCORD_TOKEN`
3. Redeploy novamente

### Se não detectar threads:
1. Verifique se `SERVER_ID` e `CATEGORIA_ID` estão corretos
2. Verifique se o usuário tem acesso ao servidor
3. Verifique os logs para erros

### Se der erro de memória:
1. Aumente a memória para 1GB no painel
2. Redeploy

## 📊 Monitoramento:
- **Status Verde**: Tudo funcionando
- **Status Amarelo**: Problemas menores
- **Status Vermelho**: Aplicação offline

## 🔄 Próximas Atualizações:
Para futuras atualizações, basta:
1. Fazer push no GitHub
2. Clicar "Redeploy" na SquareCloud
3. Aguardar o build

---
**Última atualização**: Sistema de detecção de threads melhorado + reconexão automática