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
- Count every physical table and chair exactly once. Do not emit alternate
  hypotheses, duplicate boxes, partially overlapping copies, or hidden copies
  of an already listed instance.
- Before writing objects, count the visually distinct tabletops and chair
  seats. Emit one tight image_bbox for each visible physical instance. A broad
  bbox that covers multiple desks or multiple chairs is invalid.
- Preserve the photographed spacing. Do not regularize repeated furniture into
  an ideal grid unless the image actually shows equal spacing.
- Mark hidden geometry as inferred. Do not invent decorative detail.
- Keep every object inside or directly against the room bounds.
- For door, window, and board objects, set wall to left/right/back/front.
  Objects visible on the same physical wall must use the same wall value.
- Return at most {max_objects} objects.

Use these exact JSON fields without copying any preset scene dimensions:
- room: width, depth, height, confidence (all numeric)
- camera: position (3 numbers), target (3 numbers), fov_degrees
- objects: array of objects containing name, category, position (3 numbers),
  size (3 numbers), yaw_degrees, confidence, evidence, movable, wall,
  image_bbox, floor_contact, instance_id, and paired_instance_id
- instance_id is a unique stable identifier for that physical instance
- for a chair assigned to a table, paired_instance_id is that table's
  instance_id; use an empty string when no pairing is visible
- image_bbox is [left, top, right, bottom] in primary-image normalized 0-1
  coordinates and must tightly cover the visible extent of that instance
- floor_contact is [x, y] in normalized 0-1 primary-image coordinates at the
  physical floor contact beneath each table, chair, or other floor object
- floor_contact must lie at or very near that instance's image_bbox bottom
  edge. Never reuse one floor_contact.y for furniture in different depth rows.
- window objects also include sill_height_m, the estimated physical distance
  from the floor to the window's lower edge
- category must be one of:
  board, door, window, table, chair, storage, appliance, furniture, prop, unknown
- wall must be left/right/back/front for mounted objects and an empty string otherwise

Before replying, verify:
- classroom tables have a plausible full height, not tabletop thickness
- classroom chairs include seat, legs, and back in their full height
- doors start at floor level and use plausible human-scale dimensions
- wall fixtures are thin and share a consistent wall plane
- repeated furniture preserves each photographed floor contact and gap instead
  of being replaced by a synthetic evenly spaced grid
- every listed table/chair corresponds to a different visible physical instance
- the number of table/chair objects matches the visible tabletop/chair-seat
  inventory, including partially occluded instances
- every classroom chair is paired to at most one table, and every table to at
  most one chair
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


def _normalized_point(value):
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        point = [float(value[0]), float(value[1])]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(component) and 0 <= component <= 1 for component in point):
        return None
    return point


def _normalized_bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bbox = [float(component) for component in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(component) and 0 <= component <= 1 for component in bbox):
        return None
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return None
    return bbox


def _normalize3(value):
    length = math.sqrt(sum(component * component for component in value))
    if length < 1e-8:
        return None
    return [component / length for component in value]


def _cross3(left, right):
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _image_ray(point, camera, aspect_ratio):
    forward = _normalize3([
        camera["target"][index] - camera["position"][index] for index in range(3)
    ])
    if forward is None:
        return None
    right = _normalize3(_cross3([0, 1, 0], forward))
    if right is None:
        return None
    up = _normalize3(_cross3(forward, right))
    x_ndc = (point[0] - 0.5) * 2.0
    y_ndc = (0.5 - point[1]) * 2.0
    tangent = math.tan(math.radians(camera["fov_degrees"]) * 0.5)
    direction = [
        forward[index]
        + right[index] * x_ndc * tangent * aspect_ratio
        + up[index] * y_ndc * tangent
        for index in range(3)
    ]
    return _normalize3(direction)


def _ground_point_from_image(point, camera, aspect_ratio):
    direction = _image_ray(point, camera, aspect_ratio)
    if direction is None or direction[1] >= -1e-4:
        return None
    distance = -camera["position"][1] / direction[1]
    if distance <= 0:
        return None
    return [
        camera["position"][index] + direction[index] * distance
        for index in range(3)
    ]


