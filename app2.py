"""AIVLE School 학습 도우미 (Streamlit) - 4차 미니프로젝트 조별 앱

- 백서 PDF를 올리면 바로 청킹 · 임베딩 · 검색기 준비까지 끝낸다 (버튼 없음).
- 색인은 세션마다 따로 만든다 (st.session_state). 다른 사용자의 업로드가 내 색인을 지우지 않는다.
- 같은 청크의 임베딩은 서버 안에서 공유 캐시로 재사용한다.
- 사전 생성 그래프는 graph_cache.json이 있으면 그것을, 없으면 Neo4j에서 청크 해시로 읽는다.
- API 키 등 비밀 값은 Streamlit Secrets에서만 읽는다.
"""
import hashlib
import json
import os
import tempfile
import time

import streamlit as st

import rag_core

st.set_page_config(page_title='AIVLE 학습 도우미', page_icon='📘', layout='wide')

SECRET_KEYS = ['OPENAI_API_KEY', 'OPENAI_CHAT_MODEL', 'OPENAI_EMBEDDING_MODEL', 'NEO4J_URI',
               'NEO4J_USERNAME', 'NEO4J_PASSWORD', 'NEO4J_DATABASE', 'ADMIN_PASSWORD']
APP_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.path.join(APP_DIR, 'graph_cache.json')
EMBED_CACHE = os.getenv('EMBED_CACHE_DIR') or os.path.join(tempfile.gettempdir(), 'aivle_embed_cache')
MAX_MB = 30
SUGGESTED = [
    'AI 트랙과 DX 트랙은 뭐가 달라요?',
    '비전공자도 따라갈 수 있을까요?',
    '수료하려면 출석을 얼마나 해야 하나요?',
    '훈련장려금은 어떻게 받나요?',
    '빅프로젝트 팀은 어떻게 정해지나요?',
    '6개월 과정인데 잘 할 수 있을까요?',
]
INTENT_LABEL = {'fact': '공식·사실', 'experience': '선배 경험', 'advice': '조언',
                'out_of_scope': '백서 밖 질문', 'chat': '인사·안내'}


# ----------------------------------------------------------------------------
# 설정 · 세션 상태
# ----------------------------------------------------------------------------
def load_settings():
    """Streamlit Secrets → 환경 변수. 키 값은 화면에 출력하지 않는다."""
    try:
        values = {k: st.secrets[k] for k in SECRET_KEYS if k in st.secrets}
    except Exception:                       # secrets.toml이 없을 때 (로컬·Colab 실행)
        values = {}
    for key, value in values.items():
        os.environ[key] = str(value)
    os.environ.setdefault('OPENAI_CHAT_MODEL', 'gpt-6-luna')
    os.environ.setdefault('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-large')
    return bool(os.getenv('OPENAI_API_KEY'))


def init_state():
    defaults = {'messages': [], 'index': None, 'doc_id': None, 'doc_name': None, 'chains': {},
                'uploader_key': 0, 'pending': None, 'admin': False, 'last_trace': None,
                'last_error': None, 'neo4j_search': False}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if 'workspace' not in st.session_state:   # 세션 전용 폴더: 벡터저장소를 세션마다 따로 둔다
        st.session_state.workspace = tempfile.TemporaryDirectory(prefix='aivle-session-')


def reset_document():
    st.session_state.update(index=None, doc_id=None, doc_name=None, chains={}, messages=[], last_trace=None)


