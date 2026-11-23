# 🎙️ 회의록 자동화 봇

CLOVA Speech (STT) + Claude (요약/정제) 를 연결한 회의록 자동 생성 파이프라인

## 파이프라인 흐름

```
음성 파일 (.mp3/.wav)
    ↓
[CLOVA Speech] 화자 분리 + 타임스탬프 전사
    ↓
[Claude] STT 오류 교정 + 화자명 정규화
    ↓
[Claude] 요약 / 결정사항 / 액션아이템 추출
    ↓
회의록.md + 스크립트.txt 저장
```

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env
# .env 파일을 열어 API 키 입력
```

## 필요한 API 키 발급

### CLOVA Speech
1. [네이버 클라우드 플랫폼](https://www.ncloud.com) 로그인
2. AI Services > CLOVA Speech > 이용 신청
3. 도메인 생성 → **InvokeURL** + **Secret Key** 복사

### Anthropic Claude
1. [console.anthropic.com](https://console.anthropic.com) > API Keys
2. 새 키 생성 → 복사

## 사용법

```bash
# 기본 사용
python meeting_bot.py \
  --file 회의녹음.mp3 \
  --title "AX팀 주간회의" \
  --attendees "이비집사,Alex,Jefferey"

# 참석자 없이 실행 (화자 A/B/C로 표시)
python meeting_bot.py --file meeting.wav --title "기획 회의"
```

## 출력 예시

```
output/
├── 20250601_143022_AX팀_주간회의.md          ← 최종 회의록
└── 20250601_143022_AX팀_주간회의_transcript.txt ← 정제 스크립트
```

## 회의록 형식

자동 생성되는 Markdown 회의록 구조:
- 📋 **회의 요약** - 3~5줄 핵심 요약
- ✅ **결정 사항** - 테이블 형식
- 🎯 **액션 아이템** - 담당자 + 마감일 포함
- 💬 **주요 논의 내용** - 주제별 정리

## 지원 파일 형식

mp3, m4a, wav, aac, ogg, flac (CLOVA Speech 지원 포맷)

## 주의사항

- 회의 파일 1개당 CLOVA Speech + Claude API 비용 발생
- 60분 회의 기준 약 8~10분 소요
- 개인정보 포함 회의는 사내 보안 정책 확인 후 사용
