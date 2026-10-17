import asyncio
import io
import logging
import os
import re
import time
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from store import Store
from transcript import export_parts
from operations import Operations, TransferModal, RatingView, PRIORITIES
from fivem import FiveM
from community import Community, CommunityRoles
from server_setup import ServerSetup
from moderation import Moderation
from casino import Casino, BlackjackButton
from applications import Applications, HelperPanel, HelperReview

load_dotenv()
log = logging.getLogger('redfear')
KINDS = ['Ajutor', 'Reclamație', 'Contestație', 'Bug']
CATEGORY_NAMES = {'Ajutor': '🎫 AJUTOR', 'Reclamație': '🚨 RECLAMAȚII', 'Contestație': '⚖️ CONTESTAȚII', 'Bug': '🐛 BUGURI'}
QUESTIONS = {
    'Ajutor': [('Cu ce ai nevoie de ajutor?', 'Descrie problema și ce ai încercat.'), ('Numele tău în joc / ID', 'Opțional')],
    'Reclamație': [('Pe cine reclami? Nume / ID', 'Numele sau ID-ul persoanei'), ('Ce s-a întâmplat?', 'Descrie faptele, data și ora.'), ('Dovezi / linkuri', 'Poți atașa fișiere după deschiderea tichetului.')],
    'Contestație': [('Numele tău în joc / ID', 'Numele sau ID-ul contului sancționat'), ('Sancțiune și motivul contestației', 'Ce sancțiune ai primit și de ce o contești?'), ('Data / staff / dovezi', 'Informații suplimentare, dacă le ai.')],
    'Bug': [('Ce problemă ai găsit?', 'Comportament observat și comportament așteptat'), ('Pași pentru reproducere', 'Cum putem reproduce problema?'), ('Locație / oră / dovezi', 'Informații suplimentare, dacă le ai.')],
}
ACCESS = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)


def number(name):
    value = int(os.environ[name])
    if value <= 0:
        raise ValueError(f'{name} trebuie să fie un ID pozitiv')
    return value


async def reply(i, text):
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)


async def report(i, error):
    log.error('Interacțiune eșuată', exc_info=(type(error), error, error.__traceback__))
    if isinstance(error, discord.Forbidden):
        text = 'Botul nu are acces sau permisiuni suficiente. Verifică rolul botului și categoria/canalul.'
    elif isinstance(error, ValueError):
        text = str(error)
    else:
        text = 'Operația nu a reușit. Anunță un administrator și verifică logul botului.'
    await reply(i, text)


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await report(interaction, error)


class SafeModal(discord.ui.Modal):
    async def on_error(self, interaction, error):
        await report(interaction, error)


class TicketModal(SafeModal, title='RedFear • Deschide tichet'):
    def __init__(self, bot, kind):
        super().__init__()
        self.bot, self.kind = bot, kind
        for index, (label, hint) in enumerate(QUESTIONS[kind]):
            self.add_item(discord.ui.TextInput(label=label, placeholder=hint, style=discord.TextStyle.paragraph,
                                              required=index == 0 or (index == 1 and kind != 'Ajutor'),
                                              min_length=3, max_length=900))

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        details = '\n\n'.join(f'{item.label}\n{item.value}' for item in self.children if item.value)
        await self.bot.open_ticket(i, self.kind, details)


