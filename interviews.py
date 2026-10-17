"""Persistent recruitment interviews and manually evaluated probation periods."""
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

log=logging.getLogger('redfear')
LONDON=ZoneInfo('Europe/London')


def interview_time(date, hour, now=None):
    try:
        naive=dt.datetime.strptime(date.strip()+' '+hour.strip(),'%Y-%m-%d %H:%M')
        start=naive.replace(tzinfo=LONDON)
        # Reject the missing or ambiguous hour at daylight-saving transitions.
        if (start.astimezone(dt.timezone.utc).astimezone(LONDON).replace(tzinfo=None)!=naive
                or start.utcoffset()!=naive.replace(tzinfo=LONDON,fold=1).utcoffset()):
            raise ValueError()
        value=start.timestamp()
    except ValueError:
        raise ValueError('Folosește AAAA-LL-ZZ și HH:MM, ora Londrei. Evită orele ambigue la schimbarea orei.')
    now=time.time() if now is None else now
    if not now+60<=value<=now+180*86400:
        raise ValueError('Alege o dată viitoare, peste minimum un minut și în maximum 180 zile.')
    return value


def invitation_text(row):
    return (f'RedFear • Interviu pentru aplicația #{row["application"]}\n'
            f'Data: <t:{int(row["scheduled"])}:F> (afișată în ora ta locală)\n'
            f'Durată: {row["duration"]} minute\nLoc: {row["location"]}\n'
            'Confirmă participarea sau cere reprogramare. Nu se acordă automat roluri.')


async def reply(i,text):
    if i.response.is_done(): await i.followup.send(text,ephemeral=True)
    else: await i.response.send_message(text,ephemeral=True)


class ScheduleInterviewModal(discord.ui.Modal,title='Programează interviu • ora Londrei'):
    date=discord.ui.TextInput(label='Data: AAAA-LL-ZZ',max_length=10)
    hour=discord.ui.TextInput(label='Ora Londrei: HH:MM',max_length=5)
    duration=discord.ui.TextInput(label='Durată în minute: 5–180',default='30',max_length=3)
    location=discord.ui.TextInput(label='Locul / linkul canalului vocal',min_length=3,max_length=300)
    def __init__(self,bot,application):
        super().__init__();self.bot,self.application=bot,application
    async def on_submit(self,i):
        self.bot.require_owner(i)
        await i.response.defer(ephemeral=True,thinking=True)
        try: minutes=int(str(self.duration))
        except ValueError: raise ValueError('Durata trebuie să fie un număr întreg.')
        await self.bot.schedule_interview(i,self.application,str(self.date),str(self.hour),minutes,str(self.location))
    async def on_error(self,i,error): await self.bot.command_error(i,error)


class RescheduleInterviewModal(discord.ui.Modal,title='Cere reprogramarea interviului'):
    reason=discord.ui.TextInput(label='Motiv și când ai fi disponibil',style=discord.TextStyle.paragraph,min_length=3,max_length=500)
    def __init__(self,bot,row):
        super().__init__();self.bot=bot;self.row=dict(row)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True,thinking=True)
        async with self.bot.applications_lock:
            row=self.bot.store.interview_reply(self.row['application'],i.user.id,self.row['message'],self.row['revision'],'reschedule',str(self.reason))
            sent=await self.bot.recruitment_dm(self.bot.owner_user_id,
                f'Reprogramare solicitată • aplicația #{row["application"]}\nCandidat: <@{row["candidate"]}>\n{row["request"]}')
        await reply(i,'Cererea de reprogramare este salvată. '+('Proprietarul a fost notificat.' if sent else 'DM-ul către proprietar nu a ajuns; contactează-l direct.'))
    async def on_error(self,i,error): await self.bot.command_error(i,error)