def _wall_point_from_image(point, wall, camera, width, depth, aspect_ratio):
    direction = _image_ray(point, camera, aspect_ratio)
    if direction is None:
        return None
    axis = 0 if wall in {"left", "right"} else 2
    plane = (
        (-width * 0.5 if wall == "left" else width * 0.5)
        if axis == 0
        else (-depth * 0.5 if wall == "front" else depth * 0.5)
    )
    if abs(direction[axis]) < 1e-4:
        return None
    distance = (plane - camera["position"][axis]) / direction[axis]
    if distance <= 0:
        return None
    return [
        camera["position"][index] + direction[index] * distance
        for index in range(3)
    ]


def _image_floor_layout_point(point, width, depth):
    """Bounded fallback when the estimated camera cannot support ray projection."""
    return [
        (point[0] - 0.5) * width * 0.7,
        0.0,
        (0.6 - point[1]) * depth * 0.75,
    ]


def _inset_bound(value, limit):
    """Keep outliers inside while retaining their monotonic relative ordering."""
    if limit <= 0:
        return 0.0
    inset = min(limit, min(0.05, max(0.005, limit * 0.01)))
    threshold = limit - inset
    if -threshold <= value <= threshold:
        return value
    excess = abs(value) - threshold
    bounded = threshold + inset * (1.0 - math.exp(-excess / inset))
    return math.copysign(bounded, value)


def _safe_name(value, fallback):
    name = " ".join(str(value or fallback).split())
    return name[:80] or fallback


def _rotated_footprint(size, yaw_degrees):
    yaw_radians = math.radians(yaw_degrees)
    return (
        abs(math.cos(yaw_radians)) * size[0] * 0.5
        + abs(math.sin(yaw_radians)) * size[2] * 0.5,
        abs(math.sin(yaw_radians)) * size[0] * 0.5
        + abs(math.cos(yaw_radians)) * size[2] * 0.5,
    )


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


def _bbox_area(bbox):
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_iou(left, right):
    if left is None or right is None:
        return 0.0
    intersection = (
        max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
        * max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    )
    union = _bbox_area(left) + _bbox_area(right) - intersection
    return intersection / union if union > 1e-8 else 0.0


def _observation_point(entity):
    contact = entity.get("floor_contact")
    if contact is not None:
        return contact
    bbox = entity.get("image_bbox")
    if bbox is not None:
        return [(bbox[0] + bbox[2]) * 0.5, bbox[3]]
    return None


def _deduplicate_furniture(objects, warnings):
    kept = []
    removed = 0
    for entity in objects:
        if entity["category"] not in {"table", "chair"}:
            kept.append(entity)
            continue
        point = _observation_point(entity)
        duplicate = None
        for existing in kept:
            if existing["category"] != entity["category"]:
                continue
            existing_point = _observation_point(existing)
            point_distance = (
                math.hypot(
                    point[0] - existing_point[0],
                    point[1] - existing_point[1],
                )
                if point is not None and existing_point is not None else float("inf")
            )
            overlap = _bbox_iou(entity.get("image_bbox"), existing.get("image_bbox"))
            same_instance = (
                entity.get("instance_id")
                and entity["instance_id"] == existing.get("instance_id")
            )
            if (
                same_instance and point_distance <= 0.03
                or overlap >= 0.92 and point_distance <= 0.012
            ):
                duplicate = existing
                break
            if same_instance:
                warnings.append(
                    f"{entity['name']} 与 {existing['name']} 的 instance_id 重复但图像位置冲突，"
                    "已保留两个实例"
                )
        if duplicate is None:
            kept.append(entity)
            continue
        removed += 1
        if entity["confidence"] > duplicate["confidence"]:
            kept[kept.index(duplicate)] = entity
            duplicate, entity = entity, duplicate
        warnings.append(f"{entity['name']} 与 {duplicate['name']} 属于同一图像实例，已去重")
    return kept, removed


