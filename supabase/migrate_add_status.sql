-- =============================================================================
-- Migration: add status workflow columns to existing readings table
-- Run this in Supabase SQL Editor if the table already exists
-- (use this INSTEAD of schema.sql when upgrading an existing deployment)
-- =============================================================================

-- 1. Drop old 'processed' boolean if it exists
ALTER TABLE public.readings
  DROP COLUMN IF EXISTS processed;

-- 2. Add workflow status
ALTER TABLE public.readings
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'complete', 'error'));

-- 3. Add prediction result columns
ALTER TABLE public.readings
  ADD COLUMN IF NOT EXISTS ci_low_mg_dl      FLOAT8  NULL,
  ADD COLUMN IF NOT EXISTS ci_high_mg_dl     FLOAT8  NULL,
  ADD COLUMN IF NOT EXISTS glucose_category  TEXT    NULL,
  ADD COLUMN IF NOT EXISTS ood_warning       TEXT    NULL;

-- 4. Add new indexes for polling queries
CREATE INDEX IF NOT EXISTS readings_status_created_idx
  ON public.readings (status, created_at DESC);

CREATE INDEX IF NOT EXISTS readings_device_status_idx
  ON public.readings (device_id, status, created_at DESC);

-- 5. Verify — should show all column names including 'status'
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'readings'
ORDER BY ordinal_position;
