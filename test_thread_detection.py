import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

async def test_thread_detection():
    """Testa se o sistema consegue detectar threads"""
    
    token = os.getenv("DISCORD_TOKEN", "").strip()
    server_id = int(os.getenv("SERVER_ID", "0"))
    categoria_id = int(os.getenv("CATEGORIA_ID", "0"))
    
    if not token or token == "seu_token_aqui":
        print("❌ Configure um token válido no .env")
        return
    
    if not server_id or not categoria_id:
        print("❌ Configure SERVER_ID e CATEGORIA_ID no .env")
        return
    
    print(f"🔍 Testando detecção de threads...")
    print(f"📊 Servidor: {server_id}")
    print(f"📂 Categoria: {categoria_id}")
    
    client = discord.Client()
    
    @client.event
    async def on_ready():
        print(f"✅ Conectado como: {client.user}")
        
        guild = client.get_guild(server_id)
        if not guild:
            print(f"❌ Servidor {server_id} não encontrado")
            await client.close()
            return
        
        print(f"🏠 Servidor encontrado: {guild.name}")
        
        categoria = guild.get_channel(categoria_id)
        if not categoria:
            print(f"❌ Categoria {categoria_id} não encontrada")
            await client.close()
            return
        
        print(f"📂 Categoria encontrada: {categoria.name}")
        
        # Lista todos os canais da categoria
        print(f"\n📋 Canais na categoria:")
        for canal in categoria.channels:
            print(f"  - {canal.name} (ID: {canal.id})")
            
            # Lista threads ativas
            threads_ativas = getattr(canal, "threads", [])
            if threads_ativas:
                print(f"    🧵 Threads ativas ({len(threads_ativas)}):")
                for thread in threads_ativas:
                    print(f"      - {thread.name} (ID: {thread.id})")
            
            # Lista threads arquivadas recentes
            try:
                threads_arquivadas = []
                async for thread in canal.archived_threads(limit=10):
                    threads_arquivadas.append(thread)
                
                if threads_arquivadas:
                    print(f"    📁 Threads arquivadas recentes ({len(threads_arquivadas)}):")
                    for thread in threads_arquivadas:
                        age = "N/A"
                        if thread.created_at:
                            from datetime import datetime
                            age_seconds = (datetime.now(thread.created_at.tzinfo) - thread.created_at).total_seconds()
                            age = f"{int(age_seconds/3600)}h atrás"
                        print(f"      - {thread.name} (ID: {thread.id}, Criada: {age})")
            except Exception as e:
                print(f"    ⚠️ Erro ao buscar threads arquivadas: {e}")
        
        print(f"\n✅ Teste concluído!")
        await client.close()
    
    @client.event
    async def on_thread_create(thread):
        print(f"🆕 THREAD CRIADA: {thread.name} (ID: {thread.id})")
    
    @client.event
    async def on_thread_join(thread):
        print(f"🔗 ENTROU NA THREAD: {thread.name} (ID: {thread.id})")
    
    try:
        await client.start(token)
    except Exception as e:
        print(f"❌ Erro: {e}")

if __name__ == "__main__":
    asyncio.run(test_thread_detection())