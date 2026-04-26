# 🚀 Atualização Rápida - SquareCloud

## ⚡ Método Automático (Recomendado)

### Windows:
1. **Execute**: `atualizar_squarecloud.bat`
2. **Aguarde** o script fazer o push para GitHub
3. **Painel abrirá automaticamente**
4. **Clique em "Redeploy"** na sua aplicação
5. **Aguarde** o build (2-5 minutos)

### Linux/Mac:
```bash
python3 update_squarecloud.py
```

## 🔧 Método Manual

### 1. Push para GitHub:
```bash
git add .
git commit -m "Atualização $(date)"
git push origin main
```

### 2. SquareCloud:
1. Acesse: https://squarecloud.app/dashboard
2. Encontre "Selfbot Manager"
3. Clique "Redeploy"
4. Aguarde build

## ✅ Verificação de Sucesso

### Logs Esperados:
```
✅ Sessao: Usuario#1234
🌐 Servidor conectado
🧵 X thread(s) carregada(s)
🚀 Sistema ativo!
```

### Status da Aplicação:
- 🟢 **Verde**: Funcionando perfeitamente
- 🟡 **Amarelo**: Problemas menores
- 🔴 **Vermelho**: Offline (verificar logs)

## 🆘 Problemas Comuns

### Token Inválido:
1. Obter novo token (ver `COMO_OBTER_TOKEN.md`)
2. Atualizar variável `DISCORD_TOKEN`
3. Redeploy

### Erro de Memória:
1. Aumentar memória para 1GB
2. Redeploy

### Não Detecta Threads:
1. Verificar `SERVER_ID` e `CATEGORIA_ID`
2. Verificar permissões do usuário
3. Verificar logs

---
**Tempo total**: ~5 minutos
**Última atualização**: Sistema otimizado com detecção em tempo real