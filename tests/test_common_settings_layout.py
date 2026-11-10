"""Common settings retain geometry during probes and use available columns."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from tests.common_settings_fixture import create_page, APP


class CommonSettingsLayoutTests(unittest.TestCase):
    def settle(self):
        for _ in range(5):
            APP.processEvents()

    def test_background_probe_does_not_move_cards_but_errors_remain_visible(self):
        page, _ = create_page(self)
        state = SimpleNamespace(binding=lambda: object(), missing=(), error='',
            probe=None, ready=False, checking=False, install_session=None, changed=Mock())
        page.dependency_banner.readiness = state
        page._sync()
        self.settle()
        before = page.scroll.geometry()
        for checking in (True, False):
            state.checking = checking
            state.probe = None if checking else {'root': True}
            state.ready = not checking
            page._sync()
            self.settle()
            self.assertTrue(page.dependency_banner.row.isHidden())
            self.assertEqual(page.scroll.geometry(), before)
        state.error = 'probe failed'
        state.ready = False
        page._sync()
        self.settle()
        self.assertTrue(page.dependency_banner.row.isVisible())
        self.assertTrue(page.install_button.isEnabled())

    def test_wide_and_narrow_layouts_reuse_cards_and_clear_old_columns(self):
        output = Path('build/common-settings-layout')
        output.mkdir(parents=True, exist_ok=True)
        for light in (False, True):
            page, _ = create_page(self, light=light)
            identities = tuple(id(card) for card in page.cards)
            for width in (1850, 760, 1450):
                page.resize(width, 1000)
                self.settle()
                columns = page._card_columns
                if width == 1850:
                    self.assertGreater(columns, 3)
                if width == 760:
                    self.assertLess(columns, 3)
                for grid, (_, cards) in zip(page.card_groups.grids, page.card_groups.groups):
                    for index, card in enumerate(cards):
                        self.assertEqual(grid.getItemPosition(grid.indexOf(card))[:2],
                                         (index // columns, index % columns))
                    for col in range(columns, grid.columnCount()):
                        self.assertEqual(grid.columnStretch(col), 0)
                    widths = [card.width() for card in cards[:columns]]
                    self.assertLessEqual(max(widths) - min(widths), 1)
                self.assertEqual(tuple(id(card) for card in page.cards), identities)
                page.grab().save(str(output / ('%s-%s.png' % (light, width))))