class Panel(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.select(custom_id='redfear:open:v1', placeholder='🎫 Alege motivul tichetului', options=[discord.SelectOption(label=k, value=k) for k in KINDS])
    async def choose(self, i, select):
        if i.guild_id != self.bot.guild_id:
            return await reply(i, 'Server neautorizat.')
        await i.response.send_modal(TicketModal(self.bot, select.values[0]))


class CloseModal(SafeModal, title='Confirmă închiderea tichetului'):
    reason = discord.ui.TextInput(label='Motiv / cum a fost rezolvat?', style=discord.TextStyle.paragraph, min_length=3, max_length=1000)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        await self.bot.close_ticket(i, str(self.reason))


class StaffModal(SafeModal):
    def __init__(self, bot, action):
        super().__init__(title={'add': 'Adaugă participant', 'remove': 'Elimină participant', 'rename': 'Redenumește tichetul'}[action])
        self.bot, self.action = bot, action
        self.value = discord.ui.TextInput(label='Nume nou' if action == 'rename' else 'ID Discord al membrului', max_length=70 if action == 'rename' else 22)
        self.add_item(self.value)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        await self.bot.manage_ticket(i, self.action, str(self.value))


class Controls(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='Preia tichetul', emoji='🙋', custom_id='redfear:claim:v1')
    async def claim(self, i, button):
        await i.response.defer(ephemeral=True, thinking=True)
        async with self.bot.lock:
            row = self.bot.require_staff(i, 'open')
            if self.bot.store.claim(row['channel'], i.user.id):
                await i.channel.send(f'Tichet preluat de {i.user.mention}.', allowed_mentions=discord.AllowedMentions.none())
                await reply(i, 'Ai preluat tichetul.')
            else:
                await reply(i, 'Tichetul este deja preluat.')

    @discord.ui.button(label='Închide', emoji='🔒', style=discord.ButtonStyle.danger, custom_id='redfear:close:v1')
    async def close(self, i, button):
        row = self.bot.ticket(i)
        if not row or row['status'] != 'open' or (row['owner'] != i.user.id and not self.bot.staff(i.user)):
            return await reply(i, 'Nu ai acces sau tichetul este deja închis.')
        await i.response.send_modal(CloseModal(self.bot))

    async def modal(self, i, action):
        self.bot.require_staff(i, 'open')
        await i.response.send_modal(StaffModal(self.bot, action))

    @discord.ui.button(label='Adaugă membru', custom_id='redfear:add:v2', row=1)
    async def add(self, i, button):
        await self.modal(i, 'add')

    @discord.ui.button(label='Elimină membru', custom_id='redfear:remove:v2', row=1)
    async def remove(self, i, button):
        await self.modal(i, 'remove')

    @discord.ui.button(label='Redenumește', custom_id='redfear:rename:v2', row=1)
    async def rename(self, i, button):
        await self.modal(i, 'rename')


    @discord.ui.button(label='Eliberează', custom_id='redfear:release:v3', row=2)
    async def release(self, i, button):
        await self.bot.release_ticket(i)

    @discord.ui.button(label='Transferă', custom_id='redfear:transfer:v3', row=2)
    async def transfer(self, i, button):
        self.bot.require_staff(i,'open')
        await i.response.send_modal(TransferModal(self.bot))

    @discord.ui.select(custom_id='redfear:priority:v3', placeholder='Prioritatea tichetului',
                       options=[discord.SelectOption(label=label,value=key) for key,(label,color) in PRIORITIES.items()], row=3)
    async def priority(self, i, select):
        await self.bot.priority_ticket(i,select.values[0])


class ClosedControls(SafeView):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='Redeschide tichetul', emoji='🔓', custom_id='redfear:reopen:v2', style=discord.ButtonStyle.success)
    async def reopen(self, i, button):
        self.bot.require_staff(i, 'closed')
        await i.response.defer(ephemeral=True, thinking=True)
        await self.bot.reopen_ticket(i)


