"""Helper recruitment, with a dedicated private review room and manual role grants."""
import json
import logging

import discord
from discord import app_commands

log=logging.getLogger('redfear')
QUESTIONS=[
    ('Nume în joc și ID',120,3),
    ('Experiență în moderare',900,10),
    ('Disponibilitate săptămânală',500,3),
    ('Cum gestionezi un conflict între jucători?',1000,20),
    ('De ce vrei să fii Helper?',900,10),
]
LABELS={'pending':'În așteptare','accepted':'Acceptată','rejected':'Respinsă','failed':'Trimitere eșuată'}


async def respond(i,text):
    if i.response.is_done():
        await i.followup.send(text,ephemeral=True)
    else:
        await i.response.send_message(text,ephemeral=True)


class HelperPanel(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=None)
        self.bot=bot

    @discord.ui.button(label='Aplică pentru Helper',emoji='📝',style=discord.ButtonStyle.success,custom_id='redfear:helper:apply:v1')
    async def apply(self,i,button):
        config=self.bot.helper_require_applicant(i,i.message.id)
        self.bot.store.helper_eligible(i.user.id,config['cooldown_days']*86400)
        await i.response.send_modal(HelperApplicationModal(self.bot,i.message.id))

    async def on_error(self,i,error,item):
        await self.bot.command_error(i,error)


class HelperApplicationModal(discord.ui.Modal,title='Aplică pentru Helper • RedFear'):
    def __init__(self,bot,panel_id):
        super().__init__()
        self.bot,self.panel_id=bot,panel_id
        for label,maximum,minimum in QUESTIONS:
            self.add_item(discord.ui.TextInput(label=label,style=discord.TextStyle.paragraph,
                                              min_length=minimum,max_length=maximum,required=True))

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True,thinking=True)
        answers=[{'question':question[0],'answer':str(item)} for question,item in zip(QUESTIONS,self.children)]
        await self.bot.submit_helper(i,self.panel_id,answers)

    async def on_error(self,i,error):
        await self.bot.command_error(i,error)


class HelperReview(discord.ui.View):
    def __init__(self,bot,disabled=False):
        super().__init__(timeout=None)
        self.bot=bot
        for button in self.children:
            button.disabled=disabled

    async def decide(self,i,status):
        row=self.bot.helper_require_reviewer(i)
        if row['status']!='pending':
            raise ValueError('Aplicația a fost deja evaluată.')
        if row['owner']==i.user.id:
            raise ValueError('Nu poți evalua propria aplicație.')
        await i.response.send_modal(HelperDecisionModal(self.bot,row['id'],i.message.id,status))

    @discord.ui.button(label='Acceptă',emoji='✅',style=discord.ButtonStyle.success,custom_id='redfear:helper:accept:v1')
    async def accept(self,i,button):
        await self.decide(i,'accepted')

    @discord.ui.button(label='Respinge',emoji='❌',style=discord.ButtonStyle.danger,custom_id='redfear:helper:reject:v1')
    async def reject(self,i,button):
        await self.decide(i,'rejected')

    async def on_error(self,i,error,item):
        await self.bot.command_error(i,error)


class HelperDecisionModal(discord.ui.Modal,title='Confirmă evaluarea aplicației'):
    reason=discord.ui.TextInput(label='Motiv transmis și candidatului',style=discord.TextStyle.paragraph,min_length=3,max_length=1000)
    def __init__(self,bot,number,message,status):
        super().__init__()
        self.bot,self.number,self.message,self.status=bot,number,message,status

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.bot.decide_helper(i,self.number,self.message,self.status,str(self.reason))

    async def on_error(self,i,error):
        await self.bot.command_error(i,error)


