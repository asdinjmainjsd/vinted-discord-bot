"""Vinted Deal Hunter V1. Unofficial, best-effort public HTML monitoring.
No authentication, CAPTCHAs, proxy rotation or bypass techniques.
"""
import asyncio
import os
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from bs4 import BeautifulSoup
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
DB_PATH = os.environ.get("DEAL_DB", "deals.db")
INTERVAL = max(60, int(os.environ.get("CHECK_INTERVAL_SECONDS", "300")))
ITEM_RE = re.compile(r"/items/(\d+)")
ALLOWED_HOSTS = {"vinted.pt", "www.vinted.pt"}
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS searches (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 guild_id INTEGER NOT NULL,
 channel_id INTEGER NOT NULL,
 label TEXT NOT NULL,
 url TEXT NOT NULL,
 initialized INTEGER NOT NULL DEFAULT 0,
 UNIQUE(guild_id,url)
);
CREATE TABLE IF NOT EXISTS seen (
 search_id INTEGER NOT NULL,
 item_id TEXT NOT NULL,
 PRIMARY KEY(search_id,item_id)
);
"""

def clean_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError("Usa um link HTTPS do catálogo vinted.pt.")
    if parts.path.rstrip("/") != "/catalog":
        raise ValueError("O link tem de ser uma pesquisa /catalog da Vinted.")
    query = [(k, v) for k,v in parse_qsl(parts.query,keep_blank_values=True)
             if k not in {"time","page"} and not k.startswith("utm_")]
    if not any(k in {"search_text","brand_ids[]","catalog[]","price_to","order"} for k,_ in query):
        raise ValueError("O URL não parece conter filtros de pesquisa.")
    # Always inspect the newest listings.
    query = [(k,v) for k,v in query if k != "order"] + [("order","newest_first")]
    return urlunsplit(("https","www.vinted.pt","/catalog",urlencode(query),""))

def db_connect():
    db = sqlite3.connect(DB_PATH)
    db.executescript(CREATE_SQL)
    return db

def extract_items(html: str):
    """Only parse item links actually present in public page HTML.
    Returns [] when Vinted renders results in JS or restricts access.
    """
    soup = BeautifulSoup(html,"html.parser")
    items = {}
    for a in soup.select('a[href*="/items/"]'):
        href = a.get("href") or ""
        match = ITEM_RE.search(href)
        if not match: continue
        item_id = match.group(1)
        url = "https://www.vinted.pt" + href if href.startswith("/") else href
        if urlsplit(url).hostname not in ALLOWED_HOSTS: continue
        img = a.find("img")
        title = (a.get("title") or (img.get("alt") if img else None) or a.get_text(" ",strip=True) or "Novo anúncio")[:200]
        image = (img.get("src") or "") if img else ""
        items[item_id] = {"id":item_id,"title":title,"url":url,"image":image}
    return list(items.values())

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!",intents=intents)
tree = bot.tree

@tree.command(name="adicionar",description="Adicionar um link de pesquisa Vinted a este canal")
@app_commands.describe(link="URL da pesquisa Vinted",nome="Nome para identificares a pesquisa")
@app_commands.guild_only()
async def adicionar(interaction: discord.Interaction, link: str, nome: str):
    try: url = clean_url(link)
    except ValueError as ex:
        await interaction.response.send_message(str(ex),ephemeral=True)
        return
    with db_connect() as db:
        try:
            db.execute("INSERT INTO searches(guild_id,channel_id,label,url) VALUES(?,?,?,?)",
                       (interaction.guild_id,interaction.channel_id,nome[:80],url))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("Essa pesquisa já existe neste servidor.",ephemeral=True)
            return
    await interaction.response.send_message(
        f"✅ Pesquisa **{discord.utils.escape_markdown(nome[:80])}** adicionada neste canal. "
        "Na primeira leitura, os anúncios existentes são guardados sem gerar alertas. "
        "Os novos serão avisados quando a Vinted permitir obter os resultados.")

@tree.command(name="pesquisas",description="Listar pesquisas deste servidor")
@app_commands.guild_only()
async def pesquisas(interaction: discord.Interaction):
    with db_connect() as db:
        rows = db.execute("SELECT id,label,url FROM searches WHERE guild_id=? ORDER BY id",
                          (interaction.guild_id,)).fetchall()
    message = "\n".join(f"`{i}` — **{discord.utils.escape_markdown(label)}**: {url}" for i,label,url in rows)
    await interaction.response.send_message((message or "Ainda não tens pesquisas.")[:1900],ephemeral=True)

@tree.command(name="remover",description="Remover uma pesquisa pelo número")
@app_commands.guild_only()
async def remover(interaction: discord.Interaction, numero: int):
    with db_connect() as db:
        row = db.execute("SELECT id FROM searches WHERE id=? AND guild_id=?",
                         (numero,interaction.guild_id)).fetchone()
        if row:
            db.execute("DELETE FROM searches WHERE id=?",(numero,))
            db.execute("DELETE FROM seen WHERE search_id=?",(numero,))
    await interaction.response.send_message("✅ Pesquisa removida." if row else "Pesquisa não encontrada.",ephemeral=True)

@tree.command(name="teste",description="Enviar uma mensagem de teste para este canal")
@app_commands.guild_only()
async def teste(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Vinted Deal Hunter: bot ligado e a responder. Isto não confirma acesso à Vinted.")


async def get_alert_channel(channel_id: int):
    """Resolve a channel even when it is not yet in Discord's local cache."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            print(f"Canal {channel_id}: falha ao obter canal: {exc}", flush=True)
            return None
    if not hasattr(channel, "send"):
        print(f"Canal {channel_id}: não é um canal de mensagens.", flush=True)
        return None
    return channel


