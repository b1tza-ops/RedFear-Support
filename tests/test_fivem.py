import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
import test_features as fixtures
from fivem import endpoint_url, join_url, parse_status, status_embed, connect_view, PublicResolver

class ParsingTests(unittest.TestCase):
    def test_endpoint_normalization(self):
        self.assertEqual(endpoint_url('57.129.133.171:30120'),'http://57.129.133.171:30120')
        self.assertEqual(endpoint_url('play.example.com'),'http://play.example.com:30120')
        self.assertEqual(endpoint_url('https://play.example.com/'),'https://play.example.com')

    def test_unsafe_targets_rejected(self):
        for value in ['http://localhost:80','127.0.0.1:30120','http://169.254.169.254','http://10.0.0.1','http://[::1]',
                      'file:///etc/passwd','https://user:pass@example.com','https://example.com/?token=secret',
                      'https://example.com/dynamic.json','https://example.com/#x','http://example.com:70000']:
            with self.subTest(value=value),self.assertRaises(ValueError): endpoint_url(value)

    def test_join_link_strict(self):
        self.assertEqual(join_url('abc123'),'https://cfx.re/join/abc123')
        self.assertEqual(join_url('https://cfx.re/join/abc123/'),'https://cfx.re/join/abc123')
        self.assertIsNone(join_url(''))
        for value in ['https://evil.example/','https://cfx.re.evil/join/abc123','fivem://connect/abc','https://cfx.re/join/abc123?x=1']:
            with self.assertRaises(ValueError): join_url(value)

    def test_dynamic_counts_and_color_codes(self):
        status=parse_status({'hostname':'^1RedFear','clients':12,'sv_maxclients':'64'})
        self.assertEqual((status['clients'],status['capacity'],status['hostname']),(12,64,'RedFear'))
        for value in [True,-1,3000,'unknown',None]:
            with self.assertRaises(ValueError): parse_status({'hostname':'RedFear','clients':value,'sv_maxclients':'64'})
        for data in [[],{}, {'hostname':'RedFear','clients':1,'sv_maxclients':'0'}]:
            with self.assertRaises(ValueError): parse_status(data)

    def test_unavailable_embed_never_shows_stale_counts(self):
        config={'endpoint':'http://play.example.com:30120','join':'https://cfx.re/join/abc123'}
        embed=status_embed({'online':False,'checked':1000,'clients':50,'capacity':64},config)
        self.assertIn('Indisponibil',embed.description)
        self.assertFalse(any(f.name=='Jucători' for f in embed.fields))
        self.assertEqual(connect_view(config).children[0].url,config['join'])
        self.assertEqual(len(connect_view({'join':None}).children),0)

class PollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.BotTests.asyncSetUp(self)
        self.config={'endpoint':'http://play.example.com:30120','join':'https://cfx.re/join/abc123','channel':20,'message':30}
        self.bot.store.set_setting('fivem:config',json.dumps(self.config))
        self.status={'online':True,'clients':3,'capacity':64,'hostname':'RedFear','checked':1000}
        self.bot.check_fivem=AsyncMock(return_value=self.status)
        self.message=NS(id=30,edit=AsyncMock())
        self.panel=NS(fetch_message=AsyncMock(return_value=self.message),send=AsyncMock())
        self.guild.fetch_channel=AsyncMock(return_value=self.panel)
        self.bot.get_guild=lambda gid:self.guild
    async def asyncTearDown(self): await fixtures.BotTests.asyncTearDown(self)

    async def test_poll_edits_same_message_and_saves_current_status(self):
        await self.bot.fivem_update()
        self.panel.fetch_message.assert_awaited_with(30)
        self.message.edit.assert_awaited_once(); self.panel.send.assert_not_awaited()
        self.assertEqual(json.loads(self.bot.store.setting('fivem:last'))['clients'],3)

    async def test_deleted_panel_does_not_spam_replacements(self):
        self.panel.fetch_message.side_effect=discord.NotFound(NS(status=404,reason='missing'),'missing')
        await self.bot.fivem_update()
        self.assertEqual(self.bot.fivem_config()['message'],0)
        await self.bot.fivem_update()
        self.bot.check_fivem.assert_awaited_once(); self.panel.send.assert_not_awaited()

    async def test_disabled_config_performs_no_network(self):
        self.bot.store.set_setting('fivem:config','')
        await self.bot.fivem_update(); self.bot.check_fivem.assert_not_awaited()

    async def test_failed_endpoint_becomes_unavailable(self):
        from fivem import FiveM
        with patch('fivem.fetch_status',side_effect=TimeoutError()):
            result=await FiveM.check_fivem(self.bot,self.config['endpoint'])
        self.assertFalse(result['online']); self.assertNotIn('clients',result)

    async def test_dns_rebinding_to_private_ip_rejected(self):
        resolver=PublicResolver()
        try:
            with patch('aiohttp.resolver.DefaultResolver.resolve',new=AsyncMock(return_value=[{'host':'127.0.0.1'}])):
                with self.assertRaises(ValueError): await resolver.resolve('play.example.com',30120)
        finally: await resolver.close()

    async def test_setup_stop_are_admin_only(self):
        self.i.user=self.owner
        with self.assertRaises(ValueError):
            await self.bot.tree.get_command('fivem_setup').callback(self.i,'play.example.com',self.panel)
        with self.assertRaises(ValueError): await self.bot.tree.get_command('fivem_stop').callback(self.i)

    async def test_setup_reuses_message(self):
        self.panel.id=20; self.panel.guild=NS(id=100)
        self.i.response=NS(defer=AsyncMock(),is_done=lambda:True)
        await self.bot.tree.get_command('fivem_setup').callback(self.i,'play.example.com:30120',self.panel,'abc123')
        self.message.edit.assert_awaited_once(); self.panel.send.assert_not_awaited()
        self.assertEqual(self.bot.fivem_config()['message'],30)

    async def test_status_respects_panel_channel_permissions(self):
        self.bot.store.set_setting('fivem:last',json.dumps(self.status))
        self.guild.get_channel=lambda cid:NS(permissions_for=lambda user:NS(view_channel=False))
        with self.assertRaises(ValueError): await self.bot.tree.get_command('fivem_status').callback(self.i)
