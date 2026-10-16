import asyncio
import datetime
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from bot import Bot, Controls, ClosedControls, Panel, TicketModal, KINDS
from store import Store
from transcript import export_parts, render_message, safe_link


def member(number, staff=False):
    m = MagicMock(spec=discord.Member)
    m.id, m.bot = number, False
    m.guild_permissions.administrator = staff
    m.roles = []
    m.mention = f'<@{number}>'
    return m


class StorageTests(unittest.TestCase):
    def test_existing_database_migrates_without_losing_ticket(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'old.db'
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE tickets(id INTEGER PRIMARY KEY,owner INTEGER NOT NULL,channel INTEGER UNIQUE,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',claimed INTEGER,created REAL NOT NULL)")
            db.execute("INSERT INTO tickets VALUES(7,1,10,'Ajutor','open',2,1)")
            db.commit(); db.close()
            s = Store(path)
            self.assertEqual(s.active(1)['claimed'], 2)
            self.assertEqual(s.next_number(), 8)
            self.assertEqual(s.next_number(), 9)
            s.close(10, 2, 'Rezolvat')
            self.assertEqual(s.channel(10)['reason'], 'Rezolvat')
            s.db.close()
            s = Store(path)
            self.assertEqual(s.next_number(), 10)
            s.db.close()

    def test_reopen_unique_and_event_stats(self):
        s = Store(':memory:')
        s.create(1, 10, 'Bug', 1)
        s.claim(10, 2)
        s.close(10, 2, 'Rezolvat')
        s.create(1, 11, 'Ajutor', 2)
        with self.assertRaises(sqlite3.IntegrityError):
            s.reopen(10, 2)
        self.assertEqual(s.channel(10)['status'], 'closed')
        s.close(11, 3, 'Duplicat')
        s.reopen(10, 2)
        self.assertIsNone(s.channel(10)['claimed'])
        self.assertTrue(s.claim(10, 3))
        counts, staff = s.stats()
        self.assertEqual(counts, {'closed': 1, 'open': 1})
        self.assertEqual(sum(r['claims'] for r in staff), 2)
        self.assertEqual(sum(r['closes'] for r in staff), 2)

    def test_participants_persist(self):
        s = Store(':memory:')
        s.participant(10, 2, True); s.participant(10, 2, True)
        self.assertEqual(s.participants(10), [2])
        s.participant(10, 2, False)
        self.assertEqual(s.participants(10), [])


class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    def message(self, content):
        return NS(content=content, embeds=[NS(title='<script>x</script>', description='a & b', fields=[])],
                  attachments=[NS(url='javascript:alert(1)', filename='<bad>')], author=NS(id=7, name='a<b'),
                  created_at=datetime.datetime.now(datetime.timezone.utc), id=3)

    async def test_escape_and_split(self):
        msg = self.message('<img src=x onerror=alert(1)>')
        rendered = render_message(msg)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<img ', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn('&lt;bad&gt;', rendered)
        async def history(**kwargs):
            for x in range(8):
                yield self.message(str(x) + 'x' * 400)
        parts = [p async for p in export_parts(NS(history=history), '<unsafe>', limit=2000)]
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 2000)
            self.assertTrue(p.endswith(b'</html>'))
            self.assertIn(b'&lt;unsafe&gt;', p)
        self.assertIn('https://example.org/', safe_link('https://example.org/', 'safe'))


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'GUILD_ID':'100', 'TICKET_CATEGORY_ID':'200', 'ARCHIVE_CHANNEL_ID':'300',
                                      'SUPPORT_ROLE_IDS':'400', 'DATA_DIR':self.directory.name}):
            self.bot = Bot()
        self.owner, self.staff_member, self.extra = member(1), member(2, True), member(3)
        self.everyone, self.role, self.bot_member = MagicMock(spec=discord.Role), MagicMock(spec=discord.Role), member(4)
        self.role.mention = '<@&400>'
        self.guild = NS(default_role=self.everyone, me=self.bot_member, get_role=lambda r:self.role)
        self.channel = NS(id=10, name='tichet-0001', overwrites={self.owner:discord.PermissionOverwrite(**dict(view_channel=True, send_messages=True)), self.extra:discord.PermissionOverwrite(view_channel=True,send_messages=True)},
                          edit=AsyncMock(), send=AsyncMock())
        self.i = NS(guild_id=100, channel_id=10, user=self.staff_member, guild=self.guild, channel=self.channel,
                    response=NS(is_done=lambda:True), followup=NS(send=AsyncMock()))
        self.bot.store.create(1,10,'Ajutor',1)
        self.bot.send_feedback = AsyncMock()
        self.archive = NS(send=AsyncMock())
        self.bot.archive_channel = AsyncMock(return_value=self.archive)
        async def history(**kwargs):
            if False: yield
        self.channel.history = history

    async def asyncTearDown(self):
        self.bot.store.db.close()
        await self.bot.close()
        self.directory.cleanup()

    async def test_persistent_views_and_modal_limits(self):
        for view in (Panel(self.bot), Controls(self.bot), ClosedControls(self.bot)):
            self.assertTrue(view.is_persistent())
        for kind in KINDS:
            modal = TicketModal(self.bot,kind)
            self.assertLessEqual(len(modal.children),5)
            for item in modal.children:
                self.assertLessEqual(len(item.label),45)
                self.assertLessEqual(len(item.placeholder),100)
        self.assertEqual({c.name for c in self.bot.tree.get_commands()}, {'ticket_panel','ticket_controls','ticket_categories','ticket_stats','ticket_health','ticket_block','ticket_unblock','ticket_idle','ticket_backup','fivem_setup','fivem_status','fivem_stop','helper_setup','helper_toggle','helper_status','helper_republish','helper_resend'})

    async def test_no_manage_roles_overwrite(self):
        overwrites = self.bot.overwrites(self.guild,[self.role],self.owner)
        self.assertIsNone(overwrites[self.bot_member].manage_roles)
        self.assertTrue(overwrites[self.bot_member].manage_channels)
        self.assertFalse(overwrites[self.everyone].view_channel)

    async def test_nonstaff_cannot_manage(self):
        self.i.user = self.owner
        with self.assertRaises(ValueError):
            await self.bot.manage_ticket(self.i,'rename','secret')
        self.channel.edit.assert_not_awaited()

    async def test_close_hides_owner_and_extra_and_exports_html(self):
        await self.bot.close_ticket(self.i,'Rezolvat')
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')
        self.assertEqual(self.bot.store.channel(10)['closed_by'],2)
        closed = self.channel.edit.await_args_list[-1].kwargs['overwrites']
        self.assertNotIn(self.owner,closed)
        self.assertNotIn(self.extra,closed)
        self.assertFalse(closed[self.everyone].view_channel)
        self.assertTrue(self.archive.send.await_args.kwargs['file'].filename.endswith('.html'))
        self.assertIsInstance(self.channel.send.await_args.kwargs['view'],ClosedControls)

    async def test_export_failure_restores_access_and_remains_open(self):
        self.archive.send.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await self.bot.close_ticket(self.i,'Rezolvat')
        self.assertEqual(self.bot.store.channel(10)['status'],'open')
        restored = self.channel.edit.await_args.kwargs['overwrites']
        self.assertTrue(restored[self.owner].send_messages)
        self.assertTrue(restored[self.extra].view_channel)

    async def test_reopen_refuses_other_active_ticket(self):
        self.bot.store.close(10,2,'ok')
        self.bot.store.create(1,11,'Bug',2)
        with self.assertRaises(ValueError):
            await self.bot.reopen_ticket(self.i)
        self.channel.edit.assert_not_awaited()

    async def test_reopen_restores_participants_resets_claim(self):
        self.bot.store.claim(10,2)
        self.bot.store.close(10,2,'ok')
        self.bot.store.participant(10,3,True)
        self.guild.fetch_member = AsyncMock(side_effect=[self.owner,self.extra])
        await self.bot.reopen_ticket(self.i)
        self.assertEqual(self.bot.store.channel(10)['status'],'open')
        self.assertIsNone(self.bot.store.channel(10)['claimed'])
        restored = self.channel.edit.await_args.kwargs['overwrites']
        self.assertTrue(restored[self.owner].view_channel)
        self.assertTrue(restored[self.extra].view_channel)

    async def test_departed_owner_can_be_closed(self):
        # Closing uses the existing overwrite snapshot, never fetches the departed owner.
        self.channel.overwrites = {}
        await self.bot.close_ticket(self.i,'Autor plecat')
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')

    async def test_creation_routes_to_saved_category_and_limits_mentions(self):
        category = MagicMock(spec=discord.CategoryChannel)
        category.id = 900
        self.guild.get_channel = lambda number: category if number == 900 else None
        self.bot.store.set_setting('category:Bug', 900)
        new_channel = NS(id=11, mention='<#11>', send=AsyncMock())
        self.guild.create_text_channel = AsyncMock(return_value=new_channel)
        self.i.user = member(9)
        await self.bot.open_ticket(self.i, 'Bug', 'Pași pentru reproducere')
        args = self.guild.create_text_channel.await_args
        self.assertEqual(args.args[0], 'tichet-0002')
        self.assertIs(args.kwargs['category'], category)
        self.assertEqual(self.bot.store.active(9)['channel'],11)
        mentions = new_channel.send.await_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone)
        self.assertEqual(mentions.roles,[self.role])

    async def test_categories_retry_reuses_persisted_ids(self):
        existing = {}
        async def create_category(name, **kwargs):
            category = MagicMock(spec=discord.CategoryChannel)
            category.id = 1000 + len(existing)
            existing[category.id] = category
            self.assertFalse(kwargs['overwrites'][self.everyone].view_channel)
            return category
        self.guild.create_category = AsyncMock(side_effect=create_category)
        self.guild.get_channel = existing.get
        self.i.response = NS(defer=AsyncMock(), is_done=lambda:True)
        callback = self.bot.tree.get_command('ticket_categories').callback
        await callback(self.i)
        await callback(self.i)
        self.assertEqual(self.guild.create_category.await_count,4)
        self.assertTrue(all(self.bot.store.setting('category:' + k) for k in KINDS))

    async def test_ordinary_member_cannot_close_another_ticket(self):
        self.i.user = self.extra
        with self.assertRaises(ValueError):
            await self.bot.close_ticket(self.i,'No permission')
        self.channel.edit.assert_not_awaited()
