NPAAAD/SAFDZ–CLUP Harmonizer v2.7

FIRST RUN
1. Extract the ZIP to a normal folder (do not run it while still inside the ZIP).
2. Double-click INSTALL_AND_RUN.bat.
3. Wait for GIS packages to install.
4. The launcher tests the app before opening the browser.
5. The website will open automatically at 127.0.0.1, usually port 5055.

NEXT RUNS
- Double-click RUN_WEBSITE.bat.

IF THE SERVER DOES NOT START
- The launcher now prints the complete Python error on screen.
- A copy is also saved as server.log in the same folder.
- Send server.log or a screenshot of the error to ChatGPT.

INPUTS
- Existing/Actual Land Use (ZIP shapefile)
- NPAAAD (ZIP shapefile)
- SAFDZ (ZIP shapefile)
- Land Cover 2025 (optional in v1.1)

OUTPUTS
- HARMONIZATION_RESULTS.zip
- Excel summary: SAFDZ table first, NPAAAD table second
- Union shapefile
- QA/QC JSON

v2.0 FIXES
- Preserves selected ELU/NPAAAD/SAFDZ fields using unique internal names before overlay.
- Fixes KeyError: 'SAFDZ' during 80% summary/area stage.
- Validates selected classification fields before expensive processing.
- Preserves v1.8 performance optimizations and live progress indicator.

PATCH 1.9.1: Launcher avoids browser-blocked ports 5060/5061 (ERR_UNSAFE_PORT).


v2.1 fix: robust empty-overlay handling. NPAAAD/SAFDZ classification columns are retained even when a clipped layer has no intersecting polygons; misleading field-loss failures removed.


v2.2 performance engine: replaces full union overlays with spatial-indexed intersection + base remainder splitting for NPAAAD, SAFDZ, and Land Cover. This avoids generating unnecessary outside pieces and reduces polygon explosion while preserving exact boundaries (no simplification). Launcher/health version handshake fixed to 2.2.


v2.7 FIX: ELU Description Mapping is now rendered directly inside the ELU Layer Settings card. Each unique selected-field value has an AGR / BU / Other-Ignore dropdown.


v2.7: Official NPAAAD/SAFDZ header descriptions; optional Poultry shapefile upload, ELU clipping, POULTRY_HA in union output, Poultry Area (ha) in NPAAAD summary, and POULTRY_CLIPPED.zip export.


v2.7: Desktop shortcut support. INSTALL_AND_RUN.bat automatically creates a Windows desktop shortcut using harmonizer.ico. Double-clicking the shortcut starts the local server hidden and opens the website automatically. CREATE_DESKTOP_SHORTCUT.bat can recreate the shortcut anytime.


v2.7: Login protection. Default username ALRMS; default password ALRMS001. Password is stored as a one-way hash. For public/GitHub deployment, override credentials and session secret using environment variables.
