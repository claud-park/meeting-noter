"""
FLO 회의록 자동화 파이프라인
STT (Whisper 또는 CLOVA Speech) → Claude (정제 + 요약) → Markdown → Confluence

사용법:
    # Whisper 사용 (로컬, 무료)
    python meeting_bot.py --file 회의녹음.mp3 --title "AX팀 회의" --attendees "이비집사,Alex" --stt whisper

    # CLOVA Speech 사용
    python meeting_bot.py --file 회의녹음.mp3 --title "AX팀 회의" --attendees "이비집사,Alex" --stt clova

필요 환경변수 (.env):
    ANTHROPIC_API_KEY       = your_anthropic_api_key
    CLOVA_INVOKE_URL        = https://clovaspeech-gw.ncloud.com/external/v1/...  (clova 사용 시)
    CLOVA_SECRET_KEY        = your_secret_key                                     (clova 사용 시)
    CONFLUENCE_URL          = https://music-flo.atlassian.net
    CONFLUENCE_EMAIL        = your@flo.com
    CONFLUENCE_API_TOKEN    = your_confluence_api_token
    CONFLUENCE_PARENT_ID    = 4807229822
"""

import os
import sys
import json
import time
import argparse
import requests
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── 설정 ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY")
CLOVA_INVOKE_URL     = os.getenv("CLOVA_INVOKE_URL")
CLOVA_SECRET_KEY     = os.getenv("CLOVA_SECRET_KEY")
CONFLUENCE_URL       = os.getenv("CONFLUENCE_URL", "https://music-flo.atlassian.net")
CONFLUENCE_EMAIL     = os.getenv("CONFLUENCE_EMAIL")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
CONFLUENCE_PARENT_ID = os.getenv("CONFLUENCE_PARENT_ID", "4807229822")

