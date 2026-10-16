import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import test_features as fixtures
from moderation import SanctionConfirmation, case_embed
from store import Store


class ModerationStoreTests(unittest.TestCase):
    def test_persistence_pagination_and_only_applied_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'; s=Store(path)
            for action,status in [('warn','applied')]*7+[('ban','applied'),('warn','failed'),('kick','uncertain')]:
                n=s.moderation_create(2,1,action,'Motiv'); s.moderation_finish(n,status)
            pending=s.moderation_create(2,1,'ban','În curs')
            self.assertEqual(len(s.moderation_history(1,1,True)),5)
            self.assertEqual(len(s.moderation_history(1,2,True)),2)
            self.assertEqual(s.moderation_history(3),[])
            s.db.close(); s=Store(path)
            self.assertEqual(s.moderation_case(pending)['status'],'pending')
            self.assertEqual(s.moderation_case(10)['status'],'uncertain')
            s.moderation_log_state(10,1)
            self.assertEqual(s.moderation_case(10)['log_state'],1)
            s.db.close()


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.guild.id=100; self.guild.owner_id=999
        def role(number,position):
            return discord.Role(guild=self.guild,state=None,data=dict(id=str(number),name=f'Role {number}',permissions='0',position=position))
        self.helper_role=role(410,2); self.mod_role=role(420,5)
        self.owner.roles=[]; self.owner.top_role=role(430,1)
        self.owner.guild_permissions.administrator=False
        self.owner.timeout=AsyncMock(); self.owner.kick=AsyncMock()
        self.staff_member.roles=[self.mod_role]; self.staff_member.top_role=self.mod_role
        self.staff_member.guild_permissions.administrator=False
        self.staff_member.guild_permissions.moderate_members=True
        self.staff_member.guild_permissions.kick_members=True
        self.staff_member.guild_permissions.ban_members=True
        self.bot_member.top_role=role(440,9)
        self.bot_member.guild_permissions.moderate_members=True
        self.bot_member.guild_permissions.kick_members=True
        self.bot_member.guild_permissions.ban_members=True
        members={1:self.owner,2:self.staff_member,3:self.extra,4:self.bot_member}
        self.guild.fetch_member=AsyncMock(side_effect=lambda n:members[n])
        self.guild.ban=AsyncMock()
        self.i.response.defer=AsyncMock(); self.i.response.send_message=AsyncMock()
        self.i.response.edit_message=AsyncMock()
        self.bot.save_community('moderation',dict(helper=410,moderator=420))
        self.bot.log_moderation_case=AsyncMock(return_value=True)
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_helper_warns_but_cannot_timeout_even_with_native_permissions(self):
        self.staff_member.roles=[self.helper_role]; self.staff_member.top_role=self.helper_role
        row=await self.bot.execute_moderation(self.i,1,'warn','Comportament nepotrivit')
        self.assertEqual(row['status'],'applied')
        with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'timeout','Motiv',5)
        self.owner.timeout.assert_not_awaited()

    async def test_ordinary_member_cannot_warn_or_read_history(self):
        self.i.user=self.extra
        self.extra.roles=[]; self.extra.guild_permissions.administrator=False
        for action in ('warn','history','ban'):
            with self.assertRaises(ValueError): await self.bot.moderation_actor(self.i,action)
        self.assertEqual(self.bot.store.moderation_history(1),[])

    async def test_self_owner_admin_bots_and_higher_targets_protected(self):
        with self.assertRaises(ValueError): await self.bot.moderation_authorize(self.i,2,'warn')
        for attr,value in [('id',999),('bot',True)]:
            old=getattr(self.owner,attr); setattr(self.owner,attr,value)
            with self.assertRaises(ValueError): await self.bot.moderation_authorize(self.i,1,'ban')
            setattr(self.owner,attr,old)
        self.owner.guild_permissions.administrator=True
        with self.assertRaises(ValueError): await self.bot.moderation_authorize(self.i,1,'warn')
        self.owner.guild_permissions.administrator=False
        self.owner.top_role=self.mod_role
        with self.assertRaises(ValueError): await self.bot.moderation_authorize(self.i,1,'warn')

    async def test_native_actor_and_bot_permissions_both_required(self):
        self.staff_member.guild_permissions.kick_members=False
        with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'kick','Motiv')
        self.staff_member.guild_permissions.kick_members=True
        self.bot_member.guild_permissions.kick_members=False
        with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'kick','Motiv')
        self.owner.kick.assert_not_awaited()
        self.assertFalse(self.bot.store.moderation_history(1))

    async def test_bot_hierarchy_blocks_native_actions(self):
        self.bot_member.top_role=self.owner.top_role
        with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'timeout','Motiv',3)
        self.owner.timeout.assert_not_awaited()

    async def test_timeout_and_untimeout_persist_and_use_native_timeout(self):
        row=await self.bot.execute_moderation(self.i,1,'timeout','Spam repetat',5)
        self.assertEqual(row['status'],'applied'); self.assertIsNotNone(row['until_time'])
        self.assertIsNotNone(self.owner.timeout.await_args.args[0].tzinfo)
        row=await self.bot.execute_moderation(self.i,1,'untimeout','Situație clarificată')
        self.assertIsNone(self.owner.timeout.await_args.args[0])
        self.assertEqual(row['action'],'untimeout')

    async def test_invalid_duration_and_blank_reason_never_create_case(self):
        for minutes in (0,40321):
            with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'timeout','Motiv',minutes)
        with self.assertRaises(ValueError): await self.bot.execute_moderation(self.i,1,'warn','   ')
        self.assertFalse(self.bot.store.moderation_history(1))

    async def test_ban_preview_has_no_effect_then_confirm_preserves_messages(self):
        await self.bot.preview_sanction(self.i,1,'ban','Abuz repetat')
        view=self.i.followup.send.await_args.kwargs['view']
        self.guild.ban.assert_not_awaited()
        self.assertFalse(self.bot.store.moderation_history(1))
        await view.children[0].callback(self.i)
        self.guild.ban.assert_awaited_once()
        self.assertEqual(self.guild.ban.await_args.kwargs['delete_message_seconds'],0)
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.assertEqual(len(self.bot.store.moderation_history(1)),1)

    async def test_confirm_rechecks_role_and_denies_different_actor(self):
        view=SanctionConfirmation(self.bot,2,1,'kick','Motiv')
        self.i.user=self.extra
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.i.user=self.staff_member; self.staff_member.roles=[]
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.owner.kick.assert_not_awaited()

    async def test_cancel_does_not_create_case(self):
        view=SanctionConfirmation(self.bot,2,1,'ban','Motiv')
        await view.children[1].callback(self.i)
        self.assertTrue(view.used); self.guild.ban.assert_not_awaited()
        self.assertFalse(self.bot.store.moderation_history(1))

    async def test_discord_403_is_failed_and_not_a_successful_warning(self):
        self.owner.kick.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),dict(code=50013,message='Missing Permissions'))
        row=await self.bot.execute_moderation(self.i,1,'kick','Motiv')
        self.assertEqual(row['status'],'failed')
        self.assertIn('50013',row['detail'])
        self.assertEqual(self.bot.store.moderation_history(1,1,True),[])

    async def test_network_interruption_marks_uncertain_no_replay(self):
        self.guild.ban.side_effect=asyncio.TimeoutError()
        with self.assertRaises(asyncio.TimeoutError): await self.bot.execute_moderation(self.i,1,'ban','Motiv')
        self.assertEqual(self.bot.store.moderation_history(1)[0]['status'],'uncertain')
        self.guild.ban.assert_awaited_once()

    async def test_warnings_are_private_and_embed_limits_hold(self):
        for _ in range(5): await self.bot.execute_moderation(self.i,1,'warn','*'*500)
        await self.bot.show_moderation_history(self.i,1,1,True)
        kwargs=self.i.followup.send.await_args.kwargs
        self.assertTrue(kwargs['ephemeral']); self.assertLessEqual(len(kwargs['embed']),6000)
        for field in kwargs['embed'].fields: self.assertLessEqual(len(field.value),1024)
        embed=case_embed(self.bot.store.moderation_case(1))
        for field in embed.fields: self.assertLessEqual(len(field.value),1024)

    async def test_configured_mod_roles_are_never_self_assignable(self):
        self.bot_member.guild_permissions.manage_roles=True
        self.guild.channels=[]
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild,self.mod_role)

    async def test_log_uses_private_room_and_failure_keeps_case(self):
        from moderation import Moderation
        row=await self.bot.execute_moderation(self.i,1,'warn','Motiv')
        self.bot.save_community('logs',dict(channel=700,role=420))
        room=NS(send=AsyncMock())
        self.bot.community_log_room=AsyncMock(return_value=room)
        self.assertTrue(await Moderation.log_moderation_case(self.bot,self.guild,row))
        self.assertEqual(self.bot.store.moderation_case(row['id'])['log_state'],1)
        self.assertFalse(room.send.await_args.kwargs['allowed_mentions'].everyone)
        self.bot.community_log_room.side_effect=ValueError('Unsafe room')
        with self.assertLogs('redfear',level='ERROR'):
            self.assertFalse(await Moderation.log_moderation_case(self.bot,self.guild,row))
        self.assertEqual(self.bot.store.moderation_case(row['id'])['status'],'applied')
        self.assertEqual(self.bot.store.moderation_case(row['id'])['log_state'],-1)
