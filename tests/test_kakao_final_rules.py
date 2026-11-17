# -*- coding: utf-8 -*-
"""2026-11-17 마지막 묶음: 이름 · 파일 이름, 압축 파일 검사, 미리보기 접속 주소, AI 비서 근거 범위,
비밀번호 길이 · 변경, 쓰지 않는 첨부 정리, 하루 첨부 용량, 읽음 알림의 안 읽은 수 (명세 §4.1 · §4.5)"""

import base64
import http.server
import io
import threading
import unittest
import zipfile
from datetime import datetime, timedelta
from unittest import mock

from tests.test_kakao_server import ServerTestBase, Client, PNG_1PX, uname

from server.kakao import file_policy
from server.kakao import server as server_mod
from server.kakao.protocol import clean_file_name, has_hidden_chars
from server.kakao.store import BOT_USERNAME, ISO_FMT

RLO = '\u202e'


def b64(data):
    return base64.b64encode(data).decode('ascii')


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


class NameRuleTests(ServerTestBase):
    def test_file_name_cleaning(self):
        self.assertEqual(clean_file_name('C:\\Users\\me\\보고서.hwp'), '보고서.hwp')
        self.assertEqual(clean_file_name('../../etc/passwd'), 'passwd')
        self.assertEqual(clean_file_name(f'x{RLO}gnp.exe'), 'xgnp.exe')
        self.assertEqual(clean_file_name('a\r\nb.txt '), 'ab.txt')
        self.assertEqual(clean_file_name('메모.txt. .'), '메모.txt')
        for empty in ('..', '/', RLO, ' . '):
            self.assertEqual(clean_file_name(empty), '파일')
        self.assertFalse(has_hidden_chars('가족👨‍👩‍👧'))                          # 이모지 결합 문자는 그대로 둔다

    def test_stored_attachment_name_is_cleaned(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': '이름 시험',
                'attachment': {'name': f'..\\..\\memo{RLO}txt.doc', 'mime': 'text/plain', 'data_b64': b64(b'hi')}})
        att = b.wait_for('msg_new', lambda m: m['message']['content'] == '이름 시험')['message']['attachment']
        self.assertEqual(att['name'], 'memotxt.doc')
        got = b.request({'type': 'attachment_get', 'attachment_id': att['attachment_id']}, 'attachment_data')
        self.assertEqual(got['name'], 'memotxt.doc')

    def test_names_are_single_visible_lines(self):
        a, b = self.connect(), self.connect()
        for payload in ({'type': 'profile_update', 'display_name': f'관리자{RLO}'},
                        {'type': 'profile_update', 'display_name': '두\n줄'},
                        {'type': 'room_create', 'name': f'방{RLO}', 'members': [b.username]},
                        {'type': 'open_room_create', 'name': '열린방', 'nickname': '별\t명'}):
            self.assertIn('사용할 수 없는 문자', a.expect_error(payload)['message'], payload)
        a.send({'type': 'profile_update', 'display_name': '가족👨‍👩‍👧'})
        a.wait_for('user_updated', lambda m: m['user']['display_name'] == '가족👨‍👩‍👧')


