"""Real icon pixels stay sharp at fractional DPI without changing controls."""
import tempfile
from pathlib import Path
import unittest

from tests.test_standalone import APP
from PySide6.QtCore import QSize, QRect, Qt
from PySide6.QtGui import QIcon, QImage, QPainter
from modules.config_manager import BASE_DIR
from modules.icon_utils import get_theme_icon, clear_icon_cache


class ChromeIconResolutionTests(unittest.TestCase):
    def test_both_privacy_states_and_theme_icons_supply_physical_pixels(self):
        for name in ("cil-lock-locked.png","cil-lock-unlocked.png","cil-sun.png","cil-moon.png"):
            for light in (False,True):
                icon=get_theme_icon(str(BASE_DIR/"images/icons"/name),light)
                for logical in (20,30,40):
                    for dpr in (1.,1.25,1.5,1.75,2.):
                        with self.subTest(name=name,light=light,logical=logical,dpr=dpr):
                            pixmap=icon.pixmap(QSize(logical,logical),dpr,QIcon.Normal,QIcon.Off)
                            self.assertEqual(pixmap.width(),(QSize(logical,logical)*dpr).width())
                            self.assertEqual(pixmap.height(),(QSize(logical,logical)*dpr).width())
                            self.assertAlmostEqual(pixmap.devicePixelRatio(),dpr)

    def test_paint_on_high_dpi_surface_has_transparent_antialiased_edges(self):
        for name in ("cil-lock-locked.png","cil-lock-unlocked.png","cil-sun.png","cil-moon.png"):
            icon=get_theme_icon(str(BASE_DIR/"images/icons"/name),True)
            for dpr in (1.,1.25,1.5,2.):
                image=QImage(round(24*dpr),round(24*dpr),QImage.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr);image.fill(Qt.transparent)
                painter=QPainter(image);icon.paint(painter,QRect(2,2,20,20));painter.end()
                colors=[image.pixelColor(x,y) for x in range(image.width()) for y in range(image.height())]
                self.assertTrue(any(0<c.alpha()<255 for c in colors),(name,dpr))
                self.assertTrue(any(c.alpha()>200 for c in colors),(name,dpr))
                self.assertTrue(all(c.red()==85 for c in colors if c.alpha()>200),(name,dpr))

    def test_open_and_closed_lock_remain_distinguishable(self):
        images=[get_theme_icon(str(BASE_DIR/"images/icons"/name),True).pixmap(40,40).toImage()
                for name in ("cil-lock-locked.png","cil-lock-unlocked.png")]
        self.assertNotEqual(images[0],images[1])

    def test_cached_icons_do_not_grow_per_requested_size(self):
        from modules import icon_utils
        clear_icon_cache()
        path=str(BASE_DIR/"images/icons/cil-lock-unlocked.png")
        icon=get_theme_icon(path,True)
        for size in range(16,96):
            icon.pixmap(size,size)
        self.assertEqual(sum(len(bucket) for bucket in icon_utils._icon_cache.values()),1)
        self.assertEqual(get_theme_icon(path,True).cacheKey(),icon.cacheKey())

    def test_user_image_with_same_name_is_not_replaced_by_vector(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cil-lock-unlocked.png"
            image=QImage(16,16,QImage.Format_ARGB32);image.fill(Qt.red);image.save(str(path))
            actual=get_theme_icon(str(path),False).pixmap(16,16).toImage()
            self.assertEqual(actual.pixelColor(8,8).red(),255)
            self.assertEqual(actual.pixelColor(8,8).alpha(),255)
