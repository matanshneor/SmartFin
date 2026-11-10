/* Family settings were saved read-modify-write, with no lock.
 *
 * `update_family_settings` fetched the JSON, merged in Python, and wrote the
 * full map back. Four gunicorn workers, one row, and no `where settings =
 * <old>` at all:
 *
 *   20:00:00.1  Mum sets a ₪2,000 budget for "סופר ומזון"
 *   20:00:00.3  Dad turns off "expense attribution" from his phone
 *
 * Both read the same JSON. Dad's write carries `limits` from before Mum's and
 * deletes her budget. Both got `{"status":"ok"}`, and Mum's screen kept showing
 * the budget until the next refresh. `limits` is a replace-key, so the loss is
 * total, not partial, and with `owner_attribution` it is a question of **where
 * money is attributed**.
 *
 * The merge moves here, into a single `UPDATE` statement. The semantics are
 * identical to `_merge_settings`: one level deep, with a list of keys replaced
 * whole.
 */
create or replace function public.merge_family_settings(
    p_family_id  uuid,
    p_patch      jsonb,
    p_whole_keys text[] default array['limits']
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_settings jsonb;
    v_key      text;
    v_new      jsonb;
    v_old      jsonb;
begin
    if p_family_id is null then
        raise exception 'no family';
    end if;

    -- Access derives from the signed-in user: `security definer` bypasses RLS.

    if not exists (select 1 from public.profiles
                    where id = auth.uid() and family_id = p_family_id) then
        raise exception 'not a member of that family';
    end if;

    -- `for update` locks the row until the end of the statement, so the second
    -- caller waits and merges onto the value **after** the first one instead of
    -- the value before it.

    select coalesce(settings, '{}'::jsonb) into v_settings
      from public.families where id = p_family_id for update;

    for v_key in select jsonb_object_keys(coalesce(p_patch, '{}'::jsonb))
    loop
        v_new := p_patch -> v_key;
        v_old := v_settings -> v_key;

        if v_key = any(p_whole_keys) then
            -- A whole map: removing a budget is represented by its absence from
            -- the map, so a merge can't express it, only a replace.

            v_settings := jsonb_set(v_settings, array[v_key], v_new, true);
        elsif jsonb_typeof(v_new) = 'object' and jsonb_typeof(v_old) = 'object' then
            v_settings := jsonb_set(v_settings, array[v_key], v_old || v_new, true);
        else
            v_settings := jsonb_set(v_settings, array[v_key], v_new, true);
        end if;
    end loop;

    update public.families set settings = v_settings where id = p_family_id;
    return v_settings;
end;
$$;

revoke all on function public.merge_family_settings(uuid, jsonb, text[]) from public, anon;
grant execute on function public.merge_family_settings(uuid, jsonb, text[]) to authenticated;
