"""AIVLE School 학습 도우미 (Streamlit) - 4차 미니프로젝트 조별 앱

- 백서 PDF를 올리면 바로 청킹 · 임베딩 · 검색기 준비까지 끝낸다 (버튼 없음).
- 색인은 세션마다 따로 만든다 (st.session_state). 다른 사용자의 업로드가 내 색인을 지우지 않는다.
- 사전 생성 그래프는 graph_cache.json이 있으면 그것을, 없으면 Neo4j에서 청크 해시로 읽는다.
- 화면 디자인은 팀 UI 시안(유령 캐릭터·카드형 사이드바·주제 카드)을 바탕으로 했다.
- 답변 방식·추천 질문·화면 문구는 aivle_config.py(사람이 정하는 설정)에서 가져온다.
- 아이콘은 Streamlit 기본 Material 아이콘, 로고·캐릭터는 assets/ 폴더의 이미지 파일을 쓴다.
- API 키 등 비밀 값은 Streamlit Secrets에서만 읽는다.
"""
import base64
import functools
import hashlib
import html
import importlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

import aivle_config
import rag_core


def fresh(module):
    """배포가 갱신돼도 서버에는 예전 모듈이 남아 있을 수 있다 → 파일이 바뀌었으면 다시 읽는다."""
    stamp = os.path.getmtime(module.__file__)
    if getattr(module, '_loaded_mtime', None) != stamp:
        module = importlib.reload(module)
        module._loaded_mtime = stamp
    return module


aivle_config = fresh(aivle_config)      # 설정을 먼저 읽어야 rag_core가 새 설정을 가져간다
rag_core = fresh(rag_core)
cfg = aivle_config
logger = logging.getLogger('aivle_app')

st.set_page_config(page_title=cfg.APP_TITLE, page_icon='📘', layout='wide', initial_sidebar_state='auto')

SECRET_KEYS = ['OPENAI_API_KEY', 'OPENAI_CHAT_MODEL', 'OPENAI_EMBEDDING_MODEL', 'NEO4J_URI',
               'NEO4J_USERNAME', 'NEO4J_PASSWORD', 'NEO4J_DATABASE', 'ADMIN_PASSWORD']
APP_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.path.join(APP_DIR, 'graph_cache.json')
EMBED_CACHE = os.getenv('EMBED_CACHE_DIR') or os.path.join(tempfile.gettempdir(), 'aivle_embed_cache')
MAX_MB = cfg.MAX_UPLOAD_MB
AVATAR = ':material/chat:'
ASSET_DIR = os.path.join(APP_DIR, 'assets')
TOPICS, READY_QUESTIONS, CHAT_CHIPS = cfg.TOPICS, cfg.READY_QUESTIONS, cfg.CHAT_CHIPS
TOPIC_ICON = {key: icon for key, icon, _title, _questions in TOPICS}
INTENT_LABEL = {'fact': '공식·사실', 'experience': '선배 경험', 'advice': '조언',
                'out_of_scope': '백서 밖 질문', 'chat': '인사·안내'}
PART_BADGE = {'선배·교육생 경험': ('선배 경험', 'exp'), '공식 안내': ('공식 안내', 'official'),
              '공식 원문(부록)': ('공식 원문', 'appendix'), '문서 안내': ('문서 안내', 'guide')}


