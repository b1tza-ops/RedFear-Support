"""Staff workflows, feedback, health checks and persistent maintenance."""
import asyncio
import datetime
import logging
import math
import io
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

import discord
from discord import app_commands
from discord.ext import tasks

log = logging.getLogger('redfear')
PRIORITIES = {'normal': ('Normală', 0x3498DB), 'high': ('Ridicată', 0xF39C12), 'urgent': ('Urgentă', 0xE74C3C)}


async def respond(i, text):
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)


class TransferModal(discord.ui.Modal, title='Transferă tichetul'):
    target = discord.ui.TextInput(label='ID Discord moderator / helper', max_length=22)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        value = str(self.target).strip()
        if not value.isdecimal() or not 15 <= len(value) <= 22:
            raise ValueError('Introdu un ID Discord numeric valid.')
        await self.bot.transfer_ticket(i, int(value))

    async def on_error(self, i, error):
        await self.bot.command_error(i, error)


class RatingView(discord.ui.View):
    def __init__(self, bot, token):
        super().__init__(timeout=None)
        self.bot, self.token = bot, token
        select = discord.ui.Select(custom_id='redfear:rate:' + token, placeholder='Cum a fost ajutorul primit?',
                                   options=[discord.SelectOption(label=f'{score} ' + '⭐' * score, value=str(score)) for score in range(1,6)])
        select.callback = self.rate
        self.add_item(select)

    async def rate(self, i):
        # Token ties the rating to its owner AND to this particular closure cycle.
        score = int(i.data['values'][0])
        self.bot.store.rate(self.token, i.user.id, score)
        await i.response.edit_message(content=f'Mulțumim! Ai acordat {score}/5 stele.', view=None)
        self.stop()

    async def on_error(self, i, error, item):
        await self.bot.command_error(i, error)