def make_embed(item, label):
    embed = discord.Embed(
        title=item["title"][:256],
        url=item["url"],
        description=f"Pesquisa: **{discord.utils.escape_markdown(label)}**\n[Ver anúncio na Vinted]({item['url']})",
        color=0x33AABB,
        timestamp=datetime.now(timezone.utc),
    )
    if item["image"].startswith("https://"):
        embed.set_thumbnail(url=item["image"])
    return embed


@tree.command(name="testar_envio", description="Testar envio direto de mensagens neste canal")
@app_commands.guild_only()
async def testar_envio(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = await get_alert_channel(interaction.channel_id)
    if not channel:
        await interaction.followup.send("Não consegui localizar o canal. Consulta o CMD.", ephemeral=True)
        return
    try:
        await channel.send("✅ Teste de envio do Vinted Deal Hunter neste canal.")
        await interaction.followup.send("✅ Mensagem enviada para o canal.", ephemeral=True)
    except discord.HTTPException as exc:
        print(f"Falha no teste de envio: {exc}", flush=True)
        await interaction.followup.send(
            f"❌ Falha a enviar: {type(exc).__name__} (HTTP {exc.status}). Verifica Ver Canal e Enviar Mensagens.",
            ephemeral=True,
        )


@tree.command(name="recuperar", description="Reenviar até 15 anúncios atuais de uma pesquisa")
@app_commands.describe(numero="ID apresentado em /pesquisas", quantidade="Número de anúncios a reenviar (1 a 15)")
@app_commands.guild_only()
async def recuperar(interaction: discord.Interaction, numero: int, quantidade: int = 5):
    quantidade = max(1, min(15, quantidade))
    await interaction.response.defer(ephemeral=True)
    with db_connect() as db:
        row = db.execute(
            "SELECT label,url,channel_id FROM searches WHERE guild_id=? AND id=?",
            (interaction.guild_id, numero)
        ).fetchone()
    if not row:
        await interaction.followup.send("Pesquisa não encontrada.", ephemeral=True)
        return
    label, url, channel_id = row
    channel = await get_alert_channel(channel_id)
    if not channel:
        await interaction.followup.send("Não consigo aceder ao canal original da pesquisa.", ephemeral=True)
        return
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(url)
        if response.status_code != 200:
            await interaction.followup.send(
                f"Vinted: HTTP {response.status_code}. Não tentei contornar a restrição.", ephemeral=True
            )
            return
        items = extract_items(response.text)[:quantidade]
        if not items:
            await interaction.followup.send("Não encontrei anúncios acessíveis no HTML.", ephemeral=True)
            return
        delivered = 0
        for item in items:
            await channel.send(embed=make_embed(item, label))
            with db_connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO seen(search_id,item_id) VALUES(?,?)",
                    (numero, item["id"])
                )
            delivered += 1
            await asyncio.sleep(1)
        print(f"Recuperação {numero}: {delivered} alertas enviados.", flush=True)
        await interaction.followup.send(
            f"✅ Reenviei {delivered} anúncios atuais para o canal original. "
            "Alguns podem ser antigos ou já ter sido apresentados.",
            ephemeral=True,
        )
    except (httpx.HTTPError, discord.HTTPException) as exc:
        print(f"Recuperação {numero}: {type(exc).__name__}: {exc}", flush=True)
        await interaction.followup.send(
            f"❌ Interrompido após {locals().get('delivered', 0)} envios: {type(exc).__name__}. Consulta o CMD.",
            ephemeral=True,
        )


