# Como Obter Token de USUÁRIO do Discord

## ⚠️ IMPORTANTE: 
- Este é um TOKEN DE USUÁRIO, não de bot
- Selfbots violam os Termos de Serviço do Discord
- Podem resultar em banimento permanente da conta
- Use apenas em contas secundárias/teste
- NUNCA use em sua conta principal

## Passos para obter o TOKEN DE USUÁRIO:

### Método 1: Developer Tools (Mais Seguro)
1. **Abra Discord no NAVEGADOR** (não no app desktop)
2. Vá para https://discord.com/app
3. **Faça login** na conta que será usada como selfbot
4. Pressione **F12** para abrir Developer Tools
5. Vá para a aba **"Network"**
6. Pressione **F5** para recarregar
7. Procure por requisições para **"api"** ou **"gateway"**
8. Clique em uma requisição
9. Na aba **"Headers"**, procure por **"Authorization"**
10. O valor é seu token (sem "Bearer " na frente)

### Método 2: Console JavaScript
1. **Discord no navegador** (discord.com/app)
2. Pressione **F12** → aba **"Console"**
3. Cole este código:
```javascript
(webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()
```
4. Pressione **Enter**
5. O token será exibido

### Método 3: Local Storage
1. **Discord no navegador**
2. **F12** → aba **"Application"**
3. **Local Storage** → **"https://discord.com"**
4. Procure pela chave **"token"**
5. O valor (sem aspas) é seu token

## 🔧 Configuração no .env:
```env
DISCORD_TOKEN=SEU_TOKEN_DE_USUARIO_AQUI
SERVER_ID=1293459542797062165
CATEGORIA_ID=1293555181547683923
EMAIL_USER=seu_email@gmail.com
EMAIL_PASS=sua_senha_de_app
MENSAGEM_ENTRADA=Olá! Use pg Nome Sobrenome para verificar pagamento.
```

## ✅ Como identificar se é token de usuário:
- **Comprimento**: ~70+ caracteres
- **Formato**: `MTIzNDU2...` (Base64)
- **NÃO começa** com "Bot "
- **NÃO tem** prefixos especiais

## 🚨 SEGURANÇA:
- **NUNCA** compartilhe o token
- Use apenas em **contas secundárias**
- Token expira se você **trocar a senha**
- **Logout/login** pode invalidar o token
- Mantenha o arquivo **.env** privado

## 🔄 Se o token parar de funcionar:
1. Faça logout e login no Discord
2. Obtenha um novo token
3. Atualize o arquivo .env
4. Reinicie o selfbot