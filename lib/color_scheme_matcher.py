"""
color_scheme_matcher.

Licensed under MIT.

Copyright (C) 2012  Andrew Gibson <agibsonsw@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of
the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

---------------------

Original code has been heavily modifed by Isaac Muse <isaacmuse@gmail.com> for the ExportHtml project.
Algorithm has been split out into a separate library and been enhanced with a number of features.
"""
from __future__ import absolute_import
import sublime
import re
from .rgba import RGBA, clamp, round_int
from . import x11colors
from os import path
from collections import namedtuple
from plistlib import readPlistFromBytes
from .file_strip.json import sanitize_json
import json
import decimal

# For new Sublime format
FLOAT_TRIM_RE = re.compile(r'^(?P<keep>\d+)(?P<trash>\.0+|(?P<keep2>\.\d*[1-9])0+)$')

COLOR_PARTS = {
    "percent": r"[+\-]?(?:(?:\d*\.\d+)|\d+)%",
    "float": r"[+\-]?(?:(?:\d*\.\d+)|\d+)"
}

RGB_COLORS = r"""(?x)
    (?P<hexa>\#(?P<hexa_content>[\dA-Fa-f]{8}))\b |
    (?P<hex>\#(?P<hex_content>[\dA-Fa-f]{6}))\b |
    (?P<hexa_compressed>\#(?P<hexa_compressed_content>[\dA-Fa-f]{4}))\b |
    (?P<hex_compressed>\#(?P<hex_compressed_content>[\dA-Fa-f]{3}))\b |
    \b(?P<rgb>rgb\(\s*(?P<rgb_content>(?:%(float)s\s*,\s*){2}%(float)s | (?:%(percent)s\s*,\s*){2}%(percent)s)\s*\)) |
    \b(?P<rgba>rgba\(\s*(?P<rgba_content>
        (?:%(float)s\s*,\s*){3}(?:%(percent)s|%(float)s) | (?:%(percent)s\s*,\s*){3}(?:%(percent)s|%(float)s)
    )\s*\))
""" % COLOR_PARTS

HSL_COLORS = r"""(?x)
    \b(?P<hsl>hsl\(\s*(?P<hsl_content>%(float)s\s*,\s*%(percent)s\s*,\s*%(percent)s)\s*\)) |
    \b(?P<hsla>hsla\(\s*(?P<hsla_content>%(float)s\s*,\s*(?:%(percent)s\s*,\s*){2}(?:%(percent)s|%(float)s))\s*\))
""" % COLOR_PARTS

VARIABLES = r"""(?x)
    \b(?P<var>var\(\s*(?P<var_content>\w[\w\d]*)\s*\))
"""

COLOR_MOD = r"""(?x)
    \b(?P<color>color\((?P<color_content>.*)\))
"""

COLOR_NAMES = r'\b(?P<x11colors>%s)\b(?!\()' % '|'.join([name for name in x11colors.name2hex_map.keys()])

COLOR_RE = re.compile(
    r'(?x)(?i)(?:%s|%s|%s|%s|%s)' % (
        RGB_COLORS,
        HSL_COLORS,
        VARIABLES,
        COLOR_MOD,
        COLOR_NAMES
    )
)

COLOR_RGB_SPACE_RE = re.compile(
    r'(?x)(?i)(?:%s|%s|%s)' % (
        RGB_COLORS,
        VARIABLES,
        COLOR_NAMES
    )
)

COLOR_MOD_RE = re.compile(
    r'''(?x)
    color\(\s*
        (?P<base>\#[\dA-Fa-f]{8}|\#[\dA-Fa-f]{6})
        \s+(?P<type>blenda?)\(
            (?P<color>\#[\dA-Fa-f]{8}|\#[\dA-Fa-f]{6})
            \s+(?P<percent>%(percent)s)
        \)
        (?P<other>
            (?:\s+blenda?\((?:\#[\dA-Fa-f]{8}|\#[\dA-Fa-f]{6})\s+%(percent)s\))+
        )?
    \s*\)
    ''' % COLOR_PARTS
)


def fmt_float(f, p=0):
    """Set float precision and trim precision zeros."""

    string = str(
        decimal.Decimal(f).quantize(decimal.Decimal('0.' + ('0' * p) if p > 0 else '0'), decimal.ROUND_HALF_UP)
    )

    m = FLOAT_TRIM_RE.match(string)
    if m:
        string = m.group('keep')
        if m.group('keep2'):
            string += m.group('keep2')
    return string


