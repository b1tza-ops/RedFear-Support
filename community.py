"""Opt-in community tools: welcomes, notification roles and metadata-only logs."""
import json
import logging
from collections import deque

import discord
from discord import app_commands
from discord.ext import tasks

log = logging.getLogger('redfear')


async def reply(i, text):
    await i.followup.send(text, ephemeral=True)


class CommunityRoles(discord.ui.View):
    def __init__(self, bot, roles=None):
        super().__init__(timeout=None)
        self.bot = bot
        for slot in range(5):
            button = discord.ui.Button(label=(roles[slot].name[:80] if roles and slot < len(roles) else f'Rol {slot+1}'),
                custom_id=f'redfear:community:role:{slot}:v1', style=discord.ButtonStyle.secondary)
            async def callback(i, slot=slot):
                await i.response.defer(ephemeral=True, thinking=True)
                await self.bot.toggle_community_role(i, slot)
            button.callback = callback
            if roles is None or slot < len(roles):
                self.add_item(button)

    async def on_error(self, i, error, item):
        await self.bot.command_error(i, error)


class Community:
    def init_community(self):
        self.community_events = deque(maxlen=200)
        self.register_community()

    def community_config(self, key):
        raw = self.store.setting('community:' + key)
        return json.loads(raw) if raw else None

    def save_community(self, key, value):
        self.store.set_setting('community:' + key, json.dumps(value))

    def safe_community_role(self, guild, role):
        # These are notification/member labels, never moderation or private-access roles.
        helper = self.helper_config()
        protected = set(self.roles)
        if helper:
            protected.add(helper['reviewer_role'])
        if (role is None or role.guild.id != self.guild_id or role.is_default() or role.managed or role.id in protected
                or role.permissions.value != 0 or role >= guild.me.top_role):
            raise ValueError('Alege un rol simplu, fără permisiuni, nemanageriat, sub rolul botului; nu un rol de staff.')
        if not guild.me.guild_permissions.manage_roles:
            raise ValueError('Botul are nevoie de Manage Roles pe rolul său din server.')
        for channel in guild.channels:
            allow, _ = channel.overwrites_for(role).pair()
            if allow.value:
                raise ValueError('Rolul acordă acces sau permisiuni într-un canal. Folosește un rol numai pentru etichetă/notificări.')
        return role

    def community_log_overwrites(self, guild, role):
        if guild.default_role.permissions.administrator or role.is_default() or role.managed:
            raise ValueError('Alege un rol de conducere valid și elimină Administrator de pe @everyone.')
        return {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, embed_links=True)}

    async def community_log_room(self, guild, config):
        role = guild.get_role(config['role'])
        if role is None:
            raise ValueError('Rolul pentru loguri nu mai există. Rulează /community_logs.')
        room = await guild.fetch_channel(config['channel'])
        if not isinstance(room, discord.TextChannel):
            raise ValueError('Canal de loguri invalid.')
        await room.edit(overwrites=self.community_log_overwrites(guild, role), reason='Protejare loguri RedFear')
        return room

    def queue_community(self, guild_id, title, detail):
        if guild_id == self.guild_id and self.community_config('logs'):
            self.community_events.append((title[:100], detail[:900]))

    @tasks.loop(seconds=5)
    async def community_flush(self):
        async with self.community_lock:
            config = self.community_config('logs')
            guild = self.get_guild(self.guild_id)
            if not config or not guild or not self.community_events:
                return
            batch = list(self.community_events)[:5]
            try:
                room = await self.community_log_room(guild, config)
                embed = discord.Embed(title='RedFear • Activitate comunitate', color=0xDC2626, timestamp=discord.utils.utcnow())
                for title, detail in batch:
                    embed.add_field(name=title, value=detail, inline=False)
                await room.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.HTTPException, ValueError):
                log.exception('Loguri comunitate indisponibile; se reîncearcă, fără publicare în alt canal')
                return
            # Entries may arrive while network requests are running and the bounded queue may rotate.
            for entry in batch:
                if self.community_events and self.community_events[0] == entry:
                    self.community_events.popleft()

    @community_flush.before_loop
    async def before_community_flush(self):
        await self.wait_until_ready()

    async def on_member_join(self, member):
        if member.guild.id != self.guild_id:
            return
        self.queue_community(member.guild.id, 'Membru nou', f'ID: {member.id} • {discord.utils.escape_markdown(str(member))}')
        if member.bot:
            return
        config = self.community_config('welcome')
        if not config or not config['enabled']:
            return
        await self.community_autorole(member, config)
        try:
            room = member.guild.get_channel(config['channel'])
            if not isinstance(room, discord.TextChannel):
                raise ValueError('Canalul de bun venit nu există.')
            embed = discord.Embed(title='👋 Bine ai venit pe RedFear!', description=f'<@{member.id}>\n' + config['message'], color=0xDC2626)
            await room.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, ValueError):
            log.exception('Mesajul de bun venit nu a putut fi publicat')

    async def community_autorole(self, member, config):
        if not config['role'] or member.bot or member.pending:
            return
        try:
            role = self.safe_community_role(member.guild, member.guild.get_role(config['role']))
            if role not in member.roles:
                await member.add_roles(role, reason='Rol automat RedFear', atomic=True)
        except (discord.HTTPException, ValueError):
            log.exception('Rolul automat nu a fost acordat pentru membrul %s', member.id)

    async def on_member_remove(self, member):
        self.queue_community(member.guild.id, 'Membru plecat', f'ID: {member.id} • {discord.utils.escape_markdown(str(member))}')

    async def on_member_update(self, before, after):
        if after.guild.id != self.guild_id:
            return
        if getattr(before, 'pending', False) and not getattr(after, 'pending', False):
            config = self.community_config('welcome')
            if config and config['enabled']:
                await self.community_autorole(after, config)
        previous = {role.id for role in before.roles}
        current = {role.id for role in after.roles}
        if previous != current:
            added = ', '.join(str(x) for x in sorted(current-previous)) or '—'
            removed = ', '.join(str(x) for x in sorted(previous-current)) or '—'
            self.queue_community(after.guild.id, 'Roluri modificate', f'Membru ID: {after.id}\nAdăugate (ID): {added}\nEliminate (ID): {removed}')

    async def toggle_community_role(self, i, slot):
        async with self.community_lock:
            config = self.community_config('roles')
            if (i.guild_id != self.guild_id or not config or i.user.bot or not i.message
                    or i.channel_id != config['channel'] or i.message.id != config['message']
                    or slot not in range(len(config['roles']))):
                raise ValueError('Folosește panoul curent de roluri.')
            member = await i.guild.fetch_member(i.user.id)
            room = i.guild.get_channel(i.channel_id)
            if room is None or not room.permissions_for(member).view_channel:
                raise ValueError('Nu ai acces la panoul de roluri.')
            role = self.safe_community_role(i.guild, i.guild.get_role(config['roles'][slot]))
            if role in member.roles:
                await member.remove_roles(role, reason='Rol opțional eliminat de membru', atomic=True)
                text = f'Rol eliminat: {role.name}.'
            else:
                await member.add_roles(role, reason='Rol opțional ales de membru', atomic=True)
                text = f'Rol acordat: {role.name}.'
            await reply(i, text)

    def register_community(self):
        @self.tree.command(name='welcome_setup', description='Configurează mesajul de bun venit și un rol simplu automat')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def welcome(i: discord.Interaction, channel: discord.TextChannel,
                          message: app_commands.Range[str, 1, 1500], role: discord.Role = None):
            self.admin(i)
            if channel.guild.id != self.guild_id:
                raise ValueError('Alege un canal din serverul configurat.')
            if role:
                self.safe_community_role(i.guild, role)
            permissions = channel.permissions_for(i.guild.me)
            if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                raise ValueError('Botul trebuie să vadă canalul și să poată trimite mesaje/embeduri.')
            self.save_community('welcome', dict(channel=channel.id, message=message, role=role.id if role else None, enabled=True))
            await i.response.send_message('Bun venit configurat pentru membrii noi. Fără rol selectat, nu se acordă rol automat.', ephemeral=True)

        @self.tree.command(name='welcome_toggle', description='Activează/oprește bun venit și rolul automat')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def toggle(i: discord.Interaction, enabled: bool):
            self.admin(i)
            config = self.community_config('welcome')
            if not config:
                raise ValueError('Rulează /welcome_setup mai întâi.')
            config['enabled'] = enabled
            self.save_community('welcome', config)
            await i.response.send_message('Bun venit și rol automat: ' + ('pornite.' if enabled else 'oprite.'), ephemeral=True)

        @self.tree.command(name='welcome_test', description='Previzualizează privat mesajul de bun venit, fără acordare de rol')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def test(i: discord.Interaction):
            self.admin(i)
            config = self.community_config('welcome')
            if not config:
                raise ValueError('Rulează /welcome_setup mai întâi.')
            embed = discord.Embed(title='👋 Bine ai venit pe RedFear!', description=f'<@{i.user.id}>\n' + config['message'], color=0xDC2626)
            await i.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        @self.tree.command(name='roles_setup', description='Panou cu maximum cinci roluri simple opționale, fără permisiuni')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def roles(i: discord.Interaction, channel: discord.TextChannel, role1: discord.Role,
                        role2: discord.Role = None, role3: discord.Role = None, role4: discord.Role = None, role5: discord.Role = None):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.community_lock:
                selected = [r for r in (role1, role2, role3, role4, role5) if r]
                if channel.guild.id != self.guild_id or len({r.id for r in selected}) != len(selected):
                    raise ValueError('Alege un canal din server și roluri distincte.')
                for role in selected:
                    self.safe_community_role(i.guild, role)
                old = self.community_config('roles')
                message = None
                if old and old['channel'] == channel.id:
                    try:
                        message = await channel.fetch_message(old['message'])
                    except discord.NotFound:
                        pass
                embed = discord.Embed(title='🎭 RedFear • Roluri opționale', description='Apasă pentru a adăuga sau elimina un rol de notificări.\n' + '\n'.join(r.name for r in selected), color=0xDC2626)
                if message:
                    await message.edit(embed=embed, view=CommunityRoles(self, selected))
                else:
                    message = await channel.send(embed=embed, view=CommunityRoles(self, selected), allowed_mentions=discord.AllowedMentions.none())
                self.save_community('roles', dict(channel=channel.id, message=message.id, roles=[r.id for r in selected]))
                # Previous panels are invalidated by the stored message ID, even if they cannot be edited.
            await reply(i, 'Panoul de roluri este activ. Panourile vechi din alte canale nu mai funcționează.')

        @self.tree.command(name='community_logs', description='Creează/configurează camera privată pentru loguri de membri și roluri')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def logs(i: discord.Interaction, reviewer_role: discord.Role):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.community_lock:
                if reviewer_role.guild.id != self.guild_id:
                    raise ValueError('Alege un rol din serverul configurat.')
                overrides = self.community_log_overwrites(i.guild, reviewer_role)
                old = self.community_config('logs')
                room = None
                if old:
                    try:
                        room = await self.community_log_room(i.guild, dict(channel=old['channel'], role=reviewer_role.id))
                    except discord.NotFound:
                        pass
                if room is None:
                    room = await i.guild.create_text_channel('redfear-loguri', overwrites=overrides, reason='Loguri private RedFear')
                self.save_community('logs', dict(channel=room.id, role=reviewer_role.id))
                self.community_events.clear()
            await reply(i, f'Loguri private în <#{room.id}>: intrări, ieșiri și schimbări de roluri. Nu se copiază mesaje sau conversații private.')

        @self.tree.command(name='community_logs_stop', description='Oprește logurile noi; păstrează mesajele existente')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def stop(i: discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.community_lock:
                self.save_community('logs', None)
                self.community_events.clear()
            await reply(i, 'Logurile sunt oprite. Camera și istoricul existent sunt păstrate.')
