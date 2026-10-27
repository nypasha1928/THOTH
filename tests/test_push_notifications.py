"""All delivery is mocked: never contact a real push provider or trading account."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from thoth.push_notifications import PushNotifications,subscription,encode,NoRedirectSession
from thoth.production import ProductionSupervisor


def device(path='one'):
    key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    return dict(endpoint='https://fcm.googleapis.com/fcm/send/'+path,keys=dict(p256dh=encode(key),auth=encode(b'a'*16)))


class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1000.;self.sent=[]
        self.supervisor=ProductionSupervisor(self.root/'live.sqlite',clock=lambda:self.now)
        self.push=self.make()

    def tearDown(self):self.tmp.cleanup()

    def make(self):return PushNotifications(self.root/'push.sqlite',self.root/'live.sqlite','https://app.example.com',clock=lambda:self.now,sender=lambda value,payload:self.sent.append((value,payload)))

    def pause(self):
        self.supervisor.state.update(management=True,entries=True,status='running',reason='');self.supervisor.save()
        self.now+=1;self.supervisor._lock('safety_lock','Account monitoring unavailable')

    def test_pause_persists_and_delivers_once_without_network_in_save(self):
        self.push.register(device());self.pause();self.assertEqual(self.sent,[])
        self.push.capture();self.push.deliver();self.push.capture();self.push.deliver()
        self.assertEqual(len(self.sent),1)
        self.assertIn('Account monitoring unavailable',self.sent[0][1]['body'])
        self.assertEqual(self.push.status()['devices'][0]['status'],'accepted')
        self.supervisor._lock('safety_lock','Account monitoring unavailable')
        self.push.capture();self.push.deliver();self.assertEqual(len(self.sent),1)
        self.supervisor.state.update(entries=True,status='running',reason='');self.supervisor.save()
        saved=self.push.status()['recent_pauses']
        self.assertEqual(saved[0]['body'],'Account monitoring unavailable')
        self.assertEqual(saved[0]['time'],1001.)
        self.assertTrue(self.supervisor.state['entries'])

    def test_restart_notifies_without_reenabling_entries(self):
        self.push.register(device());self.supervisor.state.update(management=True,entries=True,status='running');self.supervisor.save()
        self.now+=1
        recovered=ProductionSupervisor(self.root/'live.sqlite',clock=lambda:self.now)
        self.assertFalse(recovered.state['entries'])
        restored=self.make();self.assertEqual(restored.public_key,self.push.public_key)
        restored.capture();restored.deliver()
        self.assertIn('Server restarted',self.sent[0][1]['body'])

    def test_failed_delivery_retries_and_expired_device_is_removed(self):
        self.push.register(device());self.pause();self.push.capture()
        self.push.sender=lambda *args:SimpleNamespace(status_code=503)
        self.push.deliver();self.assertEqual(self.push.status()['devices'][0]['status'],'delivery_failed')
        self.push.sender=lambda *args:SimpleNamespace(status_code=410)
        self.now+=31;self.push.deliver();self.assertEqual(self.push.status()['devices'],[])
        self.assertEqual(self.supervisor.state['status'],'safety_lock')

    def test_new_devices_do_not_receive_historical_pauses(self):
        self.pause();self.now+=1;self.push.register(device());self.push.capture();self.push.deliver()
        self.assertEqual(self.sent,[])

    def test_coin_cooldown_does_not_notify_and_disable_removes_jobs(self):
        value=device();self.push.register(value)
        self.supervisor.state.update(entries=True,status='running');self.supervisor.save()
        self.supervisor.event('BTCUSDT: consecutive-loss cooldown started');self.supervisor.save()
        self.push.capture();self.push.deliver();self.assertEqual(self.sent,[])
        self.pause();self.push.capture();self.push.remove(value);self.push.deliver();self.assertEqual(self.sent,[])

    def test_test_alert_is_queued_and_rate_limited(self):
        value=device();self.push.register(value);self.push.test(value)
        self.assertEqual(self.sent,[])
        with self.assertRaises(ValueError):self.push.test(value)
        self.push.deliver();self.assertEqual(self.sent[0][1]['title'],'THOTH test alert')
        self.assertFalse(self.supervisor.state['entries'])
        self.assertNotIn('endpoint',json.dumps(self.push.status()))
        self.assertNotIn('PRIVATE',json.dumps(self.push.status()))

    def test_endpoint_and_encryption_key_validation(self):
        value=device()
        for endpoint in ('http://fcm.googleapis.com/x','https://localhost/x','https://127.0.0.1/x','https://fcm.googleapis.com.evil.com/x','https://fcm.googleapis.com:8443/x','https://user@fcm.googleapis.com/x'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):subscription({**value,'endpoint':endpoint})
        with self.assertRaises(ValueError):subscription({**value,'keys':dict(auth=encode(b'a'*16),p256dh=encode(b'a'*65))})
        self.assertEqual(subscription(value),value)

    def test_real_payload_encryption_and_vapid_signing_without_transport(self):
        value=device()
        response=SimpleNamespace(status_code=201,text='',headers={})
        with patch.object(NoRedirectSession,'post',return_value=response) as post:
            result=self.push._send(value,dict(title='Test',body='Pause reason'))
        self.assertIs(result,response)
        self.assertEqual(post.call_args.args[0],value['endpoint'])
        self.assertIn('authorization',{k.lower():v for k,v in post.call_args.kwargs['headers'].items()})
        self.assertNotIn(b'Pause reason',post.call_args.kwargs['data'])

    def test_notification_transport_does_not_follow_redirects(self):
        with patch('requests.Session.post',return_value=SimpleNamespace(status_code=302)) as post:
            with NoRedirectSession() as session:session.post('https://fcm.googleapis.com/x',allow_redirects=True)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])


class PushApiTests(unittest.TestCase):
    def test_authenticated_opt_in_test_and_disable_do_not_enable_trading(self):
        import _environment
        from fastapi.testclient import TestClient
        from thoth.app import app
        from thoth import auth
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);live=ProductionSupervisor(root/'live.sqlite')
            service=PushNotifications(root/'push.sqlite',root/'live.sqlite','mailto:operator@localhost',sender=lambda *args:None)
            with patch('thoth.app.phone_alerts',service),patch.object(auth,'PASSWORD','fixture-password'):
                client=TestClient(app)
                self.assertEqual(client.get('/api/phone-alerts').status_code,401)
                self.assertEqual(client.post('/api/phone-alerts/enable',json=device()).status_code,401)
                self.assertEqual(client.post('/api/login',json={'password':'fixture-password'}).status_code,200)
                value=device()
                self.assertEqual(client.post('/api/phone-alerts/enable',json=value,headers={'Origin':'https://evil.example'}).status_code,403)
                result=client.post('/api/phone-alerts/enable',json=value);self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(len(client.get('/api/phone-alerts').json()['devices']),1)
                self.assertEqual(client.post('/api/phone-alerts/test',json=value).status_code,200)
                self.assertFalse(live.state['entries'])
                self.assertEqual(client.post('/api/phone-alerts/disable',json=value).status_code,200)
                self.assertEqual(client.get('/api/phone-alerts').json()['devices'],[])