# ----------------------------------------------------------------------------
# 문서 준비 (업로드 즉시)
# ----------------------------------------------------------------------------
def prepare_document(uploaded):
    data = uploaded.getvalue()
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    if st.session_state.doc_id == doc_id:          # 같은 파일이면 다시 만들지 않는다
        return
    if len(data) > MAX_MB * 1024 * 1024:
        st.sidebar.error(f'{MAX_MB}MB 이하의 PDF만 올릴 수 있어요.')
        return
    workspace = st.session_state.workspace.name
    with st.sidebar.status('문서를 준비하고 있어요…', expanded=True) as status:
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf', dir=workspace, delete=False) as tmp:
                tmp.write(data)
                pdf_path = tmp.name
            try:
                index = rag_core.build_index(
                    pdf_path, persist_directory=os.path.join(workspace, 'chroma_db'), cache_dir=EMBED_CACHE,
                    graph_path=GRAPH_PATH if os.path.exists(GRAPH_PATH) else None, use_neo4j=True,
                    neo4j_search=st.session_state.neo4j_search, progress=status.write)
            finally:
                os.remove(pdf_path)
        except Exception as exc:
            st.session_state.last_error = f'문서 준비: {type(exc).__name__}'
            reset_document()
            status.update(label='문서를 준비하지 못했어요', state='error')
            st.sidebar.error('PDF에서 글자를 읽을 수 있는지, API 설정이 맞는지 확인해 주세요.')
            return
        status.update(label=f"준비 완료 · {index.timing['total']:.0f}초", state='complete', expanded=False)
    index.source = uploaded.name
    st.session_state.update(index=index, doc_id=doc_id, doc_name=uploaded.name, chains={},
                            messages=[], last_trace=None)


def get_chain(use_graph, agentic):
    key = (use_graph, agentic)
    chains = st.session_state.chains
    if key not in chains:
        chains[key] = rag_core.make_chain(st.session_state.index, structured=True, with_sources=True,
                                          use_graph=use_graph, agentic=agentic)
    return chains[key]


def set_neo4j_search(enabled):
    """그래프 검색을 Neo4j 직접 조회로 할지(느리지만 실제 DB 조회), 메모리에서 할지 전환한다."""
    index = st.session_state.index
    graph = index.graph if index else None
    st.session_state.neo4j_search = enabled
    if graph is None:
        return
    if enabled and graph.store is None:
        try:
            store = rag_core.Neo4jStore.from_env()
            if store is not None and store.has_document(graph.doc_hash):
                graph.store = store
            else:
                st.warning('Neo4j에 이 문서의 그래프가 없어 메모리 검색을 계속 써요.')
        except Exception as exc:
            st.warning(f'Neo4j에 연결하지 못했어요 ({type(exc).__name__}).')
    elif not enabled and graph.store is not None:
        graph.store.close()
        graph.store = None


# ----------------------------------------------------------------------------
# 화면 그리기
# ----------------------------------------------------------------------------
def render_sources(meta):
    sources = meta.get('sources') or []
    cited = set(meta.get('cited_ids') or [])
    shown = [s for s in sources if s['chunk_id'] in cited] or sources
    if not shown:
        return
    label = f'답변 근거 {len(shown)}개' + (' (인용된 근거)' if cited else '')
    with st.expander(label):
        for s in shown:
            pages = f"p.{s['page_start']}" + (f"-{s['page_end']}" if s['page_end'] != s['page_start'] else '')
            st.markdown(f"**{pages} · {s['part']}** — {s['title']}")
            st.caption(s['section_path'])
            st.text(s.get('text', '')[:400])


def render_meta(meta):
    if not meta:
        return
    parts = [INTENT_LABEL.get(meta.get('intent'), '답변'), f"{meta.get('elapsed', 0):.1f}초"]
    if meta.get('graph'):
        parts.append('그래프 검색')
    if meta.get('agentic'):
        parts.append('Agentic' + (' · 재검색' if meta.get('retried') else ''))
    st.caption(' · '.join(parts))
    if meta.get('unverified'):
        st.caption('⚠️ 일부 문장은 백서 근거를 확인하지 못했어요. "(근거 확인 필요)" 표시를 참고해 주세요.')
    steps = meta.get('steps')
    if steps:
        with st.expander('Agentic 단계 보기'):
            for s in steps:
                detail = {k: v for k, v in s.items() if k not in ('node', 'sec') and v not in ('', None)}
                st.markdown(f"**{s['node']}** · {s.get('sec', 0):.1f}초")
                if detail:
                    st.caption(json.dumps(detail, ensure_ascii=False))
    render_sources(meta)