async def monitor():
    await bot.wait_until_ready()
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": "VintedDealHunter/1.0 (public-page-monitor)"},
    ) as client:
        while not bot.is_closed():
            with db_connect() as db:
                searches = db.execute(
                    "SELECT id,channel_id,label,url,initialized FROM searches"
                ).fetchall()
            for search_id, channel_id, label, url, initialized in searches:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        print(f"Pesquisa {search_id}: HTTP {response.status_code}; sem alertas", flush=True)
                        continue
                    items = extract_items(response.text)
                    if not items:
                        print(f"Pesquisa {search_id}: HTML sem anúncios acessíveis", flush=True)
                        continue

                    with db_connect() as db:
                        unseen = [
                            item for item in items
                            if not db.execute(
                                "SELECT 1 FROM seen WHERE search_id=? AND item_id=?",
                                (search_id, item["id"])
                            ).fetchone()
                        ]

                    if not initialized:
                        # Initial snapshot deliberately does not alert historical listings.
                        with db_connect() as db:
                            for item in unseen:
                                db.execute(
                                    "INSERT OR IGNORE INTO seen(search_id,item_id) VALUES(?,?)",
                                    (search_id, item["id"])
                                )
                            db.execute(
                                "UPDATE searches SET initialized=1 WHERE id=?", (search_id,)
                            )
                        print(
                            f"Pesquisa {search_id} ({label}): primeira leitura, "
                            f"{len(items)} anúncios guardados como referência.",
                            flush=True,
                        )
                        continue

                    print(
                        f"Pesquisa {search_id} ({label}): "
                        f"{len(items)} anúncios visíveis, {len(unseen)} por enviar.",
                        flush=True,
                    )
                    if not unseen:
                        continue

                    channel = await get_alert_channel(channel_id)
                    if channel is None:
                        print(
                            f"Pesquisa {search_id}: canal {channel_id} inacessível; "
                            f"{len(unseen)} anúncios permanecem por enviar.",
                            flush=True,
                        )
                        continue

                    delivered = 0
                    for item in unseen:
                        try:
                            await channel.send(embed=make_embed(item, label))
                        except discord.HTTPException as exc:
                            print(
                                f"Pesquisa {search_id}: ENVIO FALHOU "
                                f"(HTTP {exc.status}) canal={channel_id} item={item['id']}: {exc}",
                                flush=True,
                            )
                            break
                        # Persist only after Discord confirms a successful send.
                        with db_connect() as db:
                            db.execute(
                                "INSERT OR IGNORE INTO seen(search_id,item_id) VALUES(?,?)",
                                (search_id, item["id"])
                            )
                        delivered += 1
                        await asyncio.sleep(1)
                    print(
                        f"Pesquisa {search_id}: entregues {delivered}/{len(unseen)}. "
                        "Os restantes serão tentados na próxima verificação.",
                        flush=True,
                    )
                except httpx.HTTPError as exc:
                    print(f"Pesquisa {search_id}: erro HTTP: {exc}", flush=True)
                except Exception as exc:
                    print(
                        f"Pesquisa {search_id}: erro inesperado {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                await asyncio.sleep(2)
            await asyncio.sleep(INTERVAL)

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")

async def startup():
    if not TOKEN: raise RuntimeError("Configura DISCORD_BOT_TOKEN.")
    with db_connect(): pass
    async with bot:
        # Discord must provide application_id before commands can be synced.
        await bot.login(TOKEN)
        if GUILD_ID:
            guild=discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:
            await tree.sync()
        task=asyncio.create_task(monitor())
        try: await bot.connect(reconnect=True)
        finally: task.cancel()

if __name__=="__main__":
    asyncio.run(startup())
