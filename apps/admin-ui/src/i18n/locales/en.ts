/**
 * English translations — Stream H.1b PR 2a.
 *
 * Glossary terms (Agent / Run / Skill / Trigger / Manifest / Memory /
 * Curation / Eval / Tenant / API Key / Audit / Sandbox / Service
 * Account / Playground / Trace / Span / Quota / Volume) are intentionally
 * kept un-translated in both locales — operators reading helix docs +
 * runtime UI in two languages benefit from a single canonical noun.
 */
export interface TranslationKeys {
  common: {
    sign_in: string;
    sign_out: string;
    refresh: string;
    loading: string;
    cancel: string;
    confirm: string;
    save: string;
    delete: string;
    edit: string;
    search_or_jump: string;
    notifications: string;
    user_menu: string;
    anonymous: string;
  };
  theme: {
    switch_to_light: string;
    switch_to_dark: string;
    toggle: string;
  };
  nav: {
    settings_group: string;
    group_workspace: string;
    group_tenant_settings: string;
    group_platform: string;
    members_all_tenants: string;
    agents: string;
    runs: string;
    approvals: string;
    curation: string;
    eval: string;
    memory: string;
    artifacts: string;
    knowledge: string;
    skills: string;
    skill_marketplace: string;
    triggers: string;
    webhooks: string;
    tenants: string;
    platform_credentials: string;
    platform_users: string;
    api_keys: string;
    credentials: string;
    service_accounts: string;
    members: string;
    audit: string;
    mcp_servers: string;
    mcp_catalog: string;
    platform_skills: string;
    usage: string;
    chargeback: string;
    rate_card: string;
  };
  login: {
    title: string;
    paragraph: string;
    token_label: string;
    token_placeholder: string;
    token_required: string;
    token_empty: string;
    pr2_hint: string;
    sign_in_sso: string;
    sso_help: string;
    dev_login_toggle: string;
    dev_login_hide: string;
    dev_login_section: string;
  };
  auth_callback: {
    title: string;
    exchanging: string;
    failed: string;
  };
  setup: {
    title: string;
    paragraph: string;
    platform_name_label: string;
    admin_email_label: string;
    admin_email_required: string;
    admin_email_invalid: string;
    admin_password_label: string;
    admin_password_required: string;
    admin_password_length: string;
    admin_password_confirm_label: string;
    admin_password_confirm_required: string;
    admin_password_mismatch: string;
    admin_display_name_label: string;
    setup_token_label: string;
    setup_token_hint: string;
    setup_token_required: string;
    notice: string;
    submit: string;
    success_toast: string;
    done_title: string;
    done_subtitle: string;
    go_to_login: string;
    already_initialized_title: string;
    already_initialized_subtitle: string;
    error_already_initialized: string;
    error_invalid_token: string;
    error_not_configured: string;
    error_email_exists: string;
    error_keycloak_unavailable: string;
    error_generic: string;
  };
  agent_detail: {
    failed_to_load: string;
    tab_overview: string;
    tab_manifest: string;
    tab_playground: string;
    tab_runs: string;
    tab_skills: string;
    tab_triggers: string;
    tab_memory: string;
    tab_history: string;
    tab_coming_soon: string;
    config_summary: string;
    field_id: string;
    field_tenant: string;
    field_spec_sha: string;
    field_status: string;
    field_created: string;
    field_updated: string;
  };
  history_tab: {
    title: string;
    select_hint: string;
    current: string;
    col_revision: string;
    col_sha: string;
    col_actor: string;
    col_time: string;
    rollback: string;
    rollback_confirm_title: string;
    rollback_confirm_body: string;
    diff_label: string;
    diff_loading: string;
  };
  runs_tab: {
    title: string;
    window_capped: string;
    empty: string;
  };
  skills_tab: {
    title: string;
    authored_hint: string;
    col_name: string;
    col_status: string;
    col_visibility: string;
    col_version: string;
    col_created: string;
    empty: string;
  };
  triggers_tab: {
    title: string;
    manage_hint: string;
    col_name: string;
    col_kind: string;
    col_enabled: string;
    col_source: string;
    col_created: string;
    enabled: string;
    disabled: string;
    empty: string;
  };
  memory_tab: {
    title: string;
    user_scope_note: string;
    filter_kind: string;
    filter_kind_all: string;
    col_kind: string;
    col_content: string;
    col_user: string;
    col_created: string;
    empty: string;
  };
  knowledge_page: {
    page_title: string;
    subtitle: string;
    home_scope_note: string;
    failed_to_load: string;
    bases_title: string;
    bases_empty: string;
    create_base: string;
    create_duplicate: string;
    field_name: string;
    field_chunk_max: string;
    field_chunk_overlap: string;
    field_chunk_hint: string;
    col_base_name: string;
    col_chunking: string;
    delete: string;
    delete_base_confirm_title: string;
    delete_base_confirm_body: string;
    documents_title: string;
    documents_unselected: string;
    documents_empty: string;
    select_hint: string;
    upload: string;
    unsupported_type: string;
    embedder_missing: string;
    col_filename: string;
    col_status: string;
    col_chunks: string;
    col_updated: string;
    delete_doc_confirm_title: string;
  };
  rate_card_page: {
    page_title: string;
    subtitle: string;
    system_admin_only: string;
    failed_to_load: string;
    empty: string;
    filter_provider: string;
    filter_model: string;
    include_expired: string;
    create: string;
    edit: string;
    edit_title: string;
    delete: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
    col_provider: string;
    col_model: string;
    col_plan: string;
    col_input: string;
    col_output: string;
    col_markup: string;
    col_effective: string;
    all_plans: string;
    open_ended: string;
    field_provider: string;
    field_model: string;
    field_input: string;
    field_output: string;
    field_cache_creation: string;
    field_cache_read: string;
    field_markup: string;
    field_plan: string;
    field_effective_from: string;
    field_effective_until: string;
    until_after_from: string;
    identity_immutable: string;
  };
  artifacts_page: {
    page_title: string;
    subtitle_home: string;
    subtitle_cross: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    col_name: string;
    col_kind: string;
    col_latest: string;
    col_tenant: string;
    col_user: string;
    download: string;
    download_failed: string;
    versions: string;
    versions_title: string;
    versions_empty: string;
    ver_col_version: string;
    ver_col_path: string;
    ver_col_size: string;
    ver_col_created: string;
    digest_pending: string;
    delete: string;
    deleted: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
    empty_home: string;
    empty_cross: string;
  };
  manifest_tab: {
    read_only_hint: string;
    edit_hint: string;
    edit: string;
    save: string;
    cancel: string;
    save_failed: string;
  };
  manifest_editor: {
    tab_form: string;
    tab_yaml: string;
    loading_schema: string;
    schema_load_failed: string;
    invalid_yaml_title: string;
    invalid_yaml_hint: string;
  };
  model_select: {
    provider_label: string;
    provider_placeholder: string;
    model_label: string;
    model_placeholder: string;
    vision_on: string;
    vision_off: string;
    temperature: string;
    advanced: string;
  };
  agent_form: {
    section_basic: string;
    field_name: string;
    field_name_required: string;
    field_name_placeholder: string;
    field_description: string;
    section_model: string;
    section_prompt: string;
    field_prompt_placeholder: string;
    section_memory: string;
    memory_hint: string;
    memory_topk: string;
    section_tools: string;
    tool_web_search: string;
    tool_http: string;
    tool_mcp: string;
    mcp_servers_label: string;
    mcp_servers_hint: string;
    mcp_no_servers: string;
    mcp_source_platform: string;
    mcp_source_tenant: string;
    mcp_tools_label: string;
    mcp_tools_hint: string;
    mcp_tools_loading: string;
    mcp_tools_unreachable: string;
    mcp_servers_loading: string;
    mcp_servers_load_failed: string;
    section_reflection_evaluator: string;
    reflection_evaluator_hint: string;
    reflection_evaluator_clear: string;
  };
  playground: {
    session_label: string;
    new_session: string;
    session_failed: string;
    thread_id: string;
    loading_thread: string;
    input_placeholder: string;
    run: string;
    running: string;
    stop: string;
    event_log: string;
    event_count: string;
    stream_failed: string;
    empty_log: string;
    attach_image: string;
    attachments_label: string;
    remove_attachment: string;
    upload_failed: string;
    uploading: string;
  };
  event_stream: {
    title: string;
    connecting: string;
    event_count: string;
    stream_failed: string;
    empty: string;
  };
  approval_card: {
    awaiting_human: string;
    reason_kind: string;
    requested_at: string;
    timeout_at: string;
    proposed_args_label: string;
    editing_hint: string;
    edit_arguments: string;
    cancel_edit: string;
    approve: string;
    approve_with_edits: string;
    reject: string;
    approved: string;
    approved_with_edits: string;
    rejected: string;
    json_parse_error: string;
    json_must_be_object: string;
  };
  plan_panel: {
    title: string;
    progress: string;
    edit: string;
    cancel: string;
    save: string;
    saved: string;
    locked_while_running: string;
    goal_placeholder: string;
    step_placeholder: string;
    add_step: string;
    remove_step: string;
    no_plan: string;
    status_pending: string;
    status_in_progress: string;
    status_completed: string;
  };
  run_detail: {
    failed_to_load: string;
    thread_label: string;
    awaiting_approval: string;
    reason_kind: string;
    requested_at: string;
    timeout_at: string;
    proposed_args: string;
    approve: string;
    reject: string;
    approved: string;
    rejected: string;
    run_metadata: string;
    run_id: string;
    thread_id: string;
    status: string;
  };
  trace_toolbar: {
    title: string;
    no_trace: string;
    copy_aria: string;
    copied: string;
    open_in_langfuse: string;
    langfuse_unconfigured_hint: string;
  };
  approval_badge: {
    aria_label: string;
    tooltip_one: string;
    tooltip_other: string;
  };
  curation: {
    page_title: string;
    subtitle: string;
    tab_candidates: string;
    tab_datasets: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    col_signal: string;
    col_agent: string;
    col_status: string;
    col_detected: string;
    col_outcome: string;
    filter_status: string;
    filter_status_all: string;
    filter_signal: string;
    filter_signal_all: string;
    detail_title: string;
    detail_signal: string;
    detail_outcome: string;
    detail_trajectory: string;
    trajectory_missing: string;
    promote: string;
    dismiss: string;
    promote_modal_title: string;
    promote_dataset_name: string;
    promote_name_required: string;
    promote_hint: string;
    promoted: string;
    dismissed: string;
  };
  audit: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty: string;
    filter_actor: string;
    filter_action: string;
    filter_resource_type: string;
    filter_result: string;
    filter_result_all: string;
    load_more: string;
    detail_title: string;
    detail_id: string;
    detail_occurred_at: string;
    detail_actor: string;
    detail_on_behalf_of: string;
    detail_action: string;
    detail_resource: string;
    detail_result: string;
    detail_reason: string;
    detail_trace_id: string;
    detail_ip: string;
    detail_payload: string;
    detail_payload_hint: string;
    mcp_traffic_hint: string;
  };
  settings_ops: {
    quotas_page_title: string;
    quotas_subtitle: string;
    config_page_title: string;
    config_subtitle: string;
    config_record_title: string;
    config_edit_title: string;
    config_not_found: string;
    cross_tenant_blocked_title: string;
    cross_tenant_blocked_body: string;
    failed_to_load: string;
    quota_create: string;
    quota_create_modal_title: string;
    quota_created: string;
    quota_deleted: string;
    quota_empty: string;
    quota_delete_confirm_title: string;
    quota_delete_confirm_body: string;
    col_dimension: string;
    col_limit_value: string;
    col_burst: string;
    col_scope: string;
    col_updated: string;
    col_actions: string;
    field_dimension: string;
    field_limit_value: string;
    field_burst: string;
    burst_hint: string;
    dimension_required: string;
    limit_required: string;
    display_name: string;
    plan: string;
    audit_retention_days: string;
    event_log_retention_days: string;
    // Sprint #4 (Mini-ADR U-28) — Curator thresholds.
    skill_stale_days: string;
    skill_archive_days: string;
    mcp_allowlist: string;
    http_allowlist: string;
    updated: string;
    dirty: string;
    config_saved: string;
    config_parse_error: string;
    config_etag_hint_title: string;
    config_etag_hint_body: string;
  };
  settings_credentials: {
    page_title: string;
    subtitle: string;
    failed_to_load: string;
    mode_label: string;
    mode_platform: string;
    mode_help_platform: string;
    providers_heading: string;
    tools_heading: string;
    col_provider: string;
    col_tool: string;
    col_platform_status: string;
    col_used_by: string;
    status_configured: string;
    status_not_set: string;
    empty: string;
  };
  settings_tenants: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    col_display_name: string;
    col_plan: string;
    col_tenant_id: string;
    col_created: string;
    col_actions: string;
    col_status: string;
    st_active: string;
    st_suspended: string;
    deactivate: string;
    activate: string;
    deactivate_confirm: string;
    status_change_failed: string;
    status_changed: string;
    manage: string;
    failed_to_load: string;
    empty: string;
    create: string;
  };
  settings_create_tenant: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    field_display_name: string;
    display_name_required: string;
    field_plan: string;
    field_tenant_id: string;
    tenant_id_hint: string;
    tenant_id_placeholder: string;
    tenant_id_invalid: string;
    create_btn: string;
    created: string;
    created_detail: string;
    field_first_admin_email: string;
    field_first_admin_display_name: string;
    first_admin_hint: string;
    first_admin_email_invalid: string;
    first_admin_provisioned: string;
  };
  mcp_servers: {
    page_title: string;
    subtitle: string;
    add: string;
    col_name: string;
    col_transport: string;
    col_url: string;
    col_auth: string;
    col_status: string;
    col_tools: string;
    col_actions: string;
    status_enabled: string;
    status_disabled: string;
    test: string;
    edit: string;
    delete: string;
    testing: string;
    connected: string;
    unreachable: string;
    tools_loading: string;
    no_tools: string;
    empty_title: string;
    empty_hint: string;
    delete_confirm: string;
    failed_to_load: string;
  };
  create_mcp_server: {
    add_title: string;
    edit_title: string;
    field_name: string;
    field_transport: string;
    field_url: string;
    field_auth: string;
    field_token: string;
    field_timeout: string;
    token_hint_create: string;
    token_hint_edit: string;
    test_connection: string;
    test_ok: string;
    test_failed: string;
    name_required: string;
    url_required: string;
    url_invalid: string;
    token_required: string;
    submit_add: string;
    submit_save: string;
    custom_disabled: string;
  };
  mcp_catalog: {
    page_title: string;
    subtitle: string;
    add: string;
    not_admin_title: string;
    not_admin_body: string;
    failed_to_load: string;
    col_name: string;
    col_category: string;
    col_transport: string;
    col_tier: string;
    col_enabled: string;
    col_actions: string;
    empty_title: string;
    empty_hint: string;
    delete_confirm: string;
    delete_in_use: string;
    deleted: string;
    tier_free: string;
    tier_pro: string;
    tier_enterprise: string;
    auth_none: string;
    auth_bearer: string;
    add_title: string;
    edit_title: string;
    submit_add: string;
    submit_save: string;
    field_name: string;
    field_name_hint: string;
    field_display_name: string;
    field_description: string;
    field_category: string;
    field_icon: string;
    field_transport: string;
    field_url_template: string;
    url_template_hint: string;
    field_auth: string;
    field_required_tier: string;
    field_auth_schema: string;
    auth_schema_hint: string;
    field_enabled: string;
    name_required: string;
    display_name_required: string;
    url_template_required: string;
    guard_bearer_one_secret: string;
    guard_none_zero_secret: string;
    field_builder_empty: string;
    field_key_placeholder: string;
    field_label_placeholder: string;
    field_kind_param: string;
    field_kind_secret: string;
    field_required: string;
    field_remove: string;
    field_add: string;
    browser_title: string;
    browser_empty: string;
    browser_failed: string;
    select: string;
    locked_ribbon: string;
    requires_tier: string;
    advanced_hint: string;
    advanced_custom: string;
    instantiate_title: string;
    instance_name: string;
    instance_name_hint: string;
    field_value_required: string;
    back: string;
    create: string;
    err_tier_required: string;
    err_field_missing: string;
    err_field_unknown: string;
    err_param_invalid: string;
    err_url_template: string;
    err_invalid_url: string;
    err_duplicate: string;
    err_not_found: string;
  };
  settings_platform: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    failed_to_load: string;
    providers_heading: string;
    tools_heading: string;
    col_name: string;
    col_source: string;
    col_secret_ref: string;
    col_enabled: string;
    col_used_by: string;
    col_actions: string;
    source_env: string;
    source_db: string;
    source_unset: string;
    edit_btn: string;
    delete_confirm: string;
    edit_modal_title: string;
    mode_label: string;
    mode_value: string;
    mode_ref: string;
    value_label: string;
    value_hint: string;
    value_required: string;
    secret_ref_label: string;
    secret_ref_hint: string;
    enabled_label: string;
    saved: string;
    deleted: string;
    unset_ref: string;
    col_keys: string;
    col_key_id: string;
    col_priority: string;
    keys_summary: string;
    add_key_btn: string;
    add_key_modal_title: string;
    key_id_label: string;
    key_id_hint: string;
    key_id_invalid: string;
    priority_label: string;
    priority_hint: string;
    delete_key_confirm: string;
    col_tenant_overrides: string;
    tenant_overrides_btn: string;
    tenant_drawer_title: string;
    tenant_drawer_hint: string;
    tenant_select_label: string;
    tenant_select_placeholder: string;
    col_effective: string;
    tenant_source_tenant: string;
    tenant_source_suppressed: string;
    tenant_source_db: string;
    tenant_source_env: string;
    tenant_source_unset: string;
    override_btn: string;
    delete_override_confirm: string;
    edit_override_modal_title: string;
    embedding_heading: string;
    embedding_current: string;
    embedding_unconfigured: string;
    embedding_provider_label: string;
    embedding_model_label: string;
    rerank_enable: string;
    rerank_provider_label: string;
    rerank_model_label: string;
    embedding_save: string;
    embedding_saved: string;
    embedding_err_EMBEDDING_PROVIDER_KEY_MISSING: string;
    embedding_err_INVALID_EMBEDDING_MODEL: string;
    embedding_err_INVALID_RERANK_PAIR: string;
    embedding_err_RERANK_PROVIDER_KEY_MISSING: string;
    embedding_err_INVALID_RERANK_MODEL: string;
    judge_heading: string;
    judge_help_title: string;
    judge_help_body: string;
    judge_current: string;
    judge_unconfigured: string;
    judge_provider_label: string;
    judge_model_label: string;
    judge_save: string;
    judge_saved: string;
    judge_clear: string;
    judge_err_INVALID_JUDGE_PAIR: string;
    judge_err_JUDGE_PROVIDER_KEY_MISSING: string;
    judge_err_INVALID_JUDGE_MODEL: string;
    billing_heading: string;
    billing_help_title: string;
    billing_help_body: string;
    billing_title: string;
    billing_enabled: string;
    billing_disabled: string;
    billing_hint: string;
    billing_saved: string;
    billing_save_failed: string;
  };
  settings_iam: {
    sa_page_title: string;
    sa_subtitle: string;
    sa_api_keys_hint: string;
    sa_create: string;
    sa_create_modal_title: string;
    sa_created: string;
    sa_deleted: string;
    sa_empty_home: string;
    sa_empty_cross: string;
    sa_delete_confirm_title: string;
    sa_delete_confirm_body: string;
    rb_page_title: string;
    rb_subtitle: string;
    rb_create: string;
    rb_create_drawer_title: string;
    rb_create_submit: string;
    rb_create_platform_submit: string;
    rb_filter_platform_scope: string;
    rb_created: string;
    rb_deleted: string;
    rb_empty_home: string;
    rb_empty_cross: string;
    rb_empty_platform: string;
    rb_delete_confirm_title: string;
    rb_delete_confirm_body: string;
    rb_delete_platform_warn: string;
    rb_confirm_required: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    col_name: string;
    col_description: string;
    col_active: string;
    col_created: string;
    col_subject_type: string;
    col_subject_id: string;
    col_role: string;
    col_tenant: string;
    col_conditions: string;
    col_granted_at: string;
    col_actions: string;
    conditions_none: string;
    section_conditions: string;
    conditions_hint: string;
    field_resource_ids: string;
    resource_ids_hint: string;
    resource_ids_placeholder: string;
    field_labels: string;
    labels_hint: string;
    field_owner_only: string;
    field_name: string;
    field_description: string;
    field_subject_type: string;
    field_subject_id: string;
    field_role: string;
    field_platform_scope: string;
    platform_scope_hint: string;
    platform_scope_warn_title: string;
    platform_scope_warn_body: string;
    role_requires_platform_scope: string;
    confirm_phrase_label: string;
    name_required: string;
    subject_id_required: string;
    role_required: string;
  };
  settings_members: {
    page_title: string;
    subtitle: string;
    invite: string;
    filter_all: string;
    col_email: string;
    col_name: string;
    col_role: string;
    col_status: string;
    col_tenant: string;
    col_invited_at: string;
    col_actions: string;
    cross_tenant_banner: string;
    status_invited: string;
    status_active: string;
    status_suspended: string;
    status_revoked: string;
    resend: string;
    remove: string;
    set_password: string;
    set_password_title: string;
    set_password_hint: string;
    set_password_label: string;
    set_password_placeholder: string;
    set_password_submit: string;
    set_password_ok: string;
    set_password_failed: string;
    set_password_too_short: string;
    revoke_confirm_title: string;
    revoke_confirm_body: string;
    suspend_confirm_title: string;
    suspend_confirm_body: string;
    invite_drawer_title: string;
    field_email: string;
    field_role: string;
    field_display_name: string;
    field_display_name_placeholder: string;
    email_required: string;
    email_invalid: string;
    role_required: string;
    invite_submit: string;
    invite_success: string;
    invite_partial_fail: string;
    resent: string;
    removed: string;
    failed_to_load: string;
    empty: string;
    empty_cross: string;
  };
  settings_platform_users: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    failed_to_load: string;
    col_subject: string;
    col_role: string;
    col_granted_by: string;
    col_granted_at: string;
    col_actions: string;
    you: string;
    grant: string;
    granted: string;
    revoke: string;
    revoked: string;
    revoke_confirm: string;
    revoke_self_warning: string;
    subject_label: string;
    subject_hint: string;
    subject_invalid: string;
  };
  triggers: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_cron: string;
    empty_webhook: string;
    empty_cross: string;
    tab_cron: string;
    tab_webhook: string;
    col_name: string;
    col_agent: string;
    col_cron_expr: string;
    col_webhook_path: string;
    col_enabled: string;
    col_updated: string;
    col_actions: string;
    create: string;
    create_submit: string;
    create_cron_title: string;
    create_webhook_title: string;
    field_name: string;
    field_agent_name: string;
    field_agent_version: string;
    field_cron_expr: string;
    name_required: string;
    agent_required: string;
    agent_version_required: string;
    cron_required: string;
    cron_hint: string;
    webhook_secret_info_title: string;
    webhook_secret_info_body: string;
    secret_drawer_title: string;
    secret_warn_title: string;
    secret_warn_body: string;
    secret_trigger_name: string;
    secret_label: string;
    secret_copy: string;
    secret_copied: string;
    secret_copy_failed: string;
    secret_acknowledged: string;
    toggled: string;
    created: string;
    deleted: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
  };
  webhooks: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty: string;
    empty_cross: string;
    all_agents: string;
    col_name: string;
    col_url: string;
    col_events: string;
    col_agent: string;
    col_enabled: string;
    col_actions: string;
    create: string;
    create_submit: string;
    create_title: string;
    field_name: string;
    field_url: string;
    url_hint: string;
    field_events: string;
    events_placeholder: string;
    field_agent_name: string;
    agent_name_hint: string;
    name_required: string;
    url_required: string;
    events_required: string;
    secret_drawer_title: string;
    secret_warn_title: string;
    secret_warn_body: string;
    secret_endpoint_name: string;
    secret_label: string;
    secret_copy: string;
    secret_copied: string;
    secret_copy_failed: string;
    secret_acknowledged: string;
    toggled: string;
    created: string;
    deleted: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
  };
  skills: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    col_name: string;
    col_status: string;
    col_category: string;
    col_description: string;
    col_updated: string;
    col_created: string;
    col_version: string;
    col_tools: string;
    col_authored_by: string;
    col_actions: string;
    filter_status: string;
    filter_status_all: string;
    filter_category: string;
    load_more: string;
    create: string;
    create_modal_title: string;
    import_zip: string;
    export_zip: string;
    field_name: string;
    field_category: string;
    field_description: string;
    name_required: string;
    category_required: string;
    description_required: string;
    create_hint: string;
    created: string;
    imported: string;
    status_changed: string;
    change_status: string;
    metadata_title: string;
    versions_title: string;
    no_versions: string;
    latest_version_hint: string;
    // ── Capability Uplift Sprint #3 PR C — Admin UI dual-pane editor ──
    detail_files_title: string;
    detail_editor_title: string;
    detail_no_file_selected: string;
    detail_select_file_hint: string;
    detail_skill_md_pinned: string;
    detail_supporting_files_section: string;
    detail_no_supporting_files: string;
    detail_version_picker_label: string;
    detail_active_version_marker: string;
    detail_lazy_badge: string;
    detail_lazy_tooltip: string;
    detail_eager_tooltip: string;
    detail_high_risk_badge: string;
    detail_high_risk_tooltip: string;
    detail_high_risk_warning: string;
    detail_admin_required_tooltip: string;
    detail_unsaved_changes_warning: string;
    detail_diff_toggle: string;
    detail_diff_original_label: string;
    detail_diff_draft_label: string;
    file_add: string;
    file_add_modal_title: string;
    file_add_path_label: string;
    file_add_path_placeholder: string;
    file_add_content_label: string;
    file_add_upload_label: string;
    file_add_upload_hint: string;
    file_add_submit: string;
    file_action_edit: string;
    file_action_delete: string;
    file_action_rename: string;
    file_action_save: string;
    file_action_cancel: string;
    file_save_failed: string;
    file_saved: string;
    file_deleted: string;
    file_delete_confirm_title: string;
    file_delete_confirm_body: string;
    file_delete_confirm_input_hint: string;
    file_rename_modal_title: string;
    file_rename_new_path_label: string;
    file_rename_submit: string;
    file_renamed: string;
    file_binary_placeholder: string;
    file_size_label: string;
    file_mime_label: string;
    file_load_failed: string;
    detail_skill_md_edit_hint: string;
    // Sprint #4 — Curator pin + ETA hint.
    pin: string;
    unpin: string;
    pinned_toast: string;
    unpinned_toast: string;
    pin_tooltip_on: string;
    pin_tooltip_off: string;
    eta_days_to_stale: string;
    eta_due_soon: string;
    // Stream X-6 — merged tenant/platform library badges.
    source_platform: string;
    source_tenant: string;
    requires_tier: string;
    requires_tier_tooltip: string;
  };
  skill_marketplace: {
    page_title: string;
    subtitle: string;
    failed_to_load: string;
    empty: string;
    cross_tenant_title: string;
    cross_tenant_hint: string;
    tier_free: string;
    tier_pro: string;
    tier_enterprise: string;
    requires_tier: string;
    locked_ribbon: string;
    enable: string;
    enabled: string;
    subscribed_ok: string;
    unsubscribed_ok: string;
  };
  platform_skills: {
    page_title: string;
    subtitle: string;
    add: string;
    import_zip: string;
    import_github: string;
    github_modal_title: string;
    github_hint: string;
    github_source_label: string;
    github_source_ph: string;
    github_skill_label: string;
    github_skill_ph: string;
    github_ref_label: string;
    github_ref_ph: string;
    github_submit: string;
    imported: string;
    import_noop: string;
    manage: string;
    pin: string;
    unpin: string;
    not_admin_title: string;
    not_admin_body: string;
    failed_to_load: string;
    col_name: string;
    col_category: string;
    col_tier: string;
    col_status: string;
    col_version: string;
    col_actions: string;
    empty_title: string;
    empty_hint: string;
    tier_free: string;
    tier_pro: string;
    tier_enterprise: string;
    status_draft: string;
    status_active: string;
    status_archived: string;
    create_title: string;
    create_submit: string;
    field_name: string;
    field_name_hint: string;
    field_category: string;
    field_description: string;
    field_required_tier: string;
    name_required: string;
    created: string;
    duplicate_name: string;
    when_to_use_hint: string;
    manage_title: string;
    lifecycle_title: string;
    add_version_title: string;
    add_version_submit: string;
    field_prompt_fragment: string;
    field_version_description: string;
    field_tool_names: string;
    field_required_models: string;
    prompt_fragment_required: string;
    csv_hint: string;
    versions_title: string;
    no_versions: string;
    version_added: string;
    status_changed: string;
    pinned: string;
    unpinned: string;
    high_risk: string;
    lazy: string;
  };
  memory: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    col_kind: string;
    col_content: string;
    col_user: string;
    col_created: string;
    col_actions: string;
    filter_kind: string;
    filter_kind_all: string;
    search_placeholder: string;
    edit_title: string;
    edit_meta_user: string;
    edit_meta_kind: string;
    edit_content_label: string;
    save_dirty: string;
    embedder_note: string;
    embedder_unconfigured: string;
    empty_content: string;
    updated: string;
    deleted: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
  };
  eval_datasets: {
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    col_name: string;
    col_agent: string;
    col_source: string;
    col_updated: string;
    col_actions: string;
    create: string;
    create_modal_title: string;
    field_agent_name: string;
    field_name: string;
    agent_required: string;
    name_required: string;
    edit_title: string;
    edit_input_label: string;
    edit_expected_label: string;
    json_parse_error: string;
    created: string;
    updated: string;
    deleted: string;
    delete_confirm_title: string;
    delete_confirm_body: string;
  };
  api_keys: {
    page_title: string;
    subtitle: string;
    create: string;
    failed_to_load: string;
    empty: string;
    never: string;
    rotation_banner: string;
    rotation_help: string;
    col_prefix: string;
    col_scopes: string;
    col_service_account: string;
    col_status: string;
    col_last_used: string;
    col_expires: string;
    rotate: string;
    revoke: string;
    revoke_confirm: string;
    rotated: string;
    revoked: string;
    service_account_label: string;
    service_account_required: string;
    scopes_label: string;
    scopes_required: string;
    expires_label: string;
    dangerous: string;
    show_once_title: string;
    show_once_ack: string;
    show_once_help_prefix: string;
    show_once_help_emphasis: string;
    show_once_prefix_note: string;
    copy: string;
    copied: string;
  };
  tenant: {
    home_label_prefix: string;
    home_tenant: string;
    all_tenants: string;
    your_tenant: string;
    system_admin_hint: string;
    cross_tag: string;
  };
  agents_page: {
    page_title: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    column_name: string;
    column_status: string;
    column_tenant: string;
    column_created: string;
    create: string;
  };
  create_agent: {
    title: string;
    hint: string;
    submit: string;
    cancel: string;
    create_failed: string;
    embedding_required_title: string;
    embedding_required_desc: string;
    embedding_required_cta: string;
  };
  approvals_page: {
    page_title: string;
    cross_tenant_banner: string;
    filter_status: string;
    failed_to_load: string;
    empty: string;
    column_reason: string;
    column_action: string;
    column_waiting: string;
    column_timeout: string;
    column_status: string;
    column_actions: string;
    approve: string;
    reject: string;
    confirm_approve: string;
    confirm_reject: string;
    batch_approve: string;
    batch_reject: string;
    confirm_batch_approve: string;
    confirm_batch_reject: string;
    selected_count: string;
    decide_ok: string;
    decide_partial: string;
    decide_failed: string;
    select_row: string;
    select_all: string;
    waiting_minutes: string;
    waiting_hours: string;
    modify_hint: string;
  };
  runs_page: {
    page_title: string;
    subtitle: string;
    cross_tenant_banner: string;
    failed_to_load: string;
    empty_home: string;
    empty_cross: string;
    column_run_id: string;
    column_status: string;
    column_thread: string;
    column_agent: string;
    column_created: string;
    filter_status: string;
    filter_status_all: string;
    detail_hint: string;
    detail_hint_link: string;
  };
  eval_runs_page: {
    page_title: string;
    subtitle: string;
    failed_to_load: string;
    empty: string;
    column_run_id: string;
    column_status: string;
    column_suite: string;
    column_summary: string;
    column_created: string;
    filter_status: string;
    filter_status_all: string;
    suite_label: string;
    enqueue: string;
    enqueue_success: string;
  };
  eval_run_detail: {
    failed_to_load: string;
    not_found: string;
    run_metadata: string;
    run_id: string;
    suite: string;
    status: string;
    triggered_by: string;
    created_at: string;
    started_at: string;
    finished_at: string;
    summary: string;
    cases_title: string;
    cases_empty: string;
    col_case_id: string;
    col_capability: string;
    col_result: string;
    col_scores: string;
    result_passed: string;
    result_failed: string;
  };
  usage: {
    page_title: string;
    subtitle: string;
    group_by_agent: string;
    group_by_model: string;
    total_billed: string;
    as_of_note: string;
    col_key: string;
    col_input_tokens: string;
    col_output_tokens: string;
    col_cache_creation_tokens: string;
    col_cache_read_tokens: string;
    col_billed: string;
    unpriced: string;
    tokens_heading: string;
    realtime: string;
    realtime_note: string;
    empty: string;
    failed_to_load: string;
  };
  chargeback: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    tenant_filter: string;
    as_of: string;
    total_base: string;
    total_billed: string;
    total_margin: string;
    col_tenant: string;
    col_agent: string;
    col_input_tokens: string;
    col_output_tokens: string;
    col_base: string;
    col_markup: string;
    col_billed: string;
    col_margin: string;
    col_unpriced: string;
    empty: string;
    agents_empty: string;
    failed_to_load: string;
  };
  coming_soon: {
    title_prefix: string;
    body: string;
    other_pages_prefix: string;
    other_pages_suffix: string;
  };
  cmdk: {
    placeholder: string;
    aria_label: string;
    no_matches: string;
    group_agents: string;
    group_jump: string;
    group_action: string;
    label_runs: string;
    label_approvals: string;
    label_curation: string;
    label_eval: string;
    label_memory: string;
    label_artifacts: string;
    label_knowledge: string;
    label_skills: string;
    label_skill_marketplace: string;
    label_triggers: string;
    label_webhooks: string;
    label_settings_api_keys: string;
    label_settings_platform_users: string;
    action_create_agent: string;
    action_create_api_key: string;
    action_open_settings: string;
    hint_select: string;
    hint_jump: string;
    hint_close: string;
    hint_shortcuts: string;
  };
  skill_evolution: {
    governance_title: string;
    visibility_agent_private: string;
    visibility_tenant: string;
    owner: string;
    forked_from: string;
    propose_to_tenant: string;
    pending_tenant_promotion: string;
    approve: string;
    reject: string;
    proposed_toast: string;
    approved_toast: string;
    rejected_toast: string;
    no_version_to_propose: string;
    filter_visibility: string;
    filter_visibility_all: string;
    eval_title: string;
    eval_empty: string;
    eval_n_cases: string;
    eval_aria: string;
    eval_baseline: string;
    eval_with_skill: string;
    verdict_pass: string;
    verdict_fail: string;
    verdict_inconclusive: string;
    lineage_title: string;
    lineage_versions: string;
    lineage_fork_aria: string;
    origin_human: string;
    origin_in_session: string;
    origin_distilled: string;
    kill_switch_engaged_toast: string;
    kill_switch_released_toast: string;
    kill_switch_confirm_title: string;
    kill_switch_confirm_body: string;
    kill_switch_engage: string;
    kill_switch_hint: string;
    kill_switch_halted: string;
    kill_switch_active: string;
    kill_switch_tenant_label: string;
    kill_switch_global_label: string;
    kill_switch_tenant_aria: string;
    kill_switch_global_aria: string;
  };
}

