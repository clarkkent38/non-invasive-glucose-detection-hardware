"""
Supabase client helpers for the live sensor dashboard.
All functions take a supabase.Client as first argument — created once at
startup and passed in, so this module stays importable without Streamlit.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd


def create_client(url: str, key: str):
    try:
        from supabase import create_client as _c
        return _c(url, key)
    except ImportError as e:
        raise ImportError("Run: pip install supabase") from e


def get_pending_reading(client) -> Optional[dict]:
    """Return the most recent row with status='pending', or None."""
    try:
        resp = (
            client.table("readings")
            .select("*")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data
        return rows[0] if rows else None
    except Exception as exc:
        print(f"[supabase] get_pending_reading: {exc}")
        return None


def get_latest_reading(client) -> Optional[dict]:
    """Return the most recent row regardless of status."""
    try:
        resp = (
            client.table("readings")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data
        return rows[0] if rows else None
    except Exception as exc:
        print(f"[supabase] get_latest_reading: {exc}")
        return None


def get_reading_history(client, patient_name: Optional[str] = None,
                        limit: int = 30) -> pd.DataFrame:
    """Return last `limit` complete readings as a DataFrame."""
    try:
        q = (
            client.table("readings")
            .select("id,created_at,device_id,patient_name,"
                    "saliva_ph,hr_bpm,ppg_raw_dc_baseline,ppg_raw_ac_p2p,"
                    "perfusion_index,pulse_width_ms,temperature_c,"
                    "predicted_bgl_mg_dl,ci_low_mg_dl,ci_high_mg_dl,"
                    "clarke_zone,glucose_category,is_ood,status")
            .eq("status", "complete")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if patient_name and patient_name.strip():
            q = q.eq("patient_name", patient_name.strip())
        rows = q.execute().data
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        return df
    except Exception as exc:
        print(f"[supabase] get_reading_history: {exc}")
        return pd.DataFrame()


def patch_reading(client, row_id: int, patch: dict) -> bool:
    """PATCH a row by id. Returns True on success."""
    try:
        client.table("readings").update(patch).eq("id", row_id).execute()
        return True
    except Exception as exc:
        print(f"[supabase] patch_reading: {exc}")
        return False