def ask(question, use_graph, agentic):
    history = [{'role': m['role'], 'content': m['content']} for m in st.session_state.messages]
    st.session_state.messages.append({'role': 'user', 'content': question})
    with st.chat_message('user'):
        st.markdown(question)
    with st.chat_message('assistant'):
        try:
            start = time.time()
            with st.spinner('백서에서 근거를 찾고 답변을 쓰고 있어요…'):
                result = get_chain(use_graph, agentic).invoke({'input': question, 'history': history})
            agent = result['trace'].get('agent') or {}
            meta = {'intent': result.get('intent'), 'elapsed': time.time() - start, 'graph': use_graph,
                    'agentic': agentic, 'retried': agent.get('retried', False), 'steps': agent.get('steps'),
                    'sources': result['sources'], 'cited_ids': result.get('cited_ids'),
                    'unverified': any(i['type'] in ('unknown_citation', 'missing_citation')
                                      for i in result.get('issues', []))}
            st.markdown(result['answer'])
            render_meta(meta)
            st.session_state.messages.append({'role': 'assistant', 'content': result['answer'], 'meta': meta})
            st.session_state.last_trace = {'question': question, 'trace': result['trace'],
                                           'issues': result.get('issues', []), 'elapsed': meta['elapsed']}
        except Exception as exc:
            st.session_state.last_error = f'답변 생성: {type(exc).__name__}'
            st.error('답변을 만들지 못했어요. 잠시 후 다시 질문해 주세요.')
            st.session_state.messages.pop()            # 실패한 질문은 대화 맥락에 남기지 않는다


def admin_panel(configured):
    with st.expander('🔧 관리자'):
        password = os.getenv('ADMIN_PASSWORD')
        if not password:
            st.caption('ADMIN_PASSWORD가 설정되지 않아 관리자 영역을 쓸 수 없어요.')
            return
        if not st.session_state.admin:
            entered = st.text_input('관리자 비밀번호', type='password')
            if entered and entered == password:
                st.session_state.admin = True
                st.rerun()
            elif entered:
                st.error('비밀번호가 맞지 않아요.')
            return
        st.markdown('**설정**')
        st.json({'API 키': '설정됨' if configured else '없음', '답변 모델': os.getenv('OPENAI_CHAT_MODEL'),
                 '임베딩 모델': os.getenv('OPENAI_EMBEDDING_MODEL'), 'Neo4j': bool(os.getenv('NEO4J_URI')),
                 '그래프 JSON': os.path.exists(GRAPH_PATH),
                 '임베딩 캐시 파일': len(os.listdir(EMBED_CACHE)) if os.path.isdir(EMBED_CACHE) else 0})
        index = st.session_state.index
        if index is not None:
            parts = {}
            for d in index.docs:
                parts[d.metadata['part']] = parts.get(d.metadata['part'], 0) + 1
            st.markdown('**문서 색인**')
            st.json({'문서': st.session_state.doc_name, '쪽수': index.pages, '청크': len(index.docs),
                     '근거 종류별 청크': parts, '새로 임베딩한 청크': index.new_embeddings,
                     '준비 시간(초)': {k: round(v, 1) for k, v in index.timing.items()},
                     '그래프': index.graph_info})
            neo4j_on = st.toggle('그래프 검색에 Neo4j 직접 조회', value=st.session_state.neo4j_search,
                                 disabled=index.graph is None,
                                 help='끄면 Neo4j에서 읽어 온 같은 그래프로 메모리에서 찾아 더 빨라요.')
            if neo4j_on != st.session_state.neo4j_search:
                set_neo4j_search(neo4j_on)
        if st.session_state.last_trace:
            last = st.session_state.last_trace
            trace = last['trace']
            st.markdown('**마지막 답변 추적**')
            st.json({'질문': last['question'], '전체(초)': round(last['elapsed'], 2),
                     '단계별(초)': trace.get('timing'),
                     '검색 결과 수': {k: len(trace.get(k, [])) for k in ('vector', 'bm25', 'graph')},
                     '찾은 개념': trace.get('graph_concepts'),
                     '검증 기록': [i['type'] for i in last['issues']]})
        if st.session_state.last_error:
            st.caption(f'최근 오류: {st.session_state.last_error}')
        if st.session_state.messages:
            export = {'document': st.session_state.doc_name,
                      'messages': [{k: v for k, v in m.items() if k != 'meta'} | (
                          {'sources': [{'page': s['page_start'], 'title': s['title']}
                                       for s in (m.get('meta') or {}).get('sources', [])]} if m.get('meta') else {})
                                   for m in st.session_state.messages]}
            st.download_button('대화와 출처 내려받기', json.dumps(export, ensure_ascii=False, indent=2),
                               file_name='aivle_chat.json', mime='application/json')
        if st.button('관리자 로그아웃'):
            st.session_state.admin = False
            st.rerun()


