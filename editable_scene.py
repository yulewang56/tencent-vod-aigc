"""Structured editable-scene reconstruction helpers.

The cloud vision model estimates room layout and object bounds. This module
validates that estimate and packages deterministic whitebox geometry without
requiring a local ML or glTF dependency.
"""

import json
import math
import os
import re
import struct
import uuid


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EVIDENCE_VALUES = {"observed", "inferred", "assumed"}
_STRUCTURAL_CATEGORIES = {"architecture", "wall", "floor", "ceiling"}
_OPENING_CATEGORIES = {"door", "window", "opening"}
_MOUNTED_CATEGORIES = _OPENING_CATEGORIES | {"board"}
_WALL_VALUES = {"left", "right", "back", "front"}
_SEMANTIC_COLORS = {
    "architecture": "#9aa4af",
    "board": "#426457",
    "door": "#c28a52",
    "window": "#67a8c7",
    "furniture": "#cf8b45",
    "table": "#c47c3c",
    "chair": "#df9d55",
    "storage": "#a97b55",
    "appliance": "#6b8f9e",
    "prop": "#8b72b7",
    "unknown": "#7d8791",
}


def build_reconstruction_prompt(scene_type, known_room_width_m, max_objects,
                                additional_guidance, view_count):
    """Build the strict JSON instruction sent with the reference views."""
    scale_instruction = (
        f"The room width is known to be exactly {known_room_width_m:.3f} meters."
        if known_room_width_m > 0
        else "Metric scale is unknown; estimate it from doors, furniture, and human-scale priors."
    )
    guidance = additional_guidance.strip() or "No additional guidance."
    return f"""
You are a film-previsualization spatial analyst. Analyze {view_count} reference
image(s) of the same {scene_type} and return a conservative editable whitebox
layout. {scale_instruction}

Coordinate system:
- X points image-right, Y points up, Z points away from the primary camera.
- Floor is Y=0. The room is centered at X=0, Z=0.
- Floor-standing object position is the center of its footprint with Y=0.
- A wall-mounted object position uses the lower-edge center and may have Y>0.
- Size is the FULL physical bounding box [width, total height, depth] in meters.
  For tables and chairs, total height includes legs and the chair back; never
  report only tabletop, seat, or panel thickness as total height.
- Do not include people, pictures of objects, reflections, text, or tiny clutter.
- Use one axis-aligned or yaw-rotated box proxy per physical object.
- Mark hidden geometry as inferred. Do not invent decorative detail.
- Keep every object inside or directly against the room bounds.
- For door, window, and board objects, set wall to left/right/back/front.
  Objects visible on the same physical wall must use the same wall value.
- Return at most {max_objects} objects.

Use these exact JSON fields without copying any preset scene dimensions:
- room: width, depth, height, confidence (all numeric)
- camera: position (3 numbers), target (3 numbers), fov_degrees
- objects: array of objects containing name, category, position (3 numbers),
  size (3 numbers), yaw_degrees, confidence, evidence, movable, and wall
- category must be one of:
  board, door, window, table, chair, storage, appliance, furniture, prop, unknown
- wall must be left/right/back/front for mounted objects and an empty string otherwise

Before replying, verify:
- classroom tables have a plausible full height, not tabletop thickness
- classroom chairs include seat, legs, and back in their full height
- doors start at floor level and use plausible human-scale dimensions
- wall fixtures are thin and share a consistent wall plane
- room and camera numbers were inferred from the image, not copied from this instruction

Return only one valid JSON object with those fields and no Markdown.

Additional user guidance: {guidance}
""".strip()


