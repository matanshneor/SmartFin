/* הגרסה הישנה של email_for_phone — בלי סוד — היא הפרצה עצמה (ראו
 * 20261110000506_email_for_phone_secret.sql). נמחקת אחרי שהקוד שקורא לגרסה
 * עם הסוד עלה לאוויר ונבדק בהתחברות אמיתית עם טלפון. נבדק גם ב-
 * tests/test_login_lookup_secret.py::test_old_unguarded_version_is_gone.
 */
drop function if exists public.email_for_phone(text);
