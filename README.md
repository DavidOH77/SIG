# 시그(SIG) - UX 설문 분석 MVP

시그(SIG)는 **설문 수집 도구가 아니라, 업로드된 설문 시트를 분석해 실무 인사이트를 만드는 도구**입니다.

## 왜 이전에 Next.js 파일이 없었나?
이전 커밋은 실행 환경 제약(외부 npm 설치 제한) 때문에 Python 단일 앱으로 먼저 완성했습니다.  
이번 커밋에서 **Next.js(App Router) 프론트엔드 셸**을 추가해 구조를 명확히 했습니다.

---

## 현재 구조
- `server.py`: Python 분석 서버 (실제 분석/리포트/내보내기 엔진)
- `app/*`: Next.js App Router UI 셸
- `sample_data/demo_survey.csv`: 데모 설문 데이터
- `docs/architecture.md`: 아키텍처 노트

## 빠른 실행 (서비스 체험)
### 1) Python 분석 서버 실행 (필수)
```bash
python server.py
```
- 접속: `http://localhost:8000`
- 데모 프로젝트 자동 생성됨

### 2) Next.js 셸 실행 (선택)
> npm 설치 가능한 환경에서
```bash
npm install
npm run dev
```
- 접속: `http://localhost:3000`
- 내부에서 Python 분석 페이지를 iframe으로 연결해 보여줌

## IA 페이지 (Python 서버 기준)
- `/` : 홈
- `/projects/[id]` : 프로젝트 개요
- `/projects/[id]/data` : 업로드/프리뷰/매핑
- `/projects/[id]/health` : 데이터 건강도
- `/projects/[id]/analysis` : 분석 개요
- `/projects/[id]/quant` : 정량 분석
- `/projects/[id]/segments` : 세그먼트 분석
- `/projects/[id]/text` : 텍스트 인사이트
- `/projects/[id]/priorities` : Pain Point 우선순위
- `/projects/[id]/report` : 인사이트 리포트
- `/projects/[id]/export` : Export 센터

## 분석 로직 요약
- 스키마 추론: id/metadata/categorical/ordinal/numeric/multi_select/free_text/date
- 데이터 건강도: 결측/중복/의심 필드/정규화 제안
- 정량 분석: 분포/기술통계/세그먼트 비교/상관/드라이버(연관 요인)
- 텍스트 분석: 클러스터/감성/대표 인용문
- 우선순위 모델:
  - `0.4*빈도 + 0.3*심각도 + 0.2*세그먼트집중 + 0.1*타깃연관`

## MVP vs Future
### MVP
- 업로드, 타입 매핑, 분석, 리포트, export, 데모 데이터

### Future
- Next.js 네이티브 차트/컴포넌트 확장
- Python 분석 모듈 분리(서비스 경계 강화)
- 고급 텍스트 임베딩/유의성 검정
