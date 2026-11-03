CREATE TABLE model_price_versions (
 id uuid PRIMARY KEY, model_id text NOT NULL,
 currency text NOT NULL CHECK(currency IN ('CNY','USD')), scheme text NOT NULL CHECK(scheme IN ('uniform','split')),
 input_per_million numeric(18,6) NOT NULL CHECK(input_per_million>=0),
 output_per_million numeric(18,6) NOT NULL CHECK(output_per_million>=0),
 cache_read_per_million numeric(18,6), cache_write_per_million numeric(18,6),
 source text NOT NULL, effective_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(scheme='uniform' OR (cache_read_per_million>=0 AND cache_write_per_million>=0
                          AND cache_read_per_million IS NOT NULL AND cache_write_per_million IS NOT NULL))
);
CREATE INDEX model_prices_effective ON model_price_versions(model_id,effective_at DESC,created_at DESC);
CREATE TABLE money_budgets (
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, currency text NOT NULL CHECK(currency IN ('CNY','USD')),
 amount numeric(18,6) NOT NULL CHECK(amount>0), period_days integer NOT NULL CHECK(period_days BETWEEN 1 AND 366),
 anchor_at timestamptz NOT NULL DEFAULT now(), enabled boolean NOT NULL DEFAULT true,
 revision integer NOT NULL DEFAULT 1, PRIMARY KEY(owner_id,currency)
);
CREATE TABLE model_money_holds (
 call_id uuid PRIMARY KEY REFERENCES model_usage(call_id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, currency text NOT NULL CHECK(currency IN ('CNY','USD')),
 window_start timestamptz NOT NULL, window_end timestamptz NOT NULL, reserved numeric(24,9) NOT NULL CHECK(reserved>=0),
 actual numeric(24,9), confirmed_by uuid REFERENCES users(id), confirmed_at timestamptz, confirmation_note text
);
CREATE INDEX money_hold_window ON model_money_holds(owner_id,currency,window_end);
ALTER TABLE model_usage ADD COLUMN price_version_id uuid REFERENCES model_price_versions(id) ON DELETE SET NULL;
ALTER TABLE model_usage ADD COLUMN cost_status text;
ALTER TABLE model_usage ADD COLUMN estimate_method text;