def _pair_cost(table, chair):
    table_instance = table.get("instance_id")
    chair_instance = chair.get("instance_id")
    table_claims_chair = (
        table.get("paired_instance_id")
        and table["paired_instance_id"] == chair_instance
    )
    chair_claims_table = (
        chair.get("paired_instance_id")
        and chair["paired_instance_id"] == table_instance
    )
    explicit_bonus = (
        1.6 if table_claims_chair and chair_claims_table
        else 0.8 if table_claims_chair or chair_claims_table
        else 0.0
    )
    table_point = _observation_point(table)
    chair_point = _observation_point(chair)
    if table_point is None or chair_point is None:
        return math.hypot(
            chair["position"][0] - table["position"][0],
            chair["position"][2] - table["position"][2],
        ) / 10.0 + 0.4 - explicit_bonus
    table_bbox = table.get("image_bbox")
    chair_bbox = chair.get("image_bbox")
    horizontal = abs(chair_point[0] - table_point[0])
    vertical = abs(chair_point[1] - table_point[1])
    outside = 0.0
    if table_bbox is not None and chair_bbox is not None:
        chair_center = (chair_bbox[0] + chair_bbox[2]) * 0.5
        if chair_center < table_bbox[0] - 0.08 or chair_center > table_bbox[2] + 0.08:
            outside = 0.4
    return horizontal * 2.5 + vertical * 1.5 + outside - explicit_bonus


def _assign_table_chair_pairs(objects, warnings):
    tables = [item for item in objects if item["category"] == "table"]
    chairs = [item for item in objects if item["category"] == "chair"]
    paired_tables = set()
    paired_chairs = set()
    pairs = []

    def assign(table, chair):
        paired_tables.add(table["id"])
        paired_chairs.add(chair["id"])
        pair_id = f"desk-pair-{len(pairs) + 1:02d}"
        table["pair_id"] = pair_id
        chair["pair_id"] = pair_id
        table["paired_entity_id"] = chair["id"]
        chair["paired_entity_id"] = table["id"]
        pairs.append((table, chair))

    for table in tables:
        mutual = [
            chair for chair in chairs
            if (
                table.get("paired_instance_id") == chair.get("instance_id")
                and chair.get("paired_instance_id") == table.get("instance_id")
                and table.get("instance_id")
                and chair.get("instance_id")
            )
        ]
        if len(mutual) == 1 and mutual[0]["id"] not in paired_chairs:
            assign(table, mutual[0])

    for chair in chairs:
        if chair["id"] in paired_chairs or not chair.get("paired_instance_id"):
            continue
        claimed_tables = [
            table for table in tables
            if (
                table["id"] not in paired_tables
                and table.get("instance_id") == chair["paired_instance_id"]
            )
        ]
        competing_chairs = [
            candidate for candidate in chairs
            if (
                candidate["id"] not in paired_chairs
                and candidate.get("paired_instance_id") == chair["paired_instance_id"]
            )
        ]
        if len(claimed_tables) == 1 and len(competing_chairs) == 1:
            assign(claimed_tables[0], chair)

    candidates = sorted(
        (_pair_cost(table, chair), table["id"], chair["id"], table, chair)
        for table in tables for chair in chairs
        if table["id"] not in paired_tables and chair["id"] not in paired_chairs
    )
    for cost, _, _, table, chair in candidates:
        if cost > 1.6 or table["id"] in paired_tables or chair["id"] in paired_chairs:
            continue
        assign(table, chair)
    unpaired_chairs = len(chairs) - len(paired_chairs)
    if unpaired_chairs:
        warnings.append(f"{unpaired_chairs} 把椅子未找到唯一桌子配对，已保留原图像落点")
    return pairs


def _chair_pair_offset(table, chair):
    table_bbox = table.get("image_bbox")
    chair_bbox = chair.get("image_bbox")
    table_point = _observation_point(table)
    chair_point = _observation_point(chair)
    dx = 0.0
    if table_point is not None and chair_point is not None:
        dx = (chair_point[0] - table_point[0]) * table["size"][0] * 1.4
    dx = min(max(dx, -table["size"][0] * 0.32), table["size"][0] * 0.32)
    chair_lower = (
        chair_bbox is not None and table_bbox is not None
        and chair_bbox[3] >= table_bbox[3] - 0.03
    )
    direction = -1.0 if chair_lower else 1.0
    _, table_half_z = _rotated_footprint(table["size"], table["yaw_degrees"])
    _, chair_half_z = _rotated_footprint(chair["size"], chair["yaw_degrees"])
    separation = max(0.42, table_half_z + chair_half_z - 0.08)
    return dx, direction * separation


def _table_layout_point(table, pair_lookup):
    return (
        _observation_point(table)
        or _observation_point(pair_lookup.get(table["id"], {}))
        or [0.5, 0.5]
    )