class ArchiveCheckTests(unittest.TestCase):
    def check(self, name, data):
        return file_policy.check(None, name, data)

    def test_plain_archives_and_documents_pass(self):
        self.assertIsNone(self.check('자료.zip', make_zip([('a.txt', b'hello'), ('dir/b.csv', b'1,2')])))
        document = make_zip([('[Content_Types].xml', b'<Types/>'), ('word/document.xml', b'<w/>'),
                             ('Scripts/headerScripts.js', b'var a = 1;'), ('word/media/image1.png', PNG_1PX)])
        self.assertIsNone(self.check('보고서.docx', document))
        self.assertIsNone(self.check('문서.hwpx', make_zip([('mimetype', b'application/hwp+zip'),
                                                         ('Scripts/sourceScripts.js', b'function f(){}')])))

    def test_every_entry_name_is_checked(self):
        entries = [(f'f{i}.txt', b'x') for i in range(2500)] + [('tool.exe', b'MZ')]
        self.assertEqual(self.check('많음.zip', make_zip(entries))['rule'], 'type')

    def test_entry_content_is_checked(self):
        found = self.check('자료.zip', make_zip([('readme.txt', b'MZ' + b'\0' * 100)]))
        self.assertEqual((found['rule'], found['value']), ('type', 'executable'))
        self.assertIn('readme.txt', found['detail'])

    def test_archive_inside_archive(self):
        inner = make_zip([('setup.exe', b'MZ')])
        self.assertIsNotNone(self.check('겉.zip', make_zip([('안.zip', inner)])))
        renamed = make_zip([('note.txt', b'MZ' + b'\0' * 50)])
        self.assertIsNotNone(self.check('겉.zip', make_zip([('안.dat', renamed)])))
        self.assertIsNone(self.check('겉.zip', make_zip([('안.zip', make_zip([('a.txt', b'ok')]))])))

    def test_document_holding_a_program(self):
        document = make_zip([('[Content_Types].xml', b'<Types/>'), ('word/document.xml', b'<w/>'),
                             ('word/embeddings/payload.bin', b'MZ' + b'\0' * 100)])
        found = self.check('보고서.docx', document)
        self.assertEqual(found['value'], 'executable')

    def test_shortcut_and_disk_image_extensions(self):
        for name in ('링크.url', '도움말.chm', '디스크.iso', '이미지.vhd', '설정.inf'):
            self.assertEqual(self.check(name, b'data')['value'], 'executable', name)

    def test_large_listing_stays_quick(self):
        import time
        data = make_zip([(str(i), b'') for i in range(60000)])
        started = time.monotonic()
        self.assertIsNone(self.check('많음.zip', data))
        self.assertLess(time.monotonic() - started, 3.0)


