"""Explicit staff moderation, durable cases and fresh authorization at execution."""
import asyncio
import datetime
import io
import logging
import time

import discord
from discord import app_commands

log = logging.getLogger('redfear')
PERMISSIONS = {'timeout':'moderate_members','untimeout':'moderate_members','kick':'kick_members','ban':'ban_members','unban':'ban_members'}
LABELS = {'warn':'Avertisment','timeout':'Timeout','untimeout':'Eliminare timeout','kick':'Kick','ban':'Ban','unban':'Unban','warn_revoke':'Avertisment retras','purge':'Ștergere mesaje'}
STATES = {'revoked':'Retras — păstrat pentru audit','partial':'Aplicat parțial','pending':'În curs / verifică în Discord','applied':'Aplicat','failed':'Refuzat de Discord','uncertain':'Rezultat incert — verifică în Discord'}


def case_embed(row):
    embed=discord.Embed(title=f'RedFear • Caz #{row["id"]} • {LABELS[row["action"]]}',color=0xDC2626,
                        timestamp=datetime.datetime.fromtimestamp(row['created'],datetime.timezone.utc))
    if row['action']=='purge':
        embed.add_field(name='Canal',value=f'<#{row["target"]}>\nID: {row["target"]}')
    else:
        embed.add_field(name='Membru',value=f'<@{row["target"]}>\nID: {row["target"]}')
    embed.add_field(name='Staff',value=f'<@{row["actor"]}>\nID: {row["actor"]}')
    embed.add_field(name='Stare',value=STATES[row['status']],inline=False)
    embed.add_field(name='Motiv',value=discord.utils.escape_markdown(row['reason']),inline=False)
    if row['until_time']:
        embed.add_field(name='Timeout până la',value=f'<t:{int(row["until_time"])}:F>',inline=False)
    if row['detail']:
        embed.add_field(name='Detaliu',value=row['detail'],inline=False)
    return embed


