"""Previewed server cosmetics; existing channel permission overwrites are never synced."""
import json
import logging
import re
import unicodedata

import discord
from discord import app_commands

log = logging.getLogger('redfear')
GROUPS = {'info':'📌 INFORMAȚII','fivem':'🎮 FIVEM','support':'🎫 SUPORT','staff':'🔒 STAFF'}
# key, group, canonical name, topic, aliases, read-only when newly created
CHANNELS = [
    ('welcome','info','👋・bun-venit','Bine ai venit în comunitatea RedFear România!',('bun-venit',),True),
    ('rules','info','📜・regulament','Regulamentul comunității RedFear România.',('regulament',),True),
    ('announcements','info','📢・anunturi','Anunțurile oficiale ale comunității.',('anunturi',),True),
    ('roles','info','🎭・roluri','Alege notificările pe care dorești să le primești.',('roluri',),True),
    ('status','fivem','🟢・server-status','Starea serverului FiveM și conectare.',('server-status',),True),
    ('connect','fivem','🔗・cum-te-conectezi','Ghid de conectare la RedFear FiveM.',('cum-te-conectezi',),True),
    ('updates','fivem','🔄・actualizari','Noutăți și modificări ale serverului FiveM.',('actualizari',),True),
    ('help','support','🆘・ajutor','Deschide un tichet pentru ajutor din partea echipei.',('ajutor',),False),
    ('applications','support','📝・aplicatii-staff','Aplică pentru un loc în echipa RedFear.',('aplicatii-staff',),True),
    ('reviews','staff','📝・aplicatii-helper',None,('aplicatii-helper',),True),
    ('logs','staff','📋・redfear-loguri',None,('redfear-loguri',),True),
]
DEFAULT_ROLES = [('updates','📢 Update-uri',('update-uri','actualizari')),('events','🎉 Evenimente',('evenimente',))]


