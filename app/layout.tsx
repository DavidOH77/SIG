import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: '시그(SIG) 설문 분석',
  description: 'UX 리서치 설문 분석 MVP'
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <div className="top">시그(SIG) · Next.js 프론트엔드 셸</div>
        <main>{children}</main>
      </body>
    </html>
  );
}
