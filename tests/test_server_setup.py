import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import test_features as fixtures
import test_community as community_fixtures
from server_setup import LayoutPreview, normalized, snapshot


def channel(number,name,category=None):
    ch=MagicMock(spec=discord.TextChannel)
    ch.id=number; ch.name=name; ch.category_id=category; ch.topic=None; ch._overwrites=[]
    async def edit(**kwargs):
        ch.name=kwargs.get('name',ch.name)
        ch.category_id=kwargs.get('category',NS(id=ch.category_id)).id
        ch.topic=kwargs.get('topic',ch.topic)
        return ch
    ch.edit=AsyncMock(side_effect=edit)
    return ch


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.guild.id=100
        self.bot_member.guild_permissions.manage_channels=True
        self.items=[channel(50,'👋・bun-venit'),channel(51,'regulament'),channel(52,'aplicatii-helper')]
        self.guild.fetch_channels=AsyncMock(side_effect=lambda:list(self.items))
        async def create_category(name,**kwargs):
            cat=MagicMock(spec=discord.CategoryChannel)
            cat.id=1000+len(self.items); cat.name=name
            self.items.append(cat)
            return cat
        self.guild.create_category=AsyncMock(side_effect=create_category)
        async def create_text(name,**kwargs):
            ch=channel(2000+len(self.items),name,kwargs['category'].id)
            ch.topic=kwargs['topic']; self.items.append(ch)
            return ch
        self.guild.create_text_channel=AsyncMock(side_effect=create_text)
        self.i.response.defer=AsyncMock(); self.i.edit_original_response=AsyncMock()
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_preview_is_read_only_and_skips_unconfigured_private_room(self):
        plan=await self.bot.build_server_layout(self.guild)
        self.assertEqual([e['id'] for e in plan['entries']],[50,51])
        self.guild.create_category.assert_not_awaited()
        for ch in self.items: ch.edit.assert_not_awaited()
        self.assertEqual(normalized('📌 INFORMAȚII'),'informatii')

    async def test_protected_ticket_archive_and_categories_rejected(self):
        self.bot.save_community('layout-map',dict(rules=10))
        self.items.append(channel(10,'tichet-0001'))
        with self.assertRaises(ValueError): await self.bot.build_server_layout(self.guild)
        self.bot.save_community('layout-map',dict(rules=300))
        self.items.append(channel(300,'arhiva'))
        with self.assertRaises(ValueError): await self.bot.build_server_layout(self.guild)

    async def test_duplicate_aliases_require_explicit_mapping(self):
        self.items.append(channel(55,'📜・regulament'))
        with self.assertRaises(ValueError): await self.bot.build_server_layout(self.guild)
        self.bot.save_community('layout-map',dict(rules=55))
        plan=await self.bot.build_server_layout(self.guild)
        self.assertEqual(next(e for e in plan['entries'] if e['key']=='rules')['id'],55)

    async def test_apply_keeps_raw_permissions_and_messages(self):
        self.items[0]._overwrites=[NS(id=9999,type=1,allow=1024,deny=0)]
        before=snapshot(self.items[0])[-1]
        plan=await self.bot.build_server_layout(self.guild)
        await self.bot.apply_server_layout(self.guild,plan)
        options=self.items[0].edit.await_args.kwargs
        self.assertFalse(options['sync_permissions']); self.assertNotIn('overwrites',options)
        self.assertEqual(snapshot(self.items[0])[-1],before)
        self.items[0].delete.assert_not_called()
        self.assertEqual(self.items[1].name,'📜・regulament')
        self.assertEqual(self.items[0].category_id,self.items[1].category_id)

    async def test_restart_repeat_reuses_categories_and_channel_ids(self):
        plan=await self.bot.build_server_layout(self.guild)
        await self.bot.apply_server_layout(self.guild,plan)
        await self.bot.apply_server_layout(self.guild,await self.bot.build_server_layout(self.guild))
        self.guild.create_category.assert_awaited_once()
        self.guild.create_text_channel.assert_not_awaited()
        self.assertEqual(self.bot.community_config('layout-channels')['welcome'],50)

    async def test_changed_snapshot_aborts_before_creating_categories(self):
        plan=await self.bot.build_server_layout(self.guild)
        self.items[1]._overwrites=[NS(id=42,type=1,allow=0,deny=1024)]
        with self.assertRaises(ValueError): await self.bot.apply_server_layout(self.guild,plan)
        self.guild.create_category.assert_not_awaited()
        self.items[0].edit.assert_not_awaited()

    async def test_new_channels_require_explicit_option_and_never_create_private_feature_rooms(self):
        plan=await self.bot.build_server_layout(self.guild,True)
        self.assertEqual(len(plan['entries']),9)
        await self.bot.apply_server_layout(self.guild,plan)
        self.assertEqual(self.guild.create_text_channel.await_count,7)
        for call in self.guild.create_text_channel.await_args_list:
            overwrite=call.kwargs['overwrites'][self.everyone]
            self.assertTrue(overwrite.view_channel)
        rules=next(x for x in plan['entries'] if x['key']=='announcements')
        self.assertTrue(rules['readonly'])

    async def test_private_rooms_keep_permissions_when_moved(self):
        self.bot.store.set_setting('helper:config','{"review_channel":52,"panel_channel":53}')
        self.items.append(channel(53,'aplicatii-staff'))
        self.items[2]._overwrites=[NS(id=100,type=0,allow=0,deny=1024),NS(id=400,type=0,allow=1024,deny=0)]
        plan=await self.bot.build_server_layout(self.guild)
        await self.bot.apply_server_layout(self.guild,plan)
        options=self.items[2].edit.await_args.kwargs
        self.assertNotIn('overwrites',options); self.assertFalse(options['sync_permissions'])
        staff=next(c for c in self.guild.create_category.await_args_list if c.args[0]=='🔒 STAFF')
        self.assertFalse(staff.kwargs['overwrites'][self.everyone].view_channel)
        self.assertNotIn('topic',options)

    async def test_old_preview_cannot_duplicate_created_category(self):
        first=await self.bot.build_server_layout(self.guild,True)
        second=copy.deepcopy(first)
        await self.bot.apply_server_layout(self.guild,first)
        count=self.guild.create_category.await_count
        with self.assertRaises(ValueError): await self.bot.apply_server_layout(self.guild,second)
        self.assertEqual(self.guild.create_category.await_count,count)

    async def test_confirm_rechecks_actor_and_admin_and_runs_once(self):
        plan=await self.bot.build_server_layout(self.guild)
        view=LayoutPreview(self.bot,2,plan)
        self.i.user=self.owner
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.i.user=self.staff_member
        await view.children[0].callback(self.i)
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.assertTrue(view.used)

    async def test_partial_failure_retry_reuses_saved_progress(self):
        plan=await self.bot.build_server_layout(self.guild)
        self.items[1].edit.side_effect=RuntimeError('API unavailable')
        with self.assertLogs('redfear',level='ERROR'):
            with self.assertRaises(ValueError): await self.bot.apply_server_layout(self.guild,plan)
        self.assertEqual(self.bot.community_config('layout-channels')['welcome'],50)
        self.assertTrue(self.bot.community_config('layout-categories'))
        retry=await self.bot.build_server_layout(self.guild)
        self.assertEqual(retry['categories']['info']['id'],self.items[0].category_id)


class DefaultRolesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await community_fixtures.CommunityTests.asyncSetUp(self)
        self.room.permissions_for.return_value.read_message_history=True
        self.guild.fetch_roles=AsyncMock(return_value=[])
        self.guild.create_role=AsyncMock()
        self.room.fetch_message=AsyncMock(return_value=NS(id=88,edit=AsyncMock()))
    async def asyncTearDown(self): await community_fixtures.CommunityTests.asyncTearDown(self)

    async def test_create_defaults_then_reuse_without_duplicates(self):
        current=[]
        async def create(**kwargs):
            role=discord.Role(guild=self.guild,state=None,data=dict(id=str(700+len(current)),name=kwargs['name'],permissions='0',position=1))
            current.append(role)
            self.assertEqual(kwargs['permissions'].value,0)
            self.assertFalse(kwargs['mentionable'])
            return role
        self.guild.create_role.side_effect=create
        self.guild.fetch_roles.side_effect=lambda:list(current)
        callback=self.bot.tree.get_command('roles_create_defaults').callback
        await callback(self.i,self.room)
        await callback(self.i,self.room)
        self.assertEqual(self.guild.create_role.await_count,2)
        self.assertEqual(len(self.bot.community_config('roles')['roles']),2)

    async def test_unsafe_existing_role_refused_without_creating_anything(self):
        self.notification.name='Evenimente'
        self.notification._permissions=discord.Permissions(administrator=True).value
        self.guild.fetch_roles.return_value=[self.notification]
        with self.assertRaises(ValueError): await self.bot.tree.get_command('roles_create_defaults').callback(self.i,self.room)
        self.guild.create_role.assert_not_awaited()

    async def test_duplicate_existing_role_requires_manual_selection(self):
        self.notification.name='Evenimente'
        self.guild.fetch_roles.return_value=[self.notification,self.notification]
        with self.assertRaises(ValueError): await self.bot.tree.get_command('roles_create_defaults').callback(self.i,self.room)
        self.guild.create_role.assert_not_awaited()
