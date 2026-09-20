-- =============================================================================
-- Non-Invasive Glucose Prediction System — Supabase Schema
-- Run this entire script once in the Supabase SQL Editor
-- (Dashboard → SQL Editor → New query → paste → Run)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- TABLE: readings
--
-- Column design rationale:
--   • ESP32-measured columns use EXACT field names that predict.py's
--     _prepare_full_sensor_features() expects as dict keys.  Any column the
--     device omits will be filled by predict.py's documented fallbacks —
--     those fallbacks are deliberately NOT stored here to avoid confusion.
--   • APG (apg_a/b/c/d/e) and VPG (vpg_max/vpg_min) are SERVER-SIDE
--     DERIVED by predict.py from raw_ac and hr_bpm.  They are not columns
--     here because the ESP32 does not measure them directly.
--   • HRV columns (hrv_sdnn, hrv_rmssd, hrv_pnn50, hrv_lf, hrv_hf,
--     hrv_lf_hf_ratio) are nullable.  A future firmware iteration can
--     accumulate IBI buffers and send real HRV; predict.py already falls
--     back to training-mean values when they are NULL / absent.
--   • Dashboard-side context columns (age, bmi, diabetes_diagnosis, etc.)
--     are nullable so the device row is valid even without patient context.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.readings (

    -- ── Identity ────────────────────────────────────────────────────────────
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       TEXT        NOT NULL DEFAULT 'esp32_default',
                                -- Identifies which physical ESP32 sent the row.
                                -- Set #define DEVICE_ID in firmware.
    patient_name    TEXT        NULL,
                                -- Optionally written by the Streamlit dashboard
                                -- AFTER a reading arrives (PATCH by id).
                                -- The ESP32 never writes this field.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed       BOOLEAN     NOT NULL DEFAULT FALSE,
                                -- Dashboard can flip this TRUE after displaying.
                                -- Useful for "consume once" polling patterns.

    -- ── ESP32-Measured Sensor Columns ────────────────────────────────────────
    -- Field names match predict.py _prepare_full_sensor_features() EXACTLY.

    -- Saliva pH probe (analog ADC, linear-calibrated on device)
    saliva_ph               FLOAT8  NULL,  -- range 5.5 – 8.5 (train: 6.2–7.6)

    -- MAX30102 Optical PPG (IR channel)
    hr_bpm                  FLOAT8  NULL,  -- beats per minute (train: 52–130)
    ppg_raw_dc_baseline     FLOAT8  NULL,  -- IR DC moving average, ADC counts (~155k–195k)
    ppg_raw_ac_p2p          FLOAT8  NULL,  -- IR peak-to-peak amplitude, ADC counts (~1100–4850)
    perfusion_index         FLOAT8  NULL,  -- (AC/DC)*100, percent (train: 0.5–5.0)
    pulse_width_ms          FLOAT8  NULL,  -- systolic peak duration, milliseconds (train: 145–350)

    -- TMP117 I2C temperature sensor
    temperature_c           FLOAT8  NULL,  -- skin surface temp °C (train: 36.2–37.35)

    -- ── HRV Columns (nullable — requires IBI buffer, future firmware) ────────
    -- predict.py fallbacks: sdnn=42, rmssd=34, pnn50=12, lf=520, hf=380, lf_hf=1.37
    hrv_sdnn                FLOAT8  NULL,
    hrv_rmssd               FLOAT8  NULL,
    hrv_pnn50               FLOAT8  NULL,
    hrv_lf                  FLOAT8  NULL,
    hrv_hf                  FLOAT8  NULL,
    hrv_lf_hf_ratio         FLOAT8  NULL,

    -- ── Dashboard-Side Patient Context (nullable, set via UI not device) ─────
    -- predict.py fallbacks: age=45, bmi=26.5, diagnosis="None", gender="male",
    --                        fasting=1, med_taking_insulin=0
    age                     FLOAT8  NULL,
    bmi                     FLOAT8  NULL,
    diabetes_diagnosis      TEXT    NULL
                                CHECK (diabetes_diagnosis IN
                                    ('None','Prediabetes','Type 1','Type 2')
                                    OR diabetes_diagnosis IS NULL),
    gender                  TEXT    NULL
                                CHECK (gender IN ('male','female','M','F')
                                    OR gender IS NULL),
    fasting                 SMALLINT NULL
                                CHECK (fasting IN (0, 1) OR fasting IS NULL),
    med_taking_insulin      SMALLINT NULL
                                CHECK (med_taking_insulin IN (0, 1)
                                    OR med_taking_insulin IS NULL),
    med_taking_oral         SMALLINT NULL
                                CHECK (med_taking_oral IN (0, 1)
                                    OR med_taking_oral IS NULL),
    family_history          SMALLINT NULL
                                CHECK (family_history IN (0, 1)
                                    OR family_history IS NULL),
    smoking                 SMALLINT NULL
                                CHECK (smoking IN (0, 1) OR smoking IS NULL),

    -- ── Optional: store the raw prediction result for audit trail ───────────
    predicted_bgl_mg_dl     FLOAT8  NULL,  -- filled in by dashboard after predict()
    clarke_zone             TEXT    NULL,  -- e.g. "Zone A (Clinically Accurate)"
    is_ood                  BOOLEAN NULL   -- out-of-distribution flag from predict()

);

