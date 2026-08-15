# WEB-FE 를 백엔드에 붙이려면 — 수정안

관리자웹을 `backend-python` 에 연결하는 데 필요한 변경. **아직 적용하지 않았다.**
WEB-FE 는 되도록 건드리지 않기로 했으므로 확인 후 직접 반영한다.

고칠 파일은 **두 개, 각 한 군데**다.

---

## 왜 지금은 안 붙나

두 가지가 겹쳐 있다.

### ① 가짜 API 가 무조건 켜진다

```typescript
// WEB-FE/src/main.tsx:7
async function enableMocking() {
  if (!import.meta.env.DEV) return          // ← npm run dev 면 항상 통과
  const { worker } = await import('@/mocks/browser')
  await worker.start({ onUnhandledRequest: 'bypass' })
}
```

`npm run dev` 는 `import.meta.env.DEV` 가 언제나 `true` 다. MSW 가 켜져
`/api/*` 를 전부 가로채므로 **백엔드로 요청이 한 건도 안 나간다.**
서버를 같이 띄워도 화면은 mock 데이터를 보여준다.

### ② 나가더라도 갈 곳이 없다

```
.env             VITE_API_BASE_URL=/api      ← 상대경로
vite.config.ts   proxy 설정 없음
```

`/api/...` 는 vite 서버(`:5173`)로 가는데 거기엔 API 가 없다. 404 가 난다.

---

## 수정 1 — 가짜 API 를 끌 수 있게

`src/main.tsx` 의 조건에 환경변수 하나를 더한다.

```diff
 async function enableMocking() {
-  if (!import.meta.env.DEV) return
+  // VITE_USE_MOCK=false 면 실제 백엔드로 보낸다.
+  // 기본값은 그대로 mock — 백엔드 없이 화면만 보는 흐름을 깨지 않는다.
+  if (!import.meta.env.DEV) return
+  if (import.meta.env.VITE_USE_MOCK === 'false') return
   const { worker } = await import('@/mocks/browser')
   await worker.start({ onUnhandledRequest: 'bypass' })
 }
```

**기본 동작은 안 바뀐다.** 환경변수를 안 주면 지금과 똑같이 mock 이 뜬다.
백엔드에 붙일 때만 `.env.local` 에 한 줄 넣는다.

```ini
# WEB-FE/.env.local  (git 에 안 올라가는 파일)
VITE_USE_MOCK=false
```

> `.env` 를 직접 고치지 말고 `.env.local` 을 쓰는 편이 낫다. 팀원마다 백엔드를
> 띄우는지가 다른데, `.env` 를 고치면 그게 커밋에 섞여 남의 화면이 깨진다.

## 수정 2 — `/api` 를 백엔드로 보내기

`vite.config.ts` 에 프록시를 더한다.

```diff
 export default defineConfig({
   plugins: [react(), tailwindcss()],
   resolve: {
     alias: {
       '@': path.resolve(__dirname, './src'),
     },
   },
+  server: {
+    proxy: {
+      // 백엔드(uvicorn :8000)로 넘긴다. mock 이 켜져 있으면 여기까지 오지 않으므로
+      // 이 설정을 둬도 기존 동작에는 영향이 없다.
+      '/api': 'http://127.0.0.1:8000',
+    },
+  },
   test: {
     environment: 'jsdom',
     setupFiles: ['./src/test/setup.ts'],
     globals: true,
   },
 })
```

**프록시를 쓰는 이유는 CORS 때문이다.** `VITE_API_BASE_URL` 을
`http://localhost:8000/api` 로 바꿔도 되지만, 그러면 브라우저가 교차 출처로 보고
사전 요청(preflight)을 보낸다. 서버의 허용 목록이 지금 이렇게 되어 있다.

```python
# backend-python/app/config.py
cors_origins: str = "http://localhost:5173"
```

`127.0.0.1:5173` 으로 열면 이 목록에 없어서 막힌다. 주소를 어떻게 여는지에 따라
되다 안 되다 하는 건 디버깅하기 나쁘다. 프록시로 보내면 브라우저 입장에서는
같은 출처라 이 문제가 아예 안 생긴다.

---

## 실행 순서

```bash
# 1) 백엔드 (Postgres 가 떠 있어야 한다)
cd backend-python
uvicorn app.main:app --reload

# 2) 데이터 넣기 — 실측 프로젝트 파일을 DB 로
python tests/seed_from_project.py
#    → seed@hanium.local / seed1234 계정이 만들어진다

# 3) 관리자웹
cd ../WEB-FE
npm run dev
```

로그인은 시드가 만든 계정으로 한다. **회원가입으로는 못 들어간다** — 아래 참고.

---

## 붙이고 나면 달라 보이는 것들

미리 알고 있어야 "고장났다"로 오해하지 않는다.

### 화면이 비어 있다

mock 은 건물·층·비콘이 미리 채워진 상태였다. 실제 DB 는 시드로 넣은 것만 있다.

### 회원가입해도 로그인이 안 된다

```python
# app/admin/service.py:signup
status="pending",   # 슈퍼관리자 승인 전까지 로그인 불가

# app/admin/service.py:login
if admin.status != "active":
    raise HTTPException(403, "승인 대기 중이거나 거절된 계정입니다.")
```

승인하려면 이미 로그인한 `super_admin` 이 있어야 한다. **첫 계정을 만들 방법이
REST 에 없다.** 시드 스크립트가 그래서 첫 계정만 DB 에 직접 넣는다.

> 운영에는 이대로 못 간다. 첫 관리자를 만드는 수단(설치 스크립트나 환경변수
> 부트스트랩)이 있어야 한다. 지금은 시드로 때운다.

### 아직 서버에 없는 화면이 있다

프론트에는 있으나 백엔드가 안 만든 것들이다. 부르면 404 가 난다.

| 화면 | 상태 |
|---|---|
| 시각태그 | `visual_tag_id` 컬럼만 있고 기능 미구현 |
| 설치 가이드 | 미구현 |

### 응답이 느릴 수 있다

설계도·마스크를 data URL 로 통째 주고받는다. 실측 평면도가 2.5MB 라 첫 조회가
눈에 띄게 걸린다. mock 은 메모리에서 꺼내 즉시 돌려줬다.

---

## 되돌리기

두 변경 다 기본 동작을 안 바꾸므로 되돌릴 일은 거의 없지만,
`.env.local` 을 지우면 즉시 mock 으로 돌아간다.

```bash
rm WEB-FE/.env.local
```

프록시는 mock 이 켜져 있으면 거기까지 요청이 가지 않아 그대로 둬도 무해하다.
