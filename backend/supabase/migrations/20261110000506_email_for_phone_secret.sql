/* email_for_phone דורשת סוד שרק השרת מכיר (מתן, 10.11.2026 — בדיקת אבטחה).
 *
 * הפונקציה פתוחה ל-anon כי היא רצה לפני ההתחברות: מי שמקליד טלפון בטופס,
 * השרת מתרגם אותו למייל ורק אז בודק סיסמה. אבל "פתוחה ל-anon" פירושו שכל
 * מי שמחזיק את מפתח ה-anon יכול לעבור על מספרי טלפון ולאסוף את המיילים של
 * המשתמשים, בלי לעבור דרך האתר ובלי הגבלת הקצב שלו. היום המפתח שמור רק בשרת,
 * אבל Supabase מגדירה אותו כציבורי — וזה הדבר הראשון שייפתח ביום שייחשף.
 *
 * מכאן הפונקציה מחזירה מייל רק עם הסוד ‎login_lookup_secret‎ מה-Vault —
 * אותו דפוס כמו ‎push_daily‎. הסוד עצמו לא כאן (הריפו ציבורי); נוצר פעם אחת:
 *
 *   select vault.create_secret('<סוד>', 'login_lookup_secret');
 *
 * ובשרת: משתנה הסביבה ‎LOGIN_LOOKUP_SECRET‎ עם אותו ערך.
 *
 * הגרסה הישנה (ארגומנט אחד) נשארת עד שהקוד החדש עולה, כדי שהתחברות בטלפון
 * לא תישבר באמצע הפריסה. היא נמחקת במיגרציה הבאה.
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
