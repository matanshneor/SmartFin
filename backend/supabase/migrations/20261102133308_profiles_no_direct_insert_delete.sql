/* סוגר את העקיפה של נעילת השיוך למשפחה (20260916100000_lock_family_membership).
 *
 * הנעילה ההיא הסירה את הרשאת ה-UPDATE על profiles.family_id — אבל השאירה
 * למשתמש מאומת DELETE על הטבלה כולה ו-INSERT על family_id. כלומר אותה
 * תקיפה בשני צעדים במקום אחד:
 *
 *   1. DELETE על שורת הפרופיל של עצמי (profiles_own היא FOR ALL).
 *   2. INSERT של שורה חדשה עם id = auth.uid() ו-family_id של משפחה אחרת.
 *      ה-WITH CHECK בודק רק ש-id = auth.uid(), ולכן זה עובר.
 *
 * מאותו רגע כל המדיניות בשאר הטבלאות נותנות גישה מלאה למשפחה הזרה, כי
 * כולן נגזרות מ-profiles.family_id.
 *
 * לא ניתן לניצול דרך האפליקציה (אין מסלול Flask שמוחק או יוצר פרופיל, ומפתח
 * ה-anon שמור בשרת), וצריך גם לדעת את ה-UUID של המשפחה. אבל RLS היא השכבה
 * שאמורה לעצור את זה גם כשהשכבות האחרות נכשלות.
 *
 * אף מסלול לגיטימי לא משתמש בהרשאות האלה:
 *   - פרופיל נוצר בהרשמה ע"י הטריגר handle_new_user (SECURITY DEFINER).
 *   - פרופיל נמחק במחיקת חשבון ע"י delete_my_account (SECURITY DEFINER).
 *   - השיוך משתנה רק דרך create_own_family / join_family_by_code /
 *     leave_family / remove_family_member (כולן SECURITY DEFINER).
 */

revoke insert, delete on public.profiles from authenticated, anon;
