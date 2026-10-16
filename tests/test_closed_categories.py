import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import discord
import test_features as fixtures


class ClosedCategoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.closed=MagicMock(spec=discord.CategoryChannel)
        self.closed.id=700
        self.guild.create_category=AsyncMock(return_value=self.closed)
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_close_moves_channel_staff_only_without_sync(self):
        await self.bot.close_ticket(self.i,'Rezolvat')
        move=next(c for c in self.channel.edit.await_args_list if 'category' in c.kwargs)
        self.assertIs(move.kwargs['category'],self.closed)
        self.assertFalse(move.kwargs['sync_permissions'])
        self.assertFalse(move.kwargs['overwrites'][self.everyone].view_channel)
        self.assertNotIn(self.owner,move.kwargs['overwrites'])
        self.assertNotIn(self.extra,move.kwargs['overwrites'])
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')

    async def test_saved_category_reused_not_category_found_by_name(self):
        self.bot.save_community('closed-categories',[700])
        self.guild.fetch_channels.return_value=[self.closed]
        self.assertIs(await self.bot.closed_ticket_category(self.guild,10),self.closed)
        self.guild.create_category.assert_not_awaited()

    async def test_full_category_creates_next_private_category(self):
        self.bot.save_community('closed-categories',[700])
        self.guild.fetch_channels.return_value=[self.closed]+[NS(id=1000+n,category_id=700) for n in range(50)]
        await self.bot.closed_ticket_category(self.guild,10)
        self.assertEqual(self.guild.create_category.await_args.args[0],'🔒 TICHETE ÎNCHISE 2')
        self.assertFalse(self.guild.create_category.await_args.kwargs['overwrites'][self.everyone].view_channel)

    async def test_failed_move_does_not_reopen_successfully_closed_ticket(self):
        self.guild.fetch_channels.side_effect=RuntimeError('API unavailable')
        with self.assertLogs('redfear',level='ERROR'):
            await self.bot.close_ticket(self.i,'Rezolvat')
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')
        self.assertTrue(self.archive.send.await_count)
        self.assertFalse(self.channel.edit.await_args.kwargs['overwrites'][self.everyone].view_channel)

    async def test_reopen_routes_back_to_kind_category(self):
        self.bot.store.close(10,2,'Rezolvat')
        self.bot.store.set_setting('category:Ajutor',800)
        category=MagicMock(spec=discord.CategoryChannel); category.id=800
        self.guild.get_channel=lambda n:category if n==800 else None
        self.guild.fetch_member=AsyncMock(return_value=self.owner)
        await self.bot.reopen_ticket(self.i)
        self.assertIs(self.channel.edit.await_args.kwargs['category'],category)
        self.assertFalse(self.channel.edit.await_args.kwargs['sync_permissions'])
        self.assertEqual(self.bot.store.channel(10)['status'],'open')

    async def test_missing_reopen_category_keeps_closed(self):
        self.bot.store.close(10,2,'Rezolvat')
        self.guild.get_channel=lambda n:None
        self.guild.fetch_member=AsyncMock(return_value=self.owner)
        with self.assertRaises(ValueError): await self.bot.reopen_ticket(self.i)
        self.channel.edit.assert_not_awaited()
        self.assertEqual(self.bot.store.channel(10)['status'],'closed')

    async def test_bulk_moves_only_database_closed_tickets(self):
        self.bot.store.close(10,2,'Rezolvat')
        self.bot.store.create(3,11,'Bug',2)
        ch=MagicMock(spec=discord.TextChannel); ch.id=10; ch.edit=AsyncMock()
        self.guild.fetch_channel=AsyncMock(return_value=ch)
        self.i.response.defer=AsyncMock()
        await self.bot.tree.get_command('ticket_archive_closed').callback(self.i)
        self.guild.fetch_channel.assert_awaited_once_with(10)
        ch.edit.assert_awaited_once()
        self.assertEqual(self.bot.store.channel(11)['status'],'open')
