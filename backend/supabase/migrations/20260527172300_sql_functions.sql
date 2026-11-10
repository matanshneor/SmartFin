/* The first SQL functions, May 27th 2026.
 *
 * Restored together with `20260527172248`; see the explanation there.
 * Already applied. Do not run it again.
 */

-- Monthly archive per family
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

-- Allow family members to see each other's profiles
CREATE POLICY "profiles_family_read" ON profiles
    FOR SELECT USING (
        family_id IS NOT NULL
        AND family_id IN (
            SELECT family_id FROM profiles WHERE id = auth.uid()
        )
    );

-- Returns all profiles in the same family (bypasses RLS)
CREATE OR REPLACE FUNCTION get_family_members(p_family_id UUID)
RETURNS TABLE (
    id              UUID,
    name            TEXT,
    avatar_initial  TEXT
)
LANGUAGE SQL STABLE SECURITY DEFINER AS $$
    SELECT id, name, avatar_initial
    FROM profiles
    WHERE family_id = p_family_id
    ORDER BY created_at;
$$;
