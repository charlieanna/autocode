"""Portable Figma exports, verbatim readable context and capture setup inventory."""
from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse
try:
    from . import autocode_design_manifest as design, autocode_preflight_contract as contract, autocode_util as util
except ImportError:
    import autocode_design_manifest as design, autocode_preflight_contract as contract, autocode_util as util


def decoded(path, encoding):
    text = Path(path).read_text()
    if encoding == "text":
        return text
    data = json.loads(text)
    if encoding == "json-string" and isinstance(data, str):
        return data
    if encoding == "mcp-text" and isinstance(data, dict) and isinstance(data.get("content"), list):
        texts = [item["text"] for item in data["content"] if item.get("type") == "text" and isinstance(item.get("text"), str)]
        if texts:
            return "\n".join(texts)
    raise ValueError("Design context does not match its declared text/json-string/mcp-text encoding")


def validate(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"manifest", "cases"}:
        raise ValueError("Design readiness requires a public manifest and case inventory")
    if not isinstance(value['manifest'], str):
        raise ValueError('Design manifest must be a project-relative path')
    design.relative_path(value["manifest"])
    if not isinstance(value["cases"], list) or not value["cases"]:
        raise ValueError("Design readiness case inventory cannot be empty")
    seen = set()
    for row in value["cases"]:
        if (not isinstance(row, dict) or set(row) != {"id", "encoding", "context_parts", "assets", "fonts", "canvas"}
                or not isinstance(row["id"], str) or row["id"] in seen
                or row["encoding"] not in ("text", "json-string", "mcp-text")):
            raise ValueError("Invalid or duplicate design readiness case")
        seen.add(row["id"])
        contract.validate_canvas(row["canvas"])
        for key in ("context_parts", "assets"):
            paths = row[key]
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths)) or (key == "context_parts" and not paths):
                raise ValueError(f"Design {key} must be a unique path inventory")
            for path in paths:
                design.relative_path(path)
        if not isinstance(row["fonts"], list) or not row["fonts"]:
            raise ValueError("Declare the actual font files and families, including locally supplied fallbacks")
        for font in row["fonts"]:
            if not isinstance(font, dict) or set(font) != {"family", "path"} or not isinstance(font["family"], str) or not font["family"] or not isinstance(font['path'], str):
                raise ValueError("Design fonts require a family and file path")
            design.relative_path(font["path"])
    return value


def check(settings, configuration, workspace, scratch, inputs, *, phase='planning'):
    required = settings.get("figma_file") or settings.get("design_manifest")
    if not configuration:
        if required:
            raise ValueError("Visual task has no design readiness inventory; export every required Figma frame/context/asset/font into public proof inputs before dispatch")
        return []
    declared = {item["path"] for item in inputs}
    path = configuration["manifest"]
    source = design.load(Path(workspace) / path)
    copied = design.load(Path(scratch) / path)
    retained = settings.get("design_manifest")
    if retained and (design.verify(retained)["manifest_hash"] != source["manifest_hash"]):
        raise ValueError("Public design bundle differs from the approved retained Figma manifest")
    if copied["manifest_hash"] != source["manifest_hash"]:
        raise ValueError("Copied design manifest differs from the approved public bundle")
    if settings.get("figma_file"):
        target = urlparse(settings["figma_file"])
        parts = target.path.strip("/").split("/")
        key = parts[1] if len(parts) > 1 and parts[0] in ("design", "file", "proto") else None
        if source["body"]["version"] == 1:
            nodes = {file["key"]: file["nodes"] for file in source["body"]["files"]}
        else:
            frames = design.inventory.catalog(source["body"], source["root"])["screen_frames"]
            nodes = {file["key"]: [] for file in source["body"]["files"]}
            for frame in frames:
                nodes[frame["file_key"]].append(frame["node_id"])
            for file in source['body']['files']:
                nodes[file['key']].extend(page['id'] for page in file['pages'])
                for component in file['components']:
                    nodes[file['key']].append(component['source_node_id'])
                    nodes[file['key']].extend(row['node_id'] for row in component['variants'])
        node = parse_qs(target.query).get("node-id", [None])[0]
        if key not in nodes or (node and node.replace("-", ":") not in nodes[key]):
            raise ValueError("Approved Figma URL has no matching exported file/frame")
    by_id = {row["id"]: row for row in configuration["cases"]}
    if set(by_id) != {case["id"] for case in source["body"]["cases"]}:
        raise ValueError("Design readiness must cover every approved frame/state case exactly once")
    result, required_paths = [], {path}
    required_paths.update(str(Path(path).parent / artifact["path"])
                          for artifact in design.all_artifacts(source["body"]))
    for case in source["body"]["cases"]:
        row = by_id[case["id"]]
        required_paths.update(str(Path(path).parent / a["path"]) for a in case["artifacts"].values())
        required_paths.update(row["context_parts"] + row["assets"] + [f["path"] for f in row["fonts"]])
        for root in (Path(workspace), Path(scratch)):
            context = decoded(root / Path(path).parent / case["artifacts"]["design_context"]["path"], row["encoding"])
            chunks = [(root / item).read_text() for item in row["context_parts"]]
            if not context.strip() or "".join(chunks) != context:
                raise ValueError(f"{case['id']}: readable context is incomplete or not a verbatim decoded derivative")
            if any(not chunk or len(chunk.encode('utf-8')) > 1600 for chunk in chunks):
                raise ValueError(f"{case['id']}: context parts must be nonempty and at most 1600 UTF-8 bytes to avoid provider read truncation")
        browser_contracts = [check["contract"] for check in (settings.get("task_preflight") or {}).get("body", {}).get("checks", [])
                            if check.get("contract", {}).get("kind") == "browser"
                            and (phase == 'planning' or check['phase'] == phase)]
        if not any(c["viewport"] == case["viewport"] and c["canvas"] == row["canvas"]
                   and c["fonts"] == [font["family"] for font in row["fonts"]] for c in browser_contracts):
            raise ValueError(f"{case['id']}: no worker browser prerequisite matches its viewport/DPR, canvas and font inventory")
        result.append({"id": case["id"], "context_parts": row["context_parts"],
                       "raw_context_sha256": case["artifacts"]["design_context"]["sha256"],
                       "decoded_sha256": hashlib.sha256(context.encode('utf-8')).hexdigest(), "canvas": row["canvas"], "fonts": row["fonts"]})
    if missing := required_paths - declared:
        raise ValueError("Design proof inputs are not hash-bound in inputs: " + ", ".join(sorted(missing)))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create lossless, small context parts before approving a prerequisite manifest")
    parser.add_argument("source", type=Path)
    parser.add_argument("--encoding", choices=("text", "json-string", "mcp-text"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    text = decoded(args.source, args.encoding)
    args.output.mkdir(parents=True, exist_ok=False)
    chunks, current = [], ""
    for character in text:
        if len((current + character).encode("utf-8")) > 1600:
            chunks.append(current)
            current = ""
        current += character
    if current:
        chunks.append(current)
    for number, chunk in enumerate(chunks):
        (args.output / f"part-{number:04d}.txt").write_text(chunk)
    print(json.dumps({"source_sha256": util.file_hash(args.source), "parts": len(chunks)}))


if __name__ == "__main__":
    main()
