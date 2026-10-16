"""Discord interface for a free, closed play-token Blackjack economy."""
import json
import logging

import discord
from discord import app_commands
from discord.ext import tasks

from casino_store import card_text, total

log=logging.getLogger('redfear')
RULES=('Jetoane fictive gratuite, fără cumpărare, transfer, retragere sau schimb în bunuri FiveM.\n'
       '1–5 jucători • înscriere 60s • 45s pentru fiecare decizie; la expirare: Stai.\n'
       'Dealerul stă la orice 17. Blackjack natural plătește 3:2; victorie 1:1; egal: miza înapoi.\n'
       'Dublare numai la prima decizie, o carte apoi Stai. Fără split, insurance sau surrender.\n'
       'Mâinile jucătorilor sunt vizibile tuturor. La restart sau masă întreruptă se restituie mizele active.\n'
       'Începi cu 1000 de jetoane; /casino_daily oferă 500 la fiecare 24h.')


class BlackjackButton(discord.ui.DynamicItem[discord.ui.Button],
                      template=r'redfear:bj:(?P<game>\d+):(?P<rev>\d+):(?P<action>join|leave|start|hit|stand|double)'):
    def __init__(self,game,revision,action):
        self.game,self.revision,self.action=game,revision,action
        labels={'join':'Intră la masă','leave':'Renunță','start':'Începe','hit':'Trage','stand':'Stai','double':'Dublează'}
        super().__init__(discord.ui.Button(label=labels[action],custom_id=f'redfear:bj:{game}:{revision}:{action}',
                         style=discord.ButtonStyle.success if action in ('join','start') else discord.ButtonStyle.secondary))

    @classmethod
    async def from_custom_id(cls,i,item,match,/):
        return cls(int(match['game']),int(match['rev']),match['action'])

    async def callback(self,i):
        try:
            await i.response.defer(ephemeral=True,thinking=True)
            await i.client.casino_action(i,self.game,self.revision,self.action)
        except Exception as error:
            await i.client.command_error(i,error)


def game_view(row):
    view=discord.ui.View(timeout=None)
    actions=('join','leave','start') if row['phase']=='lobby' else ('hit','stand','double') if row['phase']=='playing' else ()
    for action in actions:
        view.add_item(BlackjackButton(row['id'],row['revision'],action))
    return view


def game_embed(row):
    state=json.loads(row['state'])
    embed=discord.Embed(title=f'🃏 RedFear • Blackjack #{row["id"]}',color=0xDC2626)
    if row['phase']=='lobby':
        embed.description=f'Miză: **{row["bet"]} jetoane** de persoană • {len(state["players"])}/5 locuri\nÎnscrieri până la <t:{int(row["deadline"])}:R>. Gazda poate începe mai devreme.'
        embed.add_field(name='La masă',value='\n'.join(f'<@{p["id"]}>' for p in state['players']),inline=False)
    elif row['phase']=='cancelled':
        embed.description=state['cancel_reason']+'\nMizele rezervate au fost restituite. Nicio victorie/înfrângere înregistrată.'
    else:
        dealer=state['dealer']
        shown=card_text(dealer[0])+' 🂠' if row['phase']=='playing' else ' '.join(map(card_text,dealer))+f' = {total(dealer)}'
        embed.add_field(name='Dealer',value=shown,inline=False)
        current=next((p for p in state['players'] if p['status']=='playing'),None)
        if current:
            embed.description=f'Rândul lui <@{current["id"]}> • decizie până la <t:{int(row["deadline"])}:R>.'
        else:
            embed.description='Mâna este încheiată. Soldurile au fost actualizate.'
        for p in state['players']:
            hand=' '.join(map(card_text,p['hand']))+f' = **{total(p["hand"])}**'
            if p.get('result'):
                outcome={'win':'Victorie','loss':'Înfrângere','push':'Egal'}[p['result']]
                hand+=f'\n{outcome} • miză {p["stake"]} • restituit/câștigat {p["payout"]} • net {p["payout"]-p["stake"]:+d}'
            else:
                hand+=f'\nMiză {p["stake"]} • '+('Depășit 21' if total(p['hand'])>21 else 'Joacă' if p['status']=='playing' else 'Stă')
            embed.add_field(name=f'Jucător {p["id"]}',value=f'<@{p["id"]}>\n{hand}',inline=False)
    embed.set_footer(text='Doar jetoane gratuite • Fără bani reali sau legătură cu economia FiveM')
    return embed


