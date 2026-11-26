# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from tihulu_jarvis import profiles
from gateway import Context,FixtureGateway,Request,Refused
from fixture_lifetime import FixtureLifetime

class LifetimeTests(unittest.TestCase):
    def make(self):
        ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        gate=FixtureGateway(ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        req=Request(ctx,"engine","target","local","private").checked()
        grant=gate.fixture_grant("test",req,60)
        gate.claim(grant,req)
        return gate,grant,req

    def test_normal_exit_closes_all_gateways(self):
        a,ga,ra=self.make()
        b,gb,rb=self.make()
        with FixtureLifetime() as lifetime:
            lifetime.track(a)
            lifetime.track(b)
            a.checkpoint(ga,ra)
            b.checkpoint(gb,rb)
        for gate,grant,req in ((a,ga,ra),(b,gb,rb)):
            with self.assertRaises(Refused):
                gate.checkpoint(grant,req)

    def test_exception_closes_claimed_and_unclaimed_grants(self):
        a,ga,ra=self.make()
        with self.assertRaisesRegex(RuntimeError,"fixture failure"):
            with FixtureLifetime() as lifetime:
                lifetime.track(a)
                unclaimed=a.fixture_grant("unclaimed",ra,60)
                raise RuntimeError("fixture failure")
        for grant in (ga,unclaimed):
            with self.assertRaises(Refused):
                a.checkpoint(grant,ra)

    def test_early_return_closes_gateway(self):
        a,ga,ra=self.make()
        def run():
            with FixtureLifetime() as lifetime:
                lifetime.track(a)
                return 1
        self.assertEqual(run(),1)
        with self.assertRaises(Refused):
            a.checkpoint(ga,ra)

    def test_duplicate_or_post_close_tracking_denied(self):
        a,ga,ra=self.make()
        b,gb,rb=self.make()
        lifetime=FixtureLifetime()
        lifetime.track(a)
        with self.assertRaises(Refused):
            lifetime.track(a)
        lifetime.close()
        lifetime.close()
        with self.assertRaises(Refused):
            lifetime.track(b)
        b.checkpoint(gb,rb)
        b.close()
