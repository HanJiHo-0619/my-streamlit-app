




# 김대리 개인비서 에이블의 Streamlit 화면
import asyncio
import base64
import io
import json
import os
import smtplib
import wave
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText

import streamlit as st
from google import genai
from google.auth.transport.requests import Request
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from streamlit_calendar import calendar

def load_local_env():
    if not os.path.exists("api_key.txt"):
        return
    with open("api_key.txt", encoding="utf-8") as f:
        lines = f.read().splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value in ('"""', "'''"):
            quote = value
            chunks = []
            while index < len(lines) and lines[index].strip() != quote:
                chunks.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].strip() == quote:
                index += 1
            value = "\n".join(chunks).strip()
        else:
            value = value.strip('"').strip("'")
        os.environ.setdefault(key, value)


load_local_env()
client = OpenAI()
MODEL = "gpt-6-luna"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
KST = timezone(timedelta(hours=9))
MAX_TURNS = 10

INSTRUCTIONS = """
당신은 '김 대리'의 AI 개인비서 '에이블'입니다.

회의에 참석하지 못한 김 대리가 회의 내용을 이해하고,
자신의 담당 업무와 마감일을 정확하게 파악하도록 돕습니다.

[기본 대화 원칙]
1. 사용자의 질문을 이해하고 자연스러운 한국어로 답합니다.
2. 입력으로 제공된 최근 대화 기록을 참고하여 이전 질문과 연결되는 후속 질문을 이해합니다.
3. 일상적인 대화는 도구를 호출하지 않고 직접 답합니다.
4. 회의록과 저장된 회의 분석 결과에 없는 내용을 추측하거나 만들어내지 않습니다.
5. 사용자가 요청하지 않은 도구는 호출하지 않습니다.
6. 도구 실행 결과를 받기 전에는 작업이 완료됐다고 말하지 않습니다.

[회의 요약 및 후속 질문]
1. 새로운 회의 요약이나 미팅 정리를 요청받으면 summarize_meeting 도구를 호출합니다.
2. 담당 업무를 처음 확인하는 요청이며 이전 회의 분석 결과가 제공되지 않았다면 summarize_meeting 도구를 호출합니다.
3. 이전 대화 또는 저장된 회의 분석 결과로 답변할 수 있는 후속 질문에는 summarize_meeting을 다시 호출하지 않습니다.
4. 첫 회의 요약 요청에는 도구가 반환한 summary를 참고하여 핵심 내용을 간결하게 안내합니다.
5. 김 대리의 업무 전체를 요청하면 배정된 업무와 마감일을 빠짐없이 설명합니다.
6. 특정 업무를 질문하면 해당 업무의 내용과 마감일을 중심으로 설명합니다.
7. 상세한 설명이나 원본 근거를 요청하면 제공된 회의 분석 결과에 근거하여 설명합니다.
8. 확정된 결정 사항과 검토 중인 의견을 구분합니다.
9. 담당자가 변경되거나 업무가 취소됐다면 최종 확정된 내용을 기준으로 설명합니다.
10. 마감일이 없거나 취소됐다면 마감일이 정해지지 않았다고 안내합니다.

[이메일 기능]
1. 사용자가 이메일 발송을 명시적으로 요청한 경우에만 send_email 도구를 호출합니다.
2. 수신자나 발송 내용이 불분명하면 확인을 요청합니다.
3. 사용자가 발송 내용을 확인할 수 있도록 안내합니다.
4. 도구 실행 결과를 확인하기 전에는 이메일 발송이 완료됐다고 말하지 않습니다.
5. 발송 실패 시 성공했다고 안내하지 않습니다.

[캘린더 기능]
1. 사용자가 일정 등록을 명시적으로 요청한 경우에만 register_calendar_events 도구를 호출합니다.
2. 사용자가 회의 기준일을 명시한 경우 meeting_date에 YYYY-MM-DD 형식으로 전달합니다.
3. 사용자가 회의 기준일을 말하지 않았다면 meeting_date에는 빈 문자열을 전달합니다.
4. 기준일이 없다는 이유만으로 등록 도구 호출 전에 날짜를 다시 묻지 않습니다.
5. 회의록에 명확한 날짜가 없는 업무에 대해 임의로 마감일을 확정했다고 말하지 않습니다.
6. 도구가 반환한 등록 결과를 확인하고 등록된 업무와 등록되지 않은 업무를 구분합니다.
7. 등록에 실패하거나 제외된 업무를 등록 완료했다고 말하지 않습니다.
8. 일정이 등록되지 않은 이유가 날짜 불명확성이라면 사용자에게 해당 사실을 안내합니다.

[최종 응답 원칙]
1. 사용자의 질문 의도에 맞춰 답변합니다.
2. 첫 요약은 간결하게 안내합니다.
3. 후속 질문에는 필요한 만큼 자세하게 답변합니다.
4. 김 대리의 담당 업무와 마감일은 정확하게 전달합니다.
5. 업무의 정확성을 답변 길이보다 우선합니다.
6. 도구 실행 결과와 제공된 회의 정보를 근거로 답변합니다.
7. 같은 내용을 불필요하게 반복하지 않습니다.
"""