def _cluster_rows(tables, pair_lookup):
    rows = []
    ordered = sorted(
        tables,
        key=lambda item: -_table_layout_point(item, pair_lookup)[1],
    )
    for table in ordered:
        observed_y = _table_layout_point(table, pair_lookup)[1]
        row = next(
            (candidate for candidate in rows
             if abs(candidate["observed_y"] - observed_y) <= 0.065),
            None,
        )
        if row is None:
            row = {"observed_y": observed_y, "tables": []}
            rows.append(row)
        row["tables"].append(table)
        row["observed_y"] = sum(
            _table_layout_point(item, pair_lookup)[1]
            for item in row["tables"]
        ) / len(row["tables"])
    return sorted(rows, key=lambda row: -row["observed_y"])


def _pair_extents(table, chair):
    table_half_x, table_half_z = _rotated_footprint(
        table["size"], table["yaw_degrees"])
    minimum_x = -table_half_x
    maximum_x = table_half_x
    minimum_z = -table_half_z
    maximum_z = table_half_z
    if chair is not None:
        chair_half_x, chair_half_z = _rotated_footprint(
            chair["size"], chair["yaw_degrees"])
        dx = chair["position"][0] - table["position"][0]
        dz = chair["position"][2] - table["position"][2]
        minimum_x = min(minimum_x, dx - chair_half_x)
        maximum_x = max(maximum_x, dx + chair_half_x)
        minimum_z = min(minimum_z, dz - chair_half_z)
        maximum_z = max(maximum_z, dz + chair_half_z)
    return minimum_x, maximum_x, minimum_z, maximum_z


def _pack_row_x(row, pair_lookup, width):
    tables = sorted(
        row["tables"],
        key=lambda item: _table_layout_point(item, pair_lookup)[0],
    )
    extents = [
        _pair_extents(table, pair_lookup.get(table["id"])) for table in tables
    ]
    gap = 0.22
    offsets = [-extents[0][0]]
    for index in range(1, len(tables)):
        offsets.append(
            offsets[-1] + extents[index - 1][1] - extents[index][0] + gap
        )
    group_width = offsets[-1] + extents[-1][1]
    observed_centers = [
        _table_layout_point(table, pair_lookup)[0] for table in tables
    ]
    desired_center = (
        sum((center - 0.5) * width * 0.85 for center in observed_centers)
        / len(observed_centers)
    )
    start = min(
        max(desired_center - group_width * 0.5, -width * 0.5 + 0.08),
        width * 0.5 - group_width - 0.08,
    )
    if group_width > width - 0.16:
        start = -width * 0.5 + 0.08
    for table, offset in zip(tables, offsets):
        target = start + offset
        delta = target - table["position"][0]
        table["position"][0] = target
        chair = pair_lookup.get(table["id"])
        if chair is not None:
            chair["position"][0] += delta


def _pack_rows_z(rows, pair_lookup, depth):
    row_shapes = []
    for row in rows:
        near = 0.0
        far = 0.0
        desired = 0.0
        for table in row["tables"]:
            extents = _pair_extents(table, pair_lookup.get(table["id"]))
            near = min(near, extents[2])
            far = max(far, extents[3])
            desired += table["position"][2]
        row_shapes.append({
            "row": row,
            "near": -near,
            "far": far,
            "desired": desired / len(row["tables"]),
        })
    gap = 0.16
    required = sum(shape["near"] + shape["far"] for shape in row_shapes)
    required += gap * max(0, len(row_shapes) - 1)
    if len(row_shapes) > 1 and required > depth - 0.16:
        gap = max(0.04, (depth - 0.16 - sum(
            shape["near"] + shape["far"] for shape in row_shapes
        )) / (len(row_shapes) - 1))
    centers = []
    cursor = -depth * 0.5 + 0.08
    for shape in row_shapes:
        center = cursor + shape["near"]
        centers.append(center)
        cursor = center + shape["far"] + gap
    group_end = cursor - gap + 0.08
    if group_end < depth * 0.5:
        desired_center = sum(shape["desired"] for shape in row_shapes) / len(row_shapes)
        current_center = (centers[0] - row_shapes[0]["near"] + group_end) * 0.5
        shift = min(
            max(desired_center - current_center, -depth * 0.5 + 0.08 - (
                centers[0] - row_shapes[0]["near"])),
            depth * 0.5 - group_end,
        )
        centers = [center + shift for center in centers]
    for shape, center in zip(row_shapes, centers):
        for table in shape["row"]["tables"]:
            delta = center - table["position"][2]
            table["position"][2] = center
            chair = pair_lookup.get(table["id"])
            if chair is not None:
                chair["position"][2] += delta


