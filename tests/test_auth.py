import _environment  # Isolate app import before any persistent state is initialized.
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from thoth.app import app
from thoth import auth


class AuthTests(unittest.TestCase):
    def test_login_session_logout_and_bad_cookie(self):
        with patch.object(auth,'PASSWORD','test-only-long-password'):
            client=TestClient(app,follow_redirects=False)
            self.assertEqual(client.get('/api/live').status_code,401)
            self.assertEqual(client.get('/api/live/entry-diagnostics').status_code,401)
            self.assertEqual(client.get('/api/live/fast-movers').status_code,401)
            self.assertEqual(client.get('/api/live/watchlist-review').status_code,401)
            self.assertEqual(client.get('/api/live/market-leaders').status_code,401)
            self.assertEqual(client.get('/').status_code,307)
            self.assertEqual(client.post('/api/login',json={'password':'wrong'}).status_code,401)
            response=client.post('/api/login',json={'password':'test-only-long-password'})
            self.assertEqual(response.status_code,200)
            self.assertIn('HttpOnly',response.headers['set-cookie'])
            self.assertEqual(client.get('/api/live').status_code,200)
            old_cookie=client.cookies.get('thoth_session')
            self.assertEqual(client.post('/api/logout').status_code,200)
            self.assertEqual(client.get('/api/live',headers={'Cookie':'thoth_session='+old_cookie}).status_code,401)
            self.assertEqual(client.get('/api/live').status_code,401)
            client.cookies.set('thoth_session','forged')
            self.assertEqual(client.get('/api/live').status_code,401)

    def test_cross_origin_login_is_rejected(self):
        client=TestClient(app)
        self.assertEqual(client.post('/api/login',json={'password':'anything'},headers={'Origin':'https://other.example'}).status_code,403)


class PublicAssetTests(unittest.TestCase):
    def test_login_assets_public_but_state_private_and_invalid_host_rejected(self):
        with patch.object(auth,'PASSWORD','test-long-password'):
            client=TestClient(app,follow_redirects=False)
            self.assertEqual(client.get('/login.js').status_code,200)
            self.assertEqual(client.get('/api/live').status_code,401)
            self.assertEqual(client.get('/login',headers={'Host':'untrusted.example'}).status_code,400)
            self.assertEqual(client.get('/login').headers['X-Frame-Options'],'DENY')
