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
    home: string;
    anonymous: string;
  };
  theme: {
    switch_to_light: string;
    switch_to_dark: string;
    toggle: string;
  };
  nav: {
    settings_group: string;
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
  agent_detail: {
    failed_to_load: string;
    tab_overview: string;
    tab_manifest: string;
    tab_playground: string;
    tab_runs: string;
    tab_skills: string;
    tab_triggers: string;
    tab_memory: string;
    tab_coming_soon: string;
    config_summary: string;
    field_id: string;
    field_tenant: string;
    field_spec_sha: string;
    field_status: string;
    field_created: string;
    field_updated: string;
  };
  manifest_tab: {
    read_only_hint: string;
    edit_hint: string;
    edit: string;
    save: string;
    cancel: string;
    save_failed: string;
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
    label_curation: string;
    label_memory: string;
    label_skills: string;
    label_triggers: string;
    label_settings_api_keys: string;
    action_create_agent: string;
    action_create_api_key: string;
    action_open_settings: string;
    hint_select: string;
    hint_jump: string;
    hint_close: string;
    hint_shortcuts: string;
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
    home: "Home",
    anonymous: "anonymous",
  },
  theme: {
    switch_to_light: "Switch to Light",
    switch_to_dark: "Switch to Dark",
    toggle: "Toggle theme",
  },
  nav: {
    settings_group: "Settings",
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
  agent_detail: {
    failed_to_load: "Failed to load agent",
    tab_overview: "Overview",
    tab_manifest: "Manifest",
    tab_playground: "Playground",
    tab_runs: "Runs",
    tab_skills: "Skills",
    tab_triggers: "Triggers",
    tab_memory: "Memory",
    tab_coming_soon: "Tab \"{{tab}}\" lands in Stream H.2.",
    config_summary: "Configuration",
    field_id: "Record ID",
    field_tenant: "Tenant",
    field_spec_sha: "Spec sha256",
    field_status: "Status",
    field_created: "Created",
    field_updated: "Updated",
  },
  manifest_tab: {
    read_only_hint: "Read-only — click Edit to modify the spec.",
    edit_hint: "Editing — Save writes through PUT /v1/agents, Cancel discards changes.",
    edit: "Edit",
    save: "Save",
    cancel: "Cancel",
    save_failed: "Failed to save manifest",
  },
  playground: {
    session_label: "Session",
    new_session: "New session",
    session_failed: "Failed to create session",
    thread_id: "thread",
    loading_thread: "Creating thread…",
    input_placeholder: "Type a prompt to send to the agent. The full SSE event stream lands on the right.",
    run: "Run",
    running: "Running…",
    stop: "Stop",
    event_log: "Event log",
    event_count: "{{n}} events",
    stream_failed: "Stream failed",
    empty_log: "No events yet — click Run to start.",
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
    subtitle: "Review candidate runs surfaced by the curation worker and promote them to golden eval datasets.",
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
    trajectory_missing: "Trajectory artifact missing — promote / dismiss still allowed.",
    promote: "Promote",
    dismiss: "Dismiss",
    promote_modal_title: "Promote candidate to eval dataset",
    promote_dataset_name: "Eval dataset name",
    promote_name_required: "Name is required",
    promote_hint: "Trajectory input + expected output are copied from the candidate. Edit later via the Eval Datasets tab.",
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
    cron_hint: "Standard 5-field cron, e.g. \"0 9 * * *\" runs at 09:00 daily.",
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
    create_hint: "Empty draft — add a version (prompt fragment + tool names) via API or ZIP import to make it usable.",
    created: "Skill created.",
    imported: "Imported {{name}} v{{version}}.",
    status_changed: "Status changed to {{status}}.",
    change_status: "Change status",
    metadata_title: "Metadata",
    versions_title: "Versions",
    no_versions: "No versions yet — add one via API or ZIP import.",
    latest_version_hint: "Latest version number",
  },
  memory: {
    page_title: "Memory",
    subtitle: "Per-user long-term memories used for recall during agent runs. Edit / delete here is destructive; vector embeddings re-compute on PATCH.",
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
    embedder_note: "PATCH re-computes the embedding so vector recall stays consistent with the new content.",
    embedder_unconfigured: "Backend has no embedder configured — memory PATCH refused (HELIX_AGENT_EMBEDDING_API_KEY_REF + MODEL required).",
    empty_content: "Content cannot be empty.",
    updated: "Memory updated.",
    deleted: "Memory deleted.",
    delete_confirm_title: "Delete this memory?",
    delete_confirm_body: "Soft-delete; row recoverable for 30 days then gone permanently.",
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
    delete_confirm_body: "This row will be removed from the golden suite. Cannot be undone.",
  },
  api_keys: {
    page_title: "API Keys",
    subtitle:
      "Service-account access keys. Each key carries scopes (read / write / admin). Supports rotation (double-active grace) and immediate revocation.",
    create: "Create API Key",
    failed_to_load: "Failed to load API keys",
    empty:
      "No API keys yet. Create one bound to a service account to start.",
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
    rotated: "API key rotated. Old key remains valid until the grace window expires.",
    revoked: "API key revoked.",
    service_account_label: "Service Account",
    service_account_required: "Pick a service account",
    scopes_label: "Scopes",
    scopes_required: "Pick at least one scope",
    expires_label: "Expires at",
    dangerous: "dangerous",
    show_once_title: "API Key created",
    show_once_ack: "I saved the key, close",
    show_once_help_prefix:
      "Copy this key immediately and store it securely — ",
    show_once_help_emphasis:
      "you will not be able to view the full key again",
    show_once_prefix_note:
      "Only the prefix is retained on the list:",
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
  coming_soon: {
    title_prefix: "This page is being implemented",
    body:
      "Demo only shows the 4 core pages: Agents list / Agent detail (with Playground) / Run+Approval / Settings API Keys.",
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
    label_curation: "Curation review",
    label_memory: "Memory",
    label_skills: "Skills",
    label_triggers: "Triggers",
    label_settings_api_keys: "Settings · API Keys",
    action_create_agent: "Create new Agent…",
    action_create_api_key: "Create new API Key…",
    action_open_settings: "Open Settings",
    hint_select: "Select",
    hint_jump: "Jump",
    hint_close: "Close",
    hint_shortcuts: "Type ? for shortcuts",
  },
};

export default en;