def _furniture_overlap_count(objects):
    furniture = [
        item for item in objects if item["category"] in {"table", "chair"}
    ]
    overlaps = 0
    minimum_clearance = float("inf")
    for index, left in enumerate(furniture):
        left_x, left_z = _rotated_footprint(left["size"], left["yaw_degrees"])
        for right in furniture[index + 1:]:
            if left.get("pair_id") and left.get("pair_id") == right.get("pair_id"):
                continue
            right_x, right_z = _rotated_footprint(right["size"], right["yaw_degrees"])
            gap_x = abs(left["position"][0] - right["position"][0]) - left_x - right_x
            gap_z = abs(left["position"][2] - right["position"][2]) - left_z - right_z
            minimum_clearance = min(minimum_clearance, max(gap_x, gap_z))
            if gap_x < 0 and gap_z < 0:
                overlaps += 1
    return overlaps, 0.0 if not math.isfinite(minimum_clearance) else minimum_clearance


def _minimum_category_clearance(objects, category):
    matches = [item for item in objects if item["category"] == category]
    clearance = float("inf")
    for index, left in enumerate(matches):
        left_x, left_z = _rotated_footprint(left["size"], left["yaw_degrees"])
        for right in matches[index + 1:]:
            right_x, right_z = _rotated_footprint(right["size"], right["yaw_degrees"])
            gap_x = abs(left["position"][0] - right["position"][0]) - left_x - right_x
            gap_z = abs(left["position"][2] - right["position"][2]) - left_z - right_z
            clearance = min(clearance, max(gap_x, gap_z))
    return 0.0 if not math.isfinite(clearance) else clearance


def _bound_furniture_groups(objects, pair_lookup, width, depth, warnings):
    paired_chairs = {chair["id"] for chair in pair_lookup.values()}
    for table in (item for item in objects if item["category"] == "table"):
        chair = pair_lookup.get(table["id"])
        minimum_x, maximum_x, minimum_z, maximum_z = _pair_extents(table, chair)
        span_x = maximum_x - minimum_x
        span_z = maximum_z - minimum_z
        if chair is not None and (span_x > width or span_z > depth):
            scale_x = min(1.0, width * 0.96 / max(span_x, 0.001))
            scale_z = min(1.0, depth * 0.96 / max(span_z, 0.001))
            footprint_scale = min(scale_x, scale_z)
            table["size"][0] *= footprint_scale
            table["size"][2] *= footprint_scale
            chair["size"][0] *= footprint_scale
            chair["size"][2] *= footprint_scale
            chair["position"][0] = table["position"][0] + (
                chair["position"][0] - table["position"][0]) * footprint_scale
            chair["position"][2] = table["position"][2] + (
                chair["position"][2] - table["position"][2]) * footprint_scale
            minimum_x, maximum_x, minimum_z, maximum_z = _pair_extents(
                table, chair)
            warnings.append(f"{table['name']} 桌椅组合超出房间，已同比缩小占地与相对偏移")
        lower_x = -width * 0.5 - table["position"][0] - minimum_x
        upper_x = width * 0.5 - table["position"][0] - maximum_x
        lower_z = -depth * 0.5 - table["position"][2] - minimum_z
        upper_z = depth * 0.5 - table["position"][2] - maximum_z
        delta_x = min(max(0.0, lower_x), upper_x)
        delta_z = min(max(0.0, lower_z), upper_z)
        table["position"][0] += delta_x
        table["position"][2] += delta_z
        if chair is not None:
            chair["position"][0] += delta_x
            chair["position"][2] += delta_z
    for chair in (
        item for item in objects
        if item["category"] == "chair" and item["id"] not in paired_chairs
    ):
        half_x, half_z = _rotated_footprint(chair["size"], chair["yaw_degrees"])
        chair["position"][0] = min(
            max(chair["position"][0], -width * 0.5 + half_x),
            width * 0.5 - half_x,
        )
        chair["position"][2] = min(
            max(chair["position"][2], -depth * 0.5 + half_z),
            depth * 0.5 - half_z,
        )


