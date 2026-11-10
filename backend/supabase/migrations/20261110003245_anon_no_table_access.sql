/* הקריאה האחרונה של anon — families, לבדיקת הבריאות — נסגרת.
 *
 * היא גם לא עבדה: מדיניות ה-RLS של families קוראת את profiles, ול-anon כבר
 * לא הייתה הרשאה עליה, אז ‎/health/db‎ החזיר 401. ‎ping‎ עובר עכשיו דרך
 * ‎rpc/get_my_family_id‎ (SECURITY DEFINER), כך של-anon אין שום הרשאה על
 * אף טבלה.
 */
revoke select on public.families from anon;
