/* email_for_phone requires a secret only the server knows (Matan, 10.11.2026,
 * security review).
 *
 * The function is open to anon because it runs before sign-in: whoever types
 * a phone into the form, the server translates it into an email and only then
 * checks the password. But "open to anon" means anyone holding the anon key
 * can walk through phone numbers and collect users' emails, without going
 * through the site and without its rate limit. Today the key is kept on the
 * server only, but Supabase defines it as public, and this is the first thing
 * that opens up the day it is exposed.
 *
 * From now on the function returns an email only with the secret
 * `login_lookup_secret` from the Vault, the same pattern as `push_daily`. The
 * secret itself isn't here (the repo is public); it was created once:
 *
 *   select vault.create_secret('<secret>', 'login_lookup_secret');
 *
 * And on the server: the environment variable `LOGIN_LOOKUP_SECRET` with the
 * same value.
 *
 * The old version (one argument) stays until the new code is live, so phone
 * sign-in doesn't break mid-deploy. It is dropped in the next migration.
 */

create or replace function public.email_for_phone(p_phone text, p_secret text)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select u.email
    from public.profiles p
    join auth.users u on u.id = p.id
    where p.phone = p_phone
      and coalesce(p_secret, '') <> ''
      and exists (
          select 1 from vault.decrypted_secrets
          where name = 'login_lookup_secret' and decrypted_secret = p_secret
      )
    limit 1;
$$;

revoke all on function public.email_for_phone(text, text) from public;
grant execute on function public.email_for_phone(text, text) to anon, authenticated;
