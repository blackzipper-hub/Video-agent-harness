# 去掉 URL 里的 `#`：HashRouter → BrowserRouter 改造方案

> 目的：把 `http://localhost:8081/#/zh/create` 这种 URL 改成 `http://localhost:8081/zh/create`。
> 本文先讲为什么现在是 `HashRouter`、不去掉的代价、以及完整改造路径，**不动代码**。

---

## 1. 现状盘点

### 1.1 路由实现

`src/App.tsx:5,56`：

```tsx
import { HashRouter, Routes, Route } from "react-router-dom";
...
// ✅ 使用 HashRouter - URL 格式: /cuti/new/#/en/pricing 或 /cuti/new/#/zh/pricing
// 优点：刷新任何页面都不会 404，无需后端 SPA fallback 支持
<HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
```

明确选择 HashRouter 的原因：**「无需后端 SPA fallback 支持」**——即任何 GET 请求只要打到 index.html 就能跑起来，子路径刷新不会 404。

### 1.2 URL 形状（举例）

| 环境 | 当前 URL | 期望 URL |
|---|---|---|
| 本地 dev | `localhost:8081/#/zh/create` | `localhost:8081/zh/create` |
| 生产 cuti.land（`VITE_BASE_PATH=/`） | `cuti.land/#/zh/create?thread=xxx` | `cuti.land/zh/create?thread=xxx` |
| 旧文档里的子路径示例 | `dev.newai.land/cuti/new/#/zh/pricing` | `dev.newai.land/cuti/new/zh/pricing` |

### 1.3 与 hash 强耦合的代码（**改 router 必须同步改这些地方**）

通过 grep `/#/`、`window.location.hash`、`location.hash` 找出来：

| 类型 | 文件 | 大致位置 | 说明 |
|---|---|---|---|
| 路由组件 | `src/App.tsx` | `:5, :56, :112` | 切 BrowserRouter 的主入口 |
| 分享链接拼接 | `src/components/video/MessageArea.tsx` | `:1418, :1431, :3950, :3980`（共 4 处） | image share URL 写死 `${basePath}/#/${language}/share/image?...` |
| 分享链接拼接 | `src/components/video/ImageResultsPanel.tsx` | `:132, :145, :1041, :1048, :1108, :1110`（共 6 处） | 同上 |
| 支付回调 URL | `src/components/PricingContent.tsx` | `:159, :160, :198, :199` | Stripe `success_url` / `cancel_url` 含 `#/...` |
| Admin 跳转 | `src/pages/admin/SmartTestingPage.tsx` | `:1999, :2002, :2203, :2206` | 4 处 `${BASE_URL}/#/${adminLang}/admin?tab=...` |
| 运行时判定 | `src/components/CreateVideoPage.tsx` | `:397, :3009, :6873, :8326` | `window.location.hash.includes('/create')`——切换后 `pathname` 替代 |
| Hash 重定向辅助 | `src/components/LanguageRedirect.tsx` | 整文件 | 当前根据 `location.hash` 推断目标路径，BrowserRouter 后改为根据 `location.pathname` |
| 语言切换 | `src/i18n/LanguageContext.tsx` | `:69, :78` | 拼新路径时附带 `location.hash` — 切换后 hash 通常为空 |
| 语言路由 | `src/components/LanguageRoute.tsx` | `:23, :32` | 同上 |

共约 **10 个文件、25+ 处** 直接或间接依赖 hash 路由形状。其中：
- **运行时判定**（CreateVideoPage 那 4 处）：改 router 后行为会变，必须改。
- **链接拼接**（MessageArea / ImageResultsPanel / PricingContent / SmartTestingPage）：改 router 后必须去掉 `#`，否则新链接会带不必要的 `#`，老链接虽然能被 hash → history 兼容层 redirect，但新链接不应再生成。
- **`location.hash` 附加**（LanguageContext / LanguageRoute）：BrowserRouter 模式下 hash 通常为空字符串，附加无害但可以去掉。

