# -*- coding: utf-8 -*-
"""관리자 요청 처리 (명세 §4.2 · §4.3 · §4.4 · §10.10) - KakaoServer 가 이 믹스인을 상속한다
계정 관리 · 구성원 등록/수정/삭제 · 전체 공지 · 조직도 그룹 · 파일 전송 보안. server.py 가 너무 커져 나눴다
(self.store · self._reply · self._push · self._broadcast 등은 KakaoServer 에 있다)"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from . import file_policy
from .protocol import (ACCOUNT_DISABLED, ADMIN_ONLY, EMAIL_RE, EMPLOYEE_NO_RE, ProtocolError, _opt_int, _opt_str,
                       _opt_tag, _req_int, _req_str)
from .store import BOT_USERNAME, ISO_FMT, now_iso

if TYPE_CHECKING:
    from .server import Ctx

logger = logging.getLogger('kakao.server')


class AdminHandlers:
    """관리자 요청 처리기 묶음"""

    # ------------------------------------------------------------------ 관리자 (명세 §4.2)
    def _require_admin(self, ctx: Ctx) -> str:
        """관리 범위 ('' = 그룹 전체, '회사' = 그 회사만 - 명세 §4.3). 관리자가 아니면 오류"""
        scope = self.store.admin_scope(ctx.username)
        if scope is None:
            raise ProtocolError(ADMIN_ONLY)
        return scope

    def _require_group_admin(self, ctx: Ctx) -> None:
        if self._require_admin(ctx):
            raise ProtocolError('그룹 관리자만 할 수 있습니다.')

    def _check_scope(self, scope: str, company: Optional[str]) -> None:
        if scope and company != scope:
            raise ProtocolError(f'관리 범위({scope}) 밖의 구성원입니다.')

    def _admin_target(self, ctx: Ctx, msg: dict) -> str:
        scope = self._require_admin(ctx)
        username = _req_str(msg, 'username', 120, label='사용자')
        if not self.store.user_exists(username):
            raise ProtocolError('사용자를 찾을 수 없습니다.')
        if self.store.is_bot(username):
            raise ProtocolError('봇 계정은 관리할 수 없습니다.')
        self._check_scope(scope, self.store.user_company(username))
        if scope and username != ctx.username and self.store.is_admin(username):   # 삭제된 관리자 계정도
            # 회사 관리자가 같은 회사 소속 그룹 관리자 · 다른 관리자의 비밀번호를 바꾸거나 지우면 권한을 넘겨받을 수 있다
            raise ProtocolError('관리자 계정은 그룹 관리자만 관리할 수 있습니다.')
        return username

    MEMBER_TEXT_LIMITS = {'department': 60, 'team': 60, 'title': 60, 'account': 60, 'extension': 60, 'mobile': 60, 'email': 120}

    def _member_fields(self, msg: dict, scope: str, creating: bool) -> dict:
        """구성원 필드 검사 (명세 §4.3) → 바꿀 필드만"""
        fields: dict = {}
        if creating or 'company' in msg:
            company = msg.get('company').strip() if isinstance(msg.get('company'), str) else ''
            if company not in self.store.company_names():
                raise ProtocolError('소속 회사를 선택해 주세요.')
            self._check_scope(scope, company)
            fields['company'] = company
        for key, limit in self.MEMBER_TEXT_LIMITS.items():
            if key in msg:
                value = msg[key]
                if value is None:
                    value = ''
                if not isinstance(value, str) or len(value.strip()) > limit or any(ord(ch) < 32 for ch in value):
                    raise ProtocolError(f'{key} 값이 올바르지 않습니다.')
                fields[key] = value.strip()
        if fields.get('email') and not EMAIL_RE.fullmatch(fields['email']):
            raise ProtocolError('이메일 주소 형식이 올바르지 않습니다.')
        if 'employee_no' in msg:
            number = msg['employee_no'].strip() if isinstance(msg['employee_no'], str) else ''
            if number and not EMPLOYEE_NO_RE.fullmatch(number):
                raise ProtocolError('사번은 영문·숫자 20자 이내로 입력해 주세요.')
            fields['employee_no'] = number
        return fields

    def _h_admin_member_create(self, ctx: Ctx, msg: dict) -> None:
        scope = self._require_admin(ctx)
        name = _req_str(msg, 'username', 20, label='이름')
        if any(ord(ch) < 32 for ch in name) or '@' in name or name == BOT_USERNAME:
            raise ProtocolError('이름에 사용할 수 없는 문자가 있습니다.')
        fields = self._member_fields(msg, scope, creating=True)
        try:
            self.store.member_create(name, fields)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        self._broadcast_user(name)
        self._admin_done(ctx, 'member_create', '구성원 등록', username=name)

    def _h_admin_member_update(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        fields = self._member_fields(msg, self._require_admin(ctx), creating=False)
        try:
            self.store.member_update(username, fields)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        self._broadcast_user(username)
        self._admin_done(ctx, 'member_update', '구성원 수정', username=username)

    def _h_admin_member_delete(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        if username == ctx.username:
            raise ProtocolError('자기 계정은 삭제할 수 없습니다.')
        self.store.member_set_removed(username, True)
        for conn in self._user_conns([username]):
            conn.send({'type': 'account_disabled', 'message': ACCOUNT_DISABLED})
            self._disconnect(conn)
        self._broadcast({'type': 'user_removed', 'username': username})
        self._admin_done(ctx, 'member_delete', '구성원 삭제', username=username)

    def _h_admin_member_restore(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        self.store.member_set_removed(username, False)
        self._broadcast_user(username)
        self._admin_done(ctx, 'member_restore', '구성원 복원', username=username)

    def _admin_done(self, ctx: Ctx, action: str, what: str, **fields: Any) -> None:
        logger.info('관리자 %s: %s %s', ctx.username, what, fields.get('username', ''))
        self._reply(ctx, {'type': 'admin_done', 'action': action, **fields})

    def _h_admin_users(self, ctx: Ctx, msg: dict) -> None:
        scope = self._require_admin(ctx)
        users = [u for u in self.store.admin_user_list(self._online_users()) if not scope or u['company'] == scope]
        self._reply(ctx, {'type': 'admin_users', 'users': users, 'scope': scope})

    def _h_admin_reset_password(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        password = self._new_password(msg.get('new_password'))
        self.store.set_password(username, password)
        self.store.revoke_all_login_tokens(username)
        self._drop_sessions(username, keep=ctx.conn if username == ctx.username else None)
        self._admin_done(ctx, 'reset_password', '비밀번호 재설정', username=username)

    def _h_admin_clear_password(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        self.store.clear_password(username)
        self._admin_done(ctx, 'clear_password', '등록 초기화', username=username)

    def _h_admin_reset_code(self, ctx: Ctx, msg: dict) -> None:
        """비밀번호 재설정 코드 발급 (§4.1): 관리자가 본인임을 확인하고 코드를 알려 주면, 그 사람이 로그인 창의
        [비밀번호 재설정]에서 이메일 + 코드로 직접 새 비밀번호를 정한다 (관리자는 새 비밀번호를 모른다)"""
        username = self._admin_target(ctx, msg)
        if self.store.is_admin(username):
            raise ProtocolError('관리자 계정은 [비밀번호 재설정…]으로 바꿔 주세요.')
        # 비밀번호가 없는 계정에 발급하면 등록 코드다 (§4.1 '등록 방식' - 처음 사용 등록 · 비밀번호 재설정 화면에서 쓴다)
        registered = bool(self.store.password_hash(username))
        code, expires_at = self.store.issue_reset_code(username, ctx.username)
        self._admin_done(ctx, 'reset_code', '비밀번호 재설정 코드 발급' if registered else '등록 코드 발급',
                         username=username, code=code, expires_at=expires_at, registered=registered)

    def _h_admin_set_disabled(self, ctx: Ctx, msg: dict) -> None:
        username = self._admin_target(ctx, msg)
        disabled = msg.get('disabled')
        if not isinstance(disabled, bool):
            raise ProtocolError('disabled 는 true/false 여야 합니다.')
        if disabled and username == ctx.username:
            raise ProtocolError('자기 계정은 사용 중지할 수 없습니다.')
        if not disabled and self.store.is_removed(username):
            raise ProtocolError('삭제한 구성원입니다. [복원]으로 되살려 주세요.')
        self.store.set_disabled(username, disabled)
        self._admin_done(ctx, 'set_disabled', '사용 중지' if disabled else '사용 중지 해제',
                         username=username, disabled=disabled)
        if disabled:        # 접속 중이면 알리고 끊는다 (재연결해도 join 이 거절된다)
            for conn in self._user_conns([username]):
                conn.send({'type': 'account_disabled', 'message': ACCOUNT_DISABLED})
                self._disconnect(conn)   # 읽기 스레드를 기다리지 않고 바로 접속 목록에서 뺀다 (두 번 불려도 안전)

    def _h_admin_set_admin(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        username = self._admin_target(ctx, msg)
        is_admin = msg.get('is_admin')
        if not isinstance(is_admin, bool):
            raise ProtocolError('is_admin 은 true/false 여야 합니다.')
        company = msg.get('company') or ''
        if not isinstance(company, str) or (company and company.strip() not in self.store.company_names()):
            raise ProtocolError('소속 회사를 선택해 주세요.')
        try:
            self.store.set_admin(username, is_admin, company.strip())
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        self._broadcast_user(username)
        self._admin_done(ctx, 'set_admin', '관리자 지정' if is_admin else '관리자 해제',
                         username=username, is_admin=is_admin, company=company.strip() if is_admin else '')

    def _h_admin_notice(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        content = _req_str(msg, 'content', 1000, label='공지 내용')
        conns = self._all_joined_conns()
        payload = {'type': 'admin_notice', 'sender': ctx.username, 'content': content, 'timestamp': now_iso()}
        for conn in conns:
            conn.send(payload)
        self._admin_done(ctx, 'notice', '전체 공지', delivered=len({c.username for c in conns}))

    # ---- 파일 전송 보안 (명세 §10.10) ----
    FILE_REQUEST_PENDING_MAX = 20
    FILE_EXCEPTION_DAYS_MAX = 365

    def _file_policy(self) -> dict:
        return self.store.file_policy() or file_policy.DEFAULT_POLICY

    def _file_violation(self, username: str, name: str, data: bytes) -> Optional[dict]:
        """정책 위반이면 {rule, value, detail, category, sha256}. 관리자가 풀어 준 예외(사람 + 파일)면 None"""
        violation = file_policy.check(self._file_policy(), name, data)
        if violation is None:
            return None
        digest = file_policy.sha256_of(data)
        for exc in self.store.file_exceptions(username):
            # 이름 예외는 이름만 믿는다 - 확장자와 내용이 다른 파일(이름만 바꾼 실행 파일)에는 쓰지 않는다
            if (exc['sha256'] and exc['sha256'] == digest) or (exc['pattern'] and not violation['disguised']
                                                                and file_policy.name_matches(name, exc['pattern'])):
                logger.info('파일 전송 예외 적용 %s: %s (예외 %s)', username, name, exc['exception_id'])
                return None
        return {**violation, 'sha256': digest}

    def _file_request_view(self, request: dict) -> dict:
        """요청 + 관리자 화면에 보일 방 이름"""
        meta = self.store.room_meta(request['room_id'])
        if meta is None:
            label = '(없어진 채팅방)'
        elif meta['type'] in ('direct', 'self', 'secret'):
            label = ', '.join(m for m in meta['members'] if m != request['username']) or request['username']
            label = f'1:1 · {label}'
        else:
            label = meta['name'] or ', '.join(meta['members'][:4])
        return {**request, 'room_label': label}

    def _push_file_request(self, msg_type: str, request: dict, extra_users: tuple[str, ...] = ()) -> None:
        view = self._file_request_view(request)
        self._push(set(self.store.group_admins()) | set(extra_users), {'type': msg_type, 'request': view})

    def _h_file_send_request(self, ctx: Ctx, msg: dict) -> None:
        """막힌 파일을 보내 달라고 그룹 관리자에게 승인 요청. 파일은 승인될 때까지 서버에 보관한다"""
        meta = self._require_room_member(msg.get('room_id'), ctx.username)
        kind = msg.get('kind', 'file')
        if kind not in ('image', 'file'):
            raise ProtocolError('사진 · 파일만 승인 요청할 수 있습니다.')
        if meta['type'] == 'secret':
            raise ProtocolError('비밀채팅방에서는 파일을 보낼 수 없습니다.')
        name, mime, data = self._decode_attachment(msg.get('attachment'))
        self._check_upload_quota(ctx.username, len(data))
        kind, mime = self._checked_image_kind(kind, mime, data)
        client_tmp_id = _opt_tag(msg, 'client_tmp_id')
        content = _opt_str(msg, 'content', 200) or name
        reason = _req_str(msg, 'reason', 300, label='요청 사유').strip()
        if not reason:
            raise ProtocolError('관리자가 판단할 수 있게 요청 사유를 적어 주세요.')
        violation = self._file_violation(ctx.username, name, data)
        if violation is None:                    # 막히지 않는 파일(정책이 바뀌었거나 예외)이면 바로 보낸다
            attachment_id = self.store.save_attachment(name, mime, data, ctx.username)
            self._deliver_message(meta['room_id'], ctx.username, kind, content, None, [], attachment_id,
                                  client_tmp_id, ctx)
            return
        if self.store.pending_file_request_count(ctx.username) >= self.FILE_REQUEST_PENDING_MAX:
            raise ProtocolError(f'승인을 기다리는 요청이 {self.FILE_REQUEST_PENDING_MAX}건입니다. '
                                '관리자가 처리한 뒤 다시 요청해 주세요.')
        attachment_id = self.store.save_attachment(name, mime, data, ctx.username)
        request = self.store.add_file_request(ctx.username, meta['room_id'], kind, content, attachment_id, name,
                                              len(data), violation['sha256'], violation['rule'],
                                              violation['detail'], reason)
        self._reply(ctx, {'type': 'file_request_ok', 'request': self._file_request_view(request)})
        self._push_file_request('file_request_new', request)
        logger.info('파일 전송 승인 요청 %s: %s → %s (%s)', ctx.username, name, meta['room_id'], violation['detail'])

    def _h_file_request_list(self, ctx: Ctx, msg: dict) -> None:
        requests = [self._file_request_view(r) for r in self.store.file_requests(ctx.username, limit=100)]
        self._reply(ctx, {'type': 'file_requests', 'requests': requests})

    def _h_admin_file_policy_set(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        try:
            policy = file_policy.normalize_policy(msg.get('policy'))
        except file_policy.PolicyError as exc:
            raise ProtocolError(str(exc)) from None
        self.store.set_file_policy(policy)
        self._broadcast({'type': 'file_policy', 'policy': file_policy.public_policy(policy)})
        self._admin_done(ctx, 'file_policy', '파일 전송 보안 정책 변경', enabled=policy['enabled'])

    def _h_admin_file_requests(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        status = msg.get('status')
        if status is not None and status not in ('pending', 'approved', 'rejected'):
            raise ProtocolError('status 값이 올바르지 않습니다.')
        requests = [self._file_request_view(r) for r in self.store.file_requests(status=status)]
        self._reply(ctx, {'type': 'admin_file_requests', 'requests': requests})

    def _h_admin_file_request_decide(self, ctx: Ctx, msg: dict) -> None:
        """승인하면 그때 요청한 사람 이름으로 그 방에 파일을 보낸다. allow_days 가 있으면 같은 파일은 그 기간 동안 바로 보낼 수 있다"""
        self._require_group_admin(ctx)
        request = self.store.file_request(_req_int(msg, 'request_id'))
        if request is None:
            raise ProtocolError('승인 요청을 찾을 수 없습니다.')
        approve = msg.get('approve')
        if not isinstance(approve, bool):
            raise ProtocolError('approve 는 true/false 여야 합니다.')
        note = _opt_str(msg, 'note', 300).strip()
        allow_days = _opt_int(msg, 'allow_days') or 0
        if not 0 <= allow_days <= self.FILE_EXCEPTION_DAYS_MAX:
            raise ProtocolError(f'허용 기간은 0~{self.FILE_EXCEPTION_DAYS_MAX}일입니다.')
        if request['status'] != 'pending':
            raise ProtocolError('이미 처리된 요청입니다.')
        if approve and not self.store.is_member(request['room_id'], request['username']):
            raise ProtocolError('요청한 사람이 더 이상 그 채팅방에 없어 보낼 수 없습니다. 반려해 주세요.')
        try:
            decided = self.store.decide_file_request(request['request_id'], 'approved' if approve else 'rejected',
                                                     ctx.username, note)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        if approve:
            try:
                message = self._deliver_message(request['room_id'], request['username'], request['kind'],
                                                request['content'], None, [], request['attachment_id'], None, None)
            except Exception:      # 보내지 못했으면 승인으로 남기지 않는다 - 다시 대기로 돌려 다시 결정할 수 있게
                logger.exception('승인한 파일 전송 실패 (요청 %s)', request['request_id'])
                self.store.reopen_file_request(request['request_id'])
                raise ProtocolError('파일을 보내지 못했습니다. 잠시 후 다시 승인해 주세요.') from None
            decided = self.store.set_file_request_msg(request['request_id'], message['msg_id'])
            if allow_days:
                self.store.add_file_exception(request['username'], request['sha256'], request['name'], '',
                                              (datetime.now() + timedelta(days=allow_days)).strftime(ISO_FMT),
                                              note, ctx.username, request['request_id'])
        self._push_file_request('file_request_updated', decided, (request['username'],))
        self._admin_done(ctx, 'file_request_decide', '파일 전송 승인' if approve else '파일 전송 반려',
                         username=request['username'], request_id=request['request_id'], approved=approve)

    def _h_admin_file_exceptions(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        self._reply(ctx, {'type': 'admin_file_exceptions', 'exceptions': self.store.file_exceptions()})

    def _h_admin_file_exception_add(self, ctx: Ctx, msg: dict) -> None:
        """특정인에게 특정 파일(sha256) 또는 파일 이름 패턴을 기간을 정해 풀어 준다"""
        self._require_group_admin(ctx)
        username = self._admin_target(ctx, msg)
        sha256 = _opt_str(msg, 'sha256', 64).strip().lower()
        pattern = _opt_str(msg, 'pattern', file_policy.MAX_RULE_LEN).strip()
        if sha256 and not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise ProtocolError('파일 지문(sha256) 값이 올바르지 않습니다.')
        if any(ord(ch) < 32 for ch in pattern):
            raise ProtocolError('파일 이름 값이 올바르지 않습니다.')
        if not sha256 and not pattern:
            raise ProtocolError('풀어 줄 파일 이름(예: *.zip, 설계도.dwg)을 적어 주세요.')
        days = _req_int(msg, 'days')
        if not 1 <= days <= self.FILE_EXCEPTION_DAYS_MAX:
            raise ProtocolError(f'허용 기간은 1~{self.FILE_EXCEPTION_DAYS_MAX}일입니다.')
        note = _opt_str(msg, 'note', 300).strip()
        file_name = _opt_str(msg, 'file_name', 200).strip()
        exception = self.store.add_file_exception(username, sha256, file_name, pattern,
                                                  (datetime.now() + timedelta(days=days)).strftime(ISO_FMT),
                                                  note, ctx.username)
        self._admin_done(ctx, 'file_exception_add', '파일 전송 예외 추가', username=username,
                         exception=exception)

    def _h_admin_file_exception_delete(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        exception_id = _req_int(msg, 'exception_id')
        if not self.store.delete_file_exception(exception_id):
            raise ProtocolError('예외를 찾을 수 없습니다.')
        self._admin_done(ctx, 'file_exception_delete', '파일 전송 예외 삭제', exception_id=exception_id)

    # ---- 조직도 그룹 (명세 §4.4) ----
    GROUP_MEMBERS_MAX = 500

    def _group_fields(self, msg: dict) -> tuple[str, str, list[str]]:
        name = _req_str(msg, 'name', 40, label='그룹 이름')
        description = _opt_str(msg, 'description', 100)
        if any(ord(ch) < 32 for ch in name + description):
            raise ProtocolError('그룹 이름에 사용할 수 없는 문자가 있습니다.')
        members = msg.get('members', [])
        if not isinstance(members, list) or len(members) > self.GROUP_MEMBERS_MAX \
                or not all(isinstance(m, str) and m.strip() for m in members):
            raise ProtocolError('members 값이 올바르지 않습니다.')
        return name, description, list(dict.fromkeys(m.strip() for m in members))

    def _group_target(self, ctx: Ctx, msg: dict) -> int:
        self._require_group_admin(ctx)
        group_id = _req_int(msg, 'group_id')
        if self.store.org_group(group_id) is None:
            raise ProtocolError('그룹을 찾을 수 없습니다.')
        return group_id

    def _broadcast_org_groups(self) -> None:
        self._broadcast({'type': 'org_groups', 'groups': self.store.org_groups(), 'order': self.store.org_order()})

    ORG_ORDER_MAX = 600

    def _h_admin_org_order(self, ctx: Ctx, msg: dict) -> None:
        """조직도 아래쪽 덩어리(그룹 · 게스트 등) 순서 - 그룹 관리자만. KCS AI · 그룹사는 고정이라 목록에 없다"""
        self._require_group_admin(ctx)
        order = msg.get('order')
        if not isinstance(order, list) or len(order) > self.ORG_ORDER_MAX or len(set(map(str, order))) != len(order):
            raise ProtocolError('order 값이 올바르지 않습니다.')
        for token in order:
            kind, _, value = token.partition(':') if isinstance(token, str) else ('', '', '')
            if not ((kind == 'group' and value.isdigit()) or
                    (kind == 'company' and value.strip() and len(value) <= 60 and value == value.strip())):
                raise ProtocolError('order 값이 올바르지 않습니다.')
        try:
            self.store.set_org_order(order)
        except ValueError as exc:
            self._broadcast_org_groups()          # 실패해도 요청한 사람 화면을 서버 순서로 되돌린다
            raise ProtocolError(str(exc)) from None
        self._broadcast_org_groups()
        self._admin_done(ctx, 'org_order', '조직도 순서 변경', order=order)

    def _h_admin_group_create(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        name, description, members = self._group_fields(msg)
        try:
            group_id = self.store.org_group_create(name, description, members, ctx.username)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        self._broadcast_org_groups()
        self._admin_done(ctx, 'group_create', '그룹 만들기', group_id=group_id, name=name)

    def _h_admin_group_update(self, ctx: Ctx, msg: dict) -> None:
        group_id = self._group_target(ctx, msg)
        name, description, members = self._group_fields(msg)
        try:
            self.store.org_group_update(group_id, name, description, members)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        self._broadcast_org_groups()
        self._admin_done(ctx, 'group_update', '그룹 수정', group_id=group_id, name=name)

    def _h_admin_group_reorder(self, ctx: Ctx, msg: dict) -> None:
        self._require_group_admin(ctx)
        ids = msg.get('group_ids')
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ProtocolError('group_ids 값이 올바르지 않습니다.')
        try:
            self.store.org_group_reorder(ids)
        except ValueError as exc:
            self._broadcast_org_groups()          # 실패해도 요청한 사람 화면을 서버 순서로 되돌린다
            raise ProtocolError(str(exc)) from None
        self._broadcast_org_groups()
        self._admin_done(ctx, 'group_reorder', '그룹 순서 변경', group_ids=ids)

    def _h_admin_group_delete(self, ctx: Ctx, msg: dict) -> None:
        group_id = self._group_target(ctx, msg)
        self.store.org_group_delete(group_id)
        self._broadcast_org_groups()
        self._admin_done(ctx, 'group_delete', '그룹 삭제', group_id=group_id)