def normalized(name):
    text = ''.join(c for c in unicodedata.normalize('NFKD',name.lower()) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+','-',text).strip('-')


def snapshot(channel):
    # Compare raw overwrites as well, including targets absent from the member cache.
    permissions = sorted((o.id,o.type,o.allow,o.deny) for o in channel._overwrites)
    return (channel.name,channel.category_id,channel.topic,permissions)


class LayoutPreview(discord.ui.View):
    def __init__(self,bot,owner,plan):
        super().__init__(timeout=180)
        self.bot,self.owner,self.plan = bot,owner,plan
        self.used=False

    @discord.ui.button(label='Aplică organizarea',style=discord.ButtonStyle.success)
    async def apply(self,i,button):
        self.bot.admin(i, scope="server_setup")
        if i.user.id != self.owner or self.used:
            raise ValueError('Previzualizarea nu îți aparține sau a fost deja folosită. Rulează /server_setup.')
        await i.response.defer(ephemeral=True,thinking=True)
        async with self.bot.layout_lock:
            if self.used:
                raise ValueError('Previzualizarea a fost deja folosită.')
            self.used=True
            await self.bot.apply_server_layout(i.guild,self.plan)
        button.disabled=True
        await i.edit_original_response(view=self)
        await i.followup.send('Organizare aplicată. Mesajele și permisiunile canalelor existente au fost păstrate. Configurările botului continuă să folosească aceleași ID-uri.',ephemeral=True)

    async def on_error(self,i,error,item):
        await self.bot.command_error(i,error)


class ServerSetup:
    def protected_layout_channels(self):
        ids = {self.archive_id,self.category_id}
        ids.update((self.community_config('staff-applications') or {}).get('rooms', {}).values())
        ids.update(self.community_config('closed-categories') or [])
        ids.update(row[0] for row in self.store.db.execute('SELECT channel FROM tickets WHERE channel IS NOT NULL'))
        for row in self.store.db.execute("SELECT value FROM settings WHERE key LIKE 'category:%'"):
            ids.add(int(row[0]))
        return ids

    async def build_server_layout(self,guild,create_missing=False):
        channels = await guild.fetch_channels()
        by_id = {ch.id:ch for ch in channels}
        mappings = self.community_config('layout-map') or {}
        saved = self.community_config('layout-channels') or {}
        helper = self.helper_config() or {}
        config_ids = {}
        for key,config,field in [('welcome',self.community_config('welcome'),'channel'),
                                 ('roles',self.community_config('roles'),'channel'),
                                 ('status',self.fivem_config(),'channel'),
                                 ('applications',helper,'panel_channel'),('reviews',helper,'review_channel'),
                                 ('logs',self.community_config('logs'),'channel')]:
            if config:
                config_ids[key]=config[field]
        protected = self.protected_layout_channels()
        entries=[]; used=set()
        for key,group,name,topic,aliases,readonly in CHANNELS:
            number = mappings.get(key) or config_ids.get(key) or saved.get(key)
            if number:
                ch=by_id.get(number)
                if ch is None:
                    raise ValueError(f'Canalul salvat pentru {key} nu mai există. Reconfigurează /server_map sau funcția asociată.')
            else:
                matches=[c for c in channels if isinstance(c,discord.TextChannel) and normalized(c.name) in aliases]
                if len(matches)>1:
                    raise ValueError(f'Mai multe canale pentru {key}. Alege explicit cu /server_map.')
                ch=matches[0] if matches else None
            if ch and (not isinstance(ch,discord.TextChannel) or ch.id in protected or ch.id in used):
                raise ValueError(f'Canal protejat, duplicat sau tip invalid pentru {key}. Nu poate fi reorganizat.')
            # Private review/log rooms belong to their owning features; never adopt by name alone.
            if group=='staff' and (not ch or ch.id != config_ids.get(key)):
                continue
            if ch is None and (not create_missing or group=='staff'):
                continue
            if ch:
                used.add(ch.id)
            entries.append(dict(key=key,group=group,name=name,topic=topic,readonly=readonly,
                                id=ch.id if ch else None,before=snapshot(ch) if ch else None))
        if not entries:
            raise ValueError('Nu există canale potrivite. Folosește /server_map sau create_missing:True.')
        categories={}
        saved_categories=self.community_config('layout-categories') or {}
        for group in dict.fromkeys(e['group'] for e in entries):
            ch=by_id.get(saved_categories.get(group))
            if ch is None:
                matches=[c for c in channels if isinstance(c,discord.CategoryChannel) and normalized(c.name)==normalized(GROUPS[group]) and c.id not in protected]
                if len(matches)>1:
                    raise ValueError(f'Categorii duplicate pentru {GROUPS[group]}; rezolvă duplicatul înainte de configurare.')
                ch=matches[0] if matches else None
            if ch and (not isinstance(ch,discord.CategoryChannel) or ch.id in protected):
                raise ValueError('Categorie protejată sau invalidă.')
            categories[group]=dict(id=ch.id if ch else None,name=ch.name if ch else None)
        return dict(entries=entries,categories=categories)

    async def apply_server_layout(self,guild,plan):
        if not guild.me.guild_permissions.manage_channels:
            raise ValueError('Botul are nevoie de Manage Channels.')
        fresh={c.id:c for c in await guild.fetch_channels()}
        protected=self.protected_layout_channels()
        # Validate the entire approved snapshot before changing anything.
        for item in plan['entries']:
            if item['id']:
                ch=fresh.get(item['id'])
                if not isinstance(ch,discord.TextChannel) or ch.id in protected or snapshot(ch)!=item['before']:
                    raise ValueError('Un canal s-a schimbat după previzualizare. Rulează /server_setup din nou.')
        for cat in plan['categories'].values():
            if cat['id'] and (cat['id'] not in fresh or not isinstance(fresh[cat['id']],discord.CategoryChannel) or fresh[cat['id']].name!=cat['name']):
                raise ValueError('O categorie s-a schimbat; regenerează previzualizarea.')
        categories=self.community_config('layout-categories') or {}
        saved=self.community_config('layout-channels') or {}
        # A different preview may already have created these resources. Do not duplicate them.
        for group,cat in plan['categories'].items():
            if cat['id'] is None and categories.get(group) in fresh:
                raise ValueError('Altă configurare a creat deja categoria. Rulează /server_setup din nou.')
        for item in plan['entries']:
            if item['id'] is None and saved.get(item['key']) in fresh:
                raise ValueError('Canal deja creat de altă configurare. Regenerează previzualizarea.')
        for group,cat in plan['categories'].items():
            if cat['id']:
                category=fresh[cat['id']]
            else:
                # Private parent, while child-specific access remains untouched during moves.
                overrides={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                           guild.me:discord.PermissionOverwrite(view_channel=True,manage_channels=True)}
                category=await guild.create_category(GROUPS[group],overwrites=overrides if group=='staff' else {},reason='Organizare RedFear aprobată')
            categories[group]=category.id
            fresh[category.id]=category
            self.save_community('layout-categories',categories)
        completed=[]
        try:
            for item in plan['entries']:
                category=fresh[categories[item['group']]]
                if item['id']:
                    ch=fresh[item['id']]
                    options=dict(name=item['name'],category=category,sync_permissions=False,reason='Organizare RedFear aprobată')
                    if item['topic'] is not None:
                        options['topic']=item['topic']
                    # Omitting overwrites preserves all server-side grants/denies, even uncached members.
                    await ch.edit(**options)
                else:
                    overrides={guild.default_role:discord.PermissionOverwrite(view_channel=True,send_messages=not item['readonly']),
                               guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)}
                    ch=await guild.create_text_channel(item['name'],category=category,topic=item['topic'],overwrites=overrides,reason='Canal public RedFear aprobat')
                saved[item['key']]=ch.id
                self.save_community('layout-channels',saved)
                completed.append(ch.id)
        except Exception:
            log.exception('Organizare parțială RedFear; canale finalizate: %s',completed)
            raise ValueError('Organizare oprită după o eroare; unele categorii/canale pot fi deja actualizate. Rulează /server_setup din nou pentru continuare. Nu s-au șters mesaje.')

    async def staff_plan(self, guild):
        bot = await guild.fetch_member(guild.me.id)
        if not bot.guild_permissions.manage_roles:
            raise ValueError('Botul are nevoie de Manage Roles; pune rolul botului deasupra staffului.')
        existing = await guild.fetch_roles()
        config = self.community_config('staff-roles') or {}
        moderation = self.moderation_config() or {}
        plan = []
        for key, name, color in STAFF_ROLES:
            rid = config.get(key) or (moderation.get(key) if key in ('helper', 'moderator') else None)
            if rid:
                matches = [r for r in existing if r.id == rid]
                if not matches:
                    raise ValueError('Un rol salvat lipsește. Verifică rolurile înainte de configurare.')
            else:
                matches = [r for r in existing if normalized(r.name) == normalized(name)]
            if len(matches) > 1:
                raise ValueError('Există roluri cu nume duplicate: ' + name)
            role = matches[0] if matches else None
            if role and (role.managed or role.is_default() or role >= bot.top_role):
                raise ValueError('Rol invalid sau deasupra botului: ' + role.name)
            if role and key in ('discord', 'support', 'moderation') and role.permissions.value:
                raise ValueError('Rolul de manager existent are permisiuni Discord. Verifică-l manual: ' + role.name)
            plan.append((key, role.name if role else name, color, role.id if role else None,
                         role.permissions.value if role else 0))
        if len({row[3] for row in plan if row[3]}) != sum(bool(row[3]) for row in plan):
            raise ValueError('Fiecare funcție trebuie să folosească un rol distinct.')
        return plan

    def register_server_setup(self):
        @self.tree.command(name='staff_setup', description='Doar proprietarul: previzualizează și creează rolurile de staff')
        @app_commands.guild_only()
        async def staff_setup(i: discord.Interaction):
            self.require_owner(i)
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.layout_lock:
                plan = await self.staff_plan(i.guild)
            lines = [f'{name}: ' + (f'reutilizat <@&{rid}>; permisiuni păstrate ({permissions})' if rid else 'nou, fără permisiuni Discord')
                     for key, name, color, rid, permissions in plan]
            await i.followup.send('\n'.join(lines) + '\n\nNu se atribuie roluri membrilor. Confirmarea expiră în 3 minute. Ban/kick sunt disponibile și Managerilor Discord/Suport. Unban/purge și configurările de acces sunt rezervate proprietarului.',
                                  view=StaffPreview(self, plan), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        @self.tree.command(name='server_map',description='Alege explicit un canal existent pentru organizare')
        @app_commands.guild_only()
        @app_commands.choices(destination=[app_commands.Choice(name=key,value=key) for key,*_ in CHANNELS if key not in ('reviews','logs')])
        async def mapping(i:discord.Interaction,destination:app_commands.Choice[str],channel:discord.TextChannel):
            self.admin(i)
            if channel.guild.id!=self.guild_id or channel.id in self.protected_layout_channels():
                raise ValueError('Canal protejat sau din alt server.')
            data=self.community_config('layout-map') or {}
            data[destination.value]=channel.id
            self.save_community('layout-map',data)
            await i.response.send_message('Alegere salvată. Vezi și confirmă planul prin /server_setup.',ephemeral=True)

        @self.tree.command(name='server_setup',description='Previzualizează organizarea canalelor; aplicarea cere butonul de confirmare')
        @app_commands.guild_only()
        async def setup(i:discord.Interaction,create_missing:bool=False):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            plan=await self.build_server_layout(i.guild,create_missing)
            lines=[]
            for group,cat in plan['categories'].items():
                lines.append(f'**{GROUPS[group]}** — '+(f'categorie existentă <#{cat["id"]}>' if cat['id'] else 'categorie nouă'))
                for item in plan['entries']:
                    if item['group']==group:
                        lines.append((f'<#{item["id"]}>' if item['id'] else '**NOU PUBLIC**')+' → '+item['name']+(' + descriere' if item['topic'] else ''))
            text='\n'.join(lines)+'\n\nCanalele existente păstrează mesajele și permisiunile. Fără ștergeri. Descrierile indicate vor fi înlocuite. Canalele noi nu configurează automat panourile botului. Confirmarea expiră în 3 minute.'
            await i.followup.send(text,view=LayoutPreview(self,i.user.id,plan),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

        @self.tree.command(name='roles_create_defaults',description='Creează/reutilizează roluri simple pentru Evenimente și Update-uri și publică panoul')
        @app_commands.guild_only()
        async def defaults(i:discord.Interaction,channel:discord.TextChannel):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.community_lock:
                if channel.guild.id!=self.guild_id or not i.guild.me.guild_permissions.manage_roles:
                    raise ValueError('Canal din alt server sau lipsește Manage Roles pentru bot.')
                permissions=channel.permissions_for(i.guild.me)
                if not (permissions.view_channel and permissions.send_messages and permissions.embed_links and permissions.read_message_history):
                    raise ValueError('Botul are nevoie de View Channel, Send Messages, Embed Links și Read Message History.')
                saved=self.community_config('default-roles') or {}
                selected=[]
                # Fetch current roles, validate existing candidates before creating anything.
                roles=await i.guild.fetch_roles()
                candidates=[]
                for key,name,aliases in DEFAULT_ROLES:
                    matches=[r for r in roles if r.id==saved.get(key)]
                    if not matches:
                        matches=[r for r in roles if normalized(r.name) in aliases]
                    if len(matches)>1:
                        raise ValueError('Există roluri duplicate. Alege rolurile explicit cu /roles_setup.')
                    role=matches[0] if matches else None
                    if role:
                        self.safe_community_role(i.guild,role)
                    candidates.append((key,name,role))
                for key,name,role in candidates:
                    if role is None:
                        role=await i.guild.create_role(name=name,permissions=discord.Permissions.none(),mentionable=False,hoist=False,reason='Rol notificări RedFear')
                        saved[key]=role.id
                        self.save_community('default-roles',saved)
                    self.safe_community_role(i.guild,role)
                    selected.append(role)
                await self.publish_community_roles(channel,selected)
            await i.followup.send('Rolurile de notificări sunt pregătite și panoul este publicat. Helper/Moderator nu au fost modificate.',ephemeral=True)


STAFF_ROLES = (
    ('discord', '🛠️ Manager Discord', 0x5865F2),
    ('support', '🎫 Manager Suport', 0x3498DB),
    ('moderation', '🛡️ Manager Moderare', 0xE74C3C),
    ('helper', '🟢 Helper', 0x2ECC71),
    ('moderator', '🟠 Moderator', 0xE67E22),
)


class StaffPreview(discord.ui.View):
    def __init__(self, bot, plan):
        import time
        super().__init__(timeout=180)
        self.bot, self.plan, self.used = bot, plan, False
        self.expires = time.monotonic() + 180

    @discord.ui.button(label='Confirmă rolurile', style=discord.ButtonStyle.success)
    async def apply(self, i, button):
        import time
        self.bot.require_owner(i)
        await i.response.defer(ephemeral=True, thinking=True)
        async with self.bot.layout_lock:
            if self.used or time.monotonic() >= self.expires:
                raise ValueError('Confirmare expirată sau deja utilizată. Rulează /staff_setup.')
            self.used = True
            bot = await i.guild.fetch_member(i.guild.me.id)
            if not bot.guild_permissions.manage_roles:
                raise ValueError('Botul are nevoie de Manage Roles.')
            roles = {r.id: r for r in await i.guild.fetch_roles()}
            # Validate every reused role before creating anything.
            for key, name, color, rid, permissions in self.plan:
                if rid:
                    role = roles.get(rid)
                    if (not role or role.managed or role.is_default() or role >= bot.top_role
                            or role.permissions.value != permissions or role.name != name):
                        raise ValueError('Rolurile s-au schimbat. Rulează /staff_setup din nou.')
                elif any(normalized(r.name) == normalized(name) for r in roles.values()):
                    raise ValueError('A apărut un rol cu același nume. Rulează /staff_setup din nou.')
            config = self.bot.community_config('staff-roles') or {}
            for key, name, color, rid, permissions in self.plan:
                if not rid:
                    role = await i.guild.create_role(name=name, permissions=discord.Permissions.none(),
                                                    colour=discord.Colour(color), mentionable=False,
                                                    reason='Configurare staff de către proprietarul RedFear')
                    rid = role.id
                config[key] = rid
                # Persist each result so retries reuse roles after a partial failure.
                self.bot.save_community('staff-roles', config)
            self.bot.save_community('moderation', dict(helper=config['helper'], moderator=config['moderator']))
        button.disabled = True
        await i.edit_original_response(view=self)
        await i.followup.send('Roluri configurate. Nu s-au atribuit roluri membrilor și nu s-au modificat permisiunile rolurilor existente. Manager Suport este recunoscut pentru tichetele noi; accesul la cele existente se configurează separat.', ephemeral=True)

    async def on_error(self, i, error, item):
        await self.bot.command_error(i, error)
