"""
Supabase client helper for the live sensor dashboard.

Provides two functions consumed by app/live_dashboard.py:
  get_latest_reading(client)             -> dict | None
  get_reading_history(client, patient_name, limit) -> pd.DataFrame

Credentials are read from st.secrets at call time, so this module can be
imported without Streamlit being active (e.g. in tests) — the `client`
object is created by the caller and passed in.

Supabase-py docs: https://supabase.com/docs/reference/python/introduction
"""

from __future__ import annotations

from typing import Optional
import pandas as pd


# ---------------------------------------------------------------------------
# Client factory — call once at app startup
# ---------------------------------------------------------------------------

def create_client(url: str, key: str):
    """
    Returns a supabase.Client.  Import is deferred so the module loads even
    when supabase-py is not installed (e.g. CI environments running only the
    original predict.py tests).
    """
    try:
        from supabase import create_client as _create
        return _create(url, key)
    except ImportError as exc:
        raise ImportError(
            "supabase-py not installed.  Run: pip install supabase"
        ) from exc


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_latest_reading(client) -> Optional[dict]:
    """
    Returns the most recently inserted row from `readings` as a plain dict,
    or None if the table is empty or the query fails.

    The dict keys match the schema column names exactly, which are also the
    field names that predict.py's _prepare_full_sensor_features() expects.
    Any column that is NULL in Supabase will be absent from the dict (or
    present with None), and predict.py's fallbacks handle that correctly.
    """
    try:
        resp = (
            client.table("readings")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data
        if not rows:
            return None
        return rows[0]
    except Exception as exc:
        # Non-fatal: live dashboard shows a warning instead of crashing
        print(f"[supabase_client] get_latest_reading error: {exc}")
        return None


def get_reading_history(
    client,
    patient_name: Optional[str] = None,
    limit: int = 20,
) -> pd.DataFrame:
    """
    Returns the last `limit` readings (newest-first) as a DataFrame.

    If `patient_name` is provided (non-empty string), filters to rows where
    patient_name matches — used to populate the longitudinal trend chart with
    a specific patient's history rather than all device readings.

    Returns an empty DataFrame (not None) on error, so callers can always do
    .empty / len() checks without try/except.
    """
    try:
        query = (
            client.table("readings")
            .select(
                "id, created_at, device_id, patient_name, "
                "saliva_ph, hr_bpm, ppg_raw_dc_baseline, ppg_raw_ac_p2p, "
                "perfusion_index, pulse_width_ms, temperature_c, "
                "hrv_sdnn, hrv_rmssd, hrv_lf_hf_ratio, "
                "predicted_bgl_mg_dl, clarke_zone, is_ood"
            )
            .order("created_at", desc=True)
            .limit(limit)
        )
        if patient_name and patient_name.strip():
            query = query.eq("patient_name", patient_name.strip())

        resp = query.execute()
        rows = resp.data
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        # Parse timestamps for plotting
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        return df

    except Exception as exc:
        print(f"[supabase_client] get_reading_history error: {exc}")
        return pd.DataFrame()


def patch_reading(client, row_id: int, patch: dict) -> bool:
    """
    PATCH (update) a row by id — used by the dashboard to write back
    predicted_bgl_mg_dl, clarke_zone, is_ood, and patient_name after
    running predict() on the fetched raw sensor values.

    Returns True on success, False on failure.
    """
    try:
        client.table("readings").update(patch).eq("id", row_id).execute()
        return True
    except Exception as exc:
        print(f"[supabase_client] patch_reading error: {exc}")
        return False


def mark_processed(client, row_id: int) -> bool:
    """Flip processed=true on a row after it has been displayed."""
    return patch_reading(client, row_id, {"processed": True})
