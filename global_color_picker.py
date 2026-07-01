# SPDX-License-Identifier: MIT
"""Global Color Picker add-on for Blender.

This module implements a modal operator that samples pixels from the operating
system desktop instead of only from Blender's own windows.  The Windows backend
uses ctypes so the add-on can run without third-party Python packages bundled
into Blender.
"""

from __future__ import annotations

bl_info = {
    "name": "Global Color Picker",
    "author": "OpenAI",
    "version": (1, 0, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Global Color Picker",
    "description": "Pick colors from the full OS desktop and apply them to the active material.",
    "category": "3D View",
}

import colorsys
import ctypes
import ctypes.wintypes
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatVectorProperty,
    IntVectorProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

RGB255 = Tuple[int, int, int]
RGBF = Tuple[float, float, float]


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp a numeric value before it is passed into Blender color data."""
    return max(minimum, min(maximum, value))


def rgb255_to_float(rgb: RGB255) -> RGBF:
    """Convert SDR 8-bit screen RGB to clamped Blender float RGB.

    The conversion is intentionally isolated so future HDR/color-management
    handling can be added without changing the picker or UI code.
    """
    return tuple(clamp(channel / 255.0) for channel in rgb)  # type: ignore[return-value]


def rgb_float_to_255(rgb: RGBF) -> RGB255:
    """Convert Blender float RGB to display-friendly 8-bit RGB."""
    return tuple(int(round(clamp(channel) * 255.0)) for channel in rgb)  # type: ignore[return-value]


def rgb255_to_hex(rgb: RGB255) -> str:
    """Return a web-style HEX color string."""
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def rgb_float_to_hsv(rgb: RGBF) -> RGBF:
    """Convert clamped RGB floats to HSV floats."""
    return colorsys.rgb_to_hsv(*(clamp(channel) for channel in rgb))


def set_picker_color(props: "GlobalColorPickerProperties", rgb255: RGB255) -> None:
    """Update all derived UI properties from one sampled RGB value."""
    rgb255 = tuple(max(0, min(255, int(channel))) for channel in rgb255)  # type: ignore[assignment]
    rgbf = rgb255_to_float(rgb255)
    hsv = rgb_float_to_hsv(rgbf)
    props.current_rgb_255 = rgb255
    props.current_rgb_float = rgbf
    props.current_hsv = hsv
    props.current_hex = rgb255_to_hex(rgb255)
    props.preview_color = (rgbf[0], rgbf[1], rgbf[2], 1.0)


def add_history_color(props: "GlobalColorPickerProperties", rgbf: RGBF) -> None:
    """Store a confirmed color, keeping the newest ten entries."""
    rgbf = tuple(clamp(channel) for channel in rgbf)  # type: ignore[assignment]
    rgb255 = rgb_float_to_255(rgbf)
    hex_value = rgb255_to_hex(rgb255)

    # Avoid duplicating the same color at the front of the history.
    if props.color_history and props.color_history[0].hex_value == hex_value:
        return

    item = props.color_history.add()
    item.color = (rgbf[0], rgbf[1], rgbf[2], 1.0)
    item.hex_value = hex_value
    props.color_history.move(len(props.color_history) - 1, 0)
    while len(props.color_history) > 10:
        props.color_history.remove(len(props.color_history) - 1)


@dataclass
class SampleResult:
    """Result returned by a screen sampling backend."""

    rgb: Optional[RGB255]
    warning: str = ""


class ScreenSampler:
    """Cross-platform boundary for OS screen pixel sampling."""

    def sample(self) -> SampleResult:
        raise NotImplementedError

    def is_left_pressed(self) -> bool:
        return False

    def is_right_pressed(self) -> bool:
        return False

    def is_enter_pressed(self) -> bool:
        return False

    def close(self) -> None:
        """Release native resources if a backend owns any."""


class UnsupportedScreenSampler(ScreenSampler):
    """Fallback backend for platforms that are not implemented yet."""

    def sample(self) -> SampleResult:
        return SampleResult(None, "Global screen sampling is currently implemented only on Windows.")


class WindowsScreenSampler(ScreenSampler):
    """Windows implementation using GetCursorPos/GetDC/GetPixel/ReleaseDC."""

    VK_LBUTTON = 0x01
    VK_RBUTTON = 0x02
    VK_RETURN = 0x0D

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self._set_dpi_awareness()

    def _set_dpi_awareness(self) -> None:
        """Ask Windows for physical cursor coordinates on scaled displays."""
        try:
            # Per-monitor v2 DPI awareness where available.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                self.user32.SetProcessDPIAware()
            except Exception:
                pass

    def sample(self) -> SampleResult:
        point = ctypes.wintypes.POINT()
        try:
            if not self.user32.GetCursorPos(ctypes.byref(point)):
                return SampleResult(None, "GetCursorPos failed; keeping previous color.")
            hdc = self.user32.GetDC(None)
            if not hdc:
                return SampleResult(None, "GetDC failed; keeping previous color.")
            try:
                pixel = self.gdi32.GetPixel(hdc, point.x, point.y)
                if pixel == 0xFFFFFFFF:
                    return SampleResult(None, "GetPixel failed; keeping previous color.")
                red = pixel & 0xFF
                green = (pixel >> 8) & 0xFF
                blue = (pixel >> 16) & 0xFF
                return SampleResult((red, green, blue))
            finally:
                self.user32.ReleaseDC(None, hdc)
        except Exception as exc:
            return SampleResult(None, f"Screen sampling failed: {exc}")

    def _pressed(self, virtual_key: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(virtual_key) & 0x8000)

    def is_left_pressed(self) -> bool:
        return self._pressed(self.VK_LBUTTON)

    def is_right_pressed(self) -> bool:
        return self._pressed(self.VK_RBUTTON)

    def is_enter_pressed(self) -> bool:
        return self._pressed(self.VK_RETURN)


def create_screen_sampler() -> ScreenSampler:
    """Create the best available sampler for the current operating system."""
    if sys.platform.startswith("win"):
        return WindowsScreenSampler()
    return UnsupportedScreenSampler()


def active_material(context: bpy.types.Context) -> Optional[bpy.types.Material]:
    """Return the active object's active material, if one exists."""
    obj = context.object
    if obj and getattr(obj, "active_material", None):
        return obj.active_material
    return None


def apply_color_to_material(context: bpy.types.Context, rgbf: RGBF) -> bool:
    """Apply RGB to the active material's Base Color or diffuse color."""
    mat = active_material(context)
    if mat is None:
        return False

    rgba = (clamp(rgbf[0]), clamp(rgbf[1]), clamp(rgbf[2]), 1.0)
    mat.diffuse_color = rgba
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.bl_idname == "ShaderNodeBsdfPrincipled":
                socket = node.inputs.get("Base Color")
                if socket:
                    socket.default_value = rgba
                    break
    return True


class GlobalColorHistoryItem(PropertyGroup):
    """One confirmed color in the add-on history."""

    color: FloatVectorProperty(name="Color", size=4, subtype="COLOR", min=0.0, max=1.0, default=(1.0, 1.0, 1.0, 1.0))
    hex_value: StringProperty(name="HEX", default="#FFFFFF")


class GlobalColorPickerProperties(PropertyGroup):
    """Scene-level state displayed by the Global Color Picker panel."""

    is_picking: BoolProperty(name="Picking", default=False)
    current_rgb_float: FloatVectorProperty(name="RGB Float", size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0))
    current_rgb_255: IntVectorProperty(name="RGB 0-255", size=3, min=0, max=255, default=(255, 255, 255))
    current_hsv: FloatVectorProperty(name="HSV", size=3, min=0.0, max=1.0, default=(0.0, 0.0, 1.0))
    current_hex: StringProperty(name="HEX", default="#FFFFFF")
    preview_color: FloatVectorProperty(name="Preview", size=4, subtype="COLOR", min=0.0, max=1.0, default=(1.0, 1.0, 1.0, 1.0))
    last_warning: StringProperty(name="Last Warning", default="")
    color_history: CollectionProperty(type=GlobalColorHistoryItem)


