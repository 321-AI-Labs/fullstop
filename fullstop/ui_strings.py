"""Every NEW user-visible string for the activity view, in one module.

Naming law (UI-CHARTER-V02): the owner may still rename the product; when
that sweep comes, THIS FILE (plus the rename of the package itself) is the
entire front-end pass. The JavaScript reads its strings from the STRINGS
blob below, injected verbatim by the server — no prose lives in the assets.
"""

# -- CLI surface -----------------------------------------------------------------

UI_COMMAND_HELP = "open the loopback activity view dashboard"
UI_MANIFEST_HELP = "path to the manifest whose workspace the dashboard watches"
UI_NO_BROWSER_HELP = "do not auto-open the browser; print the URL only"
UI_PORT_HELP = "first port to try for the dashboard (loopback only; falls forward to the next free port)"
UI_RUN_FLAG_HELP = "serve the activity view while this run executes (run stays foreground)"
UI_APPROVAL_TIMEOUT_HELP = (
    "seconds a browser approval card may wait before the run pauses in "
    "stopped_approval (only with --ui; default 300)")

URL_LINE = "fullstop activity view: {url}  (Ctrl-C to stop)"
RUN_UI_URL_LINE = "fullstop activity view for this run: {url}"
BROWSER_OPEN_FAILED = "note: could not auto-open a browser ({error}); open the URL above manually"

# -- Page chrome -------------------------------------------------------------------

PAGE_TITLE = "fullstop activity view"
PRODUCT_NAME = "fullstop"
TAGLINE = "a zero-dependency runtime for always-on agents"
TAB_RUN = "Run"
TAB_HISTORY = "History"
TAB_WIZARD = "New run"
TABS_ARIA_LABEL = "panels"
FOOTER_OFFLINE = "loopback only · no external assets · nothing leaves this machine"

# -- Live indicator ----------------------------------------------------------------

LIVE_LIVE = "live · polling"
LIVE_WAITING = "waiting"
LIVE_STOPPED = "stopped"

# -- Run panel ----------------------------------------------------------------------

RUN_HEADER_TITLE = "Current run"
RUN_EMPTY = "No run yet in this workspace. Start one with run --ui, or watch here after any run."
RUN_LOADING = "Loading run state..."
RUN_STATE_ERROR = "Could not read the checkpoint: {error}"
RUN_GOAL_LABEL = "Goal"
RUN_RUN_ID_LABEL = "Run"
RUN_STEPS_LABEL = "Steps"
RUN_COST_LABEL = "Cost"
RUN_COST_UNIT = "USD"
RUN_TOKENS_LABEL = "Tokens"
RUN_TOKENS_VALUE = "{input} in · {output} out"
RUN_FAILURE_LABEL = "Failure"
RUN_MANIFEST_LABEL = "Manifest"
RUN_VERIFY_BUTTON = "Verify evidence chain"
RUN_VERIFY_OK = "chain intact: {entries} entries verified"
RUN_VERIFY_BAD = "CHAIN BROKEN at entry {first_bad}"
RUN_VERIFY_ERROR = "verify failed: {error}"
RUN_VERIFY_QUEUED = "verifying..."
LOG_TITLE = "Activity"
LOG_EMPTY = "Nothing logged yet."
LOG_LOADING = "Loading activity..."
LOG_TRUNCATION_NOTE = (
    "Shown exactly as written to the activity log: credentials were scrubbed "
    "and very long values capped at write time by the run itself.")

# -- Event labels (one per types.EVENTS member; renderers key off these) ------------

EVENT_LABELS = {
    "run_start": "run started",
    "resume": "run resumed",
    "model_reply": "model reply",
    "tool_call": "tool call",
    "gate_decision": "gate decision",
    "approval_request": "approval requested",
    "approval_response": "approval answered",
    "sandbox_block": "sandbox block",
    "tool_result": "tool result",
    "step": "step complete",
    "checkpoint": "checkpoint saved",
    "guard_trip": "guard tripped",
    "config_loaded": "config loaded",
    "run_end": "run ended",
}

# -- Timeline fragments (labels and detail fragments inside event rows) -------------

MODEL_REPLY_SUMMARY = "reply · tokens {input} in / {output} out"
KV_GOAL_LABEL = "goal"
KV_STEPS_DONE_LABEL = "steps_done"
KV_N_LABEL = "n"
KV_MANIFEST_LABEL = "manifest"
FRAG_POLICY = "policy"
FRAG_LIMIT = "limit"
FRAG_VALUE = "value"
DETAIL_ARGS_LABEL = "args"
DETAIL_OUTPUT_LABEL = "output"
TOOL_RESULT_OK = "OK "
TOOL_RESULT_ERROR = "ERROR "