### 1.4 后端 / 部署

| 项目 | 是否服务前端静态 | 当前行为 |
|---|---|---|
| `Cuti-VideoAgent` (FastAPI, 9003) | 否 | 只挂 `/api/cuti` 接口；前端 dist 不由它服务 |
| Go backend `cuti.land` | 是 | `VITE_BASE_PATH=/` 直接挂根；目前没有 SPA fallback（因为 HashRouter 不需要） |
| Nginx on `dev.newai.land` | 部分 | `/api/cv-v1` → K8s、`/api/cuti` → EC2 |

**结论：去掉 `#` 必须让某个 HTTP 层为所有未匹配 GET 请求 fallback 到 `index.html`。** 这是改造的最大成本之一。

---

## 2. 不去掉 `#` 的代价（现状的 5 个痛点）

1. **URL 不像现代 web app**：SEO 几乎为零（hash 后内容搜索引擎不索引）；社交平台抓取 OG 标签时部分（如 Twitter Card 老版本）会忽略 hash 后参数。
2. **统计 / 后端日志没法直接看路径**：服务端只看到 `GET /`，看不到用户在哪个页面（虽然有前端埋点，但是后端日志失明）。
3. **服务端没法做 server-side redirect**：比如 `/zh/pricing` 想 308 到 `/zh/subscription`，HashRouter 下做不到，因为后端从来收不到 hash。
4. **Stripe / 第三方回调 URL 看起来怪**：`success_url=https://cuti.land/#/payment/success?session_id=...`，hash 在 query 之前，部分平台日志/邮件会显示不全。
5. **未来要做 SSR / 边缘渲染基本无门**：HashRouter 是纯 CSR 模型，将来想上 Vite SSR / Next.js 迁移会被绊住。

---

## 3. 改造路径（两种）

### 方案 A：一次性切到 BrowserRouter（彻底）

**改动清单**（按顺序）：

1. **`src/App.tsx`**：
   - `import { BrowserRouter, ... }`
   - `<BrowserRouter basename={import.meta.env.BASE_URL}>`（用 vite 注入的 `BASE_URL`，与 `VITE_BASE_PATH` 一致）
   - 删掉 HashRouter 那条注释、`future` 字段一并迁过去（v6 兼容）

2. **`src/components/LanguageRedirect.tsx`**：
   - 现在用 `location.hash.replace('#', '')` 推路径；改成直接用 `location.pathname` 推
   - 逻辑保留（去重复语言前缀、加默认语言前缀）

3. **`src/components/LanguageRoute.tsx` + `src/i18n/LanguageContext.tsx`**：
   - 拼新路径时去掉 `${location.hash}` 后缀（BrowserRouter 下 hash 通常无意义）

4. **`src/components/CreateVideoPage.tsx`** 4 处 `window.location.hash.includes('/create')`：
   - 改为 `window.location.pathname.includes('/create')` 或用 `useLocation().pathname`
   - **注意**：注意 basename（如生产 `/`，dev `/`，未来若改 `/cuti/new/`）下 pathname 形状会变，需要测试

5. **链接拼接（共约 14 处）**：
   - `MessageArea.tsx`、`ImageResultsPanel.tsx`、`PricingContent.tsx`、`SmartTestingPage.tsx` 中所有 `${basePath}/#/...` 改为 `${basePath}/...`
   - 注意 `basePath` 末尾斜杠的处理：建议提一个 `buildAppUrl(path: string)` helper 集中处理，避免多写多错
     ```ts
     // 建议放 src/utils/url.ts
     export function buildAppUrl(pathFromRoot: string): string {
       const origin = window.location.origin;
       const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '');
       const path = pathFromRoot.startsWith('/') ? pathFromRoot : '/' + pathFromRoot;
       return `${origin}${base}${path}`;
     }
     ```