class GLOBALCOLORPICKER_OT_start(Operator):
    """Sample the global desktop until the user confirms or cancels."""

    bl_idname = "global_color_picker.start"
    bl_label = "Start Global Pick"
    bl_options = {"REGISTER"}

    _timer = None
    _sampler: Optional[ScreenSampler] = None
    _left_was_down = False
    _right_was_down = False
    _enter_was_down = False

    def execute(self, context: bpy.types.Context):
        props = context.scene.global_color_picker
        if props.is_picking:
            self.report({"WARNING"}, "Global Color Picker is already running.")
            return {"CANCELLED"}
        props.is_picking = True
        props.last_warning = ""
        self._sampler = create_screen_sampler()
        self._timer = context.window_manager.event_timer_add(0.04, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event):
        props = context.scene.global_color_picker
        try:
            if event.type == "ESC" or event.type == "RIGHTMOUSE":
                return self._finish(context, cancelled=True)
            if event.type in {"RET", "NUMPAD_ENTER", "LEFTMOUSE"} and event.value == "PRESS":
                return self._finish(context, cancelled=False)
            if event.type == "TIMER":
                self._sample_and_update(context)
                if self._outside_confirm_pressed():
                    return self._finish(context, cancelled=False)
                if self._outside_cancel_pressed():
                    return self._finish(context, cancelled=True)
        except Exception as exc:
            props.last_warning = f"Picker error: {exc}"
            self.report({"WARNING"}, props.last_warning)
            return self._finish(context, cancelled=True)
        return {"PASS_THROUGH"}

    def _sample_and_update(self, context: bpy.types.Context) -> None:
        props = context.scene.global_color_picker
        if not self._sampler:
            return
        result = self._sampler.sample()
        if result.rgb is not None:
            set_picker_color(props, result.rgb)
            props.last_warning = ""
        elif result.warning and result.warning != props.last_warning:
            props.last_warning = result.warning
            self.report({"WARNING"}, result.warning)

    def _outside_confirm_pressed(self) -> bool:
        if not self._sampler:
            return False
        left_down = self._sampler.is_left_pressed()
        enter_down = self._sampler.is_enter_pressed()
        confirm = (left_down and not self._left_was_down) or (enter_down and not self._enter_was_down)
        self._left_was_down = left_down
        self._enter_was_down = enter_down
        return confirm

    def _outside_cancel_pressed(self) -> bool:
        if not self._sampler:
            return False
        right_down = self._sampler.is_right_pressed()
        cancel = right_down and not self._right_was_down
        self._right_was_down = right_down
        return cancel

    def _finish(self, context: bpy.types.Context, cancelled: bool):
        props = context.scene.global_color_picker
        if not cancelled:
            rgbf = tuple(props.current_rgb_float)  # type: ignore[assignment]
            applied = apply_color_to_material(context, rgbf)
            add_history_color(props, rgbf)
            if not applied:
                self.report({"INFO"}, "No active material; color was saved to history.")
        self._cleanup(context)
        return {"CANCELLED" if cancelled else "FINISHED"}

    def _cleanup(self, context: bpy.types.Context) -> None:
        props = context.scene.global_color_picker
        props.is_picking = False
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._sampler is not None:
            self._sampler.close()
            self._sampler = None

    def cancel(self, context: bpy.types.Context) -> None:
        self._cleanup(context)


