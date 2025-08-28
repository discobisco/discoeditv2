
# gui_dropdown_integration_snippet.py
# Example of wiring GearLookups into a Tkinter Combobox.
import os
from gear_loader import GearLookups

def demo_dump_keys(project_root: str):
    reg = GearLookups(project_root).load()
    return {
        "available_keys": reg.available(),
        "first_shoe_home": reg.get("shoes_home")[:5],
        "first_vendor": reg.get("shoe_vendor_locked")[:5],
        "pos": reg.get("positions")[:6],
    }

if __name__ == "__main__":
    root = os.path.dirname(__file__)
    print(demo_dump_keys(root))
