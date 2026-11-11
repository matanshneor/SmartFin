"""Line icons for buttons and markers in the UI (Matan, 12.10).

Until then some buttons were emoji (📷 scan, 📅 date, 📎 receipt, 🔁 recurring):
colourful and in the OS's style, next to the site's gold line icons. Now they
are all the same line style. Emoji remain only where the user picks them:
categories and projects.

`currentColor`: the icon takes the surrounding text colour; size `1em`: the
text size."""
from markupsafe import Markup

_PATHS = {
    "camera":   '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
                '<circle cx="12" cy="13" r="3"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "receipt":  '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57'
                'a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    # Empty states (Matan, 12.10), replacing 📭 📅 🎯 🔁 🔍 ⚠️ 📊 💼 💰
    "inbox":    '<path d="M22 12h-6l-2 3h-4l-2-3H2"/>'
                '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "target":   '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "search":   '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "alert":    '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
                '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "pie":      '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    "wallet":   '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/>'
                '<path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "coins":    '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/>'
                '<path d="m16.71 13.88.7.71-2.82 2.82"/>',
    "repeat":   '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/>'
                '<path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    # "My account" (Matan, 13.10): one row per detail, like "Recurring transactions"
    "user":     '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "palette":  '<circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="10" r="1"/><circle cx="12" cy="7.5" r="1"/>'
                '<circle cx="15.5" cy="10" r="1"/>',
    "mail":     '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "phone":    '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    "lock":     '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    # Instead of decorative emoji (design pass, 11.11): 📊 📉 📈 ✨ 👋
    "bar-chart":  '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "trend-down": '<path d="m22 17-8.5-8.5-5 5L2 7"/><path d="M16 17h6v-6"/>',
    "trend-up":   '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
    "users":      '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
                  '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "plus-circle": '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
}


def empty_illus(name: str) -> Markup:
    """Empty-state illustration: the icon, large, in a soft gold circle."""
    return Markup(f'<div class="empty-illus" aria-hidden="true">{ui_icon(name)}</div>')


def ui_icon(name: str) -> Markup:
    return Markup(
        '<svg class="ui-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{_PATHS[name]}</svg>')