TOOLS = [
    {
        "type": "function",
        "name": "summarize_meeting",
        "description": "업로드된 회의록을 요약하고 김대리 업무 체크리스트를 만듭니다. 회의 요약, 미팅 정리, 배정 업무 확인을 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "send_email",
        "description": "저장된 회의 요약을 이메일로 보냅니다. 사용자가 이메일 발송을 명시적으로 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "수신 이메일 주소"},
                "subject": {"type": "string", "description": "이메일 제목"},
            },
            "required": ["to", "subject"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "register_calendar_events",
        "description": "날짜가 확정된 김대리 업무를 구글 캘린더와 앱 캘린더에 함께 등록합니다. 사용자가 캘린더 등록을 명시적으로 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_date": {
                    "type": "string",
                    "description": "사용자가 말한 회의 기준일. YYYY-MM-DD. 말하지 않았으면 빈 문자열.",
                },
            },
            "required": ["meeting_date"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

st.set_page_config(page_title="AIVLE", page_icon="🤖", layout="wide")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "tasks" not in st.session_state:
    st.session_state.tasks = []
if "summary" not in st.session_state:
    st.session_state.summary = ""
if "events" not in st.session_state:
    st.session_state.events = []
if "calendar_tasks" not in st.session_state:
    st.session_state.calendar_tasks = []
if "meeting_text" not in st.session_state:
    st.session_state.meeting_text = ""
if "page" not in st.session_state:
    st.session_state.page = "홈"
if "uploads" not in st.session_state:
    st.session_state.uploads = []


def meeting_text():
    if st.session_state.meeting_text.strip():
        return st.session_state.meeting_text
    if os.path.exists("meeting.txt"):
        with open("meeting.txt", encoding="utf-8") as f:
            return f.read()
    return ""


def load_google_credentials():
    info = None
    raw = os.getenv("GOOGLE_TOKEN", "").strip()
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = None
    if not info and os.path.exists("token.json"):
        with open("token.json", encoding="utf-8") as f:
            info = json.load(f)
    if not info:
        raise RuntimeError("token.json 또는 GOOGLE_TOKEN 비밀값이 없습니다.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_calendar_service():
    return build("calendar", "v3", credentials=load_google_credentials())


def saved_email():
    address = (os.getenv("GMAIL_ADDRESS") or "").strip()
    if address:
        return address
    if os.path.exists("connected_email.txt"):
        with open("connected_email.txt", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_email(address):
    address = address.strip()
    with open("connected_email.txt", "w", encoding="utf-8") as f:
        f.write(address)
    os.environ["GMAIL_ADDRESS"] = address


def send_email(to, subject, body):
    sender = saved_email()
    password = os.getenv("GMAIL_APP_PASSWORD")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to, msg.as_string())
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def sync_checklist(events):
    previous = {task["title"]: task for task in st.session_state.tasks}
    tasks = []
    seen = set()
    for event in events:
        title = (event.get("summary") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        current = previous.get(title, {"title": title, "done": False, "event_id": ""})
        current["due_text"] = (event.get("due_text") or "").strip()
        tasks.append(current)
    st.session_state.tasks = tasks


def summarize_meeting():
    text = meeting_text()
    if not text.strip():
        return {"status": "failed", "error": "회의록 txt를 먼저 업로드하세요."}

    prompt = f"""
다음 회의록을 분석하여 JSON만 작성해 주세요.

[분석 원칙]
1. 회의록에 명시된 사실만 사용하고 추측하지 않습니다.
2. 회의 핵심 내용을 3~5줄로 요약합니다.
3. 확정된 결정 사항과 논의 중인 의견을 구분합니다.
4. 김 대리에게 최종 배정된 모든 업무를 빠짐없이 추출합니다.
5. 다른 사람에게 배정된 업무는 포함하지 않습니다.
6. 담당자가 변경됐다면 최종 담당자를 기준으로 합니다.
7. 최종 취소된 업무는 제외합니다.
8. 업무는 유지되고 마감일만 취소됐다면 해당 업무를 유지하고 마감일은 미정으로 처리합니다.
9. 반복 언급된 동일 업무는 하나로 정리합니다.
10. 원본 근거는 회의록 문장을 그대로 인용합니다.
11. 업무의 우선순위가 명시되지 않았다면 임의로 우선순위를 정하지 않습니다.

[summary 작성 기준]
summary에는 사람이 읽기 좋은 한국어 보고서를 작성합니다.

첫 줄:
회의 주제를 작성하고 "미팅 내용을 요약해 전달드립니다."라고 안내합니다.

[미팅 요약]
- 핵심 내용을 3~5줄로 작성합니다.
- 확정된 사항과 검토 중인 사항을 구분합니다.

[김대리님께 배정된 업무]
김 대리의 모든 업무를 아래 형식으로 작성합니다.

1. 업무 제목
   - 담당자: 김 대리
   - 해야 할 일: 회의록에 나온 업무 내용
   - 마감일: 회의록에 나온 최종 기한
   - 구체적인 요청사항: 회의록에 나온 요청사항
   - 원본 근거: 실제 회의록 문장

업무가 여러 개면 같은 형식을 반복합니다.

김 대리에게 확정된 업무가 없다면 "배정된 업무 없음"이라고 작성합니다.

[마감일 처리]
- 날짜가 명시된 경우 회의록 표현을 유지합니다.
- "다음 주까지", "금요일까지" 등의 표현은 특정 날짜로 임의 계산하지 않습니다.
- "빨리", "조속히" 등은 원문 표현을 유지하면서 구체적인 기한은 미정이라고 안내합니다.
- 마감일이 없다면 "마감일 미정"으로 작성합니다.
- 최종적으로 취소된 마감일은 사용하지 않습니다.

[events 작성 기준]
events에는 김 대리에게 최종 배정된 업무만 넣습니다.
기존 JSON 필드명과 자료형을 변경하지 않습니다.

- summary: 업무 제목
- description: 해야 할 일과 구체적인 요청사항
- owner: "김대리"
- due_text: 회의록에 나온 최종 기한 표현
- has_clock_time: 명확한 시각이 있을 때만 true
- start_date: 명확한 YYYY-MM-DD 날짜가 있을 때만 입력
- end_date: 명확한 YYYY-MM-DD 날짜가 있을 때만 입력
- start_time: 명확한 시각이 있을 때만 HH:MM
- end_time: 명확한 종료 시각이 있을 때만 HH:MM

[events 날짜 기준]
1. 마감일 표현은 due_text에 원문 그대로 기록합니다.
2. 기한이 없거나 취소됐다면 due_text는 빈 문자열입니다.
3. YYYY-MM-DD 날짜가 명시되지 않았다면 start_date와 end_date는 빈 문자열입니다.
4. "다음 주", "금요일", "빨리" 등의 표현을 임의로 특정 날짜로 변환하지 않습니다.
5. 시각이 명시되지 않았다면 has_clock_time은 false이고 start_time, end_time은 빈 문자열입니다.
6. 동일 업무는 events에 중복 추가하지 않습니다.
7. 김 대리에게 확정된 업무가 없다면 events는 빈 배열입니다.

[최종 확인]
1. 회의록 전체를 확인하여 김 대리 업무 누락 여부를 검토합니다.
2. summary와 events에 포함된 업무가 일치하는지 확인합니다.
3. 취소되거나 변경된 업무와 기한이 정확하게 반영됐는지 확인합니다.
4. 담당자, 업무, 마감일, 원본 근거를 만들어내지 않습니다.
5. JSON 외의 설명이나 코드 블록을 출력하지 않습니다.

[회의록]
{text}
"""
    response = client.responses.create(
        model=MODEL,
        instructions="당신은 김대리의 개인 비서 에이블입니다. 회의록에 있는 사실만 사용합니다.",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "meeting_pack",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "description": {"type": "string"},
                                    "owner": {"type": "string"},
                                    "due_text": {"type": "string"},
                                    "has_clock_time": {"type": "boolean"},
                                    "start_date": {"type": "string"},
                                    "end_date": {"type": "string"},
                                    "start_time": {"type": "string"},
                                    "end_time": {"type": "string"},
                                },
                                "required": [
                                    "summary", "description", "owner", "due_text",
                                    "has_clock_time", "start_date", "end_date",
                                    "start_time", "end_time",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["summary", "events"],
                    "additionalProperties": False,
                },
            }
        },
    )
    data = json.loads(response.output_text)
    events = [
        event for event in data["events"]
        if event.get("owner", "").replace(" ", "").removesuffix("님") == "김대리"
    ]
    st.session_state.summary = data["summary"]
    st.session_state.events = events
    sync_checklist(events)
    seed_app_calendar()
    return {"summary": data["summary"]}


def send_meeting_email(to, subject):
    if not to:
        return {"status": "failed", "error": "수신 이메일 주소가 없습니다."}
    if not st.session_state.summary:
        summarized = summarize_meeting()
        if summarized.get("status") == "failed":
            return summarized
    return send_email(to, subject or "회의 요약", st.session_state.summary)


def next_week_friday(meeting_date):
    base = date.fromisoformat(meeting_date)
    days_until_next_monday = (7 - base.weekday()) % 7 or 7
    friday = base + timedelta(days=days_until_next_monday + 4)
    return friday.isoformat(), (friday + timedelta(days=1)).isoformat()


def event_dates(event, meeting_date):
    due_text = (event.get("due_text") or "").strip()
    if "다음 주" in due_text and not event.get("has_clock_time"):
        return next_week_friday(meeting_date)

    start_date = (event.get("start_date") or "").strip()
    end_date = (event.get("end_date") or "").strip()
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        return None
    try:
        date.fromisoformat(end_date)
    except ValueError:
        end_date = (start + timedelta(days=1)).isoformat()
    return start_date, end_date


def clock_text(event):
    if not event.get("has_clock_time"):
        return "", ""
    start_time = (event.get("start_time") or "").strip()
    end_time = (event.get("end_time") or "").strip()
    if len(start_time) < 4:
        return "", ""
    return start_time, end_time


def put_app_calendar(title, start_date, event_id="", start_time="", end_time="", overwrite=True):
    timed = bool(start_time)
    start_value = f"{start_date}T{start_time}:00" if timed else start_date
    for event in st.session_state.calendar_tasks:
        if event["title"] != title:
            continue
        if event.get("edited") and not overwrite:
            return
        event["start"] = start_value
        event["allDay"] = not timed
        event["start_time"] = start_time
        event["end_time"] = end_time
        if event_id:
            event["event_id"] = event_id
        return
    st.session_state.calendar_tasks.append({
        "title": title,
        "start": start_value,
        "allDay": not timed,
        "event_id": event_id,
        "start_time": start_time,
        "end_time": end_time,
        "edited": False,
    })


def seed_app_calendar():
    meeting_date = datetime.now(KST).date().isoformat()
    for event in st.session_state.events:
        title = (event.get("summary") or "").strip()
        dates = event_dates(event, meeting_date)
        start_time, end_time = clock_text(event)
        for task in st.session_state.tasks:
            if task["title"] == title:
                task["deadline"] = dates[0] if dates else ""
                task["start_time"] = start_time
                task["end_time"] = end_time
        if dates:
            put_app_calendar(title, dates[0], start_time=start_time, end_time=end_time, overwrite=False)


def remember_event_id(title, event_id):
    for task in st.session_state.tasks:
        if task["title"] == title:
            task["event_id"] = event_id


def create_calendar_event(summary, start_date, end_date, description, start_time="", end_time=""):
    try:
        service = get_calendar_service()
        if start_time:
            end_clock = end_time or start_time
            if end_clock == start_time and ":" in start_time:
                hour, minute = [int(part) for part in start_time.split(":", 1)]
                end_clock = (datetime(2000, 1, 1, hour, minute) + timedelta(hours=1)).strftime("%H:%M")
            body = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": f"{start_date}T{start_time}:00", "timeZone": "Asia/Seoul"},
                "end": {"dateTime": f"{start_date}T{end_clock}:00", "timeZone": "Asia/Seoul"},
            }
        else:
            body = {
                "summary": summary,
                "description": description,
                "start": {"date": start_date},
                "end": {"date": end_date},
            }
        created = service.events().insert(calendarId="primary", body=body).execute()
        return {
            "status": "success",
            "summary": summary,
            "start_date": start_date,
            "event_id": created["id"],
        }
    except Exception as e:
        return {"status": "failed", "summary": summary, "error": str(e)}


