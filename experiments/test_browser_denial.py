# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from urllib.parse import quote
from browser_engine_acceptance import WebDriverError, denied_navigation

class BrowserDenialTests(unittest.TestCase):
    target="http://127.0.0.1:52909/fixture"
    def error(self,code="connectionFailure",target=None,extra="",status=500,kind="unknown error"):
        url=quote(self.target if target is None else target,safe="/")
        return WebDriverError(status,kind,"Reached error page: about:neterror?e="+code+"&u="+url+extra)
    def test_exact_gecko_connection_failure_and_timeout(self):
        for code in ("connectionFailure","netTimeout"):
            self.assertTrue(denied_navigation(self.error(code,extra="&a="),self.target))
    def test_wrong_destination_or_non_network_failure_denied(self):
        for err in (self.error(target="http://127.0.0.1:1/fixture"),
                    self.error("dnsNotFound"),self.error("nssBadCert"),
                    self.error(status=404),self.error(kind="invalid session id")):
            self.assertFalse(denied_navigation(err,self.target))
    def test_bare_or_embedded_error_not_evidence(self):
        for message in ("NS_ERROR_CONNECTION_REFUSED","prefix "+self.error().message,
                        "Reached error page: about:neterror?"):
            self.assertFalse(denied_navigation(WebDriverError(500,"unknown error",message),self.target))
    def test_duplicate_parameters_and_fragment_denied(self):
        for extra in ("&e=connectionFailure","&u=","#fragment","&x=bad space"):
            self.assertFalse(denied_navigation(self.error(extra=extra),self.target))
