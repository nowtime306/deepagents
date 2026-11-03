CREATE TABLE extra_cost_prices (
 id uuid PRIMARY KEY, model_id text NOT NULL, item text NOT NULL,
 unit text NOT NULL CHECK(unit IN ('call','image','audio_second','video_second')),
 currency text NOT NULL CHECK(currency IN ('CNY','USD')), unit_price numeric(24,9) NOT NULL CHECK(unit_price>=0),
 source text NOT NULL, effective_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX extra_cost_price_effective ON extra_cost_prices(model_id,item,effective_at DESC,id DESC);
CREATE TABLE extra_cost_usage (
 id uuid PRIMARY KEY, call_id uuid NOT NULL REFERENCES model_usage(call_id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES users(id), item text NOT NULL, price_id uuid NOT NULL REFERENCES extra_cost_prices(id),
 quantity numeric(24,9) CHECK(quantity>=0), amount numeric(24,9),
 source text NOT NULL CHECK(source IN ('provider','manual')), created_at timestamptz NOT NULL DEFAULT now(),
 confirmed_by uuid REFERENCES users(id),confirmed_at timestamptz,confirmation_note text,
 UNIQUE(call_id,item), CHECK((quantity IS NULL)=(amount IS NULL))
);
CREATE TABLE extra_cost_bill_imports (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), digest text NOT NULL,
 name text NOT NULL, report jsonb NOT NULL, scope jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,digest)
);
CREATE TABLE extra_cost_holds (
 call_id uuid NOT NULL REFERENCES model_usage(call_id) ON DELETE CASCADE, item text NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), price_id uuid NOT NULL REFERENCES extra_cost_prices(id),
 currency text NOT NULL CHECK(currency IN ('CNY','USD')), reserved numeric(24,9) NOT NULL CHECK(reserved>=0),
 actual numeric(24,9), window_start timestamptz NOT NULL, window_end timestamptz NOT NULL,
 PRIMARY KEY(call_id,item)
);
