import { notFound } from 'next/navigation';

const tabs = ['data','health','analysis','quant','segments','text','priorities','report','export'] as const;

type Props = { params: { id: string }, searchParams: { tab?: string } };

export default function ProjectPage({ params, searchParams }: Props) {
  const tab = (searchParams.tab as typeof tabs[number] | undefined) ?? 'analysis';
  if (!tabs.includes(tab)) return notFound();
  const src = `http://127.0.0.1:8000/projects/${params.id}/${tab}`;
  return (
    <div className="card">
      <h2>프로젝트 {params.id}</h2>
      <p className="muted">Python 분석 UI를 Next.js 셸 내에서 표시합니다.</p>
      <iframe title="sig-python-view" src={src} style={{ width: '100%', minHeight: '1400px', border: '1px solid #d9e2ec', borderRadius: 8 }} />
    </div>
  );
}
