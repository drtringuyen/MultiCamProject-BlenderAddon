# MultiCamProject

Project camera background photos onto meshes with blended UVs and vertex-colour weights

## Installation

A Blender Extension (Blender 4.2+ extension format; `manifest.toml` sets the minimum version).

### With automatic updates
1. `Edit > Preferences > Get Extensions > Repositories > + > Add Remote Repository`
2. URL: the repository's `index.json` (see Publishing), tick **Check for Updates on Startup**
3. Install MultiCamProject from that repository

### From a file
1. Download `MultiCamProject-extension.zip`
2. Drag it into Blender (or `Get Extensions > ⌄ > Install from Disk`)

## Getting Started

### Development Setup
```bash
# Install dependencies (if any)
pip install -r requirements.txt

# Build and reload in Blender
python install.py
```

### Project Structure
```
MultiCamProject/
├── addons/MultiCamProject/
│   ├── __init__.py              # Addon metadata & registration
│   ├── properties.py            # Global properties
│   ├── panels.py                # UI panels
│   ├── infos.py                 # Info panel & debugging
│   ├── module_*_operators.py    # Module operators
│   ├── module_*_ui.py           # Module UI
│   └── module_*_utils.py        # Module utilities
├── manifest.toml                # Extension format metadata
├── build_extension.py           # Build the extension zip
├── install.py                   # Installation script
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

## Modules

This addon is organized into modules. Each module is self-contained:
- `module_example_operators.py` - Operators
- `module_example_ui.py` - UI elements
- `module_example_utils.py` - Utility functions

Enable/disable modules in `addons/MultiCamProject/__init__.py`:
```python
MODULES = {
    "example": True,  # Set to False to disable
}
```

## Global Properties

All global properties are defined in `properties.py`. Access them:
```python
context.scene.multicamproject_props.debug_mode
```

## Debug Mode

- Enable debug mode in the Info panel (N-Panel → MultiCamProject → Debug button)
- Shows extra labels marked with `text_ctxt="extra-info-label"`
- Displays build time and version information

## Publishing

1. Raise `version` in `manifest.toml` - Blender only offers an update for a higher version
2. Build the zip:
```bash
python build_extension.py
# Creates: MultiCamProject-extension.zip
```
3. Put it into the extension repository folder and regenerate its `index.json`:
```bash
blender --command extension server-generate --repo-dir=<repository folder>
```
4. Publish that folder (e.g. GitHub Pages); users' Blender finds the new version on startup

## Development Notes

- Code files longer than 500 lines should be split into modules
- All UI elements subscribe to the main panel in `panels.py`
- Use `extra-info-label` markup for debug-only text
- Run `install.py` after editing to auto-reload in Blender

## License

[Add your license here]

## Author



---

Generated with [Blender Addon Init](https://github.com/yourusername/blender-addon-init)
