# White navigation button investigation (2026-10-25)

The reported shape is an entire white button background, not simply a white icon. The normally hidden legacy sidebar was temporarily hosted in an isolated test window; production navigation visibility and styles were not changed.

With dark.qss loaded, old buttons and current settings navigation render normally under the available WindowsVista, Windows and Fusion styles. With the main theme missing, or with ConfigManager.DEFAULT_THEME_LABEL, theme_stylesheet_text returns empty. build_main_stylesheet still applies the dark frame fragment, while navigation selectors from dark.qss are absent. The button therefore uses the native light button background: about 93.5 percent of its pixels are bright, compared with roughly 2-7 percent with normal styles. This reproduces the provided screenshot's white block pattern. The image resource is not required to be corrupt to produce it.

The settings dialog loads an independent fallback stylesheet, explaining how settings navigation can remain dark alongside an unstyled old main sidebar. Current visible main-window and settings buttons were rendered 114 times across normal, missing and legacy-default theme conditions; no white-background candidate above the 70 percent threshold was found. Signal navigation and focus/theme regressions were included in the combined test run. These are local Qt 6.3.0 renders, not a reproduction on the user's Win11 device.

The exact user's old version/configuration has not yet been provided; the reproduced code path is confirmed, but attribution of that particular device's screenshot remains pending. No theme fallback or normal visual style was changed during this investigation.

Evidence: build/navigation-win11-review/styles.json, missing-theme.json, current-widgets.json and summary.json. Reference: https://doc.qt.io/qt-6/stylesheet.html (QSS wraps the native style; unspecified rendering falls through to the platform style).
