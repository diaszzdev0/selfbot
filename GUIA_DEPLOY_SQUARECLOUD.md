# Guia rápido de deploy na Square Cloud (iniciante)

## 1) Instalar a CLI da Square Cloud (via npm)
Abra o terminal do VS Code e rode:

```bash
npm install -g squarecloud-cli
```

> Se o npm não estiver instalado na sua máquina, instale Node.js antes.

## 2) Fazer login na SquareCloud

```bash
squarecloud login
```

Na sequência, cole o **token/chave de API** da sua conta.

## 3) Upload inicial (deploy) do projeto

> Escolha um nome para o app (ex.: `selfbot-rate-limit`).

```bash
squarecloud deploy \
  --name selfbot-rate-limit \
  --entry main.py \
  --memory 128
```

A CLI vai detectar `squarecloud.app`, empacotar e fazer o deploy.

## 4) Atualizar futuros commits
Quando você mudar código no VS Code (e fizer commit no Git), rode novamente:

```bash
squarecloud deploy \
  --name selfbot-rate-limit \
  --entry main.py \
  --memory 128
```

Ou, se sua SquareCloud suporta deploy por GitHub, a atualização pode ser feita pelo painel (mais simples):
- selecione o repositório
- branch `main`
- redeploy

## 5) Como validar
Após o deploy, abra os logs:

```bash
squarecloud logs --name selfbot-rate-limit --follow
```

Procure por:
- build concluído
- `Running on http://0.0.0.0:80`

---

### Observação importante
Se você estiver rodando o selfbot em modo separado (processo/multiprocessing), seu app Flask deve iniciar normalmente no `main.py`, e os workers do selfbot serão disparados pelos endpoints do painel.

