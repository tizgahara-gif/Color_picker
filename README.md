# Global Color Picker for Blender 5.1

Global Color Picker is a Blender add-on that samples the color under the operating-system mouse cursor, including pixels outside the Blender window. It is intended for workflows where reference colors live in Chrome, image viewers, PureRef, other applications, or another monitor.

## Target environment

- Blender 5.1
- Windows-first implementation
- Multi-monitor desktops, including monitors positioned at negative virtual-screen coordinates
- Internal structure prepared for future macOS/Linux sampling backends

## Installation

1. Download or copy `global_color_picker.py`.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk...** and select `global_color_picker.py`.
4. Enable **Global Color Picker**.
5. Open the 3D Viewport Sidebar and select the **Global Color Picker** tab.

## Usage

1. Select an object with an active material if you want the picked color to be applied immediately.
2. Press **Start Global Pick** in **3D Viewport > Sidebar > Global Color Picker**.
3. Move the mouse anywhere on the OS desktop, including outside Blender and across monitors.
4. Confirm with **left click** or **Enter**. Confirmation works from inside Blender and, on Windows, from outside Blender through `GetAsyncKeyState`.
5. Cancel with **Esc** or **right click**.

When confirmed, the add-on applies the color to the active material's **Base Color** and `diffuse_color`. If no active material exists, the color is kept in the add-on history instead.

## Panel controls

- **Start Global Pick**: Starts the modal desktop color picker.
- **Apply to Active Material**: Applies the current preview color to the active material.
- **Copy HEX**: Copies the current HEX value to Blender's clipboard.
- **Clear History**: Removes saved history colors.
- **History colors**: Click a history entry to restore and reapply it.

## Displayed values

During picking, the panel updates in real time with:

- RGB 0-255
- RGB 0.0-1.0
- HSV
- HEX
- Color preview
- Up to 10 confirmed history colors

## Technical notes

- The picker is implemented as a Blender modal operator.
- On Windows, desktop sampling uses `ctypes` calls to `GetCursorPos`, `GetDC`, `GetPixel`, and `ReleaseDC`.
- The Windows backend asks for DPI awareness to reduce coordinate offsets on scaled displays.
- Pixel values are treated as SDR 8-bit screen samples and converted to clamped 0.0-1.0 Blender floats through isolated conversion functions, so HDR or color-management improvements can be added later.
- Sampling failures keep the previous color and report a warning instead of raising an exception that could stop Blender.

## Limitations and known issues

- OS screen capture cannot be implemented with Blender's standard Python API alone.
- The current global sampling backend contains Windows API dependent code.
- HDR monitor real display values are not fully captured; colors are handled as normal screen pixels and clamped to SDR-style 8-bit RGB.
- Some protected windows, DRM video surfaces, secure desktops, or applications running at a different administrator privilege level may not allow color sampling.
- macOS and Linux are not implemented yet; the add-on will show a warning instead of sampling the desktop on those platforms.
- Blender UI redraw timing can affect how quickly the Sidebar visibly refreshes, although sampling continues on the modal timer.
