"""Read-only FiveM public status; no RCON, player identifiers or server secrets."""
import asyncio
import datetime
import ipaddress
import json
import logging
import re
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp.resolver import DefaultResolver
import discord
from discord import app_commands
from discord.ext import tasks

log = logging.getLogger('redfear')


def endpoint_url(value):
    value = value.strip()
    bare = '://' not in value
    if bare:
        value = 'http://' + value
    parsed = urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Folosește IP:port sau un URL HTTP/HTTPS public, fără credențiale.')
    if parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Folosește adresa de bază a serverului, fără /info.json, parametri sau fragment.')
    host = parsed.hostname
    if '%' in host or not re.fullmatch(r'[A-Za-z0-9.:-]+',host):
        raise ValueError('Nume de server invalid.')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() == 'localhost' or '.' not in host:
            raise ValueError('Este necesară o adresă publică.')
    else:
        if not ip.is_global:
            raise ValueError('Este necesară o adresă publică, nu localhost/LAN.')
    # Bare FiveM host names use 30120; explicit URLs use their protocol default.
    port = parsed.port
    if port is None and bare:
        port = 30120
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Port invalid.')
    netloc = f'[{host}]' if ':' in host else host.lower()
    if port is not None:
        netloc += f':{port}'
    return urlunsplit((parsed.scheme,netloc,'','',''))


def join_url(value):
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r'[A-Za-z0-9]{4,16}',value):
        return 'https://cfx.re/join/' + value
    if re.fullmatch(r'https://cfx\.re/join/[A-Za-z0-9]{4,16}/?',value):
        return value.rstrip('/')
    raise ValueError('Linkul de conectare trebuie să fie https://cfx.re/join/COD sau doar codul de conectare.')


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        records = await super().resolve(host,port,family)
        if not records or any(not ipaddress.ip_address(record['host']).is_global for record in records):
            raise ValueError('Endpointul nu se rezolvă exclusiv către adrese publice.')
        return records


def count(value, minimum=0):
    if isinstance(value,bool) or not re.fullmatch(r'[0-9]{1,4}',str(value)):
        raise ValueError('Număr de jucători invalid.')
    result = int(value)
    if not minimum <= result <= 2048:
        raise ValueError('Număr de jucători invalid.')
    return result


def parse_status(data):
    if not isinstance(data,dict):
        raise ValueError('Răspuns FiveM invalid.')
    clients = count(data.get('clients'))
    capacity = count(data.get('sv_maxclients'),1)
    hostname = data.get('hostname')
    if not isinstance(hostname,str) or not hostname.strip():
        raise ValueError('Numele serverului lipsește.')
    hostname = re.sub(r'\^[0-9]|[\x00-\x1f\x7f]','',hostname)[:200]
    return {'online':True,'clients':clients,'capacity':capacity,'hostname':hostname}


async def fetch_status(endpoint):
    endpoint = endpoint_url(endpoint)
    connector = aiohttp.TCPConnector(resolver=PublicResolver())
    async with aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=8),trust_env=False) as session:
        async with session.get(endpoint+'/dynamic.json',allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('Endpointul FiveM nu este disponibil.')
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw)>65536:
                    raise ValueError('Răspuns FiveM prea mare.')
            return parse_status(json.loads(raw))


def status_embed(status, config):
    checked = datetime.datetime.fromtimestamp(status['checked'],datetime.timezone.utc)
    embed = discord.Embed(title='RedFear • FiveM',color=0x2ECC71 if status['online'] else 0xE74C3C,timestamp=checked)
    if status['online']:
        embed.description = '🟢 **Online**\n' + discord.utils.escape_markdown(discord.utils.escape_mentions(status['hostname']))
        embed.add_field(name='Jucători',value=f'{status["clients"]} / {status["capacity"]}')
    else:
        embed.description = '🔴 **Indisponibil**\nServerul este oprit sau endpointul de status nu poate fi accesat. Numărul de jucători nu este disponibil.'
    embed.add_field(name='Conectare din consola FiveM (F8)',value=f'`connect {config["endpoint"]}`',inline=False)
    embed.set_footer(text='Actualizare aproximativ la 60 secunde • Ultima verificare')
    return embed


def connect_view(config):
    view = discord.ui.View(timeout=None)
    if config.get('join'):
        view.add_item(discord.ui.Button(label='Conectează-te',style=discord.ButtonStyle.link,url=config['join'],emoji='🎮'))
    return view