def google_time_body(start_date, start_time, end_time):
    if start_time:
        end_clock = end_time or start_time
        if end_clock == start_time and ":" in start_time:
            hour, minute = [int(part) for part in start_time.split(":", 1)]
            end_clock = (datetime(2000, 1, 1, hour, minute) + timedelta(hours=1)).strftime("%H:%M")
        return {
            "start": {"dateTime": f"{start_date}T{start_time}:00", "timeZone": "Asia/Seoul"},
            "end": {"dateTime": f"{start_date}T{end_clock}:00", "timeZone": "Asia/Seoul"},
        }
    end_date = (date.fromisoformat(start_date) + timedelta(days=1)).isoformat()
    return {"start": {"date": start_date}, "end": {"date": end_date}}


def update_google_event(event_id, start_date, start_time, end_time):
    if not event_id:
        return {"status": "skipped"}
    try:
        service = get_calendar_service()
        service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body=google_time_body(start_date, start_time, end_time),
        ).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def register_calendar_events(meeting_date=""):
    meeting_date = (meeting_date or "").strip()
    if not meeting_date:
        meeting_date = datetime.now(KST).date().isoformat()
    else:
        try:
            date.fromisoformat(meeting_date)
        except ValueError:
            return {"status": "failed", "error": "기준일은 YYYY-MM-DD 형식이어야 합니다."}
    if not st.session_state.events:
        summarized = summarize_meeting()
        if summarized.get("status") == "failed":
            return summarized

    registered = []
    skipped = []
    for event in st.session_state.events:
        title = event.get("summary", "")
        edited = next((item for item in st.session_state.calendar_tasks if item.get("title") == title and item.get("edited")), None)
        if edited and event_day(edited):
            start_date = event_day(edited)
            end_date = (date.fromisoformat(start_date) + timedelta(days=1)).isoformat()
            dates = (start_date, end_date)
            start_time = (edited.get("start_time") or "").strip()
            end_time = (edited.get("end_time") or "").strip()
        else:
            dates = event_dates(event, meeting_date)
            start_time, end_time = clock_text(event)
        if not dates:
            skipped.append({"summary": title, "reason": "등록할 날짜가 없습니다."})
            continue
        description = event.get("description", "")
        due_text = (event.get("due_text") or "").strip()
        if due_text:
            description = f"{description}\n기한 원문: {due_text}\n기준일: {meeting_date}"
        put_app_calendar(title, dates[0], start_time=start_time, end_time=end_time, overwrite=True)
        result = create_calendar_event(title, dates[0], dates[1], description, start_time, end_time)
        if result["status"] == "success":
            event["event_id"] = result["event_id"]
            put_app_calendar(title, dates[0], result["event_id"], start_time, end_time, overwrite=True)
            remember_event_id(title, result["event_id"])
            registered.append(result)
        else:
            skipped.append({"summary": title, "reason": result["error"]})
    return {"status": "success", "registered": registered, "skipped": skipped, "meeting_date": meeting_date}


