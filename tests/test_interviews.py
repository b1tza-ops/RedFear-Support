import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import discord
from store import Store
from interviews import interview_time, InterviewReply, RescheduleInterviewModal, LONDON
from applications import HelperReview
import test_staff_applications as fixtures


class InterviewTimeTests(unittest.TestCase):
    def test_london_zone_and_transition_validation(self):
        stamp=interview_time('2027-01-10','12:00',dt.datetime(2027,1,1,tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(dt.datetime.fromtimestamp(stamp,dt.timezone.utc).hour,12)
        stamp=interview_time('2027-07-10','12:00',dt.datetime(2027,7,1,tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(dt.datetime.fromtimestamp(stamp,dt.timezone.utc).hour,11)
        for date,hour,now in [('2027-03-28','01:30',dt.datetime(2027,3,1,tzinfo=dt.timezone.utc).timestamp()),
                              ('2027-10-31','01:30',dt.datetime(2027,10,1,tzinfo=dt.timezone.utc).timestamp()),
                              ('invalid','12:00',0),('2027-01-01','12:00',2**32)]:
            with self.assertRaises(ValueError): interview_time(date,hour,now)


class InterviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:')
        self.app=self.s.helper_create(1,'[]',20,0,0,kind='discord')
        self.s.helper_set_message(self.app,99)
    def tearDown(self): self.s.db.close()
    def schedule(self): return self.s.interview_schedule(self.app,2,time.time()+3600,30,'Canal vocal')

    def test_replies_bind_candidate_message_revision_and_expiration(self):
        row=self.schedule();self.s.interview_message(self.app,1,500,1)
        for actor,msg,revision in ((3,500,1),(1,501,1),(1,500,2)):
            with self.assertRaises(ValueError): self.s.interview_reply(self.app,actor,msg,revision,'confirmed')
        row=self.s.interview_reply(self.app,1,500,1,'confirmed')
        self.assertEqual(row['status'],'confirmed')
        with self.assertRaises(ValueError):self.s.interview_reply(self.app,1,500,1,'confirmed')
        self.s.interview_reply(self.app,1,500,1,'reschedule','După ora 18')
        self.assertEqual(self.s.interview(self.app)['request'],'După ora 18')
        with self.assertRaises(ValueError):self.s.interview_reply(self.app,1,500,1,'confirmed')

    def test_reprogramming_invalidates_previous_message_and_does_not_reset_application(self):
        self.schedule();self.s.interview_message(self.app,1,500,1)
        row=self.schedule()
        self.assertEqual(row['revision'],2);self.assertIsNone(row['message'])
        with self.assertRaises(ValueError):self.s.interview_reply(self.app,1,500,1,'confirmed')
        self.assertEqual(self.s.helper_application(self.app)['status'],'pending')

    def test_trial_and_result_single_execution_private_notes_and_final_evaluation(self):
        self.schedule()
        self.s.interview_result(self.app,2,'trial','Evaluare inițială bună',3,7)
        trial=self.s.trial(self.app)
        self.assertEqual(trial['ends']-trial['started'],7*86400)
        with self.assertRaises(ValueError):self.s.interview_result(self.app,2,'accepted','Alt rezultat')
        with self.assertRaises(ValueError):self.s.trial_note(self.app,1,'Note falsificate',2)
        self.s.trial_note(self.app,3,'Activitate bună',2)
        self.s.trial_finish(self.app,2,'passed','Rezultate bune')
        with self.assertRaises(ValueError):self.s.trial_finish(self.app,2,'failed','Alt rezultat')
        with self.assertRaises(ValueError):self.s.trial_note(self.app,3,'Notă după închidere',2)
        self.assertEqual(self.s.trial(self.app)['status'],'passed')
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM staff_interview_events WHERE event='trial-note'").fetchone()[0],1)

    def test_self_review_decided_application_and_invalid_result_refused(self):
        with self.assertRaises(ValueError):self.s.interview_schedule(self.app,1,time.time()+3600,30,'Loc')
        self.schedule()
        with self.assertRaises(ValueError):self.s.interview_result(self.app,1,'accepted','Propria cerere')
        with self.assertRaises(ValueError):self.s.interview_result(self.app,2,'trial','Fără responsabil')
        self.s.helper_decide(self.app,2,'rejected','Respinsă separat')
        with self.assertRaises(ValueError):self.s.interview_result(self.app,2,'accepted','Alt rezultat')

    def test_restart_retains_interview_trial_and_reminder_claim(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db';s=Store(path)
            app=s.helper_create(1,'[]',20,0,0)
            s.helper_set_message(app,99)
            s.interview_schedule(app,2,time.time()+3600,30,'Voce')
            s.interview_message(app,1,500,1)
            self.assertTrue(s.reminder_reserve(app,1,'interview',1))
            s.db.close();s=Store(path)
            self.assertEqual(s.interview(app)['message'],500)
            self.assertFalse(s.reminder_reserve(app,1,'interview',1))
            s.interview_result(app,2,'trial','Probă acordată',3,14)
            s.trial_note(app,3,'Activitate bună',2)
            s.db.close();s=Store(path)
            self.assertEqual(s.trial(app)['responsible'],3)
            self.assertEqual(s.db.execute("SELECT detail FROM staff_interview_events WHERE event='trial-note'").fetchone()[0],'Activitate bună')
            s.db.close()


class InterviewBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.StaffApplicationTests.asyncSetUp(self)
        self.i.user=self.staff_member
        self.app=self.bot.store.helper_create(1,json.dumps(fixtures.answers('support')),20,0,0,kind='support')
        self.bot.store.helper_set_message(self.app,99)
        self.dm=NS(send=AsyncMock(return_value=NS(id=500)))
        self.bot.fetch_user=AsyncMock(return_value=self.dm)
        self.i.response.send_modal=AsyncMock()
        self.bot.store.interview_schedule(self.app,2,time.time()+600,30,'Canal vocal')
    async def asyncTearDown(self):await fixtures.StaffApplicationTests.asyncTearDown(self)

    async def test_schedule_button_owner_only_and_modal(self):
        self.i.channel_id=20;self.i.message=NS(id=99)
        view=HelperReview(self.bot)
        self.i.user=self.extra;self.extra.guild_permissions.administrator=True
        with self.assertRaises(ValueError):await view.children[2].callback(self.i)
        self.i.user=self.staff_member
        await view.children[2].callback(self.i)
        self.i.response.send_modal.assert_awaited_once()

    async def test_dm_confirmation_and_reprogramming_with_spoofed_actor_refused(self):
        await self.bot.send_interview_invitation(self.bot.store.interview(self.app))
        self.i.message=NS(id=500);self.i.guild_id=None
        view=InterviewReply(self.bot)
        self.assertTrue(view.is_persistent())
        with self.assertRaises(ValueError):await view.children[0].callback(self.i)
        self.i.user=self.owner
        await view.children[0].callback(self.i)
        self.assertEqual(self.bot.store.interview(self.app)['status'],'confirmed')
        await view.children[1].callback(self.i)
        self.assertIsInstance(self.i.response.send_modal.await_args.args[0],RescheduleInterviewModal)

    async def test_reminders_once_per_person_revision_and_restart(self):
        row=self.bot.store.interview(self.app)
        await self.bot.process_recruitment_reminders()
        self.assertEqual(self.dm.send.await_count,2)
        await self.bot.process_recruitment_reminders()
        self.assertEqual(self.dm.send.await_count,2)
        self.bot.store.interview_schedule(self.app,2,time.time()+600,30,'Alt canal')
        await self.bot.process_recruitment_reminders()
        self.assertEqual(self.dm.send.await_count,4)

    async def test_request_for_reschedule_and_past_interview_have_no_reminders(self):
        self.bot.store.interview_message(self.app,1,500,1)
        self.bot.store.interview_reply(self.app,1,500,1,'reschedule','Mâine după 18')
        await self.bot.process_recruitment_reminders()
        self.dm.send.assert_not_awaited()
        self.bot.store.interview_schedule(self.app,2,time.time()-20,30,'Vechi')
        await self.bot.process_recruitment_reminders()
        self.dm.send.assert_not_awaited()

    async def test_blocked_dm_is_recorded_and_status_is_fallback(self):
        self.dm.send.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'DM disabled')
        self.assertFalse(await self.bot.send_interview_invitation(self.bot.store.interview(self.app)))
        self.assertEqual(self.bot.store.interview(self.app)['dm_state'],-1)
        self.i.user=self.owner
        await self.bot.tree.get_command('staff_recruitment_status').callback(self.i,self.app)
        self.assertIn('Canal vocal',self.i.followup.send.await_args.args[0])
        self.i.user=self.extra
        with self.assertRaises(ValueError):await self.bot.tree.get_command('staff_recruitment_status').callback(self.i,self.app)

    async def test_trial_reminder_owner_responsible_once_and_internal_notes_hidden(self):
        self.bot.store.interview_result(self.app,2,'trial','Bun candidat',3,1)
        self.bot.store.db.execute('UPDATE staff_trial SET ends=? WHERE application=?',(time.time()-1,self.app))
        self.bot.store.db.commit()
        self.bot.store.trial_note(self.app,3,'Observație confidențială',2)
        await self.bot.process_recruitment_reminders()
        self.assertEqual(self.dm.send.await_count,2)
        await self.bot.process_recruitment_reminders()
        self.assertEqual(self.dm.send.await_count,2)
        self.i.user=self.owner
        await self.bot.tree.get_command('staff_recruitment_status').callback(self.i,self.app)
        self.assertNotIn('confidențială',self.i.followup.send.await_args.args[0])
        self.i.user=self.staff_member
        await self.bot.tree.get_command('staff_recruitment_status').callback(self.i,self.app)
        self.assertIn('confidențială',self.i.followup.send.await_args.args[0])

    async def test_result_command_creates_trial_and_never_grants_roles(self):
        self.staff_member.guild=self.guild
        self.guild.fetch_member=AsyncMock(return_value=self.staff_member)
        await self.bot.tree.get_command('staff_interview_result').callback(self.i,self.app,
            NS(value='trial'),'Rezultat interviu bun',self.staff_member,7)
        self.assertEqual(self.bot.store.trial(self.app)['responsible'],2)
        self.assertEqual(self.bot.store.interview(self.app)['result'],'trial')
        self.owner.add_roles.assert_not_called()
        self.assertIn('probă',self.dm.send.await_args_list[0].args[0])
        self.assertIn('responsabil',self.dm.send.await_args_list[1].args[0])
        await self.bot.tree.get_command('staff_trial_finish').callback(self.i,self.app,NS(value='passed'),'Activitate bună')
        self.assertEqual(self.bot.store.trial(self.app)['status'],'passed')
        self.owner.add_roles.assert_not_called()

    async def test_managers_and_other_admin_cannot_record_results_or_finish_trial(self):
        self.i.user=self.extra;self.extra.guild_permissions.administrator=True
        for name,args in [('staff_interview_result',(self.app,NS(value='accepted'),'Motiv final')),
                          ('staff_trial_finish',(self.app,NS(value='passed'),'Motiv final')),
                          ('staff_interview_resend',(self.app,))]:
            with self.assertRaises(ValueError):await self.bot.tree.get_command(name).callback(self.i,*args)
        self.assertIsNone(self.bot.store.interview(self.app)['result'])
