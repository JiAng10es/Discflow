# -*- coding: utf-8 -*-
import unittest
import py_compile
import re

class TestTask4ImmersiveVinylRefactor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_dynamic_color_extraction(self):
        vinyl_css_match = re.search(r'\.md-vinyl-disc\s*\{\{([\s\S]+?)\}\}\s*body\.is-playing', self.content)
        self.assertIsNotNone(vinyl_css_match)
        vinyl_css = vinyl_css_match.group(1)
        self.assertIn('--vinyl-tint-highlight', vinyl_css)
        self.assertIn('--ambient-primary', vinyl_css)

    def test_tonearm_positioning_and_no_drift(self):
        self.assertIn('id="mdVinylRig"', self.content)
        self.assertIn('id="immersiveTonearm"', self.content)
        self.assertIn('.md-tonearm-assembly', self.content)

    def test_interface_distraction_free(self):
        self.assertNotIn('<div class="md-top-bar">', self.content)

    def test_fluid_morph_card(self):
        self.assertIn('id="mdPlaylistMorphBtn"', self.content)
        self.assertIn('id="mdFluidMorphCard"', self.content)
        self.assertIn('id="fluidTrackList"', self.content)
        self.assertIn('--spring-fluid-morph:', self.content)

if __name__ == '__main__':
    unittest.main()