class InterviewReply(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=None);self.bot=bot
    def row(self,i):
        row=self.bot.store.db.execute('SELECT * FROM staff_interviews WHERE message=?',(i.message.id,)).fetchone()
        app=self.bot.store.helper_application(row['application']) if row else None
        if (not row or not app or app['status']!='pending' or i.user.id!=row['candidate']
                or row['status'] not in ('scheduled','confirmed') or row['scheduled']<=time.time()):
            raise ValueError('Invitație expirată, înlocuită sau care nu îți aparține.')
        return row
    @discord.ui.button(label='Confirm participarea',style=discord.ButtonStyle.success,custom_id='redfear:interview:confirm:v1')
    async def confirm(self,i,button):
        await i.response.defer(ephemeral=True,thinking=True)
        async with self.bot.applications_lock:
            old=self.row(i)
            row=self.bot.store.interview_reply(old['application'],i.user.id,i.message.id,old['revision'],'confirmed')
            sent=await self.bot.recruitment_dm(self.bot.owner_user_id,f'Interviu confirmat • aplicația #{row["application"]} • <@{i.user.id}> • <t:{int(row["scheduled"])}:F>')
        await reply(i,'Participarea a fost confirmată.'+(' Proprietarul nu a putut primi DM.' if not sent else ''))
    @discord.ui.button(label='Cer reprogramare',style=discord.ButtonStyle.secondary,custom_id='redfear:interview:reschedule:v1')
    async def reschedule(self,i,button):
        await i.response.send_modal(RescheduleInterviewModal(self.bot,self.row(i)))
    async def on_error(self,i,error,item): await self.bot.command_error(i,error)


