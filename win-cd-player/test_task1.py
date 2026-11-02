# -*- coding: utf-8 -*-
import unittest
import py_compile
import re

class TestTask1TopBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_topbar_elements(self):
        header_start = self.content.find('<header class="top-bar">')
        self.assertNotEqual(header_start, -1)
        header_end = self.content.find('</header>', header_start)
        header_part = self.content[header_start:header_end]

        # 验证 4 个纯图标与状态灯
        self.assertIn('id="discflowAppBtn"', header_part)
        self.assertIn('id="topRescanBtn"', header_part)
        self.assertIn('id="topEjectBtn"', header_part)
        self.assertIn('id="topThemeBtn"', header_part)
        self.assertIn('id="statusDot"', header_part)

        # 验证无冗余文字标签
        self.assertNotIn('沉浸黑胶', header_part)
        self.assertNotIn('<span>读盘</span>', header_part)
        self.assertNotIn('<span>弹出</span>', header_part)

if __name__ == '__main__':
    unittest.main()