class GLOBALCOLORPICKER_OT_apply_current(Operator):
    """Apply the current sampled color to the active material."""

    bl_idname = "global_color_picker.apply_current"
    bl_label = "Apply to Active Material"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context):
        props = context.scene.global_color_picker
        rgbf = tuple(props.current_rgb_float)  # type: ignore[assignment]
        if apply_color_to_material(context, rgbf):
            add_history_color(props, rgbf)
            return {"FINISHED"}
        add_history_color(props, rgbf)
        self.report({"WARNING"}, "No active material; color was saved to history.")
        return {"CANCELLED"}


class GLOBALCOLORPICKER_OT_copy_hex(Operator):
    """Copy the current HEX value to Blender's clipboard."""

    bl_idname = "global_color_picker.copy_hex"
    bl_label = "Copy HEX"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context):
        context.window_manager.clipboard = context.scene.global_color_picker.current_hex
        return {"FINISHED"}


class GLOBALCOLORPICKER_OT_clear_history(Operator):
    """Clear confirmed color history."""

    bl_idname = "global_color_picker.clear_history"
    bl_label = "Clear History"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context):
        context.scene.global_color_picker.color_history.clear()
        return {"FINISHED"}


class GLOBALCOLORPICKER_OT_apply_history(Operator):
    """Apply one history color to the active material and current preview."""

    bl_idname = "global_color_picker.apply_history"
    bl_label = "Apply History Color"
    bl_options = {"REGISTER"}

    index: bpy.props.IntProperty(default=0)

    def execute(self, context: bpy.types.Context):
        props = context.scene.global_color_picker
        if self.index < 0 or self.index >= len(props.color_history):
            return {"CANCELLED"}
        item = props.color_history[self.index]
        rgbf = (item.color[0], item.color[1], item.color[2])
        set_picker_color(props, rgb_float_to_255(rgbf))
        if not apply_color_to_material(context, rgbf):
            self.report({"WARNING"}, "No active material; color kept as current color.")
            return {"CANCELLED"}
        return {"FINISHED"}