def _solve_furniture_layout(objects, width, depth, warnings):
    input_furniture = sum(
        item["category"] in {"table", "chair"} for item in objects)
    objects, deduplicated = _deduplicate_furniture(objects, warnings)
    pairs = _assign_table_chair_pairs(objects, warnings)
    overlaps_before, _ = _furniture_overlap_count(objects)
    pair_lookup = {}
    for table, chair in pairs:
        pair_lookup[table["id"]] = chair
        dx, dz = _chair_pair_offset(table, chair)
        chair["position"][0] = table["position"][0] + dx
        chair["position"][2] = table["position"][2] + dz
        chair["projection_source"] = "image_contact_pair_solver"
    tables = [item for item in objects if item["category"] == "table"]
    observed_tables = [
        table for table in tables
        if (
            _observation_point(table) is not None
            or _observation_point(pair_lookup.get(table["id"], {})) is not None
        )
    ]
    rows = _cluster_rows(observed_tables, pair_lookup) if observed_tables else []
    for row in rows:
        _pack_row_x(row, pair_lookup, width)
    if rows:
        _pack_rows_z(rows, pair_lookup, depth)
    _bound_furniture_groups(objects, pair_lookup, width, depth, warnings)
    overlaps, minimum_clearance = _furniture_overlap_count(objects)
    if overlaps:
        warnings.append(f"全局布局后仍有 {overlaps} 组非配对家具包围盒重叠")
    return objects, {
        "input_furniture": input_furniture,
        "output_furniture": sum(
            item["category"] in {"table", "chair"} for item in objects),
        "deduplicated_furniture": deduplicated,
        "table_chair_pairs": len(pairs),
        "furniture_rows": len(rows),
        "initial_furniture_overlaps": overlaps_before,
        "residual_furniture_overlaps": overlaps,
        "minimum_furniture_clearance_m": minimum_clearance,
        "minimum_table_clearance_m": _minimum_category_clearance(
            objects, "table"),
        "minimum_chair_clearance_m": _minimum_category_clearance(
            objects, "chair"),
    }