-- ---------------------------------------------------------------------------
-- INDEX: fast latest-reading queries
-- The dashboard queries ORDER BY created_at DESC LIMIT 1 constantly.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS readings_created_at_desc_idx
    ON public.readings (created_at DESC);

-- Optional: index for per-patient history queries
CREATE INDEX IF NOT EXISTS readings_patient_name_idx
    ON public.readings (patient_name, created_at DESC)
    WHERE patient_name IS NOT NULL;

-- ---------------------------------------------------------------------------
-- ROW LEVEL SECURITY
--
-- SECURITY NOTE (read before deploying to production):
--   The policies below allow the anonymous (public) Supabase key to INSERT
--   and SELECT freely.  This is acceptable for a research prototype where:
--     (a) data is not sensitive PII in the clinical sense,
--     (b) the device is on a private WiFi network, and
--     (c) the Supabase project URL is not publicly advertised.
--
--   For a production or clinical deployment you MUST:
--     1. Create a dedicated Supabase service-role key for the ESP32 with
--        INSERT-only access (no SELECT), stored in firmware flash/NVS.
--     2. Create a separate read-only key for Streamlit.
--     3. Add a per-device RLS predicate: USING (device_id = current_setting(
--        'request.jwt.claims')::json->>'device_id') so each device can only
--        read its own rows.
--     4. Never commit real Supabase keys to version control.
-- ---------------------------------------------------------------------------

ALTER TABLE public.readings ENABLE ROW LEVEL SECURITY;

-- Allow anonymous INSERT (ESP32 writes readings using the public anon key)
CREATE POLICY "anon_insert" ON public.readings
    FOR INSERT
    TO anon
    WITH CHECK (true);

-- Allow anonymous SELECT (Streamlit reads readings using the public anon key)
CREATE POLICY "anon_select" ON public.readings
    FOR SELECT
    TO anon
    USING (true);

-- Allow anonymous UPDATE (dashboard patches patient_name / predicted_bgl etc.)
CREATE POLICY "anon_update" ON public.readings
    FOR UPDATE
    TO anon
    USING (true)
    WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- GRANT: anon role needs explicit table-level grants in addition to RLS
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON public.readings TO anon;
GRANT USAGE ON SEQUENCE public.readings_id_seq TO anon;  -- needed for IDENTITY col

-- ---------------------------------------------------------------------------
-- Verification: after running, this should return 0 rows (empty table)
-- and list the 3 policies.  Run separately to confirm.
-- ---------------------------------------------------------------------------
-- SELECT COUNT(*) FROM public.readings;
-- SELECT policyname, cmd FROM pg_policies WHERE tablename = 'readings';