# -- Gate chips ----------------------------------------------------------------------

CHIP_ALLOW = "allow"
CHIP_ASK = "ask"
CHIP_DENY = "denied"
CHIP_REASON_LABEL = "reason"

# -- Approval cards -----------------------------------------------------------------

APPROVAL_TITLE = "Approval needed"
APPROVAL_SUBTITLE = "The gate will not run this until you answer."
APPROVAL_TOOL_LABEL = "Tool"
APPROVAL_REASON_LABEL = "Why the gate asked"
APPROVAL_REQUEST_LABEL = "Complete request"
APPROVAL_APPROVE = "Approve"
APPROVAL_DENY = "Deny"
APPROVAL_KEYBOARD_HINT = "Y approves · N denies (card focused)"
APPROVAL_ANSWERED_APPROVE = "approved — waiting for the run to pick it up"
APPROVAL_ANSWERED_DENY = "denied — waiting for the run to pick it up"
APPROVAL_EXPIRED = "this card expired; the run pauses in stopped_approval if nobody answered"
APPROVAL_MISSING = "no approval is waiting right now"

# -- History panel --------------------------------------------------------------------

HISTORY_TITLE = "Runs in this workspace"
HISTORY_EMPTY = "No runs recorded yet."
HISTORY_LOADING = "Loading history..."
HISTORY_RUN_STEPS = "{steps} steps"
HISTORY_RUN_TOKENS = "{input} in · {output} out"
HISTORY_STARTED = "started {ts}"
HISTORY_ENDED = "ended {ts}"
HISTORY_STATUS_INTERRUPTED = "interrupted"
HISTORY_STATUS_RUNNING = "running"
HISTORY_CURRENT_TAG = "current"

# -- Wizard ---------------------------------------------------------------------------

WIZARD_TITLE = "New run (manifest builder)"
WIZARD_INTRO = ("Builds one manifest file with an inline policy, validated by the same "
                "strict validators the runtime uses. It never runs anything, and it "
                "never writes credentials: the API key stays in your environment, "
                "referenced by name only.")
WIZARD_SECTION_IDENTITY = "Identity"
WIZARD_SECTION_GOAL = "Goal"
WIZARD_SECTION_PROVIDER = "Provider"
WIZARD_SECTION_LIMITS = "Limits"
WIZARD_SECTION_POLICY = "Policy"
WIZARD_SECTION_SAVE = "Save"
WIZARD_FIELD_NAME = "Agent name"
WIZARD_FIELD_ROLE = "Role"
WIZARD_FIELD_HOME = "Workspace home (absolute path)"
WIZARD_FIELD_HOME_HINT = "The agent's sandbox. The manifest file may not live inside it."
WIZARD_FIELD_GOAL = "Goal"
WIZARD_FIELD_PROVIDER_TYPE = "Provider type"
WIZARD_FIELD_SCRIPT_PATH = "Script path (scripted provider)"
WIZARD_FIELD_BASE_URL = "Base URL (openai_compat)"
WIZARD_FIELD_API_KEY_ENV = "API key env var NAME (openai_compat)"
WIZARD_FIELD_API_KEY_ENV_HINT = "The NAME of the environment variable only. The value is never typed here, never rendered, never stored."
WIZARD_FIELD_MODEL = "Model (openai_compat)"
WIZARD_FIELD_PRICE_IN = "USD per 1k input tokens (optional)"
WIZARD_FIELD_PRICE_OUT = "USD per 1k output tokens (optional)"
WIZARD_FIELD_MAX_STEPS = "Max steps"
WIZARD_FIELD_MAX_COST = "Max cost USD (optional)"
WIZARD_FIELD_MAX_CALLS = "Max tool calls per step"
WIZARD_FIELD_MAX_CONTEXT = "Max context chars (optional)"
WIZARD_FIELD_LOG_TRUNCATE = "Log truncate chars"
WIZARD_FIELD_CREDENTIALS = "Credential env var NAMES (comma-separated, optional)"
WIZARD_FIELD_CREDENTIALS_HINT = "NAMES only. Values are read from your environment at run time and scrubbed from everything written."
WIZARD_FIELD_PROTECTED = "Protected paths (one glob per line)"
WIZARD_FIELD_PREAPPROVED = "Write pre-approved globs (one per line)"
WIZARD_FIELD_SHELL_ALLOW = "Shell allow entries (one per line)"
WIZARD_FIELD_SHELL_ALLOW_HINT = 'A bare program string pre-approves only the bare invocation; {"program": "...", "args": ["*"...]} constrains arguments.'
WIZARD_FIELD_SHELL_DENY = "Shell deny programs (one per line)"
WIZARD_FIELD_WEB_ALLOW = "Web allow domains (one per line)"
WIZARD_FIELD_WEB_DENY = "Web deny domains (one per line)"
WIZARD_FIELD_SAVE_PATH = "Manifest file to create"
WIZARD_FIELD_SAVE_PATH_HINT = "Must not already exist. Written atomically; nothing else is touched."
WIZARD_VALIDATE_BUTTON = "Validate"
WIZARD_SAVE_BUTTON = "Save manifest"
WIZARD_VALIDATING = "validating..."
WIZARD_VALID_OK = "valid — the manifest passes the strict validators"
WIZARD_VALID_BAD = "the validators rejected this manifest:"
WIZARD_SAVED = "manifest written: {path} ({bytes} bytes)"
WIZARD_GLOBAL_ERRORS = "General problems:"
WIZARD_FIELD_ERRORS_HEADING = "Field problems:"
WIZARD_SAVE_REFUSED = "save refused: {reason}"
WIZARD_PATH_EXISTS = "that path already exists; the wizard only creates new files"
WIZARD_PATH_INSIDE_HOME = "that path resolves inside the workspace home; the gate's config must stay outside the agent's sandbox"