const en: TranslationKeys = {
  common: {
    sign_in: "Sign in",
    sign_out: "Sign out",
    refresh: "Refresh",
    loading: "Loading…",
    cancel: "Cancel",
    confirm: "Confirm",
    save: "Save",
    delete: "Delete",
    edit: "Edit",
    search_or_jump: "Search or jump",
    notifications: "Notifications",
    user_menu: "User menu",
    anonymous: "anonymous",
  },
  theme: {
    switch_to_light: "Switch to Light",
    switch_to_dark: "Switch to Dark",
    toggle: "Toggle theme",
  },
  nav: {
    settings_group: "Settings",
    group_workspace: "Workspace",
    group_tenant_settings: "Tenant settings",
    group_platform: "Platform",
    members_all_tenants: "Members (all tenants)",
    agents: "Agents",
    runs: "Runs",
    approvals: "Approvals",
    curation: "Curation+Eval",
    eval: "Eval",
    memory: "Memory",
    artifacts: "Artifacts",
    knowledge: "Knowledge",
    skills: "Skills",
    skill_marketplace: "Skill Marketplace",
    triggers: "Triggers",
    webhooks: "Webhooks",
    tenants: "Tenants",
    platform_credentials: "Platform Credentials",
    platform_users: "Platform Admins",
    api_keys: "API Keys",
    credentials: "Credentials",
    service_accounts: "Service Accounts",
    members: "Members",
    audit: "Audit",
    mcp_servers: "MCP Servers",
    mcp_catalog: "MCP Catalog",
    platform_skills: "Platform Skills",
    usage: "Usage",
    chargeback: "Chargeback",
    rate_card: "Rate Card",
  },
  login: {
    title: "helix Admin",
    paragraph:
      "Paste your OIDC JWT or helix API key to sign in. Both are stored in this browser only; the control-plane re-verifies on every request.",
    token_label: "Token",
    token_placeholder: "eyJ… (JWT)   or   aforge_pat_… (helix API key)",
    token_required: "Token is required",
    token_empty: "Token cannot be empty",
    pr2_hint: "OIDC code-flow login lands in H.1b PR 2 — see",
    sign_in_sso: "Sign in with SSO",
    sso_help:
      "You will be redirected to your organization's identity provider.",
    dev_login_toggle: "Developer login (paste token)",
    dev_login_hide: "Hide developer login",
    dev_login_section: "Developer login",
  },
  auth_callback: {
    title: "Signing in…",
    exchanging:
      "Exchanging the authorization code with your identity provider.",
    failed: "Sign-in failed",
  },
  setup: {
    title: "Set up helix",
    paragraph:
      "No platform administrator exists yet. Create the first system admin to finish installing helix.",
    platform_name_label: "Platform name",
    admin_email_label: "Administrator email",
    admin_email_required: "Email is required",
    admin_email_invalid: "Enter a valid email address",
    admin_password_label: "Password",
    admin_password_required: "Password is required",
    admin_password_length: "Password must be 8–256 characters",
    admin_password_confirm_label: "Confirm password",
    admin_password_confirm_required: "Please confirm the password",
    admin_password_mismatch: "The two passwords do not match",
    admin_display_name_label: "Display name (optional)",
    setup_token_label: "Setup token",
    setup_token_hint:
      "The HELIX_AGENT_SETUP_TOKEN value configured at deploy time.",
    setup_token_required: "Setup token is required",
    notice:
      "This one-time wizard creates the first platform system administrator. It is disabled once setup completes.",
    submit: "Create administrator",
    success_toast: "Platform administrator created",
    done_title: "Platform administrator created",
    done_subtitle:
      "helix is initialized. Sign in with the administrator account you just created.",
    go_to_login: "Go to sign in",
    already_initialized_title: "Already initialized",
    already_initialized_subtitle:
      "A platform administrator already exists. Sign in instead.",
    error_already_initialized:
      "The platform is already initialized — please sign in.",
    error_invalid_token: "The setup token is incorrect.",
    error_not_configured:
      "Setup is not configured on this deployment (no setup token set).",
    error_email_exists: "An account with this email already exists.",
    error_keycloak_unavailable:
      "The identity provider is unavailable. Try again shortly.",
    error_generic: "Setup failed. Please check the details and try again.",
  },
  agent_detail: {
    failed_to_load: "Failed to load agent",
    tab_overview: "Overview",
    tab_manifest: "Manifest",
    tab_playground: "Playground",
    tab_runs: "Runs",
    tab_skills: "Skills",
    tab_triggers: "Triggers",
    tab_memory: "Memory",
    tab_history: "History",
    tab_coming_soon: 'Tab "{{tab}}" lands in Stream H.2.',
    config_summary: "Configuration",
    field_id: "Record ID",
    field_tenant: "Tenant",
    field_spec_sha: "Spec sha256",
    field_status: "Status",
    field_created: "Created",
    field_updated: "Updated",
  },
  history_tab: {
    title: "Revision history",
    select_hint: "Select two revisions to compare",
    current: "current",
    col_revision: "Revision",
    col_sha: "Spec SHA",
    col_actor: "Actor",
    col_time: "Time",
    rollback: "Roll back",
    rollback_confirm_title: "Roll back to revision #{{revision}}?",
    rollback_confirm_body:
      "This appends a new revision with the old content — history is never rewritten.",
    diff_label: "Diff: #{{older}} → #{{newer}}",
    diff_loading: "Loading diff…",
  },
  runs_tab: {
    title: "Runs of this agent",
    window_capped:
      "This agent has more threads than the server window — runs from older threads are not shown.",
    empty: "No runs for this agent yet.",
  },
  skills_tab: {
    title: "Skills authored by this agent",
    authored_hint: "Skills created by {{agent}} (all versions)",
    col_name: "Name",
    col_status: "Status",
    col_visibility: "Visibility",
    col_version: "Latest",
    col_created: "Created",
    empty: "This agent has not authored any skills yet.",
  },
  triggers_tab: {
    title: "Triggers bound to this version",
    manage_hint: "Create / enable / disable on the Triggers page",
    col_name: "Name",
    col_kind: "Kind",
    col_enabled: "State",
    col_source: "Source",
    col_created: "Created",
    enabled: "enabled",
    disabled: "disabled",
    empty: "No triggers bound to this agent version.",
  },
  memory_tab: {
    title: "Long-term memory",
    user_scope_note:
      "Memory is a per-user asset — these are the per-user items this agent reads; there is no per-agent partition. Governance actions live on the Memory page.",
    filter_kind: "Filter by kind",
    filter_kind_all: "All kinds",
    col_kind: "Kind",
    col_content: "Content",
    col_user: "User",
    col_created: "Created",
    empty: "No memory items yet.",
  },
  knowledge_page: {
    page_title: "Knowledge",
    subtitle: "Tenant-shared knowledge bases feeding agent retrieval.",
    home_scope_note:
      "Knowledge bases follow your signed-in tenant only — this page does not switch with the global tenant scope (no cross-tenant backend support).",
    failed_to_load: "Failed to load knowledge bases",
    bases_title: "Bases",
    bases_empty: "No knowledge bases yet.",
    create_base: "New base",
    create_duplicate: "A knowledge base with this name already exists.",
    field_name: "Name",
    field_chunk_max: "Chunk max tokens (optional)",
    field_chunk_overlap: "Chunk overlap tokens (optional)",
    field_chunk_hint:
      "Defaults apply when left empty; overlap must be less than max.",
    col_base_name: "Name",
    col_chunking: "Chunk max/overlap",
    delete: "Delete",
    delete_base_confirm_title: "Delete base {{name}}?",
    delete_base_confirm_body:
      "This removes the base together with all its documents and vectors.",
    documents_title: "Documents in {{name}}",
    documents_unselected: "Documents",
    documents_empty: "No documents in this base yet.",
    select_hint: "Select a base on the left to manage its documents.",
    upload: "Upload",
    unsupported_type: "Unsupported document type: {{name}}",
    embedder_missing:
      "Document ingestion is unavailable — no embedding model is configured on the platform.",
    col_filename: "Filename",
    col_status: "Status",
    col_chunks: "Chunks",
    col_updated: "Updated",
    delete_doc_confirm_title: "Delete document {{name}}?",
  },
  rate_card_page: {
    page_title: "Rate Card",
    subtitle:
      "Platform model pricing — micro-USD per token, plus a basis-point markup.",
    system_admin_only:
      "Rate-card management is platform-admin (system_admin) only.",
    failed_to_load: "Failed to load rate cards",
    empty: "No rate-card rows yet.",
    filter_provider: "Provider",
    filter_model: "Model",
    include_expired: "Include expired",
    create: "New rate",
    edit: "Edit",
    edit_title: "Edit {{provider}} / {{model}}",
    delete: "Delete",
    delete_confirm_title: "Delete this rate-card row?",
    delete_confirm_body:
      "Usage already priced by this row keeps its historical cost; future usage falls back to the next matching row.",
    col_provider: "Provider",
    col_model: "Model",
    col_plan: "Plan",
    col_input: "Input (µ$)",
    col_output: "Output (µ$)",
    col_markup: "Markup",
    col_effective: "Effective",
    all_plans: "all plans",
    open_ended: "open-ended",
    field_provider: "Provider",
    field_model: "Model",
    field_input: "Input price (micro-USD / token)",
    field_output: "Output price (micro-USD / token)",
    field_cache_creation: "Cache-creation price (micro-USD / token)",
    field_cache_read: "Cache-read price (micro-USD / token)",
    field_markup: "Markup (basis points)",
    field_plan: "Plan tier (empty = all plans)",
    field_effective_from: "Effective from",
    field_effective_until: "Effective until (empty = open-ended)",
    until_after_from: "effective_until must be after effective_from",
    identity_immutable:
      "Provider / model / plan / effective-from are this row's identity and cannot change — reprice by creating a new row.",
  },
  artifacts_page: {
    page_title: "Artifacts",
    subtitle_home:
      "Your run artifacts — artifacts are per-user assets; this account only sees and manages its own.",
    subtitle_cross:
      "Platform-wide read-only aggregate across every tenant and user.",
    cross_tenant_banner: "All tenants",
    failed_to_load: "Failed to load artifacts",
    col_name: "Name",
    col_kind: "Kind",
    col_latest: "Latest",
    col_tenant: "Tenant",
    col_user: "User",
    download: "Download",
    download_failed: "Download failed: {{detail}}",
    versions: "Versions",
    versions_title: "Versions of {{name}}",
    versions_empty: "No versions recorded.",
    ver_col_version: "Version",
    ver_col_path: "Workspace path",
    ver_col_size: "Size (bytes)",
    ver_col_created: "Created",
    digest_pending: "Backfilled after the first download",
    delete: "Delete",
    deleted: "Soft-deleted {{name}}",
    delete_confirm_title: "Delete {{name}}?",
    delete_confirm_body:
      "Soft-delete: the artifact disappears from lists and downloads; bytes stay until the retention sweep, and re-saving the same name un-deletes it.",
    empty_home: "This account has no run artifacts yet.",
    empty_cross: "No artifacts anywhere on the platform yet.",
  },
  manifest_tab: {
    read_only_hint: "Read-only — click Edit to modify the spec.",
    edit_hint:
      "Editing — Save writes through PUT /v1/agents, Cancel discards changes.",
    edit: "Edit",
    save: "Save",
    cancel: "Cancel",
    save_failed: "Failed to save manifest",
  },
  manifest_editor: {
    tab_form: "Form",
    tab_yaml: "YAML",
    loading_schema: "Loading schema…",
    schema_load_failed: "Failed to load the manifest schema",
    invalid_yaml_title: "Can't switch to Form",
    invalid_yaml_hint:
      "The YAML is invalid or doesn't match the manifest schema. Fix it here first.",
  },
  model_select: {
    provider_label: "Provider",
    provider_placeholder: "Select a configured provider",
    model_label: "Model",
    model_placeholder: "Select a model",
    vision_on: "Vision: supported",
    vision_off: "Vision: not supported",
    temperature: "Temperature",
    advanced: "Advanced",
  },
  agent_form: {
    section_basic: "Basics",
    field_name: "Name",
    field_name_required: "Name is required",
    field_name_placeholder: "my-agent",
    field_description: "Description",
    section_model: "Model",
    section_prompt: "System prompt",
    field_prompt_placeholder: "You are a helpful assistant.",
    section_memory: "Long-term memory",
    memory_hint: "Remembers across sessions; needs a platform embedding.",
    memory_topk: "Memories recalled per run",
    section_tools: "Tools",
    tool_web_search: "Web search",
    tool_http: "HTTP tool",
    tool_mcp: "MCP tools",
    mcp_servers_label: "MCP servers this agent can use",
    mcp_servers_hint: "Leave all unchecked to allow every available server",
    mcp_no_servers:
      "No MCP servers available. Register one under Settings → MCP Servers.",
    mcp_source_platform: "platform",
    mcp_source_tenant: "tenant",
    mcp_tools_label: "Tools",
    mcp_tools_hint:
      "Leave all unchecked to allow every tool from the selected servers",
    mcp_tools_loading: "Loading tools…",
    mcp_tools_unreachable: "Could not load tools",
    mcp_servers_loading: "Loading servers…",
    mcp_servers_load_failed: "Could not load servers",
    section_reflection_evaluator: "Reflection evaluator (optional)",
    reflection_evaluator_hint:
      "When reflection is enabled, which model judges whether the task is done. Leave empty to reuse the agent's own model. Prefer a different model — not necessarily stronger; an independent vantage counters the model's bias toward its own output and covers shared blind spots (same-model self-critique tends to declare 'done' early and miss its own errors). Pick a stronger model for deep-reasoning tasks.",
    reflection_evaluator_clear: "Clear (use the agent's own model)",
  },
  playground: {
    session_label: "Session",
    new_session: "New session",
    session_failed: "Failed to create session",
    thread_id: "thread",
    loading_thread: "Creating thread…",
    input_placeholder:
      "Type a prompt to send to the agent. The full SSE event stream lands on the right.",
    run: "Run",
    running: "Running…",
    stop: "Stop",
    event_log: "Event log",
    event_count: "{{n}} events",
    stream_failed: "Stream failed",
    empty_log: "No events yet — click Run to start.",
    attach_image: "Attach image",
    attachments_label: "Attachments",
    remove_attachment: "Remove attachment",
    upload_failed: "Image upload failed",
    uploading: "Uploading…",
  },
  event_stream: {
    title: "Event stream",
    connecting: "Connecting…",
    event_count: "{{n}} events",
    stream_failed: "Stream failed",
    empty: "No events yet.",
  },
  approval_card: {
    awaiting_human: "awaiting approval",
    reason_kind: "Reason",
    requested_at: "Requested",
    timeout_at: "Timeout",
    proposed_args_label: "Proposed arguments (read-only)",
    editing_hint: "Editing arguments — Approve sends them as 'modify' decision",
    edit_arguments: "Edit arguments",
    cancel_edit: "Cancel edit",
    approve: "Approve",
    approve_with_edits: "Approve with edits",
    reject: "Reject",
    approved: "Approved — run resuming.",
    approved_with_edits: "Approved with edits — run resuming.",
    rejected: "Rejected — run cancelled.",
    json_parse_error: "Invalid JSON",
    json_must_be_object: "Top-level value must be a JSON object",
  },
  plan_panel: {
    title: "Plan",
    progress: "{{done}}/{{total}} completed",
    edit: "Edit plan",
    cancel: "Cancel",
    save: "Save plan",
    saved: "Plan saved — the workspace PLAN.md syncs on the next turn.",
    locked_while_running: "The agent owns the plan while a run is live.",
    goal_placeholder: "Goal — one sentence",
    step_placeholder: "Step description",
    add_step: "Add step",
    remove_step: "Remove step",
    no_plan: "The agent has not made a plan yet.",
    status_pending: "pending",
    status_in_progress: "in progress",
    status_completed: "completed",
  },
  run_detail: {
    failed_to_load: "Failed to load run",
    thread_label: "Thread",
    awaiting_approval: "awaiting approval",
    reason_kind: "Reason",
    requested_at: "Requested",
    timeout_at: "Timeout",
    proposed_args: "Proposed arguments",
    approve: "Approve",
    reject: "Reject",
    approved: "Approved — run resuming.",
    rejected: "Rejected — run cancelled.",
    run_metadata: "Run metadata",
    run_id: "Run ID",
    thread_id: "Thread ID",
    status: "Status",
  },
  trace_toolbar: {
    title: "Trace",
    no_trace: "No trace recorded for this run.",
    copy_aria: "Copy trace ID",
    copied: "Trace ID copied",
    open_in_langfuse: "Open in Langfuse",
    langfuse_unconfigured_hint:
      "Set VITE_LANGFUSE_BASE_URL to enable the deep link.",
  },
  approval_badge: {
    aria_label: "Pending approvals",
    tooltip_one: "{{count}} run is awaiting approval",
    tooltip_other: "{{count}} runs are awaiting approval",
  },
  curation: {
    page_title: "Curation+Eval",
    subtitle:
      "Review candidate runs surfaced by the curation worker and promote them to golden eval datasets.",
    tab_candidates: "Candidates",
    tab_datasets: "Eval Datasets",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load candidates",
    empty_home: "No pending candidates in this tenant.",
    empty_cross: "No candidates across all tenants.",
    col_signal: "Signal",
    col_agent: "Agent",
    col_status: "Status",
    col_detected: "Detected",
    col_outcome: "Outcome",
    filter_status: "Status filter",
    filter_status_all: "All statuses",
    filter_signal: "Signal filter",
    filter_signal_all: "All signals",
    detail_title: "Candidate detail",
    detail_signal: "Signal",
    detail_outcome: "Outcome",
    detail_trajectory: "Trajectory",
    trajectory_missing:
      "Trajectory artifact missing — promote / dismiss still allowed.",
    promote: "Promote",
    dismiss: "Dismiss",
    promote_modal_title: "Promote candidate to eval dataset",
    promote_dataset_name: "Eval dataset name",
    promote_name_required: "Name is required",
    promote_hint:
      "Trajectory input + expected output are copied from the candidate. Edit later via the Eval Datasets tab.",
    promoted: "Candidate promoted to eval dataset.",
    dismissed: "Candidate dismissed.",
  },
  audit: {
    page_title: "Audit",
    subtitle:
      "Immutable trail of every mutating action. Cross-tenant view requires system_admin. Payloads are PII-redacted at write time.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load audit log",
    empty: "No audit entries match these filters.",
    filter_actor: "Actor ID (exact)",
    filter_action: "Action (e.g. memory:update)",
    filter_resource_type: "Resource type",
    filter_result: "Result",
    filter_result_all: "All results",
    load_more: "Load more",
    detail_title: "Audit entry detail",
    detail_id: "ID",
    detail_occurred_at: "Occurred at",
    detail_actor: "Actor",
    detail_on_behalf_of: "On behalf of",
    detail_action: "Action",
    detail_resource: "Resource",
    detail_result: "Result",
    detail_reason: "Reason",
    detail_trace_id: "Trace ID",
    detail_ip: "IP",
    detail_payload: "Payload (details)",
    detail_payload_hint:
      "Already redactor-cleaned at write time — sensitive fields surface as [REDACTED].",
    mcp_traffic_hint: "MCP traffic to {{server}} — server · response size",
  },
  settings_ops: {
    quotas_page_title: "Tenant Quotas",
    quotas_subtitle:
      "Per-tenant rate / cost limits keyed by dimension. Tenant-scoped only — switch tenant via the top-bar to manage another.",
    config_page_title: "Tenant Config",
    config_subtitle:
      "Per-tenant feature knobs: plan, retention, MCP/HTTP allowlists, PII fields. Same per-tenant scope as Quotas.",
    config_record_title: "Current config",
    config_edit_title: "Edit config (JSON patch)",
    config_not_found:
      "No tenant_config row exists for this tenant yet. First save will create one.",
    cross_tenant_blocked_title: "Cross-tenant view does not apply here",
    cross_tenant_blocked_body:
      "Quotas + config are managed per-tenant. Switch to a specific tenant via the top-bar to view / edit.",
    failed_to_load: "Failed to load",
    quota_create: "Create Quota",
    quota_create_modal_title: "Create / upsert quota",
    quota_created: "Quota saved.",
    quota_deleted: "Quota deleted.",
    quota_empty: "No quotas configured for this tenant.",
    quota_delete_confirm_title: "Delete this quota?",
    quota_delete_confirm_body:
      "The dimension will fall back to platform defaults until re-created.",
    col_dimension: "Dimension",
    col_limit_value: "Limit",
    col_burst: "Burst",
    col_scope: "Scope",
    col_updated: "Updated",
    col_actions: "Actions",
    field_dimension: "Dimension",
    field_limit_value: "Limit value",
    field_burst: "Burst (optional)",
    burst_hint:
      "Token-bucket burst capacity. Defaults to platform setting when empty.",
    dimension_required: "Dimension is required",
    limit_required: "Limit value is required",
    display_name: "Display name",
    plan: "Plan",
    audit_retention_days: "Audit retention (days)",
    event_log_retention_days: "Event log retention (days)",
    skill_stale_days: "Skill stale threshold (days)",
    skill_archive_days: "Skill archive threshold (days)",
    mcp_allowlist: "MCP allowlist",
    http_allowlist: "HTTP tool allowlist",
    updated: "Updated",
    dirty: "edited (unsaved)",
    config_saved: "Tenant config saved.",
    config_parse_error: "JSON parse error",
    config_etag_hint_title: "Last-writer-wins (M0)",
    config_etag_hint_body:
      "No ETag concurrency check yet — M1 will add If-Match. If another admin is editing simultaneously, the last Save will overwrite. Reload after save to see latest.",
  },
  settings_credentials: {
    page_title: "Credentials",
    subtitle:
      "LLM and tool credentials are platform-managed. This read-only view shows, per provider / tool, whether the platform has a credential configured and how many of this tenant's agents use it.",
    failed_to_load: "Failed to load credentials",
    mode_label: "Credentials mode",
    mode_platform: "Platform",
    mode_help_platform:
      "All LLM / tool calls use the platform's credentials. Credentials are managed at the platform level.",
    providers_heading: "Provider credentials",
    tools_heading: "Tool credentials",
    col_provider: "Provider",
    col_tool: "Tool",
    col_platform_status: "Platform status",
    col_used_by: "Used by (agents)",
    status_configured: "Configured",
    status_not_set: "Not configured",
    empty: "No entries — the platform has opted into no providers / tools yet.",
  },
  settings_tenants: {
    page_title: "Tenants",
    subtitle:
      "All tenants on the platform. Click Manage to switch into a tenant and edit its config, quotas, and credentials.",
    not_admin_title: "System admin only",
    not_admin_body:
      "Listing all tenants is a platform-level action available to system admins.",
    col_display_name: "Display name",
    col_plan: "Plan",
    col_tenant_id: "Tenant id",
    col_created: "Created",
    col_actions: "Actions",
    col_status: "Status",
    st_active: "Active",
    st_suspended: "Suspended",
    deactivate: "Deactivate",
    activate: "Activate",
    deactivate_confirm:
      "Suspend this tenant? Its members will be blocked until reactivated.",
    status_change_failed: "Failed to change tenant status",
    status_changed: "Tenant status updated.",
    manage: "Manage",
    failed_to_load: "Failed to load tenants",
    empty: "No tenants yet — create one from Create Tenant.",
    create: "Create Tenant",
  },
  settings_create_tenant: {
    page_title: "Create Tenant",
    subtitle:
      "Provision a new tenant. Platform-level action — system admins only. The new tenant id is shown on success so you can configure it.",
    not_admin_title: "System admin only",
    not_admin_body:
      "Creating tenants is a platform-level operation. Ask a system admin to provision one.",
    field_display_name: "Display name",
    display_name_required: "Display name is required",
    field_plan: "Plan",
    field_tenant_id: "Tenant id (optional)",
    tenant_id_hint:
      "Leave empty to let the server generate a UUID. Supply one only for idempotent provisioning.",
    tenant_id_placeholder: "auto-generated",
    tenant_id_invalid:
      "Must be a valid UUID (e.g. 123e4567-e89b-12d3-a456-426614174000), or leave empty to auto-generate.",
    create_btn: "Create tenant",
    created: "Tenant created.",
    created_detail: "New tenant id:",
    field_first_admin_email: "First admin email (optional)",
    field_first_admin_display_name: "First admin display name (optional)",
    first_admin_hint:
      "Provisions the company's first admin and sends a set-password email. In dev (no SMTP) set the password in the Keycloak admin console instead.",
    first_admin_email_invalid: "Enter a valid email address",
    first_admin_provisioned: "First admin invited:",
  },
  mcp_servers: {
    page_title: "MCP Servers",
    subtitle: "Manage the remote MCP servers your agents can call tools from",
    add: "Add server",
    col_name: "Name",
    col_transport: "Transport",
    col_url: "URL",
    col_auth: "Auth",
    col_status: "Status",
    col_tools: "Tools",
    col_actions: "Actions",
    status_enabled: "Enabled",
    status_disabled: "Disabled",
    test: "Test",
    edit: "Edit",
    delete: "Delete",
    testing: "Testing…",
    connected: "Connected · {{count}} tools",
    unreachable: "Unreachable",
    tools_loading: "Loading tools…",
    no_tools: "No tools advertised",
    empty_title: "No MCP servers yet",
    empty_hint:
      "MCP servers let your agents call external tools like GitHub or Linear.",
    delete_confirm: "Delete server {{name}}?",
    failed_to_load: "Failed to load MCP servers",
  },
  create_mcp_server: {
    add_title: "Add MCP server",
    edit_title: "Edit MCP server",
    field_name: "Name",
    field_transport: "Transport",
    field_url: "URL",
    field_auth: "Auth type",
    field_token: "Bearer token",
    field_timeout: "Timeout (seconds)",
    token_hint_create: "Pasted once, stored encrypted — never shown again",
    token_hint_edit:
      "Leave blank to keep the current token; enter a new value to rotate",
    test_connection: "Test connection",
    test_ok: "Connected · {{count}} tools",
    test_failed: "Connection failed",
    name_required: "Name is required",
    url_required: "URL is required",
    url_invalid: "URL must start with http:// or https://",
    token_required: "Token is required for bearer auth",
    submit_add: "Add",
    submit_save: "Save",
    custom_disabled:
      "Custom servers are disabled by your platform admin; use the catalog.",
  },
  mcp_catalog: {
    page_title: "MCP Catalog",
    subtitle:
      "Curate the MCP connector types tenants can add. Platform-level, system admins only.",
    add: "New connector",
    not_admin_title: "System admin only",
    not_admin_body:
      "The MCP connector catalog is managed by system admins. Ask one to add connectors.",
    failed_to_load: "Failed to load the MCP catalog",
    col_name: "Connector",
    col_category: "Category",
    col_transport: "Transport",
    col_tier: "Required plan",
    col_enabled: "Enabled",
    col_actions: "Actions",
    empty_title: "No connectors yet",
    empty_hint:
      "Add connector types so tenants can wire up GitHub, Linear and other MCP servers in a few clicks.",
    delete_confirm: "Delete connector {{name}}?",
    delete_in_use: "In use by tenants — cannot delete. Disable it instead.",
    deleted: "Connector deleted",
    tier_free: "Free",
    tier_pro: "Pro",
    tier_enterprise: "Enterprise",
    auth_none: "None",
    auth_bearer: "Bearer token",
    add_title: "New connector",
    edit_title: "Edit connector",
    submit_add: "Create",
    submit_save: "Save",
    field_name: "Name (slug)",
    field_name_hint: "Lowercase identifier, immutable after creation",
    field_display_name: "Display name",
    field_description: "Description",
    field_category: "Category",
    field_icon: "Icon",
    field_transport: "Transport",
    field_url_template: "URL template",
    url_template_hint:
      "Use {param} placeholders — they are filled from the param fields at instantiation.",
    field_auth: "Auth type",
    field_required_tier: "Required plan",
    field_auth_schema: "Auth schema fields",
    auth_schema_hint:
      "Fields the tenant fills in when adding this connector. Secrets are stored encrypted; params fill the URL template.",
    field_enabled: "Enabled",
    name_required: "A valid lowercase slug is required",
    display_name_required: "Display name is required",
    url_template_required: "URL template is required",
    guard_bearer_one_secret: "Bearer auth requires exactly one secret field.",
    guard_none_zero_secret: "Auth type None must have no secret fields.",
    field_builder_empty: "No fields yet — add one below.",
    field_key_placeholder: "key",
    field_label_placeholder: "Label",
    field_kind_param: "Param",
    field_kind_secret: "Secret",
    field_required: "Required",
    field_remove: "Remove field",
    field_add: "Add field",
    browser_title: "Add MCP server",
    browser_empty: "No connectors are available for your plan yet.",
    browser_failed: "Failed to load the catalog",
    select: "Add",
    locked_ribbon: "Locked",
    requires_tier: "Requires {{tier}} plan",
    advanced_hint: "Need something not in the catalog?",
    advanced_custom: "Advanced — add a custom server",
    instantiate_title: "Add {{name}}",
    instance_name: "Instance name (optional)",
    instance_name_hint:
      "Defaults to the connector name; override to add more than one.",
    field_value_required: "{{label}} is required",
    back: "Back",
    create: "Create",
    err_tier_required:
      "Your plan does not include this connector. Upgrade to add it.",
    err_field_missing: "A required field is missing.",
    err_field_unknown: "An unexpected field was supplied.",
    err_param_invalid: "A parameter value is invalid.",
    err_url_template:
      "The connector URL template could not be filled — check your params.",
    err_invalid_url: "The resulting server URL is invalid.",
    err_duplicate: "A server with this name already exists.",
    err_not_found: "This connector no longer exists.",
  },
  settings_platform: {
    page_title: "Platform Credentials",
    subtitle:
      "Platform-level provider & tool credential refs (the runtime overlay over env config). System admins only. DB rows win over env; disable to turn one off without deleting.",
    not_admin_title: "System admin only",
    not_admin_body:
      "Platform credentials are managed by system admins. Ask one to configure providers/tools.",
    failed_to_load: "Failed to load platform credentials.",
    providers_heading: "Providers",
    tools_heading: "Tools",
    col_name: "Name",
    col_source: "Source",
    col_secret_ref: "Secret reference",
    col_enabled: "Enabled",
    col_used_by: "Used by agents",
    col_actions: "Actions",
    source_env: "env",
    source_db: "db",
    source_unset: "unset",
    edit_btn: "Edit",
    delete_confirm:
      "Delete this DB-managed credential? (env fallback, if any, applies again.)",
    edit_modal_title: "Edit {{key}} credential",
    secret_ref_label: "Secret reference (KMS / secret URI)",
    secret_ref_hint:
      "A secret manager reference (e.g. kms://platform/anthropic-key) — never a plaintext key.",
    mode_label: "Credential source",
    mode_value: "Paste a key",
    mode_ref: "Reference (URI)",
    value_label: "API key",
    value_hint:
      "Pasted once and encrypted at rest — it is never shown again or stored in plaintext.",
    value_required: "Paste a key, or switch to Reference.",
    enabled_label: "Enabled",
    saved: "Credential saved.",
    deleted: "Credential deleted.",
    unset_ref: "not set",
    col_keys: "Keys",
    col_key_id: "Key ID",
    col_priority: "Priority",
    keys_summary: "{{on}}/{{total}} on",
    add_key_btn: "Add key",
    add_key_modal_title: "Add a key to {{key}}",
    key_id_label: "Key ID",
    key_id_hint:
      "Distinguishes sibling keys of one provider (e.g. acct-b); 'default' is the primary.",
    key_id_invalid: "Letters, digits, dot, underscore, hyphen only.",
    priority_label: "Priority",
    priority_hint:
      "Lower is tried first; on rate-limit / out-of-balance / revoked the next key takes over.",
    delete_key_confirm:
      "Delete this key? (Sibling keys of the same provider still fail over.)",
    col_tenant_overrides: "Tenant overrides",
    tenant_overrides_btn: "Tenant overrides",
    tenant_drawer_title: "Per-tenant credential overrides",
    tenant_drawer_hint:
      "Overrides are platform-procured and platform-managed; tenants never see them. A disabled override suppresses the key for the tenant (no fallback).",
    tenant_select_label: "Tenant",
    tenant_select_placeholder: "Select a tenant…",
    col_effective: "Effective",
    tenant_source_tenant: "override",
    tenant_source_suppressed: "suppressed",
    tenant_source_db: "platform (db)",
    tenant_source_env: "platform (env)",
    tenant_source_unset: "unset",
    override_btn: "Override",
    delete_override_confirm:
      "Remove this override? The tenant falls back to the platform credential.",
    edit_override_modal_title: "Edit tenant override for {{key}}",
    embedding_heading: "Embedding & Rerank",
    embedding_current: "Current",
    embedding_unconfigured:
      "No embedding model configured — long-term memory is unavailable until you set one.",
    embedding_provider_label: "Embedding provider",
    embedding_model_label: "Embedding model",
    rerank_enable: "Enable rerank",
    rerank_provider_label: "Rerank provider",
    rerank_model_label: "Rerank model",
    embedding_save: "Save",
    embedding_saved: "Embedding configuration saved.",
    embedding_err_EMBEDDING_PROVIDER_KEY_MISSING:
      "This provider has no configured key — add it under Providers above first.",
    embedding_err_INVALID_EMBEDDING_MODEL:
      "That model can't be used for embeddings.",
    embedding_err_INVALID_RERANK_PAIR:
      "Set both rerank provider and model, or neither.",
    embedding_err_RERANK_PROVIDER_KEY_MISSING:
      "The rerank provider has no configured key.",
    embedding_err_INVALID_RERANK_MODEL: "That model can't be used for rerank.",
    judge_heading: "Output Judge",
    judge_help_title: "What is the judge model?",
    judge_help_body:
      "A separate model that reviews agent replies (and, for tool-using agents, tool calls) for signs of prompt-injection hijacking or secret leakage — a safety backstop above the rule-based screen. Pick a cheap, fast model: it is called often. Leave it unset to reuse each agent's own primary model (more accurate, but pricier and slower). It only runs when an agent's manifest enables output_judge / action_screen.",
    judge_current: "Current judge model",
    judge_unconfigured: "Not set — judges reuse each agent's own model.",
    judge_provider_label: "Judge provider",
    judge_model_label: "Judge model",
    judge_save: "Save",
    judge_saved: "Judge configuration saved.",
    judge_clear: "Clear (use agent's own model)",
    judge_err_INVALID_JUDGE_PAIR:
      "Set both judge provider and model, or neither to clear.",
    judge_err_JUDGE_PROVIDER_KEY_MISSING:
      "This provider has no configured key — add it under Providers above first.",
    judge_err_INVALID_JUDGE_MODEL:
      "That model can't be used as a judge (pick a chat model).",
    billing_heading: "Cost rollup",
    billing_help_title: "What is cost rollup?",
    billing_help_body:
      "An offline job prices each tenant's token usage with the rate cards and writes the monthly cost ledger that powers the chargeback view. It runs on a schedule. Turn it off to pause cost computation platform-wide (the schedule keeps firing but each run skips); turn it back on to resume.",
    billing_title: "Cost rollup",
    billing_enabled: "Cost rollup enabled",
    billing_disabled: "Cost rollup paused",
    billing_hint:
      "While paused, the chargeback / usage cost figures stop updating; raw token metering is unaffected.",
    billing_saved: "Cost rollup setting saved.",
    billing_save_failed: "Failed to save cost rollup setting",
  },
  settings_iam: {
    sa_page_title: "Service Accounts",
    sa_subtitle:
      "Non-human identities — bots, workflows, integrations. Each service account holds zero or more API keys.",
    sa_api_keys_hint: "Manage the keys themselves on the API Keys page.",
    sa_create: "Create Service Account",
    sa_create_modal_title: "Create service account",
    sa_created: "Service account created.",
    sa_deleted: "Service account deleted.",
    sa_empty_home: "No service accounts in this tenant yet.",
    sa_empty_cross: "No service accounts across all tenants.",
    sa_delete_confirm_title: "Delete this service account?",
    sa_delete_confirm_body:
      "All API keys bound to this service account will stop authenticating immediately.",
    rb_page_title: "Role Bindings",
    rb_subtitle:
      "Bind a user or service account to a role. platform_scope=true grants cross-tenant SYSTEM_ADMIN; only system_admins can create those bindings.",
    rb_create: "Create Role Binding",
    rb_create_drawer_title: "Create role binding",
    rb_create_submit: "Create",
    rb_create_platform_submit: "Create platform-scope binding",
    rb_filter_platform_scope: "Show platform-scope only",
    rb_created: "Role binding created.",
    rb_deleted: "Role binding deleted.",
    rb_empty_home: "No role bindings in this tenant yet.",
    rb_empty_cross: "No role bindings across all tenants.",
    rb_empty_platform: "No platform-scope bindings.",
    rb_delete_confirm_title: "Delete this role binding?",
    rb_delete_confirm_body: "The subject loses this role immediately.",
    rb_delete_platform_warn:
      "Deleting a platform-scope binding revokes cross-tenant SYSTEM_ADMIN from the subject.",
    rb_confirm_required:
      "Type the confirmation phrase exactly to authorize the platform-scope binding.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load",
    col_name: "Name",
    col_description: "Description",
    col_active: "Active",
    col_created: "Created",
    col_subject_type: "Subject type",
    col_subject_id: "Subject ID",
    col_role: "Role",
    col_tenant: "Tenant",
    col_conditions: "Conditions",
    col_granted_at: "Granted at",
    col_actions: "Actions",
    conditions_none: "type-wide",
    section_conditions: "ABAC conditions (optional)",
    conditions_hint:
      "Narrow this grant to matching resource instances. Leave empty for type-wide access.",
    field_resource_ids: "Resource IDs",
    resource_ids_hint: "Agent names this role may act on. Empty = any.",
    resource_ids_placeholder: "agent-foo, agent-bar",
    field_labels: "Labels",
    labels_hint:
      "Match resources whose metadata.labels superset these (k=v, comma-separated).",
    field_owner_only: "Owner only (resources created by this subject)",
    field_name: "Name",
    field_description: "Description",
    field_subject_type: "Subject type",
    field_subject_id: "Subject ID (UUID)",
    field_role: "Role",
    field_platform_scope: "platform_scope (cross-tenant)",
    platform_scope_hint:
      "Only system_admins can set this. Required when role=system_admin.",
    platform_scope_warn_title: "Cross-tenant elevation",
    platform_scope_warn_body:
      "This grants the subject SYSTEM_ADMIN across every tenant. Cannot be undone except by deleting the binding. Operation will be audited.",
    role_requires_platform_scope:
      "role=system_admin requires platform_scope=true (backend DTO validator).",
    confirm_phrase_label: "Type {{phrase}} to confirm",
    name_required: "Name is required",
    subject_id_required: "Subject ID is required",
    role_required: "Role is required",
  },
  settings_members: {
    page_title: "Members",
    subtitle:
      "People in this tenant and their invitation lifecycle. Invite by email, resend pending invites, or remove a member.",
    invite: "Invite",
    filter_all: "All",
    col_email: "Email",
    col_name: "Name",
    col_role: "Role",
    col_status: "Status",
    col_tenant: "Tenant",
    col_invited_at: "Invited at",
    col_actions: "Actions",
    cross_tenant_banner:
      "Cross-tenant view — read-only. Switch to a single tenant to invite or manage members.",
    status_invited: "Invited",
    status_active: "Active",
    status_suspended: "Suspended",
    status_revoked: "Revoked",
    resend: "Resend",
    remove: "Remove",
    set_password: "Set password",
    set_password_title: "Set a temporary password",
    set_password_hint: "The member must change it on first login.",
    set_password_label: "Temporary password",
    set_password_placeholder: "At least 8 characters",
    set_password_submit: "Set password",
    set_password_ok: "Password set.",
    set_password_failed: "Failed to set password",
    set_password_too_short: "At least 8 characters",
    revoke_confirm_title: "Revoke this invite?",
    revoke_confirm_body:
      "The pending invitation is cancelled and the link stops working immediately.",
    suspend_confirm_title: "Suspend this member?",
    suspend_confirm_body:
      "The member loses access immediately. They can be re-invited later.",
    invite_drawer_title: "Invite a member",
    field_email: "Email",
    field_role: "Role",
    field_display_name: "Display name",
    field_display_name_placeholder: "Optional",
    email_required: "Email is required",
    email_invalid: "Enter a valid email address",
    role_required: "Role is required",
    invite_submit: "Send invite",
    invite_success: "Invitation sent.",
    invite_partial_fail: "Some invitations failed: {{detail}}",
    resent: "Invitation resent.",
    removed: "Member removed.",
    failed_to_load: "Failed to load members",
    empty: "No members in this tenant yet.",
    empty_cross: "No members across all tenants.",
  },
  settings_platform_users: {
    page_title: "Platform Admins",
    subtitle:
      "Platform-level system_admin grants. Admins see every tenant and manage platform credentials, the MCP catalog and other cross-tenant settings. system_admin only.",
    not_admin_title: "system_admin only",
    not_admin_body:
      "Platform admins are managed by existing system_admins. Ask one to grant you the role.",
    failed_to_load: "Failed to load platform admins.",
    col_subject: "Subject",
    col_role: "Role",
    col_granted_by: "Granted by",
    col_granted_at: "Granted at",
    col_actions: "Actions",
    you: "You",
    grant: "Grant admin",
    granted: "Platform admin granted.",
    revoke: "Revoke",
    revoked: "Platform admin revoked.",
    revoke_confirm: "Revoke this platform admin?",
    revoke_self_warning:
      "This is your own grant — revoking it removes your platform access.",
    subject_label: "Subject UUID",
    subject_hint:
      "The Keycloak user UUID (the `sub` claim), not an email address.",
    subject_invalid: "Enter a valid UUID (Keycloak `sub`).",
  },
  triggers: {
    page_title: "Triggers",
    subtitle:
      "Agent auto-start hooks — cron expression timers or external webhook ingest (HMAC secret auth). Cross-tenant view requires system_admin.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load triggers",
    empty_cron: "No cron triggers yet.",
    empty_webhook: "No webhook triggers yet.",
    empty_cross: "No triggers across all tenants.",
    tab_cron: "Cron",
    tab_webhook: "Webhook",
    col_name: "Name",
    col_agent: "Agent",
    col_cron_expr: "Cron expression",
    col_webhook_path: "Webhook path",
    col_enabled: "Enabled",
    col_updated: "Updated",
    col_actions: "Actions",
    create: "Create Trigger",
    create_submit: "Create",
    create_cron_title: "Create cron trigger",
    create_webhook_title: "Create webhook trigger",
    field_name: "Name",
    field_agent_name: "Agent name",
    field_agent_version: "Agent version",
    field_cron_expr: "Cron expression",
    name_required: "Name is required",
    agent_required: "Agent name is required",
    agent_version_required: "Agent version is required",
    cron_required: "Cron expression is required",
    cron_hint: 'Standard 5-field cron, e.g. "0 9 * * *" runs at 09:00 daily.',
    webhook_secret_info_title: "Webhook secret will be generated server-side",
    webhook_secret_info_body:
      "After creation a one-time secret will be shown. Save it now — there is no way to retrieve it later (rotation = delete + re-create in M0).",
    secret_drawer_title: "Webhook secret (show once)",
    secret_warn_title: "Save this secret now",
    secret_warn_body:
      "This is the only time the full secret is shown. Treat it like a password — copy it to your secret manager before closing.",
    secret_trigger_name: "Trigger",
    secret_label: "Secret",
    secret_copy: "Copy",
    secret_copied: "Secret copied to clipboard.",
    secret_copy_failed: "Clipboard copy failed — manually copy from the field.",
    secret_acknowledged: "I saved the secret",
    toggled: "Trigger updated.",
    created: "Trigger created.",
    deleted: "Trigger deleted.",
    delete_confirm_title: "Delete this trigger?",
    delete_confirm_body:
      "The cron schedule / webhook endpoint will stop firing immediately. Cannot be undone.",
  },
  webhooks: {
    page_title: "Webhooks",
    subtitle:
      "Outbound webhook endpoints — the platform signs and POSTs agent-lifecycle events (run completed / failed, approval requested, artifact saved) to your URL.",
    cross_tenant_banner: "Cross-tenant view",
    failed_to_load: "Failed to load webhook endpoints",
    empty:
      "No webhook endpoints yet. Create one to receive signed event deliveries.",
    empty_cross: "No webhook endpoints across any tenant.",
    all_agents: "All agents",
    col_name: "Name",
    col_url: "URL",
    col_events: "Events",
    col_agent: "Agent",
    col_enabled: "Enabled",
    col_actions: "Actions",
    create: "New endpoint",
    create_submit: "Create",
    create_title: "New webhook endpoint",
    field_name: "Name",
    field_url: "Delivery URL",
    url_hint:
      "HTTPS endpoint the platform POSTs to. Private / metadata addresses are rejected.",
    field_events: "Event types",
    events_placeholder: "Select one or more events",
    field_agent_name: "Agent scope (optional)",
    agent_name_hint:
      "Leave blank to receive events from every agent in the tenant.",
    name_required: "Name is required",
    url_required: "URL is required",
    events_required: "Select at least one event type",
    secret_drawer_title: "Signing secret — shown once",
    secret_warn_title: "Copy this secret now",
    secret_warn_body:
      "This HMAC signing secret is shown only once and never again. Verify the X-Helix-Signature-256 header with it. Rotation = delete + re-create.",
    secret_endpoint_name: "Endpoint",
    secret_label: "Signing secret",
    secret_copy: "Copy",
    secret_copied: "Secret copied to clipboard",
    secret_copy_failed: "Copy failed — select the text manually",
    secret_acknowledged: "I've saved it",
    toggled: "Endpoint updated",
    created: "Webhook endpoint created",
    deleted: "Webhook endpoint deleted",
    delete_confirm_title: "Delete this webhook endpoint?",
    delete_confirm_body:
      "Deliveries to this endpoint stop immediately. Cannot be undone.",
  },
  skills: {
    page_title: "Skills",
    subtitle:
      "Reusable skill library — each skill carries versions with prompt fragments + tool name allow-lists. Import / export as .skill ZIP for cross-instance sync.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load skills",
    empty_home: "No skills in this tenant yet.",
    empty_cross: "No skills across all tenants yet.",
    col_name: "Name",
    col_status: "Status",
    col_category: "Category",
    col_description: "Description",
    col_updated: "Updated",
    col_created: "Created",
    col_version: "Version",
    col_tools: "Tools",
    col_authored_by: "Authored by",
    col_actions: "Actions",
    filter_status: "Status filter",
    filter_status_all: "All statuses",
    filter_category: "Category",
    load_more: "Load more",
    create: "Create",
    create_modal_title: "Create skill (empty draft)",
    import_zip: "Import ZIP",
    export_zip: "Export ZIP",
    field_name: "Skill name (a-z, 0-9, _, -)",
    field_category: "Category",
    field_description: "Description",
    name_required: "Name is required",
    category_required: "Category is required",
    description_required: "Description is required",
    create_hint:
      "Empty draft — add a version (prompt fragment + tool names) via API or ZIP import to make it usable.",
    created: "Skill created.",
    imported: "Imported {{name}} v{{version}}.",
    status_changed: "Status changed to {{status}}.",
    change_status: "Change status",
    metadata_title: "Metadata",
    versions_title: "Versions",
    no_versions: "No versions yet — add one via API or ZIP import.",
    latest_version_hint: "Latest version number",
    detail_files_title: "Files",
    detail_editor_title: "Editor",
    detail_no_file_selected: "Select a file from the tree to view or edit it.",
    detail_select_file_hint:
      "All edits create a new immutable version — the prior version stays intact for rollback.",
    detail_skill_md_pinned: "SKILL.md (main body)",
    detail_supporting_files_section: "Supporting files",
    detail_no_supporting_files:
      "No supporting files. Use + Add to upload reference docs, prompts, or scripts.",
    detail_version_picker_label: "Version",
    detail_active_version_marker: "(latest)",
    detail_lazy_badge: "Lazy",
    detail_lazy_tooltip:
      "Body fetched on-demand via the skill_view tool — only the summary is in the system prompt.",
    detail_eager_tooltip:
      "Body eager-loaded into the system prompt at agent build time.",
    detail_high_risk_badge: "High-risk",
    detail_high_risk_tooltip:
      "Declares exec_python / exec_shell / http, or carries scripts/* files — Activate requires admin role.",
    detail_high_risk_warning:
      "This version is high-risk. Review the supporting files + tool list carefully before activating.",
    detail_admin_required_tooltip: "Contact a tenant admin to activate.",
    detail_unsaved_changes_warning:
      "You have unsaved changes. Save or cancel before switching files.",
    detail_diff_toggle: "Show diff vs. server",
    detail_diff_original_label: "Server",
    detail_diff_draft_label: "Draft",
    file_add: "+ Add file",
    file_add_modal_title: "Add supporting file",
    file_add_path_label: "Relative path",
    file_add_path_placeholder: "e.g. reference/error_codes.md",
    file_add_content_label: "Content (text)",
    file_add_upload_label: "Or upload a file",
    file_add_upload_hint:
      "Max 1 MB per file, 5 MB per skill total. Path / extension validated server-side.",
    file_add_submit: "Add",
    file_action_edit: "Edit",
    file_action_delete: "Delete",
    file_action_rename: "Rename",
    file_action_save: "Save",
    file_action_cancel: "Cancel",
    file_save_failed: "Save failed",
    file_saved: "Saved as v{{version}}.",
    file_deleted: "Deleted — new v{{version}} created.",
    file_delete_confirm_title: "Delete {{path}}?",
    file_delete_confirm_body:
      "A new SkillVersion will be created without this file. The prior version stays intact.",
    file_delete_confirm_input_hint: "Type the file path to confirm",
    file_rename_modal_title: "Rename {{path}}",
    file_rename_new_path_label: "New relative path",
    file_rename_submit: "Rename",
    file_renamed: "Renamed — new v{{version}} created.",
    file_binary_placeholder:
      "[BINARY: {{size}} bytes, mime={{mime}}] — preview disabled. Export the ZIP to inspect.",
    file_size_label: "Size",
    file_mime_label: "MIME",
    file_load_failed: "Failed to load file",
    detail_skill_md_edit_hint:
      "SKILL.md is the skill's core prompt. Saving an edit creates a new version and keeps all supporting files.",
    pin: "Pin",
    unpin: "Unpin",
    pinned_toast: "Pinned — Curator will skip this skill at every stage.",
    unpinned_toast: "Unpinned — Curator will treat this skill normally.",
    pin_tooltip_on: "Pinned. Curator skips this skill at every transition.",
    pin_tooltip_off: "Pin to exempt this skill from auto-stale / auto-archive.",
    eta_days_to_stale: "{{days}}d to stale",
    eta_due_soon: "stale soon",
    source_platform: "Platform",
    source_tenant: "Mine",
    requires_tier: "Requires {{tier}}",
    requires_tier_tooltip:
      "Your plan doesn't include this skill — upgrade to {{tier}} to use it.",
  },
  skill_marketplace: {
    page_title: "Skill Marketplace",
    subtitle:
      "Browse the platform-curated skill library and enable the skills your agents should use.",
    failed_to_load: "Failed to load the skill marketplace",
    empty: "No platform skills are available yet.",
    cross_tenant_title: "Pick a tenant",
    cross_tenant_hint:
      "The skill marketplace is per-tenant. Switch from all-tenants to a specific tenant to browse and enable skills.",
    tier_free: "Free",
    tier_pro: "Pro",
    tier_enterprise: "Enterprise",
    requires_tier: "Requires {{tier}}",
    locked_ribbon: "Locked",
    enable: "Enable",
    enabled: "Enabled",
    subscribed_ok: "Enabled “{{name}}”",
    unsubscribed_ok: "Disabled “{{name}}”",
  },
  platform_skills: {
    page_title: "Platform Skills",
    subtitle:
      "Curate reusable skills tenants can bind to their agents. Platform-level, system admins only.",
    add: "New skill",
    import_zip: "Import .skill",
    import_github: "Import from GitHub",
    github_modal_title: "Import skill from GitHub",
    github_hint:
      "Pull a skill from a public GitHub repo. Accepts owner/repo, a github.com URL, or a skills.sh URL. For a repo with many skills, name which one.",
    github_source_label: "Source",
    github_source_ph: "vercel-labs/skills  ·  github.com/…  ·  skills.sh/…",
    github_skill_label: "Skill",
    github_skill_ph: "find-skills (optional — required if the repo has many)",
    github_ref_label: "Ref",
    github_ref_ph: "branch / tag / SHA (optional, default branch)",
    github_submit: "Import",
    imported: 'Imported "{{name}}" v{{version}}.',
    import_noop: 'No change to "{{name}}" — already the latest version.',
    manage: "Manage",
    pin: "Pin",
    unpin: "Unpin",
    not_admin_title: "System admin only",
    not_admin_body:
      "The platform skill catalog is managed by system admins. Ask one to add skills.",
    failed_to_load: "Failed to load platform skills",
    col_name: "Name",
    col_category: "Category",
    col_tier: "Required plan",
    col_status: "Status",
    col_version: "Version",
    col_actions: "Actions",
    empty_title: "No platform skills yet",
    empty_hint:
      "Add curated skills so every tenant can bind battle-tested capabilities without reinventing them.",
    tier_free: "Free",
    tier_pro: "Pro",
    tier_enterprise: "Enterprise",
    status_draft: "Draft",
    status_active: "Active",
    status_archived: "Archived",
    create_title: "New platform skill",
    create_submit: "Create",
    field_name: "Name (slug)",
    field_name_hint: "Lowercase identifier, immutable after creation",
    field_category: "Category",
    field_description: "Description",
    field_required_tier: "Required plan",
    name_required: "A valid lowercase slug is required",
    created: "Platform skill created.",
    duplicate_name: "A platform skill with this name already exists.",
    when_to_use_hint:
      "Describe WHEN to use this skill — model-driven selection keys off this text.",
    manage_title: "Manage {{name}}",
    lifecycle_title: "Lifecycle",
    add_version_title: "Add version",
    add_version_submit: "Add version",
    field_prompt_fragment: "Prompt fragment",
    field_version_description: "Description",
    field_tool_names: "Tool names",
    field_required_models: "Required models",
    prompt_fragment_required: "Prompt fragment is required",
    csv_hint: "Comma-separated.",
    versions_title: "Versions",
    no_versions: "No versions yet — add one above to make this skill usable.",
    version_added: "Added v{{version}}.",
    status_changed: "Status changed to {{status}}.",
    pinned: "Pinned",
    unpinned: "Unpinned",
    high_risk: "High-risk",
    lazy: "Lazy",
  },
  memory: {
    page_title: "Memory",
    subtitle:
      "Per-user long-term memories used for recall during agent runs. Edit / delete here is destructive; vector embeddings re-compute on PATCH.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load memories",
    empty_home: "No memories in this tenant yet.",
    empty_cross: "No memories across all tenants yet.",
    col_kind: "Kind",
    col_content: "Content",
    col_user: "User",
    col_created: "Created",
    col_actions: "Actions",
    filter_kind: "Kind filter",
    filter_kind_all: "All kinds",
    search_placeholder: "Filter by content (client-side)",
    edit_title: "Edit memory",
    edit_meta_user: "User",
    edit_meta_kind: "Kind",
    edit_content_label: "Content",
    save_dirty: "Save (edited)",
    embedder_note:
      "PATCH re-computes the embedding so vector recall stays consistent with the new content.",
    embedder_unconfigured:
      "Backend has no embedder configured — memory PATCH refused (HELIX_AGENT_EMBEDDING_API_KEY_REF + MODEL required).",
    empty_content: "Content cannot be empty.",
    updated: "Memory updated.",
    deleted: "Memory deleted.",
    delete_confirm_title: "Delete this memory?",
    delete_confirm_body:
      "Soft-delete; row recoverable for 30 days then gone permanently.",
  },
  eval_datasets: {
    failed_to_load: "Failed to load eval datasets",
    empty_home: "No eval datasets in this tenant yet.",
    empty_cross: "No eval datasets across all tenants yet.",
    col_name: "Name",
    col_agent: "Agent",
    col_source: "Source",
    col_updated: "Updated",
    col_actions: "Actions",
    create: "Create",
    create_modal_title: "Create eval dataset",
    field_agent_name: "Agent name",
    field_name: "Dataset name",
    agent_required: "Agent name is required",
    name_required: "Name is required",
    edit_title: "Edit eval dataset",
    edit_input_label: "Input (JSON object)",
    edit_expected_label: "Expected output (JSON object or empty for null)",
    json_parse_error: "JSON parse error",
    created: "Eval dataset created.",
    updated: "Eval dataset updated.",
    deleted: "Eval dataset deleted.",
    delete_confirm_title: "Delete this eval dataset?",
    delete_confirm_body:
      "This row will be removed from the golden suite. Cannot be undone.",
  },
  api_keys: {
    page_title: "API Keys",
    subtitle:
      "Service-account access keys. Each key carries scopes (read / write / admin). Supports rotation (double-active grace) and immediate revocation.",
    create: "Create API Key",
    failed_to_load: "Failed to load API keys",
    empty: "No API keys yet. Create one bound to a service account to start.",
    never: "never",
    rotation_banner: "{{count}} keys in rotation grace window",
    rotation_help:
      "Old keys are still valid until the grace window expires; have callers swap to the new key now.",
    col_prefix: "Prefix",
    col_scopes: "Scopes",
    col_service_account: "Service Account",
    col_status: "Status",
    col_last_used: "Last used",
    col_expires: "Expires",
    rotate: "Rotate",
    revoke: "Revoke",
    revoke_confirm: "Revoke this key immediately?",
    rotated:
      "API key rotated. Old key remains valid until the grace window expires.",
    revoked: "API key revoked.",
    service_account_label: "Service Account",
    service_account_required: "Pick a service account",
    scopes_label: "Scopes",
    scopes_required: "Pick at least one scope",
    expires_label: "Expires at",
    dangerous: "dangerous",
    show_once_title: "API Key created",
    show_once_ack: "I saved the key, close",
    show_once_help_prefix: "Copy this key immediately and store it securely — ",
    show_once_help_emphasis: "you will not be able to view the full key again",
    show_once_prefix_note: "Only the prefix is retained on the list:",
    copy: "Copy",
    copied: "Copied to clipboard",
  },
  tenant: {
    home_label_prefix: "Home",
    home_tenant: "Home tenant",
    all_tenants: "All tenants",
    your_tenant: "your tenant",
    system_admin_hint: "system admin",
    cross_tag: "cross",
  },
  agents_page: {
    page_title: "Agents",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load agents",
    empty_home: "No agents in this tenant yet — click Create to add one.",
    empty_cross: "No agents across all tenants yet.",
    column_name: "Name",
    column_status: "Status",
    column_tenant: "Tenant",
    column_created: "Created",
    create: "Create",
  },
  create_agent: {
    title: "Create Agent",
    hint: "Edit the manifest then click Create. The backend validates the YAML through ManifestLoader; errors surface here.",
    submit: "Create",
    cancel: "Cancel",
    create_failed: "Failed to create agent",
    embedding_required_title: "Configure platform embedding first",
    embedding_required_desc:
      "New agents use long-term memory, which needs a platform embedding model. No embedding is configured yet — set one in Platform Settings, then create your agent.",
    embedding_required_cta: "Go to Platform Settings",
  },
  approvals_page: {
    page_title: "Approvals",
    cross_tenant_banner: "cross-tenant view (read-only)",
    filter_status: "Filter by status",
    failed_to_load: "Failed to load approvals",
    empty: "No approvals in this view — nothing is waiting on you.",
    column_reason: "Reason",
    column_action: "Requested action",
    column_waiting: "Waiting",
    column_timeout: "Times out",
    column_status: "Status",
    column_actions: "Decision",
    approve: "Approve",
    reject: "Reject",
    confirm_approve: "Approve this action?",
    confirm_reject: "Reject this action?",
    batch_approve: "Approve selected",
    batch_reject: "Reject selected",
    confirm_batch_approve: "Approve {{count}} selected actions?",
    confirm_batch_reject: "Reject {{count}} selected actions?",
    selected_count: "{{count}} selected",
    decide_ok: "{{count}} decision(s) applied",
    decide_partial:
      "{{ok}} applied, {{failed}} failed (already decided or gone) — list refreshed",
    decide_failed: "Decision request failed: {{error}}",
    select_row: "Select approval: {{summary}}",
    select_all: "Select all approvals on this page",
    waiting_minutes: "{{count}} min",
    waiting_hours: "{{count}} h",
    modify_hint:
      "To approve with edited arguments, open the run and use the approval card there.",
  },
  runs_page: {
    page_title: "Runs",
    subtitle: "Cross-thread index of every agent run.",
    cross_tenant_banner: "cross-tenant view",
    failed_to_load: "Failed to load runs",
    empty_home: "No runs in this tenant yet.",
    empty_cross: "No runs across all tenants yet.",
    column_run_id: "Run ID",
    column_status: "Status",
    column_thread: "Thread",
    column_agent: "Agent",
    column_created: "Created",
    filter_status: "Status filter",
    filter_status_all: "All statuses",
    detail_hint: "Need to start a new run?",
    detail_hint_link: "Open the Playground tab on an agent.",
  },
  eval_runs_page: {
    page_title: "Eval Runs",
    subtitle: "Trigger and monitor capability evaluation runs.",
    failed_to_load: "Failed to load eval runs",
    empty: "No eval runs yet. Enqueue the baseline suite to start.",
    column_run_id: "Run ID",
    column_status: "Status",
    column_suite: "Suite",
    column_summary: "Summary",
    column_created: "Created",
    filter_status: "Status filter",
    filter_status_all: "All statuses",
    suite_label: "Suite to run",
    enqueue: "Enqueue run",
    enqueue_success: "Queued an eval run.",
  },
  eval_run_detail: {
    failed_to_load: "Failed to load eval run",
    not_found: "eval run not found",
    run_metadata: "Run metadata",
    run_id: "Run ID",
    suite: "Suite",
    status: "Status",
    triggered_by: "Triggered by",
    created_at: "Created",
    started_at: "Started",
    finished_at: "Finished",
    summary: "Summary",
    cases_title: "Case results",
    cases_empty: "No case results recorded yet.",
    col_case_id: "Case",
    col_capability: "Capability",
    col_result: "Result",
    col_scores: "Scores",
    result_passed: "passed",
    result_failed: "failed",
  },
  usage: {
    page_title: "Usage",
    subtitle: "Billed cost and token usage for your tenant this month.",
    group_by_agent: "By Agent",
    group_by_model: "By Model",
    total_billed: "Total billed",
    as_of_note: "Cost as of {{time}}; updates hourly.",
    col_key: "Name",
    col_input_tokens: "Input tokens",
    col_output_tokens: "Output tokens",
    col_cache_creation_tokens: "Cache write tokens",
    col_cache_read_tokens: "Cache read tokens",
    col_billed: "Billed cost",
    unpriced: "Unpriced",
    tokens_heading: "Token usage",
    realtime: "Realtime",
    realtime_note:
      "Live current-month counters; billed cost above lags by up to an hour.",
    empty: "No usage recorded for this month.",
    failed_to_load: "Failed to load usage",
  },
  chargeback: {
    page_title: "Chargeback",
    subtitle:
      "Cross-tenant cost split — base, markup, billed and margin per tenant.",
    not_admin_title: "system_admin only",
    not_admin_body:
      "The chargeback report is restricted to platform administrators.",
    tenant_filter: "Filter by tenant ID",
    as_of: "As of",
    total_base: "Total base cost",
    total_billed: "Total billed",
    total_margin: "Total margin",
    col_tenant: "Tenant",
    col_agent: "Agent",
    col_input_tokens: "Input tokens",
    col_output_tokens: "Output tokens",
    col_base: "Base cost",
    col_markup: "Markup",
    col_billed: "Billed",
    col_margin: "Margin",
    col_unpriced: "Unpriced buckets",
    empty: "No chargeback data for this month.",
    agents_empty: "No per-agent cost for this tenant this month.",
    failed_to_load: "Failed to load chargeback",
  },
  coming_soon: {
    title_prefix: "This page is being implemented",
    body: "Demo only shows the 4 core pages: Agents list / Agent detail (with Playground) / Run+Approval / Settings API Keys.",
    other_pages_prefix: "Other pages",
    other_pages_suffix: "will land in Stream H.",
  },
  cmdk: {
    placeholder: "Search or jump — type a command, Agent name, Run ID…",
    aria_label: "Command palette search",
    no_matches: "No matches",
    group_agents: "Agents",
    group_jump: "Jump",
    group_action: "Actions",
    label_runs: "Runs (across agents)",
    label_approvals: "Approvals (queue)",
    label_curation: "Curation review",
    label_eval: "Eval runs",
    label_memory: "Memory",
    label_artifacts: "Artifacts",
    label_knowledge: "Knowledge",
    label_skills: "Skills",
    label_skill_marketplace: "Skill Marketplace",
    label_triggers: "Triggers",
    label_webhooks: "Webhooks",
    label_settings_api_keys: "Settings · API Keys",
    label_settings_platform_users: "Settings · Platform Admins",
    action_create_agent: "Create new Agent…",
    action_create_api_key: "Create new API Key…",
    action_open_settings: "Open Settings",
    hint_select: "Select",
    hint_jump: "Jump",
    hint_close: "Close",
    hint_shortcuts: "Type ? for shortcuts",
  },
  skill_evolution: {
    governance_title: "Evolution governance",
    visibility_agent_private: "Agent-private",
    visibility_tenant: "Tenant-wide",
    owner: "Owner",
    forked_from: "Forked",
    propose_to_tenant: "Propose to tenant",
    pending_tenant_promotion: "Pending tenant promotion",
    approve: "Approve",
    reject: "Reject",
    proposed_toast: "Promotion requested",
    approved_toast: "Promoted to tenant",
    rejected_toast: "Promotion rejected",
    no_version_to_propose: "This skill has no published version to propose",
    filter_visibility: "Visibility filter",
    filter_visibility_all: "All visibility",
    eval_title: "Replay evidence",
    eval_empty: "No replay verification yet",
    eval_n_cases: "{{n}} cases",
    eval_aria: "baseline {{baseline}} vs with-skill {{skill}}",
    eval_baseline: "baseline",
    eval_with_skill: "with skill",
    verdict_pass: "pass",
    verdict_fail: "fail",
    verdict_inconclusive: "inconclusive",
    lineage_title: "Lineage",
    lineage_versions: "Versions",
    lineage_fork_aria: "fork lineage diagram",
    origin_human: "human",
    origin_in_session: "self-authored",
    origin_distilled: "distilled",
    kill_switch_engaged_toast: "Emergency stop engaged",
    kill_switch_released_toast: "Emergency stop released",
    kill_switch_confirm_title: "Engage emergency stop?",
    kill_switch_confirm_body:
      "Auto-promotion of new skills is degraded to human review until released.",
    kill_switch_engage: "Engage",
    kill_switch_hint: "Persistent emergency stop for auto-promotion",
    kill_switch_halted: "Auto-evolution halted",
    kill_switch_active: "Auto-evolution active",
    kill_switch_tenant_label: "Tenant",
    kill_switch_global_label: "Global",
    kill_switch_tenant_aria: "Toggle tenant auto-evolution emergency stop",
    kill_switch_global_aria: "Toggle global auto-evolution emergency stop",
  },
};

export default en;