class PreviewAddressTests(unittest.TestCase):
    def setUp(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<html><head><title>page</title></head></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def test_connection_goes_to_the_checked_address(self):
        url = f'http://preview-host.invalid:{self.port}/'                        # 이름 풀이가 되지 않는 호스트
        with mock.patch.object(server_mod, '_preview_addresses', return_value=['127.0.0.1']) as resolved:
            preview = server_mod.fetch_link_preview(url)
        self.assertEqual(preview['title'], 'page')                             # 확인한 주소로만 접속했다
        self.assertGreaterEqual(resolved.call_count, 2)                        # 확인 + 접속 직전

    def test_refused_address_is_never_contacted(self):
        self.assertIsNone(server_mod.fetch_link_preview(f'http://127.0.0.1:{self.port}/'))
        with mock.patch.object(server_mod, 'preview_host_allowed', return_value=True):   # 확인을 지나쳐도
            self.assertIsNone(server_mod.fetch_link_preview(f'http://127.0.0.1:{self.port}/'))   # 접속 단계에서 막힌다
        self.assertEqual(server_mod.fetch_link_preview(f'http://127.0.0.1:{self.port}/', block_internal=False)['title'],
                         'page')


class AiScopeTests(ServerTestBase):
    def test_group_question_uses_only_that_room(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        other = self.group(a, [c], name='다른방')['room_id']
        self.send_text(a, other, '다른 방의 이야기')
        a.send({'type': 'room_create', 'name': '봇방', 'members': [b.username, BOT_USERNAME]})
        rid = a.wait_for('room_updated', lambda m: m['room']['name'] == '봇방')['room']['room_id']
        self.send_text(b, rid, '상대가 한 말')
        self.send_text(a, rid, f'@{BOT_USERNAME} 질문입니다')
        a.wait_for('msg_new', lambda m: m['message']['sender'] == BOT_USERNAME)
        question, asker, room_ids, history = self.rag.questions[-1]
        self.assertEqual((asker, room_ids), (a.username, [rid]))
        self.assertTrue(all(m['sender'] in (a.username, BOT_USERNAME) for m in history))

    def test_direct_question_keeps_all_my_rooms(self):
        a, b = self.connect(), self.connect()
        other = self.group(a, [b])['room_id']
        a.send({'type': 'room_open_direct', 'target': BOT_USERNAME})
        rid = a.wait_for('room_updated', lambda m: BOT_USERNAME in m['room']['members'])['room']['room_id']
        self.send_text(a, rid, '내 질문')
        a.wait_for('msg_new', lambda m: m['message']['sender'] == BOT_USERNAME)
        self.assertIn(other, self.rag.questions[-1][2])


class PasswordRuleTests(ServerTestBase):
    SERVER_KWARGS = {'require_password': True}

    def setUp(self):
        super().setUp()
        self.store = self.server.store
        self.server._login_failures.clear()

    def user(self, password='old-password'):
        name = uname('pw')
        self.store.member_create(name, {'company': 'KCS 홀딩스', 'email': f'{name}@kcs.test'})
        self.store.set_password(name, password)
        return name

    def login(self, name, password='old-password', **extra):
        c = Client(self.port, name, join=False)
        self.clients.append(c)
        c.welcome = c.request({'type': 'join', 'username': name, 'password': password, **extra}, 'welcome')
        return c

    def test_new_passwords_need_eight_characters(self):
        self.assertEqual(self.server.password_min, 8)
        name = self.user()
        c = self.login(name)
        err = c.expect_error({'type': 'password_change', 'old_password': 'old-password', 'new_password': 'short77'})
        self.assertIn('8~64자', err['message'])
        code = self.store.issue_reset_code(name, '관리자')[0]
        err = Client(self.port, 'x', join=False)
        self.clients.append(err)
        self.assertIn('8~64자', err.expect_error({'type': 'password_reset', 'username': name, 'code': code,
                                                'email': f'{name}@kcs.test', 'new_password': '1234567'})['message'])
        short = self.user('abcd')                                               # 예전에 정한 짧은 비밀번호로 로그인은 된다
        self.login(short, 'abcd')

    def test_change_own_password(self):
        name = self.user()
        here = self.login(name, remember=True)
        elsewhere = self.login(name)
        old_token = here.welcome['login_token']
        err = here.expect_error({'type': 'password_change', 'old_password': 'wrong-one', 'new_password': 'new-password'})
        self.assertEqual(err['code'], 'auth_failed')
        done = here.request({'type': 'password_change', 'old_password': 'old-password', 'new_password': 'new-password',
                             'remember': True}, 'password_changed')
        self.assertTrue(done['login_token'] and done['login_token'] != old_token)
        self.assertEqual(elsewhere.wait_for('error', lambda m: m.get('code') == 'session_expired')['req_type'], '')
        here.request({'type': 'todo_list'}, 'todo_list')                        # 바꾼 연결은 그대로
        self.assertFalse(self.store.check_login_token(name, old_token))
        self.login(name, 'new-password')
        other = Client(self.port, name, join=False)
        self.clients.append(other)
        self.assertEqual(other.expect_error({'type': 'join', 'username': name, 'password': 'old-password'})['code'],
                         'auth_failed')

    def test_reset_by_admin_ends_open_sessions(self):
        admin, target = self.user(), self.user()
        self.store.set_admin(admin, True)
        a, t = self.login(admin), self.login(target)
        self.assertIn('8~64자', a.expect_error({'type': 'admin_reset_password', 'username': target,
                                              'new_password': 'short'})['message'])
        a.request({'type': 'admin_reset_password', 'username': target, 'new_password': 'reset-password'}, 'admin_done')
        t.wait_for('error', lambda m: m.get('code') == 'session_expired')
        self.login(target, 'reset-password')


class OpenServerPasswordTests(ServerTestBase):
    def test_test_servers_keep_four_characters(self):
        self.assertEqual(self.server.password_min, 4)
        self.register(password='abcd')


class StorageRuleTests(ServerTestBase):
    def attach(self, client, rid, name, data, kind='file'):
        client.send({'type': 'send', 'room_id': rid, 'kind': kind, 'content': name,
                     'attachment': {'name': name, 'mime': 'application/octet-stream', 'data_b64': b64(data)}})

    def age(self, attachment_id, hours):
        when = (datetime.now() - timedelta(hours=hours)).strftime(ISO_FMT)
        with self.server.store._lock:
            self.server.store._db.execute('UPDATE attachments SET created_at=? WHERE attachment_id=?',
                                          (when, attachment_id))
            self.server.store._db.commit()

    def test_unused_attachments_are_emptied_after_a_day(self):
        store = self.server.store
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        self.attach(a, rid, 'kept.txt', b'in a message')
        used = b.wait_for('msg_new', lambda m: m['message']['content'] == 'kept.txt')['message']['attachment']
        a.send({'type': 'profile_update', 'avatar_b64': b64(PNG_1PX)})
        first = a.wait_for('user_updated', lambda m: m['user']['username'] == a.username and m['user']['avatar_id'])
        a.send({'type': 'profile_update', 'avatar_b64': b64(PNG_1PX)})
        second = a.wait_for('user_updated', lambda m: m['user']['username'] == a.username
                            and m['user']['avatar_id'] != first['user']['avatar_id'])
        old_avatar, new_avatar = first['user']['avatar_id'], second['user']['avatar_id']
        fresh = store.save_attachment('fresh.bin', 'application/octet-stream', b'just uploaded', a.username)
        waiting = store.save_attachment('later.bin', 'application/octet-stream', b'scheduled', a.username)
        store.add_scheduled(rid, a.username, '2099-01-01T00:00:00', {'kind': 'file', 'content': 'x',
                                                                    'attachment_id': waiting})
        for attachment_id in (used['attachment_id'], old_avatar, new_avatar, waiting):
            self.age(attachment_id, 30)
        self.assertEqual(store.purge_orphan_attachments(), 1)                   # 바꾸기 전 프로필 사진만
        self.assertEqual(store.get_attachment(old_avatar)['data'], b'')
        for attachment_id in (used['attachment_id'], new_avatar, fresh, waiting):
            self.assertTrue(store.get_attachment(attachment_id)['data'], attachment_id)
        self.assertEqual(store.purge_orphan_attachments(), 0)

    def test_daily_upload_quota(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        self.server.MAX_DAILY_UPLOAD_BYTES = 1000
        try:
            self.attach(a, rid, 'one.bin', b'x' * 600)
            b.wait_for('msg_new', lambda m: m['message']['content'] == 'one.bin')
            a.send({'type': 'send', 'room_id': rid, 'kind': 'file', 'content': 'two.bin', 'req_id': 'q',
                    'attachment': {'name': 'two.bin', 'mime': 'application/octet-stream', 'data_b64': b64(b'x' * 600)}})
            err = a.wait_for('error', lambda m: m.get('req_id') == 'q')
            self.assertEqual(err['code'], 'quota_exceeded')
            self.attach(b, rid, 'mine.bin', b'x' * 600)                          # 다른 사람의 한도는 따로
            a.wait_for('msg_new', lambda m: m['message']['content'] == 'mine.bin')
        finally:
            del self.server.MAX_DAILY_UPLOAD_BYTES

    def test_read_update_tells_reader_the_unread_count(self):
        a, b = self.connect(), self.connect()
        rid = self.group(a, [b])['room_id']
        first = self.send_text(a, rid, '하나')
        self.send_text(a, rid, '둘')
        b2 = self.connect(b.username)
        b.clear()
        b.send({'type': 'read', 'room_id': rid, 'last_msg_id': first['msg_id']})
        for mine in (b, b2):
            ru = mine.wait_for('read_update', lambda m: m['username'] == b.username)
            self.assertEqual(ru['unread_count'], 1)
        self.assertNotIn('unread_count', a.wait_for('read_update', lambda m: m['username'] == b.username))