class Interviews:
    async def recruitment_dm(self,user_id,text,view=None):
        try:
            user=await self.fetch_user(user_id)
            return await user.send(text,view=view,allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info('Notificare recrutare indisponibilă pentru %s',user_id)
            return None

    async def send_interview_invitation(self,row):
        if row['status'] not in ('scheduled','confirmed') or row['scheduled']<=time.time():
            raise ValueError('Interviul nu mai poate fi confirmat. Reprogramează-l.')
        message=await self.recruitment_dm(row['candidate'],invitation_text(row),InterviewReply(self))
        self.store.interview_message(row['application'],row['revision'],message.id if message else None,1 if message else -1)
        return bool(message)

    async def schedule_interview(self,i,application,date,hour,minutes,location):
        self.require_owner(i)
        start=interview_time(date,hour)
        if not 5<=minutes<=180 or not 3<=len(location.strip())<=300:
            raise ValueError('Durată 5–180 minute și loc de 3–300 caractere.')
        async with self.applications_lock:
            app=self.store.helper_application(application)
            if not app: raise ValueError('Aplicația nu există.')
            await self.helper_private_room(i.guild,app['review_channel'],app['reviewer_role'])
            row=self.store.interview_schedule(application,i.user.id,start,minutes,location.strip())
            sent=await self.send_interview_invitation(row)
        await reply(i,f'Interviu salvat pentru <t:{int(start)}:F>. '+('Invitația a fost trimisă prin DM.' if sent else 'DM blocat; candidatul poate vedea data cu /staff_recruitment_status. Reîncearcă /staff_interview_resend.'))

    async def process_recruitment_reminders(self,now=None):
        now=time.time() if now is None else now
        async with self.applications_lock:
            rows=self.store.db.execute("SELECT s.* FROM staff_interviews s JOIN helper_applications a ON a.id=s.application WHERE s.status IN ('scheduled','confirmed') AND a.status='pending' AND s.scheduled>? AND s.scheduled<=?",(now,now+900)).fetchall()
            alerts=[]
            for row in rows:
                for recipient in {row['candidate'],self.owner_user_id}:
                    alerts.append((row['application'],row['revision'],'interview',recipient,
                                   'Reminder: interviul începe în cel mult 15 minute.\n'+invitation_text(row)))
            for row in self.store.db.execute("SELECT * FROM staff_trial WHERE status='active' AND ends<=?",(now,)).fetchall():
                for recipient in {self.owner_user_id,row['responsible']}:
                    alerts.append((row['application'],1,'trial',recipient,f'Perioada de probă pentru aplicația #{row["application"]}, candidat <@{row["candidate"]}>, s-a încheiat. Evaluarea finală trebuie făcută manual de proprietar.'))
            for application,revision,kind,recipient,text in alerts:
                if not self.store.reminder_reserve(application,revision,kind,recipient): continue
                try:
                    sent=await self.recruitment_dm(recipient,text)
                    self.store.reminder_finish(application,revision,kind,recipient,'sent' if sent else 'failed')
                except Exception:
                    log.exception('Eroare reminder recrutare pentru aplicația %s',application)
                    self.store.reminder_finish(application,revision,kind,recipient,'uncertain')

    @tasks.loop(seconds=60)
    async def recruitment_poll(self):
        try: await self.process_recruitment_reminders()
        except Exception: log.exception('Verificarea reminderelor de recrutare a eșuat')

    @recruitment_poll.before_loop
    async def recruitment_ready(self): await self.wait_until_ready()

    def register_interviews(self):
        @self.tree.command(name='staff_interview',description='Proprietar: programează/reprogramează un interviu; ora Londrei')
        @app_commands.guild_only()
        async def schedule(i:discord.Interaction,application_id:app_commands.Range[int,1],date:str,hour:str,
                           location:app_commands.Range[str,3,300],minutes:app_commands.Range[int,5,180]=30):
            self.require_owner(i)
            await i.response.defer(ephemeral=True,thinking=True)
            await self.schedule_interview(i,application_id,date,hour,minutes,location)

        @self.tree.command(name='staff_interview_resend',description='Proprietar: retrimite invitația curentă prin DM')
        @app_commands.guild_only()
        async def resend(i:discord.Interaction,application_id:app_commands.Range[int,1]):
            self.require_owner(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                row=self.store.interview(application_id)
                app=self.store.helper_application(application_id)
                if not row or not app or app['status']!='pending': raise ValueError('Interviu activ inexistent.')
                sent=await self.send_interview_invitation(row)
            await reply(i,'Invitație retrimisă; butoanele invitației anterioare sunt invalidate.' if sent else 'DM indisponibil. Datele sunt vizibile candidatului prin /staff_recruitment_status.')

        @self.tree.command(name='staff_interview_result',description='Proprietar: rezultat interviu și opțional perioadă de probă')
        @app_commands.guild_only()
        @app_commands.choices(result=[app_commands.Choice(name=n,value=v) for n,v in [('Acceptat','accepted'),('Respins','rejected'),('Perioadă de probă','trial')]])
        async def result(i:discord.Interaction,application_id:app_commands.Range[int,1],result:app_commands.Choice[str],reason:app_commands.Range[str,3,1000],
                         responsible:discord.Member=None,days:app_commands.Range[int,1,90]=14):
            self.require_owner(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                app=self.store.helper_application(application_id)
                if not app: raise ValueError('Aplicația nu există.')
                room=await self.helper_private_room(i.guild,app['review_channel'],app['reviewer_role'])
                if result.value=='trial':
                    if not responsible or responsible.bot or responsible.id==app['owner'] or responsible.guild.id!=self.guild_id:
                        raise ValueError('Alege un responsabil din staff, diferit de candidat.')
                    fresh=await i.guild.fetch_member(responsible.id)
                    if not (self.staff(fresh) or self.manager(fresh,'discord') or self.manager(fresh,'moderation')):
                        raise ValueError('Responsabilul trebuie să fie membru al echipei.')
                row=self.store.interview_result(application_id,i.user.id,result.value,reason,responsible.id if responsible else None,days)
                from applications import application_embed, HelperReview
                try:
                    if row['message']:
                        card=await room.fetch_message(row['message'])
                        await card.edit(embed=application_embed(row),view=HelperReview(self,True))
                except discord.HTTPException: log.exception('Rezultat salvat; cardul trebuie republicat')
                if result.value=='trial':
                    trial=self.store.trial(application_id)
                    text=f'RedFear • Aplicația #{application_id}: acceptată pentru probă până la <t:{int(trial["ends"])}:F>.\nMotiv: {reason}\nRolul se acordă manual; decizia finală urmează după evaluare.'
                    sent=await self.recruitment_dm(row['owner'],text)
                    self.store.helper_dm(application_id,1 if sent else -1)
                    await self.recruitment_dm(trial['responsible'],f'Ai fost desemnat responsabil pentru proba aplicației #{application_id}, candidat <@{trial["candidate"]}>, până la <t:{int(trial["ends"])}:F>. Observații: /staff_trial_note; situație: /staff_recruitment_status application_id:{application_id}. Evaluarea finală aparține proprietarului.')
                else: sent=await self.notify_helper(row)
            await reply(i,'Rezultat salvat. '+('DM trimis.' if sent else 'DM indisponibil; candidatul poate folosi /staff_recruitment_status.')+' Rolurile nu au fost modificate.')

        @self.tree.command(name='staff_trial_note',description='Proprietar/responsabil: observație privată pentru perioada de probă')
        @app_commands.guild_only()
        async def note(i:discord.Interaction,application_id:app_commands.Range[int,1],note:app_commands.Range[str,3,1000]):
            if i.guild_id!=self.guild_id: raise ValueError('Server neautorizat.')
            async with self.applications_lock:
                self.store.trial_note(application_id,i.user.id,note,self.owner_user_id)
            await reply(i,'Observație privată salvată.')

        @self.tree.command(name='staff_trial_finish',description='Proprietar: evaluarea finală a perioadei de probă')
        @app_commands.guild_only()
        @app_commands.choices(result=[app_commands.Choice(name='Promovat',value='passed'),app_commands.Choice(name='Nepromovat',value='failed')])
        async def finish(i:discord.Interaction,application_id:app_commands.Range[int,1],result:app_commands.Choice[str],reason:app_commands.Range[str,3,1000]):
            self.require_owner(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.applications_lock:
                row=self.store.trial_finish(application_id,i.user.id,result.value,reason)
                sent=await self.recruitment_dm(row['candidate'],f'RedFear • Proba pentru aplicația #{application_id}: '+('promovată.' if result.value=='passed' else 'nepromovată.')+f'\nMotiv: {reason}\nRolurile sunt gestionate manual de proprietar.')
            await reply(i,'Evaluare finală salvată. '+('DM trimis.' if sent else 'DM indisponibil; rezultatul este vizibil prin /staff_recruitment_status.'))

        @self.tree.command(name='staff_recruitment_status',description='Interviu/probă proprie; proprietarul poate selecta o aplicație')
        @app_commands.guild_only()
        async def status(i:discord.Interaction,application_id:app_commands.Range[int,1]=None):
            if i.guild_id!=self.guild_id: raise ValueError('Server neautorizat.')
            app=self.store.helper_application(application_id) if application_id else self.store.helper_latest(i.user.id)
            if not app: return await reply(i,'Nu există aplicație.')
            row=self.store.interview(app['id']);trial=self.store.trial(app['id'])
            responsible=bool(trial and trial['responsible']==i.user.id)
            if i.user.id not in (self.owner_user_id,app['owner']) and not responsible:
                raise ValueError('Poți vedea numai propria aplicație sau proba pentru care ești responsabil.')
            text=f'Aplicația #{app["id"]}: {app["status"]}.'
            if row:
                text+=f'\nInterviu: <t:{int(row["scheduled"])}:F> • {row["duration"]} minute • {row["status"]}\nLoc: {row["location"]}'
                if row['request']: text+='\nReprogramare solicitată: '+row['request']
            if trial:
                text+=f'\nProbă: {trial["status"]} • până la <t:{int(trial["ends"])}:F> • responsabil <@{trial["responsible"]}>'
                if trial['result_reason']: text+='\nEvaluare finală: '+trial['result_reason']
            elif app['reason']: text+='\nMotiv: '+app['reason']
            if i.user.id==self.owner_user_id or responsible:
                notes=self.store.db.execute("SELECT actor,detail FROM staff_interview_events WHERE application=? AND event='trial-note' ORDER BY id DESC LIMIT 5",(app['id'],)).fetchall()
                for note in notes: text+=f'\nObservație <@{note["actor"]}>: '+note['detail'][:240]
            await reply(i,text[:1950])