def run_tool(name, arguments):
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    arguments = arguments or {}
    if name == "summarize_meeting":
        return summarize_meeting()
    if name == "send_email":
        return send_meeting_email(arguments.get("to", ""), arguments.get("subject", "회의 요약"))
    if name == "register_calendar_events":
        return register_calendar_events(arguments.get("meeting_date", ""))
    return {"error": f"unknown tool: {name}"}


def pcm_to_wav(pcm, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def live_speak(text):
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY가 없습니다.")
    gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    spoken = bytearray()
    async with gemini.aio.live.connect(
        model="gemini-3.8-live",
        config={
            "response_modalities": ["AUDIO"],
            "system_instruction": "사용자가 준 문장을 바꾸거나 덧붙이지 말고 그대로 읽습니다.",
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Leda"}
                }
            },
        },
    ) as session:
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )
        async for msg in session.receive():
            if msg.data:
                spoken.extend(msg.data)
            server_content = msg.server_content
            if server_content and server_content.turn_complete:
                break
    if not spoken:
        raise RuntimeError("음성 응답이 비어 있습니다.")
    return pcm_to_wav(bytes(spoken))


def ask_assistant(user_text):
    items = [
        {"role": message["role"], "content": message["content"]}
        for message in st.session_state.messages[-(MAX_TURNS * 2):]
    ]
    items.append({"role": "user", "content": user_text})
    response = client.responses.create(
        model=MODEL,
        instructions=INSTRUCTIONS,
        input=items,
        tools=TOOLS,
    )
    for _ in range(3):
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            result = run_tool(call.name, call.arguments)
            outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
        items = [*items, *response.output, *outputs]
        response = client.responses.create(
            model=MODEL,
            instructions=INSTRUCTIONS,
            input=items,
            tools=TOOLS,
        )
    answer = response.output_text or "요청을 처리하지 못했습니다."
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.session_state.messages = st.session_state.messages[-(MAX_TURNS * 2):]
    return answer


def remember_upload(name, text):
    os.makedirs("meeting_uploads", exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._- ") or "meeting.txt"
    path = os.path.join("meeting_uploads", f"{stamp}_{safe}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    st.session_state.uploads.insert(0, {
        "name": name,
        "path": path,
        "saved_at": datetime.now(KST).strftime("%Y.%m.%d %H:%M"),
    })
    st.session_state.meeting_text = text


def load_saved_uploads():
    if st.session_state.get("uploads_loaded"):
        return
    st.session_state.uploads_loaded = True
    if not os.path.isdir("meeting_uploads"):
        return
    files = sorted(os.listdir("meeting_uploads"), reverse=True)
    for filename in files:
        if not filename.endswith(".txt"):
            continue
        path = os.path.join("meeting_uploads", filename)
        st.session_state.uploads.append({
            "name": filename.split("_", 2)[-1],
            "path": path,
            "saved_at": datetime.fromtimestamp(os.path.getmtime(path), KST).strftime("%Y.%m.%d %H:%M"),
        })


def event_day(event):
    return (event.get("start") or "")[:10]


def time_label(event):
    start_time = (event.get("start_time") or "").strip()
    end_time = (event.get("end_time") or "").strip()
    if not start_time and "T" in (event.get("start") or ""):
        start_time = event["start"][11:16]
    if not start_time:
        return ""
    if end_time:
        return f"{start_time}–{end_time}"
    return start_time


def dday_label(deadline):
    if not deadline:
        return ""
    try:
        target = date.fromisoformat(deadline)
    except ValueError:
        return ""
    diff = (target - datetime.now(KST).date()).days
    if diff == 0:
        return "D-Day"
    if diff > 0:
        return f"D-{diff}"
    return f"D+{abs(diff)}"


