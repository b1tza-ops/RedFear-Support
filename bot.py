import asyncio
import io
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from store import Store

load_dotenv()
log = logging.getLogger('redfear')
KINDS = ['Ajutor', 'Reclamație', 'Contestație', 'Bug']

def number(name):
    value = int(os.environ[name])
    if value <= 0:
        raise ValueError(f'{name} trebuie să fie un ID pozitiv')
    return value

class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        log.exception('Interacțiune eșuată', exc_info=error)
        msg = 'Operația nu a reușit. Anunță un administrator; poți încerca din nou.'
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

class TicketModal(discord.ui.Modal, title='RedFear • Deschide tichet'):
    details = discord.ui.TextInput(label='Descrie problema', style=discord.TextStyle.paragraph, min_length=10, max_length=1800)
    def __init__(self, bot, kind):
        super().__init__()
        self.bot, self.kind = bot, kind
    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.open_ticket(i, self.kind, str(self.details))
        except Exception:
            log.exception('Creare tichet eșuată')
            await i.followup.send('Nu am putut crea tichetul. Contactează un administrator.', ephemeral=True)

class Panel(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
    @discord.ui.select(custom_id='redfear:open:v1', placeholder='🎫 Alege motivul tichetului', options=[discord.SelectOption(label=k, value=k) for k in KINDS])
    async def choose(self, i, select):
        if i.guild_id != self.bot.guild_id:
            return await i.response.send_message('Server neautorizat.', ephemeral=True)
        await i.response.send_modal(TicketModal(self.bot, select.values[0]))

class Confirm(SafeView):
    def __init__(self, bot, requester):
        super().__init__(timeout=60)
        self.bot, self.requester = bot, requester
    @discord.ui.button(label='Confirmă închiderea', style=discord.ButtonStyle.danger)
    async def yes(self, i, button):
        if i.user.id != self.requester:
            return await i.response.send_message('Confirmarea aparține altui utilizator.', ephemeral=True)
        await i.response.defer(ephemeral=True, thinking=True)
        await self.bot.close_ticket(i)

class Controls(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
    @discord.ui.button(label='Preia tichetul', emoji='🙋', custom_id='redfear:claim:v1')
    async def claim(self, i, button):
        row = self.bot.ticket(i)
        if not row or row['status'] != 'open' or not self.bot.staff(i.user):
            return await i.response.send_message('Doar echipa de suport poate prelua un tichet deschis.', ephemeral=True)
        if self.bot.store.claim(i.channel_id, i.user.id):
            await i.response.send_message(f'Tichet preluat de {i.user.mention}.', allowed_mentions=discord.AllowedMentions.none())
        else:
            await i.response.send_message('Tichetul este deja preluat.', ephemeral=True)
    @discord.ui.button(label='Închide', emoji='🔒', style=discord.ButtonStyle.danger, custom_id='redfear:close:v1')
    async def close(self, i, button):
        row = self.bot.ticket(i)
        if not row or (row['owner'] != i.user.id and not self.bot.staff(i.user)):
            return await i.response.send_message('Nu ai acces la această acțiune.', ephemeral=True)
        await i.response.send_message('Închidem tichetul și salvăm conversația? Canalul va rămâne ca arhivă pentru staff.', view=Confirm(self.bot, i.user.id), ephemeral=True)

class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.guild_id = number('GUILD_ID')
        self.category_id = number('TICKET_CATEGORY_ID')
        self.archive_id = number('ARCHIVE_CHANNEL_ID')
        self.roles = {int(x.strip()) for x in os.environ['SUPPORT_ROLE_IDS'].split(',')}
        if not self.roles or min(self.roles) <= 0 or self.guild_id in self.roles:
            raise ValueError('SUPPORT_ROLE_IDS trebuie să conțină roluri de staff, nu @everyone')
        data = Path(os.getenv('DATA_DIR', 'data'))
        data.mkdir(parents=True, exist_ok=True)
        self.store = Store(data / 'tickets.sqlite3')
        self.lock = asyncio.Lock()
        self.cooldowns = {}
        self.tree = app_commands.CommandTree(self)
        @self.tree.command(name='ticket_panel', description='Publică panoul de suport RedFear')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def panel(i: discord.Interaction):
            if i.guild_id != self.guild_id or not i.user.guild_permissions.administrator:
                return await i.response.send_message('Doar administratorii serverului configurat.', ephemeral=True)
            await i.response.defer(ephemeral=True)
            await i.channel.send(embed=discord.Embed(title='🎫 RedFear • Suport', description='Alege motivul de mai jos pentru un tichet privat.\nDescrie problema și atașează dovezi.\nAcces: tu, echipa de suport și administratorii.\nLa închidere, conversația este arhivată pentru staff.', color=0xDC2626), view=Panel(self))
            await i.followup.send('Panou publicat.', ephemeral=True)
    async def setup_hook(self):
        self.add_view(Panel(self))
        self.add_view(Controls(self))
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
    def staff(self, member):
        return isinstance(member, discord.Member) and (member.guild_permissions.administrator or any(r.id in self.roles for r in member.roles))
    def ticket(self, i):
        return self.store.channel(i.channel_id) if i.guild_id == self.guild_id else None
    async def on_ready(self):
        log.info('Conectat: %s; server %s', self.user, self.guild_id)
    async def open_ticket(self, i, kind, details):
        async with self.lock:
            active = self.store.active(i.user.id)
            if active:
                try:
                    await i.guild.fetch_channel(active['channel'])
                except discord.NotFound:
                    self.store.close(active['channel'])
                else:
                    return await i.followup.send(f'Ai deja un tichet: <#{active["channel"]}>', ephemeral=True)
            now = time.monotonic()
            if now - self.cooldowns.get(i.user.id, -1000) < 60:
                return await i.followup.send('Așteaptă un minut înainte de un tichet nou.', ephemeral=True)
            category = i.guild.get_channel(self.category_id)
            roles = [i.guild.get_role(r) for r in self.roles]
            if not isinstance(category, discord.CategoryChannel) or any(r is None for r in roles):
                raise ValueError('Categoria sau rolurile de suport nu există')
            access = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            overwrites = {i.guild.default_role: discord.PermissionOverwrite(view_channel=False), i.user: discord.PermissionOverwrite(**access), i.guild.me: discord.PermissionOverwrite(**access, manage_channels=True, manage_roles=True)}
            overwrites.update({r: discord.PermissionOverwrite(**access) for r in roles})
            # Explicit overwrites prevent public category permissions leaking into a ticket.
            channel = await i.guild.create_text_channel(f'tichet-{i.id}', category=category, overwrites=overwrites, topic=f'RedFear | autor={i.user.id} | tip={kind}', reason='Tichet de suport')
            try:
                self.store.create(i.user.id, channel.id, kind, time.time())
            except Exception:
                await channel.delete(reason='Creare în baza de date eșuată')
                raise
            self.cooldowns[i.user.id] = now
            await channel.send(content=i.user.mention, embed=discord.Embed(title=f'🎫 {kind}', description=details, color=0xDC2626), view=Controls(self))
            await i.followup.send(f'Tichet creat: {channel.mention}', ephemeral=True)
    async def close_ticket(self, i):
        async with self.lock:
            row = self.ticket(i)
            if not row or row['status'] != 'open' or (row['owner'] != i.user.id and not self.staff(i.user)):
                return await i.followup.send('Tichet închis sau acces refuzat.', ephemeral=True)
            archive = await i.guild.fetch_channel(self.archive_id)
            if not isinstance(archive, discord.TextChannel) or archive.id == i.channel_id:
                raise ValueError('Canal de arhivă invalid')
            # Fail closed if archive is visible to any ordinary role.
            if archive.permissions_for(i.guild.default_role).view_channel:
                raise ValueError('Arhiva trebuie să fie privată')
            for role in i.guild.roles:
                if role.id not in self.roles and not role.permissions.administrator and not role.is_bot_managed() and archive.permissions_for(role).view_channel:
                    raise ValueError('Arhiva este vizibilă unui rol fără acces de suport')
            # Freeze author writes before export, retaining an idempotent retry path.
            owner = await i.guild.fetch_member(row['owner'])
            overwrite = i.channel.overwrites_for(owner)
            overwrite.send_messages = False
            await i.channel.set_permissions(owner, overwrite=overwrite)
            parts, current = [], f'RedFear | tichet {i.channel_id} | autor {row["owner"]}\n'
            async for msg in i.channel.history(limit=None, oldest_first=True):
                line = f'\n[{msg.created_at.isoformat()}] {msg.author} ({msg.author.id})\n{msg.content}\n'
                line += '\n'.join(e.description or '' for e in msg.embeds)
                line += '\n' + '\n'.join(a.url for a in msg.attachments) + '\n'
                if len((current + line).encode()) > 1_000_000:
                    parts.append(current); current = ''
                current += line
            parts.append(current)
            for index, part in enumerate(parts, 1):
                await archive.send(f'Arhivă tichet {i.channel_id} • partea {index}/{len(parts)} • închis de {i.user.id}', file=discord.File(io.BytesIO(part.encode()), filename=f'tichet-{i.channel_id}-{index}.txt'))
            overwrite.view_channel = False
            await i.channel.set_permissions(owner, overwrite=overwrite)
            await i.channel.edit(name=f'inchis-{i.channel_id}')
            self.store.close(i.channel_id)
            await i.channel.send('🔒 Tichet închis. Conversația a fost salvată în arhiva privată.')
            await i.followup.send('Tichet închis și arhivat.', ephemeral=True)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    Bot().run(os.environ['DISCORD_TOKEN'])
