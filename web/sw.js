// 최소 서비스워커: 홈 화면 설치(PWA)용. 실시간 데이터는 캐시하지 않는다.
self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => self.clients.claim());
self.addEventListener('fetch', () => {});
