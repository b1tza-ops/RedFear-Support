import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import test_features as fixtures
from applications import HelperPanel, HelperReview, HelperApplicationModal, HelperDecisionModal, QUESTIONS, application_embed
from store import Store


def answers():
    return [{'question':q,'answer':'a'*maximum} for q,maximum,_ in QUESTIONS]


class HelperStorageTests(unittest.TestCase):
    def setUp(self): self.s=Store(':memory:')
    def tearDown(self): self.s.db.close()
    def create(self,owner=1,now=1000):
        return self.s.helper_create(owner,json.dumps(answers()),20,400,86400,now)

    def test_pending_unique_and_separate_from_tickets(self):
        self.s.create(1,10,'Bug',1)
        number=self.create()
        with self.assertRaises(ValueError): self.create(now=2000)
        self.assertEqual(self.s.helper_latest(1)['id'],number)
        self.assertIsNone(self.s.helper_latest(2))
        self.assertEqual(self.s.channel(10)['status'],'open')

    def test_cooldown_starts_at_decision(self):
        n=self.create(); self.s.helper_set_message(n,99)
        self.s.helper_decide(n,2,'rejected','Experiență insuficientă',2000)
        with self.assertRaises(ValueError): self.create(now=2000+86399)
        self.create(now=2000+86400)

    def test_decision_single_and_no_self_review(self):
        n=self.create()
        with self.assertRaises(ValueError): self.s.helper_decide(n,2,'accepted','Bine',2000)
        self.s.helper_set_message(n,99)
        with self.assertRaises(ValueError): self.s.helper_decide(n,1,'accepted','Bine',2000)
        self.s.helper_decide(n,2,'accepted','Bine',2000)
        with self.assertRaises(ValueError): self.s.helper_decide(n,3,'rejected','Alt motiv',2001)
        self.assertEqual(self.s.helper_application(n)['reviewed_by'],2)

    def test_delivery_failure_retry_and_card_lookup(self):
        n=self.create(); self.s.helper_delivery_failed(n)
        with self.assertRaises(ValueError): self.create(now=1059)
        other=self.create(now=1060); self.s.helper_set_message(other,99)
        self.s.helper_delivery_failed(other)
        self.assertEqual(self.s.helper_card(20,99)['status'],'pending')
        self.assertIsNone(self.s.helper_card(21,99))

    def test_restart_retains_answers_pending_decision_and_dm_state(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'; s=Store(path)
            n=s.helper_create(1,json.dumps(answers()),20,400,86400,1000)
            s.helper_set_message(n,99); s.db.close(); s=Store(path)
            self.assertEqual(json.loads(s.helper_application(n)['answers']),answers())
            s.helper_decide(n,2,'accepted','Bun candidat',2000); s.helper_dm(n,-1)
            s.db.close(); s=Store(path)
            self.assertEqual(s.helper_application(n)['dm_state'],-1)
            with self.assertRaises(ValueError): s.helper_eligible(1,86400,2001)
            s.db.close()


class HelperBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.role.id=400; self.role.is_default.return_value=False; self.role.is_bot_managed.return_value=False
        self.everyone.permissions.administrator=False
        self.room=MagicMock(spec=discord.TextChannel); self.room.id=20
        self.room.edit=AsyncMock(); self.room.send=AsyncMock(return_value=NS(id=99))
        self.room.fetch_message=AsyncMock(return_value=NS(edit=AsyncMock()))
        self.guild.fetch_channel=AsyncMock(return_value=self.room)
        self.guild.get_channel=lambda n:self.channel if n==10 else None
        self.channel.permissions_for=lambda u:NS(view_channel=True)
        self.i.response.defer=AsyncMock(); self.i.message=NS(id=99)
        self.config=dict(panel_channel=10,panel_message=88,review_channel=20,reviewer_role=400,cooldown_days=7,enabled=True)
        self.bot.store.set_setting('helper:config',json.dumps(self.config))
        self.bot.fetch_user=AsyncMock(return_value=NS(send=AsyncMock()))
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)
    def create(self):
        n=self.bot.store.helper_create(1,json.dumps(answers()),20,400,86400,1000)
        self.bot.store.helper_set_message(n,99)
        return n

    async def test_persistent_views_modal_and_embed_limits(self):
        for v in (HelperPanel(self.bot),HelperReview(self.bot)): self.assertTrue(v.is_persistent())
        for modal in (HelperApplicationModal(self.bot,88),HelperDecisionModal(self.bot,1,99,'accepted')):
            self.assertLessEqual(len(modal.children),5)
            for child in modal.children: self.assertLessEqual(len(child.label),45)
        n=self.create(); row=self.bot.store.helper_decide(n,2,'accepted','r'*1000)
        embed=application_embed(row)
        self.assertLessEqual(len(embed),6000)
        for field in embed.fields: self.assertLessEqual(len(field.value),1024)

    async def test_submission_hardens_privacy_before_sending(self):
        order=[]
        async def edit(**kw): order.append('private')
        async def send(**kw): order.append('send'); return NS(id=99)
        self.room.edit.side_effect=edit; self.room.send.side_effect=send
        self.i.user=self.owner
        await self.bot.submit_helper(self.i,88,answers())
        self.assertEqual(order,['private','send'])
        overrides=self.room.edit.await_args.kwargs['overwrites']
        self.assertEqual(set(overrides),{self.everyone,self.role,self.bot_member})
        self.assertFalse(overrides[self.everyone].view_channel)
        self.assertIsNone(overrides[self.bot_member].manage_roles)
        self.assertEqual(self.bot.store.helper_latest(1)['message'],99)
        self.assertFalse(self.room.send.await_args.kwargs['allowed_mentions'].everyone)

    async def test_privacy_failure_never_stores_or_posts_answers(self):
        self.i.user=self.owner; self.room.edit.side_effect=RuntimeError('Forbidden')
        with self.assertRaises(RuntimeError): await self.bot.submit_helper(self.i,88,answers())
        self.assertIsNone(self.bot.store.helper_latest(1)); self.room.send.assert_not_awaited()

    async def test_submission_revalidates_paused_or_stale_panel(self):
        self.i.user=self.owner
        with self.assertRaises(ValueError): await self.bot.submit_helper(self.i,87,answers())
        self.config['enabled']=False; self.bot.store.set_setting('helper:config',json.dumps(self.config))
        with self.assertRaises(ValueError): await self.bot.submit_helper(self.i,88,answers())
        self.room.send.assert_not_awaited()

    async def test_failed_post_does_not_leave_pending_application(self):
        self.i.user=self.owner; self.room.send.side_effect=RuntimeError('offline')
        with self.assertRaises(RuntimeError): await self.bot.submit_helper(self.i,88,answers())
        self.assertEqual(self.bot.store.helper_latest(1)['status'],'failed')

    async def test_reviewer_checks_role_channel_message_and_self(self):
        n=self.create(); self.i.channel_id=20; self.i.user=self.extra
        with self.assertRaises(ValueError): self.bot.helper_require_reviewer(self.i)
        self.extra.roles=[self.role]
        self.assertEqual(self.bot.helper_require_reviewer(self.i)['id'],n)
        self.i.channel_id=10
        with self.assertRaises(ValueError): self.bot.helper_require_reviewer(self.i)
        self.i.channel_id=20; self.i.message.id=98
        with self.assertRaises(ValueError): self.bot.helper_require_reviewer(self.i)
        self.i.user=self.owner; self.owner.guild_permissions.administrator=True
        with self.assertRaises(ValueError): await self.bot.decide_helper(self.i,n,99,'accepted','Motiv')

    async def test_decision_persists_when_dm_blocked_and_double_click_refused(self):
        n=self.create(); self.i.channel_id=20
        user=self.bot.fetch_user.return_value
        user.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),{'message':'DM blocked','code':50007})
        await self.bot.decide_helper(self.i,n,99,'accepted','Bun candidat')
        row=self.bot.store.helper_application(n)
        self.assertEqual((row['status'],row['dm_state']),('accepted',-1))
        with self.assertRaises(ValueError): await self.bot.decide_helper(self.i,n,99,'rejected','Alt motiv')
        self.assertEqual(user.send.await_count,1)
        self.assertTrue(all(b.disabled for b in self.room.fetch_message.return_value.edit.await_args.kwargs['view'].children))

    async def test_status_only_reads_own_application_and_is_private(self):
        self.create(); self.i.user=self.extra
        await self.bot.tree.get_command('helper_status').callback(self.i)
        call=self.i.followup.send.await_args
        self.assertIn('Nu ai trimis',call.args[0]); self.assertTrue(call.kwargs['ephemeral'])

    async def test_missing_application_rejected_cleanly(self):
        self.i.channel_id=20
        with self.assertRaises(ValueError): await self.bot.decide_helper(self.i,999,99,'accepted','Motiv')
        self.room.edit.assert_not_awaited()

    async def test_setup_reuses_panel_and_private_room(self):
        self.guild.id=100; self.role.guild=self.guild; self.channel.guild=self.guild
        self.channel.fetch_message=AsyncMock(return_value=NS(edit=AsyncMock(),id=88))
        self.guild.create_text_channel=AsyncMock()
        callback=self.bot.tree.get_command('helper_setup').callback
        await callback(self.i,self.channel,self.role,7)
        await callback(self.i,self.channel,self.role,7)
        self.guild.create_text_channel.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.channel.fetch_message.return_value.edit.await_count,2)
        self.assertEqual(self.bot.helper_config()['panel_message'],88)

    async def test_setup_never_uses_private_room_as_public_panel(self):
        self.guild.id=100; self.role.guild=self.guild; self.room.guild=self.guild
        with self.assertRaises(ValueError):
            await self.bot.tree.get_command('helper_setup').callback(self.i,self.room,self.role,7)
        self.room.send.assert_not_awaited(); self.room.edit.assert_not_awaited()

    async def test_role_change_also_applies_to_decided_card_recovery(self):
        n=self.create(); self.bot.store.helper_decide(n,2,'accepted','Bun candidat')
        self.bot.store.helper_reviewer_role(20,500)
        self.assertEqual(self.bot.store.helper_application(n)['reviewer_role'],500)

    async def test_resend_does_not_duplicate_delivered_result(self):
        n=self.create(); self.bot.store.helper_decide(n,2,'accepted','Bun candidat')
        callback=self.bot.tree.get_command('helper_resend').callback
        await callback(self.i,n)
        with self.assertRaises(ValueError): await callback(self.i,n)
        self.bot.fetch_user.return_value.send.assert_awaited_once()
