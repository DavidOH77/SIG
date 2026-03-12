# SIG 아키텍처 노트

## 1. MVP 아키텍처
현재 구현은 실행 환경 제약(외부 패키지 제한) 때문에 단일 Python 앱으로 구성했습니다.

- `server.py`
  - 라우팅 + 렌더링 + 분석 엔진
  - 업로드 파싱(csv/xlsx)
  - DB CRUD(sqlite)
  - 리포트/내보내기
- `data/sig.db`
  - 프로젝트 메타데이터
- `data/*.json`
  - 원본 데이터셋, 스키마, 분석 결과 캐시

## 2. 요청 흐름
1. 프로젝트 생성
2. 파일 업로드
3. 파싱 + 컬럼 타입 추론
4. 데이터/스키마 저장
5. 건강도/정량/텍스트/우선순위/리포트 계산
6. 페이지별 시각화/내보내기

## 3. explainable analytics 설계
모든 인사이트는 다음 근거에 연결되도록 구성:
- 정량 통계 테이블
- 세그먼트별 수치 차이
- 텍스트 클러스터 빈도/감성/대표 인용문
- 우선순위 점수 구성요소

과도한 인과 주장 방지를 위해 드라이버 분석은 “연관 요인”으로만 표기.

## 4. 미래 확장 권장 구조 (목표 스택)
- Frontend: Next.js App Router + Tailwind + 컴포넌트 시스템
- API: Next.js route handlers
- Analysis: Python 서비스(pandas/scikit/statsmodels)
- Storage: PostgreSQL + object storage(S3)
- Background Jobs: Celery/RQ 혹은 queue worker
- LLM layer: 계산 결과를 grounding context로만 전달하는 요약기

## 5. 모듈 경계 제안
- ingestion: 파일 파싱, 스키마 추론, 정합성 검사
- analytics.quant: 통계/세그먼트/상관/드라이버
- analytics.text: 텍스트 전처리/클러스터/감성
- insight_engine: findings/hypothesis/action 생성
- export: markdown/csv/html report

