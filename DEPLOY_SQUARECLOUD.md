# Deploy na SquareCloud - Guia Completo

## 📋 Pré-requisitos
- Conta na SquareCloud
- Repositório GitHub atualizado
- Token de usuário Discord válido

## 🚀 Passos para Deploy:

### 1. Acesse o Painel da SquareCloud
- Vá para https://squarecloud.app
- Faça login na sua conta

### 2. Criar Nova Aplicação
- Clique em "Create Application"
- Selecione "Import from GitHub"
- Escolha o repositório: `diaszzdev0/selfbot`
- Branch: `main`

### 3. Configurar Variáveis de Ambiente
**IMPORTANTE:** Configure estas variáveis no painel da SquareCloud:

```
DISCORD_TOKEN=SEU_TOKEN_DE_USUARIO_AQUI
SERVER_ID=1293459542797062165
CATEGORIA_ID=1293555181547683923
EMAIL_USER=seu_email@gmail.com
EMAIL_PASS=sua_senha_de_app_gmail
IMAP_SERVER=imap.gmail.com
MENSAGEM_ENTRADA=👋 Olá! Use pg Nome Sobrenome para verificar pagamento
DATABASE_URL=sqlite:///selfbot.db
FLASK_SECRET_KEY=sua_chave_secreta_aqui
```

### 4. Configurações da Aplicação
- **Nome**: Selfbot Manager
- **Memória**: 512MB (mínimo recomendado)
- **Arquivo Principal**: app.py
- **Comando Start**: python app.py
- **Porta**: 80

### 5. Deploy
- Clique em "Deploy"
- Aguarde o processo de build
- Verifique os logs para erros

## 🔧 Configurações Importantes:

### Token Discord
- **DEVE ser token de USUÁRIO**, não de bot
- Siga o guia `COMO_OBTER_TOKEN.md`
- Configure na seção "Environment Variables"

### Email (Para verificação de pagamentos)
- Use Gmail com senha de app
- Ative autenticação de 2 fatores
- Gere senha de app específica

### IDs do Discord
- SERVER_ID: ID do servidor Discord
- CATEGORIA_ID: ID da categoria onde estão os canais

## 📊 Monitoramento:

### Logs da Aplicação
- Acesse "Logs" no painel
- Monitore erros de conexão
- Verifique status dos bots

### Status da Aplicação
- Verde: Funcionando
- Amarelo: Problemas
- Vermelho: Offline

## ⚠️ Troubleshooting:

### Erro de Token
```
❌ Token inválido ou expirado
```
**Solução**: Obter novo token de usuário

### Erro de Memória
```
Application killed due to memory limit
```
**Solução**: Aumentar memória para 1GB

### Erro de Conexão
```
⏰ Timeout na conexão
```
**Solução**: Verificar token e conectividade

## 🔄 Atualizações:
1. Faça push para o GitHub
2. No painel SquareCloud: "Redeploy"
3. Aguarde o novo build

## 📞 Suporte:
- Discord SquareCloud: https://discord.gg/squarecloud
- Documentação: https://docs.squarecloud.app