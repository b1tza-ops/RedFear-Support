import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import test_features as fixtures
from operations import RatingView, Operations, backup_database
from store import Store

class StoreOperationsTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:'); self.s.create(1,10,'Bug',1000)
    def tearDown(self): self.s.db.close()

    def test_assignment_authorization(self):
        self.s.claim(10,2)
        with self.assertRaises(ValueError): self.s.assign(10,3,4)
        self.s.assign(10,2,4); self.assertEqual(self.s.channel(10)['claimed'],4)
        self.s.assign(10,3,None,admin=True)
        self.assertTrue(self.s.claim(10,5)); self.s.close(10)
        with self.assertRaises(ValueError): self.s.assign(10,5,6)

    def test_block_expiry_and_unblock(self):
        self.s.block(1,2,'Abuz',5000)
        self.assertTrue(self.s.blocked(1,4999)); self.assertIsNone(self.s.blocked(1,5000))
        self.s.block(1,2,'Abuz'); self.assertTrue(self.s.blocked(1,10**12))
        self.s.unblock(1,2); self.assertIsNone(self.s.blocked(1))

    def test_priority_validation(self):
        self.s.set_priority(10,'urgent',2)
        with self.assertRaises(ValueError): self.s.set_priority(10,'other',2)
        self.assertEqual(self.s.channel(10)['priority'],'urgent')

    def test_rating_ownership_single_vote_stats(self):
        self.s.claim(10,2); self.s.close(10,2,'Done'); token=self.s.invite_feedback(10,1000)
        with self.assertRaises(ValueError): self.s.rate(token,9,5,1001)
        with self.assertRaises(ValueError): self.s.rate(token,1,6,1001)
        self.s.rate(token,1,4,1001)
        with self.assertRaises(ValueError): self.s.rate(token,1,1,1002)
        self.assertEqual(self.s.pending_feedback(1002),[])
        row=self.s.feedback_stats()[0]
        self.assertEqual((row['staff'],row['count'],row['average']),(2,1,4.0))

    def test_expired_and_old_cycle_ratings(self):
        self.s.close(10,2,'Done'); token=self.s.invite_feedback(10,1000)
        with self.assertRaises(ValueError): self.s.rate(token,1,5,1000+31*86400)
        self.s.reopen(10,2)
        with self.assertRaises(ValueError): self.s.rate(token,1,5,1001)
        self.s.close(10,2,'Again'); second=self.s.invite_feedback(10,1002)
        self.assertNotEqual(token,second)
        with self.assertRaises(ValueError): self.s.rate(token,1,5,1003)
        self.s.rate(second,1,5,1003)

    def test_activity_and_idle_configuration(self):
        self.s.warn(10,5000); self.s.touch(10,5001)
        self.assertIsNone(self.s.channel(10)['warned_at'])
        self.s.touch(10,1000); self.assertEqual(self.s.channel(10)['last_activity'],5001)
        for warn,close in [(0,0),(3600,3600),(7200,3600)]:
            with self.assertRaises(ValueError): self.s.configure_idle(warn,close)
        self.s.configure_idle(3600,7200); self.assertEqual(self.s.setting('idle_close'),'7200')
        self.s.configure_idle(3600,0); self.assertEqual(self.s.setting('idle_close'),'0')

    def test_pending_feedback_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'store.db'; s=Store(path); s.create(1,10,'Bug',1000)
            s.set_priority(10,'high',2); s.close(10,2,'Done')
            token=s.invite_feedback(10,1000); s.db.close(); s=Store(path)
            self.assertEqual(s.pending_feedback(1001)[0]['token'],token)
            self.assertEqual(s.channel(10)['priority'],'high')
            s.rate(token,1,3,1002); s.db.close()