def drop_app_event(title):
    st.session_state.calendar_tasks = [
        event for event in st.session_state.calendar_tasks
        if event.get("title") != title
    ]


def sorted_tasks(tasks):
    def sort_key(task):
        deadline = task.get("deadline") or "9999-99-99"
        return deadline, task["title"]
    return sorted(tasks, key=sort_key)


def run_request(request_text, audio_value):
    parts = []
    if request_text and request_text.strip():
        parts.append(request_text.strip())
    if audio_value is not None:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.wav", audio_value, "audio/wav"),
        )
        if transcript.text.strip():
            parts.append(transcript.text.strip())
    if not parts:
        st.warning("음성이나 텍스트로 요청해 주세요.")
        return
    with st.spinner("답변을 준비하고 음성으로 읽고 있습니다..."):
        answer = ask_assistant("\n".join(parts))
        try:
            st.session_state.messages[-1]["audio"] = asyncio.run(live_speak(answer))
            st.session_state.voice_token = st.session_state.get("voice_token", 0) + 1
            st.session_state.messages[-1]["voice_token"] = st.session_state.voice_token
            st.session_state.autoplay_voice = True
        except Exception as e:
            st.session_state.voice_error = str(e)
    st.rerun()


def render_chat(placeholder):
    if st.session_state.get("voice_error"):
        st.error(st.session_state.pop("voice_error"))
    st.markdown('<div class="chat-heading"><span>💬</span><b>AIVLE AI 채팅</b><span class="chat-more">⛶ &nbsp; ⋮</span></div>', unsafe_allow_html=True)
    if not st.session_state.messages:
        st.markdown(
            '<div class="welcome-bubble">안녕하세요!<br>AIVLE입니다. 👋<br>'
            '<small>업무, 회의, 일정 등 궁금한 내용을<br>자유롭게 물어보세요.</small></div>',
            unsafe_allow_html=True,
        )
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    with st.form("chat_form"):
        chat_text = st.text_input("채팅", placeholder=placeholder, label_visibility="collapsed")
        chat_submit = st.form_submit_button("보내기")
    if chat_submit:
        try:
            run_request(chat_text, None)
        except Exception as e:
            st.error(str(e))


def render_voice():
    autoplay_voice = st.session_state.pop("autoplay_voice", False)
    last_voice = max(
        (index for index, message in enumerate(st.session_state.messages) if message.get("audio")),
        default=-1,
    )
    voice_player = st.components.v2.component(
        "able_voice",
        js="""
        export default function(component) {
          if (!window.__ableUnlock) {
            window.__ableUnlock = true;
            window.__ableCtx = new AudioContext();
            document.addEventListener("pointerdown", () => {
              if (window.__ableCtx && window.__ableCtx.state !== "running") {
                window.__ableCtx.resume();
              }
            }, true);
          }
          const data = component.data || {};
          if (!data.wav || window.__ablePlayed === data.token) return;
          window.__ablePlayed = data.token;
          const ctx = window.__ableCtx;
          const binary = atob(data.wav);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          ctx.decodeAudioData(bytes.buffer.slice(0)).then((buffer) => {
            const source = ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(ctx.destination);
            source.start(0);
          });
        }
        """,
    )
    play_wav = ""
    play_token = 0
    if autoplay_voice and last_voice >= 0:
        spoken = st.session_state.messages[last_voice]
        play_wav = base64.b64encode(spoken["audio"]).decode("ascii")
        play_token = spoken.get("voice_token", 0)
    voice_player(data={"wav": play_wav, "token": play_token}, key="able_voice_player", height=0)