# -- Errors ----------------------------------------------------------------------------

ERROR_GENERIC = "request failed: {error}"
ERROR_NOT_FOUND = "not found: {path}"
ERROR_METHOD = "method not allowed"
ERROR_BAD_POST = "bad request: {reason}"
ERROR_FORBIDDEN_HOST = "refused: this server is loopback only"
ERROR_FORBIDDEN_HEADER = "refused: POST requires the local UI header"
ERROR_NO_PENDING = "no pending approval with that id"
ERROR_NO_HOME = "no workspace: {error}"
HTTP_ERROR_PREFIX = "HTTP "

# -- The blob injected into the JavaScript ----------------------------------------------

STRINGS = {
    "pageTitle": PAGE_TITLE,
    "productName": PRODUCT_NAME,
    "tagline": TAGLINE,
    "tabRun": TAB_RUN,
    "tabHistory": TAB_HISTORY,
    "tabWizard": TAB_WIZARD,
    "tabsAriaLabel": TABS_ARIA_LABEL,
    "footerOffline": FOOTER_OFFLINE,
    "liveLive": LIVE_LIVE,
    "liveWaiting": LIVE_WAITING,
    "liveStopped": LIVE_STOPPED,
    "runHeaderTitle": RUN_HEADER_TITLE,
    "runEmpty": RUN_EMPTY,
    "runLoading": RUN_LOADING,
    "runStateError": RUN_STATE_ERROR,
    "runGoalLabel": RUN_GOAL_LABEL,
    "runRunIdLabel": RUN_RUN_ID_LABEL,
    "runStepsLabel": RUN_STEPS_LABEL,
    "runCostLabel": RUN_COST_LABEL,
    "runCostUnit": RUN_COST_UNIT,
    "runTokensLabel": RUN_TOKENS_LABEL,
    "runTokensValue": RUN_TOKENS_VALUE,
    "runFailureLabel": RUN_FAILURE_LABEL,
    "runManifestLabel": RUN_MANIFEST_LABEL,
    "runVerifyButton": RUN_VERIFY_BUTTON,
    "runVerifyOk": RUN_VERIFY_OK,
    "runVerifyBad": RUN_VERIFY_BAD,
    "runVerifyError": RUN_VERIFY_ERROR,
    "runVerifyQueued": RUN_VERIFY_QUEUED,
    "logTitle": LOG_TITLE,
    "logEmpty": LOG_EMPTY,
    "logLoading": LOG_LOADING,
    "logTruncationNote": LOG_TRUNCATION_NOTE,
    "eventLabels": EVENT_LABELS,
    "modelReplySummary": MODEL_REPLY_SUMMARY,
    "kvGoalLabel": KV_GOAL_LABEL,
    "kvStepsDoneLabel": KV_STEPS_DONE_LABEL,
    "kvNLabel": KV_N_LABEL,
    "kvManifestLabel": KV_MANIFEST_LABEL,
    "fragPolicy": FRAG_POLICY,
    "fragLimit": FRAG_LIMIT,
    "fragValue": FRAG_VALUE,
    "detailArgsLabel": DETAIL_ARGS_LABEL,
    "detailOutputLabel": DETAIL_OUTPUT_LABEL,
    "toolResultOk": TOOL_RESULT_OK,
    "toolResultError": TOOL_RESULT_ERROR,
    "httpErrorPrefix": HTTP_ERROR_PREFIX,
    "chipAllow": CHIP_ALLOW,
    "chipAsk": CHIP_ASK,
    "chipDeny": CHIP_DENY,
    "chipReasonLabel": CHIP_REASON_LABEL,
    "approvalTitle": APPROVAL_TITLE,
    "approvalSubtitle": APPROVAL_SUBTITLE,
    "approvalToolLabel": APPROVAL_TOOL_LABEL,
    "approvalReasonLabel": APPROVAL_REASON_LABEL,
    "approvalRequestLabel": APPROVAL_REQUEST_LABEL,
    "approvalApprove": APPROVAL_APPROVE,
    "approvalDeny": APPROVAL_DENY,
    "approvalKeyboardHint": APPROVAL_KEYBOARD_HINT,
    "approvalAnsweredApprove": APPROVAL_ANSWERED_APPROVE,
    "approvalAnsweredDeny": APPROVAL_ANSWERED_DENY,
    "approvalExpired": APPROVAL_EXPIRED,
    "approvalMissing": APPROVAL_MISSING,
    "historyTitle": HISTORY_TITLE,
    "historyEmpty": HISTORY_EMPTY,
    "historyLoading": HISTORY_LOADING,
    "historyRunSteps": HISTORY_RUN_STEPS,
    "historyRunTokens": HISTORY_RUN_TOKENS,
    "historyStarted": HISTORY_STARTED,
    "historyEnded": HISTORY_ENDED,
    "historyStatusInterrupted": HISTORY_STATUS_INTERRUPTED,
    "historyStatusRunning": HISTORY_STATUS_RUNNING,
    "historyCurrentTag": HISTORY_CURRENT_TAG,
    "wizardTitle": WIZARD_TITLE,
    "wizardIntro": WIZARD_INTRO,
    "wizardSectionIdentity": WIZARD_SECTION_IDENTITY,
    "wizardSectionGoal": WIZARD_SECTION_GOAL,
    "wizardSectionProvider": WIZARD_SECTION_PROVIDER,
    "wizardSectionLimits": WIZARD_SECTION_LIMITS,
    "wizardSectionPolicy": WIZARD_SECTION_POLICY,
    "wizardSectionSave": WIZARD_SECTION_SAVE,
    "wizardFields": {
        "name": WIZARD_FIELD_NAME,
        "role": WIZARD_FIELD_ROLE,
        "home": WIZARD_FIELD_HOME,
        "homeHint": WIZARD_FIELD_HOME_HINT,
        "goal": WIZARD_FIELD_GOAL,
        "providerType": WIZARD_FIELD_PROVIDER_TYPE,
        "scriptPath": WIZARD_FIELD_SCRIPT_PATH,
        "baseUrl": WIZARD_FIELD_BASE_URL,
        "apiKeyEnv": WIZARD_FIELD_API_KEY_ENV,
        "apiKeyEnvHint": WIZARD_FIELD_API_KEY_ENV_HINT,
        "model": WIZARD_FIELD_MODEL,
        "priceIn": WIZARD_FIELD_PRICE_IN,
        "priceOut": WIZARD_FIELD_PRICE_OUT,
        "maxSteps": WIZARD_FIELD_MAX_STEPS,
        "maxCost": WIZARD_FIELD_MAX_COST,
        "maxCalls": WIZARD_FIELD_MAX_CALLS,
        "maxContext": WIZARD_FIELD_MAX_CONTEXT,
        "logTruncate": WIZARD_FIELD_LOG_TRUNCATE,
        "credentials": WIZARD_FIELD_CREDENTIALS,
        "credentialsHint": WIZARD_FIELD_CREDENTIALS_HINT,
        "protected": WIZARD_FIELD_PROTECTED,
        "preapproved": WIZARD_FIELD_PREAPPROVED,
        "shellAllow": WIZARD_FIELD_SHELL_ALLOW,
        "shellAllowHint": WIZARD_FIELD_SHELL_ALLOW_HINT,
        "shellDeny": WIZARD_FIELD_SHELL_DENY,
        "webAllow": WIZARD_FIELD_WEB_ALLOW,
        "webDeny": WIZARD_FIELD_WEB_DENY,
        "savePath": WIZARD_FIELD_SAVE_PATH,
        "savePathHint": WIZARD_FIELD_SAVE_PATH_HINT,
    },
    "wizardValidateButton": WIZARD_VALIDATE_BUTTON,
    "wizardSaveButton": WIZARD_SAVE_BUTTON,
    "wizardValidating": WIZARD_VALIDATING,
    "wizardValidOk": WIZARD_VALID_OK,
    "wizardValidBad": WIZARD_VALID_BAD,
    "wizardSaved": WIZARD_SAVED,
    "wizardGlobalErrors": WIZARD_GLOBAL_ERRORS,
    "wizardFieldErrorsHeading": WIZARD_FIELD_ERRORS_HEADING,
    "errorGeneric": ERROR_GENERIC,
}
