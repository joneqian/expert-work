# Expert Work Keycloak 主题(白标)

Stream ACCT — 让 Keycloak 登录/邮件页长得像 Expert Work 产品,用户看不出背后是 Keycloak。

## 结构

```
Expert Work/login/
  theme.properties          # parent=keycloak,叠加品牌 CSS
  resources/css/expert_work.css   # 深色 + cyan/violet,对齐 admin-ui 设计基线
```

`theme.properties` 只覆盖 `styles`(追加 `expert_work.css`)。PatternFly 基础样式来自父主题的
`stylesCommon`(**未覆盖,继承保留**),所以布局不会因覆盖而崩。纯 CSS,不改 FreeMarker
模板 —— 升级 Keycloak 不易碎。

## 接线

- `docker-compose.yml` 把本目录挂到 `/opt/keycloak/themes/Expert Work`。
- `realm-expert-work.json` 设 `"loginTheme": "Expert Work"`。
- `start-dev` 关主题缓存,改 CSS 刷新页面即生效(不必重启容器)。

## 改了主题不生效?

`--import-realm` 只在 Keycloak **首次** boot(H2 空)导入。改了 realm 的 `loginTheme`
要重建容器触发重导入:`docker compose --profile auth rm -sf keycloak && docker compose --profile auth up -d keycloak`。仅改 CSS 不需要(热加载)。

## 换 Logo

页面 header 现在渲染 realm `displayName`(=`Expert Work`)作文字 wordmark(cyan→violet 渐变)。
要用图片 logo:把文件放 `login/resources/img/logo.svg`,在 `expert_work.css` 的
`#kc-header-wrapper` 用 `background-image: url(../img/logo.svg)` + 隐藏文字。

## 本地 vs 生产

- **本地**:直接 `localhost:8080` 看到品牌登录页,**不需要域名**。
- **生产**(可选,连地址栏也不露 keycloak):给 Keycloak 配自有域名
  (`KC_HOSTNAME=auth.yourapp.com`)+ 反向代理 + TLS。纯运维,与本主题正交,不影响本地。
