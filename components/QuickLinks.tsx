import Link from 'next/link';

const links = [
  ['프로젝트 개요', '/projects/1'],
  ['데이터 업로드', '/projects/1/data'],
  ['데이터 건강도', '/projects/1/health'],
  ['분석 개요', '/projects/1/analysis'],
  ['정량 분석', '/projects/1/quant'],
  ['세그먼트 분석', '/projects/1/segments'],
  ['텍스트 인사이트', '/projects/1/text'],
  ['우선순위', '/projects/1/priorities'],
  ['리포트', '/projects/1/report'],
  ['내보내기', '/projects/1/export']
] as const;

export default function QuickLinks() {
  return (
    <div className="card">
      <h3>IA 빠른 이동</h3>
      <div className="grid">
        {links.map(([label, href]) => (
          <Link key={href} href={href} className="btn">
            {label}
          </Link>
        ))}
      </div>
    </div>
  );
}
