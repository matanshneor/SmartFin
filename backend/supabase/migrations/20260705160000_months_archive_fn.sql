-- Moved from schema_functions.sql (deleted), so every database definition
-- lives in one place: the migrations folder.
-- Returns a monthly summary (income/expenses/savings/balance) for every active
-- month of a family.

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