def render_sidebar():
    st.markdown(
        '<div class="brand"><div class="brand-bot">🤖</div><div><strong>AIVLE</strong>'
        '<span>AIVLE | AI 개인비서</span></div></div>',
        unsafe_allow_html=True,
    )
    open_tasks = len(st.session_state.tasks)
    nav_items = (("⌂   홈", "홈"), (f"☑   내 업무     {open_tasks}", "내 업무"), ("▤   회의 요약", "회의 요약"))
    for label, page in nav_items:
        if st.button(label, key=f"nav_{page}", use_container_width=True, type="primary" if st.session_state.page == page else "secondary"):
            st.session_state.page = page
            st.rerun()
    st.markdown('<div class="side-spacer"></div><div class="side-footer">', unsafe_allow_html=True)
    st.markdown("👤 &nbsp; **마케팅팀 김 대리**")
    address = saved_email()
    if address:
        st.markdown(f'<div class="connection">✉ &nbsp; 이메일 <em>연결됨</em></div>', unsafe_allow_html=True)
    else:
        st.caption("이메일 주소를 연결해 주세요.")
        with st.form("email_form"):
            typed = st.text_input("보낼 이메일 주소")
            save = st.form_submit_button("저장")
        if save and typed.strip():
            save_email(typed)
            st.rerun()
    if (os.getenv("GOOGLE_TOKEN") or "").strip() or os.path.exists("token.json"):
        st.markdown('<div class="connection">▣ &nbsp; 캘린더 <em>연결됨</em></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="connection">▣ &nbsp; 캘린더 <em>연결 필요</em></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def page_heading(icon, title, description):
    st.markdown(
        f'<div class="page-heading"><span class="heading-icon">{icon}</span>'
        f'<div><h1>{title}</h1><p>{description}</p></div></div>',
        unsafe_allow_html=True,
    )


def render_home():
    main, chat = st.columns([3.05, 1.25], gap="large")
    with main:
        st.markdown('<div class="home-hero">AIVLE과 함께<br>업무를 <strong>더 효율적으로</strong> 관리하세요.</div>', unsafe_allow_html=True)
        month, week = st.columns([3, 1], gap="medium")
        with month:
            with st.container(border=True):
                calendar(
                    events=st.session_state.calendar_tasks,
                    options={"initialView": "dayGridMonth", "locale": "ko", "height": 485, "headerToolbar": {"left": "prev,next", "center": "title", "right": "today"}},
                    key="work_calendar",
                )
        with week:
            with st.container(border=True):
                today = datetime.now(KST).date()
                monday = today - timedelta(days=today.weekday())
                sunday = monday + timedelta(days=6)
                st.markdown("<h3 class='card-title'>이번 주 일정</h3>", unsafe_allow_html=True)
                st.caption(f"{monday.month}.{monday.day}({ '월화수목금토일'[monday.weekday()] }) – {sunday.month}.{sunday.day}({ '월화수목금토일'[sunday.weekday()] })")
                week_events = [
                    event for event in st.session_state.calendar_tasks
                    if monday.isoformat() <= event_day(event) <= sunday.isoformat()
                ]
                week_events.sort(key=lambda event: (event_day(event), event.get("start_time") or "99:99"))
                if not week_events:
                    st.markdown('<p class="empty-text">이번 주 일정이 없습니다.</p>', unsafe_allow_html=True)
                current = ""
                for event in week_events:
                    day = event_day(event)
                    if day != current:
                        current = day
                        parsed = date.fromisoformat(day)
                        st.markdown(f"<b class='date-chip'>{parsed.month}.{parsed.day}({ '월화수목금토일'[parsed.weekday()] })</b>", unsafe_allow_html=True)
                    label = time_label(event)
                    st.markdown(f"<div class='schedule-item'><span></span>{event['title']}<small>{label}</small></div>", unsafe_allow_html=True)
        titles = [event["title"] for event in st.session_state.calendar_tasks]
        with st.expander("일정 수정"):
            if not titles:
                st.write("요약으로 일정이 생기면 여기서 날짜와 시간을 바꿀 수 있습니다.")
            else:
                with st.form("edit_event_form"):
                    picked = st.selectbox("일정", titles)
                    current_event = next(event for event in st.session_state.calendar_tasks if event["title"] == picked)
                    try:
                        current_day = date.fromisoformat(event_day(current_event))
                    except ValueError:
                        current_day = today
                    new_day = st.date_input("날짜", value=current_day)
                    new_start = st.text_input("시작 시각", value=current_event.get("start_time") or "", placeholder="비우면 시간 없이 표시")
                    new_end = st.text_input("종료 시각", value=current_event.get("end_time") or "")
                    save_edit = st.form_submit_button("수정")
                if save_edit:
                    current_event["edited"] = True
                    put_app_calendar(
                        picked,
                        new_day.isoformat(),
                        event_id=current_event.get("event_id", ""),
                        start_time=new_start.strip(),
                        end_time=new_end.strip(),
                        overwrite=True,
                    )
                    for task in st.session_state.tasks:
                        if task["title"] == picked:
                            task["deadline"] = new_day.isoformat()
                            task["start_time"] = new_start.strip()
                            task["end_time"] = new_end.strip()
                    current_event["edited"] = True
                    updated = update_google_event(
                        current_event.get("event_id", ""),
                        new_day.isoformat(),
                        new_start.strip(),
                        new_end.strip(),
                    )
                    if updated.get("status") == "failed":
                        st.session_state.voice_error = f"앱 일정은 수정했습니다. 구글 캘린더 수정은 실패했습니다. {updated['error']}"
                    st.rerun()
        with st.container(border=True):
            with st.form("home_request"):
                input_col, action_col = st.columns([5, 1])
                with input_col:
                    request_text = st.text_input("요청", placeholder="회의를 요약하고, 내 업무를 알려줘.", label_visibility="collapsed")
                    audio_value = st.audio_input("음성으로 요청")
                with action_col:
                    st.markdown("<div class='form-label'>AI 요청</div>", unsafe_allow_html=True)
                    submit = st.form_submit_button("✦ 분석하기", use_container_width=True)
        if submit:
            try:
                run_request(request_text, audio_value)
            except Exception as e:
                st.error(str(e))
    with chat:
        with st.container(border=True):
            render_chat("궁금한 내용을 입력하세요...")


def render_tasks():
    page_heading("☑", "내 업무", "할 일을 관리하고 진행 상황을 한눈에 확인하세요.")
    tasks = st.session_state.tasks
    done_count = sum(1 for task in tasks if task.get("done"))
    total = len(tasks)
    with st.container(border=True):
        progress = done_count / total if total else 0
        st.markdown(f'<div class="progress-head"><span>업무 진행률<br><strong>{done_count} / {total} 완료</strong></span><b>{int(progress * 100)}%</b></div>', unsafe_allow_html=True)
        st.progress(progress)
    filter_col, sort_col = st.columns([4, 1])
    with filter_col:
        filter_name = st.radio("필터", [f"전체 ({total})", f"진행 중 ({total - done_count})", f"완료 ({done_count})"], horizontal=True, label_visibility="collapsed", key="task_filter")
    with sort_col:
        st.selectbox("정렬", ["마감일순", "가나다순"], label_visibility="collapsed")
    visible = tasks
    if filter_name.startswith("진행 중"):
        visible = [task for task in tasks if not task.get("done")]
    elif filter_name.startswith("완료"):
        visible = [task for task in tasks if task.get("done")]
    visible = sorted_tasks(visible)
    with st.container(border=True):
        if not visible:
            st.markdown('<p class="empty-text">표시할 업무가 없습니다.</p>', unsafe_allow_html=True)
        for task in visible:
            badge = dday_label(task.get("deadline", "")) or "마감일 없음"
            left, right = st.columns([7, 1])
            with left:
                checked = st.checkbox(task["title"], value=bool(task.get("done")), key=f"done_{task['title']}")
            with right:
                tone = "done" if task.get("done") else "due"
                st.markdown(f'<span class="deadline {tone}">{badge}</span>', unsafe_allow_html=True)
            st.divider()
            if checked != task.get("done"):
                task["done"] = checked
                if checked:
                    drop_app_event(task["title"])
                st.rerun()
    with st.container(border=True):
        st.markdown("<h3 class='add-title'>⊕ &nbsp;업무 추가</h3>", unsafe_allow_html=True)
        with st.form("add_task_form"):
            title_col, deadline_col, submit_col = st.columns([5, 1.45, 1.2])
            with title_col:
                title = st.text_input("새로운 업무", placeholder="새로운 업무를 입력하세요...", label_visibility="collapsed")
            with deadline_col:
                deadline = st.date_input("마감일", value=date.today(), label_visibility="collapsed")
                use_deadline = st.checkbox("캘린더 표시", value=True)
            with submit_col:
                added = st.form_submit_button("추가", use_container_width=True)
    if added:
        title = title.strip()
        if not title:
            st.warning("업무를 입력해 주세요.")
        elif any(task["title"] == title for task in st.session_state.tasks):
            st.warning("이미 있는 업무입니다.")
        else:
            st.session_state.tasks.append({
                "title": title,
                "done": False,
                "event_id": "",
                "due_text": "",
                "deadline": deadline.isoformat() if use_deadline else "",
                "start_time": "",
                "end_time": "",
            })
            if use_deadline:
                put_app_calendar(title, deadline.isoformat(), overwrite=True)
            st.rerun()


def render_meetings():
    load_saved_uploads()
    page_heading("▤", "회의 요약", "회의록, 녹취 텍스트를 업로드하면 핵심 내용을 요약해드립니다.")
    left, right = st.columns([1, 1.65], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("<div class='upload-intro'>☁<br><b>회의 텍스트 파일 업로드</b><small>.txt 파일 (최대 50MB)</small></div>", unsafe_allow_html=True)
            uploaded = st.file_uploader("파일 선택", type=["txt"], label_visibility="collapsed")
            if uploaded is not None and st.session_state.get("last_upload_name") != uploaded.name:
                remember_upload(uploaded.name, uploaded.getvalue().decode("utf-8"))
                st.session_state.last_upload_name = uploaded.name
                st.rerun()
            st.divider()
            st.markdown("<h3 class='recent-title'>◷ &nbsp;최근 업로드</h3>", unsafe_allow_html=True)
            if not st.session_state.uploads:
                st.markdown('<p class="empty-text">올린 회의록이 없습니다.</p>', unsafe_allow_html=True)
            for item in st.session_state.uploads[:8]:
                if st.button(f"▤   {item['name']}\n{item['saved_at']}", key=f"upload_{item['path']}", use_container_width=True):
                    with open(item["path"], encoding="utf-8") as f:
                        st.session_state.meeting_text = f.read()
                    st.rerun()
    with right:
        with st.container(border=True):
            if st.session_state.summary:
                st.markdown("<h3 class='summary-title'>▤ &nbsp;회의 요약</h3>", unsafe_allow_html=True)
                st.markdown(f'<div class="summary-content">{st.session_state.summary}</div>', unsafe_allow_html=True)
                st.divider()
                st.markdown("<h3 class='summary-title'>☑ &nbsp;김대리 업무</h3>", unsafe_allow_html=True)
                if st.session_state.tasks:
                    for task in st.session_state.tasks:
                        extra = task.get("due_text") or "마감일 미정"
                        st.markdown(f"<div class='summary-task'>• &nbsp;{task['title']} <small>{extra}</small></div>", unsafe_allow_html=True)
                else:
                    st.markdown('<p class="empty-text">배정된 업무가 없습니다.</p>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="summary-placeholder">회의록을 업로드한 뒤<br>채팅에서 요약을 요청해 주세요.</div>', unsafe_allow_html=True)
            st.divider()
            render_chat("회의 내용에 대해 추가 질문하세요...")


st.markdown(
    """
    <style>
    :root { --mint: #10a985; --mint-dark: #078768; --mint-pale: #e6f8f2; --ink: #10172c; --muted: #687287; --line: #e7ebf0; }
    * { font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif; }
    .stApp { background: linear-gradient(145deg, #fff 0%, #fff 70%, #f2fffa 100%); color: var(--ink); }
    #MainMenu, footer, [data-testid="stHeader"] { visibility: hidden; }
    [data-testid="stAppViewContainer"] > .main { background: radial-gradient(ellipse 45% 12% at 88% 6%, #def8ef 0%, rgba(222,248,239,0) 80%); }
    .block-container { max-width: 1510px; padding: 2rem 3.2rem 2.5rem; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #f7fffc, #effbf7); border-right: 1px solid #e5f2ed; }
    [data-testid="stSidebar"] > div:first-child { padding: 1.65rem 1rem; }
    [data-testid="stSidebar"] .stButton > button { min-height: 3.55rem; border: 0; border-radius: 14px; background: transparent; color: var(--ink); font-size: 1.04rem; font-weight: 700; text-align: left; padding-left: 1rem; margin: .15rem 0; }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] { background: #dff8ef; color: #079c78; }
    [data-testid="stSidebar"] .stButton > button:hover { background: #eafaf4; color: var(--mint-dark); border: 0; }
    .brand { display:flex; align-items:center; gap:.75rem; margin:.2rem .45rem 3rem; color:#081438; }
    .brand-bot { width:3.2rem; height:3.2rem; display:grid; place-items:center; background:linear-gradient(145deg,#e8fff7,#c7f5e6); border-radius:1rem; font-size:1.8rem; box-shadow:0 4px 18px #bcebdd80; }
    .brand strong { display:block; font-size:1.78rem; line-height:1; letter-spacing:-.04em; }
    .brand span { display:block; margin-top:.35rem; font-size:.84rem; font-weight:700; }
    .side-spacer { height: 37vh; min-height: 12rem; }
    .side-footer { border-top:1px solid #e1eee9; padding:.95rem .55rem 0; }
    .connection { display:flex; justify-content:space-between; align-items:center; font-weight:650; padding:.45rem 0; }
    .connection em { background:#dcf8ef; color:#079b76; border-radius:1rem; font-style:normal; font-size:.76rem; padding:.26rem .54rem; }
    .page-heading { display:flex; gap:1rem; align-items:flex-start; margin:.2rem 0 2rem; }
    .heading-icon { font-size:2rem; padding-top:.2rem; color:#081438; }
    .page-heading h1 { margin:0; font-size:2.05rem; letter-spacing:-.06em; color:#09112a; }
    .page-heading p { margin:.35rem 0 0; font-size:1rem; color:#47526b; }
    .home-hero { font-size:2.18rem; line-height:1.18; letter-spacing:-.065em; font-weight:700; color:#11162c; margin:0 0 2rem .4rem; }
    .home-hero strong { color:#0aa77e; font-weight:800; }
    [data-testid="stVerticalBlockBorderWrapper"] { border:1px solid var(--line); border-radius:1.35rem; background:rgba(255,255,255,.93); box-shadow:0 5px 16px rgba(29,51,75,.035); }
    [data-testid="stVerticalBlockBorderWrapper"] > div { padding:.7rem 1rem; }
    .card-title, .add-title, .recent-title, .summary-title { color:#11162c; letter-spacing:-.045em; margin:.2rem 0 .45rem; font-size:1.22rem; }
    .date-chip { display:inline-block; margin:1.05rem 0 .45rem; border-radius:1rem; padding:.25rem .5rem; background:#e5faf2; font-size:.86rem; }
    .schedule-item { display:grid; grid-template-columns:.55rem 1fr; gap:.45rem; align-items:center; font-weight:650; margin:.5rem 0; line-height:1.25; }
    .schedule-item span { width:.48rem; height:.48rem; border-radius:50%; background:#39bfa2; }
    .schedule-item small { grid-column:2; color:var(--muted); font-size:.78rem; font-weight:500; }
    .empty-text { color:var(--muted); padding:1rem .2rem; }
    .chat-heading { display:flex; align-items:center; gap:.55rem; padding:.25rem .1rem 1rem; border-bottom:1px solid var(--line); font-size:1.1rem; }
    .chat-heading:first-letter { color:var(--mint); }
    .chat-more { margin-left:auto; color:#0c1530; font-size:1.12rem; }
    .welcome-bubble { margin:1.7rem .15rem 1rem 2.9rem; padding:1rem 1.1rem; border-radius:1rem; background:#f1f2f4; font-weight:650; line-height:1.55; }
    .welcome-bubble small { font-size:.86rem; font-weight:500; }
    [data-testid="stChatMessage"] { padding:.45rem 0; background:transparent; }
    [data-testid="stForm"] { border:0; padding:0; }
    [data-testid="stTextInput"] input { border:1px solid #e1e6ed; border-radius:.85rem; min-height:2.85rem; background:#fff; }
    [data-testid="stTextInput"] input:focus { border-color:#62cdb4; box-shadow:0 0 0 1px #62cdb4; }
    [data-testid="stSelectbox"] > div > div, [data-testid="stDateInput"] input { border-radius:.85rem; border-color:#e1e6ed; }
    .stButton > button, [data-testid="stFormSubmitButton"] > button { border-radius:.85rem; font-weight:750; border-color:#b8e8da; color:#078c6c; }
    [data-testid="stFormSubmitButton"] > button { background:linear-gradient(135deg,#3bb394,#76c9b4); color:white; border:0; min-height:2.7rem; }
    [data-testid="stProgress"] > div > div > div > div { background:linear-gradient(90deg,#30aa8c,#8bd2bd); border-radius:99px; }
    [data-testid="stProgress"] > div > div { background:#edf0f1; border-radius:99px; }
    .progress-head { display:flex; justify-content:space-between; align-items:flex-start; font-size:1.03rem; font-weight:700; margin:.1rem 0 .55rem; }
    .progress-head strong { display:inline-block; font-size:1.7rem; line-height:1.3; color:#079d78; letter-spacing:-.05em; }
    .progress-head b { color:#3e475c; font-size:1.35rem; }
    [data-testid="stRadio"] { margin:.75rem 0 1rem; }
    [data-testid="stRadio"] label { background:#f6f8fa; border:1px solid #e6eaf0; border-radius:999px; padding:.45rem .88rem; font-weight:650; }
    [data-testid="stRadio"] label:has(input:checked) { background:#e8f9f3; border-color:#49b89e; color:#078d6c; }
    [data-testid="stRadio"] label div:first-child { display:none; }
    [data-testid="stCheckbox"] { padding:.3rem .1rem; }
    [data-testid="stCheckbox"] label { font-size:1.08rem; font-weight:700; color:#12182d; }
    .deadline { display:inline-block; white-space:nowrap; text-align:center; padding:.31rem .65rem; border-radius:999px; background:#eef9f4; color:#159879; font-weight:750; margin-top:.25rem; }
    .deadline.due { background:#fff2e4; color:#b75c10; }
    .add-title { color:#0a9d7a; }
    .upload-intro { text-align:center; padding:2.2rem .4rem 1rem; border:1px dashed #cbd7df; border-radius:1rem; background:linear-gradient(140deg,#fff,#f8fcfd); color:#078f6e; font-size:2.5rem; }
    .upload-intro b { display:block; margin-top:.6rem; font-size:1.15rem; color:#14192d; }
    .upload-intro small { display:block; margin:.25rem; color:#657087; font-size:.85rem; }
    [data-testid="stFileUploader"] { margin:1rem auto; max-width:14rem; }
    [data-testid="stFileUploader"] section { border:0; background:transparent; }
    [data-testid="stFileUploader"] section button { color:#078f6e; border-color:#c8e9df; border-radius:999px; }
    .summary-content { white-space:pre-wrap; line-height:1.7; color:#242b3e; background:#fbfcfd; border-radius:.8rem; padding:.8rem; }
    .summary-task { padding:.4rem 0; font-weight:650; }.summary-task small { color:var(--muted); font-weight:500; margin-left:.4rem; }
    .summary-placeholder { min-height:13rem; display:grid; place-items:center; text-align:center; color:#758095; line-height:1.7; }
    .form-label { color:#788297; font-size:.78rem; margin-bottom:.55rem; }
    @media (max-width: 900px) { .block-container { padding:1rem; } .side-spacer { height:5rem; min-height:0; } .home-hero { font-size:1.8rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)
load_saved_uploads()
render_voice()
with st.sidebar:
    render_sidebar()
if st.session_state.page == "내 업무":
    render_tasks()
elif st.session_state.page == "회의 요약":
    render_meetings()
else:
    render_home()