def alpha_dec_normalize(dec):
    """Normailze a deciaml alpha value."""

    temp = float(dec)
    if temp < 0.0 or temp > 1.0:
        dec = fmt_float(clamp(float(temp), 0.0, 1.0), 3)
    alpha = "%02x" % round_int(float(dec) * 255.0)
    return alpha


def alpha_percent_normalize(perc):
    """Normailze a percent alpha value."""

    alpha_float = clamp(float(perc.strip('%')), 0.0, 100.0) / 100.0
    alpha = "%02x" % round_int(alpha_float * 255.0)
    return alpha


def blend(m):
    """Blend colors."""

    base = m.group('base')
    color = m.group('color')
    blend_type = m.group('type')
    percent = m.group('percent')
    if percent.endswith('%'):
        percent = float(percent.strip('%'))
    else:
        percent = int(alpha_dec_normalize(percent), 16) * (100.0 / 255.0)
    rgba = RGBA(base)
    if blend_type == 'blend':
        rgba.blend(color, percent)
    else:
        rgba.blend(color, percent)
        # rgba.blenda(color, percent)
    color = rgba.get_rgb() if rgba.a == 255 else rgba.get_rgba()
    if m.group('other'):
        color = "color(%s %s)" % (color, m.group('other'))
    return color


def translate_color(m, var, var_src):
    """Translate the match object to a color w/ alpha."""

    color = None
    alpha = None
    groups = m.groupdict()
    if groups.get('hex_compressed'):
        content = m.group('hex_compressed_content')
        color = "#%02x%02x%02x" % (
            int(content[0:1] * 2, 16), int(content[1:2] * 2, 16), int(content[2:3] * 2, 16)
        )
    elif groups.get('hexa_compressed'):
        content = m.group('hexa_compressed_content')
        color = "#%02x%02x%02x" % (
            int(content[0:1] * 2, 16), int(content[1:2] * 2, 16), int(content[2:3] * 2, 16)
        )
        alpha = content[3:]
    elif groups.get('hex'):
        content = m.group('hex_content')
        if len(content) == 6:
            color = "#%02x%02x%02x" % (
                int(content[0:2], 16), int(content[2:4], 16), int(content[4:6], 16)
            )
        else:
            color = "#%02x%02x%02x" % (
                int(content[0:1] * 2, 16), int(content[1:2] * 2, 16), int(content[2:3] * 2, 16)
            )
    elif groups.get('hexa'):
        content = m.group('hexa_content')
        if len(content) == 8:
            color = "#%02x%02x%02x" % (
                int(content[0:2], 16), int(content[2:4], 16), int(content[4:6], 16)
            )
            alpha = content[6:]
        else:
            color = "#%02x%02x%02x" % (
                int(content[0:1] * 2, 16), int(content[1:2] * 2, 16), int(content[2:3] * 2, 16)
            )
            alpha = content[3:]
    elif groups.get('rgb'):
        content = [x.strip() for x in m.group('rgb_content').split(',')]
        if content[0].endswith('%'):
            r = round_int(clamp(float(content[0].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            g = round_int(clamp(float(content[1].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            b = round_int(clamp(float(content[2].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            color = "#%02x%02x%02x" % (r, g, b)
        else:
            color = "#%02x%02x%02x" % (
                clamp(round_int(float(content[0])), 0, 255),
                clamp(round_int(float(content[1])), 0, 255),
                clamp(round_int(float(content[2])), 0, 255)
            )
    elif groups.get('rgba'):
        content = [x.strip() for x in m.group('rgba_content').split(',')]
        if content[0].endswith('%'):
            r = round_int(clamp(float(content[0].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            g = round_int(clamp(float(content[1].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            b = round_int(clamp(float(content[2].strip('%')), 0.0, 255.0) * (255.0 / 100.0))
            color = "#%02x%02x%02x" % (r, g, b)
        else:
            color = "#%02x%02x%02x" % (
                clamp(round_int(float(content[0])), 0, 255),
                clamp(round_int(float(content[1])), 0, 255),
                clamp(round_int(float(content[2])), 0, 255)
            )
        if content[3].endswith('%'):
            alpha = alpha_percent_normalize(content[3])
        else:
            alpha = alpha_dec_normalize(content[3])
    elif groups.get('hsl'):
        content = [x.strip() for x in m.group('hsl_content').split(',')]
        rgba = RGBA()
        hue = float(content[0])
        if hue < 0.0 or hue > 360.0:
            hue = hue % 360.0
        h = hue / 360.0
        s = clamp(float(content[1].strip('%')), 0.0, 100.0) / 100.0
        l = clamp(float(content[2].strip('%')), 0.0, 100.0) / 100.0
        rgba.fromhls(h, l, s)
        color = rgba.get_rgb()
    elif groups.get('hsla'):
        content = [x.strip() for x in m.group('hsla_content').split(',')]
        rgba = RGBA()
        hue = float(content[0])
        if hue < 0.0 or hue > 360.0:
            hue = hue % 360.0
        h = hue / 360.0
        s = clamp(float(content[1].strip('%')), 0.0, 100.0) / 100.0
        l = clamp(float(content[2].strip('%')), 0.0, 100.0) / 100.0
        rgba.fromhls(h, l, s)
        color = rgba.get_rgb()
        if content[3].endswith('%'):
            alpha = alpha_percent_normalize(content[3])
        else:
            alpha = alpha_dec_normalize(content[3])
    elif groups.get('var'):
        content = m.group('var_content')
        if content in var:
            color = var[content]
        else:
            v = var_src[content]
            m = COLOR_RE.match(v.strip())
            color = translate_color(m, var, var_src)
    elif groups.get('x11colors'):
        try:
            color = x11colors.name2hex(m.group('x11colors')).lower()
        except:
            pass
    elif groups.get('color'):
        content = m.group('color')
        content = COLOR_RGB_SPACE_RE.sub((lambda match, v=var, vs=var_src: translate_color(match, v, vs)), content)
        n = -1
        while n:
            content, n = COLOR_MOD_RE.subn(blend, content)
        color = content

    if color is not None and alpha is not None:
        color += alpha

    return color


def sublime_format_path(pth):
    """Format path for sublime internal use."""

    m = re.match(r"^([A-Za-z]{1}):(?:/|\\)(.*)", pth)
    if sublime.platform() == "windows" and m is not None:
        pth = m.group(1) + "/" + m.group(2)
    return pth.replace("\\", "/")


class SchemeColors(
    namedtuple(
        'SchemeColors',
        ['fg', 'fg_simulated', 'bg', "bg_simulated", "style", "fg_selector", "bg_selector", "style_selectors"],
        verbose=False
    )
):
    """SchemeColors."""


class SchemeSelectors(namedtuple('SchemeSelectors', ['name', 'scope'], verbose=False)):
    """SchemeSelectors."""


class ColorSchemeMatcher(object):
    """Determine color scheme colors and style for text in a Sublime view buffer."""

    def __init__(self, scheme_file, color_filter=None):
        """Initialize."""
        if color_filter is None:
            color_filter = self.filter
        self.legacy = not scheme_file.lower().endswith('.sublime-color-scheme')
        self.color_scheme = path.normpath(scheme_file)
        self.scheme_file = path.basename(self.color_scheme)
        if self.legacy:
            self.scheme_obj = color_filter(
                readPlistFromBytes(
                    re.sub(
                        br"^[\r\n\s]*<!--[\s\S]*?-->[\s\r\n]*|<!--[\s\S]*?-->", b'',
                        sublime.load_binary_resource(sublime_format_path(self.color_scheme))
                    )
                )
            )
        else:
            self.scheme_obj = json.loads(
                sanitize_json(
                    sublime.load_resource(sublime_format_path(self.color_scheme)),
                    preserve_lines=True
                )
            )
        self.scheme_file = scheme_file
        self.matched = {}
        self.variables = {}

        self.parse_scheme()

    def filter(self, plist):
        """Dummy filter call that does nothing."""

        return plist

    def parse_scheme(self):
        """Parse the color scheme."""

        if self.legacy:
            color_settings = {}
            for item in self.scheme_obj["settings"]:
                if item.get('scope', None) is None and item.get('name', None) is None:
                    color_settings = item["settings"]
                    break
        else:
            for k, v in self.scheme_obj.get('variables', {}).items():
                m = COLOR_RE.match(v.strip())
                self.variables[k] = translate_color(m, self.variables, self.scheme_obj.get('variables'))

            color_settings = {}
            for k, v in self.scheme_obj["defaults"].items():
                m = COLOR_RE.match(v.strip())
                if m is not None:
                    color_settings[k] = translate_color(m, self.variables, {})

        # Get general theme colors from color scheme file
        bground, bground_sim = self.process_color(
            color_settings.get("background", '#FFFFFF'), simple_strip=True
        )

        # Need to set background so other colors can simulate their transparency.
        self.special_colors = {
            "background": {'color': bground, 'color_simulated': bground_sim}
        }

        fground, fground_sim = self.process_color(color_settings.get("foreground", '#000000'))
        sbground = self.process_color(color_settings.get("selection", fground))[0]
        sbground_sim = self.process_color(color_settings.get("selection", fground_sim))[1]
        sfground, sfground_sim = self.process_color(color_settings.get("selectionForeground", None))
        gbground = self.process_color(color_settings.get("gutter", bground))[0]
        gbground_sim = self.process_color(color_settings.get("gutter", bground_sim))[1]
        gfground = self.process_color(color_settings.get("gutterForeground", fground))[0]
        gfground_sim = self.process_color(color_settings.get("gutterForeground", fground_sim))[1]

        self.special_colors["foreground"] = {'color': fground, 'color_simulated': fground_sim}
        self.special_colors["background"] = {'color': bground, 'color_simulated': bground_sim}
        self.special_colors["selectionForeground"] = {'color': sfground, 'color_simulated': sfground_sim}
        self.special_colors["selection"] = {'color': sbground, 'color_simulated': sbground_sim}
        self.special_colors["gutter"] = {'color': gbground, 'color_simulated': gbground_sim}
        self.special_colors["gutterForeground"] = {'color': gfground, 'color_simulated': gfground_sim}

        self.colors = {}
        if self.legacy:
            # Create scope colors mapping from color scheme file
            for item in self.scheme_obj["settings"]:
                name = item.get('name', '')
                scope = item.get('scope', None)
                color = None
                bgcolor = None
                style = []
                if 'settings' in item and scope is not None:
                    color = item['settings'].get('foreground', None)
                    bgcolor = item['settings'].get('background', None)
                    if 'fontStyle' in item['settings']:
                        for s in item['settings']['fontStyle'].split(' '):
                            if s == "bold" or s == "italic":  # or s == "underline":
                                style.append(s)

                if scope is not None:
                    self.add_entry(name, scope, color, bgcolor, style)

        else:
            # Create scope colors mapping from color scheme file
            for item in self.scheme_obj["rules"]:
                name = item.get('name', '')
                scope = item.get('scope', None)
                color = None
                bgcolor = None
                style = []
                if scope is not None:
                    color = item.get('foreground', None)
                    if color is not None:
                        color = translate_color(COLOR_RE.match(color.strip()), self.variables, {})
                    bgcolor = item.get('background', None)
                    if bgcolor is not None:
                        bgcolor = translate_color(COLOR_RE.match(bgcolor.strip()), self.variables, {})
                    if item.get('bold', False) is True:
                        style.append('bold')
                    if item.get('italic', False) is True:
                        style.append('italic')

                if scope is not None:
                    self.add_entry(name, scope, color, bgcolor, style)

    def add_entry(self, name, scope, color, bgcolor, style):
        """Add color entry."""

        if color is not None:
            fg, fg_sim = self.process_color(color)
        else:
            fg, fg_sim = None, None
        if bgcolor is not None:
            bg, bg_sim = self.process_color(bgcolor)
        else:
            bg, bg_sim = None, None
        self.colors[scope] = {
            "name": name,
            "scope": scope,
            "color": fg,
            "color_simulated": fg_sim,
            "bgcolor": bg,
            "bgcolor_simulated": bg_sim,
            "style": style
        }

    def process_color(self, color, simple_strip=False):
        """
        Strip transparency from the color value.

        Transparency can be stripped in one of two ways:
            - Simply mask off the alpha channel.
            - Apply the alpha channel to the color essential getting the color seen by the eye.
        """

        if color is None or color.strip() == "":
            return None, None

        if not color.startswith('#'):
            if self.legacy:
                color = x11colors.name2hex(color)
                if color is None:
                    return None, None
            else:
                return None, None

        rgba = RGBA(color.replace(" ", ""))
        if not simple_strip:
            bground = self.special_colors['background']['color_simulated']
            rgba.apply_alpha(bground if bground != "" else "#FFFFFF")

        return color, rgba.get_rgb()

    def get_special_color(self, name, simulate_transparency=False):
        """
        Get the core colors (background, foreground) for the view and gutter.

        Get the visible look of the color by simulated transparency if requrested.
        """

        return self.special_colors.get(name, {}).get('color_simulated' if simulate_transparency else 'color')

    def get_scheme_obj(self):
        """Get the plist file used during the process."""

        return self.scheme_obj

    def get_scheme_file(self):
        """Get the scheme file used during the process."""

        return self.scheme_file

    def guess_color(self, scope_key, selected=False, explicit_background=False):
        """
        Guess the colors and style of the text for the given Sublime scope.

        By default, we always fall back to the schemes default background,
        but if desired, we can show that no background was explicitly
        specified by returning None.  This is done by enabling explicit_background.
        This will only show backgrounds that were explicitly specified.

        This was orginially introduced for mdpopups so that it would
        know when a background was not needed.  This allowed mdpopups
        to generate syntax highlighted code that could be overlayed on
        block elements with different background colors and allow that
        background would show through.
        """

        color = self.special_colors['foreground']['color']
        color_sim = self.special_colors['foreground']['color_simulated']
        bgcolor = self.special_colors['background']['color'] if not explicit_background else None
        bgcolor_sim = self.special_colors['background']['color_simulated'] if not explicit_background else None
        style = set([])
        color_selector = SchemeSelectors("foreground", "foreground")
        bg_selector = SchemeSelectors("background", "background")
        style_selectors = {"bold": SchemeSelectors("", ""), "italic": SchemeSelectors("", "")}
        if scope_key in self.matched:
            color = self.matched[scope_key]["color"]
            color_sim = self.matched[scope_key]["color_simulated"]
            style = self.matched[scope_key]["style"]
            bgcolor = self.matched[scope_key]["bgcolor"]
            bgcolor_sim = self.matched[scope_key]["bgcolor_simulated"]
            selectors = self.matched[scope_key]["selectors"]
            color_selector = selectors["color"]
            bg_selector = selectors["background"]
            style_selectors = selectors["style"]
        else:
            best_match_bg = 0
            best_match_fg = 0
            best_match_style = 0
            for key in self.colors:
                match = sublime.score_selector(scope_key, key)
                if self.colors[key]["color"] is not None and match > best_match_fg:
                    best_match_fg = match
                    color = self.colors[key]["color"]
                    color_sim = self.colors[key]["color_simulated"]
                    color_selector = SchemeSelectors(self.colors[key]["name"], self.colors[key]["scope"])
                if self.colors[key]["style"] is not None and match > best_match_style:
                    best_match_style = match
                    for s in self.colors[key]["style"]:
                        style.add(s)
                        if s == "bold":
                            style_selectors["bold"] = SchemeSelectors(
                                self.colors[key]["name"], self.colors[key]["scope"]
                            )
                        elif s == "italic":
                            style_selectors["italic"] = SchemeSelectors(
                                self.colors[key]["name"], self.colors[key]["scope"]
                            )
                if self.colors[key]["bgcolor"] is not None and match > best_match_bg:
                    best_match_bg = match
                    bgcolor = self.colors[key]["bgcolor"]
                    bgcolor_sim = self.colors[key]["bgcolor_simulated"]
                    bg_selector = SchemeSelectors(self.colors[key]["name"], self.colors[key]["scope"])

            if len(style) == 0:
                style = ""
            else:
                style = ' '.join(style)

            self.matched[scope_key] = {
                "color": color,
                "bgcolor": bgcolor,
                "color_simulated": color_sim,
                "bgcolor_simulated": bgcolor_sim,
                "style": style,
                "selectors": {
                    "color": color_selector,
                    "background": bg_selector,
                    "style": style_selectors
                }
            }

        if selected:
            if self.special_colors['selectionForeground']['color']:
                color = self.special_colors['selectionForeground']['color']
                color_sim = color = self.special_colors['selectionForeground']['color_simulated']
                style = ''
            if self.special_colors['selection']['color']:
                bgcolor = self.special_colors['selection']['color']
                bgcolor_sim = color = self.special_colors['selection']['color_simulated']

        return SchemeColors(
            color, color_sim, bgcolor, bgcolor_sim, style,
            color_selector, bg_selector, style_selectors
        )
