# -*- coding: utf-8 -*-
"""
채팅방 대화 요약 (카카오톡 v26.6.0 '대화 요약' 대응, 명세 §9.1)

- summarize(messages, name_of, llm=None) -> (summary_text, source)
  source: 'claude' | 'extractive'
- llm 은 anthropic.Anthropic 클라이언트(또는 None). 실패하면 추출형 요약으로 대체한다.
- 추출형 요약: 참여자·메시지 수, 주요 키워드, 결정/요청/일정/질문 문장, 공유된 파일·링크·일정·투표.
"""

import os
import re
from collections import Counter

from .rag import DEFAULT_MODEL, FATAL_LLM_ERRORS  # 모델 이름·치명 오류는 RAG 와 한 곳에서
MAX_INPUT_MESSAGES = 300
MAX_LINE = 90
NOTHING_TO_SUMMARIZE = '요약할 대화가 아직 없습니다. 메시지가 쌓인 뒤 다시 요약해 보세요.'

SYSTEM_PROMPT = (
    '당신은 회사 메신저 KCS Talk의 대화 요약 도우미입니다. 주어진 채팅 기록만 근거로 한국어로 요약하세요. '
    '형식: 첫 줄에 한 문장 요약, 이어서 "• " 로 시작하는 핵심 항목 3~6개(결정된 사항, 요청·할 일과 담당자, '
    '일정·마감, 미해결 질문). 기록에 없는 내용은 추측하지 말고, 사람 이름은 기록에 나온 그대로 쓰세요.'
)

# 문장 유형별 단서 (추출형 요약)
CUES = (
    ('결정', ('결정', '확정', '하기로', '정했', '합의', '승인', '진행합니다', '진행하겠')),
    ('요청', ('부탁', '요청', '해주세요', '해 주세요', '주실 수', '확인 바랍', '검토 부탁', '공유해')),
    ('일정', ('회의', '미팅', '마감', '까지', '일정', '오전', '오후', '내일', '다음 주', '월요일', '화요일',
             '수요일', '목요일', '금요일')),
)
# 메시지 종류 → 요약에 쓰는 이름 (전사·공유 집계 공용)
KIND_LABELS = {'image': '사진', 'file': '파일', 'voice': '음성메시지', 'contact': '연락처', 'poll': '투표',
               'event': '일정', 'emoticon': '이모티콘', 'call': '통화', 'board': '게시판', 'ladder': '사다리 게임',
               'link': '링크'}

STOPWORDS = {'그리고', '그런데', '근데', '저는', '제가', '우리', '이거', '그거', '저거', '네', '아', '음', '혹시',
             '오늘', '지금', '정말', '진짜', '좀', '더', '수', '것', '등', '및', '이번', '그럼', '감사합니다',
             '알겠습니다', '확인했습니다', '넵', '네네', 'ㅋㅋ', 'ㅎㅎ'}


def _clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def _short(text, n=MAX_LINE):
    text = _clean(text)
    return text if len(text) <= n else text[:n - 1] + '…'


def _usable(messages):
    """요약 대상: 삭제·시스템·비밀 메시지 제외"""
    return [m for m in messages
            if not m.get('deleted') and m.get('kind') not in ('system', 'secret') and m.get('sender')]


def _line_of(m, name_of):
    kind = m.get('kind')
    who = name_of(m.get('sender'))
    if kind == 'text':
        return f'{who}: {_clean(m.get("content"))}'
    detail = m.get('content') or ''
    if kind == 'contact':
        detail = name_of(detail)
    elif kind == 'poll':
        detail = (m.get('poll') or {}).get('question', '')
    return f'{who}: [{KIND_LABELS.get(kind, kind)}] {_clean(detail)}'.rstrip()


def keywords(messages, n=5):
    counts = Counter()
    for m in messages:
        if m.get('kind') != 'text':
            continue
        for word in re.findall(r'[가-힣A-Za-z0-9]{2,}', m.get('content') or ''):
            w = re.sub(r'(은|는|이|가|을|를|에|의|로|으로|와|과|도|만|에서|에게|께서|입니다|합니다|해요|이에요|예요)$', '', word)
            if len(w) >= 2 and w not in STOPWORDS and not w.isdigit():
                counts[w] += 1
    return [w for w, c in counts.most_common(n) if c >= 2] or [w for w, _ in counts.most_common(min(n, 3))]


def extractive_summary(messages, name_of):
    msgs = _usable(messages)
    if not msgs:
        return NOTHING_TO_SUMMARIZE
    speakers = Counter(m['sender'] for m in msgs)
    top = ', '.join(f'{name_of(u)}({c})' for u, c in speakers.most_common(4))
    lines = [f'메시지 {len(msgs)}개 · 참여 {len(speakers)}명 — {top}']
    kws = keywords(msgs)
    if kws:
        lines.append('주요 키워드: ' + ', '.join(kws))
    picked = set()
    for label, cues in CUES:
        found = [m for m in msgs if m.get('kind') == 'text' and m['msg_id'] not in picked
                 and any(c in (m.get('content') or '') for c in cues)]
        for m in found[-2:]:          # 최근 것 위주
            picked.add(m['msg_id'])
            lines.append(f'• [{label}] {name_of(m["sender"])}: {_short(m.get("content"))}')
    questions = [m for m in msgs if m.get('kind') == 'text' and m['msg_id'] not in picked
                 and (m.get('content') or '').rstrip().endswith('?')]
    for m in questions[-2:]:
        lines.append(f'• [질문] {name_of(m["sender"])}: {_short(m.get("content"))}')
    shared = Counter(m.get('kind') for m in msgs if m.get('kind') in ('image', 'file', 'voice', 'event', 'poll'))
    links = sum(1 for m in msgs if m.get('kind') == 'text' and 'http' in (m.get('content') or ''))
    if links:
        shared['link'] = links
    if shared:
        lines.append('공유: ' + ' · '.join(f'{KIND_LABELS[k]} {v}' for k, v in shared.items()))
    if len(lines) <= 2 + bool(kws):
        last = [m for m in msgs if m.get('kind') == 'text'][-3:]
        for m in last:
            lines.append(f'• {name_of(m["sender"])}: {_short(m.get("content"))}')
    return '\n'.join(lines)


def claude_summary(client, messages, name_of):
    msgs = _usable(messages)[-MAX_INPUT_MESSAGES:]
    if not msgs:
        return NOTHING_TO_SUMMARIZE
    transcript = '\n'.join(_line_of(m, name_of) for m in msgs)
    response = client.messages.create(
        model=os.environ.get('KCS_RAG_MODEL', DEFAULT_MODEL),
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': f'<chat>\n{transcript}\n</chat>\n\n위 대화를 요약해 주세요.'}],
        timeout=60.0,
    )
    if response.stop_reason == 'refusal':
        raise RuntimeError('refusal')
    text = ''.join(b.text for b in response.content if b.type == 'text').strip()
    if not text:
        raise RuntimeError('empty')
    return text


def summarize(messages, name_of, llm=None, on_fatal=None):
    """(요약, 'claude'|'extractive'). 인증·권한 오류면 on_fatal() 로 알려 이후 호출을 막게 한다"""
    if llm is not None:
        try:
            return claude_summary(llm, messages, name_of), 'claude'
        except Exception as exc:  # 네트워크·인증·거절 → 추출형
            if on_fatal is not None and type(exc).__name__ in FATAL_LLM_ERRORS:
                on_fatal()
            print(f'[요약] Claude 호출 실패, 추출형 요약 사용: {exc}')
    return extractive_summary(messages, name_of), 'extractive'
