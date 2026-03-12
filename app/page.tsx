import Link from 'next/link';
import QuickLinks from '@/components/QuickLinks';

export default function HomePage() {
  return (
    <>
      <div className="card">
        <h1>시그(SIG) MVP</h1>
        <p>
          이전 커밋은 Python 단일 서버 중심이었고, 이 커밋에서 Next.js App Router 셸을 추가했습니다.
          실제 분석 엔진은 Python 서버(`server.py`)가 담당합니다.
        </p>
        <p className="muted">실행: Python 서버 8000 + Next 서버 3000(선택)</p>
        <Link href="/projects/1" className="btn">데모 프로젝트 보기</Link>
      </div>
      <QuickLinks />
    </>
  );
}