6. **`vite.config.ts`**：
   - `server.proxy` 不动
   - 但需要在 `server` 加 SPA fallback（dev 下 Vite 默认已经做了——任何未命中文件 + 非 `/api/*` 的 GET 会返回 index.html，**这点要在 dev 验证**）
   - `preview` 同理需要确认

7. **部署侧（最大风险点）**：
   - **本地 dev (Vite)**：默认 OK，但要确保 `/zh/create` 不会撞到 `/api` 代理规则；当前 `/api/*` 已显式 proxy，不会冲突
   - **`cuti.land`（Go backend）**：需要加一条「未匹配 GET → serve `dist/index.html`」的规则。这是 Go 后端那边的改动
   - **`dev.newai.land`（Nginx）**：需要加
     ```nginx
     location / {
       try_files $uri $uri/ /index.html;
     }
     ```
     **且要确保 `/api/cv-v1` 和 `/api/cuti` 的代理规则在 try_files 之前**
   - 任何托管平台（如 Cloudflare Pages / Vercel）：勾选 SPA 模式

8. **OG meta（`vite.config.ts:37-69`）**：
   - 当前 `og:url` 用 `siteUrl + base`，改后形状变化不大，但分享路径需要包含完整 history path 才有意义。可以在某些页面里单独覆盖 og:url meta。
   - 优先级低，先按现状即可。

**风险与已分享出去的链接**：

- 老的分享链接 / Stripe 历史回调 URL 都是 `https://cuti.land/#/xxx` 形式
- 切完 BrowserRouter 后，浏览器打开老链接时：
  - 浏览器只会请求 `https://cuti.land/`（hash 不上传），服务器返回 SPA 首页
  - 首页 JS 起来后看到 `location.hash = "#/zh/pricing"`，但 BrowserRouter 不再消费 hash
  - 结果：用户停在首页 SelectionHub，**老链接失效**

→ 必须做 **hash → history 兼容层**（见方案 C）。

---

### 方案 C：渐进式切换（**推荐**）

在方案 A 的基础上 **加一个 hash 兼容重定向**，让老链接不失效。

#### C.1 新增一段早期重定向脚本（在 React 挂载之前跑）

放在 `index.html` 的 `<head>` 或在 `src/main.tsx` 顶部：

```html
<!-- index.html 里，<script type="module" src="/src/main.tsx"> 前面 -->
<script>
  (function () {
    var h = window.location.hash;
    if (h && h.startsWith('#/')) {
      var base = '__VITE_BASE_PATH__'.replace(/\/$/, ''); // 构建时替换或运行时读 <base>
      var newPath = base + h.slice(1); // '#/zh/pricing' → '/zh/pricing'
      var search = window.location.search || '';
      var origin = window.location.origin;
      window.location.replace(origin + newPath + search);
    }
  })();
</script>
```

或者在 `src/main.tsx` 用 ES 模块版本（轻微闪烁但更可控）。

效果：

| 老链接 | 自动跳转到 |
|---|---|
| `cuti.land/#/zh/pricing` | `cuti.land/zh/pricing` |
| `cuti.land/#/payment/success?session_id=x` | `cuti.land/payment/success?session_id=x` |

#### C.2 推进步骤（按周）

| 阶段 | 改动 | 风险 | 验证 |
|---|---|---|---|
| W1 | 后端 / Nginx 加 SPA fallback（**先做、独立 PR**） | 中：fallback 配错会让 404 也返回 index.html | 用 curl 验证 `/zh/create`、`/non-exist-api/foo` 行为 |
| W2 | 加 `buildAppUrl` helper 并把所有链接拼接处替换 | 低：链接形状变了但还是 hash | 可单独合入，本期产物里 hash 形式不变 |
| W3 | 切 `App.tsx` 到 `BrowserRouter` + 改 `CreateVideoPage` 等运行时判定 + 改 `LanguageRedirect` + 同时上线 hash 兼容脚本 | **高**：本步是用户可感知的切换 | 内部 staging 全流程跑通 + 老分享链接 + Stripe 回调 + Admin 跳转 |
| W4 | 移除链接拼接里的 `#`（依赖 W3 已稳定） | 低 | 新生成的 URL 干净 |
| W+1 月 | 视情况移除 hash 兼容脚本（如果还有用户使用老链接就保留） | 低 | — |

