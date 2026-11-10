/* The policies here are also defined in the first schema file. `CREATE POLICY`
 * has no `IF NOT EXISTS`, so a build from scratch stopped here with 42710. The
 * ledger is complete, so on the linked project this isn't visible, but disaster
 * recovery, a test environment and `db reset` all failed. `drop policy if
 * exists` before each one makes the file re-runnable without changing the
 * result. */

-- =============================================
-- SmartFin – Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor
-- =============================================

-- ===== FAMILIES =====
CREATE TABLE IF NOT EXISTS families (
    id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== PROFILES (extends auth.users) =====
CREATE TABLE IF NOT EXISTS profiles (
    id              UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
    name            TEXT NOT NULL,
    family_id       UUID REFERENCES families(id) ON DELETE SET NULL,
    avatar_initial  TEXT GENERATED ALWAYS AS (UPPER(LEFT(name, 1))) STORED,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO public.profiles (id, name)
    VALUES (NEW.id, COALESCE(NEW.raw_user_meta_data->>'name', 'משתמש'));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();

-- ===== CATEGORIES =====
CREATE TABLE IF NOT EXISTS categories (
    id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name       TEXT NOT NULL,
    icon       TEXT NOT NULL DEFAULT '📦',
    type       TEXT NOT NULL CHECK (type IN ('expense', 'income', 'savings')),
    family_id  UUID REFERENCES families(id) ON DELETE CASCADE,
    is_custom  BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default categories (global, no family_id)
INSERT INTO categories (name, icon, type, is_custom) VALUES
    ('מזון',        '🛒', 'expense',  FALSE),
    ('רכב',         '🚗', 'expense',  FALSE),
    ('בידור',       '🎬', 'expense',  FALSE),
    ('חשבונות',     '📱', 'expense',  FALSE),
    ('דיור',        '🏠', 'expense',  FALSE),
    ('בריאות',      '🏥', 'expense',  FALSE),
    ('אחר',         '📦', 'expense',  FALSE),
    ('משכורת',      '💼', 'income',   FALSE),
    ('פרילנס',      '💻', 'income',   FALSE),
    ('הכנסה אחרת', '💵', 'income',   FALSE),
    ('חיסכון',      '💰', 'savings',  FALSE),
    ('השקעה',       '📈', 'savings',  FALSE)
ON CONFLICT DO NOTHING;

-- ===== TRANSACTIONS =====
CREATE TABLE IF NOT EXISTS transactions (
    id                   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    amount               NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    type                 TEXT NOT NULL CHECK (type IN ('expense', 'income', 'savings')),
    category_id          UUID REFERENCES categories(id) ON DELETE SET NULL,
    description          TEXT,
    date                 DATE NOT NULL DEFAULT CURRENT_DATE,
    user_id              UUID REFERENCES profiles(id) ON DELETE SET NULL,
    family_id            UUID REFERENCES families(id) ON DELETE CASCADE NOT NULL,
    is_recurring         BOOLEAN DEFAULT FALSE,
    recurring_frequency  TEXT CHECK (recurring_frequency IN ('monthly_1', 'monthly_15', 'weekly', 'biweekly')),
    recurring_end_date   DATE,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast monthly queries
CREATE INDEX IF NOT EXISTS idx_transactions_family_date
    ON transactions (family_id, date DESC);

CREATE INDEX IF NOT EXISTS idx_transactions_family_month
    ON transactions (family_id, EXTRACT(YEAR FROM date), EXTRACT(MONTH FROM date));

-- ===== ROW LEVEL SECURITY =====
ALTER TABLE families     ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles     ENABLE ROW LEVEL SECURITY;
ALTER TABLE categories   ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;

-- Profiles: user can read/update own profile
drop policy if exists "profiles_own" on profiles;
CREATE POLICY "profiles_own" ON profiles
    FOR ALL USING (auth.uid() = id);

-- Families: members can read their family
drop policy if exists "families_member_read" on families;
CREATE POLICY "families_member_read" ON families
    FOR SELECT USING (
        id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

-- Categories: read global + own family's custom
drop policy if exists "categories_read" on categories;
CREATE POLICY "categories_read" ON categories
    FOR SELECT USING (
        family_id IS NULL
        OR family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

drop policy if exists "categories_insert" on categories;

CREATE POLICY "categories_insert" ON categories
    FOR INSERT WITH CHECK (
        family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

drop policy if exists "categories_delete" on categories;

CREATE POLICY "categories_delete" ON categories
    FOR DELETE USING (
        is_custom = TRUE
        AND family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

-- Transactions: family members can read/write own family
drop policy if exists "transactions_family_select" on transactions;
CREATE POLICY "transactions_family_select" ON transactions
    FOR SELECT USING (
        family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

drop policy if exists "transactions_family_insert" on transactions;

CREATE POLICY "transactions_family_insert" ON transactions
    FOR INSERT WITH CHECK (
        family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );

drop policy if exists "transactions_family_delete" on transactions;

CREATE POLICY "transactions_family_delete" ON transactions
    FOR DELETE USING (
        family_id IN (SELECT family_id FROM profiles WHERE id = auth.uid())
    );
-- =============================================
-- SmartFin – SQL Functions
-- Run this AFTER schema.sql in Supabase SQL Editor
-- =============================================

-- Returns a monthly archive summary for a given family,
-- sorted from newest month to oldest.
CREATE OR REPLACE FUNCTION get_months_archive(p_family_id UUID)
RETURNS TABLE (
    year     INT,
    month    INT,
    income   NUMERIC,
    expense  NUMERIC,
    savings  NUMERIC,
    balance  NUMERIC
)
LANGUAGE SQL STABLE SECURITY DEFINER AS $$
    SELECT
        EXTRACT(YEAR  FROM date)::INT  AS year,
        EXTRACT(MONTH FROM date)::INT  AS month,
        COALESCE(SUM(amount) FILTER (WHERE type = 'income'),  0) AS income,
        COALESCE(SUM(amount) FILTER (WHERE type = 'expense'), 0) AS expense,
        COALESCE(SUM(amount) FILTER (WHERE type = 'savings'), 0) AS savings,
        COALESCE(SUM(amount) FILTER (WHERE type = 'income'),  0)
            - COALESCE(SUM(amount) FILTER (WHERE type = 'expense'), 0) AS balance
    FROM transactions
    WHERE family_id = p_family_id
    GROUP BY year, month
    ORDER BY year DESC, month DESC;
$$;
