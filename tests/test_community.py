import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import test_features as fixtures
from community import CommunityRoles


class CommunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.guild.id = 100
        self.everyone.permissions.administrator = False
        self.notification = discord.Role(guild=self.guild, state=None, data=dict(id='500', name='Evenimente', permissions='0', position=1))
        self.bot_member.top_role = discord.Role(guild=self.guild, state=None, data=dict(id='600', name='Bot', permissions='0', position=10))
        self.bot_member.guild_permissions.manage_roles = True
        self.guild.channels = []
        self.guild.get_role = lambda n: self.notification if n == 500 else self.role if n == 400 else None
        self.room = MagicMock(spec=discord.TextChannel)
        self.room.id = 700
        self.room.edit = AsyncMock()
        self.room.send = AsyncMock(return_value=NS(id=88))
        self.room.guild = self.guild
        self.room.permissions_for.return_value = NS(view_channel=True, send_messages=True, embed_links=True)
        self.guild.get_channel = lambda n: self.room if n == 700 else None
        self.guild.fetch_channel = AsyncMock(return_value=self.room)
        self.owner.guild = self.guild
        self.owner.pending = False
        self.owner.add_roles = AsyncMock(); self.owner.remove_roles = AsyncMock()
        self.guild.fetch_member = AsyncMock(return_value=self.owner)
        self.i.response.defer = AsyncMock(); self.i.response.send_message = AsyncMock()
        self.i.channel_id = 700; self.i.message = NS(id=88)
        self.bot.save_community('roles',dict(channel=700,message=88,roles=[500]))

    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_buttons_persist_and_limits(self):
        view = CommunityRoles(self.bot,[self.notification])
        self.assertTrue(view.is_persistent()); self.assertEqual(len(view.children),1)
        self.assertEqual(view.children[0].label,'Evenimente')
        self.assertEqual(len(CommunityRoles(self.bot).children),5)
        self.assertTrue(self.bot.intents.members)

    async def test_add_and_remove_role_using_fresh_member(self):
        self.i.user=self.owner
        await self.bot.toggle_community_role(self.i,0)
        self.owner.add_roles.assert_awaited_once_with(self.notification,reason='Rol opțional ales de membru',atomic=True)
        self.owner.roles=[self.notification]
        await self.bot.toggle_community_role(self.i,0)
        self.owner.remove_roles.assert_awaited_once()
        self.assertTrue(self.i.followup.send.await_args.kwargs['ephemeral'])

    async def test_stale_panel_wrong_server_and_invalid_slot_rejected(self):
        for attr,value in [('guild_id',101),('channel_id',701)]:
            old=getattr(self.i,attr); setattr(self.i,attr,value)
            with self.assertRaises(ValueError): await self.bot.toggle_community_role(self.i,0)
            setattr(self.i,attr,old)
        self.i.message.id=89
        with self.assertRaises(ValueError): await self.bot.toggle_community_role(self.i,0)
        self.i.message.id=88
        with self.assertRaises(ValueError): await self.bot.toggle_community_role(self.i,5)
        self.owner.add_roles.assert_not_awaited()

    async def test_elevated_or_higher_role_refused_at_click(self):
        self.notification._permissions=discord.Permissions(administrator=True).value
        with self.assertRaises(ValueError): await self.bot.toggle_community_role(self.i,0)
        self.notification._permissions=0; self.notification.position=11
        with self.assertRaises(ValueError): await self.bot.toggle_community_role(self.i,0)
        self.owner.add_roles.assert_not_awaited()

    async def test_staff_and_reviewer_roles_refused_even_without_permissions(self):
        self.bot.roles.add(500)
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild,self.notification)
        self.bot.roles.remove(500)
        self.bot.store.set_setting('helper:config',json.dumps(dict(reviewer_role=500)))
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild,self.notification)

    async def test_channel_access_grant_refused(self):
        ch=NS(overwrites_for=lambda r:discord.PermissionOverwrite(view_channel=True))
        self.guild.channels=[ch]
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild,self.notification)
        self.guild.channels=[NS(overwrites_for=lambda r:discord.PermissionOverwrite(view_channel=False))]
        self.assertIs(self.bot.safe_community_role(self.guild,self.notification),self.notification)

    async def test_role_management_permission_required(self):
        self.bot_member.guild_permissions.manage_roles=False
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild,self.notification)

    async def test_welcome_and_autorole_new_human_only(self):
        self.bot.save_community('welcome',dict(channel=700,message='Salut! @everyone',role=500,enabled=True))
        await self.bot.on_member_join(self.owner)
        self.owner.add_roles.assert_awaited_once()
        self.room.send.assert_awaited_once()
        self.assertFalse(self.room.send.await_args.kwargs['allowed_mentions'].everyone)
        self.owner.bot=True
        await self.bot.on_member_join(self.owner)
        self.assertEqual(self.room.send.await_count,1)

    async def test_disabled_welcome_does_nothing(self):
        self.bot.save_community('welcome',dict(channel=700,message='Salut',role=500,enabled=False))
        await self.bot.on_member_join(self.owner)
        self.owner.add_roles.assert_not_awaited(); self.room.send.assert_not_awaited()

    async def test_unsafe_autorole_does_not_prevent_welcome(self):
        self.bot.save_community('welcome',dict(channel=700,message='Salut',role=500,enabled=True))
        self.notification._permissions=discord.Permissions(manage_roles=True).value
        with self.assertLogs('redfear',level='ERROR'):
            await self.bot.on_member_join(self.owner)
        self.owner.add_roles.assert_not_awaited(); self.room.send.assert_awaited_once()

    async def test_welcome_test_does_not_assign_role(self):
        self.bot.save_community('welcome',dict(channel=700,message='Salut',role=500,enabled=True))
        await self.bot.tree.get_command('welcome_test').callback(self.i)
        self.assertTrue(self.i.response.send_message.await_args.kwargs['ephemeral'])
        self.owner.add_roles.assert_not_awaited()

    async def test_only_admin_can_configure(self):
        self.i.user=self.owner
        with self.assertRaises(ValueError):
            await self.bot.tree.get_command('welcome_setup').callback(self.i,self.room,'Salut',self.notification)
        with self.assertRaises(ValueError):
            await self.bot.tree.get_command('community_logs').callback(self.i,self.role)
        self.room.edit.assert_not_awaited()

    async def test_private_logs_harden_before_send_and_exclude_content(self):
        self.role.is_default.return_value=False; self.role.managed=False
        self.bot.save_community('logs',dict(channel=700,role=400))
        self.bot.get_guild=lambda n:self.guild
        order=[]
        async def edit(**kw): order.append('private')
        async def send(**kw): order.append('send')
        self.room.edit.side_effect=edit; self.room.send.side_effect=send
        await self.bot.on_member_join(self.owner)
        before=NS(roles=[],guild=self.guild,id=1)
        after=NS(roles=[self.notification],guild=self.guild,id=1)
        await self.bot.on_member_update(before,after)
        await self.bot.community_flush.coro(self.bot)
        self.assertEqual(order,['private','send'])
        overrides=self.room.edit.await_args.kwargs['overwrites']
        self.assertEqual(set(overrides),{self.everyone,self.role,self.bot_member})
        self.assertFalse(overrides[self.everyone].view_channel)
        self.assertIsNone(overrides[self.bot_member].manage_roles)
        self.assertEqual(len(self.bot.community_events),0)
        embed=self.room.send.await_args.kwargs['embed']
        self.assertEqual(len(embed.fields),2)
        self.assertIn('500',embed.fields[1].value)
        self.assertNotIn('content',embed.to_dict())

    async def test_logs_do_not_send_on_failed_privacy_and_retry(self):
        self.role.is_default.return_value=False; self.role.managed=False
        self.bot.save_community('logs',dict(channel=700,role=400))
        self.bot.get_guild=lambda n:self.guild
        self.bot.queue_community(100,'Test','ID 1')
        self.room.edit.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Forbidden')
        with self.assertLogs('redfear',level='ERROR'):
            await self.bot.community_flush.coro(self.bot)
        self.room.send.assert_not_awaited(); self.assertEqual(len(self.bot.community_events),1)
        self.room.edit.side_effect=None
        await self.bot.community_flush.coro(self.bot)
        self.room.send.assert_awaited_once(); self.assertFalse(self.bot.community_events)

    async def test_logs_disabled_other_guild_and_bounded_queue(self):
        self.bot.queue_community(100,'Test','secret'); self.assertFalse(self.bot.community_events)
        self.bot.save_community('logs',dict(channel=700,role=400))
        self.bot.queue_community(101,'Test','secret'); self.assertFalse(self.bot.community_events)
        for x in range(250): self.bot.queue_community(100,'Event',str(x))
        self.assertEqual(len(self.bot.community_events),200)
        await self.bot.tree.get_command('community_logs_stop').callback(self.i)
        self.assertFalse(self.bot.community_events); self.assertIsNone(self.bot.community_config('logs'))

    async def test_roles_setup_reuses_panel_and_rejects_duplicate_roles(self):
        self.room.fetch_message=AsyncMock(return_value=NS(edit=AsyncMock(),id=88))
        callback=self.bot.tree.get_command('roles_setup').callback
        await callback(self.i,self.room,self.notification)
        self.room.send.assert_not_awaited()
        self.room.fetch_message.return_value.edit.assert_awaited_once()
        with self.assertRaises(ValueError): await callback(self.i,self.room,self.notification,self.notification)

    async def test_settings_survive_reopening_store(self):
        from store import Store
        self.bot.save_community('welcome',dict(channel=700,message='Salut',role=500,enabled=True))
        other=Store(self.bot.database_path)
        self.assertEqual(json.loads(other.setting('community:welcome'))['role'],500)
        other.db.close()

    async def test_membership_screening_delays_autorole(self):
        self.bot.save_community('welcome',dict(channel=700,message='Salut',role=500,enabled=True))
        self.owner.pending=True
        await self.bot.on_member_join(self.owner)
        self.owner.add_roles.assert_not_awaited()
        before=NS(guild=self.guild,pending=True,roles=[])
        self.owner.pending=False
        await self.bot.on_member_update(before,self.owner)
        self.owner.add_roles.assert_awaited_once()
        self.assertEqual(self.room.send.await_count,1)