def normalize_reconstruction_layout(raw, known_room_width_m=0.0, max_objects=36,
                                    image_aspect_ratio=1.0):
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
    normalized_camera = {
        "position": camera_position,
        "target": camera_target,
        "fov_degrees": camera_fov,
    }
    if (
        abs(camera_position[0]) >= width * 0.45
        or abs(camera_target[0]) >= width * 0.45
        or not 0.8 <= camera_position[1] <= min(3.0, height)
    ):
        warnings.append("参考相机位于房间边界或高度不可靠，已改用居中参考机位")
        normalized_camera = {
            "position": [0, min(max(1.6, height * 0.5), 2.0), -depth * 0.8],
            "target": [0, min(1.2, height * 0.4), 0],
            "fov_degrees": camera_fov,
        }
    try:
        aspect_ratio = float(image_aspect_ratio)
    except (TypeError, ValueError):
        aspect_ratio = 1.0
    if not math.isfinite(aspect_ratio) or aspect_ratio <= 0:
        aspect_ratio = 1.0

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
        image_bbox = _normalized_bbox(item.get("image_bbox"))
        floor_contact = _normalized_point(item.get("floor_contact"))
        if image_bbox is not None:
            bbox_contact = [(image_bbox[0] + image_bbox[2]) * 0.5, image_bbox[3]]
            if (
                floor_contact is None
                or floor_contact[0] < image_bbox[0] - 0.08
                or floor_contact[0] > image_bbox[2] + 0.08
                or abs(floor_contact[1] - image_bbox[3]) > 0.1
            ):
                floor_contact = bbox_contact
        evidence = str(item.get("evidence") or "inferred").strip().lower()
        if evidence not in _EVIDENCE_VALUES:
            evidence = "inferred"
        name = _safe_name(item.get("name"), f"对象 {index + 1}")
        size[0] = min(size[0], width)
        size[1] = min(size[1], height * 1.5)
        size[2] = min(size[2], depth)
        _apply_category_priors(name, category, position, size, height, warnings)
        normalized_yaw = ((yaw + 180.0) % 360.0) - 180.0
        wall = str(item.get("wall") or "").strip().lower()
        projection_used = False
        if category in _MOUNTED_CATEGORIES:
            if wall not in _WALL_VALUES:
                wall = _nearest_wall(position, width, depth)
                warnings.append(f"{name} 缺少可靠墙面标记，已吸附到 {wall} 墙")
            wall_anchor = (
                [(image_bbox[0] + image_bbox[2]) * 0.5, image_bbox[3]]
                if image_bbox is not None else None
            )
            projected_wall_point = (
                _wall_point_from_image(
                    wall_anchor, wall, normalized_camera, width, depth, aspect_ratio)
                if wall_anchor is not None else None
            )
            if projected_wall_point is not None:
                position = projected_wall_point
                projection_used = True
            _attach_to_wall(wall, position, size, width, depth)
            normalized_yaw = 0.0
            if category == "door":
                position[1] = 0.0
            elif category == "window":
                raw_sill = item.get("sill_height_m")
                try:
                    raw_sill_height = float(raw_sill) * scale
                except (TypeError, ValueError):
                    raw_sill_height = float("nan")
                projected_sill_height = position[1] if projection_used else float("nan")
                sill_height = (
                    projected_sill_height
                    if math.isfinite(projected_sill_height)
                    and 0.45 <= projected_sill_height <= 1.5
                    else raw_sill_height
                )
                max_sill = max(0.0, height - size[1])
                if not math.isfinite(sill_height) or not 0.45 <= sill_height <= 1.5:
                    sill_height = 0.9
                    warnings.append(f"{name} 的窗台高度不可靠，已使用 0.9m 教室先验")
                position[1] = min(max(sill_height, 0.45), max_sill)
            elif category == "board":
                upper = max(0.0, height - size[1])
                position[1] = min(max(position[1], min(0.7, upper)), upper)
        else:
            wall = ""
            footprint_x, footprint_z = _rotated_footprint(size, normalized_yaw)
            max_center_x = max(0.0, width * 0.5 - footprint_x)
            max_center_z = max(0.0, depth * 0.5 - footprint_z)
            projected_ground = (
                _ground_point_from_image(floor_contact, normalized_camera, aspect_ratio)
                if floor_contact is not None else None
            )
            if floor_contact is not None:
                image_layout = (
                    projected_ground
                    if projected_ground is not None
                    and abs(projected_ground[0]) <= width * 0.5
                    and abs(projected_ground[2]) <= depth * 0.5
                    else _image_floor_layout_point(floor_contact, width, depth)
                )
                bounded_model_x = _inset_bound(position[0], max_center_x)
                bounded_model_z = _inset_bound(position[2], max_center_z)
                bounded_image_x = _inset_bound(image_layout[0], max_center_x)
                bounded_image_z = _inset_bound(image_layout[2], max_center_z)
                position[0] = bounded_model_x * 0.35 + bounded_image_x * 0.65
                position[2] = bounded_model_z * 0.35 + bounded_image_z * 0.65
                projection_used = True
            position[1] = min(max(position[1], 0.0), max(0.0, height - size[1]))
        pre_boundary_position = list(position)
        footprint_x, footprint_z = _rotated_footprint(size, normalized_yaw)
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
        if position != pre_boundary_position:
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
            "instance_id": str(item.get("instance_id") or "").strip(),
            "paired_instance_id": str(
                item.get("paired_instance_id") or "").strip(),
            "wall": wall,
            "image_bbox": image_bbox,
            "floor_contact": floor_contact,
            "projection_source": "image_contact" if projection_used else "model_3d",
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

    objects, layout_quality = _solve_furniture_layout(
        objects, width, depth, warnings)
    for entity in objects:
        entity.pop("interaction_anchor", None)
        anchor = _interaction_anchor(
            entity["category"], entity["position"], entity["size"])
        if anchor:
            entity["interaction_anchor"] = anchor
    return {
        "room": {
            "width": width,
            "depth": depth,
            "height": height,
            "confidence": confidence,
            "scale_source": "known_room_width" if known_room_width_m > 0 else "visual_estimate",
        },
        "camera": normalized_camera,
        "objects": objects,
        "layout_quality": layout_quality,
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
                "image_bbox": entity.get("image_bbox"),
                "floor_contact": entity.get("floor_contact"),
                "projection_source": entity.get("projection_source", "model_3d"),
                "pair_id": entity.get("pair_id", ""),
                "paired_entity_id": entity.get("paired_entity_id", ""),
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
        "layout_quality": layout.get("layout_quality", {}),
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