class FiveM:
    def register_fivem(self):
        @self.tree.command(name='fivem_setup',description='Configurează panoul FiveM: adresa publică, canal și link cfx.re')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def setup(i:discord.Interaction,endpoint:str,channel:discord.TextChannel,join_link:str=''):
            self.admin(i)
            normalized = endpoint_url(endpoint)
            connect = join_url(join_link)
            if channel.guild.id != self.guild_id:
                raise ValueError('Alege un canal din serverul configurat.')
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.fivem_lock:
                old = self.fivem_config()
                config = {'endpoint':normalized,'join':connect,'channel':channel.id,'message':0}
                status = await self.check_fivem(normalized)
                existing = None
                if old and old['channel']==channel.id and old['message']:
                    try:
                        existing = await channel.fetch_message(old['message'])
                    except discord.NotFound:
                        pass
                if existing:
                    await existing.edit(embed=status_embed(status,config),view=connect_view(config))
                    config['message'] = existing.id
                else:
                    message = await channel.send(embed=status_embed(status,config),view=connect_view(config))
                    config['message'] = message.id
                self.store.set_setting('fivem:config',json.dumps(config))
                self.store.set_setting('fivem:last',json.dumps(status))
                if old and old['channel'] != channel.id and old['message']:
                    try:
                        previous = await i.guild.fetch_channel(old['channel'])
                        message = await previous.fetch_message(old['message'])
                        await message.edit(content='Panoul FiveM a fost mutat în alt canal.',embed=None,view=None)
                    except discord.HTTPException:
                        log.warning('Panoul FiveM vechi nu a putut fi dezactivat; șterge-l manual.')
            await i.followup.send('Panou FiveM configurat. '+('Butonul de conectare este activ.' if connect else 'Adaugă linkul cfx.re prin aceeași comandă pentru a activa butonul; comanda F8 este afișată.'),ephemeral=True)

        @self.tree.command(name='fivem_status',description='Arată ultima verificare a serverului FiveM')
        @app_commands.guild_only()
        async def status(i:discord.Interaction):
            if i.guild_id != self.guild_id:
                raise ValueError('Server neautorizat.')
            config = self.fivem_config()
            saved = self.store.setting('fivem:last')
            if not config or not saved:
                raise ValueError('Un administrator trebuie să execute /fivem_setup mai întâi.')
            panel_channel = i.guild.get_channel(config['channel'])
            if panel_channel is None or not panel_channel.permissions_for(i.user).view_channel:
                raise ValueError('Nu ai acces la canalul panoului FiveM.')
            await i.response.send_message(embed=status_embed(json.loads(saved),config),view=connect_view(config),ephemeral=True)

        @self.tree.command(name='fivem_stop',description='Oprește actualizarea panoului FiveM, fără a opri serverul de joc')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def stop(i:discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.fivem_lock:
                config = self.fivem_config()
                self.store.set_setting('fivem:config','')
                if config and config['message']:
                    try:
                        channel = await i.guild.fetch_channel(config['channel'])
                        message = await channel.fetch_message(config['message'])
                        await message.edit(content='Actualizarea panoului FiveM a fost oprită de un administrator.',embed=None,view=None)
                    except discord.HTTPException:
                        log.warning('Panoul FiveM nu a putut fi editat după dezactivare.')
            await i.followup.send('Actualizarea panoului este oprită. Serverul FiveM nu a fost modificat.',ephemeral=True)

    def fivem_config(self):
        value = self.store.setting('fivem:config')
        return json.loads(value) if value else None

    async def check_fivem(self,endpoint):
        try:
            status = await fetch_status(endpoint)
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,UnicodeError,OSError):
            status = {'online':False}
        status['checked'] = datetime.datetime.now(datetime.timezone.utc).timestamp()
        return status

    async def fivem_update(self):
        async with self.fivem_lock:
            config = self.fivem_config()
            if not config or not config['message']:
                return
            status = await self.check_fivem(config['endpoint'])
            self.store.set_setting('fivem:last',json.dumps(status))
            guild = self.get_guild(self.guild_id)
            if guild is None:
                return
            try:
                channel = await guild.fetch_channel(config['channel'])
                message = await channel.fetch_message(config['message'])
                await message.edit(embed=status_embed(status,config),view=connect_view(config))
            except discord.NotFound:
                # Do not generate replacement messages every minute after deletion.
                config['message']=0
                self.store.set_setting('fivem:config',json.dumps(config))
                log.warning('Panoul FiveM a fost șters. Rulează /fivem_setup pentru a-l recrea.')

    @tasks.loop(minutes=1)
    async def fivem_poll(self):
        try:
            await self.fivem_update()
        except Exception:
            log.exception('Actualizarea panoului FiveM a eșuat; reîncercare în următorul minut.')

    @fivem_poll.before_loop
    async def before_fivem(self):
        await self.wait_until_ready()