class Casino:
    def casino_config(self):
        return self.community_config('casino')

    def casino_member(self,i,playing=False):
        if i.guild_id!=self.guild_id or i.user.bot:
            raise ValueError('Joc disponibil numai membrilor serverului RedFear.')
        config=self.casino_config()
        if not config or not config['enabled']:
            raise ValueError('Casino oprit. Un administrator poate folosi /casino_setup.')
        if playing and i.channel_id!=config['channel']:
            raise ValueError(f'Folosește canalul de joc <#{config["channel"]}>.')
        if playing and not i.channel.permissions_for(i.user).view_channel:
            raise ValueError('Nu ai acces la masa de joc.')
        return config

    async def update_casino_message(self,guild,row):
        if not row['message']:
            return
        try:
            channel=await guild.fetch_channel(row['channel'])
            message=await channel.fetch_message(row['message'])
            await message.edit(embed=game_embed(row),view=game_view(row))
        except discord.HTTPException:
            # Never leave a live wager dependent on an inaccessible message.
            self.store.casino_void(row['id'],'Mesajul mesei nu mai poate fi actualizat.')
            log.exception('Masa Blackjack %s nu a putut fi actualizată; mizele active sunt restituite',row['id'])

    async def casino_action(self,i,number,revision,action):
        async with self.casino_lock:
            self.casino_member(i,True)
            row=self.store.casino_game(number)
            if not row or row['channel']!=i.channel_id or not i.message or row['message']!=i.message.id:
                raise ValueError('Folosește mesajul curent al mesei.')
            if row['phase'] not in ('lobby','playing'):
                raise ValueError('Această masă s-a încheiat sau a fost anulată. Jetoanele sunt salvate în /casino_profile.')
            if action=='join':
                row=self.store.casino_join(number,i.user.id)
            elif action=='leave':
                row=self.store.casino_leave(number,i.user.id)
            elif action=='start':
                row=self.store.casino_start(number,i.user.id)
            else:
                row=self.store.casino_move(number,i.user.id,action,revision)
            await self.update_casino_message(i.guild,row)
            current=self.store.casino_game(number)
            text='Masă actualizată.' if current['phase']!='cancelled' else 'Masă anulată; mizele active au fost restituite.'
        await i.followup.send(text,ephemeral=True)

    @tasks.loop(seconds=5)
    async def casino_poll(self):
        guild=self.get_guild(self.guild_id)
        if not guild:
            return
        async with self.casino_lock:
            for row in self.store.casino_active():
                try:
                    changed=self.store.casino_tick(row['id'])
                except Exception:
                    log.exception('Mână Blackjack întreruptă %s',row['id'])
                    changed=self.store.casino_void(row['id'],'Eroare în joc; mizele au fost restituite.')
                if changed:
                    await self.update_casino_message(guild,changed)

    @casino_poll.before_loop
    async def before_casino_poll(self):
        await self.wait_until_ready()
        guild=self.get_guild(self.guild_id)
        if guild:
            async with self.casino_lock:
                for row in self.casino_recovered:
                    await self.update_casino_message(guild,row)
                self.casino_recovered=[]

    def register_casino(self):
        @self.tree.command(name='casino_setup',description='Configurează Blackjack gratuit; fără canal selectat creează o cameră publică dedicată')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def setup(i:discord.Interaction,channel:discord.TextChannel=None):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.casino_lock:
                old=self.casino_config()
                if self.store.casino_active():
                    raise ValueError('Există o masă activă. Așteaptă încheierea sau folosește /casino_stop.')
                if channel is None and old:
                    try:
                        channel=await i.guild.fetch_channel(old['channel'])
                    except discord.NotFound:
                        pass
                if channel is None:
                    channel=await i.guild.create_text_channel('🎰・casino',reason='Blackjack gratuit RedFear',
                        overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=True,send_messages=False),
                                    i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)})
                    # Persist immediately so a later failed rules message does not duplicate the room.
                    self.save_community('casino',dict(channel=channel.id,enabled=False,rules=None))
                if not isinstance(channel,discord.TextChannel) or channel.guild.id!=self.guild_id:
                    raise ValueError('Alege un canal text din server.')
                permissions=channel.permissions_for(i.guild.me)
                if not all(getattr(permissions,k) for k in ('view_channel','send_messages','embed_links','read_message_history')):
                    raise ValueError('Botul are nevoie de View Channel, Send Messages, Embed Links și Read Message History.')
                message=None
                if old and old['channel']==channel.id and old.get('rules'):
                    try:
                        message=await channel.fetch_message(old['rules'])
                    except discord.NotFound:
                        pass
                embed=discord.Embed(title='🎰 RedFear • Blackjack cu jetoane gratuite',description=RULES+'\n\nÎncepe cu **/blackjack bet:50**. Sold: **/casino_profile**.',color=0xDC2626)
                if message:
                    await message.edit(embed=embed)
                else:
                    message=await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
                self.save_community('casino',dict(channel=channel.id,enabled=True,rules=message.id))
            await i.followup.send(f'Blackjack activ în <#{channel.id}>. Jetoane gratuite, fără premii cu valoare reală.',ephemeral=True)

        @self.tree.command(name='casino_stop',description='Oprește jocurile noi și restituie toate mizele active')
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def stop(i:discord.Interaction):
            self.admin(i)
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.casino_lock:
                config=self.casino_config()
                if not config:
                    raise ValueError('Casino neconfigurat.')
                config['enabled']=False
                self.save_community('casino',config)
                for row in self.store.casino_active():
                    row=self.store.casino_void(row['id'],'Casino oprit de administrator.')
                    await self.update_casino_message(i.guild,row)
            await i.followup.send('Casino oprit. Mizele active au fost restituite, iar soldurile păstrate.',ephemeral=True)

        @self.tree.command(name='blackjack',description='Deschide o masă cu 1–5 jucători; miza este în jetoane fictive gratuite')
        @app_commands.guild_only()
        async def blackjack(i:discord.Interaction,bet:app_commands.Range[int,10,500]=50):
            await i.response.defer(ephemeral=True,thinking=True)
            async with self.casino_lock:
                self.casino_member(i,True)
                row=self.store.casino_create(i.channel_id,i.user.id,bet)
                try:
                    message=await i.channel.send(embed=game_embed(row),view=game_view(row),allowed_mentions=discord.AllowedMentions.none())
                    self.store.casino_message(row['id'],message.id)
                except Exception:
                    self.store.casino_void(row['id'],'Publicarea mesei a eșuat.')
                    raise
            await i.followup.send('Masa este deschisă. Miza ta este rezervată; ceilalți au 60 de secunde să intre.',ephemeral=True)

        @self.tree.command(name='casino_profile',description='Soldul tău și statisticile Blackjack, în jetoane fictive')
        @app_commands.guild_only()
        async def profile(i:discord.Interaction):
            self.casino_member(i)
            row=self.store.casino_account(i.user.id)
            seat=self.store.db.execute('SELECT stake FROM casino_seats WHERE member=?',(i.user.id,)).fetchone()
            await i.response.send_message(f'Jetoane disponibile: **{row["balance"]}**\nRezervate la masă: **{seat["stake"] if seat else 0}**\nVictorii: {row["wins"]} • Înfrângeri: {row["losses"]} • Egaluri: {row["pushes"]}\nFără valoare reală sau schimb în bunuri.',ephemeral=True)

        @self.tree.command(name='casino_daily',description='Primești gratuit 500 de jetoane o dată la 24 de ore')
        @app_commands.guild_only()
        async def daily(i:discord.Interaction):
            self.casino_member(i)
            row=self.store.casino_daily(i.user.id)
            await i.response.send_message(f'Ai primit 500 de jetoane gratuite. Sold disponibil: **{row["balance"]}**.',ephemeral=True)

        @self.tree.command(name='casino_top',description='Top 10 solduri disponibile în jetoane gratuite')
        @app_commands.guild_only()
        async def top(i:discord.Interaction):
            self.casino_member(i)
            rows=self.store.casino_top()
            text='\n'.join(f'{index}. <@{r["member"]}> — **{r["balance"]}**' for index,r in enumerate(rows,1)) or 'Nicio partidă încă.'
            await i.response.send_message('**Clasament jetoane gratuite**\n'+text+'\nMizele active nu sunt incluse în soldurile disponibile.',ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
