# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from unittest.mock import patch
from dataclasses import replace
from tihulu_jarvis import profiles
from gateway import Context, Request, FixtureGateway, Refused

class GatewayChainEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("task","session","profile",1,"local","LOCAL_ONLY")
        self.g=FixtureGateway(self.ctx,(profiles.Rule("filesystem_read",(),"allow"),),clock=profiles.PolicyClock())
        self.chain=[]
        for i in range(12):
            req=Request(self.ctx,"engine","target"+str(i),"local")
            grant=self.g.fixture_grant("g"+str(i),req,60)
            if self.chain:
                self.g.claim_with_dependencies(grant,req,tuple(self.chain))
            else:
                self.g.claim(grant,req)
            self.chain.append((grant,req))
    def test_dense_chain_evaluates_exact_grants_once_per_group(self):
        with patch.object(self.g,"_validate_one_locked",wraps=self.g._validate_one_locked) as check:
            self.g.checkpoint_claimed_group(tuple(reversed(self.chain)))
            self.assertEqual(check.call_count,len(self.chain))
    def test_duplicate_reference_different_request_not_skipped(self):
        grant,req=self.chain[0]
        with self.assertRaises(Refused):
            self.g.checkpoint_claimed_group((self.chain[-1],(grant,replace(req,target="wrong"))))
    def test_duplicate_reference_forged_grant_not_skipped(self):
        grant,req=self.chain[0]
        with self.assertRaises(Refused):
            self.g.checkpoint_claimed_group((self.chain[-1],(replace(grant),req)))
    def test_revoked_shared_ancestor_still_refused(self):
        self.g.revoke("g0")
        with self.assertRaises(Refused):
            self.g.checkpoint_claimed_group(tuple(reversed(self.chain)))
