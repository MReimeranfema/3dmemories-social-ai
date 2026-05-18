# TODO: SQLAlchemy ORM Models
# Spezifikation: database/data-model.md
#
# ContentJob — Tabelle: content_jobs
#   Felder: id (UUID), slack_user_id, slack_channel_id, command, command_text,
#           status (JobStatus Enum), drive_file_id, drive_file_url, sheets_row,
#           celery_task_id, error_message, retry_count, is_test, is_archived,
#           created_at, updated_at
#
# ApiCall — Tabelle: api_calls
#   Felder: id (UUID), service, endpoint, method, status_code, success,
#           latency_ms, error_message, job_id, created_at
