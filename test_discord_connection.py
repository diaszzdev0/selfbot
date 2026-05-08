import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

async def test_simple_connection():
    """Teste simples de conexão Discord"""
    
    # Pega o token do .env
    token = os.getenv("DISCORD_TOKEN", "").strip()
    
    if not token:
        print("Token nao encontrado no .env")
        return
    
    print(f"Token encontrado (primeiros 10 chars): {token[:10]}...")
    
    # Configuração para discord.py-self (versão antiga)
    client = discord.Client()
    
    @client.event
    async def on_ready():
        print(f"Conectado como: {client.user}")
        print(f"Latencia: {client.latency:.3f}s")
        print(f"Servidores: {len(client.guilds)}")
        
        # Lista alguns servidores
        for guild in client.guilds[:3]:  # Primeiros 3 servidores
            print(f"  - {guild.name} (ID: {guild.id})")
        
        # Aguarda 5 segundos e desconecta
        await asyncio.sleep(5)
        print("Desconectando...")
        await client.close()
    
    @client.event
    async def on_disconnect():
        print("Desconectado")
    
    @client.event
    async def on_resumed():
        print("Reconectado")
    
    try:
        print("Tentando conectar...")
        await asyncio.wait_for(client.start(token), timeout=30.0)
    except asyncio.TimeoutError:
        print("Timeout de 30s - possivel problema de conectividade")
    except discord.LoginFailure:
        print("Token invalido ou expirado")
    except discord.HTTPException as e:
        print(f"Erro HTTP Discord: {e}")
    except Exception as e:
        print(f"Erro: {type(e).__name__}: {e}")
    finally:
        if not client.is_closed():
            await client.close()

if __name__ == "__main__":
    asyncio.run(test_simple_connection())