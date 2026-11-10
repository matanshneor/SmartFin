/* The policies here are also defined in the first schema file. `CREATE POLICY`
 * has no `IF NOT EXISTS`, so a build from scratch stopped here with 42710. The
 * ledger is complete, so on the linked project this isn't visible, but disaster
 * recovery, a test environment and `db reset` all failed. `drop policy if
 * exists` before each one makes the file re-runnable without changing the
 * result. */

-- Allow family members to read each other's profiles
drop policy if exists "profiles_family_read" on profiles;
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