# ----------------------------------------------------------------------------
# 앱
# ----------------------------------------------------------------------------
configured = load_settings()
init_state()

with st.sidebar:
    st.header('📄 백서')
    uploaded = st.file_uploader('PDF를 올리면 바로 준비해요', type=['pdf'],
                                key=f"uploader_{st.session_state.uploader_key}",
                                disabled=not configured, help=f'{MAX_MB}MB 이하, 글자를 읽을 수 있는 PDF')
if not configured:
    st.sidebar.error('관리자가 OPENAI_API_KEY를 설정해야 사용할 수 있어요.')
elif uploaded is not None:
    prepare_document(uploaded)
elif st.session_state.index is not None:
    reset_document()                                   # 업로드 창에서 파일을 지우면 문서도 해제

index = st.session_state.index
ready = index is not None
with st.sidebar:
    use_graph = agentic = False
    if ready:
        st.success(f'사용 중: {st.session_state.doc_name}')
        st.caption(f'{index.pages}쪽 · 청크 {len(index.docs)}개')
        graph_ok = index.graph is not None
        if graph_ok:
            st.caption(f"사전 그래프 사용 가능 · 개념 {index.graph_info['concepts']}개")
        else:
            st.caption('이 문서용 사전 그래프가 없어 기본 검색(벡터 + BM25)으로 답해요.')
        st.subheader('답변 방식')
        use_graph = st.toggle('그래프 검색', value=graph_ok, disabled=not graph_ok,
                              help='질문 속 개념과 연결된 절을 함께 찾아요.')
        agentic = st.toggle('Agentic (근거 평가·재검색)', value=False,
                            help='근거가 부족하면 다시 찾아요. 질문마다 몇 초 더 걸려요.')
        col1, col2 = st.columns(2)
        if col1.button('대화 삭제', disabled=not st.session_state.messages, use_container_width=True):
            st.session_state.messages = []
            st.session_state.last_trace = None
            st.rerun()
        if col2.button('문서 변경', use_container_width=True):
            reset_document()
            st.session_state.uploader_key += 1          # 업로드 창을 비운다
            st.rerun()
    admin_panel(configured)
    st.caption('올린 문서와 대화는 이 브라우저 세션에서만 쓰고 저장하지 않아요.')

st.title('📘 AIVLE School 학습 도우미')
st.caption('에이블스쿨 백서를 근거로 답하고, 공부와 진로 고민에는 따뜻한 조언도 함께 드려요.')

if not ready:
    st.info('왼쪽에서 백서 PDF를 올리면 바로 질문할 수 있어요. 처음 준비에는 1~2분 걸릴 수 있어요.')

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])
        if message['role'] == 'assistant':
            render_meta(message.get('meta'))

if ready:
    with st.expander('💡 추천 질문', expanded=not st.session_state.messages):
        cols = st.columns(3)
        for i, question in enumerate(SUGGESTED):
            if cols[i % 3].button(question, key=f'suggest_{i}', use_container_width=True):
                st.session_state.pending = question
                st.rerun()

prompt = st.chat_input('에이블스쿨에 대해 궁금한 점을 물어보세요' if ready else '먼저 백서 PDF를 올려 주세요',
                       disabled=not ready, max_chars=1000)
if ready and st.session_state.pending:
    prompt, st.session_state.pending = st.session_state.pending, None
if ready and prompt:
    ask(prompt, use_graph, agentic)