def application_embed(row):
    embed=discord.Embed(title=f'Aplicație Helper #{row["id"]} • {LABELS[row["status"]]}',
        description=f'Candidat: <@{row["owner"]}> • ID {row["owner"]}',
        color={'pending':0xF39C12,'accepted':0x2ECC71,'rejected':0xE74C3C,'failed':0x999999}[row['status']])
    for answer in json.loads(row['answers']):
        embed.add_field(name=answer['question'],value=answer['answer'],inline=False)
    if row['reason']:
        embed.description+=f'\nEvaluată de <@{row["reviewed_by"]}>'
        embed.add_field(name='Decizie și motiv',value=row['reason'],inline=False)
    embed.set_footer(text='Acceptarea nu acordă automat rolul • Date private pentru conducere')
    return embed


class Applications:
    def helper_config(self):
        value=self.store.setting('helper:config')
        return json.loads(value) if value else None

    def helper_overwrites(self,guild,role):
        if guild.default_role.permissions.administrator:
            raise ValueError('@everyone are Administrator; canalul nu poate fi privatizat.')
        access=discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)
        return {guild.default_role:discord.PermissionOverwrite(view_channel=False),role:access,
                guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)}

    async def helper_private_room(self,guild,channel_id,role_id):
        role=guild.get_role(role_id)
        if role is None or role.is_default() or role.is_bot_managed():
            raise ValueError('Rolul de conducere nu mai este valid. Rulează /helper_setup.')
        room=await guild.fetch_channel(channel_id)
        if not isinstance(room,discord.TextChannel):
            raise ValueError('Canalul privat de evaluare nu mai există.')
        # Dedicated room: replace all overrides, including grants to uncached members.
        # Never post answers before this operation succeeds.
        await room.edit(overwrites=self.helper_overwrites(guild,role),reason='Protejarea aplicațiilor Helper')
        return room

    def helper_require_applicant(self,i,panel_id):
        config=self.helper_config()
        if i.guild_id!=self.guild_id or not config or not config['enabled']:
            raise ValueError('Recrutarea pentru Helper este închisă momentan.')
        if i.user.bot or i.channel_id!=config['panel_channel'] or panel_id!=config['panel_message']:
            raise ValueError('Folosește panoul de recrutare curent.')
        channel=i.guild.get_channel(config['panel_channel'])
        if channel is None or not channel.permissions_for(i.user).view_channel:
            raise ValueError('Nu ai acces la panoul de recrutare.')
        return config

    def helper_require_reviewer(self,i,row=None,message_id=None):
        if i.guild_id!=self.guild_id or not isinstance(i.user,discord.Member):
            raise ValueError('Server sau evaluator neautorizat.')
        if row is None:
            row=self.store.helper_card(i.channel_id,i.message.id) if i.message else None
        if not row or i.channel_id!=row['review_channel'] or (message_id is not None and row['message']!=message_id):
            raise ValueError('Folosește cardul curent al aplicației din canalul privat.')
        if not i.user.guild_permissions.administrator and not any(r.id==row['reviewer_role'] for r in i.user.roles):
            raise ValueError('Numai conducerea poate evalua aplicațiile.')
        return row

    def register_applications(self):
        @self.tree.command(name='helper_setup',description='Configurează recrutarea și un canal dedicat privat pentru conducere')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def setup(i:discord.Interaction,panel_channel:discord.TextChannel,reviewer_role:discord.Role,
                        cooldown_days:app_commands.Range[int,1,90]=7):
            self.admin(i)
            if panel_channel.guild.id!=self.guild_id or reviewer_role.guild.id!=self.guild_id:
                raise ValueError('Alege canalul și rolul din serverul configurat.')
            if reviewer_role.is_default() or reviewer_role.is_bot_managed():
                raise ValueError('Alege rolul conducerii, nu @everyone sau rolul unui bot.')
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                old=self.helper_config()
                if old and panel_channel.id==old['review_channel']:
                    raise ValueError('Alege un canal public pentru panou, diferit de camera privată de evaluare.')
                room=None
                if old:
                    try:
                        room=await self.helper_private_room(i.guild,old['review_channel'],reviewer_role.id)
                    except discord.NotFound:
                        pass
                if room is None:
                    room=await i.guild.create_text_channel('aplicatii-helper',overwrites=self.helper_overwrites(i.guild,reviewer_role),reason='Canal privat de recrutare RedFear')
                # All cards in this dedicated room use its current role, including recovered cards.
                self.store.helper_reviewer_role(room.id,reviewer_role.id)
                embed=discord.Embed(title='📝 RedFear • Recrutare Helper',description='Vrei să ajuți comunitatea? Completează formularul de mai jos.\nRăspunsurile sunt private pentru conducere și administratori.\nNu trimite parole, tokenuri sau alte date sensibile.\nAcceptarea este urmată de discuție; rolul este acordat manual.',color=0xDC2626)
                embed.add_field(name='Reaplicare',value=f'O singură aplicație în așteptare. Reaplicare după {cooldown_days} zile de la decizie.')
                message=None
                if old and old['panel_channel']==panel_channel.id:
                    try:
                        message=await panel_channel.fetch_message(old['panel_message'])
                    except discord.NotFound:
                        pass
                if message:
                    await message.edit(embed=embed,view=HelperPanel(self))
                else:
                    message=await panel_channel.send(embed=embed,view=HelperPanel(self),allowed_mentions=discord.AllowedMentions.none())
                config=dict(panel_channel=panel_channel.id,panel_message=message.id,review_channel=room.id,
                            reviewer_role=reviewer_role.id,cooldown_days=cooldown_days,enabled=True)
                self.store.set_setting('helper:config',json.dumps(config))
                if old and old['panel_channel']!=panel_channel.id:
                    try:
                        previous=await i.guild.fetch_channel(old['panel_channel'])
                        prior=await previous.fetch_message(old['panel_message'])
                        await prior.edit(content='Panoul de recrutare a fost mutat.',embed=None,view=None)
                    except discord.HTTPException:
                        log.warning('Panoul vechi Helper nu a putut fi editat; butonul lui este invalidat.')
            await respond(i,f'Recrutare configurată. Evaluare privată în <#{room.id}> pentru {reviewer_role.mention} și administratori. Rolurile candidaților nu sunt modificate automat.')

        @self.tree.command(name='helper_toggle',description='Deschide sau închide primirea aplicațiilor pentru Helper')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def toggle(i:discord.Interaction,enabled:bool):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                config=self.helper_config()
                if not config:
                    raise ValueError('Rulează /helper_setup mai întâi.')
                config['enabled']=enabled
                self.store.set_setting('helper:config',json.dumps(config))
            await respond(i,'Recrutare '+('deschisă.' if enabled else 'închisă. Aplicațiile existente pot fi evaluate.'))

        @self.tree.command(name='helper_status',description='Arată privat starea ultimei tale aplicații pentru Helper')
        @app_commands.guild_only()
        async def status(i:discord.Interaction):
            if i.guild_id!=self.guild_id:
                raise ValueError('Server neautorizat.')
            row=self.store.helper_latest(i.user.id)
            if row is None:
                return await respond(i,'Nu ai trimis încă o aplicație pentru Helper.')
            text=f'Aplicația ta #{row["id"]}: **{LABELS[row["status"]]}**.'
            if row['reason']:
                text+='\nMotiv: '+row['reason']
            if row['status']=='accepted':
                text+='\nConducerea te va contacta; rolul se acordă manual.'
            if row['status']=='failed':
                text+='\nRăspunsurile nu au fost publicate. Poți încerca din nou după un minut; anunță administrația.'
            await respond(i,text)

        @self.tree.command(name='helper_republish',description='Reface cardul unei aplicații în canalul ei privat de evaluare')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def republish(i:discord.Interaction,application_id:app_commands.Range[int,1]):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                row=self.store.helper_application(application_id)
                if not row or row['status']=='failed':
                    raise ValueError('Aplicația nu există sau trimiterea inițială a eșuat.')
                room=await self.helper_private_room(i.guild,row['review_channel'],row['reviewer_role'])
                message=None
                if row['message']:
                    try:
                        message=await room.fetch_message(row['message'])
                    except discord.NotFound:
                        pass
                if message:
                    await message.edit(embed=application_embed(row),view=HelperReview(self,row['status']!='pending'))
                else:
                    message=await room.send(embed=application_embed(row),view=HelperReview(self,row['status']!='pending'),allowed_mentions=discord.AllowedMentions.none())
                    self.store.helper_set_message(row['id'],message.id)
            await respond(i,'Cardul aplicației a fost refăcut.')

        @self.tree.command(name='helper_resend',description='Reîncearcă notificarea DM pentru o aplicație deja evaluată')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def resend(i:discord.Interaction,application_id:app_commands.Range[int,1]):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                row=self.store.helper_application(application_id)
                if not row or row['status'] not in ('accepted','rejected') or row['dm_state']==1:
                    raise ValueError('Aplicație neevaluată sau rezultatul a fost deja trimis.')
                sent=await self.notify_helper(row)
            await respond(i,'DM trimis.' if sent else 'DM indisponibil. Candidatul poate folosi /helper_status.')

    async def submit_helper(self,i,panel_id,answers):
        async with self.applications_lock:
            config=self.helper_require_applicant(i,panel_id)
            self.store.helper_eligible(i.user.id,config['cooldown_days']*86400)
            if len(answers)!=len(QUESTIONS) or any(a.get('question')!=q[0] or not isinstance(a.get('answer'),str) or not q[2]<=len(a['answer'])<=q[1] for a,q in zip(answers,QUESTIONS)):
                raise ValueError('Formular incomplet sau răspunsuri invalide.')
            room=await self.helper_private_room(i.guild,config['review_channel'],config['reviewer_role'])
            number=self.store.helper_create(i.user.id,json.dumps(answers,ensure_ascii=False),room.id,config['reviewer_role'],config['cooldown_days']*86400)
            row=self.store.helper_application(number)
            try:
                message=await room.send(embed=application_embed(row),view=HelperReview(self),allowed_mentions=discord.AllowedMentions.none())
                self.store.helper_set_message(number,message.id)
            except Exception:
                self.store.helper_delivery_failed(number)
                raise
            await respond(i,f'Aplicația #{number} a fost trimisă conducerii. Vei primi rezultatul prin DM; poți verifica și cu /helper_status.')

    async def notify_helper(self,row):
        try:
            owner=await self.fetch_user(row['owner'])
            text=f'RedFear • Aplicația Helper #{row["id"]}: {LABELS[row["status"]]}.\nMotiv: {row["reason"]}'
            if row['status']=='accepted':
                text+='\nConducerea te va contacta pentru discuție. Rolul va fi acordat manual.'
            await owner.send(text,allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.store.helper_dm(row['id'],-1)
            log.info('DM Helper indisponibil pentru aplicația %s.',row['id'])
            return False
        self.store.helper_dm(row['id'],1)
        return True

    async def decide_helper(self,i,number,message_id,status,reason):
        async with self.applications_lock:
            row=self.store.helper_application(number)
            if row is None:
                raise ValueError('Aplicația nu există.')
            row=self.helper_require_reviewer(i,row,message_id)
            if row['owner']==i.user.id or row['status']!='pending':
                raise ValueError('Nu poți evalua propria aplicație sau aplicația este deja evaluată.')
            room=await self.helper_private_room(i.guild,row['review_channel'],row['reviewer_role'])
            row=self.store.helper_decide(number,i.user.id,status,reason)
            card_updated=True
            try:
                message=await room.fetch_message(row['message'])
                await message.edit(embed=application_embed(row),view=HelperReview(self,True))
            except discord.HTTPException:
                card_updated=False
                log.exception('Decizia Helper este salvată, dar cardul nu a putut fi editat')
            sent=await self.notify_helper(row)
            await respond(i,f'Decizie salvată: {LABELS[status]}. '+('Candidatul a primit DM.' if sent else 'DM indisponibil; candidatul poate folosi /helper_status.')+
                          ('' if card_updated else ' Refă cardul cu /helper_republish.')+
                          (' Rolul trebuie acordat manual după discuție.' if status=='accepted' else ''))
