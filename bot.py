import asyncio
import re
import discord
from imap_tools import MailBox, AND
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
SERVER_ID = int(os.getenv("SERVER_ID", "0"))
CATEGORIA_ID = int(os.getenv("CATEGORIA_ID", "0"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
MENSAGEM_ENTRADA = os.getenv("MENSAGEM_ENTRADA", "👋 Olá! Estou aqui para ajudar. Use `pg Nome Sobrenome` para verificar seu pagamento.")

DELAY_TIME = 7

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.threads = True

client = discord.Client(self_bot=True, intents=intents)

threads_com_mensagem: set[int] = set()


def buscar_pagamento(nome: str):
    with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mb:
        msgs = list(mb.fetch(AND(seen=False), mark_seen=False))
        for msg in msgs:
            conteudo = (msg.subject or "") + " " + (msg.text or "") + " " + (msg.html or "")
            if nome.lower() in conteudo.lower():
                valor = re.search(r"R?\$?\s?([\d.,]+)", conteudo)
                id_pag = re.search(r"(?:ID|id|Id)[:\s#]*([\w\d\-]+)", conteudo)
                banco = re.search(r"(?:Banco|banco|Bank|bank)[:\s]*([\w\s]+)", conteudo)
                mb.flag([msg.uid], [r"\Seen"], True)
                return {
                    "valor": valor.group(1) if valor else "N/A",
                    "id": id_pag.group(1) if id_pag else "N/A",
                    "banco": banco.group(1).strip() if banco else "N/A",
                }
    return None


@client.event
async def on_ready():
    print(f"Selfbot online como {client.user}")


async def processar_thread(thread: discord.Thread):
    """Função central: entra na thread e envia a mensagem uma única vez."""
    if thread.id in threads_com_mensagem:
        return
    threads_com_mensagem.add(thread.id)

    if not thread.parent:
        print(f"[THREAD] Thread dinâmica sem categoria detectada: '{thread.name}' (id={thread.id})")
        print(f"[THREAD] Aguardando {DELAY_TIME}s para entrar...")
    else:
        print(f"[THREAD] Thread detectada: '{thread.name}' | categoria={getattr(thread.parent, 'category_id', None)}")

    await asyncio.sleep(DELAY_TIME)

    try:
        await thread.join()
        await thread.send(MENSAGEM_ENTRADA)
        print(f"[THREAD] Mensagem enviada em '{thread.name}'")
    except Exception as e:
        print(f"[THREAD] Erro em '{thread.name}': {e}")
        threads_com_mensagem.discard(thread.id)


@client.event
async def on_thread_create(thread: discord.Thread):
    if not thread.guild or thread.guild.id != SERVER_ID:
        return

    parent = thread.parent

    if parent is not None:
        # Thread com pai conhecido: verifica categoria
        if getattr(parent, "category_id", None) != CATEGORIA_ID:
            return
    else:
        # Thread dinâmica/aleatória sem pai: processa desde que esteja no servidor correto
        print(f"[THREAD] Thread sem parent detectada: '{thread.name}' — processando mesmo assim")

    asyncio.ensure_future(processar_thread(thread))


@client.event
async def on_thread_join(thread: discord.Thread):
    """Fallback: cobre casos onde on_thread_create não dispara."""
    if not thread.guild or thread.guild.id != SERVER_ID:
        return

    parent = thread.parent
    if parent is not None and getattr(parent, "category_id", None) != CATEGORIA_ID:
        return

    asyncio.ensure_future(processar_thread(thread))


@client.event
async def on_message(message: discord.Message):
    if not message.guild or message.guild.id != SERVER_ID:
        return

    channel = message.channel

    # Fallback para threads detectadas via mensagem
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if parent is not None and getattr(parent, "category_id", None) != CATEGORIA_ID:
            pass  # thread fora da categoria, mas pode ser dinâmica
        asyncio.ensure_future(processar_thread(channel))

    if message.author != client.user:
        return
    if not message.content.lower().startswith("pg "):
        return

    partes = message.content.split(" ", 1)
    if len(partes) < 2 or not partes[1].strip():
        return

    nome_busca = partes[1].strip()

    msg_fila = await message.reply(
        f"⏳ Verificação na fila!\n"
        f"Sua posição: 1\n"
        f"Estimativa de espera: 15 segundos.\n"
        f"Processamos 4 por vez para manter a estabilidade. Aguarde..."
    )

    await asyncio.sleep(15)

    resultado = await asyncio.get_running_loop().run_in_executor(None, buscar_pagamento, nome_busca)

    await msg_fila.delete()

    if resultado:
        await message.reply(
            f"✅ Pagamento confirmado ({resultado['banco']}) para **{nome_busca}**\n"
            f"Valor: {resultado['valor']} (BRL)\n"
            f"ID: {resultado['id']}"
        )
    else:
        await message.reply(f"❌ **Pagamento não confirmado** para {nome_busca}.")


client.run(TOKEN)
