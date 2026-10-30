from vkbottle import Keyboard, KeyboardButtonColor, Text


def get_main_menu(is_admin: bool = False) -> str:
    """Return JSON string for the persistent main menu keyboard."""
    kb = Keyboard(one_time=False, inline=False)
    kb.add(Text("✂️ Записаться"), color=KeyboardButtonColor.PRIMARY)
    kb.add(Text("📍 Как добраться"), color=KeyboardButtonColor.SECONDARY)
    kb.row()
    kb.add(Text("📋 Мои записи"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("🎁 Пригласить друга"), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Text("🧴 Советы и товары"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("🌐 Наш сайт"), color=KeyboardButtonColor.SECONDARY)

    if is_admin:
        kb.row()
        kb.add(Text("⚙️ Админ-панель"), color=KeyboardButtonColor.SECONDARY)

    return kb.get_json()
