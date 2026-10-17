import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import test_moderation as fixtures
from moderation import PurgeConfirmation, case_embed
from store import Store


def message(number,pinned=False,bot=False):
    return NS(id=number,pinned=pinned,author=NS(bot=bot),type=discord.MessageType.default,
              jump_url=f'https://discord.com/channels/100/900/{number}',delete=AsyncMock())


class ExtraModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ModerationTests.asyncSetUp(self)
        self.banned=NS(id=77)
        self.guild.fetch_ban=AsyncMock(return_value=NS(user=self.banned))
        self.guild.unban=AsyncMock()
        self.public=MagicMock(spec=discord.TextChannel)
        self.public.id=900; self.public.guild=self.guild; self.public.category_id=None
        self.public.permissions_for.return_value=NS(view_channel=True,manage_messages=True,read_message_history=True)
        self.guild.fetch_channel=AsyncMock(return_value=self.public)
        self.i.channel_id=900; self.i.channel=self.public
        self.messages=[message(111),message(112),message(113)]
        async def history(**kwargs):
            for item in self.messages[:kwargs['limit']]: yield item
        self.public.history=history
        self.public.fetch_message=AsyncMock(side_effect=lambda n:next(m for m in self.messages if m.id==n))
    async def asyncTearDown(self): await fixtures.ModerationTests.asyncTearDown(self)

    async def test_unban_preview_then_confirm_for_user_absent_from_members(self):
        await self.bot.tree.get_command('unban').callback(self.i,'77','Contestație acceptată')
        self.guild.unban.assert_not_awaited()
        view=self.i.followup.send.await_args.kwargs['view']
        await view.children[0].callback(self.i)
        self.guild.unban.assert_awaited_once()
        self.assertEqual(self.bot.store.moderation_history(77)[0]['action'],'unban')
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)

    async def test_unban_not_banned_or_invalid_id_does_not_apply(self):
        callback=self.bot.tree.get_command('unban').callback
        for value in ('<@77>','abc','0',str(2**64)):
            with self.assertRaises(ValueError): await callback(self.i,value,'Motiv')
        self.guild.fetch_ban.side_effect=discord.NotFound(NS(status=404,reason='Not Found'),'Unknown Ban')
        with self.assertRaises(ValueError): await callback(self.i,'77','Motiv')
        self.guild.unban.assert_not_awaited()

    async def test_unban_rechecks_native_permission_at_confirmation(self):
        await self.bot.preview_sanction(self.i,77,'unban','Motiv')
        view=self.i.followup.send.await_args.kwargs['view']
        self.staff_member.guild_permissions.ban_members=False
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.guild.unban.assert_not_awaited()

    async def test_revoke_preserves_original_and_adds_linked_audit_once(self):
        old=await self.bot.execute_moderation(self.i,1,'warn','Motiv original')
        await self.bot.tree.get_command('warn_revoke').callback(self.i,old['id'],'Corectare avertisment')
        row=self.bot.store.moderation_case(old['id'])
        self.assertEqual((row['status'],row['reason'],row['actor']),('revoked','Motiv original',2))
        self.assertEqual(self.bot.store.moderation_history(1,1,True),[])
        audit=self.bot.store.moderation_history(1)[0]
        self.assertEqual(audit['action'],'warn_revoke'); self.assertIn(str(old['id']),audit['detail'])
        with self.assertRaises(ValueError): self.bot.store.moderation_revoke_warn(old['id'],2,'Din nou')
        self.assertEqual(len(self.bot.store.moderation_history(1)),2)
        reopened=Store(self.bot.database_path)
        self.assertEqual(reopened.moderation_case(old['id'])['status'],'revoked'); reopened.db.close()

    async def test_helper_cannot_revoke_or_purge(self):
        self.staff_member.roles=[self.helper_role]
        with self.assertRaises(ValueError): await self.bot.tree.get_command('warn_revoke').callback(self.i,1,'Motiv')
        with self.assertRaises(ValueError): await self.bot.preview_purge(self.i,3,'Motiv')
        self.assertTrue(all(m.delete.await_count==0 for m in self.messages))

    async def test_revoke_rejects_nonwarning_and_blank_reason(self):
        n=self.bot.store.moderation_create(2,1,'ban','Motiv')
        self.bot.store.moderation_finish(n,'applied')
        with self.assertRaises(ValueError): self.bot.store.moderation_revoke_warn(n,2,'Motiv')
        n=self.bot.store.moderation_create(2,1,'warn','Motiv'); self.bot.store.moderation_finish(n,'applied')
        with self.assertRaises(ValueError): self.bot.store.moderation_revoke_warn(n,2,'   ')
        self.assertEqual(self.bot.store.moderation_case(n)['status'],'applied')

    async def test_preview_is_read_only_excludes_pins_and_bots(self):
        self.messages[1].pinned=True; self.messages[2].author.bot=True
        await self.bot.preview_purge(self.i,3,'Curățare spam')
        view=self.i.followup.send.await_args.kwargs['view']
        self.assertEqual(view.messages,(111,))
        self.assertTrue(all(m.delete.await_count==0 for m in self.messages))
        self.assertIsNone(self.bot.store.moderation_case(1))
        self.assertTrue(self.i.followup.send.await_args.kwargs['ephemeral'])

    async def test_confirmation_deletes_only_fixed_ids_and_once(self):
        await self.bot.preview_purge(self.i,2,'Curățare')
        view=self.i.followup.send.await_args.kwargs['view']
        self.messages.insert(0,message(999))
        await view.children[0].callback(self.i)
        self.messages[0].delete.assert_not_awaited()
        self.messages[1].delete.assert_awaited_once(); self.messages[2].delete.assert_awaited_once()
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        row=self.bot.store.moderation_case(1)
        self.assertEqual((row['action'],row['status'],row['target']),('purge','applied',900))
        items=self.bot.store.moderation_purge_items(row['id'])
        self.assertEqual([(x['message'],x['status']) for x in items],[(111,'deleted'),(112,'deleted')])
        self.assertEqual(case_embed(row).fields[0].name,'Canal')

    async def test_newly_pinned_message_is_skipped_at_confirm(self):
        await self.bot.preview_purge(self.i,2,'Curățare')
        view=self.i.followup.send.await_args.kwargs['view']
        self.messages[0].pinned=True
        await view.children[0].callback(self.i)
        self.messages[0].delete.assert_not_awaited(); self.messages[1].delete.assert_awaited_once()
        self.assertEqual(self.bot.store.moderation_purge_items(1)[0]['status'],'skipped')

    async def test_private_ticket_archive_and_reviews_are_protected(self):
        for number in (10,self.bot.archive_id):
            self.public.id=number; self.i.channel_id=number
            with self.assertRaises(ValueError): await self.bot.preview_purge(self.i,2,'Motiv')
        self.public.id=900; self.i.channel_id=900
        self.bot.store.set_setting('helper:config','{"review_channel":900}')
        with self.assertRaises(ValueError): await self.bot.preview_purge(self.i,2,'Motiv')
        self.bot.store.set_setting('helper:config','')
        self.public.permissions_for.return_value.view_channel=False
        with self.assertRaises(ValueError): await self.bot.preview_purge(self.i,2,'Motiv')
        self.assertTrue(all(m.delete.await_count==0 for m in self.messages))

    async def test_changed_channel_privacy_or_permissions_blocks_confirmation(self):
        await self.bot.preview_purge(self.i,2,'Motiv')
        view=self.i.followup.send.await_args.kwargs['view']
        self.public.permissions_for.return_value.manage_messages=False
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.assertTrue(all(m.delete.await_count==0 for m in self.messages))

    async def test_purge_wrong_actor_and_cancel_are_noops(self):
        view=PurgeConfirmation(self.bot,2,900,[111],'Motiv')
        self.i.user=self.extra
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        self.i.user=self.staff_member
        await view.children[1].callback(self.i)
        self.assertIsNone(self.bot.store.moderation_case(1))
        self.messages[0].delete.assert_not_awaited()

    async def test_partial_deletion_is_recorded_and_does_not_continue(self):
        self.messages[1].delete.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'Missing Permissions')
        row=await self.bot.execute_purge(self.i,900,[111,112,113],'Motiv')
        self.assertEqual(row['status'],'partial')
        self.messages[0].delete.assert_awaited_once(); self.messages[2].delete.assert_not_awaited()
        self.assertEqual([x['status'] for x in self.bot.store.moderation_purge_items(row['id'])],['deleted','skipped','pending'])

    async def test_interrupted_delete_marks_uncertain_no_automatic_retry(self):
        self.messages[0].delete.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError): await self.bot.execute_purge(self.i,900,[111,112],'Motiv')
        row=self.bot.store.moderation_case(1)
        self.assertEqual(row['status'],'uncertain')
        self.assertEqual(self.bot.store.moderation_purge_items(1)[0]['status'],'uncertain')
        self.messages[0].delete.assert_awaited_once(); self.messages[1].delete.assert_not_awaited()

    async def test_deleted_message_not_replaced_by_newer_history(self):
        self.public.fetch_message.side_effect=discord.NotFound(NS(status=404,reason='Not Found'),'Deleted')
        row=await self.bot.execute_purge(self.i,900,[111],'Motiv')
        self.assertEqual(row['status'],'applied')
        self.assertIn('ștergeri confirmate: 0',row['detail'])
        self.assertTrue(all(m.delete.await_count==0 for m in self.messages))

    async def test_expired_confirmation_never_deletes_or_unbans(self):
        view=PurgeConfirmation(self.bot,2,900,[111],'Motiv')
        view.expires=0
        with self.assertRaises(ValueError): await view.children[0].callback(self.i)
        from moderation import SanctionConfirmation
        sanction=SanctionConfirmation(self.bot,2,77,'unban','Motiv')
        sanction.expires=0
        with self.assertRaises(ValueError): await sanction.children[0].callback(self.i)
        self.messages[0].delete.assert_not_awaited(); self.guild.unban.assert_not_awaited()
