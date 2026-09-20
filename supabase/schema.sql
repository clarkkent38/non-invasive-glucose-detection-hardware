-- =============================================================================
-- Non-Invasive Glucose Prediction System — Supabase Schema
-- Run this entire script once in the Supabase SQL Editor
-- (Dashboard → SQL Editor → New query → paste → Run)
--
-- Step-by-step flow:
--   1. ESP32 button pressed → sensors collected → INSERT row, status='pending'
--   2. ESP32 display shows "Waiting for user details on dashboard"
--   3. Dashboard detects status='pending' row → shows user details form
--   4. User fills form → clicks Submit → dashboard runs prediction
--   5. Dashboard PATCHes row: status='complete', predicted_bgl_mg_dl, etc.
--   6. ESP32 polls for status='complete' → shows results on TFT display
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.readings (

    -- ── Identity ────────────────────────────────────────────────────────────
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       TEXT        NOT NULL DEFAULT 'esp32_default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Workflow status ──────────────────────────────────────────────────────
    -- 'pending'  : ESP32 inserted sensors; waiting for user details from dashboard
    -- 'complete' : Dashboard submitted user details + ran prediction
    -- 'error'    : Something went wrong (set by dashboard on predict() failure)
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'complete', 'error')),

    -- ── Patient context (set by dashboard form after ESP32 inserts) ──────────
    patient_name        TEXT        NULL,
    age                 FLOAT8      NULL,
    bmi                 FLOAT8      NULL,
    gender              TEXT        NULL
                                    CHECK (gender IN ('male','female') OR gender IS NULL),
    diabetes_diagnosis  TEXT        NULL
                                    CHECK (diabetes_diagnosis IN
                                        ('None','Prediabetes','Type 1','Type 2')
                                        OR diabetes_diagnosis IS NULL),
    fasting             SMALLINT    NULL CHECK (fasting IN (0,1) OR fasting IS NULL),
    med_taking_insulin  SMALLINT    NULL CHECK (med_taking_insulin IN (0,1) OR med_taking_insulin IS NULL),
    med_taking_oral     SMALLINT    NULL CHECK (med_taking_oral IN (0,1) OR med_taking_oral IS NULL),
    family_history      SMALLINT    NULL CHECK (family_history IN (0,1) OR family_history IS NULL),
    smoking             SMALLINT    NULL CHECK (smoking IN (0,1) OR smoking IS NULL),

    -- ── ESP32-Measured Sensor Columns ────────────────────────────────────────
    -- Names match predict.py _prepare_full_sensor_features() EXACTLY.
    saliva_ph               FLOAT8  NULL,
    hr_bpm                  FLOAT8  NULL,
    ppg_raw_dc_baseline     FLOAT8  NULL,
    ppg_raw_ac_p2p          FLOAT8  NULL,
    perfusion_index         FLOAT8  NULL,
    pulse_width_ms          FLOAT8  NULL,
    temperature_c           FLOAT8  NULL,

    -- ── HRV (nullable — future firmware; predict.py uses training-mean fallbacks)
    hrv_sdnn                FLOAT8  NULL,
    hrv_rmssd               FLOAT8  NULL,
    hrv_pnn50               FLOAT8  NULL,
    hrv_lf                  FLOAT8  NULL,
    hrv_hf                  FLOAT8  NULL,
    hrv_lf_hf_ratio         FLOAT8  NULL,

    -- ── Prediction results (written by dashboard after predict()) ───────────
    predicted_bgl_mg_dl     FLOAT8  NULL,
    ci_low_mg_dl            FLOAT8  NULL,   -- 5th-percentile confidence bound
    ci_high_mg_dl           FLOAT8  NULL,   -- 95th-percentile confidence bound
    clarke_zone             TEXT    NULL,
    glucose_category        TEXT    NULL,   -- 'Normal','Prediabetes','Elevated','Hyperglycemia','Hypoglycemia'
    is_ood                  BOOLEAN NULL,
    ood_warning             TEXT    NULL

);

-- ---------------------------------------------------------------------------
-- INDEXES
-- ---------------------------------------------------------------------------
-- Dashboard polls for latest pending row frequently
CREATE INDEX IF NOT EXISTS readings_status_created_idx
    ON public.readings (status, created_at DESC);

-- Per-patient history charts
CREATE INDEX IF NOT EXISTS readings_patient_created_idx
    ON public.readings (patient_name, created_at DESC)
    WHERE patient_name IS NOT NULL;

-- ESP32 polls by device_id + status to find its own completed row
CREATE INDEX IF NOT EXISTS readings_device_status_idx
    ON public.readings (device_id, status, created_at DESC);

-- ---------------------------------------------------------------------------
-- ROW LEVEL SECURITY
-- ---------------------------------------------------------------------------
ALTER TABLE public.readings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_insert" ON public.readings
    FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_select" ON public.readings
    FOR SELECT TO anon USING (true);

CREATE POLICY "anon_update" ON public.readings
    FOR UPDATE TO anon USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- GRANTS
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON public.readings TO anon;
GRANT USAGE ON SEQUENCE public.readings_id_seq TO anon;
