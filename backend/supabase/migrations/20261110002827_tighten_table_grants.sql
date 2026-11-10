/* השומר בכניסה (מתן, 10.11.2026 — בדיקת אבטחה, נקודה 3).
 *
 * ברירת המחדל של Supabase נותנת ל-anon ול-authenticated הכל על כל טבלה —
 * כולל TRUNCATE, שלא כפוף ל-RLS בכלל. כל ההגנה נשענה על RLS לבדה. RLS
 * עדיין נועלת כל שורה; זו שכבה שנייה, כך שכלל RLS אחד שייכתב לא נכון
 * בעתיד לא יספיק כדי לחשוף נתונים.
 *
 *   · anon — מי שלא מחובר — לא צריך אף טבלה: התחברות, הרשמה וקישור הזמנה
 *     עוברים דרך פונקציות SECURITY DEFINER. (קריאה של families נשארה כאן
 *     לבדיקת הבריאות, ונסגרה במיגרציה הבאה, אחרי שהבדיקה עברה לפונקציה.)
 *   · authenticated — שומר SELECT/INSERT/UPDATE/DELETE (ו-RLS מגבילה אותם);
 *     מאבד רק TRUNCATE, TRIGGER ו-REFERENCES, שהאפליקציה לא משתמשת בהם.
 *   · ברירת המחדל לטבלאות חדשות — אותו דבר, כדי שזה לא יחזור מעצמו.
 *
 * נבדק ב-tests/test_table_grants.py.
 */
revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
grant select on public.families to anon;
revoke truncate, trigger, references on all tables in schema public from authenticated;
alter default privileges for role postgres in schema public revoke all on tables from anon;
alter default privileges for role postgres in schema public revoke all on sequences from anon;
alter default privileges for role postgres in schema public revoke truncate, trigger, references on tables from authenticated;
