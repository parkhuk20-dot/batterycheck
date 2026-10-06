import './globals.css';

export const metadata = {
  title: '배터리체크 | 내 제품의 확인과 다음 행동',
  description: '라벨 인식, 공식 리콜 공지 후보, 제품별 확인 이력과 후속 조치를 연결합니다.',
  manifest: '/manifest.webmanifest',
  icons: { icon: '/icon.svg', apple: '/icon.svg' },
};

export const viewport = { themeColor: '#214d3b', width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }) {
  return <html lang="ko"><body>{children}</body></html>;
}