CLAUDE_MODEL = "claude-sonnet-4-6"
OUTPUT_DIR   = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ── STT: Whisper (로컬) ───────────────────────────────────────────────
def transcribe_whisper(audio_path: str) -> str:
    """
    OpenAI Whisper로 로컬 STT
    - 화자 분리 없음 (타임스탬프만 포함)
    - 설치: pip install openai-whisper
    """
    print("[1/4] STT 변환 중 (Whisper)... 파일 길이에 따라 수 분 소요될 수 있어요")
    try:
        import whisper
    except ImportError:
        print("❌ Whisper 미설치. 실행: pip install openai-whisper")
        sys.exit(1)

    model = whisper.load_model("medium")   # small / medium / large — 클수록 정확하나 느림
    result = model.transcribe(audio_path, language="ko", verbose=False)

    lines = []
    for seg in result["segments"]:
        start = seg["start"]
        h = int(start // 3600)
        m = int((start % 3600) // 60)
        s = int(start % 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        lines.append(f"[{ts}] 참석자?: {seg['text'].strip()}")

    raw = "\n".join(lines)
    print(f"   ✓ Whisper STT 완료 — {len(result['segments'])} 세그먼트")
    return raw


# ── STT: CLOVA Speech ─────────────────────────────────────────────────
def transcribe_clova(audio_path: str) -> str:
    """CLOVA Speech API로 STT (화자 분리 포함)"""
    print("[1/4] STT 변환 중 (CLOVA Speech)...")

    if not all([CLOVA_INVOKE_URL, CLOVA_SECRET_KEY]):
        print("❌ CLOVA_INVOKE_URL / CLOVA_SECRET_KEY 환경변수 누락")
        sys.exit(1)

    request_body = {
        "language": "ko-KR",
        "completion": "sync",
        "diarization": {"enable": True, "speakerCountMin": 2, "speakerCountMax": 10},
        "timestamp": True,
        "fullText": True,
    }

    with open(audio_path, "rb") as f:
        response = requests.post(
            f"{CLOVA_INVOKE_URL}/recognizer/upload",
            headers={"X-CLOVASPEECH-API-KEY": CLOVA_SECRET_KEY},
            files={"media": f, "params": (None, json.dumps(request_body), "application/json")},
            timeout=600,
        )

    if response.status_code != 200:
        raise RuntimeError(f"CLOVA Speech 오류 {response.status_code}: {response.text}")

    result = response.json()
    if result.get("result") == "FAILED":
        raise RuntimeError(f"CLOVA Speech 실패: {result.get('message')}")

    lines = []
    for seg in result.get("segments", []):
        speaker = seg.get("diarization", {}).get("label", "?")
        ms = seg.get("start", 0)
        h, r = divmod(ms // 1000, 3600)
        m, s = divmod(r, 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}"
        lines.append(f"[{ts}] 참석자{speaker}: {seg.get('text','').strip()}")

    raw = "\n".join(lines)
    print(f"   ✓ CLOVA STT 완료 — {len(result.get('segments', []))} 세그먼트")
    return raw


# ── Step 2: Claude 텍스트 정제 ───────────────────────────────────────
def refine_transcript(raw: str, attendees: list[str], client: anthropic.Anthropic) -> str:
    print("[2/4] 텍스트 정제 중...")

    attendee_str = ", ".join(attendees) if attendees else "참석자 정보 없음"

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8096,
        messages=[{"role": "user", "content": f"""당신은 회의 전사 전문가입니다. 아래 STT 원문을 정제해주세요.

## 참석자 목록
{attendee_str}

## 정제 규칙
1. "참석자A/B/C" 레이블을 문맥으로 파악해 실제 이름으로 교체 (특정 불가 시 유지)
2. STT 오인식 단어를 문맥에 맞게 교정 (고유명사, 영어 혼용어 주의)
3. "음~", "어~", "그니까" 등 필러 워드 제거
4. 문장 단위로 자연스럽게 정리 (원문 의미 유지)
5. 타임스탬프 [HH:MM:SS] 형식 유지

## STT 원문
{raw}

## 출력 형식
[HH:MM:SS] 이름: 발화 내용
(위 형식만 출력)"""}],
    )

    refined = message.content[0].text.strip()
    print("   ✓ 정제 완료")
    return refined


# ── Step 3: Claude 회의록 생성 ───────────────────────────────────────
def generate_minutes(
    refined: str,
    title: str,
    date: str,
    attendees: list[str],
    client: anthropic.Anthropic,
) -> str:
    print("[3/4] 회의록 생성 중...")

    attendee_str = ", ".join(attendees) if attendees else "미기재"

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": f"""당신은 FLO 내부 회의 기록 전문가입니다.
아래 스크립트를 분석해 실용적인 회의록을 작성해주세요.

## 회의 정보
- 제목: {title}
- 일시: {date}
- 참석자: {attendee_str}

## 스크립트
{refined}

## 작성 지침
- 스크립트에 있는 내용만 작성 (추가/추론 금지)
- 결정사항: "~하기로 함" 형식
- 액션아이템: 담당자 + 마감일 (언급 없으면 "미정")
- 논의 의견이 대립된 경우 양측 의견 모두 기록

## 출력 형식

# {title}

> **일시:** {date}
> **참석자:** {attendee_str}
> **작성일:** {datetime.now().strftime("%Y-%m-%d")} (자동 생성)

---

## 📋 회의 요약
(3~5줄 핵심 요약)

---

## ✅ 결정 사항
| # | 결정 내용 | 관련 참석자 |
|---|-----------|------------|

---

## 🎯 액션 아이템
| # | 할 일 | 담당자 | 마감일 |
|---|-------|--------|--------|

---

## 💬 주요 논의 내용
### [주제명]
내용

---

## 📎 참고 / 기타
(링크, 후속 일정 등)
"""}],
    )

    minutes = message.content[0].text.strip()
    print("   ✓ 회의록 생성 완료")
    return minutes


# ── Step 4: 파일 저장 ────────────────────────────────────────────────
def save_outputs(minutes: str, refined: str, title: str) -> dict:
    print("[4/4] 파일 저장 중...")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    safe = title.replace(" ", "_").replace("/", "-")[:30]
    base = OUTPUT_DIR / f"{ts}_{safe}"

    md_path = base.with_suffix(".md")
    tx_path = Path(str(base) + "_transcript.txt")

    md_path.write_text(minutes, encoding="utf-8")
    tx_path.write_text(refined, encoding="utf-8")

    print(f"   ✓ 회의록: {md_path}")
    print(f"   ✓ 스크립트: {tx_path}")
    return {"minutes": md_path, "transcript": tx_path}


# ── Step 5: Confluence 업로드 ─────────────────────────────────────────
def markdown_to_confluence(md: str) -> str:
    html = md
    html = re.sub(r'^# (.+)$',   r'<h1>\1</h1>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$',  r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.+?)\*',     r'<em>\1</em>', html)
    html = re.sub(r'^---$', '<hr/>', html, flags=re.MULTILINE)
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)

    def convert_table(m):
        rows = [l for l in m.group(0).strip().split('\n') if not re.match(r'^\|[-| ]+\|$', l)]
        out = '<table><tbody>'
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip('|').split('|')]
            tag = 'th' if i == 0 else 'td'
            out += '<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>'
        return out + '</tbody></table>'

    html = re.sub(r'(\|.+\|\n)+', convert_table, html)

    parts = []
    for p in re.split(r'\n\n+', html):
        p = p.strip()
        if not p: continue
        if re.match(r'^<(h[1-3]|table|hr|blockquote)', p):
            parts.append(p)
        else:
            parts.append(f'<p>{p.replace(chr(10), "<br/>")}</p>')
    return '\n'.join(parts)


def upload_to_confluence(minutes: str, title: str, date: str) -> str | None:
    if not all([CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN]):
        print("   ⚠️  Confluence 환경변수 미설정 — 업로드 건너뜀")
        return None

    print("[5/5] Confluence 업로드 중...")

    response = requests.post(
        f"{CONFLUENCE_URL}/wiki/rest/api/content",
        json={
            "type": "page",
            "title": f"[회의록] {title} ({date})",
            "ancestors": [{"id": CONFLUENCE_PARENT_ID}],
            "space": {"key": "AX"},
            "body": {"storage": {
                "value": markdown_to_confluence(minutes),
                "representation": "storage",
            }},
        },
        auth=(CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if response.status_code == 200:
        page_id = response.json().get("id")
        url = f"{CONFLUENCE_URL}/wiki/spaces/AX/pages/{page_id}"
        print(f"   ✓ Confluence 업로드 완료: {url}")
        return url
    else:
        print(f"   ❌ Confluence 실패 ({response.status_code}): {response.text[:200]}")
        return None


# ── 메인 파이프라인 ───────────────────────────────────────────────────
def run_pipeline(audio_path: str, title: str, attendees_str: str, stt_engine: str):
    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY 누락")
        sys.exit(1)

    attendees = [a.strip() for a in attendees_str.split(",")] if attendees_str else []
    date = datetime.now().strftime("%Y년 %m월 %d일")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    start = time.time()
    print(f"\n🎙️  FLO 회의록 자동화 시작")
    print(f"   파일: {audio_path}")
    print(f"   STT: {stt_engine.upper()}")
    print(f"   제목: {title}")
    print(f"   참석자: {', '.join(attendees) if attendees else '미입력'}\n")

    try:
        # 1. STT
        if stt_engine == "whisper":
            raw = transcribe_whisper(audio_path)
        else:
            raw = transcribe_clova(audio_path)

        # 2. 정제
        refined = refine_transcript(raw, attendees, client)

        # 3. 회의록
        minutes = generate_minutes(refined, title, date, attendees, client)

        # 4. 저장
        paths = save_outputs(minutes, refined, title)

        # 5. Confluence
        url = upload_to_confluence(minutes, title, date)
        if url:
            paths["confluence"] = url

        elapsed = time.time() - start
        print(f"\n✅ 완료! 총 소요 시간: {elapsed:.1f}초\n")
        print("=" * 60)
        print(minutes[:600] + "..." if len(minutes) > 600 else minutes)
        print("=" * 60)

        return paths

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        raise


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FLO 회의록 자동화 봇")
    parser.add_argument("--file",      required=True,  help="음성 파일 경로")
    parser.add_argument("--title",     default="팀 회의", help="회의 제목")
    parser.add_argument("--attendees", default="",     help="참석자 (쉼표 구분)")
    parser.add_argument("--stt",       default="whisper", choices=["whisper", "clova"],
                        help="STT 엔진 선택 (기본값: whisper)")
    args = parser.parse_args()

    run_pipeline(
        audio_path=args.file,
        title=args.title,
        attendees_str=args.attendees,
        stt_engine=args.stt,
    )