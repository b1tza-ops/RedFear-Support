import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import discord
import test_moderation as fixtures
from bot import Bot
from server_setup import StaffPreview, STAFF_ROLES


class StaffAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ModerationTests.asyncSetUp(self)
        self.i.edit_original_response = AsyncMock()
        self.guild.me.top_role = self.bot_member.top_role
        self.bot_member.guild_permissions.manage_roles = True
        self.roles = [self.helper_role, self.mod_role]
        self.guild.fetch_roles = AsyncMock(side_effect=lambda: list(self.roles))
        async def create_role(**kwargs):
            role = discord.Role(guild=self.guild, state=None, data=dict(
                id=str(500+len(self.roles)), name=kwargs['name'], permissions='0', position=1))
            self.roles.append(role)
            return role
        self.guild.create_role = AsyncMock(side_effect=create_role)
    async def asyncTearDown(self):
        await fixtures.ModerationTests.asyncTearDown(self)

    async def test_production_owner_and_admin_cannot_bypass(self):
        self.assertEqual(Bot.owner_user_id, 415585084213690378)
        self.bot.owner_user_id = 999
        self.staff_member.guild_permissions.administrator = True
        for action in ('ban','unban','kick','purge'):
            with self.assertRaises(ValueError): await self.bot.moderation_actor(self.i, action)
        with self.assertRaises(ValueError): self.bot.admin(self.i)
        with self.assertRaises(ValueError): await self.bot.tree.get_command('staff_setup').callback(self.i)

    async def test_preview_does_not_create_and_confirm_reuses(self):
        plan = await self.bot.staff_plan(self.guild)
        self.guild.create_role.assert_not_awaited()
        self.assertEqual([r[3] for r in plan[-2:]], [410,420])
        view = StaffPreview(self.bot, plan)
        await view.children[0].callback(self.i)
        self.assertEqual(self.guild.create_role.await_count, 3)
        self.assertEqual(self.bot.moderation_config(), dict(helper=410, moderator=420))
        for call in self.guild.create_role.await_args_list:
            self.assertEqual(call.kwargs['permissions'].value, 0)
        retry = await self.bot.staff_plan(self.guild)
        self.assertTrue(all(row[3] for row in retry))
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)

    async def test_expired_preview_and_changed_role_refused(self):
        plan = await self.bot.staff_plan(self.guild)
        view = StaffPreview(self.bot, plan); view.expires = time.monotonic()-1
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        view = StaffPreview(self.bot, plan)
        self.helper_role.name = 'Changed'
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.guild.create_role.assert_not_awaited()

    async def test_manager_scope_does_not_grant_owner_access(self):
        self.bot.owner_user_id = 999
        self.bot.save_community('staff-roles', dict(discord=420))
        self.i.command = NS(name='roles_setup')
        self.bot.admin(self.i)
        self.i.command = NS(name='community_logs')
        with self.assertRaises(ValueError): self.bot.admin(self.i)
        self.staff_member.roles = []
        self.i.command = NS(name='roles_setup')
        with self.assertRaises(ValueError): self.bot.admin(self.i)

    async def test_manager_moderation_has_no_ban(self):
        self.bot.owner_user_id = 999
        self.bot.save_community('staff-roles', dict(moderation=420))
        self.bot.save_community('moderation', None)
        self.staff_member.guild_permissions.moderate_members = False
        await self.bot.moderation_actor(self.i, 'timeout')
        with self.assertRaises(ValueError): await self.bot.moderation_actor(self.i, 'ban')

    async def test_staff_roles_cannot_be_self_selected(self):
        self.bot.save_community('staff-roles', dict(discord=420))
        with self.assertRaises(ValueError): self.bot.safe_community_role(self.guild, self.mod_role)

    async def test_duplicate_names_and_manager_native_permissions_refused(self):
        role = discord.Role(guild=self.guild,state=None,data=dict(id='600',name=STAFF_ROLES[0][1],permissions='8',position=1))
        self.roles.append(role)
        with self.assertRaises(ValueError): await self.bot.staff_plan(self.guild)
        role._permissions=0
        duplicate=discord.Role(guild=self.guild,state=None,data=dict(id='601',name=role.name,permissions='0',position=1))
        self.roles.append(duplicate)
        with self.assertRaises(ValueError): await self.bot.staff_plan(self.guild)

    async def test_partial_creation_can_resume_without_duplicate_roles(self):
        plan = await self.bot.staff_plan(self.guild)
        create = self.guild.create_role.side_effect
        calls = 0
        async def interrupted(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('network unavailable')
            return await create(**kwargs)
        self.guild.create_role.side_effect = interrupted
        with self.assertRaises(RuntimeError):
            await StaffPreview(self.bot, plan).children[0].callback(self.i)
        first = self.bot.community_config('staff-roles')['discord']
        self.guild.create_role.side_effect = create
        await StaffPreview(self.bot, await self.bot.staff_plan(self.guild)).children[0].callback(self.i)
        self.assertEqual(self.bot.community_config('staff-roles')['discord'], first)
        self.assertEqual(len(self.roles), 5)

    async def test_owner_rechecked_at_confirmation(self):
        view = StaffPreview(self.bot, await self.bot.staff_plan(self.guild))
        self.i.user = self.extra
        self.extra.guild_permissions.administrator = True
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.guild.create_role.assert_not_awaited()