class Bot(Casino, Moderation, ServerSetup, Community, Applications, FiveM, Operations, discord.Client):
    owner_user_id = 415585084213690378

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.guild_id = number('GUILD_ID')
        self.category_id = number('TICKET_CATEGORY_ID')
        self.archive_id = number('ARCHIVE_CHANNEL_ID')
        self.roles = {int(x.strip()) for x in os.environ['SUPPORT_ROLE_IDS'].split(',')}
        if not self.roles or min(self.roles) <= 0 or self.guild_id in self.roles:
            raise ValueError('SUPPORT_ROLE_IDS trebuie să conțină roluri de staff, nu @everyone')
        data = Path(os.getenv('DATA_DIR', 'data'))
        data.mkdir(parents=True, exist_ok=True)
        self.data_path = data
        self.database_path = data / 'tickets.sqlite3'
        self.store = Store(self.database_path)
        self.backup_lock = asyncio.Lock()
        self.fivem_lock = asyncio.Lock()
        self.applications_lock = asyncio.Lock()
        self.community_lock = asyncio.Lock()
        self.layout_lock = asyncio.Lock()
        self.moderation_lock = asyncio.Lock()
        self.casino_lock = asyncio.Lock()
        self.casino_recovered = []
        self.last_backup = max((p.stat().st_mtime for p in (data / 'backups').glob('[0-9]' * 8 + 'T*.sqlite3')), default=0)
        self.lock = asyncio.Lock()
        self.cooldowns = {}
        self.tree = app_commands.CommandTree(self)
        self.tree.error(self.command_error)
        self.register_commands()
        self.register_operations()
        self.register_fivem()
        self.register_applications()
        self.init_community()
        self.register_server_setup()
        self.register_moderation()
        self.register_casino()

    async def command_error(self, i, error):
        await report(i, getattr(error, 'original', error))

    def require_owner(self, i):
        if i.guild_id != self.guild_id or i.user.id != self.owner_user_id:
            raise ValueError('Comandă rezervată exclusiv proprietarului botului.')

    def manager(self, member, key):
        config = self.community_config('staff-roles') or {}
        return config.get(key) in {r.id for r in member.roles}

    def admin(self, i, scope=None):
        if i.guild_id != self.guild_id:
            raise ValueError('Server neautorizat.')
        if i.user.id == self.owner_user_id:
            return
        command = scope or getattr(getattr(i, 'command', None), 'name', None)
        allowed = {
            'welcome_test': 'discord', 'roles_setup': 'discord',
            'server_setup': 'discord', 'server_map': 'discord', 'roles_create_defaults': 'discord',
            'ticket_panel': 'support', 'ticket_health': 'support',
        }
        if command not in allowed or not self.manager(i.user, allowed[command]):
            raise ValueError('Comandă rezervată proprietarului sau managerului autorizat pentru această funcție.')

    def require_staff(self, i, status=None):
        row = self.ticket(i)
        if not row or not self.staff(i.user) or (status and row['status'] != status):
            raise ValueError('Acțiune rezervată staffului; verifică starea tichetului.')
        if status == 'open' and row['claimed'] is not None and row['claimed'] != i.user.id and not (i.user.guild_permissions.administrator or i.user.id == self.owner_user_id or self.manager(i.user, 'support')):
            raise ValueError('Tichet preluat de alt coleg. Numai acesta sau un administrator îl poate administra.')
        return row

    def register_commands(self):
        @self.tree.command(name='ticket_panel', description='Publică panoul de suport RedFear')
        @app_commands.guild_only()
        async def panel(i: discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            await i.channel.send(embed=discord.Embed(title='🎫 RedFear • Suport', description='Alege motivul pentru un tichet privat.\nCompletează formularul și atașează dovezi în tichet.\nAcces: tu, participanții adăugați, suportul și administratorii.\nLa închidere salvăm conversația în arhiva privată.', color=0xDC2626), view=Panel(self))
            await reply(i, 'Panou publicat.')

        @self.tree.command(name='ticket_categories', description='Creează și configurează cele patru categorii private de suport')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def categories(i: discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.lock:
                roles = self.support_roles(i.guild)
                for kind, name in CATEGORY_NAMES.items():
                    saved = self.store.setting('category:' + kind)
                    existing = i.guild.get_channel(int(saved)) if saved else None
                    if existing is not None:
                        if not isinstance(existing, discord.CategoryChannel):
                            raise ValueError('Configurație categorie invalidă.')
                        continue
                    # Never adopt an unrelated category by name. Retry reuses persisted IDs.
                    overwrites = self.overwrites(i.guild, roles)
                    category = await i.guild.create_category(name, overwrites=overwrites, reason='Configurare RedFear Support')
                    self.store.set_setting('category:' + kind, category.id)
            await reply(i, 'Categorii private configurate: Ajutor, Reclamații, Contestații și Buguri. Tichetele noi sunt rutate automat.')

        @self.tree.command(name='ticket_stats', description='Statistici tichete și activitatea echipei')
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_channels=True)
        async def stats(i: discord.Interaction):
            if i.guild_id != self.guild_id or not self.staff(i.user):
                raise ValueError('Doar echipa de suport.')
            counts, staff = self.store.stats()
            text = f"Deschise: {counts.get('open', 0)}\nÎnchise: {counts.get('closed', 0)}\nTotal: {sum(counts.values())}\n\nActivitate staff (preluări / închideri):\n"
            lines = [f"<@{r['actor']}> — {r['claims']} / {r['closes']}" for r in staff]
            # Attach full report to avoid silently dropping large teams.
            ratings = self.store.feedback_stats()
            lines += [f'Evaluări pentru {r["staff"] or "neatribuit"}: {r["average"]:.1f}/5 ({r["count"]} evaluări)' for r in ratings]
            report_text = text + '\n'.join(lines)
            await i.response.send_message(report_text[:1800] if len(report_text) <= 1800 else text + 'Raport complet atașat.',
                                          file=discord.File(io.BytesIO(report_text.encode()), filename='statistici.txt'), ephemeral=True)

        @self.tree.command(name='ticket_controls', description='Reafișează butoanele în tichete existente')
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_channels=True)
        async def controls(i: discord.Interaction):
            row = self.require_staff(i)
            await i.response.defer(ephemeral=True, thinking=True)
            await i.channel.send('Administrare tichet', view=Controls(self) if row['status'] == 'open' else ClosedControls(self))
            await reply(i, 'Butoane publicate.')

        @self.tree.command(name='ticket_archive_closed', description='Mută tichetele deja închise în categoria privată dedicată')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def archive_closed(i: discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            moved = missing = failed = 0
            async with self.lock:
                rows = self.store.db.execute("SELECT * FROM tickets WHERE status='closed' AND channel IS NOT NULL").fetchall()
                for row in rows:
                    try:
                        channel = await i.guild.fetch_channel(row['channel'])
                        if not isinstance(channel, discord.TextChannel) or channel.id == self.archive_id:
                            failed += 1
                            continue
                        await self.move_closed_ticket(i.guild, channel)
                        moved += 1
                    except discord.NotFound:
                        missing += 1
                    except Exception:
                        failed += 1
                        log.exception('Nu am putut muta tichetul închis %s', row['id'])
            await reply(i, f'Tichete închise organizate: {moved}. Canale inexistente: {missing}. Erori: {failed}. Nu s-au șters mesaje.')

    async def closed_ticket_category(self, guild, channel_id):
        channels = await guild.fetch_channels()
        saved = self.community_config('closed-categories') or []
        categories = {c.id: c for c in channels if isinstance(c, discord.CategoryChannel)}
        for number in reversed(saved):
            category = categories.get(number)
            count = sum(c.category_id == number and c.id != channel_id for c in channels if not isinstance(c, discord.CategoryChannel))
            if category and count < 50:
                return category
        suffix = f' {len(saved)+1}' if saved else ''
        category = await guild.create_category('🔒 TICHETE ÎNCHISE' + suffix,
            overwrites=self.overwrites(guild, self.support_roles(guild)), reason='Arhivă privată pentru canalele închise')
        saved.append(category.id)
        self.save_community('closed-categories', saved)
        return category

    async def move_closed_ticket(self, guild, channel):
        category = await self.closed_ticket_category(guild, channel.id)
        # Explicit staff-only permissions; never sync with a parent category.
        await channel.edit(category=category, sync_permissions=False,
            overwrites=self.overwrites(guild, self.support_roles(guild)), reason='Organizare tichet închis')

    async def setup_hook(self):
        self.casino_recovered = self.store.casino_recover()
        self.add_dynamic_items(BlackjackButton)
        for view in (Panel(self), Controls(self), ClosedControls(self), HelperPanel(self), HelperReview(self), CommunityRoles(self)):
            self.add_view(view)
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        for invite in self.store.pending_feedback():
            self.add_view(RatingView(self,invite['token']))
        self.maintenance.start()
        self.fivem_poll.start()
        self.community_flush.start()
        self.casino_poll.start()

    async def close(self):
        self.maintenance.cancel()
        self.fivem_poll.cancel()
        self.community_flush.cancel()
        self.casino_poll.cancel()
        await super().close()

    def staff(self, member):
        return isinstance(member, discord.Member) and (member.id == self.owner_user_id or member.guild_permissions.administrator or self.manager(member, 'support') or self.manager(member, 'helper') or self.manager(member, 'moderator') or any(r.id in self.roles for r in member.roles))

    def ticket(self, i):
        return self.store.channel(i.channel_id) if i.guild_id == self.guild_id else None

    def support_roles(self, guild):
        ids = set(self.roles)
        config = self.community_config('staff-roles') or {}
        for key in ('support', 'helper', 'moderator'):
            role_id = config.get(key)
            if role_id and guild.get_role(role_id):
                ids.add(role_id)
        roles = [guild.get_role(r) for r in ids]
        if any(r is None for r in roles):
            raise ValueError('Un rol de suport configurat nu mai există.')
        return roles

    def overwrites(self, guild, roles, owner=None, participants=()):
        # MANAGE_ROLES must stay inherited from the guild role: Discord forbids
        # non-admins explicitly granting that permission during channel creation.
        result = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                  guild.me: discord.PermissionOverwrite(**ACCESS, manage_channels=True)}
        result.update({r: discord.PermissionOverwrite(**ACCESS) for r in roles})
        for member in ([owner] if owner else []) + list(participants):
            result[member] = discord.PermissionOverwrite(**ACCESS)
        return result

    async def on_ready(self):
        log.info('Conectat: %s; server %s', self.user, self.guild_id)

    async def open_ticket(self, i, kind, details):
        if i.guild_id != self.guild_id or kind not in KINDS:
            raise ValueError('Server sau tip de tichet invalid.')
        async with self.lock:
            if self.store.blocked(i.user.id):
                raise ValueError('Accesul tău la crearea tichetelor este suspendat. Contactează administrația serverului.')
            active = self.store.active(i.user.id)
            if active:
                try:
                    await i.guild.fetch_channel(active['channel'])
                except discord.NotFound:
                    self.store.close(active['channel'], reason='Canal șters extern')
                else:
                    return await reply(i, f'Ai deja un tichet: <#{active["channel"]}>')
            now = time.monotonic()
            if now - self.cooldowns.get(i.user.id, -1000) < 60:
                return await reply(i, 'Așteaptă un minut înainte de un tichet nou.')
            category_id = int(self.store.setting('category:' + kind) or self.category_id)
            category = i.guild.get_channel(category_id)
            roles = self.support_roles(i.guild)
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError('Categoria nu există. Rulează /ticket_categories sau verifică TICKET_CATEGORY_ID.')
            number = self.store.next_number()
            channel = await i.guild.create_text_channel(f'tichet-{number:04d}', category=category,
                         overwrites=self.overwrites(i.guild, roles, i.user),
                         topic=f'RedFear | autor={i.user.id} | tip={kind}', reason='Tichet de suport')
            try:
                self.store.create(i.user.id, channel.id, kind, time.time(), number=number)
            except Exception:
                await channel.delete(reason='Creare în baza de date eșuată')
                raise
            self.cooldowns[i.user.id] = now
            mentions = ' '.join(r.mention for r in roles)
            await channel.send(content=f'{i.user.mention} {mentions}',
                               embed=discord.Embed(title=f'🎫 #{number:04d} • {kind}', description=details, color=0xDC2626),
                               view=Controls(self), allowed_mentions=discord.AllowedMentions(everyone=False, users=[i.user], roles=roles))
            await reply(i, f'Tichet creat: {channel.mention}')

    async def manage_ticket(self, i, action, value):
        async with self.lock:
            row = self.require_staff(i, 'open')
            if action == 'rename':
                name = re.sub(r'[^\w-]+', '-', value.lower(), flags=re.UNICODE).strip('-_')[:60]
                if not name:
                    raise ValueError('Introdu un nume valid.')
                await i.channel.edit(name=f'tichet-{row["id"]:04d}-{name}', reason=f'Redenumit de {i.user.id}')
                self.store.event(i.channel_id, 'rename', i.user.id, name)
                return await reply(i, 'Tichet redenumit.')
            if action not in ('add', 'remove') or not re.fullmatch(r'[1-9][0-9]{14,21}', value.strip()):
                raise ValueError('Introdu un ID Discord numeric valid.')
            member_id = int(value.strip())
            if member_id == row['owner']:
                raise ValueError('Autorul nu poate fi adăugat/eliminat prin acest buton.')
            member = await i.guild.fetch_member(member_id)
            if member.bot or self.staff(member):
                raise ValueError('Acest buton este pentru participanți obișnuiți, nu staff sau boți.')
            if action == 'remove' and member.id not in self.store.participants(i.channel_id):
                raise ValueError('Membrul nu a fost adăugat ca participant.')
            if action == 'add' and len(self.store.participants(i.channel_id)) >= 10:
                raise ValueError('Maximum 10 participanți suplimentari per tichet.')
            await i.channel.set_permissions(member, overwrite=discord.PermissionOverwrite(**ACCESS) if action == 'add' else None,
                                            reason=f'Participant modificat de {i.user.id}')
            self.store.participant(i.channel_id, member.id, action == 'add')
            self.store.event(i.channel_id, action, i.user.id, str(member.id))
            await i.channel.send(f'Participant {"adăugat" if action == "add" else "eliminat"}: {member.mention}.')
            await reply(i, 'Participant actualizat.')

    async def archive_channel(self, guild, current_id):
        archive = await guild.fetch_channel(self.archive_id)
        if not isinstance(archive, discord.TextChannel) or archive.id == current_id:
            raise ValueError('Canal de arhivă invalid.')
        for role in guild.roles:
            if role.id not in self.roles and not role.permissions.administrator and not (role.is_bot_managed() and role in guild.me.roles) and archive.permissions_for(role).view_channel:
                raise ValueError('Arhiva este vizibilă unui rol fără acces de suport.')
        for target, overwrite in archive.overwrites.items():
            if isinstance(target, discord.Member) and not target.bot and not self.staff(target) and archive.permissions_for(target).view_channel:
                raise ValueError('Arhiva permite accesul unui membru fără rol de suport.')
        return archive

    async def close_ticket(self, i, reason):
        async with self.lock:
            row = self.ticket(i)
            if not row or row['status'] != 'open' or (row['owner'] != i.user.id and not self.staff(i.user)):
                raise ValueError('Tichet închis sau acces refuzat.')
            if row['owner'] != i.user.id:
                self.require_staff(i,'open')
            await self.close_locked(i.guild,i.channel,row,i.user.id,reason)
            await reply(i, 'Tichet închis și arhivat.')

    async def close_locked(self, guild, channel, row, actor_id, reason):
        # Caller holds self.lock, whether closure comes from a human or maintenance.
        archive = await self.archive_channel(guild, channel.id)
        original = dict(channel.overwrites)
        frozen = dict(original)
        # Freeze every non-staff participant, including legacy member overwrites.
        for target, overwrite in original.items():
            if isinstance(target, discord.Member) and not target.bot and not self.staff(target):
                frozen[target] = discord.PermissionOverwrite.from_pair(*overwrite.pair())
                frozen[target].send_messages = False
                frozen[target].send_messages_in_threads = False
        try:
            await channel.edit(overwrites=frozen)
            summary = (f'Tichet #{row["id"]:04d} • canal {channel.id} • {row["kind"]}\n'
                       f'Autor: {row["owner"]}\nPreluat de: {row["claimed"]}\nPrioritate: {row["priority"]}\nÎnchis de: {actor_id}\nMotiv: {reason}\n'
                       'Exportul include mesajele existente. Linkurile atașamentelor pot expira.')
            index = 0
            async for part in export_parts(channel, summary):
                index += 1
                await archive.send(f'Arhivă #{row["id"]:04d} • partea {index} • autor {row["owner"]} • închis de {actor_id}',
                    file=discord.File(io.BytesIO(part), filename=f'tichet-{row["id"]:04d}-{index}.html'))
            # Closed channels retain only staff/bot access, not participants.
            closed_overwrites = self.overwrites(guild, self.support_roles(guild))
            await channel.edit(name=f'inchis-{row["id"]:04d}', overwrites=closed_overwrites)
            self.store.close(channel.id, actor_id, reason)
        except Exception:
            # Leave an open ticket usable if export/network fails. Partial archive
            # posts may remain; retries are explicitly labelled by their timestamps.
            try:
                await channel.edit(overwrites=original)
            except discord.HTTPException:
                log.exception('Nu am putut restaura permisiunile după export eșuat')
            raise
        try:
            await self.move_closed_ticket(guild, channel)
        except Exception:
            # Export and closure already succeeded; do not reopen on a cosmetic move failure.
            log.exception('Tichet închis și privat, dar mutarea în categoria de închise a eșuat')
        try:
            await channel.send('🔒 Tichet închis. Conversația a fost salvată în arhiva privată.', view=ClosedControls(self))
        except discord.HTTPException:
            log.exception('Închidere reușită, dar mesajul de confirmare nu a fost trimis')
        try:
            await self.send_feedback(guild,channel.id)
        except Exception:
            log.exception('Închidere reușită, dar invitația de feedback a eșuat')

    async def reopen_ticket(self, i):
        async with self.lock:
            row = self.require_staff(i, 'closed')
            if self.store.blocked(row['owner']):
                raise ValueError('Autorul are accesul la tichete suspendat; deblochează-l înainte de redeschidere.')
            if self.store.active(row['owner']):
                raise ValueError('Autorul are deja un tichet deschis. Închide-l înainte de redeschidere.')
            try:
                owner = await i.guild.fetch_member(row['owner'])
            except discord.NotFound:
                raise ValueError('Autorul nu mai este pe server; tichetul rămâne arhivat.')
            participants = []
            for member_id in self.store.participants(i.channel_id):
                try:
                    participants.append(await i.guild.fetch_member(member_id))
                except discord.NotFound:
                    continue
            old = dict(i.channel.overwrites)
            old_name = i.channel.name
            old_category = getattr(i.channel, 'category', None)
            category_id = int(self.store.setting('category:' + row['kind']) or self.category_id)
            category = i.guild.get_channel(category_id)
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError('Categoria pentru acest tip de tichet lipsește; tichetul rămâne închis.')
            await i.channel.edit(name=f'tichet-{row["id"]:04d}', category=category, sync_permissions=False, overwrites=self.overwrites(i.guild, self.support_roles(i.guild), owner, participants))
            try:
                self.store.reopen(i.channel_id, i.user.id)
            except Exception:
                await i.channel.edit(name=old_name, category=old_category, sync_permissions=False, overwrites=old)
                raise
            await reply(i, 'Tichet redeschis; preluarea a fost resetată.')
            await i.channel.send(f'🔓 Tichet redeschis de {i.user.mention}.', view=Controls(self))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    Bot().run(os.environ['DISCORD_TOKEN'])