---

## 4. 推荐：方案 C

理由：

1. **老链接不失效**：Stripe 已经在用户邮箱里的回执 URL、用户已经分享出去的图片 share URL、Admin 同事收藏的 tab 跳转 URL、社交平台抓取的 OG 链接，都靠 hash 兼容脚本兜住。
2. **可以分多个 PR / 分多周上线**：先把后端 fallback 准备好（独立无影响），再切 router；任何一步出问题都能 rollback。
3. **回滚成本低**：W3 那个 PR 单独 revert 即可回到 HashRouter；后端 fallback 配置保留也无害。

不推荐方案 B（"用 history.replaceState 把 # 隐藏起来"那种 hack）：
- 它仍然是 HashRouter，所有 `location.hash` 同步问题没解
- 直接访问无 `#` URL 仍然会因为没有后端 fallback 而 404
- 给后续真正切换 router 留坑

---

## 5. 改完之后的 Checklist

- [ ] 本地 dev：访问 `localhost:8081/zh/create?thread=xxx` 直接生效；F5 刷新不 404
- [ ] 本地 dev：访问老链接 `localhost:8081/#/zh/create?thread=xxx` 自动 replace 到无 # 版本
- [ ] 本地 dev：`/api/cuti/*` 代理仍正常（不被 SPA fallback 吞掉）
- [ ] 任意未知路径如 `localhost:8081/zh/foo-bar-baz` 进入 NotFound 页（而不是 nginx/Go 404）
- [ ] 生产 staging：cuti.land（或 dev.newai.land）同上述全部场景验证
- [ ] Stripe checkout 走完一次：success_url / cancel_url 使用新的无 `#` 形态，能正确回到本站
- [ ] 老 Stripe 回执（hash 形态）点击：自动跳转到新形态
- [ ] image share 链接生成 + 打开：无 `#`
- [ ] Admin tab 跳转链接：从老 hash 形态自动 redirect 到 history 形态
- [ ] `CreateVideoPage` 里 4 处 `hash.includes('/create')` 行为不变（thread 切换、卡片展开）
- [ ] 语言切换：`/zh/foo` → `/en/foo` 路径正确，无 `#` 残留
- [ ] OG meta：分享到微信/Twitter 看预览图、标题、跳转都正常

---

## 6. 不在本方案范围内（顺手记一下，将来可做）

- React Router v6 → v7 升级（当前已开了 `future.v7_*` flag，做好准备）
- SSR / 静态预渲染（提高 SEO；切完 BrowserRouter 才有意义）
- 路由层把 `/:lang` 升级为 `i18n locale resolver`（目前 `LanguageRoute` 已经基本做到）

---

## 7. 决策点（需要你拍板）

1. **方案 A（一次性切）还是方案 C（渐进切）？** —— 我推荐 C。
2. **后端 SPA fallback 谁来做？**
   - `cuti.land`（Go backend）：需要 Go 同事或我们这边动 Go 代码？
   - `dev.newai.land`（Nginx）：需要运维改 Nginx 配置？
3. **本期是否做 `buildAppUrl` helper 收拢链接拼接？** 我建议做，否则 14 处分散修改容易漏。
4. **hash 兼容脚本保留多久？** 建议至少 1 个月（覆盖一次 Stripe 月度账单 + 大部分分享链接的活跃期），之后看埋点数据决定。

你确认这几个点后，我就把改动按 W1 → W4 拆成多个 PR 推进。
