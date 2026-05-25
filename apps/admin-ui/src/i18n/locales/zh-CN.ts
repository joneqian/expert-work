/**
 * 简体中文翻译 — Stream H.1b PR 2a。
 *
 * 术语表(Agent / Run / Skill / Trigger / Manifest / Memory /
 * Curation / Eval / Tenant / API Key / Audit / Sandbox / Service
 * Account / Playground / Trace / Span / Quota / Volume)在两个语言中
 * 都保留原文 — 让操作人员在 helix 文档和 UI 之间使用同一套规范名词。
 */
import type { TranslationKeys } from "./en";

const zhCN: TranslationKeys = {
  common: {
    sign_in: "登录",
    sign_out: "退出登录",
    refresh: "刷新",
    loading: "加载中…",
    cancel: "取消",
    confirm: "确定",
    save: "保存",
    delete: "删除",
    search_or_jump: "搜索或跳转",
    notifications: "通知",
    user_menu: "用户菜单",
    home: "首页",
    anonymous: "匿名用户",
  },
  theme: {
    switch_to_light: "切到 Light",
    switch_to_dark: "切到 Dark",
    toggle: "切换主题",
  },
  nav: {
    settings_group: "Settings",
  },
  login: {
    title: "helix Admin",
    paragraph:
      "粘贴你的 OIDC JWT 或 helix API Key 登录。两种凭据都仅保存在本地浏览器,control-plane 会在每个请求重新校验。",
    token_label: "Token",
    token_placeholder: "eyJ… (JWT)   或   aforge_pat_… (helix API Key)",
    token_required: "Token 必填",
    token_empty: "Token 不能为空",
    pr2_hint: "OIDC code-flow 登录会在 H.1b PR 2 落地,详见",
    sign_in_sso: "用 SSO 登录",
    sso_help: "你将被跳转到所在组织的身份认证服务(IdP)。",
    dev_login_toggle: "开发者登录(粘贴 Token)",
    dev_login_hide: "收起开发者登录",
    dev_login_section: "开发者登录",
  },
  auth_callback: {
    title: "登录中…",
    exchanging: "正在用授权码向 IdP 换取 Token。",
    failed: "登录失败",
  },
  tenant: {
    home_label_prefix: "Home",
    home_tenant: "Home tenant",
    all_tenants: "All tenants",
    your_tenant: "你所属的 Tenant",
    system_admin_hint: "system admin",
    cross_tag: "cross",
  },
  agents_page: {
    page_title: "Agents",
    cross_tenant_banner: "跨 Tenant 视图",
    failed_to_load: "Agent 列表加载失败",
    empty_home: "当前 Tenant 还没有 Agent。使用 POST /v1/agents 创建。",
    empty_cross: "所有 Tenant 都还没有 Agent。",
    column_name: "名称",
    column_status: "状态",
    column_tenant: "Tenant",
    column_created: "创建时间",
  },
  coming_soon: {
    title_prefix: "此页面在 H.1b 正式实施中",
    body: "demo 仅演示 4 个核心页面:Agents 列表 / Agent 详情(含 Playground)/ Run+Approval / Settings API Keys。",
    other_pages_prefix: "其他页面",
    other_pages_suffix: "将在 Stream H 全面落地。",
  },
  cmdk: {
    placeholder: "搜索或跳转 — 输入命令、Agent 名、Run ID…",
    aria_label: "命令面板搜索",
    no_matches: "没有匹配项",
    group_agents: "Agents",
    group_jump: "跳转",
    group_action: "动作",
    label_runs: "Runs(跨 Agent)",
    label_curation: "Curation 评审",
    label_memory: "Memory",
    label_skills: "Skills",
    label_triggers: "Triggers",
    label_settings_api_keys: "Settings · API Keys",
    action_create_agent: "创建新 Agent…",
    action_create_api_key: "创建新 API Key…",
    action_open_settings: "打开 Settings",
    hint_select: "选择",
    hint_jump: "跳转",
    hint_close: "关闭",
    hint_shortcuts: "输入 ? 查看快捷键",
  },
};

export default zhCN;