# ----------------------------------------------------------------------------
# 아이콘 · 로고 · 스타일
# ----------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def asset_uri(filename, mime):
    """assets/ 폴더의 이미지를 data URI로 읽는다. 파일이 없으면 None (글자로 대신 표시)."""
    path = os.path.join(ASSET_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return f'data:{mime};base64,' + base64.b64encode(f.read()).decode('ascii')


def mi(name, size=20, color='#079e9b'):
    """Material 아이콘 (Streamlit에 들어 있는 Material Symbols 글꼴)."""
    return f'<span class="mi" style="font-size:{size}px;color:{color}" aria-hidden="true">{name}</span>'


def ui_wordmark():
    logo = asset_uri('aivle_logo.png', 'image/png')
    if logo is None:
        return '<span class="brand-text">AIVLE</span>'
    return (f'<span class="brand-wordmark" role="img" aria-label="AIVLE" '
            f'style="-webkit-mask-image:url({logo});mask-image:url({logo})"></span>')


def ui_ghost():
    ghost = asset_uri('ghost.webp', 'image/webp')
    return f'<img class="ghost-image" src="{ghost}" alt="돋보기를 든 학습 도우미 유령">' if ghost else ''


def ui_heading(icon, title):
    st.html(f'<div class="panel-title">{mi(icon, 21, "#166a70")}<span>{title}</span></div>')


CSS = '''
:root{--teal:#079e9b;--teal-dark:#087f80;--ink:#10202b;--muted:#667a87;--line:#e1e9ed;--mint:#e4f7f4;}
[data-testid="stAppViewContainer"]{background:#fcfefe;color:var(--ink);}
[data-testid="stHeader"]{background:transparent;height:2.4rem;}
[data-testid="stMain"]{background:radial-gradient(ellipse at 100% 0%,#dff5f3 0,transparent 29%),#fcfefe;scroll-padding-bottom:130px;}
[data-testid="stMainBlockContainer"]{max-width:1280px!important;padding:2.6rem 2rem 140px!important;}
p,label,button,li{font-family:Inter,"Pretendard","Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif;}
.mi{font-family:"Material Symbols Rounded";font-weight:400;font-style:normal;line-height:1;letter-spacing:normal;text-transform:none;display:inline-block;white-space:nowrap;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;flex-shrink:0;}
.brand-text{font-weight:800;letter-spacing:.06em;color:var(--teal);font-size:30px;}
.sidebar-logo .brand-text{color:#fff;font-size:34px;}
/* 사이드바: 카드형 패널 */
[data-testid="stSidebar"]{background:#f3f7f8;border-right:1px solid #e8eef0;min-width:310px;max-width:350px;}
[data-testid="stSidebarContent"]{padding:0!important;overflow-y:auto!important;}
[data-testid="stSidebarHeader"]{height:28px!important;min-height:28px!important;padding:0 12px!important;}
[data-testid="stSidebarUserContent"]{padding:0 16px 24px!important;}
.st-key-sidebar_stack{display:flex!important;flex-direction:column!important;gap:16px!important;}
[data-testid="stSidebar"] :is(.st-key-document_panel,.st-key-answer_settings,.st-key-document_status,.st-key-management_panel,.st-key-admin_panel){border:1px solid #e5ecef!important;border-radius:18px!important;background:#fff;box-shadow:0 3px 7px #173d490c;padding:18px!important;gap:12px!important;}
.sidebar-logo{height:72px;display:flex;align-items:center;padding:0 22px;border-radius:14px;background:linear-gradient(115deg,#16aaa6,#079a99);box-shadow:inset 0 1px 0 #ffffff30;}
.brand-wordmark{display:inline-block;aspect-ratio:504/114;background:var(--teal);-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;-webkit-mask-size:contain;mask-size:contain;-webkit-mask-position:center;mask-position:center;}
.sidebar-logo .brand-wordmark{width:196px;background:#fff;}
.panel-title{display:flex;align-items:center;gap:10px;min-height:27px;font-size:17px;font-weight:750;color:#17303a;line-height:1.5;}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p{font-size:13px!important;font-weight:550;}
[data-testid="stSidebar"] [data-testid="stExpander"]{background:#fafcfd;border:1px solid #e1e9ed;border-radius:10px;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary{font-size:12px;min-height:32px;padding:6px 9px!important;}
[data-testid="stSidebar"] [data-testid="stFileUploader"] section{padding:8px!important;border-radius:10px;background:#edf8f6;}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"]{display:none;}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button{width:100%;font-size:12px;min-height:35px;}
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"]{display:none!important;}
[data-testid="stFileUploaderDropzone"] button span[data-has-shortcut]{display:inline-flex;align-items:center;gap:6px;}
[data-testid="stFileUploaderDropzone"] button span[data-has-shortcut]::after{content:"PDF 파일 선택";font-size:12.5px;font-weight:600;}
.stButton button,.stDownloadButton button{border:1px solid #d8e2e7;border-radius:11px;min-height:40px;font-weight:600;background:#fff;color:#20353e;}
.stButton button:hover,.stDownloadButton button:hover{border-color:#5cbfb8;color:#078482;background:#f4fbfa;}
.stButton button:disabled{opacity:.6;}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label{width:100%;}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label>div:first-child{background-color:#cfdde1;}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked)>div:first-child{background-color:var(--teal)!important;}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:disabled){opacity:.55;}
[class*="st-key-followups_"] .stButton button>div{justify-content:flex-start!important;}
.st-key-ready_questions .stButton button [data-testid="stIconMaterial"],.st-key-starter_area .stButton button [data-testid="stIconMaterial"]{color:var(--teal);font-size:22px;}
[class*="st-key-followups_"] .stButton button [data-testid="stIconMaterial"]{color:var(--teal);}
.doc-card{display:flex;align-items:center;gap:10px;border:1px solid #e3e9ed;background:#fafcfd;border-radius:12px;padding:12px;}
.doc-name{font-size:12px;font-weight:650;line-height:1.55;overflow-wrap:anywhere;}.doc-meta{font-size:11px;color:#80909b;margin-top:3px;}.doc-check{margin-left:auto;}
.upload-copy{display:flex;align-items:center;gap:10px;font-size:12px;line-height:1.7;color:#617985;}
.status-card{display:flex;gap:10px;padding:12px;border-radius:11px;background:#e5f7f3;}.status-card.waiting{background:#f0f5f7;}
.status-dot{background:#249b72;border-radius:50%;width:19px;height:19px;display:flex;align-items:center;justify-content:center;flex:0 0 19px;margin-top:2px;}.waiting .status-dot{background:#8ba0aa;}
.status-dot .mi{color:#fff!important;}
.status-title{font-size:13px;font-weight:700;margin-bottom:4px;}.status-copy{font-size:11px;line-height:1.7;color:#5b707c;overflow-wrap:anywhere;}
.ui-api-note{font-size:11px;line-height:1.7;color:#856b2e;background:#fff8e9;border:1px solid #f0e5cc;border-radius:10px;padding:11px;}
.ui-note{font-size:10px!important;line-height:1.65!important;color:#8a9aa4;margin:0 2px!important;}
.st-key-management_panel .stButton button{display:flex;align-items:center;justify-content:center;gap:6px;padding:8px 7px!important;}
.st-key-management_panel .stButton button p{font-size:11px!important;white-space:nowrap!important;}
.st-key-export_chat button{font-size:11px!important;color:#5f7682;}
/* 첫 화면: 소개 배너 + 주제 카드 */
.intro-hero{position:relative;min-height:208px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:16px 20px 26px;margin:0 -4px 22px;border-bottom:1px solid #e8f0f1;background:linear-gradient(115deg,#f5fbfa,#fcfefe);border-radius:17px 17px 0 0;}
.intro-copy{min-width:0;max-width:75%;}.intro-eyebrow{display:inline-block;padding:3px 11px;border-radius:20px;background:#8bd6d0;color:#fff;font-size:12px;margin-bottom:12px;}
.intro-title{font-size:clamp(32px,3vw,43px);font-weight:800;line-height:1.3;letter-spacing:-.05em;color:#11212c;margin:0 0 13px;}
.intro-description{font-size:16px;line-height:1.8;color:#617681;word-break:keep-all;margin:0;}
.intro-art{position:relative;isolation:isolate;width:150px;flex:0 0 150px;margin-right:12px;}
.ghost-image{display:block;width:100%;height:auto;filter:drop-shadow(0 12px 12px rgba(24,49,53,.28));position:relative;z-index:1;}
.intro-art::after{content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);width:92px;height:16px;border-radius:50%;background:radial-gradient(ellipse,rgba(31,67,69,.23) 0%,rgba(31,67,69,.11) 45%,rgba(31,67,69,0) 76%);filter:blur(3px);z-index:0;}
.landing-section-title{display:flex;align-items:center;gap:9px;font-size:21px;font-weight:750;color:#19313b;letter-spacing:-.03em;margin:0 0 18px;}
.st-key-intro_cards [class*="st-key-topic_card_"]{background:linear-gradient(160deg,#fff,#f8fbfc);border:1px solid #e3ebef!important;border-radius:17px!important;padding:18px!important;gap:10px!important;box-shadow:0 3px 6px #183e4908;min-height:250px;}
.topic-card-icon{width:38px;height:38px;border-radius:11px;background:#e8f7f4;display:flex;align-items:center;justify-content:center;margin-bottom:13px;}
.topic-card-name{font-size:18px;font-weight:750;color:#17313c;letter-spacing:-.03em;}
.st-key-intro_cards .stButton button{border:0!important;box-shadow:none!important;background:transparent!important;min-height:37px;padding:8px 0!important;justify-content:flex-start;width:100%;}
.st-key-intro_cards .stButton button>div{justify-content:flex-start!important;text-align:left;}
.st-key-intro_cards .stButton button p{font-size:13px!important;font-weight:450!important;color:#405c6a;word-break:keep-all;text-align:left;}
.st-key-intro_cards .stButton button:hover{background:#eaf7f3!important;}
.st-key-intro_cards .stButton button:disabled{opacity:1!important;cursor:not-allowed;}.st-key-intro_cards .stButton button:disabled p{color:#7d939e!important;}
.landing-note{font-size:12px;line-height:1.7;color:#7d939e;margin:18px 0 0;display:flex;gap:8px;align-items:center;}
.ready-header{padding:7px 0 26px;}.ready-title{font-size:32px;font-weight:800;letter-spacing:-.04em;color:#11212c;margin:0 0 9px;}.ready-description{font-size:15px;line-height:1.7;color:#6e818c;margin:0;word-break:keep-all;}
.st-key-ready_questions{gap:14px!important;}
.st-key-ready_questions .stButton button{display:flex;align-items:center;justify-content:flex-start;gap:12px;min-height:74px;padding:16px!important;border-radius:15px;border:1px solid #e2ebee;background:#fbfefe;box-shadow:0 3px 6px #173d4908;text-align:left;}
.st-key-ready_questions .stButton button>div{flex:1;justify-content:flex-start;text-align:left;}
.st-key-ready_questions .stButton button p{font-size:14px!important;font-weight:550;word-break:keep-all;color:#35545f;}
.home-header{text-align:center;padding:4px 0 14px;}
.home-brand{display:flex;align-items:center;justify-content:center;gap:13px;flex-wrap:wrap;}
.home-brand .brand-wordmark{width:clamp(170px,15vw,230px);}
.home-title{font-size:clamp(28px,2.6vw,40px);font-weight:800;letter-spacing:-.055em;color:#11212a;}
.home-subtitle{word-break:keep-all;font-size:15px;color:#536774;margin:10px 0 0;}
.st-key-starter_area{gap:12px!important;margin:8px 0 24px!important;}
.st-key-starter_area .stButton button{display:flex!important;align-items:center!important;justify-content:center!important;gap:9px!important;width:100%;min-height:50px;border:1px solid #d8efed;border-radius:15px;background:linear-gradient(180deg,#fcffff,#edf9f7);}
.st-key-starter_area .stButton button p{font-size:13px!important;font-weight:650!important;color:#17363d!important;word-break:keep-all;}
/* 대화 */
[data-testid="stChatMessage"]{background:transparent;gap:13px;padding:8px 0;margin-bottom:14px;align-items:flex-start;}
[data-testid="stChatMessageContent"]{border:1px solid #e2e9ed;border-radius:17px;background:#fff;padding:20px 23px;box-shadow:0 3px 7px #1c485809;min-width:0;}
[data-testid="stChatMessageContent"] p,[data-testid="stChatMessageContent"] li{font-size:15px;line-height:1.8;color:#263944;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){flex-direction:row-reverse;margin-left:20%;}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"]{background:#e0f6f3;border-color:#e0f6f3;box-shadow:none;flex-grow:0;padding:12px 19px;}
[data-testid="stChatMessageAvatarUser"]{background:#e0f4f0;color:#459d91;}
[data-testid="stChatMessageAvatarCustom"]{width:39px;height:39px;flex:0 0 39px;border-radius:50%;background:#0ca59f;display:flex;align-items:center;justify-content:center;}
[data-testid="stChatMessageAvatarCustom"] *{color:#fff!important;}
[data-testid="stChatMessageContent"] [data-testid="stExpander"]{border:1px solid #e8eff1;border-radius:12px;background:#fcfefe;margin-top:6px;}
[data-testid="stChatMessageContent"] [data-testid="stExpander"] summary{color:#188787;font-size:12.5px;}
.meta-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;}
.pill{font-size:11px;padding:2px 9px;border-radius:999px;background:#f0f5f7;color:#5b707c;border:1px solid #e3eaee;}
.pill.on{background:#e4f7f4;color:#087f80;border-color:#c8ebe6;}
.warn-note{font-size:12px;color:#8a6d1f;background:#fff8e9;border:1px solid #f0e5cc;border-radius:9px;padding:7px 10px;margin-top:8px;}
.source-summary{display:flex;gap:9px;align-items:center;color:#637783;border-top:1px solid #e6edef;padding-top:12px;margin-top:6px;font-size:13px;}
.source-summary strong{color:var(--teal);font-weight:650;}
.src-card{border:1px solid #e3ebef;border-radius:12px;padding:12px 14px;margin:6px 0 10px;background:#fff;}
.src-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.badge{font-size:11px;padding:1px 8px;border-radius:999px;font-weight:650;}
.badge.exp{background:#fff1e0;color:#b4610e;}.badge.official{background:#e4f7f4;color:#087f80;}
.badge.appendix{background:#eef1f3;color:#5f7380;}.badge.guide{background:#f3effa;color:#6a55a0;}
.src-page{font-size:12px;color:#5b707c;font-weight:650;}.src-title{font-size:13px;font-weight:650;color:#1d3540;}
.src-path{font-size:11px;color:#8a9aa4;margin:5px 0 6px;}
.src-text{font-size:12.5px;line-height:1.75;color:#455a64;white-space:pre-wrap;word-break:keep-all;}
.step-row{display:flex;gap:10px;align-items:baseline;font-size:12.5px;color:#36505b;padding:4px 0;border-bottom:1px dashed #e6edef;}
.step-row b{min-width:62px;color:#087f80;}.step-row span.sec{margin-left:auto;color:#8a9aa4;font-size:11px;}
.map-legend{font-size:11px;color:#7d939e;margin-top:4px;}
.followup-title{display:flex;gap:6px;align-items:center;font-size:12.5px;font-weight:650;color:#4d6670;margin:14px 0 2px;}
[class*="st-key-followups_"] .stButton button{min-height:34px;border-radius:999px;background:#f4fbfa;border:1px solid #cfe9e5;justify-content:flex-start;}
[class*="st-key-followups_"] .stButton button p{font-size:12.5px!important;font-weight:550;color:#215a5c;word-break:keep-all;text-align:left;}
[data-testid="stBottom"]{background:transparent;}
[data-testid="stBottomBlockContainer"]{max-width:1280px!important;padding:10px 2rem 22px!important;background:linear-gradient(0deg,#fcfefe 85%,#fcfefe00);}
[data-testid="stChatInput"]{border:1px solid #d8e3e8;border-radius:15px;background:#fff;box-shadow:0 3px 7px #163e4c0c;padding:6px 9px;}
[data-testid="stChatInput"] textarea{font-size:15px;line-height:1.6;color:#243f49;}
[data-testid="stChatInputSubmitButton"]{background:linear-gradient(125deg,#18b6ae,#079e9b);color:#fff;border-radius:11px;width:42px;height:40px;}
[data-testid="stChatInputSubmitButton"] svg{color:#fff;fill:#fff;}
@media(max-width:1150px){[data-testid="stMainBlockContainer"]{padding:2.6rem 1.35rem 135px!important;}
 .intro-title{font-size:33px;}.intro-description{font-size:14px;}.intro-art{width:120px;flex-basis:120px;margin:0;}
 .st-key-intro_cards>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"],.st-key-starter_area>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}
 .st-key-intro_cards [data-testid="stColumn"],.st-key-starter_area [data-testid="stColumn"]{width:100%!important;min-width:0!important;}}
@media(max-width:700px){[data-testid="stMainBlockContainer"]{padding:2.7rem 1rem 135px!important;}
 .intro-hero{min-height:0;padding:12px 9px 22px;}.intro-copy{max-width:100%;}.intro-art{display:none;}.intro-title{font-size:28px;}.intro-description{font-size:13px;}
 .st-key-intro_cards [class*="st-key-topic_card_"]{min-height:0;padding:14px!important;}
 .ready-title{font-size:27px;}.st-key-ready_questions>[data-testid="stLayoutWrapper"]>[data-testid="stHorizontalBlock"]{display:flex!important;flex-direction:column!important;}
 .st-key-ready_questions [data-testid="stColumn"]{width:100%!important;}
 [data-testid="stChatMessageContent"]{padding:14px 15px;}[data-testid="stChatMessageContent"] p,[data-testid="stChatMessageContent"] li{font-size:14px;}
 [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){margin-left:5%;}}
'''


def inject_css():
    st.html('<style>' + CSS + '</style>')


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
    os.environ.setdefault('OPENAI_CHAT_MODEL', cfg.CHAT_MODEL)
    os.environ.setdefault('OPENAI_EMBEDDING_MODEL', cfg.EMBEDDING_MODEL)
    return bool(os.getenv('OPENAI_API_KEY'))


def init_state():
    defaults = {'messages': [], 'index': None, 'doc_id': None, 'doc_name': None, 'doc_size': 0, 'chains': {},
                'uploader_key': 0, 'pending': None, 'admin': False, 'last_trace': None,
                'last_error': None, 'neo4j_search': False}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if 'workspace' not in st.session_state:   # 세션 전용 폴더: 벡터저장소를 세션마다 따로 둔다
        st.session_state.workspace = tempfile.TemporaryDirectory(prefix='aivle-session-')


def reset_document():
    st.session_state.update(index=None, doc_id=None, doc_name=None, doc_size=0, chains={}, messages=[],
                            last_trace=None)


def prepare_document(uploaded):
    """업로드 즉시 색인을 만든다. 같은 파일이면 다시 만들지 않는다."""
    data = uploaded.getvalue()
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    if st.session_state.doc_id == doc_id:
        return
    if len(data) > MAX_MB * 1024 * 1024:
        st.error(f'{MAX_MB}MB 이하의 PDF만 올릴 수 있어요.')
        return
    workspace = st.session_state.workspace.name
    with st.status('문서를 준비하고 있어요…', expanded=True) as status:
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
            logger.exception('문서 준비 실패')         # 원인은 Manage app → 로그에서 확인
            st.session_state.last_error = f'문서 준비: {type(exc).__name__}: {str(exc)[:200]}'
            reset_document()
            status.update(label='문서를 준비하지 못했어요', state='error')
            st.error('PDF에서 글자를 읽을 수 있는지, API 설정이 맞는지 확인해 주세요.')
            return
        status.update(label=f"준비 완료 · {index.timing['total']:.0f}초", state='complete', expanded=False)
    index.source = uploaded.name
    st.session_state.update(index=index, doc_id=doc_id, doc_name=uploaded.name, doc_size=len(data),
                            chains={}, messages=[], last_trace=None)
    st.toast(f"문서 준비 완료 · {index.timing['total']:.0f}초")
    st.rerun()                                         # 사이드바를 '준비 완료' 상태로 다시 그린다


def get_chain(use_graph, agentic):
    key = (use_graph, agentic)
    chains = st.session_state.chains
    if key not in chains:
        chains[key] = rag_core.make_chain(st.session_state.index, structured=True, with_sources=True,
                                          use_graph=use_graph, agentic=agentic)
    return chains[key]


def set_neo4j_search(enabled):
    """그래프 검색을 Neo4j 직접 조회로 할지(실제 DB 조회), 메모리에서 할지(더 빠름) 전환한다."""
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
# 답변 가공: [n] 각주 · 출처 번호 · 개념 지도 · 후속 질문
# ----------------------------------------------------------------------------
def format_answer(result):
    """구조화 답변을 깔끔한 본문과 출처 목록으로 바꾼다. 본문에는 인용 표시를 넣지 않고, 출처는 아래 카드로 보여 준다."""
    by_id = {s['chunk_id']: s for s in result.get('sources', [])}
    facts = result.get('facts')
    if facts is None or (not facts and not result.get('advice') and not result.get('closing')):
        numbered = [{**s, 'number': i} for i, s in enumerate(result.get('sources', []), 1)]
        return result['answer'], numbered, False            # 일반 답변으로 대체된 경우
    order = []
    for fact in facts:
        for cid in fact['citations']:
            if cid in by_id and cid not in order:
                order.append(cid)
    paragraphs = [' '.join(f['text'].strip() for f in facts), ' '.join(result.get('advice', [])),
                  result.get('closing', '')]
    body = '\n\n'.join(p for p in paragraphs if p.strip())
    numbered = [{**by_id[cid], 'number': i} for i, cid in enumerate(order, 1)]
    return body, numbered, True


_TOKENIZE = None


def _tokens(text):
    global _TOKENIZE
    if _TOKENIZE is None:
        _TOKENIZE = rag_core.korean_tokenizer()
    return set(_TOKENIZE(text))


def pick_followups(question, cited_ids, graph, limit=3):
    """인용된 청크의 사전 생성 질문에서 후속 질문을 고른다 (LLM 호출 없음)."""
    asked = [m['content'] for m in st.session_state.messages if m['role'] == 'user'] + [question]
    asked_tokens = [_tokens(q) for q in asked]
    candidates = graph.questions_for(cited_ids, limit=12) if graph is not None else []
    if not candidates:
        candidates = [q for _k, _i, _t, qs in TOPICS for q in qs]
    picked = []
    for q in candidates:
        tokens = _tokens(q)
        overlap = max((len(tokens & a) / max(len(tokens | a), 1) for a in asked_tokens), default=0)
        if overlap < 0.5 and q not in picked:
            picked.append(q)
        if len(picked) >= limit:
            break
    return picked


MAP_NOISE = {'교육생경험', '선배경험', '공식안내', '공식원문', '문서안내', '백서', '선배후기', '공개후기'}
TITLE_PREFIX = re.compile(r'^(PART\s*\d+\.|부록\s*[A-C]\.|[A-C]\d\.|Q\d*\.|\d+(\.\d+)*\.?)\s*')


def title_keys(index):
    """절·장 제목을 개념 키 모양으로 모아 둔다 (개념 지도에서 제목성 개념을 거르는 데 사용)."""
    if getattr(index, '_title_keys', None) is None:
        keys = set()
        for d in index.docs:
            for part in [d.metadata['title']] + d.metadata['section_path'].split(' > '):
                keys.add(rag_core.concept_key(TITLE_PREFIX.sub('', part)))
        index._title_keys = keys
    return index._title_keys


def tidy_concept_map(subgraph, index):
    """질문 개념은 그대로 두고, 이웃 중 제목·문서 표기에서 나온 개념과 문장 조각을 뺀다 (그래프 데이터는 그대로)."""
    titles = title_keys(index)
    keep = [n for n in subgraph['nodes']
            if n['query'] or not (n['key'] in MAP_NOISE or n['key'] in titles or len(n['key']) > 12)]
    kept = {n['key'] for n in keep}
    edges = [e for e in subgraph['edges'] if e['source'] in kept and e['target'] in kept]
    linked = {e['source'] for e in edges} | {e['target'] for e in edges}
    keep = [n for n in keep if n['query'] or n['key'] in linked]
    return {'nodes': keep, 'edges': edges} if len(keep) >= 2 and edges else None


def concept_map_dot(subgraph):
    def label(text):
        return text.replace('\\', '').replace('"', "'")
    lines = ['digraph G {', 'graph [rankdir=LR, bgcolor="transparent", pad=0.2, nodesep=0.3, ranksep=0.45, size="7.5,3.2"];',
             'node [shape=box, style="rounded,filled", fontname="Noto Sans KR", fontsize=10, '
             'color="#cfe6e3", fillcolor="#f4fbfa", fontcolor="#1d3540", margin="0.15,0.07"];',
             'edge [color="#9fbcc0", fontname="Noto Sans KR", fontsize=9, fontcolor="#6b8590", arrowsize=0.6];']
    for n in subgraph['nodes']:
        style = ', fillcolor="#079e9b", fontcolor="white", color="#079e9b"' if n['query'] else ''
        lines.append(f'"{label(n["key"])}" [label="{label(n["name"])}"{style}];')
    for e in subgraph['edges']:
        lines.append(f'"{label(e["source"])}" -> "{label(e["target"])}" [label="{label(" · ".join(e["types"]))}"];')
    lines.append('}')
    return '\n'.join(lines)


# ----------------------------------------------------------------------------
# 화면 그리기
# ----------------------------------------------------------------------------
def esc(text):
    return html.escape(str(text or ''))


def clean_snippet(text, limit=420):
    """출처 미리보기: 마크다운 표는 '칸 · 칸' 줄로 바꾸고 구분선·굵게 표시·줄바꿈 태그를 지운다."""
    lines = []
    for line in str(text or '').splitlines():
        s = line.strip()
        if s.startswith('|'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            if all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
                continue                                   # |---|---| 구분선
            cells = [c for c in cells if c]
            if cells:
                lines.append(' · '.join(cells))
        elif s:
            lines.append(s)
    out = re.sub(r'<br\s*/?>', ' ', '\n'.join(lines)).replace('**', '')
    return out[:limit] + ('…' if len(out) > limit else '')


def page_label(s):
    return f"p.{s['page_start']}" + (f"-{s['page_end']}" if s['page_end'] != s['page_start'] else '')


def render_sources(meta, show_sources):
    numbered = meta.get('numbered') or []
    if not show_sources or not numbered:
        return
    pages = ', '.join(dict.fromkeys(page_label(s) for s in numbered))
    title = '참고한 백서' if meta.get('structured') else '검색한 백서'
    st.html(f'<div class="source-summary">{mi("menu_book", 19)}<span>{title} <strong>{len(numbered)}곳</strong>'
            f' · {esc(pages)}</span></div>')
    with st.expander(f'참고 출처 자세히 보기 · {len(numbered)}개'):
        for s in numbered:
            badge, cls = PART_BADGE.get(s.get('part'), ('백서', 'guide'))
            st.html(f'''<div class="src-card"><div class="src-head">
                <span class="badge {cls}">{badge}</span><span class="src-page">{page_label(s)}</span>
                <span class="src-title">{esc(s.get("title"))}</span></div>
                <div class="src-path">{esc(s.get("section_path"))}</div>
                <div class="src-text">{esc(clean_snippet(s.get("text")))}</div></div>''')


def render_steps(steps):
    with st.expander('Agentic 단계 보기'):
        rows = []
        for s in steps:
            detail = []
            if s['node'] == '분류':
                detail.append('잡담' if s.get('route') == 'chat' else f"검색어: {s.get('search_query', '')}")
            elif s['node'] in ('검색', '재검색'):
                detail.append(f"'{s.get('query', '')}'")
                detail.append(f"+{s['added']}개" if 'added' in s else f"{s.get('found', 0)}개")
            elif s['node'] == '근거 평가':
                detail.append('근거 충분' if s.get('sufficient') else f"부족: {s.get('missing', '')}")
            elif s['node'] == '생성':
                detail.append(f"근거 {s.get('docs', 0)}개로 답변")
            rows.append(f'<div class="step-row"><b>{esc(s["node"])}</b><span>{esc(" · ".join(detail))}</span>'
                        f'<span class="sec">{s.get("sec", 0):.1f}초</span></div>')
        st.html(''.join(rows))


def save_feedback(message_id):
    value = st.session_state.get(f'fb_{message_id}')
    for m in st.session_state.messages:
        if m.get('id') == message_id:
            m['feedback'] = {0: 'down', 1: 'up'}.get(value)


def render_assistant_extras(message, is_last, show_sources):
    meta = message.get('meta') or {}
    pills = [INTENT_LABEL.get(meta.get('intent'), '답변'), f"{meta.get('elapsed', 0):.1f}초"]
    html_pills = ''.join(f'<span class="pill">{esc(p)}</span>' for p in pills)
    if meta.get('graph'):
        html_pills += '<span class="pill on">그래프 검색</span>'
    if meta.get('agentic'):
        html_pills += '<span class="pill on">Agentic' + (' · 재검색' if meta.get('retried') else '') + '</span>'
    st.html(f'<div class="meta-row">{html_pills}</div>')
    if meta.get('unverified'):
        st.html(f'<div class="warn-note">{esc(cfg.LOW_EVIDENCE_NOTE)}</div>')
    render_sources(meta, show_sources)
    if meta.get('concept_map') and meta['concept_map'].get('nodes'):
        with st.expander('개념 지도 · 질문과 연결된 개념'):
            st.graphviz_chart(concept_map_dot(meta['concept_map']), width='content')
            st.html('<div class="map-legend">진한 칸: 질문에서 찾은 개념 · 연한 칸: 포함·선행·요구 같은 관계로 이어진 개념</div>')
    if meta.get('steps'):
        render_steps(meta['steps'])
    st.feedback('thumbs', key=f"fb_{message['id']}", on_change=save_feedback, args=(message['id'],))
    if is_last and meta.get('followups'):
        st.html(f'<div class="followup-title">{mi("auto_awesome", 15)}<span>이어서 물어보기</span></div>')
        with st.container(key=f"followups_{message['id']}"):
            for i, q in enumerate(meta['followups']):
                if st.button(q, key=f"fu_{message['id']}_{i}", icon=':material/subdirectory_arrow_right:', width='stretch'):
                    st.session_state.pending = q
                    st.rerun()


def ask(question, use_graph, agentic):
    history = [{'role': m['role'], 'content': m['content']} for m in st.session_state.messages]
    st.session_state.messages.append({'id': uuid.uuid4().hex[:10], 'role': 'user', 'content': question})
    with st.chat_message('user'):
        st.markdown(question)
    with st.chat_message('assistant', avatar=AVATAR):
        status = st.status('답변을 준비하고 있어요', expanded=True)
        ctx = get_script_run_ctx()
        main_thread = threading.current_thread()

        def on_progress(event, step, info):
            if threading.current_thread() is not main_thread and ctx is not None:
                add_script_run_ctx(threading.current_thread(), ctx)
            if event == 'start':
                status.update(label=f'{step} 중…')
                return
            extra = ''
            if step == '질문 분석' and info.get('route') == 'chat':
                extra = ' · 인사·잡담이라 검색을 건너뛰어요'
            elif step == '근거 평가':
                extra = ' · 충분해요' if info.get('sufficient') else ' · 부족해서 다시 찾아요'
            elif step == '다시 찾기':
                extra = f" · 근거 {info.get('added', 0)}개 추가"
            elif step == '근거 찾기' and info.get('found') is not None:
                extra = f" · {info['found']}개"
            status.write(f"✓ {step}{extra} ({info.get('sec', 0):.1f}초)")

        try:
            start = time.time()
            with rag_core.progress_listener(on_progress):
                result = get_chain(use_graph, agentic).invoke({'input': question, 'history': history})
            elapsed = time.time() - start
            status.update(label=f'완료 · {elapsed:.1f}초', state='complete', expanded=False)
        except Exception as exc:
            logger.exception('답변 생성 실패')         # 원인은 Manage app → 로그에서 확인
            status.update(label='답변을 만들지 못했어요', state='error')
            st.session_state.last_error = f'답변 생성: {type(exc).__name__}: {str(exc)[:200]}'
            st.error('답변을 만들지 못했어요. 잠시 후 다시 질문해 주세요.')
            st.session_state.messages.pop()            # 실패한 질문은 대화 맥락에 남기지 않는다
            return
    body, numbered, structured = format_answer(result)
    trace, agent = result['trace'], result['trace'].get('agent') or {}
    graph = st.session_state.index.graph
    cited = [s['chunk_id'] for s in numbered]
    keys = trace.get('graph_concept_keys') or []
    meta = {'intent': result.get('intent'), 'elapsed': elapsed, 'graph': use_graph, 'agentic': agentic,
            'retried': agent.get('retried', False), 'steps': agent.get('steps'), 'numbered': numbered,
            'structured': structured,
            'unverified': any(i['type'] in ('unknown_citation', 'missing_citation') for i in result.get('issues', [])),
            'concept_map': (tidy_concept_map(graph.subgraph(keys), st.session_state.index)
                            if (use_graph and graph is not None and keys) else None),
            'followups': pick_followups(question, cited, graph) if result.get('intent') != 'chat' else []}
    st.session_state.messages.append({'id': uuid.uuid4().hex[:10], 'role': 'assistant', 'content': body,
                                      'meta': meta, 'feedback': None})
    st.session_state.last_trace = {'question': question, 'trace': trace, 'issues': result.get('issues', []),
                                   'elapsed': elapsed}
    st.rerun()                                         # 답변은 대화 기록에서 한 가지 방식으로 그린다


def export_json():
    out = []
    for m in st.session_state.messages:
        item = {'role': m['role'], 'content': m['content']}
        if m['role'] == 'assistant':
            meta = m.get('meta') or {}
            item.update({'intent': meta.get('intent'), 'elapsed_sec': round(meta.get('elapsed', 0), 1),
                         'graph': meta.get('graph'), 'agentic': meta.get('agentic'), 'feedback': m.get('feedback'),
                         'sources': [{'number': s['number'], 'page': s['page_start'], 'title': s['title']}
                                     for s in meta.get('numbered') or []]})
        out.append(item)
    return json.dumps({'document': st.session_state.doc_name, 'messages': out}, ensure_ascii=False, indent=2)


def admin_panel(configured):
    with st.container(border=True, key='admin_panel'):
        ui_heading('admin_panel_settings', '관리자')
        password = os.getenv('ADMIN_PASSWORD')
        if not password:
            st.caption('ADMIN_PASSWORD가 설정되지 않아 관리자 영역을 쓸 수 없어요.')
            return
        if not st.session_state.admin:
            entered = st.text_input('관리자 비밀번호', type='password', label_visibility='collapsed',
                                    placeholder='관리자 비밀번호')
            if entered and entered == password:
                st.session_state.admin = True
                st.rerun()
            elif entered:
                st.error('비밀번호가 맞지 않아요.')
            return
        with st.expander('설정 · 색인', expanded=False):
            st.json({'API 키': '설정됨' if configured else '없음', '답변 모델': os.getenv('OPENAI_CHAT_MODEL'),
                     '임베딩 모델': os.getenv('OPENAI_EMBEDDING_MODEL'), 'Neo4j': bool(os.getenv('NEO4J_URI')),
                     '그래프 JSON': os.path.exists(GRAPH_PATH),
                     '임베딩 캐시 파일': len(os.listdir(EMBED_CACHE)) if os.path.isdir(EMBED_CACHE) else 0})
            index = st.session_state.index
            if index is not None:
                parts = {}
                for d in index.docs:
                    parts[d.metadata['part']] = parts.get(d.metadata['part'], 0) + 1
                st.json({'문서': st.session_state.doc_name, '쪽수': index.pages, '청크': len(index.docs),
                         '근거 종류별 청크': parts, '새로 임베딩한 청크': index.new_embeddings,
                         '준비 시간(초)': {k: round(v, 1) for k, v in index.timing.items()},
                         '그래프': index.graph_info})
        index = st.session_state.index
        neo4j_on = st.toggle('그래프 검색에 Neo4j 직접 조회', value=st.session_state.neo4j_search,
                             disabled=index is None or index.graph is None,
                             help='끄면 Neo4j에서 읽어 온 같은 그래프로 메모리에서 찾아 더 빨라요.')
        if neo4j_on != st.session_state.neo4j_search:
            set_neo4j_search(neo4j_on)
        if st.session_state.last_trace:
            last = st.session_state.last_trace
            trace = last['trace']
            with st.expander('마지막 답변 추적', expanded=False):
                st.json({'질문': last['question'], '전체(초)': round(last['elapsed'], 2),
                         '단계별(초)': trace.get('timing'),
                         '검색 결과 수': {k: len(trace.get(k, [])) for k in ('vector', 'bm25', 'graph')},
                         '찾은 개념': trace.get('graph_concepts'),
                         '검증 기록': [i['type'] for i in last['issues']]})
        votes = [m.get('feedback') for m in st.session_state.messages if m['role'] == 'assistant']
        st.caption(f"답변 평가 · 👍 {votes.count('up')} · 👎 {votes.count('down')} · 미평가 {votes.count(None)}")
        if st.session_state.last_error:
            st.caption(f'최근 오류: {st.session_state.last_error}')
        if st.button('관리자 로그아웃', width='stretch'):
            st.session_state.admin = False
            st.rerun()


def render_sidebar(configured):
    with st.sidebar:
        with st.container(key='sidebar_stack'):
            st.html(f'<div class="sidebar-logo">{ui_wordmark()}</div>')
            with st.container(border=True, key='document_panel'):
                ui_heading('description', '내 백서')
                present = st.session_state.get(f"uploader_{st.session_state.uploader_key}")
                if present is not None and st.session_state.index is not None:
                    st.html(f'''<div class="doc-card">{mi("picture_as_pdf", 26, "#e96868")}
                        <div><div class="doc-name">{esc(st.session_state.doc_name)}</div>
                        <div class="doc-meta">{st.session_state.doc_size / 1024 / 1024:.1f} MB · PDF 백서</div></div>
                        <span class="doc-check">{mi("check_circle", 20, "#249b72")}</span></div>''')
                else:
                    st.html(f'<div class="upload-copy">{mi("upload_file", 24)}<span>PDF 백서를 올리면 바로 준비해요.'
                            f'<br>최대 {MAX_MB} MB · 글자를 읽을 수 있는 PDF</span></div>')
                box = st.expander('문서 교체') if present is not None and st.session_state.index is not None else st.container()
                with box:
                    uploaded = st.file_uploader('PDF 백서', type=['pdf'], label_visibility='collapsed',
                                                key=f"uploader_{st.session_state.uploader_key}",
                                                disabled=not configured)
                if configured and uploaded is not None:
                    prepare_document(uploaded)
                elif uploaded is None and st.session_state.index is not None:
                    reset_document()                   # 업로드 창에서 파일을 지우면 문서도 해제
            index = st.session_state.index
            ready = index is not None
            graph_ok = ready and index.graph is not None
            with st.container(border=True, key='answer_settings'):
                ui_heading('tune', '답변 설정')
                use_graph = st.toggle('그래프 검색', value=graph_ok, disabled=not graph_ok,
                                      help='질문 속 개념과 관계로 이어진 절을 함께 찾아요. 사전 그래프가 있는 백서에서만 켤 수 있어요.')
                agentic = st.toggle('Agentic (근거 평가·재검색)', value=False,
                                    help='근거가 부족하면 다시 찾아요. 질문마다 몇 초 더 걸려요.')
                show_sources = st.toggle('참고 출처 표시', value=True, help='답변 아래 근거 카드를 보여 줘요.')
            with st.container(border=True, key='document_status'):
                ui_heading('monitoring', '현재 상태')
                if not configured:
                    st.html('<div class="ui-api-note"><strong>API 설정이 필요해요</strong><br>'
                            '관리자가 Streamlit Secrets에 OPENAI_API_KEY를 설정하면 사용할 수 있어요.</div>')
                elif not ready:
                    st.html(f'<div class="status-card waiting"><span class="status-dot">{mi("hourglass_empty", 13)}</span>'
                            '<div><div class="status-title">문서를 기다리고 있어요</div>'
                            '<div class="status-copy">PDF 백서를 올리면 바로 질문할 수 있어요.</div></div></div>')
                else:
                    info = index.graph_info
                    graph_line = (f"그래프 개념 {info['concepts']:,}개 · {'Neo4j' if info.get('source') == 'neo4j' else 'JSON'}"
                                  if graph_ok else '사전 그래프 없음 · 기본 검색으로 답해요')
                    st.html(f'<div class="status-card"><span class="status-dot">{mi("check", 14)}</span>'
                            f'<div><div class="status-title">문서 준비 완료</div><div class="status-copy">'
                            f'{index.pages}쪽 · 청크 {len(index.docs)}개<br>{esc(graph_line)}</div></div></div>')
            with st.container(border=True, key='management_panel'):
                ui_heading('folder_managed', '관리')
                col1, col2 = st.columns(2, gap='small')
                if col1.button('대화 초기화', key='reset_chat', icon=':material/refresh:', disabled=not st.session_state.messages,
                               width='stretch'):
                    st.session_state.messages = []
                    st.session_state.last_trace = None
                    st.rerun()
                if col2.button('문서 변경', key='release_document', icon=':material/swap_horiz:', disabled=not ready, width='stretch'):
                    reset_document()
                    st.session_state.uploader_key += 1          # 업로드 창을 비운다
                    st.rerun()
                st.download_button('대화와 출처 저장', export_json() if st.session_state.messages else '{}',
                                   file_name='aivle_chat.json', mime='application/json', key='export_chat',
                                   disabled=not st.session_state.messages, width='stretch',
                                   icon=':material/download:')
            admin_panel(configured)
            st.html('<p class="ui-note">올린 문서와 대화는 이 브라우저 세션에서만 쓰고 저장하지 않아요.<br>'
                    '서버가 다시 시작되면 문서를 다시 올려 주세요.</p>')
    return ready, use_graph, agentic, show_sources


def render_intro(configured):
    st.html(f'''<section class="intro-hero"><div class="intro-copy"><span class="intro-eyebrow">AIVLE School</span>
        <div class="intro-title" role="heading" aria-level="1">{cfg.APP_TITLE}</div>
        <p class="intro-description">{cfg.INTRO_DESCRIPTION}</p></div>
        <div class="intro-art" aria-hidden="true">{ui_ghost()}</div></section>''')
    st.html(f'<div class="landing-section-title">{mi("auto_awesome", 21)}<span>이런 질문을 해 보세요!</span></div>')
    with st.container(key='intro_cards'):
        cols = st.columns(4, gap='medium')
        for col, (key, icon, title, questions) in zip(cols, TOPICS):
            with col, st.container(border=True, key=f'topic_card_{key}'):
                st.html(f'<div class="topic-card-icon">{mi(icon, 24)}</div><div class="topic-card-name">{title}</div>')
                for i, q in enumerate(questions):
                    st.button(q, key=f'topic_{key}_{i}', disabled=True, width='stretch')
    note = ('왼쪽에서 백서 PDF를 올리면 위 질문을 누르거나 직접 질문할 수 있어요. 답변마다 백서 원문 근거를 함께 보여 드려요.'
            if configured else '관리자가 API 키를 설정하면 사용할 수 있어요.')
    st.html(f'<p class="landing-note">{mi("info", 16, "#7d939e")}<span>{note}</span></p>')


def render_ready():
    st.html('<header class="ready-header"><div class="ready-title" role="heading" aria-level="1">무엇이 궁금하세요?</div>'
            f'<p class="ready-description">{cfg.READY_DESCRIPTION}</p></header>')
    st.html(f'<div class="landing-section-title">{mi("auto_awesome", 21)}<span>많이 찾는 질문</span></div>')
    with st.container(key='ready_questions'):
        for start in (0, 2):
            cols = st.columns(2, gap='medium')
            for col, (key, q) in zip(cols, READY_QUESTIONS[start:start + 2]):
                if col.button(q, key=f'ready_{key}', icon=f':material/{TOPIC_ICON[key]}:', width='stretch'):
                    st.session_state.pending = q
                    st.rerun()
    with st.expander('주제별 질문 더 보기'):
        cols = st.columns(4, gap='small')
        for col, (key, _icon, title, questions) in zip(cols, TOPICS):
            col.markdown(f'**{title}**')
            for i, q in enumerate(questions):
                if col.button(q, key=f'more_{key}_{i}', width='stretch'):
                    st.session_state.pending = q
                    st.rerun()


def render_chat_header():
    st.html(f'<header class="home-header"><div class="home-brand">{ui_wordmark()}'
            '<span class="home-title" role="heading" aria-level="1">학습 도우미</span></div></header>')
    with st.container(key='starter_area'):
        cols = st.columns(4, gap='small')
        for col, (key, label, q) in zip(cols, CHAT_CHIPS):
            with col, st.container(key=f'chip_{key}'):
                if st.button(label, key=f'chipbtn_{key}', icon=f':material/{TOPIC_ICON[key]}:', width='stretch', help=q):
                    st.session_state.pending = q
                    st.rerun()


# ----------------------------------------------------------------------------
# 앱
# ----------------------------------------------------------------------------
configured = load_settings()
init_state()
inject_css()
ready, use_graph, agentic, show_sources = render_sidebar(configured)

if st.session_state.messages:
    render_chat_header()
elif ready:
    render_ready()
else:
    render_intro(configured)

for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message['role'], avatar=AVATAR if message['role'] == 'assistant' else None):
        st.markdown(message['content'])
        if message['role'] == 'assistant':
            render_assistant_extras(message, i == len(st.session_state.messages) - 1, show_sources)

prompt = st.chat_input('에이블스쿨에 대해 궁금한 점을 물어보세요' if ready else '먼저 왼쪽에서 백서 PDF를 올려 주세요',
                       disabled=not ready, max_chars=1000)
if ready and st.session_state.pending:
    prompt, st.session_state.pending = st.session_state.pending, None
if ready and prompt:
    ask(prompt, use_graph, agentic)
