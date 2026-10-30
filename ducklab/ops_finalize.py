"""Monthly report finalization workflow: finalize/correct dialogs and the
Excel export of the selected live or finalized report.

The OpsState event handlers are thin delegates to the functions here; all
data flows through AppService with the session token revalidated on every
event. Finalization/correction controls are convenience only; AppService
remains the authorization and version-allocation authority.
"""

from __future__ import annotations

import reflex as rx

from ducklab import authz
from ducklab import report_presenter
from ducklab.auth_pages import app_service
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_snapshots import (
    SnapshotNotFoundError, finalization_warnings, snapshot_to_report,
)


def _finalize_error(exc: Exception) -> str:
    # Local import: auth_message lives on the ops_state module that
    # delegates here (importing it at module level would be circular).
    from ducklab.ops_state import auth_message
    if isinstance(exc, (AuthenticationError, AuthorizationError)):
        return auth_message(exc)
    return str(exc) or "The report could not be finalized."


def open_finalize(state, action: str) -> None:
    state.finalize_dialog_open = True
    state.finalize_action = action
    state.finalize_error = ""
    state.finalize_warnings = []
    state.finalize_next_version = 1
    try:
        user = state._actor()
        store_id = state._selected_store_id()
        month = state._selected_month()
        if store_id is None or month is None:
            raise SnapshotNotFoundError("Select a store and month first.")
        report = app_service.preview_month(user, store_id, month)
        rows = app_service.list_report_versions(user, store_id, month)
        state.finalize_month_label = report_presenter.month_label(month)
        state.finalize_next_version = max(
            (int(row["version"]) for row in rows), default=0) + 1
        state.finalize_warnings = finalization_warnings(report)
    except Exception as exc:
        state.finalize_error = _finalize_error(exc)
    finally:
        state.finalize_saving = False
    yield


def set_finalize_dialog_open(state, open: bool) -> None:
    state.finalize_dialog_open = bool(open)
    if not open:
        state.finalize_error = ""


def save_finalization(state) -> None:
    if state.finalize_saving:
        return
    state.finalize_saving = True
    state.finalize_error = ""
    yield
    try:
        user = state._actor()
        store_id = state._selected_store_id()
        month = state._selected_month()
        if store_id is None or month is None:
            raise SnapshotNotFoundError("Select a store and month first.")
        save = (app_service.correct_month if state.finalize_action == "correct"
                else app_service.finalize_month)
        result = save(user, store_id, month)
        # The service's allocated number is authoritative, even if another
        # operator finalized while this dialog was open.
        rows = app_service.list_report_versions(user, store_id, month)
        row = next(row for row in rows
                   if int(row["version"]) == int(result["version"]))
        state.finalize_dialog_open = False
        state._clear_report()
        state.report_mode = "finalized"
        state.selected_version_label = report_presenter._version_label(row)
        version_rows = app_service.list_report_versions(user, store_id, month)
        state.versions = [report_presenter._version_label(v)
                          for v in version_rows]
        state.report_status = "loading"
        state._load_report(user, store_id, month, version_rows)
    except Exception as exc:
        # Keep both this dialog and the previously selected report intact.
        state.finalize_error = _finalize_error(exc)
    finally:
        state.finalize_saving = False


def export_report(state) -> rx.event.EventSpec | None:
    state.error_message = ""
    store_id = state._selected_store_id()
    month = state._selected_month()
    if store_id is None or month is None:
        state.error_message = "Select a store and month before exporting."
        return None
    try:
        from ducklab import report_export
        user = state._actor()
        snapshot = None
        if state.report_mode == "finalized":
            wanted = None
            if state.selected_version_label:
                rows = app_service.list_report_versions(user, store_id, month)
                wanted = report_presenter._version_for_label(
                    state.selected_version_label, rows)
            snapshot = app_service.get_finalized_report(
                user, store_id, month, version=wanted)
            report = snapshot_to_report(snapshot)
        else:
            report = app_service.preview_month(user, store_id, month)
        data = report_export.export_bytes(
            report, app_service.read_repo,
            include_payroll=authz.can_manage_config(user), snapshot=snapshot)
        return rx.download(
            data=data,
            filename=report_export.export_filename(report, snapshot))
    except AuthenticationError:
        return rx.redirect("/login")
    except (AuthorizationError, ConfigNotFoundError, StoreNotFoundError,
            InvalidMonthError, SnapshotNotFoundError, RepositoryError) as exc:
        state.error_message = str(exc)
        return None
    except Exception as exc:
        state.error_message = str(exc) or "The report could not be exported."
        return None