def extract_json_object(text):
    """Extract a single JSON object from a model response."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("混元视觉没有返回场景 JSON")
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("混元视觉响应中找不到 JSON 对象") from None
        try:
            value = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as error:
            raise ValueError(
                f"混元视觉返回的场景 JSON 无法解析：第 {error.lineno} 行 {error.msg}"
            ) from None
    if not isinstance(value, dict):
        raise ValueError("混元视觉返回值必须是 JSON 对象")
    return value


def _number(value, field, minimum, maximum):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是数字") from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field} 必须在 {minimum:g}-{maximum:g} 之间")
    return result


def _vec3(value, field, minimum, maximum):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} 必须是 3 个数字")
    return [
        _number(component, f"{field}[{index}]", minimum, maximum)
        for index, component in enumerate(value)
    ]


def _safe_name(value, fallback):
    name = " ".join(str(value or fallback).split())
    return name[:80] or fallback


def _category(value):
    category = str(value or "unknown").strip().lower()
    aliases = {
        "cabinet": "storage",
        "shelf": "storage",
        "sofa": "furniture",
        "bed": "furniture",
        "desk": "table",
        "stool": "chair",
        "entrance": "door",
        "blackboard": "board",
        "chalkboard": "board",
        "whiteboard": "board",
    }
    category = aliases.get(category, category)
    return category if category in _SEMANTIC_COLORS else "unknown"


def _apply_category_priors(name, category, position, size, room_height, warnings):
    """Correct common VLM dimension-semantic mistakes without changing layout."""
    original_position = list(position)
    original_size = list(size)
    if category == "table":
        position[1] = 0.0
        if size[1] < 0.55 or size[1] > 1.15:
            size[1] = 0.75
        size[0] = min(max(size[0], 0.5), 3.0)
        size[2] = min(max(size[2], 0.4), 1.5)
    elif category == "chair":
        position[1] = 0.0
        if size[1] < 0.55 or size[1] > 1.3:
            size[1] = 0.85
        size[0] = min(max(size[0], 0.35), 1.2)
        size[2] = min(max(size[2], 0.35), 1.2)
    elif category == "door":
        position[1] = 0.0
        size[0] = min(max(size[0], 0.8), 1.8)
        size[1] = min(max(size[1], 1.9), room_height)
        size[2] = min(max(size[2], 0.06), 0.18)
    elif category == "window":
        size[0] = min(max(size[0], 0.6), 4.0)
        size[1] = min(max(size[1], 0.8), min(2.2, room_height))
        size[2] = min(max(size[2], 0.05), 0.16)
    elif category == "board":
        size[0] = min(max(size[0], 1.0), 6.0)
        size[1] = min(max(size[1], 0.8), min(2.0, room_height))
        size[2] = min(max(size[2], 0.04), 0.14)
    if position != original_position or size != original_size:
        warnings.append(f"{name} 的尺寸/高度已按 {category} 物理先验修正")


def _nearest_wall(position, width, depth):
    distances = {
        "left": abs(position[0] + width * 0.5),
        "right": abs(position[0] - width * 0.5),
        "back": abs(position[2] - depth * 0.5),
        "front": abs(position[2] + depth * 0.5),
    }
    return min(distances, key=distances.get)


def _attach_to_wall(wall, position, size, width, depth):
    """Make mounted proxies thin and place their lower edge on one wall plane."""
    span = max(size[0], size[2])
    thickness = min(size[0], size[2], 0.14)
    if wall in {"left", "right"}:
        size[0], size[2] = thickness, span
        position[0] = -width * 0.5 if wall == "left" else width * 0.5
        limit = max(0.0, depth * 0.5 - span * 0.5)
        position[2] = min(max(position[2], -limit), limit)
    else:
        size[0], size[2] = span, thickness
        position[2] = depth * 0.5 if wall == "back" else -depth * 0.5
        limit = max(0.0, width * 0.5 - span * 0.5)
        position[0] = min(max(position[0], -limit), limit)


def _interaction_anchor(category, position, size):
    x, _, z = position
    width, height, depth = size
    if category == "chair":
        return {"type": "seat", "position": [x, min(height, 0.55), z]}
    if category == "door":
        return {"type": "passage", "position": [x, 0.0, z]}
    if category in {"table", "storage"}:
        return {"type": "surface", "position": [x, height, z]}
    if category in {"appliance", "prop"}:
        return {"type": "approach", "position": [x, 0.0, z - depth * 0.65]}
    return None


def normalize_reconstruction_layout(raw, known_room_width_m=0.0, max_objects=36):
    """Validate model output and return a bounded, metric scene description."""
    if not isinstance(raw, dict):
        raise ValueError("场景布局必须是 JSON 对象")
    room = raw.get("room")
    if not isinstance(room, dict):
        raise ValueError("场景布局缺少 room 对象")
    width = _number(room.get("width"), "room.width", 1.0, 100.0)
    depth = _number(room.get("depth"), "room.depth", 1.0, 100.0)
    height = _number(room.get("height"), "room.height", 1.8, 20.0)
    confidence = _number(room.get("confidence", 0.5), "room.confidence", 0.0, 1.0)
    scale = known_room_width_m / width if known_room_width_m > 0 else 1.0
    width *= scale
    depth *= scale
    height *= scale
    if not 1.8 <= height <= 20.0:
        raise ValueError("按已知房间宽度换算后，房间高度超出 1.8-20 米")

    warnings = []
    raw_objects = raw.get("objects")
    if not isinstance(raw_objects, list):
        raise ValueError("场景布局缺少 objects 数组")
    limit = max(1, min(int(max_objects), 56))
    if len(raw_objects) > limit:
        warnings.append(f"模型返回 {len(raw_objects)} 个对象，仅保留前 {limit} 个")
    objects = []
    for index, item in enumerate(raw_objects[:limit]):
        if not isinstance(item, dict):
            warnings.append(f"objects[{index}] 不是对象，已跳过")
            continue
        try:
            position = _vec3(item.get("position"), f"objects[{index}].position", -200, 200)
            size = _vec3(item.get("size"), f"objects[{index}].size", 0.02, 100)
            yaw = _number(
                item.get("yaw_degrees", 0), f"objects[{index}].yaw_degrees", -3600, 3600)
            object_confidence = _number(
                item.get("confidence", 0.5), f"objects[{index}].confidence", 0.0, 1.0)
        except ValueError as error:
            warnings.append(f"{error}，该对象已跳过")
            continue
        position = [component * scale for component in position]
        size = [component * scale for component in size]
        category = _category(item.get("category"))
        evidence = str(item.get("evidence") or "inferred").strip().lower()
        if evidence not in _EVIDENCE_VALUES:
            evidence = "inferred"
        name = _safe_name(item.get("name"), f"对象 {index + 1}")
        original = list(position)
        size[0] = min(size[0], width)
        size[1] = min(size[1], height * 1.5)
        size[2] = min(size[2], depth)
        _apply_category_priors(name, category, position, size, height, warnings)
        position[1] = min(max(position[1], 0.0), max(0.0, height - size[1]))
        normalized_yaw = ((yaw + 180.0) % 360.0) - 180.0
        wall = str(item.get("wall") or "").strip().lower()
        if category in _MOUNTED_CATEGORIES:
            if wall not in _WALL_VALUES:
                wall = _nearest_wall(position, width, depth)
                warnings.append(f"{name} 缺少可靠墙面标记，已吸附到 {wall} 墙")
            _attach_to_wall(wall, position, size, width, depth)
            normalized_yaw = 0.0
        else:
            wall = ""
        yaw_radians = math.radians(normalized_yaw)
        footprint_x = (
            abs(math.cos(yaw_radians)) * size[0] * 0.5
            + abs(math.sin(yaw_radians)) * size[2] * 0.5
        )
        footprint_z = (
            abs(math.sin(yaw_radians)) * size[0] * 0.5
            + abs(math.cos(yaw_radians)) * size[2] * 0.5
        )
        footprint_scale = min(
            1.0,
            width * 0.5 / max(footprint_x, 0.001),
            depth * 0.5 / max(footprint_z, 0.001),
        )
        if footprint_scale < 1.0:
            size[0] *= footprint_scale
            size[2] *= footprint_scale
            footprint_x *= footprint_scale
            footprint_z *= footprint_scale
            warnings.append(
                f"{_safe_name(item.get('name'), f'对象 {index + 1}')} 占地超出房间，已缩小")
        if category not in _MOUNTED_CATEGORIES:
            max_x = max(0.0, width * 0.5 - footprint_x)
            max_z = max(0.0, depth * 0.5 - footprint_z)
            position[0] = min(max(position[0], -max_x), max_x)
            position[2] = min(max(position[2], -max_z), max_z)
        if position != original:
            warnings.append(
                f"{name} 超出房间边界，已约束")
        color = str(item.get("color") or "")
        if not _HEX_COLOR_RE.fullmatch(color):
            color = _SEMANTIC_COLORS[category]
        movable_default = category not in _STRUCTURAL_CATEGORIES | _MOUNTED_CATEGORIES
        entity = {
            "id": f"scene-object-{index + 1:02d}",
            "name": name,
            "category": category,
            "wall": wall,
            "primitive": "box",
            "position": position,
            "size": size,
            "yaw_degrees": normalized_yaw,
            "confidence": object_confidence,
            "evidence": evidence,
            "movable": bool(item.get("movable", movable_default)),
            "collider": category not in {"window", "opening"},
            "color": color.lower(),
            "opacity": 0.62 if evidence != "observed" else 1.0,
        }
        anchor = _interaction_anchor(category, position, size)
        if anchor:
            entity["interaction_anchor"] = anchor
        objects.append(entity)

    camera = raw.get("camera") if isinstance(raw.get("camera"), dict) else {}
    try:
        if "position" in camera and "target" in camera:
            camera_position = [
                value * scale for value in _vec3(
                    camera["position"], "camera.position", -500, 500)
            ]
            camera_target = [
                value * scale for value in _vec3(
                    camera["target"], "camera.target", -500, 500)
            ]
        else:
            camera_position = [0, max(1.6, height * 0.55), -depth * 0.8]
            camera_target = [0, min(1.2, height * 0.4), 0]
        camera_fov = _number(
            camera.get("fov_degrees", 55), "camera.fov_degrees", 15, 100)
    except ValueError as error:
        warnings.append(f"{error}，已使用默认参考机位")
        camera_position = [0, max(1.6, height * 0.55), -depth * 0.8]
        camera_target = [0, min(1.2, height * 0.4), 0]
        camera_fov = 55.0

    return {
        "room": {
            "width": width,
            "depth": depth,
            "height": height,
            "confidence": confidence,
            "scale_source": "known_room_width" if known_room_width_m > 0 else "visual_estimate",
        },
        "camera": {
            "position": camera_position,
            "target": camera_target,
            "fov_degrees": camera_fov,
        },
        "objects": objects,
        "warnings": warnings,
    }


def _room_entities(room):
    width = room["width"]
    depth = room["depth"]
    height = room["height"]
    thickness = max(0.08, min(width, depth) * 0.012)
    confidence = room["confidence"]
    common = {
        "category": "architecture",
        "primitive": "box",
        "confidence": confidence,
        "evidence": "inferred",
        "movable": False,
        "collider": True,
        "color": _SEMANTIC_COLORS["architecture"],
        "opacity": 0.72,
    }
    return [
        {
            **common, "id": "room-floor", "name": "地面",
            "position": [0, -thickness, 0], "size": [width, thickness, depth],
            "yaw_degrees": 0,
        },
        {
            **common, "id": "room-wall-back", "name": "后墙",
            "position": [0, 0, depth * 0.5 - thickness * 0.5],
            "size": [width, height, thickness], "yaw_degrees": 0,
        },
        {
            **common, "id": "room-wall-left", "name": "左墙",
            "position": [-width * 0.5 + thickness * 0.5, 0, 0],
            "size": [thickness, height, depth], "yaw_degrees": 0,
        },
        {
            **common, "id": "room-wall-right", "name": "右墙",
            "position": [width * 0.5 - thickness * 0.5, 0, 0],
            "size": [thickness, height, depth], "yaw_degrees": 0,
        },
    ]


def build_scene_documents(layout, model, request_id, image_hashes):
    """Convert a normalized layout into director-native scene/camera/manifest JSON."""
    entities = _room_entities(layout["room"]) + list(layout["objects"])
    scene_objects = []
    for entity in entities:
        position = entity["position"]
        scene_objects.append({
            "id": entity["id"],
            "name": entity["name"],
            "type": "box",
            "position": position,
            "end": position,
            "scale": entity["size"],
            "rotation": [0, math.radians(entity["yaw_degrees"]), 0],
            "motion": "static",
            "appearance": {
                "color": entity["color"],
                "opacity": entity["opacity"],
            },
            "semantic": {
                "category": entity["category"],
                "confidence": entity["confidence"],
                "evidence": entity["evidence"],
                "movable": entity["movable"],
                "collider": entity["collider"],
                "wall": entity.get("wall", ""),
            },
        })
    scene = {
        "version": 3,
        "appearance": {
            "preset": "custom",
            "preview_mode": "director",
            "export_mode": "semantic",
            "sky_color": "#d9e2eb",
            "ground_color": "#bcc4cc",
            "grid_color": "#73808c",
            "actor_color": "#d94f70",
            "prop_color": "#727d89",
            "auto_actor_colors": True,
            "actor_palette": [
                "#d94f70", "#3978d4", "#2b9a78", "#e18335",
                "#8a5cc7", "#168fa3", "#b05d2e", "#65722e",
            ],
            "ground_visible": False,
        },
        "objects": scene_objects,
    }
    camera_value = layout["camera"]
    frame = {
        "position": camera_value["position"],
        "target": camera_value["target"],
        "fov": camera_value["fov_degrees"],
        "roll": 0,
    }
    camera = {
        "version": 3,
        "active_camera": "camera-reference",
        "cameras": [{
            "id": "camera-reference",
            "name": "参考图机位",
            "keyframes": [
                {"time": 0.0, **frame},
                {"time": 1.0, **frame},
            ],
        }],
        "cuts": [{"time": 0.0, "camera_id": "camera-reference"}],
    }
    manifest = {
        "version": 1,
        "generator": "tencent-hunyuan-vision-structured-whitebox",
        "model": model,
        "request_id": request_id,
        "source_image_sha256": image_hashes,
        "coordinate_system": {
            "units": "meters",
            "up": "+Y",
            "forward": "+Z",
            "object_position": "floor-footprint center",
        },
        "room": layout["room"],
        "entities": entities,
        "interaction_anchors": [
            {"entity_id": entity["id"], **entity["interaction_anchor"]}
            for entity in entities if entity.get("interaction_anchor")
        ],
        "warnings": layout["warnings"],
        "limitations": [
            "单图尺度和遮挡区域为视觉估计，不是测量级扫描",
            "几何为可编辑包围盒白模，不是原物体的高精度表面重建",
            "碰撞体为盒状代理；导航网格需要在目标引擎按角色参数重新烘焙",
        ],
    }
    return scene, camera, manifest


def _hex_rgba(color, opacity):
    return [
        int(color[1:3], 16) / 255.0,
        int(color[3:5], 16) / 255.0,
        int(color[5:7], 16) / 255.0,
        opacity,
    ]


def build_glb(entities, collision_only=False):
    """Build a valid glTF 2.0 binary containing independently named box nodes."""
    selected = [
        entity for entity in entities
        if not collision_only or entity.get("collider", False)
    ]
    positions = [
        -0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 1, 0.5, -0.5, 1, 0.5,
        0.5, 0, -0.5, -0.5, 0, -0.5, -0.5, 1, -0.5, 0.5, 1, -0.5,
        -0.5, 0, -0.5, -0.5, 0, 0.5, -0.5, 1, 0.5, -0.5, 1, -0.5,
        0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 1, -0.5, 0.5, 1, 0.5,
        -0.5, 1, 0.5, 0.5, 1, 0.5, 0.5, 1, -0.5, -0.5, 1, -0.5,
        -0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 0, 0.5, -0.5, 0, 0.5,
    ]
    normals = (
        [0, 0, 1] * 4 + [0, 0, -1] * 4 + [-1, 0, 0] * 4
        + [1, 0, 0] * 4 + [0, 1, 0] * 4 + [0, -1, 0] * 4
    )
    indices = []
    for face in range(6):
        base = face * 4
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    position_bytes = struct.pack(f"<{len(positions)}f", *positions)
    normal_bytes = struct.pack(f"<{len(normals)}f", *normals)
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    binary = position_bytes + normal_bytes + index_bytes

    material_keys = []
    for entity in selected:
        key = (
            "#777777" if collision_only else entity["color"],
            0.35 if collision_only else entity["opacity"],
        )
        if key not in material_keys:
            material_keys.append(key)
    materials = []
    meshes = []
    for color, opacity in material_keys:
        materials.append({
            "name": f"material-{len(materials) + 1}",
            "pbrMetallicRoughness": {
                "baseColorFactor": _hex_rgba(color, opacity),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.88,
            },
            "alphaMode": "BLEND" if opacity < 1 else "OPAQUE",
            "doubleSided": True,
        })
        meshes.append({
            "name": f"box-{len(meshes) + 1}",
            "primitives": [{
                "attributes": {"POSITION": 0, "NORMAL": 1},
                "indices": 2,
                "material": len(meshes),
            }],
        })
    nodes = []
    for entity in selected:
        key = (
            "#777777" if collision_only else entity["color"],
            0.35 if collision_only else entity["opacity"],
        )
        yaw = math.radians(entity["yaw_degrees"]) * 0.5
        nodes.append({
            "name": entity["name"],
            "mesh": material_keys.index(key),
            "translation": entity["position"],
            "rotation": [0, math.sin(yaw), 0, math.cos(yaw)],
            "scale": entity["size"],
            "extras": {
                "entity_id": entity["id"],
                "category": entity["category"],
                "confidence": entity["confidence"],
                "evidence": entity["evidence"],
                "movable": entity["movable"],
                "collider": entity["collider"],
            },
        })
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "Tencent VOD AIGC editable whitebox",
        },
        "scene": 0,
        "scenes": [{"name": "Editable Scene", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
            {
                "buffer": 0, "byteOffset": len(position_bytes),
                "byteLength": len(normal_bytes), "target": 34962,
            },
            {
                "buffer": 0, "byteOffset": len(position_bytes) + len(normal_bytes),
                "byteLength": len(index_bytes), "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3",
                "min": [-0.5, 0.0, -0.5], "max": [0.5, 1.0, 0.5],
            },
            {"bufferView": 1, "componentType": 5126, "count": 24, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
    }
    json_bytes = json.dumps(
        gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )


def write_scene_package(output_root, filename, scene, camera, manifest):
    """Write all reconstruction artifacts atomically and return their paths."""
    safe_name = "".join(
        character if character.isalnum() or character in ("-", "_") else "_"
        for character in str(filename or "editable_scene")
    ).strip("_")[:80] or "editable_scene"
    directory = os.path.join(
        output_root, "vod_aigc", "editable_scenes", f"{safe_name}_{uuid.uuid4().hex[:12]}")
    os.makedirs(directory, exist_ok=False)
    paths = {
        "scene": os.path.join(directory, "scene.glb"),
        "collision": os.path.join(directory, "collision.glb"),
        "scene_json": os.path.join(directory, "scene.json"),
        "camera_json": os.path.join(directory, "camera.json"),
        "manifest": os.path.join(directory, "scene_manifest.json"),
    }
    payloads = {
        "scene": build_glb(manifest["entities"]),
        "collision": build_glb(manifest["entities"], collision_only=True),
        "scene_json": json.dumps(scene, ensure_ascii=False, indent=2).encode("utf-8"),
        "camera_json": json.dumps(camera, ensure_ascii=False, indent=2).encode("utf-8"),
        "manifest": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    try:
        for key, path in paths.items():
            partial = f"{path}.part"
            with open(partial, "wb") as handle:
                handle.write(payloads[key])
            os.replace(partial, path)
    except OSError:
        for path in paths.values():
            for candidate in (path, f"{path}.part"):
                try:
                    if os.path.isfile(candidate):
                        os.remove(candidate)
                except OSError:
                    pass
        raise
    return paths