def backup_database(source, directory, keep=7):
    """Separate SQLite connection: safe for a worker thread while the bot writes."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Director de backup nesigur.')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S-')
    fd, temporary = tempfile.mkstemp(prefix='.sqlite-', dir=directory)
    os.close(fd)
    try:
        with sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(temporary) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Verificarea backupului SQLite a eșuat.')
        filename = directory / (stamp + Path(temporary).name.removeprefix('.sqlite-') + '.sqlite3')
        os.replace(temporary, filename)
        os.chmod(filename, 0o600)
        # Only prune our own prefix/suffix, never unrelated files.
        backups = sorted(directory.glob('[0-9]' * 8 + 'T*.sqlite3'), key=lambda p:p.stat().st_mtime, reverse=True)
        for old in backups[max(1, keep):]:
            old.unlink()
        return filename
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Operations:
    def register_operations(self):
        @self.tree.command(name='ticket_health', description='Verifică permisiunile, arhiva, SQLite și backupul')
        @app_commands.guild_only()
        @app_commands.default_permissions()
        async def health(i: discord.Interaction):
            self.require_support(i)
            await i.response.defer(ephemeral=True, thinking=True)
            lines = await self.health_report(i.guild)
            await i.followup.send(file=discord.File(io.BytesIO('\n'.join(lines).encode()), filename='ticket-health.txt'),
                                  content='\n'.join(lines)[:1800], ephemeral=True)

        @self.tree.command(name='ticket_block', description='Suspendă crearea/redeschiderea tichetelor pentru abuz')
        @app_commands.guild_only()
        @app_commands.default_permissions()
        async def block(i: discord.Interaction, member: discord.Member, reason: str, hours: app_commands.Range[int,0,8760]=0):
            self.require_support(i)
            if member.bot or self.staff(member):
                raise ValueError('Nu poți bloca stafful sau boții prin această comandă.')
            if not 3 <= len(reason) <= 1000:
                raise ValueError('Motivul trebuie să aibă între 3 și 1000 de caractere.')
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.lock:
                self.store.block(member.id, i.user.id, reason, time.time()+hours*3600 if hours else None)
            await respond(i, f'Crearea/redeschiderea tichetelor a fost suspendată pentru {member.mention}. Durată: {str(hours)+" ore" if hours else "până la deblocare"}. Tichetele existente rămân accesibile.')

        @self.tree.command(name='ticket_unblock', description='Restabilește accesul unui membru la tichete')
        @app_commands.guild_only()
        @app_commands.default_permissions()
        async def unblock(i: discord.Interaction, member: discord.Member):
            self.require_support(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.lock:
                self.store.unblock(member.id,i.user.id)
            await respond(i, 'Accesul la crearea tichetelor a fost restabilit.')

        @self.tree.command(name='ticket_idle', description='Configurează orele de inactivitate; close_hours=0 oprește închiderea')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def idle(i: discord.Interaction, warn_hours: app_commands.Range[int,1,720]=24,
                       close_hours: app_commands.Range[int,0,1440]=0):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.lock:
                self.store.configure_idle(warn_hours*3600,close_hours*3600)
            await respond(i, f'Avertizare după {warn_hours} ore fără mesaje umane. ' +
                          (f'Închidere după {close_hours} ore de inactivitate, cu minimum {close_hours-warn_hours} ore după avertizare.' if close_hours else 'Închiderea automată este dezactivată.'))

        @self.tree.command(name='ticket_backup', description='Salvează acum un backup SQLite local')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def backup(i: discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.backup_lock:
                path = await self.make_backup()
            await respond(i, f'Backup local verificat: {path.name}. Nu este încărcat în Discord.')

    def require_support(self, i):
        if i.guild_id != self.guild_id or not self.staff(i.user):
            raise ValueError('Doar echipa de suport a serverului configurat.')

    async def release_ticket(self, i):
        await i.response.defer(ephemeral=True,thinking=True)
        async with self.lock:
            self.require_staff(i,'open')
            self.store.assign(i.channel_id,i.user.id,None,i.user.guild_permissions.administrator)
            await i.channel.send(f'🙋 Tichet eliberat de {i.user.mention}; poate fi preluat de alt coleg.')
        await respond(i,'Tichet eliberat.')

    async def transfer_ticket(self, i, target_id):
        async with self.lock:
            self.require_staff(i,'open')
            target = await i.guild.fetch_member(target_id)
            if not self.staff(target) or target.bot:
                raise ValueError('Destinatarul trebuie să fie un membru al echipei de suport.')
            self.store.assign(i.channel_id,i.user.id,target.id,i.user.guild_permissions.administrator)
            await i.channel.send(f'🔁 Tichet transferat de {i.user.mention} către {target.mention}.',
                                 allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=[target]))
        await respond(i,'Tichet transferat.')

    async def priority_ticket(self, i, priority):
        await i.response.defer(ephemeral=True,thinking=True)
        async with self.lock:
            self.require_staff(i,'open')
            if priority not in PRIORITIES:
                raise ValueError('Prioritate invalidă.')
            label,color = PRIORITIES[priority]
            topic = (i.channel.topic or '').split(' | prioritate=')[0][:900]
            await i.channel.edit(topic=f'{topic} | prioritate={priority}')
            self.store.set_priority(i.channel_id,priority,i.user.id)
            await i.channel.send(embed=discord.Embed(title='Prioritate: '+label,
                description=f'Actualizată de {i.user.mention}.',color=color))
        await respond(i,'Prioritate actualizată.')

    async def send_feedback(self, guild, channel_id):
        token = self.store.invite_feedback(channel_id)
        row = self.store.channel(channel_id)
        try:
            owner = await self.fetch_user(row['owner'])
            view = RatingView(self,token)
            await owner.send(f'Tichetul RedFear #{row["id"]:04d} a fost închis. Poți evalua ajutorul primit, o singură dată, în următoarele 30 de zile.', view=view)
        except discord.HTTPException:
            log.info('Feedback DM indisponibil pentru tichetul %s; închiderea a reușit.',channel_id)

    async def make_backup(self):
        path = await asyncio.to_thread(backup_database,self.database_path,self.data_path/'backups',7)
        self.last_backup = time.time()
        log.info('Backup SQLite verificat: %s',path.name)
        return path

    async def health_report(self, guild):
        lines = ['RedFear Support • diagnostic',f'Gateway: {round(self.latency*1000) if math.isfinite(self.latency) else "?"} ms']
        required = ['view_channel','manage_channels','manage_roles','send_messages','embed_links','attach_files','read_message_history']
        missing = [p for p in required if not getattr(guild.me.guild_permissions,p)]
        lines.append('Permisiuni server: '+(', '.join(missing) if missing else 'OK'))
        for kind in ('Ajutor','Reclamație','Contestație','Bug'):
            cid = int(self.store.setting('category:'+kind) or self.category_id)
            category = guild.get_channel(cid)
            if not isinstance(category,discord.CategoryChannel):
                lines.append(f'{kind}: categorie absentă ({cid})')
                continue
            permissions = category.permissions_for(guild.me)
            missing = [p for p in required if not getattr(permissions,p)]
            lines.append(f'{kind}: '+(', '.join(missing) if missing else 'OK'))
        try:
            archive = await self.archive_channel(guild,0)
            missing = [p for p in ('view_channel','send_messages','attach_files','read_message_history') if not getattr(archive.permissions_for(guild.me),p)]
            lines.append('Arhivă: '+(', '.join(missing) if missing else 'privată; permisiuni OK'))
        except (discord.HTTPException,ValueError) as error:
            lines.append('Arhivă: '+str(error))
        roles = [guild.get_role(r) for r in self.roles]
        absent = [str(r) for r in self.roles if guild.get_role(r) is None]
        lines.append('Roluri suport: '+(', '.join(absent)+' absente' if absent else 'OK'))
        ping = not absent and (guild.me.guild_permissions.mention_everyone or all(r.mentionable for r in roles))
        lines.append('Ping staff: '+('OK' if ping else 'rolurile nu sunt mentionable și botul nu are Mention Everyone'))
        lines.append('SQLite: '+self.store.db.execute('PRAGMA quick_check').fetchone()[0])
        lines.append('Director date scriibil: '+str(os.access(self.data_path,os.W_OK)))
        lines.append(f'Spațiu disponibil: {shutil.disk_usage(self.data_path).free//(1024*1024)} MB')
        lines.append('Ultimul backup: '+(datetime.datetime.fromtimestamp(self.last_backup,datetime.timezone.utc).isoformat() if self.last_backup else 'încă niciunul'))
        lines.append(f'Backup automat: la 6 ore, 7 copii locale. Avertizare: {int(self.store.setting("idle_warn") or 86400)//3600} ore; închidere: {int(self.store.setting("idle_close") or 0)//3600} ore (0=oprită).')
        return lines

    async def on_message(self, message):
        if message.author.bot or not message.guild or message.guild.id != self.guild_id:
            return
        async with self.lock:
            self.store.touch(message.channel.id,message.created_at.timestamp())

    async def idle_step(self, now=None):
        now = time.time() if now is None else now
        warn_seconds = int(self.store.setting('idle_warn') or 86400)
        close_seconds = int(self.store.setting('idle_close') or 0)
        guild = self.get_guild(self.guild_id)
        if guild is None:
            return
        for candidate in self.store.open_tickets():
            if now-candidate['last_activity'] < warn_seconds:
                continue
            try:
                async with self.lock:
                    row = self.store.channel(candidate['channel'])
                    if row['status'] != 'open':
                        continue
                    channel = await guild.fetch_channel(row['channel'])
                    # Reconcile human activity missed during downtime before any decision.
                    after = datetime.datetime.fromtimestamp(row['last_activity'],datetime.timezone.utc)
                    async for message in channel.history(after=after,limit=None,oldest_first=True):
                        if not message.author.bot:
                            self.store.touch(row['channel'],message.created_at.timestamp())
                    row = self.store.channel(row['channel'])
                    if now-row['last_activity'] < warn_seconds:
                        continue
                    if row['warned_at'] is None:
                        text = (f'⚠️ Tichet inactiv. Scrie un mesaj pentru a-l păstra activ. Închiderea automată poate avea loc peste cel puțin {(close_seconds-warn_seconds)//3600} ore.' if close_seconds else '⚠️ Tichet inactiv. Echipa poate verifica dacă mai ai nevoie de ajutor. Închiderea automată este dezactivată.')
                        await channel.send(f'<@{row["owner"]}> '+text,
                            allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=[discord.Object(id=row['owner'])]))
                        self.store.warn(row['channel'],now)
                    elif close_seconds and now-row['last_activity'] >= close_seconds and now-row['warned_at'] >= close_seconds-warn_seconds:
                        await self.close_locked(guild,channel,row,guild.me.id,'Închidere automată pentru inactivitate după avertizare.')
            except Exception:
                log.exception('Verificarea inactivității a eșuat pentru %s; tichetul va fi reîncercat.',candidate['channel'])

    @tasks.loop(minutes=1)
    async def maintenance(self):
        try:
            if time.time()-self.last_backup >= 6*3600:
                async with self.backup_lock:
                    if time.time()-self.last_backup >= 6*3600:
                        await self.make_backup()
        except Exception:
            log.exception('Backup automat eșuat; reîncercare în următorul minut.')
        try:
            await self.idle_step()
        except Exception:
            log.exception('Verificarea inactivității a eșuat; reîncercare în următorul minut.')

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.wait_until_ready()
