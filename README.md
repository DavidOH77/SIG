# 시그(SIG) - UX 설문 분석 MVP

시그(SIG)는 **설문 수집 도구가 아닌, 업로드된 설문 시트 분석 도구**입니다.  
한국 스타트업 실무자가 Excel/Sheets에서 하던 분석 업무를 빠르게 자동화하도록 설계했습니다.

## 핵심 가치
- CSV/XLSX 업로드 후 자동 구조 파악
- 데이터 건강도 점검
- 정량/세그먼트 분석
- 자유서술 텍스트 클러스터/감성 분석
- Pain Point 우선순위 스코어링 (설명 가능한 공식)
- 근거 연결형 인사이트 리포트 + 내보내기

## 기술 선택 (MVP 현실화 버전)
이 저장소 실행 환경에서는 외부 패키지 설치가 제한되어, **표준 라이브러리 기반 Python WSGI 앱**으로 구현했습니다.
- 서버: Python `wsgiref`
- 저장소: SQLite (`sqlite3`)
- 파일 처리: `csv`, `zipfile+xml` (xlsx 1시트 파싱)
- 분석: 통계/상관/세그먼트/텍스트 휴리스틱을 직접 구현

> 권장 스택(Next.js + pandas/scikit)은 `docs/architecture.md`의 Future plan에 정리했습니다.

## 실행 방법
```bash
python server.py
```
- 접속: http://localhost:8000
- 최초 실행 시 데모 프로젝트 자동 생성


## 화면이 안 보일 때 체크
- 서버 실행 로그 확인: `python server.py` 실행 후 `SIG 서버 실행: http://localhost:8000` 출력 확인
- 브라우저 주소 확인: `http://localhost:8000`
- HEAD 요청도 200을 반환하도록 처리되어 헬스체크 환경에서도 화면 접근이 가능합니다.

## 페이지 IA
- `/` : 홈/프로젝트 목록
- `/projects/[id]` : 프로젝트 개요
- `/projects/[id]/data` : 업로드/프리뷰/컬럼 타입 매핑/타깃 지표 선택
- `/projects/[id]/health` : 데이터 건강도
- `/projects/[id]/analysis` : 분석 개요
- `/projects/[id]/quant` : 정량 분석
- `/projects/[id]/segments` : 세그먼트 분석
- `/projects/[id]/text` : 텍스트 인사이트
- `/projects/[id]/priorities` : Pain Point 우선순위
- `/projects/[id]/report` : 인사이트 리포트
- `/projects/[id]/export` : 내보내기

## 분석 로직 요약
### 1) 컬럼 타입 추론
- 숫자 비율, 고유값 비율, 텍스트 길이, 날짜 패턴, multi-select 구분자 패턴 기반
- 타입: `id/metadata/categorical/ordinal/numeric/multi_select/free_text/date`
- UI에서 수동 오버라이드 가능

### 2) 데이터 건강도
- 행/열 수
- 컬럼별 결측 수/비율
- 응답자 ID 중복
- 상수값/결측 과다 컬럼 탐지
- 정규화 제안(값 표준화, Likert 범위 점검 등)

### 3) 정량 분석
- 분포, 기술통계(mean/median/std)
- 세그먼트별 평균 비교 (전체 대비 delta)
- 수치형/순서형 상관행렬(피어슨)
- 타깃 지표 설정 시 드라이버형 연관 분석
  - **주의 문구**: 연관 요인이며 인과관계 아님

### 4) 텍스트 분석
- 자유서술 컬럼 탐지
- 한국어/영문 토큰 단위 정규화
- 키워드+토큰 기반 간단 클러스터링
- 긍/중립/부정 감성 분류
- 대표 인용문 추출
- 세그먼트별 부정 텍스트 비율

### 5) Pain Point 우선순위 모델
공식:
```text
Priority Score =
0.4 * normalized_frequency
+ 0.3 * severity_proxy
+ 0.2 * segment_risk_concentration
+ 0.1 * target_metric_association
```
- `frequency`: 전체 대비 이슈 출현량
- `severity`: 부정 감성 중심 점수
- `segment_risk_concentration`: 세그먼트 집중/확산 리스크
- `target_metric_association`: 타깃지표 연관도 참고치

## 내보내기
- Markdown 요약 (`/export/md`)
- 인쇄용 보고서 화면 (`/report`)
- 정제 데이터 CSV (`/export/clean.csv`)
- Pain Point CSV (`/export/pain_points.csv`)

## 샘플 데이터
자동 생성 데모 설문 필드:
- respondent_id
- age_group
- user_type
- region
- plan_type
- tenure_months
- overall_satisfaction
- ease_of_use
- pricing_satisfaction
- support_satisfaction
- likelihood_to_recommend
- main_goal_achieved
- biggest_frustration_text
- improvement_request_text

## MVP 범위 vs 향후 계획
### MVP 포함
- 업로드/매핑/건강도/정량/세그먼트/텍스트/우선순위/리포트/내보내기

### Future
- Next.js + Python 분석 마이크로서비스 구조 분리
- 임베딩 기반 텍스트 클러스터링 고도화
- 통계적 유의성 검정 강화
- 인증/권한/협업 공유 링크
- 리포트 스냅샷 버저닝
