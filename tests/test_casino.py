import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import test_features as fixtures
from casino import game_embed, game_view, BlackjackButton
from casino_store import total,natural
from store import Store


class CasinoStoreTests(unittest.TestCase):
    def setUp(self): self.s=Store(':memory:')
    def tearDown(self): self.s.db.close()
    def create(self,players=1,bet=50):
        row=self.s.casino_create(10,1,bet,1000)
        for member in range(2,players+1): self.s.casino_join(row['id'],member,1001)
        return row
    def start(self,players=1):
        row=self.create(players)
        with patch('casino_store.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda deck:deck.reverse()
            return self.s.casino_start(row['id'],1,1002)
    def rig(self,row,hand=None,dealer=None,deck=None):
        state=json.loads(row['state'])
        if hand is not None: state['players'][0]['hand']=hand
        if dealer is not None: state['dealer']=dealer
        if deck is not None: state['deck']=deck
        self.s.db.execute('UPDATE casino_games SET state=? WHERE id=?',(json.dumps(state),row['id']))
        self.s.db.commit()
        return state

    def test_aces_and_naturals(self):
        self.assertEqual(total([0,13,9]),12)
        self.assertEqual(total([0,9]),21)
        self.assertTrue(natural([0,9])); self.assertFalse(natural([0,1,7]))

    def test_daily_is_free_once_per_24h(self):
        self.assertEqual(self.s.casino_daily(1,0)['balance'],1500)
        with self.assertRaises(ValueError): self.s.casino_daily(1,86399)
        self.assertEqual(self.s.casino_daily(1,86400)['balance'],2000)

    def test_reservation_and_duplicate_join_are_atomic(self):
        row=self.create()
        self.assertEqual(self.s.casino_account(1)['balance'],950)
        with self.assertRaises(ValueError): self.s.casino_join(row['id'],1,1001)
        self.assertEqual(self.s.casino_account(1)['balance'],950)
        with self.assertRaises(ValueError): self.s.casino_create(20,1,50,1061)
        self.assertEqual(len(self.s.casino_active()),1)

    def test_one_table_capacity_and_deadline(self):
        row=self.create(5)
        with self.assertRaises(ValueError): self.s.casino_join(row['id'],6,1001)
        with self.assertRaises(ValueError): self.s.casino_create(10,6,50,1001)
        self.s.casino_void(row['id'],'Test')
        second=self.s.casino_create(10,6,50,1100)
        with self.assertRaises(ValueError): self.s.casino_join(second['id'],7,1160)

    def test_invalid_bets_and_insufficient_funds_leave_no_game(self):
        for bet in (0,11,501):
            with self.assertRaises(ValueError): self.s.casino_create(10,1,bet,1000)
        self.s.casino_account(1)
        self.s.db.execute('UPDATE casino_accounts SET balance=0 WHERE member=1'); self.s.db.commit()
        with self.assertRaises(ValueError): self.s.casino_create(10,1,50,1000)
        self.assertEqual(self.s.casino_active(),[])

    def test_leave_refunds_and_owner_cancel_refunds_everyone_once(self):
        row=self.create(3)
        self.s.casino_leave(row['id'],2,1002)
        self.assertEqual(self.s.casino_account(2)['balance'],1000)
        self.s.casino_leave(row['id'],1,1003)
        self.s.casino_void(row['id'],'Repeat')
        for member in (1,2,3): self.assertEqual(self.s.casino_account(member)['balance'],1000)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM casino_seats').fetchone()[0],0)

    def test_only_owner_starts_and_only_current_player_acts(self):
        row=self.create(2)
        with self.assertRaises(ValueError): self.s.casino_start(row['id'],2,1002)
        with patch('casino_store.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda deck:deck.reverse()
            row=self.s.casino_start(row['id'],1,1002)
        with self.assertRaises(ValueError): self.s.casino_move(row['id'],2,'hit',row['revision'],1003)
        with self.assertRaises(ValueError): self.s.casino_join(row['id'],3,1003)

    def test_win_loss_push_and_3_to_2_natural(self):
        for hand,dealer,payout,result in [([9,8],[9,6],100,'win'),([9,6],[9,8],0,'loss'),([9,6],[9,6],50,'push'),([0,9],[9,6],125,'win'),([0,9],[13,9],50,'push'),([9,1,8],[13,9],0,'loss')]:
            with self.subTest(hand=hand,dealer=dealer):
                self.s.db.close(); self.s=Store(':memory:')
                row=self.start(); self.rig(row,hand,dealer,[2,3,4])
                resultrow=self.s.casino_move(row['id'],1,'stand',row['revision'],1003)
                player=json.loads(resultrow['state'])['players'][0]
                self.assertEqual((player['payout'],player['result']),(payout,result))
                self.assertEqual(self.s.casino_account(1)['balance'],950+payout)
                with self.assertRaises(ValueError): self.s.casino_move(row['id'],1,'stand',row['revision'],1004)
                self.assertEqual(self.s.casino_account(1)['balance'],950+payout)

    def test_double_debits_once_and_draws_only_one_card(self):
        row=self.start(); self.rig(row,[9,1],[9,6],[7])
        row=self.s.casino_move(row['id'],1,'double',row['revision'],1003)
        player=json.loads(row['state'])['players'][0]
        self.assertEqual((player['stake'],len(player['hand'])),(100,3))
        self.assertEqual(self.s.casino_account(1)['balance'],1100)

    def test_hit_prevents_later_double_and_stale_revision_cannot_draw_twice(self):
        row=self.start(); self.rig(row,[1,2],[9,6],[4,3])
        nextrow=self.s.casino_move(row['id'],1,'hit',row['revision'],1003)
        with self.assertRaises(ValueError): self.s.casino_move(row['id'],1,'hit',row['revision'],1004)
        with self.assertRaises(ValueError): self.s.casino_move(row['id'],1,'double',nextrow['revision'],1004)
        self.assertEqual(len(json.loads(self.s.casino_game(row['id'])['state'])['players'][0]['hand']),3)

    def test_automatic_start_and_expired_turn_gives_next_player_full_window(self):
        row=self.create(2)
        with patch('casino_store.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda deck:deck.reverse()
            row=self.s.casino_tick(row['id'],1065)
        self.assertEqual(row['phase'],'playing')
        row=self.s.casino_tick(row['id'],1200)
        state=json.loads(row['state'])
        self.assertEqual(state['players'][0]['status'],'stand')
        self.assertEqual(row['deadline'],1245)
        self.assertIsNone(self.s.casino_tick(row['id'],1201))

    def test_restart_recovery_refunds_doubled_stakes_once(self):
        row=self.start(2); self.rig(row,[9,1],[9,6],[7])
        row=self.s.casino_move(row['id'],1,'double',row['revision'],1003)
        self.assertEqual(row['phase'],'playing')
        self.s.casino_recover(); self.s.casino_recover()
        self.assertEqual(self.s.casino_account(1)['balance'],1000)
        self.assertEqual(self.s.casino_account(2)['balance'],1000)
        self.assertEqual(self.s.casino_account(1)['wins'],0)

    def test_saved_balances_and_active_stakes_recover_after_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'; s=Store(path)
            s.casino_daily(1,1000); s.casino_create(10,1,100,1000)
            s.db.close(); s=Store(path); s.casino_recover()
            self.assertEqual(s.casino_account(1)['balance'],1500)
            s.db.close()

    def test_cooldown_prevents_cancel_create_spam(self):
        row=self.create(); self.s.casino_void(row['id'],'Test')
        with self.assertRaises(ValueError): self.s.casino_create(10,1,50,1010)
        self.s.casino_create(10,1,50,1060)

    def test_dealer_natural_ends_hand_immediately(self):
        row=self.create()
        def shuffle(deck):
            deck[:]=[c for c in range(52) if c not in (9,7,0,22)]+[22,0,7,9]
        with patch('casino_store.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=shuffle
            row=self.s.casino_start(row['id'],1,1001)
        self.assertEqual(row['phase'],'finished')
        self.assertEqual(self.s.casino_account(1)['balance'],950)

    def test_soft_17_stands_without_drawing(self):
        row=self.start(); self.rig(row,[9,8],[0,5],[9])
        row=self.s.casino_move(row['id'],1,'stand',row['revision'],1003)
        self.assertEqual(len(json.loads(row['state'])['dealer']),2)
        self.assertEqual(self.s.casino_account(1)['balance'],1050)

    def test_failed_double_keeps_original_stake_and_hand(self):
        row=self.start(); self.rig(row,[9,1],[9,6],[7])
        self.s.db.execute('UPDATE casino_accounts SET balance=0 WHERE member=1'); self.s.db.commit()
        before=self.s.casino_game(row['id'])['state']
        with self.assertRaises(ValueError): self.s.casino_move(row['id'],1,'double',row['revision'],1003)
        self.assertEqual(self.s.casino_game(row['id'])['state'],before)
        self.assertEqual(self.s.db.execute('SELECT stake FROM casino_seats WHERE member=1').fetchone()[0],50)

    def test_many_five_player_rounds_settle_once_with_consistent_wallets(self):
        balances={member:100000 for member in range(1,6)}
        for member in balances:
            self.s.casino_account(member)
            self.s.db.execute('UPDATE casino_accounts SET balance=100000 WHERE member=?',(member,))
        self.s.db.commit()
        for game in range(40):
            now=1000+game*200
            row=self.s.casino_create(10,1,50,now)
            for member in range(2,6): self.s.casino_join(row['id'],member,now+1)
            row=self.s.casino_start(row['id'],1,now+2)
            while row['phase']=='playing':
                current=next(p for p in json.loads(row['state'])['players'] if p['status']=='playing')
                row=self.s.casino_move(row['id'],current['id'],'stand',row['revision'],now+3)
            for player in json.loads(row['state'])['players']:
                balances[player['id']]+=player['payout']-player['stake']
                account=self.s.casino_account(player['id'])
                self.assertEqual(account['balance'],balances[player['id']])
                self.assertEqual(account['wins']+account['losses']+account['pushes'],game+1)
            self.assertFalse(self.s.casino_active())
            self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM casino_seats').fetchone()[0],0)


class CasinoBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.guild.id=100
        self.channel=MagicMock(spec=discord.TextChannel)
        self.channel.id=10; self.channel.guild=self.guild; self.i.channel=self.channel
        self.channel.permissions_for=lambda m:NS(view_channel=True,send_messages=True,embed_links=True,read_message_history=True)
        self.bot.save_community('casino',dict(channel=10,enabled=True,rules=88))
        self.i.response.defer=AsyncMock(); self.i.response.send_message=AsyncMock()
        self.i.user=self.owner; self.i.client=self.bot; self.i.message=NS(id=99)
        self.channel.send=AsyncMock(return_value=NS(id=99))
        self.card=NS(id=88,edit=AsyncMock())
        self.channel.fetch_message=AsyncMock(return_value=self.card)
        self.guild.fetch_channel=AsyncMock(return_value=self.channel)
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_no_hole_card_or_deck_exposed_and_buttons_have_revision(self):
        row=self.bot.store.casino_create(10,1,50,1000)
        with patch('casino_store.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda deck:deck.reverse()
            row=self.bot.store.casino_start(row['id'],1,1001)
        embed=game_embed(row)
        self.assertIn('🂠',embed.fields[0].value)
        self.assertNotIn('deck',str(embed.to_dict()))
        view=game_view(row)
        self.assertTrue(view.is_persistent())
        for button in view.children:
            self.assertIn(f':{row["revision"]}:',button.custom_id)
        self.assertLess(len(embed),6000)

    async def test_failed_table_publication_refunds_chips(self):
        self.channel.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'No access')
        with self.assertRaises(discord.Forbidden): await self.bot.tree.get_command('blackjack').callback(self.i,50)
        self.assertEqual(self.bot.store.casino_account(1)['balance'],1000)
        self.assertFalse(self.bot.store.casino_active())

    async def test_wrong_channel_or_message_never_changes_stake(self):
        row=self.bot.store.casino_create(10,1,50); self.bot.store.casino_message(row['id'],99)
        self.i.message.id=98
        with self.assertRaises(ValueError): await self.bot.casino_action(self.i,row['id'],0,'leave')
        self.i.message.id=99; self.i.channel_id=11
        with self.assertRaises(ValueError): await self.bot.casino_action(self.i,row['id'],0,'leave')
        self.assertEqual(self.bot.store.casino_account(1)['balance'],950)

    async def test_message_edit_failure_voids_active_game(self):
        row=self.bot.store.casino_create(10,1,50); self.bot.store.casino_message(row['id'],99)
        self.card.edit.side_effect=discord.NotFound(NS(status=404,reason='Not Found'),'Deleted')
        with self.assertLogs('redfear',level='ERROR'):
            await self.bot.update_casino_message(self.guild,self.bot.store.casino_game(row['id']))
        self.assertEqual(self.bot.store.casino_account(1)['balance'],1000)
        self.assertEqual(self.bot.store.casino_game(row['id'])['phase'],'cancelled')

    async def test_restart_old_button_reports_cancelled_without_new_charge(self):
        row=self.bot.store.casino_create(10,1,50); self.bot.store.casino_message(row['id'],99)
        self.bot.store.casino_recover()
        with self.assertRaises(ValueError): await self.bot.casino_action(self.i,row['id'],0,'join')
        self.assertEqual(self.bot.store.casino_account(1)['balance'],1000)

    async def test_stop_refunds_and_blocks_new_tables(self):
        row=self.bot.store.casino_create(10,1,50)
        self.i.user=self.staff_member
        await self.bot.tree.get_command('casino_stop').callback(self.i)
        self.assertFalse(self.bot.casino_config()['enabled'])
        self.assertEqual(self.bot.store.casino_account(1)['balance'],1000)
        with self.assertRaises(ValueError): await self.bot.tree.get_command('blackjack').callback(self.i,50)

    async def test_setup_reuses_channel_and_rules_message(self):
        self.i.user=self.staff_member; self.guild.create_text_channel=AsyncMock()
        await self.bot.tree.get_command('casino_setup').callback(self.i)
        self.guild.create_text_channel.assert_not_awaited(); self.channel.send.assert_not_awaited()
        self.card.edit.assert_awaited_once()

    async def test_nonadmin_cannot_setup_and_disabled_daily_refused(self):
        with self.assertRaises(ValueError): await self.bot.tree.get_command('casino_setup').callback(self.i)
        self.bot.save_community('casino',dict(channel=10,enabled=False))
        with self.assertRaises(ValueError): await self.bot.tree.get_command('casino_daily').callback(self.i)
        self.assertIsNone(self.bot.store.db.execute('SELECT * FROM casino_accounts WHERE member=1').fetchone())
