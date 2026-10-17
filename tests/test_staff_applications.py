import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import discord
import test_applications as fixtures
from applications import STAFF_KINDS, StaffApplicationModal, StaffApplicationPanel, staff_questions, application_embed
from store import Store


def answers(kind):
    return [dict(question=q[0], answer='a'*q[1]) for q in staff_questions(kind)]


class StaffApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.HelperBotTests.asyncSetUp(self)
        self.guild.id=100
        self.room.guild=self.guild
        self.room.category_id=50
        self.guild.fetch_member=AsyncMock(return_value=self.staff_member)
        self.i.channel.permissions_for=lambda who:NS(view_channel=True)
        self.config_new=dict(rooms={key:20 for key in STAFF_KINDS},panel_channel=10,panel_message=88,cooldown_days=7,enabled=True)
        self.bot.save_community('staff-applications',self.config_new)
        self.i.user=self.owner
    async def asyncTearDown(self):
        await fixtures.HelperBotTests.asyncTearDown(self)

    async def test_all_forms_have_specific_questions_and_fit_embed_limits(self):
        panel=StaffApplicationPanel(self.bot)
        self.assertTrue(panel.is_persistent())
        self.assertEqual(len(panel.children[0].options),5)
        for kind in STAFF_KINDS:
            modal=StaffApplicationModal(self.bot,88,kind)
            self.assertEqual(len(modal.children),5)
            for child in modal.children: self.assertLessEqual(len(child.label),45)
            n=self.bot.store.helper_create(50+list(STAFF_KINDS).index(kind),json.dumps(answers(kind)),20,0,0,kind=kind)
            row=self.bot.store.helper_application(n)
            embed=application_embed(row)
            self.assertIn(STAFF_KINDS[kind][0],embed.title)
            self.assertLess(len(embed),6000)

    async def test_submission_hardens_private_room_before_send_and_saves_kind(self):
        events=[]
        self.room.edit.side_effect=lambda **kwargs:events.append('private')
        self.room.send.side_effect=lambda **kwargs:(events.append('send') or NS(id=99))
        await self.bot.submit_staff_application(self.i,88,'discord',answers('discord'))
        self.assertEqual(events,['private','send'])
        row=self.bot.store.helper_latest(1)
        self.assertEqual(row['kind'],'discord');self.assertEqual(row['reviewer_role'],0)
        overrides=self.room.edit.await_args.kwargs['overwrites']
        self.assertEqual(set(overrides),{self.everyone,self.bot_member,self.staff_member})
        self.assertFalse(overrides[self.everyone].view_channel)
        self.assertNotIn(self.owner,overrides)
        self.assertNotIn(self.role,overrides)
        with self.assertRaises(ValueError):
            await self.bot.submit_staff_application(self.i,88,'support',answers('support'))

    async def test_only_owner_can_review_new_applications_even_if_administrator(self):
        await self.bot.submit_staff_application(self.i,88,'support',answers('support'))
        row=self.bot.store.helper_latest(1)
        self.i.channel_id=20;self.i.message=NS(id=99)
        self.i.user=self.extra;self.extra.guild_permissions.administrator=True
        with self.assertRaises(ValueError): self.bot.helper_require_reviewer(self.i)
        with self.assertRaises(ValueError): await self.bot.decide_helper(self.i,row['id'],99,'accepted','Bun candidat')
        self.i.user=self.staff_member
        await self.bot.decide_helper(self.i,row['id'],99,'accepted','Bun candidat')
        self.assertEqual(self.bot.store.helper_application(row['id'])['status'],'accepted')
        self.bot.fetch_user.assert_awaited_once_with(1)
        with self.assertRaises(ValueError): await self.bot.decide_helper(self.i,row['id'],99,'rejected','Alt motiv')
        self.staff_member.add_roles.assert_not_called()

    async def test_stale_panel_closed_recruitment_and_invalid_answers_are_refused(self):
        for panel,kind in ((87,'discord'),(88,'unknown')):
            with self.assertRaises(ValueError): await self.bot.submit_staff_application(self.i,panel,kind,answers('discord'))
        with self.assertRaises(ValueError): await self.bot.submit_staff_application(self.i,88,'discord',[])
        self.config_new['enabled']=False;self.bot.save_community('staff-applications',self.config_new)
        with self.assertRaises(ValueError): await self.bot.submit_staff_application(self.i,88,'discord',answers('discord'))
        self.room.send.assert_not_awaited()

    async def test_no_answers_sent_if_room_cannot_be_hardened(self):
        self.room.edit.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Missing Permissions')
        with self.assertRaises(discord.Forbidden): await self.bot.submit_staff_application(self.i,88,'moderation',answers('moderation'))
        self.room.send.assert_not_awaited()
        self.assertIsNone(self.bot.store.helper_latest(1))

    async def test_old_helper_pending_blocks_manager_application_and_remains_intact(self):
        n=self.bot.store.helper_create(1,json.dumps(fixtures.answers()),21,400,0)
        with self.assertRaises(ValueError): await self.bot.submit_staff_application(self.i,88,'moderator',answers('moderator'))
        row=self.bot.store.helper_application(n)
        self.assertEqual(row['kind'],'helper');self.assertEqual(row['review_channel'],21)
        self.assertEqual(json.loads(row['answers']),fixtures.answers())

    async def test_private_rooms_protected_from_layout_and_public_panel(self):
        self.assertIn(20,self.bot.protected_layout_channels())
        with self.assertRaises(ValueError): await self.bot.staff_private_room(self.guild,12345)
        self.guild.fetch_channel.assert_not_awaited()

    async def test_own_status_has_only_own_record_and_kind(self):
        await self.bot.submit_staff_application(self.i,88,'moderator',answers('moderator'))
        await self.bot.tree.get_command('staff_application_status').callback(self.i)
        self.assertIn('Moderator',self.i.followup.send.await_args.args[0])
        self.i.user=self.extra
        await self.bot.tree.get_command('staff_application_status').callback(self.i)
        self.assertIn('Nu ai trimis',self.i.followup.send.await_args.args[0])


class StaffApplicationSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.HelperBotTests.asyncSetUp(self)
        self.bot.store.db.execute('UPDATE tickets SET channel=11 WHERE channel=10')
        self.bot.store.db.commit()
        self.guild.id = 100
        self.guild.fetch_member = AsyncMock(return_value=self.staff_member)
        self.category = MagicMock(spec=discord.CategoryChannel)
        self.category.id = 50; self.category.guild = self.guild
        self.panel = MagicMock(spec=discord.TextChannel)
        self.panel.id = 10; self.panel.guild = self.guild
        self.panel.permissions_for.return_value = NS(view_channel=True)
        self.card = NS(id=88, edit=AsyncMock())
        self.panel.fetch_message = AsyncMock(return_value=self.card)
        self.panel.send = AsyncMock(return_value=self.card)
        self.rooms = {}
        self.guild.fetch_channel = AsyncMock(side_effect=lambda n:self.rooms[n])
        async def create(name, **kwargs):
            room=MagicMock(spec=discord.TextChannel)
            room.id=1000+len(self.rooms);room.guild=self.guild;room.category_id=50
            room.edit=AsyncMock()
            self.rooms[room.id]=room
            return room
        self.guild.create_text_channel=AsyncMock(side_effect=create)
    async def asyncTearDown(self):
        await fixtures.HelperBotTests.asyncTearDown(self)

    async def test_setup_creates_five_private_rooms_and_upgrades_existing_helper_panel(self):
        old=self.bot.store.helper_create(1,json.dumps(fixtures.answers()),20,400,0)
        callback=self.bot.tree.get_command('staff_applications_setup').callback
        await callback(self.i,self.panel,self.category,7)
        config=self.bot.community_config('staff-applications')
        self.assertEqual(len(set(config['rooms'].values())),5)
        self.assertTrue(config['enabled'])
        self.assertEqual(config['panel_message'],88)
        self.panel.send.assert_not_awaited()
        self.assertIsInstance(self.card.edit.await_args.kwargs['view'],StaffApplicationPanel)
        self.assertFalse(self.bot.helper_config()['enabled'])
        self.assertEqual(self.bot.store.helper_application(old)['reviewer_role'],400)
        for call in self.guild.create_text_channel.await_args_list:
            self.assertEqual(call.kwargs['category'],self.category)
            self.assertEqual(set(call.kwargs['overwrites']),{self.everyone,self.bot_member,self.staff_member})
        await callback(self.i,self.panel,self.category,7)
        self.assertEqual(self.guild.create_text_channel.await_count,5)

    async def test_setup_partial_failure_resumes_and_does_not_publish_partial_panel(self):
        original=self.guild.create_text_channel.side_effect
        count=0
        async def failing(*args,**kwargs):
            nonlocal count
            count+=1
            if count==3: raise RuntimeError('network unavailable')
            return await original(*args,**kwargs)
        self.guild.create_text_channel.side_effect=failing
        callback=self.bot.tree.get_command('staff_applications_setup').callback
        with self.assertRaises(RuntimeError): await callback(self.i,self.panel,self.category,7)
        config=self.bot.community_config('staff-applications')
        self.assertFalse(config['enabled']);self.assertEqual(len(config['rooms']),2)
        self.card.edit.assert_not_awaited()
        self.guild.create_text_channel.side_effect=original
        await callback(self.i,self.panel,self.category,7)
        self.assertEqual(len(self.rooms),5)
        self.assertTrue(self.bot.community_config('staff-applications')['enabled'])

    async def test_setup_owner_only_and_refuses_private_publication_target(self):
        self.i.user=self.extra;self.extra.guild_permissions.administrator=True
        callback=self.bot.tree.get_command('staff_applications_setup').callback
        with self.assertRaises(ValueError): await callback(self.i,self.panel,self.category,7)
        self.i.user=self.staff_member
        self.panel.permissions_for.return_value=NS(view_channel=False)
        with self.assertRaises(ValueError): await callback(self.i,self.panel,self.category,7)
        self.guild.create_text_channel.assert_not_awaited()


class StaffApplicationMigrationTests(unittest.TestCase):
    def test_existing_helper_database_migrates_and_keeps_answers(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            db=sqlite3.connect(path)
            db.execute("CREATE TABLE helper_applications(id INTEGER PRIMARY KEY,owner INTEGER NOT NULL,answers TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created REAL NOT NULL,review_channel INTEGER NOT NULL,reviewer_role INTEGER NOT NULL,message INTEGER,reviewed_at REAL,reviewed_by INTEGER,reason TEXT,dm_state INTEGER NOT NULL DEFAULT 0)")
            db.execute("INSERT INTO helper_applications(id,owner,answers,created,review_channel,reviewer_role) VALUES(5,1,'[]',1000,20,400)")
            db.commit();db.close()
            store=Store(path)
            self.assertEqual(store.helper_application(5)['kind'],'helper')
            self.assertEqual(store.helper_application(5)['answers'],'[]')
            with self.assertRaises(ValueError): store.helper_create(1,'[]',22,0,0,kind='discord')
            store.db.close()
