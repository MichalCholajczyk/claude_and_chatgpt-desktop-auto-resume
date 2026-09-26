"""Draw assets/chatgpt.png and assets/claude.png, the launcher's ChatGPT and Claude tiles,
from the icons of the apps installed on this PC (their Microsoft Store packages).

The pictures ship with the repository, so every copy of Auto-Resume shows them, with or
without the apps and with or without Pillow. Run this again only when an app changes its
icon. Needs Pillow:  py tools/make_app_icons.py

The logos are OpenAI's and Anthropic's; the launcher uses them only to name the app each
tile starts.
"""
import os
import winreg

from PIL import Image

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
SIZE = 300           # px, like brain.png: the launcher shows 150 px (halved without Pillow)

PACKAGES_KEY = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
                r"\CurrentVersion\AppModel\Repository\Packages")
# Store package name prefixes, the artwork inside them (best first) and how much of the
# picture the logo fills. ChatGPT's knot is line art and fills it; Claude's solid square
# reads larger, so it gets three quarters to sit level with the knot and the brain.
ICONS = {
    "chatgpt": (("OpenAI.Codex_", "OpenAI.ChatGPT"),
                (r"assets\Square44x44Logo.targetsize-256_altform-unplated.png",
                 r"assets\Square150x150Logo.scale-200.png"), 1.0),
    "claude": (("Claude_", "AnthropicPBC.Claude"),
               (r"assets\Square44x44Logo.targetsize-256.png",
                r"assets\Square150x150Logo.scale-200.png"), 0.75),
}


def _version(package_name):
    """(26, 915, 4065, 0) from "OpenAI.Codex_26.915.4065.0_x64__2p2nqsd0c76g0"."""
    try:
        return tuple(int(part) for part in package_name.split("_")[1].split("."))
    except (IndexError, ValueError):
        return ()


def package_folders(prefixes):
    """Install folders of the Store packages whose names start with a prefix, newest first."""
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PACKAGES_KEY) as key:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                if not name.startswith(prefixes):
                    continue
                try:
                    with winreg.OpenKey(key, name) as package:
                        folder = winreg.QueryValueEx(package, "PackageRootFolder")[0]
                except OSError:
                    continue
                found.append((_version(name), folder))
    except OSError:
        return []
    return [folder for _, folder in sorted(found, reverse=True)]


def source(key):
    """Path of the installed app's own artwork, or None."""
    prefixes, names, _ = ICONS[key]
    for folder in package_folders(prefixes):
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return None


def draw(path, fill):
    """The logo without its transparent margin, `fill` of a SIZE x SIZE picture, centred.
    A plain 8-bit RGBA PNG, which Tk reads by itself when Pillow is missing."""
    logo = Image.open(path).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())
    side = round(SIZE * fill)
    logo = logo.resize((side, round(side * logo.height / logo.width)), Image.LANCZOS)
    picture = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    picture.alpha_composite(logo, ((SIZE - logo.width) // 2, (SIZE - logo.height) // 2))
    return picture


def main():
    for key, (_, _, fill) in ICONS.items():
        path = source(key)
        if path is None:
            print(f"{key}: the app is not installed here; {key}.png left as it is")
            continue
        target = os.path.join(ASSETS, key + ".png")
        draw(path, fill).save(target, optimize=True)
        print(f"{key}: {target}  (from {path})")


if __name__ == "__main__":
    main()