class SanctionConfirmation(discord.ui.View):
    def __init__(self,bot,actor,target,action,reason):
        super().__init__(timeout=120)
        self.bot,self.actor,self.target,self.action,self.reason=bot,actor,target,action,reason
        self.used=False
        self.expires=time.monotonic()+120

    async def verify(self,i):
        if i.guild_id!=self.bot.guild_id or i.user.id!=self.actor or self.used or time.monotonic()>=self.expires:
            raise ValueError('Confirmarea nu îți aparține sau a fost deja folosită.')

    @discord.ui.button(label='Confirmă sancțiunea',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        await self.verify(i)
        self.used=True
        for child in self.children: child.disabled=True
        await i.response.defer(ephemeral=True,thinking=True)
        await self.bot.execute_moderation(i,self.target,self.action,self.reason)

    @discord.ui.button(label='Anulează',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):
        await self.verify(i)
        self.used=True
        await i.response.edit_message(content='Sancțiune anulată.',view=None)

    async def on_error(self,i,error,item):
        await self.bot.command_error(i,error)


class Moderation:
    def moderation_config(self):
        return self.community_config('moderation')

    async def moderation_actor(self,i,action):
        if i.guild_id!=self.guild_id:
            raise ValueError('Server neautorizat.')
        actor=await i.guild.fetch_member(i.user.id)
        if actor.bot:
            raise ValueError('Acțiune rezervată membrilor staff.')
        config=self.moderation_config()
        administrator=actor.guild_permissions.administrator
        roles={r.id for r in actor.roles}
        needed=({'helper','moderator'} if action in ('warn','history') else {'moderator'})
        if not administrator and (not config or not any(config[k] in roles for k in needed)):
            raise ValueError('Acțiune rezervată '+('Helper/Moderator.' if action in ('warn','history') else 'Moderatorilor.'))
        permission=PERMISSIONS.get(action)
        if permission and not getattr(actor.guild_permissions,permission):
            raise ValueError('Îți lipsește permisiunea Discord pentru această sancțiune.')
        return actor

    async def moderation_authorize(self,i,target_id,action):
        actor=await self.moderation_actor(i,action)
        if action=='unban':
            bot=await i.guild.fetch_member(i.guild.me.id)
            if not bot.guild_permissions.ban_members:
                raise ValueError('Botului îi lipsește Ban Members.')
            try:
                entry=await i.guild.fetch_ban(discord.Object(id=target_id))
            except discord.NotFound:
                raise ValueError('Acest utilizator nu este banat pe server.')
            return actor,entry.user
        target=await i.guild.fetch_member(target_id)
        bot=await i.guild.fetch_member(i.guild.me.id)
        if target.id in (actor.id,bot.id,i.guild.owner_id) or target.bot or target.guild_permissions.administrator:
            raise ValueError('Nu poți sancționa propria persoană, boți, proprietarul sau administratorii.')
        if actor.id!=i.guild.owner_id and actor.top_role<=target.top_role:
            raise ValueError('Rolul tău trebuie să fie deasupra rolului membrului.')
        if action!='warn':
            if bot.top_role<=target.top_role:
                raise ValueError('Rolul botului trebuie să fie deasupra rolului membrului.')
            if not getattr(bot.guild_permissions,PERMISSIONS[action]):
                raise ValueError('Botului îi lipsește permisiunea Discord pentru această sancțiune.')
        return actor,target

    async def log_moderation_case(self,guild,row):
        async with self.community_lock:
            config=self.community_config('logs')
            if not config:
                self.store.moderation_log_state(row['id'],-1)
                return False
            try:
                room=await self.community_log_room(guild,config)
                await room.send(embed=case_embed(row),allowed_mentions=discord.AllowedMentions.none())
            except (discord.HTTPException,ValueError):
                self.store.moderation_log_state(row['id'],-1)
                log.exception('Cazul de moderare %s este salvat, dar logul privat nu a fost trimis',row['id'])
                return False
            self.store.moderation_log_state(row['id'],1)
            return True

    async def execute_moderation(self,i,target_id,action,reason,minutes=None):
        if action not in ('warn','timeout','untimeout','kick','ban','unban') or not 3<=len(reason.strip())<=500:
            raise ValueError('Acțiune sau motiv invalid (3–500 caractere).')
        if action=='timeout' and (not isinstance(minutes,int) or not 1<=minutes<=40320):
            raise ValueError('Timeout între 1 și 40320 minute.')
        async with self.moderation_lock:
            actor,target=await self.moderation_authorize(i,target_id,action)
            until=discord.utils.utcnow()+datetime.timedelta(minutes=minutes) if action=='timeout' else None
            number=self.store.moderation_create(actor.id,target.id,action,reason.strip(),until.timestamp() if until else None)
            # Case first; never replay a pending/uncertain action automatically after a restart.
            audit=f'RedFear #{number} | staff {actor.id} | {reason.strip()}'[:512]
            try:
                if action in ('timeout','untimeout'):
                    await target.timeout(until,reason=audit)
                elif action=='kick':
                    await target.kick(reason=audit)
                elif action=='ban':
                    await i.guild.ban(target,reason=audit,delete_message_seconds=0)
                elif action=='unban':
                    await i.guild.unban(target,reason=audit)
            except discord.HTTPException as error:
                status='failed' if 400<=error.status<500 and error.status!=429 else 'uncertain'
                self.store.moderation_finish(number,status,f'Discord HTTP {error.status}, cod {error.code}.')
            except BaseException:
                self.store.moderation_finish(number,'uncertain','Cerere întreruptă; verifică starea în Discord înainte să repeți.')
                raise
            else:
                self.store.moderation_finish(number,'applied')
            row=self.store.moderation_case(number)
            sent=await self.log_moderation_case(i.guild,row)
        await i.followup.send(f'Caz #{number}: {LABELS[action]} — {STATES[row["status"]]}.'+
                              ('' if sent else ' Istoricul este salvat; log privat indisponibil. Folosește /mod_case.'),ephemeral=True)
        return row

    def register_moderation(self):
        @self.tree.command(name='moderation_setup',description='Alege rolurile existente Helper și Moderator; nu acordă permisiuni Discord')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def setup(i:discord.Interaction,helper_role:discord.Role,moderator_role:discord.Role):
            self.admin(i)
            if helper_role.id==moderator_role.id or any(r.guild.id!=self.guild_id or r.is_default() or r.managed for r in (helper_role,moderator_role)):
                raise ValueError('Alege două roluri existente, distincte, nemanageriate și diferite de @everyone.')
            self.save_community('moderation',dict(helper=helper_role.id,moderator=moderator_role.id))
            await i.response.send_message('Moderare configurată. Helper: warn/istoric. Moderator: sancțiuni dacă are și permisiunile Discord necesare. Rolurile nu au fost modificate.',ephemeral=True)

        @self.tree.command(name='warn',description='Avertisment cu motiv; disponibil pentru Helper și Moderator')
        @app_commands.guild_only()
        async def warn(i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,3,500]):
            await i.response.defer(ephemeral=True,thinking=True)
            await self.execute_moderation(i,member.id,'warn',reason)

        @self.tree.command(name='warnings',description='Avertismentele unui membru; vizibile privat staffului')
        @app_commands.guild_only()
        async def warnings(i:discord.Interaction,member:discord.User,page:app_commands.Range[int,1]=1):
            await self.show_moderation_history(i,member.id,page,True)

        @self.tree.command(name='mod_history',description='Istoricul sancțiunilor și încercărilor; privat pentru staff')
        @app_commands.guild_only()
        async def history(i:discord.Interaction,member:discord.User,page:app_commands.Range[int,1]=1):
            await self.show_moderation_history(i,member.id,page,False)

        @self.tree.command(name='mod_case',description='Detaliile private ale unui caz de moderare')
        @app_commands.guild_only()
        async def case(i:discord.Interaction,case_id:app_commands.Range[int,1]):
            await i.response.defer(ephemeral=True,thinking=True)
            await self.moderation_actor(i,'history')
            row=self.store.moderation_case(case_id)
            if not row:
                raise ValueError('Caz inexistent.')
            options={}
            if row['action']=='purge':
                items=self.store.moderation_purge_items(case_id)
                audit='\n'.join(f'{item["message"]}: {item["status"]}' for item in items)
                options['file']=discord.File(io.BytesIO(audit.encode()),filename=f'caz-{case_id}-mesaje.txt')
            await i.followup.send(embed=case_embed(row),ephemeral=True,allowed_mentions=discord.AllowedMentions.none(),**options)

        @self.tree.command(name='timeout',description='Restricționează temporar un membru (1 minut–28 zile)')
        @app_commands.guild_only()
        @app_commands.default_permissions(moderate_members=True)
        async def timeout(i:discord.Interaction,member:discord.Member,minutes:app_commands.Range[int,1,40320],reason:app_commands.Range[str,3,500]):
            await i.response.defer(ephemeral=True,thinking=True)
            await self.execute_moderation(i,member.id,'timeout',reason,minutes)

        @self.tree.command(name='untimeout',description='Elimină timeoutul unui membru')
        @app_commands.guild_only()
        @app_commands.default_permissions(moderate_members=True)
        async def untimeout(i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,3,500]):
            await i.response.defer(ephemeral=True,thinking=True)
            await self.execute_moderation(i,member.id,'untimeout',reason)

        @self.tree.command(name='kick',description='Propune kick; necesită confirmare și permisiuni')
        @app_commands.guild_only()
        @app_commands.default_permissions(kick_members=True)
        async def kick(i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,3,500]):
            await self.preview_sanction(i,member.id,'kick',reason)

        @self.tree.command(name='ban',description='Propune ban fără ștergere de mesaje; necesită confirmare')
        @app_commands.guild_only()
        @app_commands.default_permissions(ban_members=True)
        async def ban(i:discord.Interaction,member:discord.Member,reason:app_commands.Range[str,3,500]):
            await self.preview_sanction(i,member.id,'ban',reason)

        @self.tree.command(name='unban',description='Elimină un ban după ID; cere confirmare și Ban Members')
        @app_commands.guild_only()
        @app_commands.default_permissions(ban_members=True)
        async def unban(i:discord.Interaction,user_id:str,reason:app_commands.Range[str,3,500]):
            if not 1<=len(user_id)<=20 or not user_id.isascii() or not user_id.isdecimal() or not 1<=int(user_id)<2**64:
                raise ValueError('Introdu ID-ul numeric Discord al utilizatorului.')
            await self.preview_sanction(i,int(user_id),'unban',reason)

        @self.tree.command(name='warn_revoke',description='Retrage un avertisment activ cu motiv, păstrând auditul')
        @app_commands.guild_only()
        async def revoke(i:discord.Interaction,case_id:app_commands.Range[int,1],reason:app_commands.Range[str,3,500]):
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.moderation_lock:
                actor=await self.moderation_actor(i,'warn_revoke')
                row=self.store.moderation_revoke_warn(case_id,actor.id,reason)
                sent=await self.log_moderation_case(i.guild,row)
            await i.followup.send(f'Avertismentul #{case_id} a fost retras. Audit: cazul #{row["id"]}.'+('' if sent else ' Log privat indisponibil; auditul rămâne salvat.'),ephemeral=True)

        @self.tree.command(name='purge',description='Previzualizează ștergerea mesajelor umane recente; necesită confirmare')
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_messages=True)
        async def purge(i:discord.Interaction,count:app_commands.Range[int,1,50],reason:app_commands.Range[str,3,500]):
            await i.response.defer(ephemeral=True,thinking=True)
            await self.preview_purge(i,count,reason)

        @self.tree.command(name='mod_log_retry',description='Retrimite în logul privat un caz care nu a fost confirmat ca livrat')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def retry(i:discord.Interaction,case_id:app_commands.Range[int,1]):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.moderation_lock:
                row=self.store.moderation_case(case_id)
                if not row or row['log_state']==1:
                    raise ValueError('Caz inexistent sau log deja livrat.')
                sent=await self.log_moderation_case(i.guild,row)
            await i.followup.send('Log trimis.' if sent else 'Log privat indisponibil; cazul rămâne în SQLite.',ephemeral=True)

    async def preview_sanction(self,i,target_id,action,reason):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.moderation_authorize(i,target_id,action)
        await i.followup.send(f'Confirmi **{LABELS[action]}** pentru <@{target_id}> (ID {target_id})?\nMotiv: '+discord.utils.escape_markdown(reason)+
                              '\n'+('Mesajele nu vor fi șterse. ' if action=='ban' else '')+'Confirmarea expiră în 2 minute.',
                              view=SanctionConfirmation(self,i.user.id,target_id,action,reason),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    async def show_moderation_history(self,i,target_id,page,warnings_only):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.moderation_actor(i,'history')
        rows=self.store.moderation_history(target_id,page,warnings_only)
        embed=discord.Embed(title=f'RedFear • Istoric ID {target_id} • Pagina {page}',color=0xDC2626)
        for row in rows:
            text=f'{STATES[row["status"]]} • Staff <@{row["actor"]}> • <t:{int(row["created"])}:f>\n'+discord.utils.escape_markdown(row['reason'][:450])
            embed.add_field(name=f'#{row["id"]} • {LABELS[row["action"]]}',value=text,inline=False)
        if not rows:
            embed.description='Nicio înregistrare pe această pagină.'
        embed.set_footer(text='5 cazuri/pagină • Istoric local al acțiunilor prin acest bot')
        await i.followup.send(embed=embed,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    def purge_protected(self,channel):
        ids=self.protected_layout_channels()
        helper=self.helper_config() or {}
        ids.add(helper.get('review_channel'))
        config=self.community_config('logs') or {}
        ids.add(config.get('channel'))
        ids.discard(None)
        if channel.id in ids or channel.category_id in ids:
            return True
        return not channel.permissions_for(channel.guild.default_role).view_channel

    async def purge_authorize(self,i,channel_id):
        if i.channel_id!=channel_id:
            raise ValueError('Confirmarea este valabilă numai în canalul selectat.')
        actor=await self.moderation_actor(i,'purge')
        bot=await i.guild.fetch_member(i.guild.me.id)
        channel=await i.guild.fetch_channel(channel_id)
        if not isinstance(channel,discord.TextChannel) or channel.guild.id!=self.guild_id or self.purge_protected(channel):
            raise ValueError('Ștergerea este blocată în canale private, tichete, arhive și aplicații. Folosește un canal text public.')
        for member in (actor,bot):
            permissions=channel.permissions_for(member)
            if not all(getattr(permissions,name) for name in ('view_channel','read_message_history','manage_messages')):
                raise ValueError('Atât tu, cât și botul aveți nevoie de View Channel, Read Message History și Manage Messages în canal.')
        return actor,channel

    @staticmethod
    def purge_eligible(message):
        return not message.pinned and not message.author.bot and message.type in (discord.MessageType.default,discord.MessageType.reply)

    async def preview_purge(self,i,count,reason):
        if not 1<=count<=50 or not 3<=len(reason.strip())<=500:
            raise ValueError('Alege 1–50 mesaje și un motiv de 3–500 caractere.')
        _,channel=await self.purge_authorize(i,i.channel_id)
        messages=[message async for message in channel.history(limit=count) if self.purge_eligible(message)]
        if not messages:
            raise ValueError('Nu există mesaje umane nefixate eligibile în ultimele mesaje verificate.')
        text=(f'Confirmi ștergerea definitivă a **{len(messages)} mesaje** din <#{channel.id}>?\n'
              f'Selecție fixă dintre ultimele {count} mesaje; boții, mesajele fixate și cele de sistem sunt excluse.\n'
              f'[Cel mai recent selectat]({messages[0].jump_url}) • [Cel mai vechi selectat]({messages[-1].jump_url})\n'
              f'Motiv: {discord.utils.escape_markdown(reason)}\n'
              'Nu vor fi incluse mesaje noi. Ștergerea nu poate fi anulată. Confirmarea expiră în 2 minute.')
        await i.followup.send(text,view=PurgeConfirmation(self,i.user.id,channel.id,[m.id for m in messages],reason),
                              ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    async def execute_purge(self,i,channel_id,messages,reason):
        if not 3<=len(reason.strip())<=500:
            raise ValueError('Motiv obligatoriu de 3–500 caractere.')
        async with self.moderation_lock:
            actor,channel=await self.purge_authorize(i,channel_id)
            number=self.store.moderation_purge_create(actor.id,channel.id,messages,reason.strip())
            deleted=skipped=0
            status='applied'; problem=''
            current=None
            try:
                for message_id in messages:
                    current=message_id
                    try:
                        message=await channel.fetch_message(message_id)
                        if not self.purge_eligible(message):
                            self.store.moderation_purge_update(number,message_id,'skipped'); skipped+=1
                            continue
                        await message.delete()
                    except discord.NotFound:
                        self.store.moderation_purge_update(number,message_id,'skipped'); skipped+=1
                        continue
                    self.store.moderation_purge_update(number,message_id,'deleted'); deleted+=1
            except discord.HTTPException as error:
                known=400<=error.status<500 and error.status!=429
                status=('partial' if deleted else 'failed') if known else 'uncertain'
                if current is not None:
                    self.store.moderation_purge_update(number,current,'skipped' if known else 'uncertain')
                problem=f' Oprit la Discord HTTP {error.status}, cod {error.code}; cererile rămase nu au fost reluate.'
            except BaseException:
                if current is not None:
                    self.store.moderation_purge_update(number,current,'uncertain')
                self.store.moderation_finish(number,'uncertain',f'Canal {channel.id}. Ștergeri confirmate: {deleted}; omise: {skipped}. Cerere întreruptă; verifică în Discord.')
                raise
            self.store.moderation_finish(number,status,f'Canal {channel.id}. Selectate: {len(messages)}; ștergeri confirmate: {deleted}; omise: {skipped}.'+problem)
            row=self.store.moderation_case(number)
            sent=await self.log_moderation_case(i.guild,row)
        await i.followup.send(f'Caz #{number}: {STATES[status]}. Șterse confirmat: {deleted}; omise: {skipped}. '+
                              ('Log privat trimis.' if sent else 'Audit salvat; log privat indisponibil.')+' Detalii și ID-uri în /mod_case.',ephemeral=True)
        return row


class PurgeConfirmation(discord.ui.View):
    def __init__(self,bot,actor,channel,messages,reason):
        super().__init__(timeout=120)
        self.bot,self.actor,self.channel,self.messages,self.reason=bot,actor,channel,tuple(messages),reason
        self.used=False
        self.expires=time.monotonic()+120

    def verify(self,i):
        if i.guild_id!=self.bot.guild_id or i.user.id!=self.actor or i.channel_id!=self.channel or self.used or time.monotonic()>=self.expires:
            raise ValueError('Confirmare expirată, folosită sau destinată altui membru/canal.')

    @discord.ui.button(label='Șterge selecția definitiv',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        self.verify(i); self.used=True
        await i.response.defer(ephemeral=True,thinking=True)
        await self.bot.execute_purge(i,self.channel,self.messages,self.reason)

    @discord.ui.button(label='Anulează',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):
        self.verify(i); self.used=True
        await i.response.edit_message(content='Ștergere anulată. Niciun mesaj nu a fost șters.',view=None)

    async def on_error(self,i,error,item):
        await self.bot.command_error(i,error)
