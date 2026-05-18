"""Initial schema — alle 12 Tabellen.

Revision ID: 0001
Revises: —
Create Date: 2026-05-17

Tabellen-Erstellungsreihenfolge:
  1. slack_messages       — keine FK-Abhängigkeiten
  2. ideas                — FK → slack_messages
  3. content_jobs         — FK → ideas   (deferred FK → assets)
  4. prompts              — FK → content_jobs   (deferred FK → assets)
  5. assets               — FK → content_jobs, prompts   (deferred FK → drive_files)
  6. drive_files          — FK → content_jobs   (deferred FK → assets)
  7. approvals            — FK → slack_messages
  8. api_calls            — FK → content_jobs, prompts
  9. costs                — FK → api_calls, content_jobs
 10. errors               — FK → api_calls, slack_messages
 11. sheet_logs           — keine FK (polymorphisch)
 12. performance_later    — FK → content_jobs

Deferred FKs (use_alter=True) werden am Ende via ALTER TABLE hinzugefügt:
  - content_jobs.primary_asset_id → assets.asset_id
  - assets.drive_file_id          → drive_files.file_id
  - drive_files.asset_id          → assets.asset_id
  - prompts.asset_id              → assets.asset_id
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── 1. slack_messages ──────────────────────────────────────────────────────
    op.create_table(
        "slack_messages",
        sa.Column("message_id", sa.String(20), primary_key=True),
        sa.Column("slack_internal_id", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("message_type", sa.String(50), nullable=False),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_id", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("message_body", sa.Text, nullable=True),
        sa.Column("block_payload", postgresql.JSONB, nullable=True),
        sa.Column("sent_by", sa.String(100), nullable=True),
        sa.Column("response_received", sa.Boolean, default=False, nullable=True),
        sa.Column("response_type", sa.String(50), nullable=True),
        sa.Column("response_by", sa.String(100), nullable=True),
        sa.Column("response_text", sa.Text, nullable=True),
        sa.Column("response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_message_id", sa.String(50), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_timed_out", sa.Boolean, default=False, nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_attempt", sa.Integer, default=0, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )

    # ── 2. ideas ───────────────────────────────────────────────────────────────
    op.create_table(
        "ideas",
        sa.Column("idea_id", sa.String(20), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("hook", sa.Text, nullable=False),
        sa.Column("core_emotion", sa.String(100), nullable=False),
        sa.Column("target_group", sa.String(255), nullable=False),
        sa.Column("best_platforms", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(20), nullable=False),
        sa.Column("click_potential", sa.Numeric(3, 1), nullable=False),
        sa.Column("watchtime_potential", sa.Numeric(3, 1), nullable=False),
        sa.Column("share_potential", sa.Numeric(3, 1), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("main_scene", sa.Text, nullable=True),
        sa.Column("transformation", sa.Text, nullable=True),
        sa.Column("reveal", sa.Text, nullable=True),
        sa.Column("cta_style", sa.String(255), nullable=True),
        sa.Column("production_complexity", sa.String(20), nullable=True),
        sa.Column("risk_level", sa.String(20), nullable=True),
        sa.Column("short_description", sa.Text, nullable=True),
        sa.Column("trend_reference", sa.String(20), nullable=True),
        sa.Column("hook_id", sa.String(20), nullable=True),
        sa.Column("slack_message_id", sa.String(50), sa.ForeignKey("slack_messages.message_id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("archived_reason", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_deleted", sa.Boolean, default=False, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_to_slack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ideas_status", "ideas", ["status"])
    op.create_index("ix_ideas_is_deleted", "ideas", ["is_deleted"])
    op.create_index("ix_ideas_created_at", "ideas", ["created_at"])

    # ── 3. content_jobs (ohne deferred FK primary_asset_id) ───────────────────
    op.create_table(
        "content_jobs",
        sa.Column("content_id", sa.String(30), primary_key=True),
        sa.Column("idea_id", sa.String(20), sa.ForeignKey("ideas.idea_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("content_type", sa.String(20), nullable=False),
        sa.Column("hook", sa.Text, nullable=False),
        sa.Column("target_audience", sa.String(255), nullable=False),
        sa.Column("primary_emotion", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("retry_counter", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("brief_content", postgresql.JSONB, nullable=True),
        sa.Column("platform_agent", sa.String(50), nullable=True),
        sa.Column("has_image", sa.Boolean, default=True, nullable=True),
        sa.Column("has_video", sa.Boolean, default=False, nullable=True),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("hashtags", sa.Text, nullable=True),
        sa.Column("cta", sa.String(255), nullable=True),
        sa.Column("qc_score", sa.Numeric(3, 1), nullable=True),
        sa.Column("qc_breakdown", postgresql.JSONB, nullable=True),
        sa.Column("qc_notes", sa.Text, nullable=True),
        sa.Column("no_go_triggered", sa.Boolean, default=False, nullable=True),
        sa.Column("no_go_detail", sa.Text, nullable=True),
        # primary_asset_id wird via ALTER TABLE nach assets-Erstellung hinzugefügt
        sa.Column("drive_folder_id", sa.String(255), nullable=True),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("revision_instructions", sa.Text, nullable=True),
        sa.Column("publishing_notes", sa.Text, nullable=True),
        sa.Column("archived_reason", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_deleted", sa.Boolean, default=False, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("brief_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qc_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qc_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_to_drive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("logged_to_sheets_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_to_slack_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_content_jobs_status", "content_jobs", ["status"])
    op.create_index("ix_content_jobs_idea_id", "content_jobs", ["idea_id"])
    op.create_index("ix_content_jobs_platform", "content_jobs", ["platform"])
    op.create_index("ix_content_jobs_is_deleted", "content_jobs", ["is_deleted"])
    op.create_index("ix_content_jobs_created_at", "content_jobs", ["created_at"])

    # ── 4. prompts (ohne deferred FK asset_id) ────────────────────────────────
    op.create_table(
        "prompts",
        sa.Column("prompt_id", sa.String(20), primary_key=True),
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("prompt_type", sa.String(50), nullable=False),
        sa.Column("prompt_text", sa.Text, nullable=False),
        sa.Column("target_service", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("negative_prompt", sa.Text, nullable=True),
        sa.Column("parameters", postgresql.JSONB, nullable=True),
        # asset_id deferred — wird nach assets-Erstellung hinzugefügt
        sa.Column("result_quality", sa.Numeric(3, 1), nullable=True),
        sa.Column("version", sa.Integer, default=1, nullable=True),
        sa.Column("parent_prompt_id", sa.String(20), sa.ForeignKey("prompts.prompt_id", ondelete="SET NULL"), nullable=True),
        sa.Column("revision_reason", sa.Text, nullable=True),
        sa.Column("is_successful", sa.Boolean, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prompts_content_id", "prompts", ["content_id"])

    # ── 5. assets (ohne deferred FK drive_file_id) ────────────────────────────
    op.create_table(
        "assets",
        sa.Column("asset_id", sa.String(20), primary_key=True),
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_format", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("width_px", sa.Integer, nullable=True),
        sa.Column("height_px", sa.Integer, nullable=True),
        sa.Column("duration_seconds", sa.Numeric(5, 2), nullable=True),
        sa.Column("aspect_ratio", sa.String(10), nullable=True),
        sa.Column("resolution_label", sa.String(20), nullable=True),
        sa.Column("temp_url", sa.Text, nullable=True),
        # drive_file_id deferred — wird nach drive_files-Erstellung hinzugefügt
        sa.Column("drive_url", sa.Text, nullable=True),
        sa.Column("prompt_id", sa.String(20), sa.ForeignKey("prompts.prompt_id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer, default=1, nullable=True),
        sa.Column("is_primary", sa.Boolean, default=False, nullable=True),
        sa.Column("qc_score", sa.Numeric(3, 1), nullable=True),
        sa.Column("qc_notes", sa.Text, nullable=True),
        sa.Column("is_deleted", sa.Boolean, default=False, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qc_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_assets_content_id", "assets", ["content_id"])
    op.create_index("ix_assets_asset_type", "assets", ["asset_type"])
    op.create_index("ix_assets_status", "assets", ["status"])

    # ── 6. drive_files (ohne deferred FK asset_id) ────────────────────────────
    op.create_table(
        "drive_files",
        sa.Column("file_id", sa.String(20), primary_key=True),
        sa.Column("drive_file_id", sa.String(100), nullable=False, unique=True),
        sa.Column("drive_url", sa.Text, nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("folder_path", sa.String(500), nullable=False),
        sa.Column("folder_id", sa.String(100), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("uploaded_by", sa.String(50), nullable=False),
        # asset_id deferred — wird nach assets-Erstellung hinzugefügt (assets ist schon da!)
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="SET NULL"), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("is_public", sa.Boolean, default=False, nullable=True),
        sa.Column("current_folder", sa.String(100), nullable=True),
        sa.Column("moved_to_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_to_rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_to_final_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_in_drive", sa.Boolean, default=False, nullable=True),
        sa.Column("deleted_in_drive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_drive_files_content_id", "drive_files", ["content_id"])

    # ── 7. approvals ──────────────────────────────────────────────────────────
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(20), primary_key=True),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_id", sa.String(30), nullable=False),
        sa.Column("approval_type", sa.String(30), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("slack_message_id", sa.String(50), sa.ForeignKey("slack_messages.message_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("revision_instructions", sa.Text, nullable=True),
        sa.Column("slack_response_id", sa.String(50), nullable=True),
        sa.Column("response_time_minutes", sa.Integer, nullable=True),
        sa.Column("is_timeout", sa.Boolean, default=False, nullable=True),
        sa.Column("slack_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_approvals_object_id", "approvals", ["object_id"])
    op.create_index("ix_approvals_object_type", "approvals", ["object_type"])
    op.create_index("ix_approvals_result", "approvals", ["result"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])

    # ── 8. api_calls ──────────────────────────────────────────────────────────
    op.create_table(
        "api_calls",
        sa.Column("call_id", sa.String(20), primary_key=True),
        sa.Column("service", sa.String(30), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("called_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt_id", sa.String(20), sa.ForeignKey("prompts.prompt_id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_body_hash", sa.String(64), nullable=True),
        sa.Column("response_summary", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("tokens_input", sa.Integer, nullable=True),
        sa.Column("tokens_output", sa.Integer, nullable=True),
        sa.Column("images_generated", sa.Integer, nullable=True),
        sa.Column("credits_used", sa.Numeric(10, 4), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_of", sa.String(20), sa.ForeignKey("api_calls.call_id", ondelete="SET NULL"), nullable=True),
        sa.Column("rate_limited", sa.Boolean, default=False, nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_api_calls_service", "api_calls", ["service"])
    op.create_index("ix_api_calls_content_id", "api_calls", ["content_id"])
    op.create_index("ix_api_calls_success", "api_calls", ["success"])
    op.create_index("ix_api_calls_created_at", "api_calls", ["created_at"])

    # ── 9. costs ──────────────────────────────────────────────────────────────
    op.create_table(
        "costs",
        sa.Column("cost_id", sa.String(20), primary_key=True),
        sa.Column("call_id", sa.String(20), sa.ForeignKey("api_calls.call_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("service", sa.String(30), nullable=False),
        sa.Column("cost_unit", sa.String(50), nullable=False),
        sa.Column("cost_amount", sa.Numeric(12, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="SET NULL"), nullable=True),
        sa.Column("cost_eur", sa.Numeric(10, 4), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("billing_period", sa.Date, nullable=True),
        sa.Column("budget_category", sa.String(50), nullable=True),
        sa.Column("is_estimated", sa.Boolean, default=False, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_costs_service", "costs", ["service"])
    op.create_index("ix_costs_content_id", "costs", ["content_id"])
    op.create_index("ix_costs_billing_period", "costs", ["billing_period"])
    op.create_index("ix_costs_created_at", "costs", ["created_at"])

    # ── 10. errors ────────────────────────────────────────────────────────────
    op.create_table(
        "errors",
        sa.Column("error_id", sa.String(20), primary_key=True),
        sa.Column("error_type", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_id", sa.String(30), nullable=True),
        sa.Column("previous_status", sa.String(50), nullable=False),
        sa.Column("reporting_agent", sa.String(50), nullable=False),
        sa.Column("retry_counter", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("call_id", sa.String(20), sa.ForeignKey("api_calls.call_id", ondelete="SET NULL"), nullable=True),
        sa.Column("stack_trace", sa.Text, nullable=True),
        sa.Column("api_status_code", sa.Integer, nullable=True),
        sa.Column("api_error_body", sa.Text, nullable=True),
        sa.Column("resolution", sa.String(50), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("slack_alert_sent", sa.Boolean, default=False, nullable=True),
        sa.Column("slack_alert_id", sa.String(50), sa.ForeignKey("slack_messages.message_id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_critical", sa.Boolean, default=False, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_errors_error_type", "errors", ["error_type"])
    op.create_index("ix_errors_object_id", "errors", ["object_id"])
    op.create_index("ix_errors_object_type", "errors", ["object_type"])
    op.create_index("ix_errors_is_critical", "errors", ["is_critical"])
    op.create_index("ix_errors_created_at", "errors", ["created_at"])

    # ── 11. sheet_logs ────────────────────────────────────────────────────────
    op.create_table(
        "sheet_logs",
        sa.Column("log_id", sa.String(20), primary_key=True),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_id", sa.String(30), nullable=False),
        sa.Column("sheet_name", sa.String(100), nullable=False),
        sa.Column("sheet_id", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("row_id", sa.Integer, nullable=True),
        sa.Column("columns_updated", sa.Text, nullable=True),
        sa.Column("previous_values", postgresql.JSONB, nullable=True),
        sa.Column("new_values", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, default=0, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_pending_sync", sa.Boolean, default=False, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_sheet_logs_object_id", "sheet_logs", ["object_id"])
    op.create_index("ix_sheet_logs_is_pending_sync", "sheet_logs", ["is_pending_sync"])
    op.create_index("ix_sheet_logs_success", "sheet_logs", ["success"])
    op.create_index("ix_sheet_logs_created_at", "sheet_logs", ["created_at"])

    # ── 12. performance_later ─────────────────────────────────────────────────
    op.create_table(
        "performance_later",
        sa.Column("performance_id", sa.String(20), primary_key=True),
        sa.Column("content_id", sa.String(30), sa.ForeignKey("content_jobs.content_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("views", sa.BigInteger, nullable=True),
        sa.Column("plays", sa.BigInteger, nullable=True),
        sa.Column("reach", sa.BigInteger, nullable=True),
        sa.Column("impressions", sa.BigInteger, nullable=True),
        sa.Column("likes", sa.Integer, nullable=True),
        sa.Column("comments", sa.Integer, nullable=True),
        sa.Column("shares", sa.Integer, nullable=True),
        sa.Column("saves", sa.Integer, nullable=True),
        sa.Column("follows_gained", sa.Integer, nullable=True),
        sa.Column("profile_visits", sa.Integer, nullable=True),
        sa.Column("link_clicks", sa.Integer, nullable=True),
        sa.Column("watchtime_seconds", sa.BigInteger, nullable=True),
        sa.Column("avg_watchtime_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("completion_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("engagement_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("ctr", sa.Numeric(5, 4), nullable=True),
        sa.Column("hook_retention_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("later_post_id", sa.String(100), nullable=True),
        sa.Column("platform_post_id", sa.String(255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_source", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_performance_later_platform", "performance_later", ["platform"])
    op.create_index("ix_performance_later_measured_at", "performance_later", ["measured_at"])

    # ── Deferred FKs (use_alter) — nach allen Tabellen hinzufügen ─────────────

    # content_jobs.primary_asset_id → assets.asset_id
    op.add_column("content_jobs", sa.Column("primary_asset_id", sa.String(20), nullable=True))
    op.create_foreign_key(
        "fk_content_jobs_primary_asset_id",
        "content_jobs", "assets",
        ["primary_asset_id"], ["asset_id"],
        ondelete="SET NULL",
    )

    # assets.drive_file_id → drive_files.file_id
    op.add_column("assets", sa.Column("drive_file_id", sa.String(20), nullable=True))
    op.create_foreign_key(
        "fk_assets_drive_file_id",
        "assets", "drive_files",
        ["drive_file_id"], ["file_id"],
        ondelete="SET NULL",
    )

    # drive_files.asset_id → assets.asset_id
    op.add_column("drive_files", sa.Column("asset_id", sa.String(20), nullable=True))
    op.create_foreign_key(
        "fk_drive_files_asset_id",
        "drive_files", "assets",
        ["asset_id"], ["asset_id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_drive_files_asset_id", "drive_files", ["asset_id"])

    # prompts.asset_id → assets.asset_id
    op.add_column("prompts", sa.Column("asset_id", sa.String(20), nullable=True))
    op.create_foreign_key(
        "fk_prompts_asset_id",
        "prompts", "assets",
        ["asset_id"], ["asset_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Deferred FKs zuerst entfernen
    op.drop_constraint("fk_prompts_asset_id", "prompts", type_="foreignkey")
    op.drop_column("prompts", "asset_id")

    op.drop_constraint("fk_drive_files_asset_id", "drive_files", type_="foreignkey")
    op.drop_index("ix_drive_files_asset_id", table_name="drive_files")
    op.drop_column("drive_files", "asset_id")

    op.drop_constraint("fk_assets_drive_file_id", "assets", type_="foreignkey")
    op.drop_column("assets", "drive_file_id")

    op.drop_constraint("fk_content_jobs_primary_asset_id", "content_jobs", type_="foreignkey")
    op.drop_column("content_jobs", "primary_asset_id")

    # Tabellen in umgekehrter Reihenfolge
    op.drop_table("performance_later")
    op.drop_table("sheet_logs")
    op.drop_table("errors")
    op.drop_table("costs")
    op.drop_table("api_calls")
    op.drop_table("approvals")
    op.drop_table("drive_files")
    op.drop_table("assets")
    op.drop_table("prompts")
    op.drop_table("content_jobs")
    op.drop_table("ideas")
    op.drop_table("slack_messages")
