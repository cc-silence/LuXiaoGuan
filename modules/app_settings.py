def is_light_theme(theme_name=None, light_theme=False):
    """判断当前是否为亮色主题。

    参数：theme_name 是主题名称字符串（或 None），light_theme 是布尔标志位。
    返回值：light_theme 为 True 时直接返回 True；否则检查 theme_name 是否含 "light"。
    副作用：无。
    """
    if light_theme:
        return True
    return "light" in str(theme_name or "").lower()


class Settings():
    # 应用设置
    # ///////////////////////////////////////////////////////////////
    ENABLE_CUSTOM_TITLE_BAR = True
    MENU_WIDTH = 132
    RIGHT_BOX_WIDTH = 240
    TIME_ANIMATION = 200

    # 左右扩展面板按钮颜色
    BTN_RIGHT_BOX_COLOR = "background-color: #ff79c6;"

    # 菜单选中样式表
    MENU_SELECTED_STYLESHEET = """
    color: #F1F3F5;
    """