class GLOBALCOLORPICKER_PT_panel(Panel):
    """3D Viewport Sidebar panel for the picker."""

    bl_label = "Global Color Picker"
    bl_idname = "GLOBALCOLORPICKER_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Global Color Picker"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.global_color_picker

        row = layout.row()
        row.enabled = not props.is_picking
        row.operator("global_color_picker.start", icon="EYEDROPPER")
        if props.is_picking:
            layout.label(text="Picking... Left/Enter confirms, Right/Esc cancels", icon="EVENT_MOUSEMOVE")

        layout.prop(props, "preview_color", text="Preview")
        layout.label(text=f"RGB 0-255: {tuple(props.current_rgb_255)}")
        layout.label(text="RGB 0.0-1.0: ({:.4f}, {:.4f}, {:.4f})".format(*props.current_rgb_float))
        layout.label(text="HSV: ({:.4f}, {:.4f}, {:.4f})".format(*props.current_hsv))
        layout.label(text=f"HEX: {props.current_hex}")
        if props.last_warning:
            layout.label(text=props.last_warning, icon="ERROR")

        actions = layout.row(align=True)
        actions.operator("global_color_picker.apply_current", icon="MATERIAL")
        actions.operator("global_color_picker.copy_hex", icon="COPYDOWN")
        layout.operator("global_color_picker.clear_history", icon="TRASH")

        layout.separator()
        layout.label(text="History")
        for index, item in enumerate(props.color_history):
            row = layout.row(align=True)
            row.prop(item, "color", text="")
            op = row.operator("global_color_picker.apply_history", text=item.hex_value)
            op.index = index


classes = (
    GlobalColorHistoryItem,
    GlobalColorPickerProperties,
    GLOBALCOLORPICKER_OT_start,
    GLOBALCOLORPICKER_OT_apply_current,
    GLOBALCOLORPICKER_OT_copy_hex,
    GLOBALCOLORPICKER_OT_clear_history,
    GLOBALCOLORPICKER_OT_apply_history,
    GLOBALCOLORPICKER_PT_panel,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.global_color_picker = bpy.props.PointerProperty(type=GlobalColorPickerProperties)


def unregister() -> None:
    # Prevent stale state from indicating that a modal picker is still active
    # after the add-on is disabled.  Blender removes modal timers when their
    # operators are cancelled during unregister/shutdown.
    for scene in bpy.data.scenes:
        if hasattr(scene, "global_color_picker"):
            scene.global_color_picker.is_picking = False
    del bpy.types.Scene.global_color_picker
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
