export default function manifest() {
  return { name: '배터리체크', short_name: '배터리체크', description: '내 제품의 확인과 다음 행동',
    start_url: '/', scope: '/', display: 'standalone', background_color: '#f3f7f4', theme_color: '#214d3b',
    lang: 'ko', icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }] };
}