class BackupTests(unittest.TestCase):
    def test_live_wal_backup_integrity_retention_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.db'; s=Store(source); s.db.execute('PRAGMA journal_mode=WAL')
            directory=Path(d)/'backups'; directory.mkdir()
            unrelated=directory/'manual.sqlite3'; unrelated.write_text('keep')
            for n in range(5):
                s.set_setting('version',n); path=backup_database(source,directory,keep=3)
                with sqlite3.connect(path) as restored:
                    self.assertEqual(restored.execute('PRAGMA quick_check').fetchone()[0],'ok')
                    self.assertEqual(restored.execute("SELECT value FROM settings WHERE key='version'").fetchone()[0],str(n))
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(list(directory.glob('2*.sqlite3'))),3)
            self.assertEqual(unrelated.read_text(),'keep')
            self.assertEqual(directory.stat().st_mode & 0o777,0o700); s.db.close()

    def test_failed_backup_keeps_old_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'bad.db'; source.write_text('bad database')
            directory=Path(d)/'backups'; directory.mkdir()
            previous=directory/'20260101T000000-old.sqlite3'; previous.write_text('old')
            with self.assertRaises(sqlite3.DatabaseError): backup_database(source,directory)
            self.assertEqual(previous.read_text(),'old'); self.assertEqual(list(directory.glob('.sqlite-*')),[])

class BotOperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.staff_member.guild_permissions.administrator=False
        role=MagicMock(spec=discord.Role); role.id=400; self.staff_member.roles=[role]
        self.other=fixtures.member(5); self.other.roles=[role]
        self.channel.topic='Ticket'; self.guild.id=100; self.guild.me=self.bot_member
        self.guild.fetch_channel=AsyncMock(return_value=self.channel)
        self.guild.fetch_member=AsyncMock()
        self.bot.get_guild=lambda gid:self.guild
        self.bot.store.db.execute('UPDATE tickets SET last_activity=1000,created=1000'); self.bot.store.db.commit()
        self.bot.close_locked=AsyncMock()
        self.i.response=NS(defer=AsyncMock(),is_done=lambda:True,edit_message=AsyncMock())
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_exclusive_assignment(self):
        self.bot.store.claim(10,2); self.i.user=self.other
        with self.assertRaises(ValueError): self.bot.require_staff(self.i,'open')
        self.i.user=fixtures.member(4,True); self.assertTrue(self.bot.require_staff(self.i,'open'))

    async def test_transfer_validates_and_limits_ping(self):
        self.bot.store.claim(10,2); self.guild.fetch_member.return_value=self.owner
        with self.assertRaises(ValueError): await self.bot.transfer_ticket(self.i,1)
        self.guild.fetch_member.return_value=self.other; await self.bot.transfer_ticket(self.i,5)
        self.assertEqual(self.bot.store.channel(10)['claimed'],5)
        mentions=self.channel.send.await_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone); self.assertFalse(mentions.roles); self.assertEqual(mentions.users,[self.other])

    async def test_release_and_priority(self):
        self.bot.store.claim(10,2); await self.bot.release_ticket(self.i)
        self.assertIsNone(self.bot.store.channel(10)['claimed'])
        await self.bot.priority_ticket(self.i,'urgent')
        self.assertEqual(self.bot.store.channel(10)['priority'],'urgent')
        self.assertIn('prioritate=urgent',self.channel.edit.await_args.kwargs['topic'])
        self.assertIn('Urgentă',self.channel.send.await_args.kwargs['embed'].title)

    async def test_block_prevents_create_reopen(self):
        self.bot.store.block(1,2,'Abuz'); self.i.user=self.owner
        with self.assertRaises(ValueError): await self.bot.open_ticket(self.i,'Bug','test')
        self.i.user=self.staff_member; self.bot.store.close(10,2,'Done')
        with self.assertRaises(ValueError): await self.bot.reopen_ticket(self.i)
        self.guild.fetch_member.assert_not_awaited()

    async def test_warning_then_close_after_full_grace(self):
        self.bot.store.configure_idle(3600,7200); await self.bot.idle_step(4600)
        self.assertEqual(self.bot.store.channel(10)['warned_at'],4600)
        self.bot.close_locked.assert_not_awaited(); await self.bot.idle_step(8199)
        self.bot.close_locked.assert_not_awaited(); await self.bot.idle_step(8200)
        self.bot.close_locked.assert_awaited_once()

    async def test_old_ticket_needs_new_warning(self):
        self.bot.store.configure_idle(3600,7200); await self.bot.idle_step(100000)
        await self.bot.idle_step(100001); self.bot.close_locked.assert_not_awaited()

    async def test_default_does_not_autoclose(self):
        await self.bot.idle_step(100000); await self.bot.idle_step(10000000)
        self.bot.close_locked.assert_not_awaited(); self.channel.send.assert_awaited_once()

    async def test_human_activity_resets_warning(self):
        self.bot.store.configure_idle(3600,7200); await self.bot.idle_step(4600)
        message=NS(author=self.owner,guild=self.guild,channel=self.channel,created_at=datetime.datetime.fromtimestamp(8000,datetime.timezone.utc))
        await self.bot.on_message(message); await self.bot.idle_step(8200)
        self.assertIsNone(self.bot.store.channel(10)['warned_at']); self.bot.close_locked.assert_not_awaited()

    async def test_bot_activity_is_ignored(self):
        message=NS(author=NS(bot=True),guild=self.guild,channel=self.channel,created_at=datetime.datetime.now(datetime.timezone.utc))
        await self.bot.on_message(message); self.assertEqual(self.bot.store.channel(10)['last_activity'],1000)

    async def test_missed_activity_during_downtime_prevents_close(self):
        self.bot.store.configure_idle(3600,7200); self.bot.store.warn(10,4600)
        async def history(**kwargs):
            yield NS(author=self.owner,created_at=datetime.datetime.fromtimestamp(8100,datetime.timezone.utc))
        self.channel.history=history; await self.bot.idle_step(8200)
        self.bot.close_locked.assert_not_awaited(); self.assertEqual(self.bot.store.channel(10)['last_activity'],8100)

    async def test_rating_view_owner_only(self):
        self.bot.store.close(10,2,'Done'); token=self.bot.store.invite_feedback(10)
        view=RatingView(self.bot,token); self.assertTrue(view.is_persistent()); self.i.data={'values':['5']}
        with self.assertRaises(ValueError): await view.rate(self.i)
        self.i.response.edit_message.assert_not_awaited(); self.i.user=self.owner
        await view.rate(self.i); self.i.response.edit_message.assert_awaited_once(); self.assertTrue(view.is_finished())

    async def test_feedback_dm_failure_does_not_raise(self):
        self.bot.store.close(10,2,'Done')
        owner=NS(send=AsyncMock(side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'DM closed')))
        self.bot.fetch_user=AsyncMock(return_value=owner)
        await Operations.send_feedback(self.bot,self.guild,10)
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')

    async def test_health_detects_missing_permissions(self):
        self.guild.me.guild_permissions=discord.Permissions.none(); self.guild.get_channel=lambda cid:None
        self.bot.archive_channel=AsyncMock(side_effect=ValueError('Arhiva trebuie să fie privată.'))
        role=MagicMock(spec=discord.Role); role.mentionable=False; self.bot.support_roles=lambda guild:[role]; self.guild.get_role=lambda rid:role
        lines=await self.bot.health_report(self.guild)
        self.assertTrue(any('manage_channels' in line for line in lines)); self.assertTrue(any('Arhiva trebuie' in line for line in lines))
        self.assertIn('SQLite: ok',lines)

    async def test_command_auth(self):
        self.bot.owner_user_id = 999
        for command in ('ticket_idle','ticket_backup'):
            with self.assertRaises(ValueError): await self.bot.tree.get_command(command).callback(self.i)
        self.i.user=self.owner
        with self.assertRaises(ValueError): await self.bot.tree.get_command('ticket_health').callback(self.i)
