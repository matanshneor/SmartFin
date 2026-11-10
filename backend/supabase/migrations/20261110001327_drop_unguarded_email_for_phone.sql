/* The old version of email_for_phone (without a secret) is the hole itself
 * (see 20261110000506_email_for_phone_secret.sql). Dropped after the code
 * calling the version with the secret went live and was checked with a real
 * phone sign-in. Also checked in
 * tests/test_login_lookup_secret.py::test_old_unguarded_version_is_gone.
 */
drop function if exists public.email_for_phone(text);
